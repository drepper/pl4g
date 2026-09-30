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
from ..sema.clauses import Conditions
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
    #: What a condition written in a signature does in this build.  A build-time
    #: choice and not a per-clause one: a clause says what must be true and a
    #: build says what to do about it, which is the division g++'s
    #: `-fcontract-evaluation-semantic` draws and the one this follows.
    conditions: Conditions = Conditions.CHECK
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
    #: How much room the program's own stack has, and how much unreachable
    #: space sits below it.  A stack of the program's own means running off the
    #: bottom is a fault at a known address rather than a quiet write into
    #: whatever was mapped next, which is what the kernel's own stack gives.
    stack_size: int = 1 << 20
    guard_size: int = 1 << 16
    report_log: Path | None = None
    #: What to run a test binary through, where this machine does not run what
    #: was built.  An emulator is the usual answer; without one the tests for
    #: another target are not run and the build says so.
    test_runner: str | None = None
    #: Where to look for modules, in the order to look, as the command line
    #: gave them.  An entry that is not absolute is relative to the importing
    #: file first and to where the compiler was run second.
    module_path: list[Path] = field(default_factory=list)
    incremental: bool = False
    #: What `-Dname=value` said, which is how a build file is told something
    #: from outside.  Read by `std.option` while the build function runs and by
    #: nothing else.
    defines: dict[str, str] = field(default_factory=dict)
    #: Whether this compilation is one a build function asked for.  Such a
    #: compilation compiles what it was given and never looks for a build
    #: function of its own, which is what keeps a build file that names itself
    #: from running for ever.
    from_build: bool = False
    show_help: bool = False
    show_help_json: bool = False
    show_targets: bool = False
    show_version: bool = False
    #: What the compiler was asked to do.  A command line that names none means
    #: `build`, which is what every command line meant before there were any.
    command: str = "build"


def load_option_table() -> Mapping[str, Any]:
    """Read the shared option table."""
    with share_file(OPTIONS_FILE).open(encoding="utf-8") as stream:
        return json.load(stream)


def render_help(table: Mapping[str, Any]) -> str:
    """Render the help text from the shared option table."""
    out: list[str] = ["".join(("usage: ", table["usage"])), ""]
    commands = table.get("commands")
    if commands:
        out.append("commands:")
        for command in commands:
            note = ("" if command.get("implemented", True)
                    else "  (not implemented yet)")
            out.append("".join(("  ", str(command["name"]).ljust(28),
                                command["help"], note)))
        out.append("")
        out.append("options:")
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
