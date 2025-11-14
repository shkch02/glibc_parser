from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence


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
        mapping = [
            {"strategy": "raw", "value": argument.strip()}
            for argument in self.raw_arguments
        ]
        from json import dumps

        return {
            "kernel_syscall": self.kernel_symbol,
            "args_mapping_json": dumps(mapping, ensure_ascii=False),
            "macro_name": self.macro_name,
            "source_path": str(self.source_path),
            "status": "parsed",
        }


def merge_results(
    base: Dict[str, List[SyscallCallInfo]],
    incoming: Dict[str, List[SyscallCallInfo]],
) -> None:
    for key, value in incoming.items():
        base.setdefault(key, []).extend(value)

