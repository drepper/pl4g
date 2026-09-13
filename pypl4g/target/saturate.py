"""The saturating operations, which answer with the nearest value their type
can hold rather than going past it.

Written once for all three targets.  What differs between them -- how a bound
replaces a value that went past, how a narrow value is made to fill a whole
register, and what the two multiply instructions are called -- is behind
``Assembler.clamp``, ``Assembler.widen`` and the tables, which each backend
answers for itself.

Three cases, decided by how the type compares with the register it is held in.

**Narrower than its register.**  The operation is computed as it stands and the
answer is exact: two values under 2^31 cannot make a sum, a difference or a
product that a register cannot hold.  So saturating it is computing it and
comparing the answer with the two ends of the type.

**As wide as its register but narrower than the widest.**  This is a thirty-two
bit type on the two architectures that have thirty-two bit registers.  Both
operands are widened into whole registers first -- which costs one instruction
each and nothing at all on the architecture that has only one width -- and then
it is the case above.

**As wide as the widest register.**  There is nowhere wider to compute in, so it
is done the other way round: the operation is allowed to wrap, and what says it
wrapped is a property of the wrapped answer.  A sum that came out below what it
was given has carried; a difference asked of too small a number is one whose
left operand was the smaller; and a sum of two numbers of one sign that comes out
with the other sign has gone past the end on that side.  A product is the one
this cannot answer: seeing that it went past needs the upper half of it, which
two of these architectures have as an instruction and the third has only in a
form with a fixed pair of registers.
"""

from typing import Protocol, Sequence

from ..ir.inst import BinOp
from ..ir.types import IntType, Type
from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCOperand, MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg
from ..source.location import Span


