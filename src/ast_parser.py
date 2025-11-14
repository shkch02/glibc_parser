import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from clang import cindex
except ImportError:  # pragma: no cover - optional dependency guard
    cindex = None  # type: ignore[misc]


# Text-fallback macro patterns (used when libclang is unavailable or fails)
SYS_CALL_MACRO_PATTERN = re.compile(
    r"(INLINE_SYSCALL|INTERNAL_SYSCALL|INTERNAL_SYSCALL_DECL|INTERNAL_SYSCALL_CALL|"
    r"SYSCALL_CANCEL|SYSCALL_CANCEL_IF|__libc_do_syscall)\s*\(",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SyscallCallInfo:
    kernel_symbol: str
    raw_arguments: List[str]
    conditional_context: str
    source_location: str
    origin_macro: str


@dataclass(frozen=True)
class SyscallExtractionResult:
    source_path: Path
    macro_name: str
    kernel_symbol: str
    raw_arguments: Sequence[str]
    function_signature: str

    def as_payload(self) -> Dict[str, Any]:
        mapping = [{"strategy": "raw", "value": argument.strip()} for argument in self.raw_arguments]
        return {
            "kernel_syscall": self.kernel_symbol,
            "args_mapping_json": json.dumps(mapping, ensure_ascii=False),
            "macro_name": self.macro_name,
            "source_path": str(self.source_path),
            "status": "parsed",
        }


class GlibcAstParser:
    """
    v2: Bottom-Up 분석을 위한 AST 파서
    - libclang을 활용하여 개별 TU 단위로 파싱
    - 매크로 확장, 조건부 컴파일, 인라인 ASM 보조 처리
    - 텍스트 기반 Fallback 포함
    """

    DEFAULT_TARGET_MACROS: Tuple[str, ...] = (
        "internal_syscall0",
        "internal_syscall1",
        "internal_syscall2",
        "internal_syscall3",
        "internal_syscall4",
        "internal_syscall5",
        "internal_syscall6",
        "INTERNAL_SYSCALL",
        "INTERNAL_SYSCALL_DECL",
        "INTERNAL_SYSCALL_CALL",
        "SYSCALL_CANCEL",
        "SYSCALL_CANCEL_IF",
        "__libc_do_syscall",
    )

    def __init__(self, glibc_root: Path, target_arch: str) -> None:
        self.glibc_root = glibc_root
        self.target_arch = target_arch

        if cindex is not None:
            self._initialise_libclang()

    # ----------------------------- Public APIs ----------------------------- #
    def run_full_analysis(
        self,
        clang_args: Optional[List[str]] = None,
        target_macros: Optional[List[str]] = None,
        c_files: Optional[List[Path]] = None,
        enable_time64_round: bool = True,
    ) -> Dict[str, List[SyscallCallInfo]]:
        """
        모든 .c 파일을 대상으로 다중 라운드 파싱을 수행하여
        wrapper 함수 이름 -> SyscallCallInfo 리스트 매핑을 생성합니다.
        """
        macros = tuple(target_macros) if target_macros else self.DEFAULT_TARGET_MACROS
        base_flags = clang_args or self._build_default_clang_args()
        files = c_files or self._discover_source_files()

        union_results: Dict[str, List[SyscallCallInfo]] = {}

        # Round 1: base flags
        results_base = self._parsing_loop(files, base_flags, macros)
        _merge_results(union_results, results_base)

        # Round 2: _TIME_BITS=64
        if enable_time64_round:
            time64_flags = list(base_flags) + ["-D_TIME_BITS=64", "-D__USE_TIME_BITS64=1"]
            results_time64 = self._parsing_loop(files, time64_flags, macros)
            _merge_results(union_results, results_time64)

        return union_results

    def parse_wrapper_function(self, symbol: str) -> Dict[str, Any]:
        """
        Backward-compatible API for earlier PoC step.
        v2 내부 구현을 유지하되, 단일 심볼에 대한 결과만 반환.
        """
        # Try v2 multi-pass over likely file(s) containing symbol
        source_path = self._locate_symbol_source(symbol)
        if source_path is not None and cindex is not None:
            results = self.run_full_analysis(
                c_files=[source_path],
                enable_time64_round=True,
            )
            if symbol in results and results[symbol]:
                first = results[symbol][0]
                return {
                    "kernel_syscall": first.kernel_symbol,
                    "args_mapping_json": json.dumps(
                        [{"strategy": "raw", "value": arg} for arg in first.raw_arguments],
                        ensure_ascii=False,
                    ),
                    "macro_name": first.origin_macro,
                    "source_path": first.source_location,
                    "status": "parsed",
                }

        # Fallback: text-based single-file heuristic
        source_path = source_path or self._locate_symbol_source(symbol)
        if source_path is None:
            return {
                "status": "symbol_not_found",
                "message": f"Unable to locate source file for `{symbol}` under {self.glibc_root}",
            }

        try:
            result = self._extract_syscall_info_text(symbol, source_path)
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

    # ----------------------- libclang initialisation ----------------------- #
    def _initialise_libclang(self) -> None:
        libclang_path = os.getenv("LIBCLANG_PATH")
        if not libclang_path:
            return
        try:
            cindex.Config.set_library_file(libclang_path)
        except Exception as exc:  # pragma: no cover
            print(
                "[glibc-parser] WARN: Failed to set libclang path "
                f"from LIBCLANG_PATH={libclang_path}: {exc}"
            )

    # ----------------------------- Preparation ----------------------------- #
    def _build_default_clang_args(self) -> List[str]:
        """
        기본 -I, -D 구성. 실제 환경에 맞춰 추가가 필요할 수 있음.
        """
        include_dirs = [
            self.glibc_root / "include",
            self.glibc_root / "sysdeps" / "unix" / "sysv" / "linux",
            self.glibc_root,
        ]
        args = [
            "-x",
            "c",
            "-std=gnu11",
            "-D_GNU_SOURCE=1",
            "-D__USE_GNU=1",
            "-D__LINUX__=1",
            f"-D__{self.target_arch.upper()}__=1",
        ]
        for inc in include_dirs:
            if inc.exists():
                args.extend(["-I", str(inc)])
        return args

    def _discover_source_files(self) -> List[Path]:
        return [p for p in self.glibc_root.rglob("*.c")]

    # -------------------------- Multi-pass parsing ------------------------- #
    def _parsing_loop(
        self,
        files: List[Path],
        clang_args: List[str],
        target_macros: Tuple[str, ...],
    ) -> Dict[str, List[SyscallCallInfo]]:
        if cindex is None:
            return {}

        index = cindex.Index.create()
        aggregate: Dict[str, List[SyscallCallInfo]] = {}

        tu_options = cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
        for source_file in files:
            try:
                tu = index.parse(
                    path=str(source_file),
                    args=clang_args,
                    options=tu_options,
                )
            except Exception:
                # Skip files that fail to parse in the current flag set
                continue

            file_results = self._walk_ast(tu, source_file, target_macros)
            _merge_results(aggregate, file_results)

        return aggregate

    # ------------------------------ AST Walk ------------------------------- #
    def _walk_ast(
        self,
        tu: "cindex.TranslationUnit",
        source_file: Path,
        target_macros: Tuple[str, ...],
    ) -> Dict[str, List[SyscallCallInfo]]:
        results: Dict[str, List[SyscallCallInfo]] = {}
        root = tu.cursor

        def visit(node: "cindex.Cursor") -> None:
            try:
                kind = node.kind
            except Exception:
                return

            # Macro expansion
            if kind == cindex.CursorKind.MACRO_INSTANTIATION and node.spelling in target_macros:
                self._process_syscall_node(node, results, origin_macro=node.spelling)

            # Direct calls
            elif kind == cindex.CursorKind.CALL_EXPR and node.spelling in target_macros:
                self._process_syscall_node(node, results, origin_macro=node.spelling)

            # Inline asm fallback
            elif kind == cindex.CursorKind.ASM_STMT:
                self._handle_inline_asm(node, results)

            for child in node.get_children():
                visit(child)

        visit(root)
        return results

    # ------------------------- Bottom-up processing ------------------------ #
    def _process_syscall_node(
        self,
        node: "cindex.Cursor",
        results: Dict[str, List[SyscallCallInfo]],
        origin_macro: str,
    ) -> None:
        wrapper_name = self._trace_to_function_decl(node)
        if not wrapper_name:
            return

        kernel_symbol, args = self._extract_call_info(node)
        if not kernel_symbol:
            return

        conditional = self._extract_conditional_context(node)
        location = self._format_location(node.location)

        info = SyscallCallInfo(
            kernel_symbol=kernel_symbol,
            raw_arguments=args,
            conditional_context=conditional,
            source_location=location,
            origin_macro=origin_macro,
        )
        results.setdefault(wrapper_name, []).append(info)

    def _trace_to_function_decl(self, node: "cindex.Cursor") -> Optional[str]:
        cur: Optional["cindex.Cursor"] = node
        while cur is not None:
            if cur.kind == cindex.CursorKind.FUNCTION_DECL:
                return cur.spelling
            cur = cur.semantic_parent
        return None

    def _extract_call_info(self, node: "cindex.Cursor") -> Tuple[Optional[str], List[str]]:
        """
        토큰 기반으로 매크로/호출 인자 분석:
        첫 번째 토큰 그룹에서 커널 심볼(예: __NR_openat 또는 pselect6_time64)을 추정,
        이후 인자 나열을 단순 분리.
        """
        try:
            tokens = list(node.get_tokens())
        except Exception:
            return None, []

        text = " ".join(t.spelling for t in tokens)
        # Heuristic: MACRO_NAME( kernel, nargs, arg0, arg1, ... )
        m = re.search(r"\(\s*([^,]+)\s*,\s*([0-9]+)\s*,(.*)\)$", text)
        if not m:
            # Another common pattern: MACRO(kernel, arg0, arg1, ...)
            m2 = re.search(r"\(\s*([^,]+)\s*,(.*)\)$", text)
            if not m2:
                return None, []
            kernel, rest = m2.group(1).strip(), m2.group(2)
            args = self._split_arguments(rest)
            return kernel, [a.strip() for a in args]

        kernel = m.group(1).strip()
        rest = m.group(3)
        args = self._split_arguments(rest)
        return kernel, [a.strip() for a in args]

    def _extract_conditional_context(self, node: "cindex.Cursor") -> str:
        """
        lexical_parent 체인을 따라 가까운 IfStmt 조건식을 토큰으로 복원.
        """
        cur: Optional["cindex.Cursor"] = node.lexical_parent
        while cur is not None:
            if cur.kind == cindex.CursorKind.IF_STMT:
                try:
                    cond_tokens = [t.spelling for t in cur.get_tokens()]
                    snippet = " ".join(cond_tokens)
                    # Heuristically trim to condition parentheses if possible
                    start = snippet.find("(")
                    end = snippet.find(")")
                    if start != -1 and end != -1 and end > start:
                        return "if " + snippet[start : end + 1]
                    return snippet[:120]
                except Exception:
                    return "if (/* condition */)"
            cur = cur.lexical_parent
        return ""

    def _handle_inline_asm(
        self,
        node: "cindex.Cursor",
        results: Dict[str, List[SyscallCallInfo]],
    ) -> None:
        """
        AST로는 구체적 의미 해석이 어려운 asm에 대해 간단한 힌트만 수집.
        """
        wrapper_name = self._trace_to_function_decl(node)
        if not wrapper_name:
            return
        location = self._format_location(node.location)
        info = SyscallCallInfo(
            kernel_symbol="asm(syscall)",
            raw_arguments=[],
            conditional_context=self._extract_conditional_context(node),
            source_location=location,
            origin_macro="asm",
        )
        results.setdefault(wrapper_name, []).append(info)

    # ----------------------------- Text fallback --------------------------- #
    def _extract_syscall_info_text(
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
        macro_call, arguments = self._extract_macro_arguments_text(body, macro_start)
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

    def _extract_macro_arguments_text(
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

    # ------------------------------ Utilities ------------------------------ #
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

    @staticmethod
    def _format_location(location: "cindex.SourceLocation") -> str:
        try:
            file = location.file.name if location.file else "<unknown>"
            return f"{file}:{location.line}"
        except Exception:
            return "<unknown>:0"


def _merge_results(
    base: Dict[str, List[SyscallCallInfo]],
    incoming: Dict[str, List[SyscallCallInfo]],
) -> None:
    for key, value in incoming.items():
        base.setdefault(key, []).extend(value)

