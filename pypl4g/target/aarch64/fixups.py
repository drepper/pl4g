"""The AArch64 relocations, and how they are stored.

A relocation here is not a field of its own: the value goes into bits of an
instruction word that already holds the opcode and the registers.  Storing one
is therefore a read, an insert and a write back, and never an overwrite.
"""

from __future__ import annotations

from typing import Callable, Final

from ...mc.fixedwidth import insert_bits
from ...mc.fixup import (ABS32, ABS64, PCREL32, PCREL32_AT_FIELD, PCREL32_AT_FIELD, FixupBase, FixupKind,
                         FixupRangeError, MCFixup, signed_fits)
from .desc import INSTRUCTION_SIZE

#: An unconditional branch or a call: a signed offset in units of four bytes,
#: measured from the instruction, in the low twenty-six bits.
BRANCH26: Final[FixupKind] = FixupKind("aarch64_branch26", 4, FixupBase.FIELD_START)

#: A conditional branch or a compare-and-branch: nineteen bits at bit five.
BRANCH19: Final[FixupKind] = FixupKind("aarch64_branch19", 4, FixupBase.FIELD_START)

#: The page an address lies in, relative to the page the instruction lies in.
#: Together with the next kind this is how an address is materialized, since the
#: architecture has no instruction that loads one in a single step.  The value is
#: the difference of the two *pages*: an instruction twelve bytes before its
#: target may still be a page away from it.
ADR_PAGE21: Final[FixupKind] = FixupKind("aarch64_adr_page21", 4, FixupBase.PAGE_4K)

#: The offset of an address within its page, for the add that follows the above.
ADD_LO12: Final[FixupKind] = FixupKind("aarch64_add_lo12", 4)

#: The same offset, for a load or a store that follows the page instead of an
#: add.  Those carry the offset in units of what they touch, so there is one
#: kind per width and each scales the value by its own: what goes in the
#: instruction is the offset divided by how many bytes it reads or writes.  The
#: runtime built from C uses them where a compiler chose to reach a constant
#: directly rather than compute its address first.
LDST8_LO12: Final[FixupKind] = FixupKind("aarch64_ldst8_lo12", 4)
LDST16_LO12: Final[FixupKind] = FixupKind("aarch64_ldst16_lo12", 4)
LDST32_LO12: Final[FixupKind] = FixupKind("aarch64_ldst32_lo12", 4)
LDST64_LO12: Final[FixupKind] = FixupKind("aarch64_ldst64_lo12", 4)
LDST128_LO12: Final[FixupKind] = FixupKind("aarch64_ldst128_lo12", 4)


def _insert(data: bytearray, offset: int, value: int, lsb: int, width: int) -> None:
    """Put *value* into a run of bits of the instruction word at *offset*."""
    insert_bits(data, offset, INSTRUCTION_SIZE, value, lsb, width)


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


def _lo12_scaled(bytes_touched: int) -> Callable[[bytearray, int, MCFixup, int], None]:
    """Build the applier for a load or store's offset of the given width."""
    def apply(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
        """Store the offset within the page, in units of what is touched."""
        within = value & 0xFFF
        if within % bytes_touched:
            raise FixupRangeError(fixup.kind, value)
        _insert(data, offset, within // bytes_touched, 10, 12)
    return apply


#: How each of this target's relocations is stored.
APPLIERS: Final[dict[FixupKind, Callable[[bytearray, int, MCFixup, int], None]]] = {
    BRANCH26: _branch(26, 0),
    BRANCH19: _branch(19, 5),
    ADR_PAGE21: _apply_adr_page,
    ADD_LO12: _apply_add_lo12,
    LDST8_LO12: _lo12_scaled(1),
    LDST16_LO12: _lo12_scaled(2),
    LDST32_LO12: _lo12_scaled(4),
    LDST64_LO12: _lo12_scaled(8),
    LDST128_LO12: _lo12_scaled(16),
}


#: Which of these kinds the object format's relocation types come to.  A number
#: here is a promise about what `bin/pl4g-runtime` may find in the runtime's
#: object; one it finds and this does not have stops the extraction rather than
#: being filled in wrongly.
FROM_ELF: Final[dict[int, str]] = {
    257: ABS64.name,           # R_AARCH64_ABS64
    275: ADR_PAGE21.name,      # R_AARCH64_ADR_PREL_PG_HI21
    277: ADD_LO12.name,        # R_AARCH64_ADD_ABS_LO12_NC
    278: LDST8_LO12.name,      # R_AARCH64_LDST8_ABS_LO12_NC
    282: BRANCH26.name,        # R_AARCH64_JUMP26
    283: BRANCH26.name,        # R_AARCH64_CALL26
    284: LDST16_LO12.name,     # R_AARCH64_LDST16_ABS_LO12_NC
    285: LDST32_LO12.name,     # R_AARCH64_LDST32_ABS_LO12_NC
    286: LDST64_LO12.name,     # R_AARCH64_LDST64_ABS_LO12_NC
    299: LDST128_LO12.name,    # R_AARCH64_LDST128_ABS_LO12_NC
}


def apply_fixup(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store a computed fixup value into the instruction word that holds it."""
    applier = APPLIERS.get(fixup.kind)
    if applier is None:
        raise FixupRangeError(fixup.kind, value)
    applier(data, offset, fixup, value)

#: Every kind a packaged patch may name, by the name the patch carries.  The
#: generic ones are here too: a relocation reaching a whole address uses the
#: same field every target does.
BY_NAME: Final[dict[str, FixupKind]] = {
    one.name: one for one in
    (ABS32, ABS64, PCREL32, BRANCH26, BRANCH19, ADR_PAGE21, ADD_LO12,
     LDST8_LO12, LDST16_LO12, LDST32_LO12, LDST64_LO12, LDST128_LO12)}
