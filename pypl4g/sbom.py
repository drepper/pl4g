"""What went into the binary, written into the binary.

Every image carries a `.sbom` section naming the compiler, every source that
was read and every definition that was compiled, each with a hash beside it.
The section is always emitted: an SBOM that a flag turns off is one nobody can
rely on being there, and the question it answers -- what is this built from --
is one asked of binaries nobody thought to ask about at the time.

**What is hashed is the tokens and not the text.**  A program means the same
thing written with indentation or with braces, with one space or four, and a
hash over the bytes would say the two are different things.  So the token
stream is normalized first: the marks that only say where a block begins and
ends become one mark each, whichever way they were written, and everything that
carries meaning is written out as what it is.  Two spellings of one program
therefore have one hash, which is the only way a hash of a definition is worth
recording.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final, Iterable, Sequence

from .front.token import TokKind, Token

#: What this compiler calls itself.  The compiler's entry in the table is the
#: hash of this string, so it is bumped when what the compiler produces changes
#: in a way a consumer of the table would want to know about.
COMPILER_ID: Final[str] = "pypl4g 0.1"

#: Where the table and its strings go.  The names are the compiler's own and
#: not any standard's: there is no agreed section for this, and a name that
#: says what is in it is worth more than one that looks official.
SECTION: Final[str] = ".sbom"
STRINGS: Final[str] = ".sbomstr"


class Tag(IntEnum):
    """What one row of the table is about.

    The numbers are part of the format: a reader that does not know a tag skips
    the row, so a tag added later costs nothing.
    """

    COMPILER = 1
    #: One source file, hashed as its tokens.
    SOURCE = 2
    #: Every source, in the order they were read, hashed as their hashes rather
    #: than as their contents -- which says the same thing and is what lets the
    #: whole be checked without the parts being read again.
    SOURCES = 3
    FUNCTION = 4
    TYPE = 5
    VARIABLE = 6
    UNIT = 7


#: The marks that say where a block begins and ends, whichever notation was
#: used.  A colon and a newline and an indent, or a brace: one thing, written
#: two ways, and what is hashed is the thing.
_BEGINS: Final[str] = "\x01"
_ENDS: Final[str] = "\x02"
#: What separates two statements, whether a newline or a semicolon said so.
_SEPARATES: Final[str] = "\x03"


def normalized(tokens: Sequence[Token]) -> list[str]:
    """The token stream as what it means, with the layout taken out of it.

    A block's begin and end become one mark each however they were written, the
    colon that introduces a layout block goes away because the mark after it
    already said so, and a statement separator is one mark whether a newline or
    a semicolon was written.  What is left is every token that carries meaning,
    written as its kind and its text -- so that a name, a number and an operator
    are told apart by more than the characters they happen to share.
    """
    made: list[str] = []
    at = 0
    while at < len(tokens):
        token = tokens[at]
        match token.kind:
            case TokKind.EOF:
                pass
            case TokKind.COLON if _opens_a_block(tokens, at):
                # The indent that follows says a block begins; this says it a
                # second time, and only in one of the two notations.
                pass
            case TokKind.INDENT | TokKind.LBRACE:
                made.append(_BEGINS)
            case TokKind.DEDENT | TokKind.RBRACE:
                # A block written by indentation ends after the newline that
                # ended its last statement; one written with braces does not.
                if made and made[-1] == _SEPARATES \
                        and token.kind is TokKind.DEDENT:
                    made.pop()
                made.append(_ENDS)
            case TokKind.NEWLINE if at + 1 < len(tokens) \
                    and tokens[at + 1].kind is TokKind.INDENT:
                # The end of the line a block's colon was written on, which
                # separates nothing: the indent after it is what begins the
                # block, and a block written with braces has neither.
                pass
            case TokKind.NEWLINE | TokKind.SEMICOLON:
                made.append(_SEPARATES)
            case _:
                made.append("".join((token.kind.name, " ", _text_of(token))))
        at += 1
    while made and made[-1] == _SEPARATES:
        # A file ends with a newline and need not; what follows the last
        # statement is nothing either way.
        made.pop()
    at = 0
    while at < len(made) and made[at] in (_ENDS, _SEPARATES):
        # Nothing begins by ending a block.  A dedent stands at the first
        # column of the line that follows the block it closes, which is the
        # line the next definition begins on, so one definition's blocks close
        # inside the next one's span -- and what is hashed here is the
        # definition and not where it happened to be written.
        at += 1
    return made[at:]


def _opens_a_block(tokens: Sequence[Token], at: int) -> bool:
    """Whether the colon at *at* is the one that introduces a layout block.

    A colon is written in four places -- a variable's type, a parameter's, a
    dictionary's key and a block -- and only the last is layout.  Which it is
    can be told from the two tokens after it and from nothing else, which is
    what keeps this a rule about the token stream rather than about the tree.
    """
    return (at + 2 < len(tokens)
            and tokens[at + 1].kind is TokKind.NEWLINE
            and tokens[at + 2].kind is TokKind.INDENT)


def _text_of(token: Token) -> str:
    """What a token holds, written so that two different tokens differ.

    A literal is written as the value it stands for rather than as the
    characters it was written with, so that `0x10` and `16` are one number and
    `1_000` and `1000` are one number -- which is the same rule the layout
    follows, applied to what a number is.
    """
    if token.int_value is not None:
        return "".join((str(token.int_value), " ", token.int_type or ""))
    if token.float_value is not None:
        return "".join((repr(token.float_value), " ", token.float_type or ""))
    if token.str_value is not None:
        return token.str_value
    return token.text


def digest_of(tokens: Sequence[Token]) -> str:
    """The hash of a normalized token stream, as the hex a reader would check."""
    made = hashlib.sha256()
    for piece in normalized(tokens):
        made.update(piece.encode("utf-8"))
        made.update(b"\0")
    return made.hexdigest()


def digest_of_text(text: str) -> str:
    """The hash of a string, for the entries that are about no source at all."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of_digests(digests: Iterable[str]) -> str:
    """The hash of several hashes, in the order they were given.

    What the whole is, said without reading the parts again: two builds whose
    sources each hash alike and were read in the same order have the same one.
    """
    made = hashlib.sha256()
    for one in digests:
        made.update(one.encode("ascii"))
        made.update(b"\0")
    return made.hexdigest()


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of the table: what is hashed, what it is called, and the hash."""

    tag: Tag
    name: str
    digest: str


@dataclass(slots=True)
class Table:
    """The bytes of the two sections, and what is in them."""

    rows: bytes = b""
    strings: bytes = b""
    entries: list[Entry] = field(default_factory=list)


#: Each row is three four-byte fields: where the hash is, what kind of thing
#: this is, and where the name is.  Four bytes is more string table than any
#: program will have and leaves the row a size a reader can step by.
ROW_SIZE: Final[int] = 12


def build(entries: Sequence[Entry], little_endian: bool) -> Table:
    """The table and its strings, laid out as they go into the image."""
    order = "little" if little_endian else "big"
    # A string table begins with a nought byte, so that offset zero is the
    # empty string -- which is what every reader of one expects.
    blob = bytearray(b"\0")
    where: dict[str, int] = {"": 0}

    def put(text: str) -> int:
        found = where.get(text)
        if found is None:
            found = len(blob)
            blob.extend(text.encode("utf-8"))
            blob.append(0)
            where[text] = found
        return found

    rows = bytearray()
    for entry in entries:
        rows.extend(put(entry.digest).to_bytes(4, order))
        rows.extend(int(entry.tag).to_bytes(4, order))
        rows.extend(put(entry.name).to_bytes(4, order))
    return Table(rows=bytes(rows), strings=bytes(blob), entries=list(entries))


def entries_for(read_units: Sequence[object]) -> list[Entry]:
    """Every row the table holds, in the order it holds them.

    The compiler first, then one row per source in the order the sources were
    read, then the hash of those hashes, and then one row per definition.  The
    order is the order a reader would want to check them in, and the hash of the
    whole comes after the parts for the same reason.
    """
    made = [Entry(Tag.COMPILER, COMPILER_ID, digest_of_text(COMPILER_ID))]
    each: list[str] = []
    for one in read_units:
        digest = digest_of(one.tokens)                 # type: ignore[attr-defined]
        each.append(digest)
        made.append(Entry(Tag.SOURCE,
                          one.path.as_posix(), digest))  # type: ignore[attr-defined]
    made.append(Entry(Tag.SOURCES, "sources", digest_of_digests(each)))
    for one in read_units:
        made.extend(_definitions_of(one))
    return made


#: What each kind of definition is called in the table.  A module import is not
#: here: what it brings in is a source of its own, and that source has a row.
_TAGS: Final[dict[str, Tag]] = {
    "FuncDef": Tag.FUNCTION,
    "TypeDef": Tag.TYPE,
    "EnumDef": Tag.TYPE,
    "VarDef": Tag.VARIABLE,
    "UnitDef": Tag.UNIT,
}


def _definitions_of(read: object) -> list[Entry]:
    """One row per definition of one source file."""
    made: list[Entry] = []
    tokens = read.tokens                               # type: ignore[attr-defined]
    for item in read.unit.items:                       # type: ignore[attr-defined]
        tag = _TAGS.get(type(item).__name__)
        if tag is None:
            continue
        span = item.span
        for attr in getattr(item, "attrs", ()):
            # What a definition says about itself is part of what it is, so an
            # attribute is inside the hash even though it is written before the
            # keyword the definition's own span begins at.
            span = span.to(attr.span)
        held = [t for t in tokens
                if t.span.start >= span.start and t.span.end <= span.end]
        name = getattr(item, "name", "") or "\N{RIGHTWARDS ARROW}"
        made.append(Entry(tag, name, digest_of(held)))
    return made
