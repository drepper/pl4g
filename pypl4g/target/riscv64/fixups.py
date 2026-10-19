"""The RISC-V relocations, and how they are stored.

The architecture scatters the bits of a jump offset across the instruction word:
the sign bit sits at the top, then ten bits of the middle, then one bit from
further up, then the rest.  Reassembling that is the whole of the work here, and
it is why storing a relocated value has to be the target's business rather than
a shared one.
"""

from __future__ import annotations

from typing import Callable, Final

from ...mc.fixedwidth import insert_bits, read_word, write_word
from ...mc.fixup import (ABS32, ABS64, PCREL32, PCREL32_AT_FIELD, FixupBase,
                         FixupKind, FixupRangeError, MCFixup,
                         apply_little_endian, signed_fits)
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

#: The same twelve bits where the second instruction is a store, which keeps its
#: immediate in two pieces either side of the register it stores.
PCREL_LO12_S: Final[FixupKind] = FixupKind("riscv_pcrel_lo12_s", INSTRUCTION_SIZE,
                                           FixupBase.FIELD_START)

#: How far the second instruction of the pair sits after the first.
PCREL_PAIR_DISTANCE: Final[int] = INSTRUCTION_SIZE

#: A call too far for one `jal`, or to a function the C compiler would not
#: assume is near: an `auipc` and the `jalr` after it, the pair as one patch of
#: two words, both measured from the `auipc`.  What the C compiler writes for a
#: call it did not inline, so the runtime may hold one.
CALL: Final[FixupKind] = FixupKind("riscv_call", 2 * INSTRUCTION_SIZE,
                                   FixupBase.FIELD_START)


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


def _apply_call(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store a call's offset into the `auipc` and the `jalr` after it."""
    if not signed_fits(value, 32):
        raise FixupRangeError(fixup.kind, value)
    _apply_pcrel_hi20(data, offset, fixup, value)
    _apply_pcrel_lo12(data, offset + INSTRUCTION_SIZE, fixup, value)


def _apply_pcrel_lo12_s(data: bytearray, offset: int, fixup: MCFixup,
                        value: int) -> None:
    """Store the lower twelve bits of an address into a store: the top seven
    above the source register, the bottom five where a destination would be."""
    del fixup
    insert_bits(data, offset, INSTRUCTION_SIZE, (value >> 5) & 0x7F, 25, 7)
    insert_bits(data, offset, INSTRUCTION_SIZE, value & 0x1F, 7, 5)


#: How each of this target's relocations is stored.
APPLIERS: Final[dict[FixupKind, Callable[[bytearray, int, MCFixup, int], None]]] = {
    # The two that reach a whole address are stored as the bytes they are: they
    # name no instruction field, so what holds them is as wide as they are and
    # is overwritten outright.
    ABS32: apply_little_endian,
    ABS64: apply_little_endian,
    JAL: _apply_jal,
    BRANCH: _apply_branch,
    PCREL_HI20: _apply_pcrel_hi20,
    PCREL_LO12_I: _apply_pcrel_lo12,
    PCREL_LO12_S: _apply_pcrel_lo12_s,
    CALL: _apply_call,
}


def apply_fixup(data: bytearray, offset: int, fixup: MCFixup, value: int) -> None:
    """Store a computed fixup value into the instruction word that holds it."""
    applier = APPLIERS.get(fixup.kind)
    if applier is None:
        raise FixupRangeError(fixup.kind, value)
    applier(data, offset, fixup, value)


#: Which of these kinds the object format's relocation types come to.  A number
#: here is a promise about what `bin/pl4g-runtime` may find in the runtime's
#: object; one it finds and this does not have stops the extraction rather than
#: being filled in wrongly.  `-mno-relax` keeps the runtime to these: a call
#: the C compiler did not inline is an `auipc` and a `jalr`, one patch of two
#: words (`CALL`), and everything else reaches what it needs in one or a pair.
FROM_ELF: Final[dict[int, str]] = {
    1: ABS32.name,             # R_RISCV_32
    2: ABS64.name,             # R_RISCV_64
    16: BRANCH.name,           # R_RISCV_BRANCH
    17: JAL.name,              # R_RISCV_JAL
    19: CALL.name,             # R_RISCV_CALL_PLT
    23: PCREL_HI20.name,       # R_RISCV_PCREL_HI20
    24: PCREL_LO12_I.name,     # R_RISCV_PCREL_LO12_I
    25: PCREL_LO12_S.name,     # R_RISCV_PCREL_LO12_S
}

#: Every kind a packaged patch may name, by the name the patch carries.  The
#: generic ones are here too: a relocation reaching a whole address uses the
#: same field every target does.
BY_NAME: Final[dict[str, FixupKind]] = {
    one.name: one for one in
    (ABS32, ABS64, PCREL32, JAL, BRANCH, PCREL_HI20, PCREL_LO12_I, PCREL_LO12_S,
     CALL)}


#: Which relocation types name another relocation rather than what they reach.
#: This architecture computes an address in two instructions and the second is
#: written against the first: what the format puts in it is a label at the
#: `auipc`, and what is wanted is what *that* one reaches.  The value says which
#: types the first may be, so a pair that does not go together is caught.
PAIRED_WITH: Final[dict[int, tuple[int, ...]]] = {
    24: (23,),      # R_RISCV_PCREL_LO12_I names an R_RISCV_PCREL_HI20
    25: (23,),      # R_RISCV_PCREL_LO12_S, the same for a store
}
