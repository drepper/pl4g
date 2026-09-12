"""The RISC-V relocations, and how they are stored.

The architecture scatters the bits of a jump offset across the instruction word:
the sign bit sits at the top, then ten bits of the middle, then one bit from
further up, then the rest.  Reassembling that is the whole of the work here, and
it is why storing a relocated value has to be the target's business rather than
a shared one.
"""

from typing import Callable, Final

from ...mc.fixedwidth import insert_bits, read_word, write_word
from ...mc.fixup import (FixupBase, FixupKind, FixupRangeError, MCFixup,
                         signed_fits)
from .desc import INSTRUCTION_SIZE

#: A jump or a call: a signed offset of twenty-one bits, measured from the
#: instruction, whose low bit is always zero and is not stored.
JAL: Final[FixupKind] = FixupKind("riscv_jal", INSTRUCTION_SIZE, FixupBase.FIELD_START)

#: A conditional branch: the same idea over thirteen bits, scattered differently.
BRANCH: Final[FixupKind] = FixupKind("riscv_branch", INSTRUCTION_SIZE,
                                     FixupBase.FIELD_START)

#: The upper twenty bits of a program-counter-relative address, for the first
#: instruction of the pair that computes one.
PCREL_HI20: Final[FixupKind] = FixupKind("riscv_pcrel_hi20", INSTRUCTION_SIZE,
                                         FixupBase.FIELD_START)

#: The lower twelve bits, for the second instruction of that pair.  It is
#: measured from the *first* instruction, not from itself, which is what the
#: fixup's base adjustment is for.
PCREL_LO12_I: Final[FixupKind] = FixupKind("riscv_pcrel_lo12_i", INSTRUCTION_SIZE,
                                           FixupBase.FIELD_START)

#: How far the second instruction of the pair sits after the first.
PCREL_PAIR_DISTANCE: Final[int] = INSTRUCTION_SIZE


def _apply_jal(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store a jump offset, which the instruction keeps in four pieces."""
    if value & 1:
        raise FixupRangeError(fixup.kind, value)
    if not signed_fits(value, 21):
        raise FixupRangeError(fixup.kind, value)
    bits = (((value >> 20) & 0x1) << 31
            | ((value >> 1) & 0x3FF) << 21
            | ((value >> 11) & 0x1) << 20
            | ((value >> 12) & 0xFF) << 12)
    word = read_word(data, offset, INSTRUCTION_SIZE)
    write_word(data, offset, INSTRUCTION_SIZE, (word & 0x00000FFF) | bits)


def _apply_branch(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store a conditional branch offset, kept in four pieces of its own."""
    if value & 1:
        raise FixupRangeError(fixup.kind, value)
    if not signed_fits(value, 13):
        raise FixupRangeError(fixup.kind, value)
    insert_bits(data, offset, INSTRUCTION_SIZE, (value >> 12) & 0x1, 31, 1)
    insert_bits(data, offset, INSTRUCTION_SIZE, (value >> 5) & 0x3F, 25, 6)
    insert_bits(data, offset, INSTRUCTION_SIZE, (value >> 1) & 0xF, 8, 4)
    insert_bits(data, offset, INSTRUCTION_SIZE, (value >> 11) & 0x1, 7, 1)


def _apply_pcrel_hi20(data: bytearray, offset: int, fixup: MCFixup,
                      value: int) -> None:
    """Store the upper bits of an address, rounded so the lower half can be signed.

    The instruction that follows adds a twelve-bit *signed* immediate, so half
    the time it subtracts.  Adding half a page here is what makes the two halves
    come to the right sum either way.
    """
    del fixup
    insert_bits(data, offset, INSTRUCTION_SIZE, (value + 0x800) >> 12, 12, 20)


def _apply_pcrel_lo12(data: bytearray, offset: int, fixup: MCFixup,
                      value: int) -> None:
    """Store the lower twelve bits of an address."""
    del fixup
    insert_bits(data, offset, INSTRUCTION_SIZE, value & 0xFFF, 20, 12)


#: How each of this target's relocations is stored.
APPLIERS: Final[dict[FixupKind, Callable[[bytearray, int, MCFixup, int], None]]] = {
    JAL: _apply_jal,
    BRANCH: _apply_branch,
    PCREL_HI20: _apply_pcrel_hi20,
    PCREL_LO12_I: _apply_pcrel_lo12,
}


def apply_fixup(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store a computed fixup value into the instruction word that holds it."""
    applier = APPLIERS.get(fixup.kind)
    if applier is None:
        raise FixupRangeError(fixup.kind, value)
    applier(data, offset, fixup, value)
