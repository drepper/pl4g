"""Parsing the command line.

`argparse` does the parsing and `share/options.json` says what there is to
parse.  The two are kept in step by a test rather than by generating one from
the other: the table is the contract between this compiler and any other, and
what a contract needs is both sides checked against it, not one side built from
it.  `--help` is still rendered from the table, so that two implementations
print the same list.

**Every refusal is a numbered diagnostic.**  `argparse` writes prose of its own
and exits; here it does neither.  What it would have exited over is turned into
an entry of the shared catalog, so that a build reading the errors sees the same
numbers whichever compiler produced them.

**Two forms are written out before `argparse` sees them.**  A short option whose
value is stuck to it, and a long option whose value may be left out, are both
places where `argparse` would take the *next* word instead -- so
`pypl4g --color prog.pl4g` would compile nothing and colour "prog.pl4g".
Normalising them first is what keeps a command line meaning what it says.
"""

from __future__ import annotations

import argparse
import re
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Final, NoReturn, Sequence, TypeVar

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..diag.style import ColourWhen
from ..sema.modules import parse_search_path
from .options import (DiagFormat, EmitKind, Options, SOURCE_SUFFIX)

EnumT = TypeVar("EnumT", bound=Enum)

#: What the compiler can be asked to do.  `build` is what a command line with no
#: command means, so that every command line that worked before there were any
#: still works and means the same thing.
BUILD: Final[str] = "build"
TEST: Final[str] = "test"
SUBCOMMANDS: Final[tuple[str, ...]] = (BUILD, TEST)

#: What `--emit=KIND` writes where no name was given: the first source with its
#: suffix replaced.  An executable takes none, which is what every compiler on a
#: system without file types does.
_SUFFIXES: Final[dict[EmitKind, str]] = {
    EmitKind.TOKENS: ".tokens",
    EmitKind.AST: ".ast",
    EmitKind.IR: ".ir",
    EmitKind.ASM: ".s",
    EmitKind.ELF: "",
}

#: How `argparse` names the option it could not take a value for.
_WANTED_A_VALUE: Final[re.Pattern[str]] = re.compile(
    r"argument ([^:]+): expected one argument")


def _enum_values(enum_type: type[Enum]) -> str:
    """The accepted values of an enumeration, for a diagnostic."""
    return ", ".join(str(member.value) for member in enum_type)


class _Refused(Exception):
    """Raised where `argparse` would have exited, and caught where it is read."""


class _Reporting(argparse.ArgumentParser):
    """`argparse`, with its refusals turned into numbered diagnostics.

    It neither prints nor exits.  Both are what a library does when it is the
    whole program, and this one is not: what is wrong with a command line is
    reported the way everything else this compiler finds is reported, and the
    run ends where every other run that found something wrong ends.
    """

    def __init__(self, diags: DiagEngine, **rest: object) -> None:
        rest.setdefault("add_help", False)
        rest.setdefault("allow_abbrev", False)
        super().__init__(**rest)  # type: ignore[arg-type]
        self._diags = diags

    def error(self, message: str) -> NoReturn:
        """Report what `argparse` would have exited over."""
        wanted = _WANTED_A_VALUE.match(message)
        if wanted is not None:
            self._diags.emit(D.IMPL_CLI_MISSING_ARGUMENT,
                             option=wanted.group(1).split("/")[0])
        else:
            self._diags.emit(D.IMPL_CLI_BAD_COMMAND_LINE, detail=message)
        raise _Refused()

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        """Never: the driver decides when the run is over."""
        del status, message
        raise _Refused()


def _normalised(argv: Sequence[str]) -> list[str]:
    """The command line with the forms `argparse` misreads written out.

    A value stuck to a short option and a long option whose value may be left
    out are both places where `argparse` would take the next word instead -- so
    `-O` becomes `-O1` and `--color` becomes `--color=yes`.  After `--` nothing
    is an option and nothing is touched.
    """
    out: list[str] = []
    rest = False
    for word in argv:
        if rest:
            out.append(word)
        elif word == "--":
            rest = True
            out.append(word)
        elif word == "-O":
            out.append("-O1")
        elif word == "--color":
            out.append("--color=yes")
        else:
            out.append(word)
    return out


def _with_command(argv: Sequence[str]) -> list[str]:
    """The command line with the command written out, where it was not.

    One whose first word is not a command means `build`, which is what every
    command line meant before there were any.  The first word and not any word:
    an option's value may be spelled like a command, and `-o test prog.pl4g`
    writes a file called `test`.
    """
    if argv and argv[0] in SUBCOMMANDS:
        return list(argv)
    return [BUILD, *argv]


