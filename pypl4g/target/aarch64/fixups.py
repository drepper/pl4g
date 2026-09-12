"""The AArch64 relocations, and how they are stored.

A relocation here is not a field of its own: the value goes into bits of an
instruction word that already holds the opcode and the registers.  Storing one
is therefore a read, an insert and a write back, and never an overwrite.
"""

from typing import Callable, Final

from ...mc.fixup import (FixupBase, FixupKind, FixupRangeError, MCFixup,
                         signed_fits)

#: An unconditional branch or a call: a signed offset in units of four bytes,
#: measured from the instruction, in the low twenty-six bits.
BRANCH26: Final[FixupKind] = FixupKind("aarch64_branch26", 4, FixupBase.FIELD_START)

#: A conditional branch or a compare-and-branch: nineteen bits at bit five.
BRANCH19: Final[FixupKind] = FixupKind("aarch64_branch19", 4, FixupBase.FIELD_START)

#: The page an address lies in, relative to the page the instruction lies in.
#: Together with the next kind this is how an address is materialized, since the
#: architecture has no instruction that loads one in a single step.
ADR_PAGE21: Final[FixupKind] = FixupKind("aarch64_adr_page21", 4, FixupBase.FIELD_START)

#: The offset of an address within its page, for the add that follows the above.
ADD_LO12: Final[FixupKind] = FixupKind("aarch64_add_lo12", 4)


def _read(data: bytearray, offset: int) -> int:
    """The instruction word at *offset*."""
    return int.from_bytes(data[offset:offset + 4], "little")


def _write(data: bytearray, offset: int, word: int) -> None:
    """Store the instruction word at *offset*."""
    data[offset:offset + 4] = (word & 0xFFFFFFFF).to_bytes(4, "little")


def _insert(data: bytearray, offset: int, value: int, lsb: int, width: int) -> None:
    """Put *value* into a run of bits of the word at *offset*."""
    word = _read(data, offset)
    mask = (1 << width) - 1
    _write(data, offset, (word & ~(mask << lsb)) | ((value & mask) << lsb))


def _branch(width: int, lsb: int) -> Callable[[bytearray, int, MCFixup, int], None]:
    """Build the applier for a branch offset of the given shape."""
    def apply(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
        """Store a branch offset, in units of four bytes."""
        if value & 3:
            raise FixupRangeError(fixup.kind, value)
        scaled = value >> 2
        if not signed_fits(scaled, width):
            raise FixupRangeError(fixup.kind, value)
        _insert(data, offset, scaled, lsb, width)
    return apply


def _apply_adr_page(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store the page difference, which the instruction splits into two pieces."""
    pages = value >> 12
    if not signed_fits(pages, 21):
        raise FixupRangeError(fixup.kind, value)
    _insert(data, offset, pages & 3, 29, 2)
    _insert(data, offset, pages >> 2, 5, 19)


def _apply_add_lo12(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store the offset of an address within its page."""
    del fixup
    _insert(data, offset, value & 0xFFF, 10, 12)


#: How each of this target's relocations is stored.
APPLIERS: Final[dict[FixupKind, Callable[[bytearray, int, MCFixup, int], None]]] = {
    BRANCH26: _branch(26, 0),
    BRANCH19: _branch(19, 5),
    ADR_PAGE21: _apply_adr_page,
    ADD_LO12: _apply_add_lo12,
}


def apply_fixup(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store a computed fixup value into the instruction word that holds it."""
    applier = APPLIERS.get(fixup.kind)
    if applier is None:
        raise FixupRangeError(fixup.kind, value)
    applier(data, offset, fixup, value)
