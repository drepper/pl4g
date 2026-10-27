"""The product of two words without carries, with the instruction where there
is one.

Written once for all three targets, as the counting of bits is, because what
differs between them is only whether the machine has the instruction -- and
where it has not, what is emitted instead is the same everywhere.

**Where there is an instruction it is used**, and whether there is one is what
the program was built for: x86-64's `pclmulqdq` at the third level and above,
RISC-V's `clmul` and `clmulh` with Zbc or Zbkc.  AArch64's `pmull` is in the
cryptographic extension, which no level of that architecture promises and which
this compiler has no way yet to be told of, so it is never assumed there.

**Where there is none, the product is made of ordinary multiplications**, which
every machine has and does quickly.  An ordinary product is the carry-less one
with the carries added in, so the trick is to keep the carries out of the bits
that are read: each factor is cut into four, every fourth bit in each, so that
a product of two of the pieces has its own bits four places apart, and the
carries out of each -- at most three bits' worth, because no place gathers more
than eight products of bits from two 32-bit pieces -- land in the three places
between, which are thrown away.  Sixteen such products make a 64-bit carry-less
product of two 32-bit words; three of those make the whole, the way Karatsuba
makes a product of two halves out of three.  About a hundred and fifty
instructions and no branch, against a loop of sixty-four turns that would take
three times as long and answer in a time that depends on the factors -- which
for the arithmetic of a cipher is the one thing it must not do.  This is the
method BearSSL multiplies with where it has no instruction to.

**A factor of all ones is a prefix exclusive or**, and that is a ladder of six
shifts and six exclusive ors for each half rather than any multiplication at
all: the lower half's bit *k* is the exclusive or of the other factor's bits up
to *k*, and the upper half's the exclusive or of those above it.  It is what a
parser finding which bytes are inside a string asks, so it is worth knowing
without the instruction too.
"""

from __future__ import annotations

from typing import Final

from ..ir.inst import BinOp
from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCOperand, MCReg
from ..mc.reg import Reg
from ..source.location import Span
from .bitcount import Scratch

#: The operation this answers for.
CARRYLESS: Final[BinOp] = BinOp.CLMUL

#: How wide a word is.
WIDTH: Final[int] = 64

#: Every bit of a word.
_ALL: Final[int] = (1 << WIDTH) - 1

#: Every fourth bit, from each of the four places a piece of a factor starts.
_HOLES: Final[tuple[int, ...]] = tuple(
    (0x1111111111111111 << at) & _ALL for at in range(4))

#: The distances of the ladder that makes an exclusive or of every bit below.
_LADDER: Final[tuple[int, ...]] = (1, 2, 4, 8, 16, 32)


def lower_carryless(asm: Assembler, low: Reg, high: Reg, left: MCOperand,
                    right: MCOperand, scratch: Scratch, span: Span) -> None:
    """Emit the product of *left* and *right* without carries, its lower half
    into *low* and its upper half into *high*."""
    if asm.multiplies_carryless:
        asm.carryless_product(low, high, left, right, span)
        return
    if _all_ones(right) and not isinstance(left, MCImm):
        _prefix(asm, low, high, left, scratch, span)
        return
    if _all_ones(left) and not isinstance(right, MCImm):
        _prefix(asm, low, high, right, scratch, span)
        return
    _karatsuba(asm, low, high, _held(asm, left, scratch, span),
               _held(asm, right, scratch, span), scratch, span)


def _all_ones(operand: MCOperand) -> bool:
    """Whether *operand* is a number written down with every bit set."""
    return isinstance(operand, MCImm) and operand.value & _ALL == _ALL


def _held(asm: Assembler, operand: MCOperand, scratch: Scratch,
          span: Span) -> Reg:
    """*operand* in a register of its own, where it is not in one already."""
    if isinstance(operand, MCReg):
        return operand.reg
    into = scratch.scratch()
    asm.loadreg(into, operand, span)
    return into


def _shifted(asm: Assembler, op: ops.Op, value: Reg, distance: int,
             scratch: Scratch, span: Span) -> Reg:
    """*value* moved *distance* places, in a register of its own."""
    into = scratch.scratch()
    asm.shift(op, into, MCReg(value), MCImm(distance, 32, signed=False), WIDTH,
              span)
    return into


