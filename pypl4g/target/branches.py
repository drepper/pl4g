"""Turning the branches of the representation into machine branches.

This is the same on every target, so it is written once.  What differs -- which
instruction tests a condition, and whether testing it takes one instruction or
two -- is behind ``Assembler.branch``, which each backend answers for itself.

Two things are decided here rather than in a backend.

**Which way round a branch is written.**  A two-way branch becomes one
conditional branch and one jump, and the jump is not needed when the block it
would go to is the next one in the image.  So when the block the branch falls
through to is the one that comes next, the jump goes; and when it is the other
one, the condition is inverted so that it can.  That saves an instruction on
nearly every branch, and the choice belongs where the block order is known.

**What a condition is.**  A comparison that feeds exactly one branch is folded
into it, because that is what every architecture's branch expects: one has the
comparison in the branch itself and the other two set flags in the instruction
before.  A condition arriving any other way -- a variable holding a truth value,
one day the result of a call -- is a value, and branching on it is branching on
its being other than zero.

**A comparison whose answer is wanted as a value** is the other half of the same
question, and it is here for the same reason: the condition and what stands in
it are the part that is the same everywhere, and the instructions that say so
are the part that is not.  A truth value is one or zero, which is what every
instruction producing one on these architectures produces.
"""

from typing import Protocol, Sequence

from ..ir.function import BasicBlock, Function
from ..ir.inst import (BlockTarget, BrInst, CmpInst, CmpPred, CondBrInst,
                       Instruction, Terminator)
from ..ir.types import MEM, parts_of
from ..ir.value import BlockParam
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCOperand, MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg
from ..source.location import Span

class UnsupportedBranch(Exception):
    """A branch shape no backend generates and none can yet lower.

    Raised rather than reported, so that a backend turns it into its own
    diagnostic with its own span, the way it does for every other construct it
    has no rule for.
    """

    def __init__(self, what: str, span: Span) -> None:
        super().__init__(what)
        self.what = what
        self.span = span


#: What each comparison of the representation tests, in the assembler's terms.
CONDITIONS: dict[CmpPred, Condition] = {
    CmpPred.EQ: Condition.EQ, CmpPred.NE: Condition.NE,
    CmpPred.SLT: Condition.SLT, CmpPred.SLE: Condition.SLE,
    CmpPred.SGT: Condition.SGT, CmpPred.SGE: Condition.SGE,
    CmpPred.ULT: Condition.ULT, CmpPred.ULE: Condition.ULE,
    CmpPred.UGT: Condition.UGT, CmpPred.UGE: Condition.UGE,
}

class Operands(Protocol):
    """How a backend turns a value of the representation into an operand.

    Two questions, because they have different answers: what the assembler is
    handed for a value, and what it is handed where only a register will do.
    Every one of the three architectures compares against a register on the left
    and will take a constant on the right, so a comparison asks for one of each.
    """

    def value(self, value: object, span: Span) -> MCOperand:
        """The operand for *value*, a constant being an immediate."""
        ...

    def in_register(self, value: object, span: Span) -> MCOperand:
        """The operand for *value*, put in a register if it is not in one."""
        ...

    def destination(self, value: object) -> Reg:
        """The register *value* is computed into, for a value that is written
        rather than read: a block parameter, which a branch writes."""
        ...

    def part_of(self, value: object, index: int, span: "Span | None") -> Reg:
        """The register holding one of a value's several parts."""
        ...


def block_label(symbol: str, index: int, block: BasicBlock) -> str:
    """The label the block at *index* of *symbol* is known by.

    The first block keeps the name the builder gives a function's entry, so that
    a branch back to it names the block that is already there rather than a
    second one at the same address.
    """
    if index == 0:
        return "".join((".L", symbol, "_entry"))
    return "".join((".L", symbol, "_", block.label))


def labels_of(symbol: str, func: Function) -> list[str]:
    """The label of every block of *func*, in the order they are laid out."""
    return [block_label(symbol, index, block)
            for index, block in enumerate(func.blocks)]


def condition_used_once(func: Function, value: Instruction) -> bool:
    """Whether *value* is read by exactly one instruction of *func*.

    A comparison read once is folded into the branch that reads it, because that
    is the shape every one of these architectures has.  One read anywhere else
    is computed into a register instead, by `lower_comparison` below, and the
    branch then tests that register against zero like any other value.
    """
    users = 0
    for block in func.blocks:
        for inst in block.insts:
            users += sum(1 for operand in inst.operands if operand is value)
    return users == 1


def folded_into_branch(func: Function, value: Instruction) -> bool:
    """Whether *value* is a comparison that the branch reading it absorbs.

    Being read once is not enough: it has to be read once *by a branch*, and as
    that branch's condition.  A comparison read once by anything else -- a
    return, a store, one day a call -- is a value that something wants, and a
    value something wants has to be somewhere, which means a register.
    """
    if not condition_used_once(func, value):
        return False
    for block in func.blocks:
        for inst in block.insts:
            if value in inst.operands:
                return isinstance(inst, CondBrInst) and inst.operands[0] is value
    return False


