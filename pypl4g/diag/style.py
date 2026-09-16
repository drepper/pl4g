"""What a diagnostic looks like on a terminal that can show colour.

Colour is decoration and never information: everything a colour says here is
said by the text as well, so a terminal that shows none loses nothing.  That is
why the default is to look -- a terminal gets colour and a pipe does not -- and
why `NO_COLOR` is honoured whatever the option says, a program reading this
output being the one case where a decision has already been made.

The codes are the eight colours and the two attributes every terminal has had
since the 1970s.  A palette of 256 would look better on the terminals that have
them and worse on the ones that do not, and what is gained is a shade.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Final, TextIO

#: What ends every run of colour.  One code rather than the exact opposite of
#: what was turned on, because what follows a diagnostic is not this program's.
RESET: Final[str] = "\N{ESCAPE}[0m"

BOLD: Final[str] = "1"
DIM: Final[str] = "2"
RED: Final[str] = "31"
GREEN: Final[str] = "32"
YELLOW: Final[str] = "33"
BLUE: Final[str] = "34"
MAGENTA: Final[str] = "35"
CYAN: Final[str] = "36"


class ColourWhen(Enum):
    """When the compiler writes colour."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


#: How each severity is written.  Red for what stops the compilation, yellow for
#: what does not, and the quieter cyan for a note, which belongs to the thing
#: above it rather than standing on its own.
_SEVERITIES: Final[dict[str, str]] = {
    "fatal": ";".join((BOLD, RED)),
    "error": ";".join((BOLD, RED)),
    "warning": ";".join((BOLD, YELLOW)),
    "note": ";".join((BOLD, CYAN)),
}

#: How each thing the highlighter finds is written.  The names are tree-sitter's
#: own capture names, and a capture the table does not know is written plain --
#: which is what makes a query that gains a name a thing that needs no change
#: here.  Looked up by the longest prefix, so that `keyword.modifier` follows
#: `keyword` unless it is named outright.
_CAPTURES: Final[dict[str, str]] = {
    "attribute": CYAN,
    "boolean": YELLOW,
    "comment": DIM,
    "function": BLUE,
    "keyword": MAGENTA,
    "module": BLUE,
    "number": YELLOW,
    "property": CYAN,
    "string": GREEN,
    "type": CYAN,
}


def _wanted(when: ColourWhen, stream: TextIO) -> bool:
    """Whether to write colour at all.

    `NO_COLOR` wins over `always` because a program that sets it has said it is
    reading this, and nothing a command line says about how output looks is
    about that.  A terminal calling itself dumb is taken at its word.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if when is ColourWhen.NEVER:
        return False
    if when is ColourWhen.ALWAYS:
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class Palette:
    """The codes a diagnostic is written with, or nothing where there is none.

    One object either way, so that nothing that renders has to ask whether there
    is colour: without it every method hands back what it was given.
    """

    on: bool = False

    @staticmethod
    def chosen(when: ColourWhen, stream: TextIO) -> Palette:
        """The palette for writing to *stream* under *when*."""
        return Palette(on=_wanted(when, stream))

    def _in(self, code: str, text: str) -> str:
        """*text* written in *code*, or as it stands where there is no colour."""
        if not self.on or not text:
            return text
        return "".join(("\N{ESCAPE}[", code, "m", text, RESET))

    def severity(self, word: str) -> str:
        """The severity, which is what a reader looks for first."""
        return self._in(_SEVERITIES.get(word, BOLD), word)

    def message(self, text: str) -> str:
        """What the diagnostic says, which is the rest of what is read."""
        return self._in(BOLD, text)

    def where(self, text: str) -> str:
        """The file, line and column."""
        return self._in(BOLD, text)

    def number(self, text: str) -> str:
        """The number in brackets, which is looked at only when it is wanted."""
        return self._in(DIM, text)

    def gutter(self, text: str) -> str:
        """The line number and the bar beside the source."""
        return self._in(DIM, text)

    def caret(self, text: str) -> str:
        """The carets under the span, which say where rather than what."""
        return self._in(";".join((BOLD, GREEN)), text)

    def capture(self, name: str, text: str) -> str:
        """A piece of source, written as what the highlighter called it.

        A name the table does not know falls back to what it is a kind of --
        `keyword.modifier` to `keyword` -- and then to no colour at all.
        """
        while name:
            code = _CAPTURES.get(name)
            if code is not None:
                return self._in(code, text)
            name = name.rpartition(".")[0]
        return text
