import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from clang import cindex
except ImportError:  # pragma: no cover - optional dependency guard
    cindex = None  # type: ignore[misc]


SYS_CALL_MACRO_PATTERN = re.compile(
    r"(INLINE_SYSCALL|INTERNAL_SYSCALL|INTERNAL_SYSCALL_DECL|INTERNAL_SYSCALL_CALL|"
    r"SYSCALL_CANCEL|SYSCALL_CANCEL_IF|__libc_do_syscall)\s*\(",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SyscallExtractionResult:
    source_path: Path
    macro_name: str
    kernel_symbol: str
    raw_arguments: Sequence[str]
    function_signature: str

    def as_payload(self) -> Dict[str, Any]:
        mapping = [
            {"strategy": "raw", "value": argument.strip()}
            for argument in self.raw_arguments
        ]
        return {
            "kernel_syscall": self.kernel_symbol,
            "args_mapping_json": json.dumps(mapping, ensure_ascii=False),
            "macro_name": self.macro_name,
            "source_path": str(self.source_path),
            "status": "parsed",
        }


class GlibcAstParser:
    """
    Extract glibc syscall wrapper information using libclang (if available) with a
    textual fallback for environments where clang cannot fully parse glibc sources.
    """

    def __init__(self, glibc_root: Path, target_arch: str) -> None:
        self.glibc_root = glibc_root
        self.target_arch = target_arch

        if cindex is not None:
            self._initialise_libclang()

    def parse_wrapper_function(self, symbol: str) -> Dict[str, Any]:
        source_path = self._locate_symbol_source(symbol)
        if source_path is None:
            return {
                "status": "symbol_not_found",
                "message": f"Unable to locate source file for `{symbol}` under {self.glibc_root}",
            }

        try:
            result = self._extract_syscall_info(symbol, source_path)
        except Exception as error:  # pragma: no cover - defensive logging
            return {
                "status": "parse_error",
                "message": f"Failed to extract syscall info for `{symbol}`: {error}",
                "source_path": str(source_path),
            }

        if result is None:
            return {
                "status": "macro_not_found",
                "message": "No syscall macro invocation detected in wrapper function.",
                "source_path": str(source_path),
            }

        return result.as_payload()

    # ------------------------------------------------------------------ #
    # libclang initialisation helpers
    # ------------------------------------------------------------------ #
    def _initialise_libclang(self) -> None:
        """
        Best-effort libclang initialisation. We respect LIBCLANG_PATH if defined,
        otherwise allow clang.cindex to discover the shared library.
        """
        libclang_path = os.getenv("LIBCLANG_PATH")
        if not libclang_path:
            return

        try:
            cindex.Config.set_library_file(libclang_path)
        except cindex.LibclangError as exc:  # pragma: no cover - discovery failure
            print(
                "[glibc-parser] WARN: Failed to set libclang path "
                f"from LIBCLANG_PATH={libclang_path}: {exc}"
            )

    # ------------------------------------------------------------------ #
    # Source discovery
    # ------------------------------------------------------------------ #
    def _locate_symbol_source(self, symbol: str) -> Optional[Path]:
        explicit = [
            self.glibc_root / f"{symbol}.c",
            self.glibc_root / f"sysdeps/unix/sysv/linux/{symbol}.c",
            self.glibc_root / f"sysdeps/unix/sysv/linux/{symbol}.S",
        ]

        for candidate in explicit:
            if candidate.exists():
                return candidate

        return self._search_globally(symbol)

    @lru_cache(maxsize=256)
    def _search_globally(self, symbol: str) -> Optional[Path]:
        pattern = re.compile(rf"\b{re.escape(symbol)}\b", re.MULTILINE)
        for source_path in self.glibc_root.rglob("*.c"):
            try:
                snippet = source_path.open(encoding="utf-8", errors="ignore").read()
            except OSError:
                continue

            if pattern.search(snippet):
                return source_path
        return None

    # ------------------------------------------------------------------ #
    # Extraction pipeline
    # ------------------------------------------------------------------ #
    def _extract_syscall_info(
        self,
        symbol: str,
        source_path: Path,
    ) -> Optional[SyscallExtractionResult]:
        source = source_path.read_text(encoding="utf-8", errors="ignore")

        signature, body = self._slice_function_block(source, symbol)
        if signature is None or body is None:
            return None

        macro_match = SYS_CALL_MACRO_PATTERN.search(body)
        if not macro_match:
            return None

        macro_name = macro_match.group(1)
        macro_start = macro_match.end()
        macro_call, arguments = self._extract_macro_arguments(body, macro_start)
        if macro_call is None or not arguments:
            return None

        kernel_symbol = arguments[0].strip()
        syscall_args = arguments[2:] if len(arguments) > 2 else []

        return SyscallExtractionResult(
            source_path=source_path.resolve(),
            macro_name=macro_name,
            kernel_symbol=kernel_symbol,
            raw_arguments=syscall_args,
            function_signature=signature.strip(),
        )

    @staticmethod
    def _slice_function_block(
        source: str,
        symbol: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return the function signature and body text for `symbol`."""
        patterns = [
            rf"\b{symbol}\s*\(",
            rf"\b__{symbol}\s*\(",
        ]

        for pattern in patterns:
            match = re.search(pattern, source)
            if not match:
                continue

            header_start = source.rfind("\n", 0, match.start())
            header_start = 0 if header_start == -1 else header_start
            brace_start = source.find("{", match.end())
            if brace_start == -1:
                continue

            signature = source[header_start:brace_start]
            body, _ = GlibcAstParser._collect_brace_block(source, brace_start)
            if body is None:
                continue

            return signature, body

        return None, None

    @staticmethod
    def _collect_brace_block(source: str, brace_index: int) -> Tuple[Optional[str], int]:
        """Collect the brace-enclosed block, returning the block text and final index."""
        depth = 0
        idx = brace_index
        while idx < len(source):
            char = source[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[brace_index : idx + 1], idx
            idx += 1
        return None, idx

    def _extract_macro_arguments(
        self,
        body: str,
        macro_start: int,
    ) -> Tuple[Optional[str], Optional[List[str]]]:
        opening = body.find("(", macro_start - 1)
        if opening == -1:
            return None, None

        args_block, _ = self._collect_parentheses_block(body, opening)
        if args_block is None:
            return None, None

        arguments = self._split_arguments(args_block[1:-1])
        return args_block, arguments

    @staticmethod
    def _collect_parentheses_block(
        text: str,
        open_index: int,
    ) -> Tuple[Optional[str], int]:
        depth = 0
        idx = open_index
        while idx < len(text):
            char = text[idx]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return text[open_index : idx + 1], idx
            idx += 1
        return None, idx

    @staticmethod
    def _split_arguments(argument_block: str) -> List[str]:
        arguments: List[str] = []
        depth = 0
        current: List[str] = []

        for char in argument_block:
            if char == "," and depth == 0:
                argument = "".join(current).strip()
                if argument:
                    arguments.append(argument)
                current = []
                continue

            if char in ("(", "{", "["):
                depth += 1
            elif char in (")", "}", "]"):
                depth -= 1

            current.append(char)

        trailing = "".join(current).strip()
        if trailing:
            arguments.append(trailing)

        return arguments