class Unsupported(Exception):
    """A saturating operation no backend can yet emit."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


class Scratch(Protocol):
    """How a backend hands out a register for a value with no name."""

    def scratch(self) -> Reg:
        """A register of the widest kind, for something computed on the way."""
        ...


class Fault(Protocol):
    """What a backend does where an answer went past the end of its type.

    Only the trapping operations have one.  It emits code that does not return,
    so nothing after it in the block can be reached.
    """

    def out_of_range(self, asm: Assembler, span: Span) -> None:
        """Emit the report and the stop."""
        ...


#: Which ordinary operation each of these is built from.
_PLAIN = {
    BinOp.SAT_ADD: ops.PLUS, BinOp.SAT_SUB: ops.MINUS, BinOp.SAT_MUL: ops.TIMES,
    BinOp.ADD: ops.PLUS, BinOp.SUB: ops.MINUS, BinOp.MUL: ops.TIMES,
}

#: The operations that clamp.
SATURATING = frozenset((BinOp.SAT_ADD, BinOp.SAT_SUB, BinOp.SAT_MUL))

#: The operations that stop the program instead.  They are the same three
#: questions asked of the same three answers; what differs is the reply.
TRAPPING = frozenset((BinOp.ADD, BinOp.SUB, BinOp.MUL))

#: What each of them is called where a message has to say which went wrong.
NAMES = {
    BinOp.ADD: "addition", BinOp.SUB: "subtraction", BinOp.MUL: "multiplication",
}

#: Division is not here.  On two of the three architectures it is one
#: instruction; on x86-64 it writes its quotient and its remainder to a fixed
#: pair of registers, which the allocator cannot yet be told about.  Naming it
#: here lets the refusal say what it waits on rather than that the operation is
#: unknown.
DIVISION = frozenset((BinOp.SDIV, BinOp.UDIV))

#: The unsaturating operation each saturating one corresponds to, so that one
#: table of shapes serves both.
_ORDINARY = {BinOp.SAT_ADD: BinOp.ADD, BinOp.SAT_SUB: BinOp.SUB,
             BinOp.SAT_MUL: BinOp.MUL,
             BinOp.ADD: BinOp.ADD, BinOp.SUB: BinOp.SUB, BinOp.MUL: BinOp.MUL}


def lower_saturating(asm: Assembler, op: BinOp, ty: Type, left: MCOperand,
                     right: MCOperand, destination: Reg, scratch: Scratch,
                     register_bits: int, span: Span) -> None:
    """Emit the saturating operation *op* on *ty* into *destination*.

    *register_bits* is how wide the register holding a value of this type is,
    which is what decides which of the three cases applies.
    """
    _lower(asm, op, ty, left, right, destination, scratch, register_bits, None, span)


def lower_trapping(asm: Assembler, op: BinOp, ty: Type, left: MCOperand,
                   right: MCOperand, destination: Reg, scratch: Scratch,
                   register_bits: int, fault: Fault, span: Span) -> None:
    """Emit the ordinary operation *op* on *ty*, which stops the program where
    the answer will not fit.

    The same three shapes and the same questions as the saturating form: an
    answer that went past the end of its type is found the same way whether the
    end is then used in its place or the program is stopped.
    """
    _lower(asm, op, ty, left, right, destination, scratch, register_bits, fault, span)


def _lower(asm: Assembler, op: BinOp, ty: Type, left: MCOperand,
           right: MCOperand, destination: Reg, scratch: Scratch,
           register_bits: int, fault: "Fault | None", span: Span) -> None:
    """The body of both of the above."""
    if not isinstance(ty, IntType):
        raise Unsupported("arithmetic on something that is not an integer")
    if ty.bits < register_bits:
        _exact(asm, op, ty, left, right, destination, register_bits, fault, span)
        return
    if ty.bits < 64:
        _widened(asm, op, ty, left, right, destination, scratch, fault, span)
        return
    _wrapping(asm, op, ty, left, right, destination, scratch, fault, span)


def _answer(asm: Assembler, cond: Condition, destination: Reg, lhs: MCOperand,
            rhs: MCOperand, bound: MCOperand, fault: "Fault | None",
            span: Span) -> None:
    """What is done where *lhs* and *rhs* stand in *cond*, which is what says
    the answer went past the end of its type.

    Either the end is put in its place, or the program stops.  The branch is
    written so that going past is the case that jumps, since it is the case that
    does not come back.
    """
    if fault is None:
        asm.clamp(cond, destination, lhs, rhs, bound, span)
        return
    carry_on = asm.reserve_label("in.range")
    asm.branch(cond.inverted(), lhs, rhs, carry_on, span)
    fault.out_of_range(asm, span)
    asm.block(carry_on)


def _exact(asm: Assembler, op: BinOp, ty: IntType, left: MCOperand,
           right: MCOperand, destination: Reg, bits: int, fault: "Fault | None",
           span: Span) -> None:
    """Compute the operation as it stands, where the answer cannot overflow the
    register, and bring it back to the ends of the type."""
    asm.op(_PLAIN[op], destination, left, right, span=span)
    value = MCReg(destination)
    high = MCImm(ty.high, bits, signed=ty.signed)
    if ty.signed:
        _answer(asm, Condition.SGT, destination, value, high, high, fault, span)
        low = MCImm(ty.low, bits, signed=True)
        _answer(asm, Condition.SLT, destination, value, low, low, fault, span)
        return
    if op is BinOp.SAT_SUB:
        # A difference is the one unsigned answer that can come out below zero,
        # and it can never come out too large: the left operand was in the type
        # to begin with and nothing was added to it.
        zero = MCImm(0, 32, signed=False)
        _answer(asm, Condition.SLT, destination, value, zero, zero, fault, span)
        return
    # A sum or a product of two unsigned values is never negative, so the only
    # end it can reach is the top -- and it may be past what a signed comparison
    # could tell apart, which is why this one is unsigned where the subtraction
    # above is signed.
    _answer(asm, Condition.UGT, destination, value, high, high, fault, span)


def _widened(asm: Assembler, op: BinOp, ty: IntType, left: MCOperand,
             right: MCOperand, destination: Reg, scratch: Scratch,
             fault: "Fault | None", span: Span) -> None:
    """Fill whole registers with the two operands and then compute exactly.

    The answer is inside the type by the time it is written back, so writing it
    at the narrower width loses nothing.
    """
    first, second = scratch.scratch(), scratch.scratch()
    asm.widen(first, left, ty.bits, ty.signed, span)
    asm.widen(second, right, ty.bits, ty.signed, span)
    wide = scratch.scratch()
    _exact(asm, op, ty, MCReg(first), MCReg(second), wide, 64, fault, span)
    asm.loadreg(destination, MCReg(wide, bits=ty.bits if ty.bits >= 32 else 32), span)


def _wrapping(asm: Assembler, op: BinOp, ty: IntType, left: MCOperand,
              right: MCOperand, destination: Reg, scratch: Scratch,
              fault: "Fault | None", span: Span) -> None:
    """Let the operation wrap and ask the wrapped answer what happened."""
    if _ORDINARY[op] is BinOp.MUL:
        raise Unsupported(
            "a saturating multiplication of the widest type, which needs the "
            "upper half of the product")
    # The left operand is read again after the operation, so it has to be
    # somewhere reading it is possible; a constant is put in a register first.
    if isinstance(left, MCReg):
        held = left
    else:
        held = MCReg(scratch.scratch())
        asm.loadreg(held.reg, left, span)
    asm.op(_PLAIN[op], destination, held, right, span=span)
    answer = MCReg(destination)
    if not ty.signed:
        if op is BinOp.SAT_ADD:
            high = MCImm(ty.high, 64, signed=False)
            _answer(asm, Condition.ULT, destination, answer, held, high, fault, span)
        else:
            zero = MCImm(0, 32, signed=False)
            _answer(asm, Condition.ULT, destination, held, right, zero, fault, span)
        return
    _signed_wrapping(asm, op, ty, held, right, destination, scratch, fault, span)


def _signed_wrapping(asm: Assembler, op: BinOp, ty: IntType, left: MCReg,
                     right: MCOperand, destination: Reg, scratch: Scratch,
                     fault: "Fault | None", span: Span) -> None:
    """The signed half of the above.

    An addition has gone past an end when both operands had one sign and the
    answer came out with the other; a subtraction when the operands had
    different signs and the answer differs from the left one.  Both are that one
    question asked of two exclusive-ors, and which end was passed is decided by
    the sign of the left operand: two negatives can only have gone below.
    """
    answer = MCReg(destination)
    first = MCReg(scratch.scratch())
    second = MCReg(scratch.scratch())
    asm.op(ops.XOR, first.reg, left, answer, span=span)
    if op is BinOp.SAT_ADD:
        asm.op(ops.XOR, second.reg, right, answer, span=span)
    else:
        asm.op(ops.XOR, second.reg, left, right, span=span)
    asm.op(ops.AND, first.reg, first, second, span=span)

    zero = MCImm(0, 32, signed=False)
    bound = scratch.scratch()
    asm.loadreg(bound, MCImm(ty.high, 64, signed=True), span)
    if fault is None:
        asm.clamp(Condition.SLT, bound, left, zero,
                  MCImm(ty.low, 64, signed=True), span)
    _answer(asm, Condition.SLT, destination, first, zero, MCReg(bound), fault, span)
