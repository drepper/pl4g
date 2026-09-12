"""Fragments: the pieces a section is built from.

A fragment whose size can still change during relaxation is kept separate from
plain data, and padding is a fragment of its own because reserved growth slack is
what makes it possible later to patch a function in place without moving
anything around it.
"""

from dataclasses import dataclass, field

from .fixup import MCFixup
from .inst import MCInst
from .symbol import MCSection


@dataclass(slots=True, eq=False)
class MCFragment:
    """Base of everything a section is made of."""

    section: MCSection | None = None
    #: Offset within the section, filled in by the layout.
    offset: int = 0

    @property
    def size(self) -> int:
        """The number of bytes this fragment occupies."""
        return 0

    def bytes_of(self) -> bytes:
        """The bytes this fragment contributes to the image."""
        return b""


@dataclass(slots=True, eq=False)
class MCDataFragment(MCFragment):
    """A run of literal bytes."""

    contents: bytearray = field(default_factory=bytearray)
    fixups: list[MCFixup] = field(default_factory=list)

    @property
    def size(self) -> int:
        """The number of bytes this fragment occupies."""
        return len(self.contents)

    def bytes_of(self) -> bytes:
        """The bytes this fragment contributes to the image."""
        return bytes(self.contents)


@dataclass(slots=True, eq=False)
class MCInstFragment(MCFragment):
    """One instruction, whose encoding may still change during relaxation."""

    inst: MCInst | None = None
    encoded: bytearray = field(default_factory=bytearray)
    fixups: list[MCFixup] = field(default_factory=list)
    #: The wider form to grow into when a displacement does not reach.
    relaxable: bool = False

    @property
    def size(self) -> int:
        """The number of bytes this fragment occupies."""
        return len(self.encoded)

    def bytes_of(self) -> bytes:
        """The bytes this fragment contributes to the image."""
        return bytes(self.encoded)


@dataclass(slots=True, eq=False)
class MCAlignFragment(MCFragment):
    """Padding that brings the next fragment to an alignment boundary."""

    alignment: int = 1
    #: A trap instruction, so that falling into padding stops rather than drifts.
    fill: int = 0xCC
    _size: int = 0

    @property
    def size(self) -> int:
        """The number of bytes this fragment occupies."""
        return self._size

    def set_size(self, value: int) -> None:
        """Record the padding the layout computed."""
        self._size = value

    def bytes_of(self) -> bytes:
        """The bytes this fragment contributes to the image."""
        return bytes((self.fill,)) * self._size


@dataclass(slots=True, eq=False)
class MCPaddingFragment(MCFragment):
    """Growth slack reserved after a function.

    This is the lever for incremental recompilation: a rebuilt function that
    still fits within its own bytes plus this slack can be patched in place,
    leaving every other address in the image untouched.
    """

    reserved: int = 0
    fill: int = 0xCC

    @property
    def size(self) -> int:
        """The number of bytes this fragment occupies."""
        return self.reserved

    def bytes_of(self) -> bytes:
        """The bytes this fragment contributes to the image."""
        return bytes((self.fill,)) * self.reserved
