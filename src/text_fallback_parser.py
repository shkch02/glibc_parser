import re
from pathlib import Path
from typing import List, Optional, Tuple

from .models import SyscallExtractionResult

SYS_CALL_MACRO_PATTERN = re.compile(
    r"(INLINE_SYSCALL|INTERNAL_SYSCALL|INTERNAL_SYSCALL_DECL|INTERNAL_SYSCALL_CALL|"
    r"SYSCALL_CANCEL|SYSCALL_CANCEL_IF|__libc_do_syscall|"
    r"internal_syscall[0-6]|__open_nocancel|__openat_nocancel)\s*\(",
    re.MULTILINE | re.IGNORECASE,
)


def extract_syscall_info_text(
    symbol: str,
    source_path: Path,
) -> Optional[SyscallExtractionResult]:
    source = source_path.read_text(encoding="utf-8", errors="ignore")

    signature, body = _slice_function_block(source, symbol)
    if signature is None or body is None:
        print(f"[glibc-parser] DEBUG: Failed to extract function block for `{symbol}`")
        macro_match = SYS_CALL_MACRO_PATTERN.search(source)
        if macro_match:
            print("[glibc-parser] DEBUG: Found macro in file but couldn't extract function block")
        return None

    print(f"[glibc-parser] DEBUG: Extracted function body (length: {len(body)} chars)")

    macro_match = SYS_CALL_MACRO_PATTERN.search(body)
    if not macro_match:
        print("[glibc-parser] DEBUG: No syscall macro found in function body")
        preview = body[:500].replace("\n", "\\n")
        print(f"[glibc-parser] DEBUG: Function body preview: {preview}...")
        return None

    macro_name = macro_match.group(1)
    print(f"[glibc-parser] DEBUG: Found macro: {macro_name}")
    macro_start = macro_match.end()
    macro_call, arguments = _extract_macro_arguments_text(body, macro_start)
    if macro_call is None or not arguments:
        print("[glibc-parser] DEBUG: Failed to extract macro arguments")
        return None

    print(f"[glibc-parser] DEBUG: Extracted {len(arguments)} arguments")
    kernel_symbol = arguments[0].strip()
    if len(arguments) > 2 and arguments[1].strip().isdigit():
        syscall_args = arguments[2:]
    else:
        syscall_args = arguments[1:]

    return SyscallExtractionResult(
        source_path=source_path.resolve(),
        macro_name=macro_name,
        kernel_symbol=kernel_symbol,
        raw_arguments=syscall_args,
        function_signature=signature.strip(),
    )


def split_arguments(argument_block: str) -> List[str]:
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


def _slice_function_block(
    source: str,
    symbol: str,
) -> Tuple[Optional[str], Optional[str]]:
    patterns = [
        (rf"^[^\n]*\b{re.escape(symbol)}\s*\(", re.MULTILINE),
        (rf"^[^\n]*\b__{re.escape(symbol)}\s*\(", re.MULTILINE),
        (rf"^[^\n]*\b__{re.escape(symbol)}64\s*\(", re.MULTILINE),
        (rf"\b{re.escape(symbol)}\s*\(", 0),
        (rf"\b__{re.escape(symbol)}\s*\(", 0),
    ]

    for pattern, flags in patterns:
        matches = list(re.finditer(pattern, source, flags))
        for match in matches:
            pos = match.start()
            search_start = match.end()
            brace_start = source.find("{", search_start)
            if brace_start == -1:
                continue

            paren_end = source.rfind(")", search_start, brace_start)
            if paren_end == -1:
                continue

            before_pos = max(0, pos - 100)
            context = source[before_pos:pos]
            if re.search(r"[a-zA-Z_][a-zA-Z0-9_]*\s*\([^)]*\)\s*$", context):
                continue

            header_start = source.rfind("\n", 0, pos)
            header_start = 0 if header_start == -1 else header_start

            sig_start = max(header_start, brace_start - 1000)
            signature = source[sig_start:brace_start].strip()

            body, _ = _collect_brace_block(source, brace_start)
            if body is None:
                continue

            if len(body.strip()) < 10:
                continue

            return signature, body

    return None, None


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
    body: str,
    macro_start: int,
) -> Tuple[Optional[str], Optional[List[str]]]:
    opening = body.find("(", macro_start - 1)
    if opening == -1:
        return None, None

    args_block, _ = _collect_parentheses_block(body, opening)
    if args_block is None:
        return None, None

    arguments = split_arguments(args_block[1:-1])
    return args_block, arguments


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

