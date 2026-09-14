"""Parsing the command line.

Written by hand rather than with a general argument parser, so that every way the
command line can be wrong maps onto a numbered diagnostic of the shared catalog
rather than onto text of some library's choosing.
"""

from enum import Enum
from pathlib import Path

from ..sema.modules import parse_search_path
from typing import Sequence, TypeVar

from ..diag import ids as D
from ..diag.engine import DiagEngine
from .options import (DiagFormat, EmitKind, Options, SOURCE_SUFFIX)


EnumT = TypeVar("EnumT", bound=Enum)


def _enum_values(enum_type: type[Enum]) -> str:
    """The accepted values of an enumeration, for a diagnostic."""
    return ", ".join(str(member.value) for member in enum_type)


class CommandLine:
    """Turns the words of a command line into options."""

    def __init__(self, diags: DiagEngine) -> None:
        self._diags = diags
        self._options = Options()

    def parse(self, argv: Sequence[str]) -> Options:
        """Parse *argv*, reporting every way it is wrong."""
        index = 0
        while index < len(argv):
            word = argv[index]
            index += 1
            if word == "--":
                for rest in argv[index:]:
                    self._add_input(rest)
                break
            if word.startswith("-") and word != "-":
                index = self._parse_option(word, argv, index)
            else:
                self._add_input(word)
        if self._options.show_help or self._options.show_help_json \
                or self._options.show_version or self._options.show_targets:
            return self._options
        self._check_required()
        return self._options

    # -- options ---------------------------------------------------------------

    def _parse_option(self, word: str, argv: Sequence[str], index: int) -> int:
        """Parse one option, returning the new position in *argv*."""
        match word:
            case "--help":
                self._options.show_help = True
            case "--help-json":
                self._options.show_help_json = True
            case "--print-targets":
                self._options.show_targets = True
            case "--version":
                self._options.show_version = True
            case "-v" | "--verbose":
                self._options.verbose = True
            case "--time-report":
                self._options.time_report = True
            case "-g":
                self._options.debug_info = True
            case "-Werror":
                self._options.warnings_are_errors = True
            case "--incremental":
                self._options.incremental = True
                self._diags.emit(D.IMPL_CLI_NOT_IMPLEMENTED, option=word)
            case _:
                return self._parse_valued_option(word, argv, index)
        return index

    def _parse_valued_option(self, word: str, argv: Sequence[str], index: int) -> int:
        """Parse an option that carries a value."""
        if word == "-o" or word == "--output":
            value, index = self._value_of(word, argv, index)
            if value is not None:
                self._set_output(Path(value), word)
            return index
        if word.startswith("-o") and len(word) > 2:
            self._set_output(Path(word[2:]), word)
            return index
        if word.startswith("--output="):
            self._set_output(Path(word[len("--output="):]), word)
            return index
        if word.startswith("--emit="):
            self._options.emit = self._choice(word, word[len("--emit="):], EmitKind,
                                              self._options.emit)
            return index
        if word.startswith("--diag-format="):
            self._options.diag_format = self._choice(
                word, word[len("--diag-format="):], DiagFormat, self._options.diag_format)
            return index
        if word.startswith("--target="):
            self._options.triple = word[len("--target="):]
            return index
        if word.startswith("--mclevel="):
            self._options.mclevel = word[len("--mclevel="):]
            return index
        if word.startswith("--module-path="):
            self._options.module_path.extend(
                parse_search_path(word[len("--module-path="):]))
            return index
        if word == "--module-path":
            value, index = self._value_of(word, argv, index)
            if value is not None:
                self._options.module_path.extend(parse_search_path(value))
            return index
        if word.startswith("--decision-log="):
            self._options.decision_log = Path(word[len("--decision-log="):])
            return index
        if word == "--decision-log":
            value, index = self._value_of(word, argv, index)
            if value is not None:
                self._options.decision_log = Path(value)
            return index
        if word.startswith("-O") and len(word) == 3 and word[2].isdigit():
            self._options.opt_level = int(word[2])
            return index
        if word == "-O":
            self._options.opt_level = 1
            return index
        if word.startswith("-Wno-"):
            self._options.warnings[word[len("-Wno-"):]] = False
            return index
        if word.startswith("-W") and len(word) > 2:
            self._options.warnings[word[2:]] = True
            return index
        self._diags.emit(D.IMPL_CLI_UNKNOWN_OPTION, option=word)
        return index

    def _value_of(self, word: str, argv: Sequence[str],
                  index: int) -> tuple[str | None, int]:
        """Take the separate value of an option."""
        if index >= len(argv):
            self._diags.emit(D.IMPL_CLI_MISSING_ARGUMENT, option=word)
            return None, index
        return argv[index], index + 1

    def _choice(self, word: str, value: str, enum_type: type[EnumT],
                fallback: EnumT) -> EnumT:
        """Check a value against the accepted ones of an enumeration."""
        try:
            return enum_type(value)
        except ValueError:
            self._diags.emit(D.IMPL_CLI_BAD_ARGUMENT, value=value, option=word,
                             expected=_enum_values(enum_type))
            return fallback

    def _set_output(self, path: Path, word: str) -> None:
        """Record the output file, refusing a second one."""
        del word
        if self._options.output is not None:
            self._diags.emit(D.IMPL_CLI_DUPLICATE_OUTPUT,
                             previous=self._options.output.as_posix())
            return
        self._options.output = path

    def _add_input(self, word: str) -> None:
        """Record a source file, checking its suffix."""
        path = Path(word)
        if path.suffix != SOURCE_SUFFIX:
            self._diags.emit(D.IMPL_CLI_BAD_SUFFIX, path=word)
            return
        self._options.inputs.append(path)

    def _check_required(self) -> None:
        """Report what the command line left out."""
        if self._options.output is None:
            self._diags.emit(D.IMPL_CLI_MISSING_OUTPUT)
        if not self._options.inputs:
            self._diags.emit(D.IMPL_CLI_NO_INPUT)


def parse_command_line(argv: Sequence[str], diags: DiagEngine) -> Options:
    """Parse *argv*."""
    return CommandLine(diags).parse(argv)
