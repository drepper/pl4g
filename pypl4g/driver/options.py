"""The command line the compiler accepts.

The option table lives in ``share`` because both implementations must accept the
same parameters: a build system should not have to know whether it is invoking
the bootstrap compiler or the final one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final, Mapping

from ..diag.style import ColourWhen
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
    #: Which of the target's microarchitecture levels the generated program may
    #: use.  What the names are is the target's business, and a target that has
    #: none says so; the default is the highest x86-64 states, since a program
    #: that will not run says so at once and one built down to the oldest
    #: machine is a thing to ask for.
    mclevel: str | None = None
    warnings: dict[str, bool] = field(default_factory=dict)
    warnings_are_errors: bool = False
    debug_info: bool = False
    verbose: bool = False
    time_report: bool = False
    diag_format: DiagFormat = DiagFormat.TEXT
    #: When to write colour.  Looking at the stream is the default, so that a
    #: terminal gets it and a pipe does not; `NO_COLOR` is honoured whatever
    #: this says.
    colour: ColourWhen = ColourWhen.AUTO
    report_log: Path | None = None
    #: Where to look for modules, in the order to look, as the command line
    #: gave them.  An entry that is not absolute is relative to the importing
    #: file first and to where the compiler was run second.
    module_path: list[Path] = field(default_factory=list)
    incremental: bool = False
    show_help: bool = False
    show_help_json: bool = False
    show_targets: bool = False
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
            # An option whose value may be left out is spelled the way it is
            # written, brackets and all: `--color[=WHEN]` says both that the
            # value goes after an equals sign and that it need not be there.
            spelled = "".join(
                (spelled, "[=", metavar, "]")
                if option.get("arg") == "joined_equals_optional"
                else (spelled, " ", metavar))
        note = "" if option.get("implemented", True) else "  (not implemented yet)"
        out.append("".join(("  ", spelled.ljust(28), option["help"], note)))
    out.append("")
    return "\n".join(out)
