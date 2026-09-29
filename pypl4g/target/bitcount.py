"""Counting the bits of a value, with the instruction where there is one.

Two questions: how many bits are set, and how many zeroes stand above the
highest one that is.  Written once for all three targets, because what differs
between them is only whether the machine answers a question outright -- and
where it does not, what is emitted instead is the same everywhere.

**Where there is an instruction it is used**, and on x86-64 which instructions
exist is what the microarchitecture level says: `popcnt` is in the second level
and `lzcnt` in the third, and a program built for the first gets neither.  That
is the level being a description of what is emitted rather than only a promise
about what may be.  AArch64 has both at every level -- one of them in the vector
unit, which is where that architecture keeps its bit counting -- and RISC-V has
neither outside an extension this compiler does not require.

**Where there is none, the bits are counted without a branch.**  The sequence is
the one every compiler uses: pairs of bits added to pairs, then nibbles to
nibbles, then bytes to bytes, and a multiplication to sum the bytes into the
top one.  Twelve instructions and no branch, against a loop of up to sixty-four
turns -- and a loop would answer in a time that depends on the value, which is
the wrong shape for something a program may do in a tight place.

**Leading zeroes without an instruction** are the bits smeared downwards and
then counted: once every bit below the highest set one is set, the count of
zeroes above it is the width less the count of ones.  That also answers for
nought, which is the width -- the one case the instructions themselves leave
undefined and the language does not.

**Both count over the value's own type.**  A value narrower than the register it
is held in is made to fill one first, without a sign, so that what is counted is
the type's bits and nothing above them; and a count of leading zeroes then has
the register's extra width taken off it.
"""

from __future__ import annotations

from typing import Final

from ..ir.inst import UnOp
from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCOperand, MCReg
from ..mc.reg import Reg
from ..source.location import Span

#: The masks the counting sequence folds with, each of them the bits of one
#: half of every field of the width it is folding.
_PAIRS: Final[int] = 0x5555555555555555
_NIBBLES: Final[int] = 0x3333333333333333
_BYTES: Final[int] = 0x0F0F0F0F0F0F0F0F
#: And what sums the bytes into the top one, every byte of it being one.
_SUM: Final[int] = 0x0101010101010101

#: How wide the register the counting is done in is.
WIDTH: Final[int] = 64

#: The two operations this answers for, which is what a backend asks to know
#: whether an instruction of the representation belongs here.
COUNTING: Final[frozenset[UnOp]] = frozenset((UnOp.COUNT_ONES,
                                              UnOp.COUNT_LEADING))


class Scratch:
    """How a backend hands out a register for a value with no name."""

    def scratch(self) -> Reg:
        """A register of the widest kind, for something computed on the way."""
        raise NotImplementedError


def lower_count(asm: Assembler, op: UnOp, value: MCOperand, bits: int,
                signed: bool, destination: Reg, scratch: Scratch,
                span: Span) -> None:
    """Emit the count *op* asks for over a value of *bits* bits into *destination*.

    The counting is done in a whole register and moved into the answer's at the
    end, named at the width the answer's register *is*: a count is a `u8`, and
    how wide a register a `u8` lives in is a thing each architecture decides for
    itself.
    """
    held = scratch.scratch()
    # The type's own bits and nothing above them, which for a value narrower
    # than its register is what filling one without a sign gives.
    if bits < WIDTH:
        asm.widen(held, value, bits, False, span)
        if signed:
            # A signed value fills its register with copies of its sign, so the
            # widening above read bits that are the sign and not the value.
            _mask_to(asm, held, bits, scratch, span)
    else:
        asm.loadreg(held, value, span)
    # Counted in a whole register and moved into the answer's at the end.  A
    # count is a `u8`, which lives in the narrowest register a value lives in,
    # and everything here is sixty-four bits wide; naming the two in one
    # instruction is naming two widths in one instruction.
    answer = scratch.scratch()
    if op is UnOp.COUNT_ONES:
        _ones(asm, held, answer, scratch, span)
    else:
        _leading(asm, held, bits, answer, scratch, span)
    asm.loadreg(destination, MCReg(answer, bits=getattr(destination, "bits", WIDTH)),
                span)