def lower_comparison(asm: Assembler, inst: CmpInst, operands: Operands,
                     destination: Reg) -> None:
    """Compute the truth value of *inst* into *destination*.

    This is the case the branch above does not take: a comparison whose answer
    is wanted as a value rather than as a place to go.  Which instructions say
    that differs on all three targets -- and on one of them the comparison is
    the only instruction there is -- so what is written here is the part that
    does not: which condition, and which two things stand in it.
    """
    asm.setcond(CONDITIONS[inst.pred], destination,
                operands.in_register(inst.operands[0], inst.span),
                operands.value(inst.operands[1], inst.span), inst.span)


def lower_branch(asm: Assembler, func: Function, labels: Sequence[str], index: int,
                 terminator: Terminator, operands: Operands,
                 zero: MCImm) -> bool:
    """Emit the machine form of one block's terminator.

    Returns whether the terminator was one this handles, so that a backend can
    go on to its own cases.  *zero* is what the target compares against when a
    condition is a value rather than a comparison, since how wide an immediate
    is written differs between them.
    """
    span = terminator.span
    following = labels[index + 1] if index + 1 < len(labels) else None
    match terminator:
        case BrInst():
            target = _target_label(func, labels, terminator.target.block)
            _pass_arguments(asm, terminator.target, operands, span)
            if target == following:
                # Control arrives there by simply going on, so there is nothing
                # to emit; the edge is still recorded, since it is still an edge.
                asm.falls_through(target)
            else:
                asm.jump(target, span)
            return True
        case CondBrInst():
            for edge in (terminator.true_target, terminator.false_target):
                if edge.args:
                    # The moves would belong on one edge and there is no block
                    # there to put them in; splitting the edge is what that
                    # needs, and nothing generates this shape.
                    raise UnsupportedBranch(
                        "a conditional branch that passes arguments", span)
            _lower_conditional(asm, func, labels, following, terminator,
                               operands, zero)
            return True
        case _:
            return False


def _pass_arguments(asm: Assembler, target: BlockTarget, operands: Operands,
                    span: Span) -> None:
    """Put a branch's arguments where the block it goes to will look for them.

    A block parameter is a value like any other and lives in a register; what
    a branch carries is the instruction to put something there.  The moves go
    before the jump, which is where they can go because only an unconditional
    branch reaches here -- an edge of a conditional one would need a block of
    its own to hold them.

    The moves are emitted in order, so a block whose parameters were rearranged
    among themselves -- the second taking what the first held -- would read a
    register after it had been written.  One value never can, and nothing
    generates more than one; the case is refused rather than got wrong.
    """
    if not target.args:
        return
    block = target.block
    assert isinstance(block, BasicBlock)
    # A memory token is not held anywhere: it exists to order the operations
    # that touch memory, and a parameter of one says only which path's ordering
    # holds from here.  There is nothing to move for it.
    carried = [(param, arg) for param, arg in zip(block.params, target.args)
               if param.ty is not MEM]
    if len(carried) > 1 and any(
            isinstance(arg, BlockParam) and arg.block is block
            for _, arg in carried):
        raise UnsupportedBranch(
            "a branch that passes a block's own parameters back to it", span)
    for param, argument in carried:
        pieces = parts_of(param.ty)
        if len(pieces) == 1:
            asm.loadreg(operands.destination(param),
                        operands.value(argument, span), span)
            continue
        # A value of several parts is that many registers, and a branch that
        # hands one over hands over every one of them.
        for index in range(len(pieces)):
            asm.loadreg(operands.part_of(param, index, span),
                        MCReg(operands.part_of(argument, index, span)), span)


def _lower_conditional(asm: Assembler, func: Function, labels: Sequence[str],
                       following: str | None, terminator: CondBrInst,
                       operands: Operands, zero: MCImm) -> None:
    """Emit a two-way branch, turning it round where that saves the jump."""
    span = terminator.span
    when_true = _target_label(func, labels, terminator.true_target.block)
    when_false = _target_label(func, labels, terminator.false_target.block)
    condition, lhs, rhs = _condition_of(func, terminator, operands, zero, span)
    if when_true == following:
        asm.branch(condition.inverted(), lhs, rhs, when_false, span)
        asm.falls_through(when_true)
        return
    asm.branch(condition, lhs, rhs, when_true, span)
    if when_false == following:
        asm.falls_through(when_false)
    else:
        asm.jump(when_false, span)


def _condition_of(func: Function, terminator: CondBrInst, operands: Operands,
                  zero: MCImm, span: Span) -> tuple[Condition, MCOperand, MCOperand]:
    """What the branch tests, and the two things it tests."""
    value = terminator.operands[0]
    if isinstance(value, CmpInst) and folded_into_branch(func, value):
        return (CONDITIONS[value.pred],
                operands.in_register(value.operands[0], span),
                operands.value(value.operands[1], span))
    return Condition.NE, operands.in_register(value, span), zero


def _target_label(func: Function, labels: Sequence[str], block: object) -> str:
    """The label of the block a branch names."""
    for index, candidate in enumerate(func.blocks):
        if candidate is block:
            return labels[index]
    raise KeyError("a branch names a block that is not in this function")