def _prefix(asm: Assembler, low: Reg, high: Reg, factor: MCOperand,
            scratch: Scratch, span: Span) -> None:
    """The product of *factor* and a word of all ones: an exclusive or of every
    bit at and below each place, and above it."""
    up, down = scratch.scratch(), scratch.scratch()
    asm.loadreg(up, factor, span)
    asm.loadreg(down, factor, span)
    for distance in _LADDER:
        moved = _shifted(asm, ops.SHIFT_LEFT, up, distance, scratch, span)
        asm.op(ops.XOR, up, MCReg(up), MCReg(moved), span=span)
        moved = _shifted(asm, ops.SHIFT_RIGHT, down, distance, scratch, span)
        asm.op(ops.XOR, down, MCReg(down), MCReg(moved), span=span)
    asm.loadreg(low, MCReg(up), span)
    # What *down* holds at each place includes that place's own bit, which the
    # upper half's does not: the upper half's bit *k* is the exclusive or of
    # the bits above *k*, one place further along.
    asm.shift(ops.SHIFT_RIGHT, high, MCReg(down), MCImm(1, 32, signed=False),
              WIDTH, span)


def _karatsuba(asm: Assembler, low: Reg, high: Reg, left: Reg, right: Reg,
               scratch: Scratch, span: Span) -> None:
    """The whole product of two words as three of two half-words: the lower
    halves', the upper halves', and that of the two halves' exclusive ors, which
    with the other two taken out is what stands in the middle."""
    def halves(word: Reg) -> tuple[Reg, Reg]:
        """The lower thirty-two bits of *word* and the upper, each in a word."""
        lifted = _shifted(asm, ops.SHIFT_LEFT, word, 32, scratch, span)
        return (_shifted(asm, ops.SHIFT_RIGHT, lifted, 32, scratch, span),
                _shifted(asm, ops.SHIFT_RIGHT, word, 32, scratch, span))

    left_low, left_high = halves(left)
    right_low, right_high = halves(right)
    lower = _product(asm, left_low, right_low, scratch, span)
    upper = _product(asm, left_high, right_high, scratch, span)
    left_both, right_both = scratch.scratch(), scratch.scratch()
    asm.op(ops.XOR, left_both, MCReg(left_low), MCReg(left_high), span=span)
    asm.op(ops.XOR, right_both, MCReg(right_low), MCReg(right_high), span=span)
    middle = _product(asm, left_both, right_both, scratch, span)
    asm.op(ops.XOR, middle, MCReg(middle), MCReg(lower), span=span)
    asm.op(ops.XOR, middle, MCReg(middle), MCReg(upper), span=span)
    # The middle stands thirty-two places up, across the two halves.
    into_low = _shifted(asm, ops.SHIFT_LEFT, middle, 32, scratch, span)
    into_high = _shifted(asm, ops.SHIFT_RIGHT, middle, 32, scratch, span)
    asm.op(ops.XOR, low, MCReg(lower), MCReg(into_low), span=span)
    asm.op(ops.XOR, high, MCReg(upper), MCReg(into_high), span=span)


def _product(asm: Assembler, left: Reg, right: Reg, scratch: Scratch,
             span: Span) -> Reg:
    """The 64-bit product without carries of two words of thirty-two bits.

    Each factor cut into four pieces of every fourth bit; the product of the
    piece starting at *i* and the one starting at *j* has its bits at the places
    *i* + *j* comes to, four apart, and the sixteen products are gathered into
    the four answers by where their bits are and masked down to those bits.
    """
    lefts: list[Reg] = []
    rights: list[Reg] = []
    for holes in _HOLES:
        # One mask for the piece of each factor: a number this wide is a load
        # of its own on two of the three machines.
        mask = scratch.scratch()
        asm.loadreg(mask, MCImm(holes, WIDTH, signed=False), span)
        for word, found in ((left, lefts), (right, rights)):
            piece = scratch.scratch()
            asm.op(ops.AND, piece, MCReg(word), MCReg(mask), span=span)
            found.append(piece)
    answer: Reg | None = None
    for at, holes in enumerate(_HOLES):
        gathered: Reg | None = None
        for one in range(4):
            made = scratch.scratch()
            asm.op(ops.TIMES, made, MCReg(lefts[one]),
                   MCReg(rights[(at - one) % 4]), span=span)
            if gathered is None:
                gathered = made
                continue
            asm.op(ops.XOR, gathered, MCReg(gathered), MCReg(made), span=span)
        assert gathered is not None
        mask = scratch.scratch()
        asm.loadreg(mask, MCImm(holes, WIDTH, signed=False), span)
        asm.op(ops.AND, gathered, MCReg(gathered), MCReg(mask), span=span)
        if answer is None:
            answer = gathered
            continue
        asm.op(ops.OR, answer, MCReg(answer), MCReg(gathered), span=span)
    assert answer is not None
    return answer
