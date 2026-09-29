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
with the other sign has gone past the end on that side.

**A product is the one the wrapped answer cannot answer for**, there being no
property of the low half that says the high half was not nought.  So it is not
asked: the product is computed whole, both halves of it, and the upper half is
what says whether the lower one is the answer.  Two of these architectures
answer the upper half with an instruction of its own; the third has it only in
the one-operand multiply, which reads a factor in a fixed register and writes
both halves to a fixed pair -- which costs a move in and two out and nothing
else, the instruction saying what it writes being enough to keep the other
factor clear of it.
"""

from __future__ import annotations

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
    BinOp.WRAP_ADD: ops.PLUS, BinOp.WRAP_SUB: ops.MINUS,
    BinOp.WRAP_MUL: ops.TIMES,
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

#: The operations that answer with the low bits of what they came to, whatever
#: they came to.  A program writes one by putting the operator inside `⎕wrap`.
WRAPPING = frozenset((BinOp.WRAP_ADD, BinOp.WRAP_SUB, BinOp.WRAP_MUL))

#: The larger and the smaller of two.  Which comparison each is asked with is
#: the whole of the difference between them, so one lowering serves all four.
EXTREMA: dict[BinOp, Condition] = {
    BinOp.SMAX: Condition.SLT, BinOp.UMAX: Condition.ULT,
    BinOp.SMIN: Condition.SGT, BinOp.UMIN: Condition.UGT,
}

#: Moving bits sideways.  A rotation is built from two shifts rather than from
#: a rotate instruction, so that it means the same thing for a type narrower
#: than the register it is held in -- which is most of them.
#:
#: The wrapping five are here too: they are the same five with the distance
#: taken modulo the width of the type instead of being a question the program
#: can get wrong, so it is one lowering with one thing decided differently.
SHIFTS = frozenset((BinOp.SHL, BinOp.ASHR, BinOp.LSHR, BinOp.ROTL, BinOp.ROTR,
                    BinOp.WRAP_SHL, BinOp.WRAP_ASHR, BinOp.WRAP_LSHR,
                    BinOp.WRAP_ROTL, BinOp.WRAP_ROTR))

NAMES.update({BinOp.SHL: "shift", BinOp.ASHR: "shift", BinOp.LSHR: "shift",
              BinOp.ROTL: "rotation", BinOp.ROTR: "rotation",
              BinOp.WRAP_SHL: "shift", BinOp.WRAP_ASHR: "shift",
              BinOp.WRAP_LSHR: "shift", BinOp.WRAP_ROTL: "rotation",
              BinOp.WRAP_ROTR: "rotation"})

#: Dividing, and asking what is left over.  They are one operation as far as the
#: instructions go -- two of the three architectures answer both questions with
#: one instruction, and on the third the same instruction writes both answers.
DIVISION = frozenset((BinOp.SDIV, BinOp.UDIV, BinOp.SREM, BinOp.UREM))

#: What each of them is called where a message has to say which went wrong.
NAMES.update({BinOp.SDIV: "division", BinOp.UDIV: "division",
              BinOp.SREM: "remainder", BinOp.UREM: "remainder",
              BinOp.FDIV: "division"})

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
           register_bits: int, fault: Fault | None, span: Span) -> None:
    """The body of both of the above."""
    if not isinstance(ty, IntType):
        raise Unsupported("arithmetic on something that is not an integer")
    if fault is not None and op in _FLAGGED and ty.bits in asm.flagged_widths:
        _by_flags(asm, op, ty, left, right, destination, fault, span)
        return
    if ty.bits < register_bits:
        _exact(asm, op, ty, left, right, destination, register_bits, fault, span)
        return
    if ty.bits < 64:
        _widened(asm, op, ty, left, right, destination, scratch, fault, span)
        return
    _wrapping(asm, op, ty, left, right, destination, scratch, fault, span)


#: The two whose overflow a flag answers for at every width.  A product is not
#: among them: seeing that one went past wants the upper half of it, which is a
#: second instruction and on some widths a pair of fixed registers -- where
#: widening it and comparing is one instruction and no constraint at all.
_FLAGGED: frozenset[BinOp] = frozenset((BinOp.ADD, BinOp.SUB))


def _by_flags(asm: Assembler, op: BinOp, ty: IntType, left: MCOperand,
              right: MCOperand, destination: Reg, fault: Fault,
              span: Span) -> None:
    """Compute the operation at the type's own width and ask the flags.

    This is what an architecture with flags is for.  The operation is emitted at
    the width the type actually is -- an eight-bit addition is an eight-bit
    addition -- so what the flags then say is whether *that* answer went past,
    which is the question, and not whether some wider answer did, which would
    then have to be asked all over again by comparing.

    One instruction and one branch, against an instruction, one or two
    comparisons and one or two branches.  And the answer needs no bringing back
    into its type afterwards, because it never left it.
    """
    asm.op_at(_PLAIN[op], destination, left, right, ty.bits, span)
    carry_on = asm.reserve_label("in.range")
    asm.branch_if_in_range(_PLAIN[op], ty.signed, carry_on, span)
    fault.out_of_range(asm, span)
    asm.block(carry_on)


def _answer(asm: Assembler, cond: Condition, destination: Reg, lhs: MCOperand,
            rhs: MCOperand, bound: MCOperand, fault: Fault | None,
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
           right: MCOperand, destination: Reg, bits: int, fault: Fault | None,
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
    if _ORDINARY[op] is BinOp.SUB:
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
             fault: Fault | None, span: Span) -> None:
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
              fault: Fault | None, span: Span) -> None:
    """Let the operation wrap and ask the wrapped answer what happened."""
    if _ORDINARY[op] is BinOp.MUL:
        _wide_product(asm, ty, left, right, destination, scratch, fault, span)
        return
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
        if _ORDINARY[op] is BinOp.ADD:
            high = MCImm(ty.high, 64, signed=False)
            _answer(asm, Condition.ULT, destination, answer, held, high, fault, span)
        else:
            zero = MCImm(0, 32, signed=False)
            _answer(asm, Condition.ULT, destination, held, right, zero, fault, span)
        return
    _signed_wrapping(asm, op, ty, held, right, destination, scratch, fault, span)


def _wide_product(asm: Assembler, ty: IntType, left: MCOperand,
                  right: MCOperand, destination: Reg, scratch: Scratch,
                  fault: Fault | None, span: Span) -> None:
    """A product of the widest type, seen through the upper half of itself.

    There is nowhere wider to compute in, so the product is computed whole --
    both halves of it -- and the upper half is what says whether the lower one
    is the answer.

    **Unsigned**, the upper half is nought exactly when the product fits, and
    the only end such a product can reach is the top.

    **Signed**, the product fits exactly when the upper half is what the lower
    one's sign says it should be: all ones under a negative answer and nought
    under a positive one, which is the lower half shifted right by its whole
    width less one.  Which end was passed is then the sign of the upper half,
    that being the sign of the product itself.
    """
    high = scratch.scratch()
    asm.wide_product(destination, high, left, right, ty.signed, span)
    zero = MCImm(0, 32, signed=False)
    if not ty.signed:
        bound = scratch.scratch()
        asm.loadreg(bound, MCImm(ty.high, 64, signed=False), span)
        _answer(asm, Condition.NE, destination, MCReg(high), zero,
                MCReg(bound), fault, span)
        return
    expected = scratch.scratch()
    asm.shift(ops.SHIFT_RIGHT_SIGNED, expected, MCReg(destination),
              MCImm(63, 32, signed=False), 64, span)
    bound = scratch.scratch()
    asm.loadreg(bound, MCImm(ty.high, 64, signed=True), span)
    if fault is None:
        # A product that went past and whose upper half is negative went past
        # the bottom; the bound is the other end for it.
        asm.clamp(Condition.SLT, bound, MCReg(high), zero,
                  MCImm(ty.low, 64, signed=True), span)
    _answer(asm, Condition.NE, destination, MCReg(high), MCReg(expected),
            MCReg(bound), fault, span)


def _signed_wrapping(asm: Assembler, op: BinOp, ty: IntType, left: MCReg,
                     right: MCOperand, destination: Reg, scratch: Scratch,
                     fault: Fault | None, span: Span) -> None:
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
    if _ORDINARY[op] is BinOp.ADD:
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


def lower_division(asm: Assembler, op: BinOp, ty: Type, left: MCOperand,
                   right: MCOperand, destination: Reg, scratch: Scratch,
                   register_bits: int, fault: Fault, divide_by_zero: Fault,
                   span: Span) -> None:
    """Emit a division or a remainder, with the two questions asked first.

    **Dividing by zero has no answer**, and what the three architectures do
    about it is three different things: one raises a fault of the processor's
    own, one answers with all ones, and one answers with zero.  So the divisor
    is compared with zero and the program is stopped where it is, which is the
    same thing happening on every target and the same message as any other
    fault.

    **The most negative number divided by minus one** has no answer either: the
    quotient is one past the largest the type can hold.  Here too the three
    disagree -- one raises a fault, two answer with the most negative number
    again -- so it is asked about rather than left to them.  Nothing like it
    arises for an unsigned type, where the only unanswerable division is by
    zero.
    """
    if not isinstance(ty, IntType):
        raise Unsupported("a division of something that is not an integer")
    bits = max(32, ty.bits) if register_bits < 64 else 64
    # Both are read again after the checks, so both have to be somewhere that
    # reading them is possible; one already in a register is left where it is,
    # since moving it would only have to choose a width to move at.
    held = _in_a_register(asm, left, scratch, span)
    divisor = _in_a_register(asm, right, scratch, span)

    zero = MCImm(0, 32, signed=False)
    _answer(asm, Condition.EQ, destination, divisor, zero, zero, divide_by_zero, span)
    if ty.signed:
        # Only this one pair overflows, so it is asked about as one thing: the
        # dividend being the most negative number *and* the divisor minus one.
        # Two questions and one answer, which is what the second clamp on a
        # value the first already settled comes to.
        smallest = MCReg(scratch.scratch())
        # The immediate is declared at the width the value is held at, so that
        # what carries it into a register is a register of that width too.
        asm.setcond(Condition.EQ, smallest.reg, held,
                    MCImm(ty.low, bits, signed=True), span)
        negative_one = MCReg(scratch.scratch())
        asm.setcond(Condition.EQ, negative_one.reg, divisor,
                    MCImm(-1, 32, signed=True), span)
        asm.op(ops.AND, smallest.reg, smallest, negative_one, span=span)
        _answer(asm, Condition.NE, destination, smallest, zero, zero, fault, span)
    asm.divide(destination, held, divisor, ty.signed,
               op in (BinOp.SREM, BinOp.UREM), bits, span)


def lower_division_result(asm: Assembler, op: BinOp, ty: Type, left: MCOperand,
                          right: MCOperand, destination: Reg, failed: Reg,
                          scratch: Scratch, register_bits: int,
                          span: Span) -> None:
    """Emit a division that answers with a result rather than stopping.

    The two questions are the same two the stopping form asks -- a divisor of
    zero, and the one signed pair whose quotient is one past the end of the type
    -- and what differs is what is done about them.  Here they are put together
    into one truth value, which is the half of the result that says whether
    there is an answer, and the division is jumped over where there is not: two
    of these architectures fault on a division by zero themselves, so asking
    first is not an optimization but the only way to reach the next
    instruction.

    The answer half is written either way.  A register holding whatever it held
    before would be a value the program could read, and what makes reading it
    harmless is that it is zero rather than that nothing may look.
    """
    if not isinstance(ty, IntType):
        raise Unsupported("a division of something that is not an integer")
    bits = max(32, ty.bits) if register_bits < 64 else 64
    held = _in_a_register(asm, left, scratch, span)
    divisor = _in_a_register(asm, right, scratch, span)
    zero = MCImm(0, 32, signed=False)
    asm.setcond(Condition.EQ, failed, divisor, zero, span)
    if ty.signed:
        # Only this one pair overflows, so it is asked about as one thing: the
        # dividend being the most negative number *and* the divisor minus one.
        smallest = MCReg(scratch.scratch())
        asm.setcond(Condition.EQ, smallest.reg, held,
                    MCImm(ty.low, bits, signed=True), span)
        negative_one = MCReg(scratch.scratch())
        asm.setcond(Condition.EQ, negative_one.reg, divisor,
                    MCImm(-1, 32, signed=True), span)
        asm.op(ops.AND, smallest.reg, smallest, negative_one, span=span)
        asm.op(ops.OR, failed, MCReg(failed),
               MCReg(smallest.reg, bits=failed.bits), span=span)
    asm.loadreg(destination, MCImm(0, max(32, ty.bits), signed=False), span)
    answered = asm.reserve_label("no.answer")
    asm.branch(Condition.NE, MCReg(failed), zero, answered, span)
    asm.divide(destination, held, divisor, ty.signed,
               op in (BinOp.SREM, BinOp.UREM), bits, span)
    asm.block(answered)


def _in_a_register(asm: Assembler, operand: MCOperand, scratch: Scratch,
                   span: Span) -> MCReg:
    """*operand* where reading it twice is possible."""
    if isinstance(operand, MCReg):
        return operand
    carried = MCReg(scratch.scratch())
    asm.loadreg(carried.reg, operand, span)
    return carried


def lower_wrapping(asm: Assembler, op: BinOp, ty: Type, left: MCOperand,
                   right: MCOperand, destination: Reg, register_bits: int,
                   span: Span) -> None:
    """Emit the operation with nothing asked afterwards about what it came to.

    One instruction, and then the answer put back into the type where the type
    is narrower than the register it was computed in.  That last step is the
    whole difference from the checked and the saturating forms: those two never
    produce a value outside the type, so the bits above it already say what the
    type says, and this one deliberately does not -- a sum of two bytes that
    wrapped has a ninth bit, and the register would otherwise hold a number the
    type has no value for.
    """
    asm.op(_PLAIN[op], destination, left, right, span=span)
    if isinstance(ty, IntType) and ty.bits < register_bits:
        _back_into_the_type(asm, ty, destination, span)


def lower_extremum(asm: Assembler, op: BinOp, left: MCOperand, right: MCOperand,
                   destination: Reg, scratch: Scratch, span: Span) -> None:
    """Emit the larger or the smaller of two values.

    The left goes into the destination and the right replaces it where the
    comparison says it should -- which is the one thing every saturating
    operation is already made of, asked here of the other operand instead of a
    bound.  So it is a comparison and a conditional move on every architecture
    that has one, and a comparison and a branch on any that does not, and
    neither of those decisions is made here.
    """
    held = _at_the_width(asm, left, scratch, destination.bits, span)
    other = _at_the_width(asm, right, scratch, destination.bits, span)
    asm.loadreg(destination, held, span)
    asm.clamp(EXTREMA[op], destination, held, other, other, span)


def _at_the_width(asm: Assembler, operand: MCOperand, scratch: Scratch,
                  bits: int, span: Span) -> MCReg:
    """*operand* in a register named at the width the answer is.

    A register with no name of its own is a whole word wide whatever is put in
    it, and moving a whole word into a byte is not an instruction any of these
    machines has.  So what comes back names the width of the value rather than
    the width of the register holding it, which is what every other operand of
    a narrow operation does.
    """
    if isinstance(operand, MCReg):
        return operand
    carried = scratch.scratch()
    asm.loadreg(carried, operand, span)
    return MCReg(carried, bits=bits)


#: The checked operation each wrapping one is the unchecked form of.
_UNCHECKED = {
    BinOp.WRAP_SHL: BinOp.SHL, BinOp.WRAP_ASHR: BinOp.ASHR,
    BinOp.WRAP_LSHR: BinOp.LSHR,
    BinOp.WRAP_ROTL: BinOp.ROTL, BinOp.WRAP_ROTR: BinOp.ROTR,
}


def lower_shift(asm: Assembler, op: BinOp, ty: Type, value: MCOperand,
                amount: MCOperand, destination: Reg, scratch: Scratch,
                register_bits: int, too_far: Fault, span: Span) -> None:
    """Emit a shift or a rotation, with the distance checked first -- or, for
    the wrapping forms, taken modulo the width instead.

    **A distance of the width or more has no answer**, and the three
    architectures answer it three different ways: two take the distance modulo
    the width of the register -- which is not the width of the type -- and the
    third does something else again.  So the distance is compared with the width
    of the *type* and the program stops where it is too far, which is the same
    thing happening everywhere.  A negative distance for a signed type is caught
    by the same comparison, being enormous read as unsigned.

    **What falls off the end of a shift is gone.**  That is what a shift is, and
    it is why this does not fault on bits lost the way an addition faults on a
    sum that does not fit: `\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}` is how a bit pattern is built, and a pattern that grew past
    the type is a pattern that was asked for.  The result is brought back into
    the type afterwards, which for an unsigned type is a mask and for a signed
    one is the sign put back.
    """
    if not isinstance(ty, IntType):
        raise Unsupported("a shift of something that is not an integer")
    held = _in_a_register(asm, value, scratch, span)
    distance = _in_a_register(asm, amount, scratch, span)
    plain = _UNCHECKED.get(op)
    if plain is not None:
        # Inside a wrap the distance is not a question the program can get
        # wrong: it is taken modulo the width of the type, which every width
        # being a power of two makes one `and` rather than a division.  The
        # architectures each take it modulo the width of the *register*, which
        # is not the same thing, so this is done here and not left to them.
        asm.op(ops.AND, distance.reg, distance,
               MCImm(ty.bits - 1, 32, signed=False), span=span)
        op = plain
    else:
        width = MCImm(ty.bits, 32, signed=False)
        _answer(asm, Condition.UGE, destination, distance, width,
                MCImm(0, 32, signed=False), too_far, span)
    # Everything below is done at the full width of a register.  A value
    # narrower than one carries its own zeroes or its own sign above itself, so
    # the wide reading of it is the value; what the wide answer has above the
    # type is then put back to what the type says, which is the last step.
    answer = scratch.scratch()
    if op in (BinOp.ROTL, BinOp.ROTR):
        _rotate(asm, op, ty, held, distance, answer, scratch, span)
    else:
        moving = {BinOp.SHL: ops.SHIFT_LEFT, BinOp.LSHR: ops.SHIFT_RIGHT,
                  BinOp.ASHR: ops.SHIFT_RIGHT_SIGNED}[op]
        asm.shift(moving, answer, _whole(held), _whole(distance), 64, span)
        _back_into_the_type(asm, ty, answer, span)
    # The answer is inside the type by now, so naming the narrower part of the
    # register it was computed in loses nothing.
    asm.loadreg(destination, MCReg(answer, bits=register_bits), span)


def _rotate(asm: Assembler, op: BinOp, ty: IntType, value: MCReg,
            distance: MCReg, destination: Reg, scratch: Scratch,
            span: Span) -> None:
    """Turn the bits of *value* round by *distance*.

    Two shifts and an or, rather than the rotate instruction the architectures
    have: theirs turns a whole register round, and a value of a narrower type
    occupies only part of one.  The other distance is the width less this one,
    taken modulo the width -- which for a width that is a power of two is one
    and, since every width here is one, is what makes a rotation by nothing come
    out as the value itself rather than as a shift by the whole width.
    """
    left = MCReg(scratch.scratch())
    right = MCReg(scratch.scratch())
    other = MCReg(scratch.scratch())
    asm.op(ops.MINUS, other.reg, MCImm(ty.bits, 32, signed=False),
           _whole(distance), span=span)
    asm.op(ops.AND, other.reg, other, MCImm(ty.bits - 1, 32, signed=False),
           span=span)
    forwards, backwards = ((_whole(distance), other) if op is BinOp.ROTL
                           else (other, _whole(distance)))
    asm.shift(ops.SHIFT_LEFT, left.reg, _whole(value), forwards, 64, span)
    asm.shift(ops.SHIFT_RIGHT, right.reg, _whole(value), backwards, 64, span)
    asm.op(ops.OR, destination, left, right, span=span)
    _back_into_the_type(asm, ty, destination, span)


def _whole(operand: MCReg) -> MCReg:
    """*operand* naming the whole of the register it is in."""
    return MCReg(operand.reg, bits=64)


def _back_into_the_type(asm: Assembler, ty: IntType, destination: Reg,
                        span: Span) -> None:
    """Put the bits above the type back to what the type says they are.

    Unsigned is an `and` with the bits the type has, whatever its width.  Signed
    is a widening where the type is one of the widths a machine widens from, and
    where it is not -- a type of five bits has no instruction that reads five
    bits -- it is a pair of shifts: up until the top bit of the type is the top
    bit of the register, and back down with the sign coming in behind it.
    """
    if ty.bits >= 64:
        return
    if not ty.signed:
        asm.op(ops.AND, destination, MCReg(destination),
               MCImm((1 << ty.bits) - 1, 32 if ty.bits < 32 else 64,
                     signed=False),
               span=span)
        return
    if ty.whole:
        asm.widen(destination, MCReg(destination), ty.bits, True, span)
        return
    # Across the whole register, which is what the widening instruction the
    # other branch uses does: the bits above the type are the sign as far up as
    # the register goes, and nothing below has to know where the type ended.
    over = MCImm(64 - ty.bits, 32, signed=False)
    asm.shift(ops.SHIFT_LEFT, destination, MCReg(destination, bits=64), over,
              64, span)
    asm.shift(ops.SHIFT_RIGHT_SIGNED, destination, MCReg(destination, bits=64),
              over, 64, span)
