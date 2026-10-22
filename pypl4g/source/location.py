"""Positions in the source text.

A position is a single integer offset into a per-compilation global coordinate
space, so that a span is two integers and carries no reference to a file object.
The mapping back to a file, line and column is done only when a diagnostic is
rendered, which is the rare case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

type FileId = int

#: Offset of a character in the global coordinate space of one compilation.
type Loc = int

NO_LOC: Final[Loc] = -1


# Not frozen, and hashed by its fields as a frozen one is: nothing changes a
# span once it is made, and a frozen dataclass sets each field through
# `object.__setattr__`, which is half of what making one costs -- and every
# token and every node of the tree makes one.
@dataclass(slots=True, unsafe_hash=True)
class Span:
    """A half-open range of source text, ``[start, end)``."""

    start: Loc
    end: Loc

    def __post_init__(self) -> None:
        assert self.end >= self.start

    @property
    def is_valid(self) -> bool:
        """Whether the span refers to real source text."""
        return self.start >= 0

    def to(self, other: Span) -> Span:
        """Return the smallest span covering both this span and *other*."""
        return Span(min(self.start, other.start), max(self.end, other.end))


INVALID_SPAN: Final[Span] = Span(NO_LOC, NO_LOC)


@dataclass(frozen=True, slots=True)
class Position:
    """A fully resolved position, as it appears in a rendered diagnostic."""

    path: str
    line: int
    column: int
