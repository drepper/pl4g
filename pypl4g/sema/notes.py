"""What the checker learned about the names in a file, kept for an editor.

The compiler has no use for this: it resolves a name, uses what it found and
forgets where it came from.  A language server does have a use for it -- what a
name is, and where it was defined, are two of the three things an editor asks --
so the checker will write it down when it is handed somewhere to write it.

**Nothing is recorded unless something asks.**  A `Checker` built without one of
these does what it always did, and what the recording costs a build is one test of
a variable against nothing per name resolved.  That is why this is a side table
handed in rather than something the checker keeps: a compilation that will never
be asked about a name should not pay for the answer.

**It is about uses, not definitions.**  Where a definition is, and what its
documentation comment says, is in the syntax tree, which whoever asks has already
got; what only the checker knows is what the name half way down a function body
turned out to mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..source.location import INVALID_SPAN, Span

#: What a name turned out to be.  The words are the ones an editor shows, so they
#: are the language's own: a `fn` is a function and a `let` is a variable.
VARIABLE: Final[str] = "variable"
PARAMETER: Final[str] = "parameter"
FUNCTION: Final[str] = "function"
TYPE: Final[str] = "type"
MODULE: Final[str] = "module"


@dataclass(frozen=True, slots=True)
class Note:
    """One name in the source, and what the checker made of it."""

    #: Where the name is written.
    span: Span
    #: Which of the kinds above.
    kind: str
    #: The name itself, as the source wrote it.
    name: str
    #: What it is, rendered the way the language writes a type.
    detail: str
    #: Where it was defined, which may be in another file -- a module's function
    #: is defined in the module.  Nothing where there is nowhere to point: a
    #: built-in type is defined by the specification.
    defined: Span = INVALID_SPAN
    #: The documentation comment of the definition, where it has one.
    doc: str | None = None


class Notes:
    """Every note of one run, and the answer to "what is at this place".

    Kept as a list in the order the checker made them and searched when asked,
    which is what a file of this size wants: a few hundred names, one question
    per keystroke that lands on a name, and nothing to keep in step.
    """

    __slots__ = ("_found",)

    def __init__(self) -> None:
        self._found: list[Note] = []

    def add(self, note: Note) -> None:
        """Write one down."""
        self._found.append(note)

    def at(self, loc: int) -> Note | None:
        """The note over *loc*, the narrowest where several cover it.

        The narrowest because a name may be inside something else that is also a
        name -- `a.b` is a member of a module, and `a` is the module -- and what
        the cursor is on is the smaller of the two.
        """
        best: Note | None = None
        for note in self._found:
            if not note.span.is_valid or not note.span.start <= loc < note.span.end:
                continue
            if best is None or (note.span.end - note.span.start) < \
                    (best.span.end - best.span.start):
                best = note
        return best

    def __len__(self) -> int:
        """How many there are, which is what a test asks."""
        return len(self._found)