class CommandLine:
    """Turns the words of a command line into options."""

    def __init__(self, diags: DiagEngine) -> None:
        self._diags = diags
        self._options = Options()

    def _parser(self) -> _Reporting:
        """The parser, with one subparser per thing the compiler can be asked."""
        top = _Reporting(self._diags, prog="pypl4g")
        # Each subparser reports the way this one does, which is what the class
        # is for; `argparse` makes them itself, so it is told which to make.
        commands = top.add_subparsers(
            dest="command", parser_class=partial(_Reporting, self._diags))
        for name in SUBCOMMANDS:
            self._declare(commands.add_parser(name))
        return top

    def _declare(self, parser: argparse.ArgumentParser) -> None:
        """Declare every option of the shared table on one subparser.

        All of them on every one: the options are about how the compiler works
        rather than about what it is being asked to do, and one that meant
        something under `build` and nothing under `test` would be a command line
        a reader had to think about.  A test checks this list against the table.
        """
        # Gathered rather than stored, so that a second one is something to
        # refuse rather than something that quietly wins: two names for one
        # output is a command line whose writer meant one of the two.
        parser.add_argument("-o", "--output", action="append", default=[])
        parser.add_argument("--emit")
        parser.add_argument("--target")
        parser.add_argument("--mclevel")
        parser.add_argument("-O", dest="opt_level")
        parser.add_argument("-g", dest="debug_info", action="store_true")
        parser.add_argument("-W", dest="warnings", action="append", default=[])
        parser.add_argument("-Werror", "--Werror", dest="werror",
                            action="store_true")
        parser.add_argument("--color")
        parser.add_argument("--diag-format", dest="diag_format")
        parser.add_argument("--module-path", dest="module_path",
                            action="append", default=[])
        parser.add_argument("--report-log", dest="report_log")
        parser.add_argument("--test-runner", dest="test_runner")
        parser.add_argument("--incremental", action="store_true")
        parser.add_argument("-v", "--verbose", action="store_true")
        parser.add_argument("--time-report", dest="time_report",
                            action="store_true")
        parser.add_argument("--help", dest="show_help", action="store_true")
        parser.add_argument("--help-json", dest="show_help_json",
                            action="store_true")
        parser.add_argument("--print-targets", dest="show_targets",
                            action="store_true")
        parser.add_argument("--version", dest="show_version",
                            action="store_true")
        parser.add_argument("inputs", nargs="*")

    def parse(self, argv: Sequence[str]) -> Options:
        """Parse *argv*, reporting every way it is wrong."""
        words = _with_command(_normalised(argv))
        try:
            found, left = self._parser().parse_known_args(words)
        except _Refused:
            return self._options
        for word in left:
            self._diags.emit(D.IMPL_CLI_UNKNOWN_OPTION, option=word)
        self._take(found)
        if self._options.show_help or self._options.show_help_json \
                or self._options.show_version or self._options.show_targets:
            return self._options
        self._check_required()
        return self._options

    def _take(self, found: argparse.Namespace) -> None:
        """Copy what was parsed into the options, checking each value."""
        options = self._options
        options.command = found.command
        for word in found.inputs:
            self._add_input(word)
        if found.output:
            if len(found.output) > 1:
                self._diags.emit(D.IMPL_CLI_DUPLICATE_OUTPUT,
                                 previous=found.output[0])
            options.output = Path(found.output[-1])
        if found.emit is not None:
            options.emit = self._choice("--emit", found.emit, EmitKind,
                                        options.emit)
        if found.target is not None:
            options.triple = found.target
        if found.mclevel is not None:
            options.mclevel = found.mclevel
        if found.opt_level is not None:
            options.opt_level = self._level(found.opt_level)
        options.debug_info = found.debug_info
        options.warnings_are_errors = found.werror
        for name in found.warnings:
            if name == "error":
                options.warnings_are_errors = True
            elif name.startswith("no-"):
                options.warnings[name[len("no-"):]] = False
            else:
                options.warnings[name] = True
        if found.color is not None:
            options.colour = self._choice("--color", found.color, ColourWhen,
                                          options.colour)
        if found.diag_format is not None:
            options.diag_format = self._choice("--diag-format",
                                               found.diag_format, DiagFormat,
                                               options.diag_format)
        for given in found.module_path:
            options.module_path.extend(parse_search_path(given))
        if found.report_log is not None:
            options.report_log = Path(found.report_log)
        options.test_runner = found.test_runner
        options.incremental = found.incremental
        if found.incremental:
            self._diags.emit(D.IMPL_CLI_NOT_IMPLEMENTED, option="--incremental")
        options.verbose = found.verbose
        options.time_report = found.time_report
        options.show_help = found.show_help
        options.show_help_json = found.show_help_json
        options.show_targets = found.show_targets
        options.show_version = found.show_version

    def _level(self, value: str) -> int:
        """The optimization level, which is one digit."""
        if len(value) == 1 and value.isdigit():
            return int(value)
        self._diags.emit(D.IMPL_CLI_BAD_ARGUMENT, value=value, option="-O",
                         expected="0, 1")
        return self._options.opt_level

    def _choice(self, word: str, value: str, enum_type: type[EnumT],
                fallback: EnumT) -> EnumT:
        """Check a value against the accepted ones of an enumeration."""
        try:
            return enum_type(value)
        except ValueError:
            self._diags.emit(D.IMPL_CLI_BAD_ARGUMENT, value=value,
                             option="".join((word, "=", value)),
                             expected=_enum_values(enum_type))
            return fallback

    def _add_input(self, word: str) -> None:
        """Record a source file, checking its suffix."""
        path = Path(word)
        if path.suffix != SOURCE_SUFFIX:
            self._diags.emit(D.IMPL_CLI_BAD_SUFFIX, path=word)
            return
        self._options.inputs.append(path)

    def _check_required(self) -> None:
        """Report what the command line left out, and settle what it left open.

        A name for the output is not something it has to say: the sources say
        what the program is called, and making the one who knows write it down
        twice would be asking for nothing.
        """
        if not self._options.inputs:
            self._diags.emit(D.IMPL_CLI_NO_INPUT)
            return
        if self._options.output is None:
            first = self._options.inputs[0]
            self._options.output = first.with_name(
                "".join((first.stem, _SUFFIXES[self._options.emit])))


def parse_command_line(argv: Sequence[str], diags: DiagEngine) -> Options:
    """Parse *argv*."""
    return CommandLine(diags).parse(argv)
