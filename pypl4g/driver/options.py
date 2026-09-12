"""The command line the compiler accepts.

The option table lives in ``share`` because both implementations must accept the
same parameters: a build system should not have to know whether it is invoking
the bootstrap compiler or the final one.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final, Mapping

from ..paths import share_file

OPTIONS_FILE: Final[str] = "options.json"

#: The suffix a source file must have.
SOURCE_SUFFIX: Final[str] = ".pl4g"


class EmitKind(Enum):
    """How far the compilation runs and what it writes."""

    TOKENS = "tokens"
    AST = "ast"
    IR = "ir"
    ASM = "asm"
    ELF = "elf"


class DiagFormat(Enum):
    """How diagnostics are rendered."""

    TEXT = "text"
    JSON = "json"


class ExitCode:
    """The statuses the compiler exits with."""

    SUCCESS: Final[int] = 0
    ERRORS: Final[int] = 1
    USAGE: Final[int] = 2
    INTERNAL: Final[int] = 70


@dataclass(slots=True)
class Options:
    """Everything the command line said."""

    output: Path | None = None
    inputs: list[Path] = field(default_factory=list)
    opt_level: int = 0
    emit: EmitKind = EmitKind.ELF
    triple: str = "x86_64-linux-none"
    warnings: dict[str, bool] = field(default_factory=dict)
    warnings_are_errors: bool = False
    debug_info: bool = False
    verbose: bool = False
    time_report: bool = False
    diag_format: DiagFormat = DiagFormat.TEXT
    decision_log: Path | None = None
    incremental: bool = False
    show_help: bool = False
    show_help_json: bool = False
    show_version: bool = False


def load_option_table() -> Mapping[str, Any]:
    """Read the shared option table."""
    with share_file(OPTIONS_FILE).open(encoding="utf-8") as stream:
        return json.load(stream)


def render_help(table: Mapping[str, Any]) -> str:
    """Render the help text from the shared option table."""
    out: list[str] = ["".join(("usage: ", table["usage"])), ""]
    for option in table["options"]:
        names: list[str] = []
        if option.get("short"):
            names.append(option["short"])
        if option.get("long"):
            names.append(option["long"])
        spelled = ", ".join(names)
        metavar = option.get("metavar")
        if metavar is not None:
            spelled = "".join((spelled, " ", metavar))
        note = "" if option.get("implemented", True) else "  (not implemented yet)"
        out.append("".join(("  ", spelled.ljust(28), option["help"], note)))
    out.append("")
    return "\n".join(out)