def _mask_to(asm: Assembler, held: Reg, bits: int, scratch: Scratch,
             span: Span) -> None:
    """Clear everything above *bits* of *held*."""
    mask = scratch.scratch()
    asm.loadreg(mask, MCImm((1 << bits) - 1, WIDTH, signed=False), span)
    asm.op(ops.AND, held, MCReg(held), MCReg(mask), span=span)


def _ones(asm: Assembler, held: Reg, destination: Reg, scratch: Scratch,
          span: Span) -> None:
    """How many bits of *held* are set."""
    if asm.counts_ones:
        asm.count_ones(destination, MCReg(held), span)
        return
    mask, other = scratch.scratch(), scratch.scratch()
    # Pairs: each pair of bits replaced by how many of the two are set, which
    # is the pair less the bit above it.
    asm.loadreg(mask, MCImm(_PAIRS, WIDTH, signed=False), span)
    asm.shift(ops.SHIFT_RIGHT, other, MCReg(held), MCImm(1, 32, signed=False),
              WIDTH, span)
    asm.op(ops.AND, other, MCReg(other), MCReg(mask), span=span)
    asm.op(ops.MINUS, held, MCReg(held), MCReg(other), span=span)
    # Nibbles: the two pairs of each added together.
    asm.loadreg(mask, MCImm(_NIBBLES, WIDTH, signed=False), span)
    asm.shift(ops.SHIFT_RIGHT, other, MCReg(held), MCImm(2, 32, signed=False),
              WIDTH, span)
    asm.op(ops.AND, other, MCReg(other), MCReg(mask), span=span)
    asm.op(ops.AND, held, MCReg(held), MCReg(mask), span=span)
    asm.op(ops.PLUS, held, MCReg(held), MCReg(other), span=span)
    # Bytes: the two nibbles of each added together.  The mask goes on after
    # the addition rather than before it twice, no byte's count reaching past
    # its own nibble pair.
    asm.shift(ops.SHIFT_RIGHT, other, MCReg(held), MCImm(4, 32, signed=False),
              WIDTH, span)
    asm.op(ops.PLUS, held, MCReg(held), MCReg(other), span=span)
    asm.loadreg(mask, MCImm(_BYTES, WIDTH, signed=False), span)
    asm.op(ops.AND, held, MCReg(held), MCReg(mask), span=span)
    # And the bytes summed into the top one, which a multiplication does: every
    # byte of the multiplier being one makes the top byte of the product the
    # sum of all eight.
    asm.loadreg(mask, MCImm(_SUM, WIDTH, signed=False), span)
    asm.op(ops.TIMES, held, MCReg(held), MCReg(mask), span=span)
    asm.shift(ops.SHIFT_RIGHT, destination, MCReg(held),
              MCImm(WIDTH - 8, 32, signed=False), WIDTH, span)


def _leading(asm: Assembler, held: Reg, bits: int, destination: Reg,
             scratch: Scratch, span: Span) -> None:
    """How many zeroes stand above the highest set bit of *held*.

    Counted over *bits* and not over the register, so what the register has
    above the value is taken off the answer.
    """
    if asm.counts_leading:
        asm.count_leading(destination, MCReg(held), span)
        if bits < WIDTH:
            asm.op(ops.MINUS, destination, MCReg(destination),
                   MCImm(WIDTH - bits, 32, signed=False), span=span)
        return
    # Every bit below the highest set one set as well, after which the zeroes
    # above it are the register's width less the number of bits set.
    other = scratch.scratch()
    for distance in (1, 2, 4, 8, 16, 32):
        asm.shift(ops.SHIFT_RIGHT, other, MCReg(held),
                  MCImm(distance, 32, signed=False), WIDTH, span)
        asm.op(ops.OR, held, MCReg(held), MCReg(other), span=span)
    _ones(asm, held, destination, scratch, span)
    # The width less what was counted, worked out in a register of its own: the
    # subtraction is written with the destination on the left on the
    # architecture whose arithmetic reads and writes one operand, and the
    # destination here is what is being subtracted.
    width = scratch.scratch()
    asm.loadreg(width, MCImm(bits, 32, signed=False), span)
    asm.op(ops.MINUS, width, MCReg(width), MCReg(destination), span=span)
    asm.loadreg(destination, MCReg(width), span)
