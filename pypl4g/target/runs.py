"""Arithmetic over a whole run of elements, and asking whether any lane went past.

Written once for every target that has registers holding a run.  What differs
between them -- what the instructions are called, how the top bit of every lane
is gathered -- is behind ``Assembler.run_op``, ``run_ones`` and ``run_top_bits``;
what is here is the part that is the same everywhere, which is how the question
"did this go past the end of its type" is asked of every lane at once.

**There are no flags.**  A machine that adds sixteen bytes in one instruction
does not write sixteen carry flags, and no architecture has ever pretended it
could.  So the question is asked of the answer, the way the widest types have
always had it asked of them -- with the difference that it is asked of every
lane at the same time, by operations that are themselves one instruction over
the whole run.

Four questions, one per operation and signedness, and each is the same formula
written in `and`, `or` and `exclusive or`:

**A signed sum** has gone past when both operands had one sign and the answer
came out with the other, which is ``(a ^ sum) & (b ^ sum)`` with its lane's top
bit set.  **A signed difference** when the operands had different signs and the
answer differs from the left one: ``(a ^ b) & (a ^ diff)``.

**An unsigned sum** has gone past when the addition carried out of the top,
which is the carry-out of the top bit written down: ``(a & b) | ((a | b) & ~sum)``.
**An unsigned difference** when it borrowed into the top:
``(~a & b) | (~(a ^ b) & diff)``.

Every one of them leaves a value whose *top bit in each lane* says whether that
lane went past, and nothing else about it is looked at.  That is deliberate: the
top bit of each byte is the one thing every one of these machines can gather out
of such a register cheaply, so a question phrased that way needs no comparison
instruction, none of which exist at every lane width anyway.

**The lanes beyond the run are not asked about.**  A run shorter than a register
leaves the rest of it holding zero on one side and, where the other side is one
value in every lane, that value -- so those lanes may well "go past" while the
program's own elements do not.  The gathered bits are masked to the lanes the run
actually covers before the branch, which is what makes reading a short run into a
whole register safe rather than merely convenient.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from ..ir.inst import BinOp
from ..ir.layout import DataLayout, stride_of
from ..ir.types import IntType, VecType
from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCOperand, MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg
from ..source.location import Span
from .saturate import Fault, Unsupported


class Scratch(Protocol):
    """How a backend hands out registers for values with no name."""

    def scratch(self) -> Reg:
        """An ordinary register, for the gathered bits."""
        ...

    def run_scratch(self) -> Reg:
        """A register holding a whole run, for a value worked out on the way."""
        ...


#: Which ordinary operation each of these is built from.
_PLAIN = {BinOp.ADD: ops.PLUS, BinOp.SUB: ops.MINUS,
          BinOp.SAT_ADD: ops.PLUS, BinOp.SAT_SUB: ops.MINUS}


def lower_trapping(asm: Assembler, op: BinOp, ty: VecType, left: MCOperand,
                   right: MCOperand, destination: Reg, scratch: Scratch,
                   fault: Fault, layout: DataLayout, span: Span) -> None:
    """Do *op* to every lane at once and stop the program if any went past."""
    lane = ty.element
    if not isinstance(lane, IntType):
        raise Unsupported("arithmetic over a run of something that is not an integer")
    asm.run_op(_PLAIN[op], destination, left, right, lane.bits, span)
    answer = MCReg(destination, bits=asm.run_bits)
    bad = _went_past(asm, op, lane, left, right, answer, scratch, span)
    found = scratch.scratch()
    asm.run_top_bits(found, bad, span)
    # Only the lanes the run covers: what is beyond it is not the program's.
    asm.op(ops.AND, found, MCReg(found, bits=32),
           MCImm(_live(ty, layout, asm.run_bits), 32, signed=False), span=span)
    carry_on = asm.reserve_label("in.range")
    asm.branch(Condition.EQ, MCReg(found, bits=32), MCImm(0, 32, signed=False),
               carry_on, span)
    fault.out_of_range(asm, span)
    asm.block(carry_on)


def _went_past(asm: Assembler, op: BinOp, lane: IntType, left: MCOperand,
               right: MCOperand, answer: MCOperand, scratch: Scratch,
               span: Span) -> MCReg:
    """A run whose every lane's top bit says whether that lane went past."""
    if lane.signed:
        first, second = ((left, answer), (right, answer)) \
            if _PLAIN[op] is ops.PLUS else ((left, right), (left, answer))
        return _both(asm, ops.XOR, ops.AND, lane, first, second, scratch, span)
    ones = scratch.run_scratch()
    asm.run_ones(ones, span)
    whole = MCReg(ones, bits=asm.run_bits)
    if _PLAIN[op] is ops.PLUS:
        # The carry out of the top bit: both operands had it, or one of them had
        # it and the answer did not.
        not_answer = _op(asm, ops.XOR, lane, answer, whole, scratch, span)
        carried = _op(asm, ops.AND, lane, left, right, scratch, span)
        either = _op(asm, ops.OR, lane, left, right, scratch, span)
        lost = _op(asm, ops.AND, lane, either, not_answer, scratch, span)
        return _op(asm, ops.OR, lane, carried, lost, scratch, span)
    # The borrow into the top bit: the left did not have it and the right did,
    # or the two agreed about it and the answer has it.
    not_left = _op(asm, ops.XOR, lane, left, whole, scratch, span)
    taken = _op(asm, ops.AND, lane, not_left, right, scratch, span)
    differ = _op(asm, ops.XOR, lane, left, right, scratch, span)
    alike = _op(asm, ops.XOR, lane, differ, whole, scratch, span)
    borrowed = _op(asm, ops.AND, lane, alike, answer, scratch, span)
    return _op(asm, ops.OR, lane, taken, borrowed, scratch, span)


def _both(asm: Assembler, inner: ops.Op, outer: ops.Op, lane: IntType,
          first: tuple[MCOperand, MCOperand], second: tuple[MCOperand, MCOperand],
          scratch: Scratch, span: Span) -> MCReg:
    """*outer* of *inner* applied to each pair, which is both signed questions."""
    one = _op(asm, inner, lane, first[0], first[1], scratch, span)
    other = _op(asm, inner, lane, second[0], second[1], scratch, span)
    return _op(asm, outer, lane, one, other, scratch, span)


def _op(asm: Assembler, what: ops.Op, lane: IntType, left: MCOperand,
        right: MCOperand, scratch: Scratch, span: Span) -> MCReg:
    """One operation over a whole run, into a register of its own."""
    into = scratch.run_scratch()
    asm.run_op(what, into, left, right, lane.bits, span)
    return MCReg(into, bits=asm.run_bits)


def _live(ty: VecType, layout: DataLayout, bits: int) -> int:
    """Which of the gathered bits belong to lanes the run actually covers.

    One bit per byte of the register comes back, so a lane's own answer is the
    bit of its topmost byte -- the top bit of a lane is the top bit of the byte
    it ends in.  A run shorter than the register leaves the rest of the bits
    saying whatever the lanes beyond it came to, and none of that is the
    program's.
    """
    stride = stride_of(ty.element, layout)
    mask = 0
    for lane in range(min(ty.lanes, bits // 8 // stride)):
        mask |= 1 << ((lane + 1) * stride - 1)
    return mask


#: The saturating operations a run has an instruction for, by lane width.  The
#: wider lanes have none on any of these machines, so those are still done an
#: element at a time.
SATURATING: Sequence[BinOp] = (BinOp.SAT_ADD, BinOp.SAT_SUB)
