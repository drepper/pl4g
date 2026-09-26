"""Where the compiler's idea of a place and the editor's meet.

The compiler counts **characters** and numbers lines and columns from one.  The
protocol counts lines from nought and, along a line, counts in whatever unit the
two ends agreed on -- sixteen-bit units by default, because that is what the
editor the protocol was designed for used.  This language is written in glyphs
that are three bytes each and it can hold a character outside the basic plane
(`"a\N{POUND SIGN}\N{EURO SIGN}\N{LINEAR B SYLLABLE B008 A}"` is in the test
suite), so the two ends disagree about every column of every interesting line
unless something converts.  This is that something, and it is why the server
offers all three units and takes whichever the editor likes best.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import quote, unquote, urlparse

from ..source.location import Span
from ..source.manager import SourceManager


class Encoding(StrEnum):
    """How far along a line a position is counted, as the protocol spells it."""

    UTF8 = "utf-8"
    UTF16 = "utf-16"
    UTF32 = "utf-32"


#: Which to ask for, best first.  `utf-32` is a character count, which is what
#: the compiler already has; `utf-8` is a byte count, which costs one encode of
#: the line; `utf-16` costs the same and is the one the protocol falls back to,
#: so it is what a client that says nothing gets.
PREFERRED: Final[tuple[Encoding, ...]] = (Encoding.UTF32, Encoding.UTF8,
                                          Encoding.UTF16)

#: What the protocol means where neither end said anything.
DEFAULT: Final[Encoding] = Encoding.UTF16


def agreed(offered: object) -> Encoding:
    """The encoding to use, given what the client said it could read.

    A client that says nothing means the default, which the protocol settled
    before it had any others.
    """
    if not isinstance(offered, list):
        return DEFAULT
    names = {str(one) for one in offered}
    for one in PREFERRED:
        if str(one) in names:
            return one
    return DEFAULT


def along(line: str, characters: int, encoding: Encoding) -> int:
    """How far along *line* a column of *characters* is, in *encoding*'s units."""
    cut = line[:characters]
    match encoding:
        case Encoding.UTF32:
            return len(cut)
        case Encoding.UTF8:
            return len(cut.encode("utf-8"))
        case _:
            return len(cut.encode("utf-16-le")) // 2


def characters(line: str, offset: int, encoding: Encoding) -> int:
    """The other way: how many characters into *line* an *offset* is.

    An offset that falls inside a character -- which a client can send, a
    sixteen-bit unit being half of some of them -- answers with the character it
    fell into rather than refusing: what the editor meant is the thing the cursor
    is on.
    """
    if encoding is Encoding.UTF32:
        return min(offset, len(line))
    unit = "utf-8" if encoding is Encoding.UTF8 else "utf-16-le"
    width = 1 if encoding is Encoding.UTF8 else 2
    so_far = 0
    for index, letter in enumerate(line):
        if so_far >= offset:
            return index
        so_far += len(letter.encode(unit)) // width
    return len(line)


def position_of(sources: SourceManager, loc: int, encoding: Encoding
                ) -> dict[str, int] | None:
    """One end of a span, as a position the protocol understands."""
    found = sources.position(loc)
    line = sources.line_text(loc)
    if found is None or line is None:
        return None
    return {"line": found.line - 1,
            "character": along(line, found.column - 1, encoding)}


def range_of(sources: SourceManager, span: Span, encoding: Encoding
             ) -> dict[str, dict[str, int]] | None:
    """A span of the compiler's as a range of the editor's.

    A span is half-open and so is a range, so the two ends convert one at a
    time and nothing is added or taken away.  A span the compiler could not
    place -- which is what it uses where something is wrong with no particular
    piece of the text -- has no range, and what wants one puts it at the start
    of the file instead.
    """
    if not span.is_valid:
        return None
    start = position_of(sources, span.start, encoding)
    end = position_of(sources, span.end, encoding)
    if start is None:
        return None
    return {"start": start, "end": end if end is not None else start}


#: Where a diagnostic goes that names no place in the text.  The first character
#: of the file: an editor has to put it somewhere, and somewhere a reader will
#: see it is better than nowhere.
WHOLE_FILE: Final[dict[str, dict[str, int]]] = {
    "start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}}


def to_uri(path: Path) -> str:
    """The name an editor knows a file by, given the name the compiler does."""
    return "".join(("file://", quote(str(path.resolve()))))


def from_uri(uri: str) -> Path | None:
    """And back again, for the only scheme a compiler can read from.

    Nothing else is a file this can compile -- an untitled buffer has no
    directory to look for a module in -- so anything else answers with nothing
    and is left alone rather than guessed at.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    return Path(unquote(parsed.path))
