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

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from ..ir.function import BasicBlock, Function
from ..ir.inst import (BlockTarget, BrInst, CmpInst, CmpPred, CondBrInst,
                       Instruction, Terminator)
from ..ir.types import MEM, parts_of
from ..ir.value import BlockParam
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCOperand, MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg, interferes
from ..mc.regalloc import registers_of
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
                if carried_values(edge):
                    # The moves would belong on one edge and there is no block
                    # there to put them in; splitting the edge is what that
                    # needs, and nothing generates this shape.  What is counted
                    # is the values, not the arguments: an edge carrying only a
                    # memory token costs no instruction and needs no block.
                    raise UnsupportedBranch(
                        "a conditional branch that passes arguments", span)
            _lower_conditional(asm, func, labels, following, terminator,
                               operands, zero)
            return True
        case _:
            return False


def carried_values(target: BlockTarget) -> list[tuple[BlockParam, object]]:
    """The parameters a branch actually has to put something in.

    A memory token is not held anywhere: it exists to order the operations that
    touch memory, and a parameter of one says only which path's ordering holds
    from here.  There is nothing to move for it, which is why an edge carrying
    only one costs no instruction and is not refused.
    """
    block = target.block
    assert isinstance(block, BasicBlock)
    return [(param, arg) for param, arg in zip(block.params, target.args)
            if param.ty is not MEM]


@dataclass(frozen=True, slots=True)
class Move:
    """One register a branch writes, and what it writes there."""

    into: Reg
    source: MCOperand


def _reads(source: MCOperand, reg: Reg) -> bool:
    """Whether putting something in *reg* would change what *source* names."""
    return any(interferes(named, reg) for named, _ in registers_of(source))


def sequenced(moves: Sequence[Move],
               spare: "Callable[[Reg], Reg]") -> list[Move]:
    """The same moves in an order in which none reads what another has written.

    A move is ready when no move still to be made reads the register it writes.
    Where none is ready, every move left is in a cycle -- a block handed its own
    parameters rearranged, which is what a loop carrying two values does on
    every turn -- and one register is copied into a spare, the move reading it
    is pointed at the spare instead, and the cycle is a chain again.

    The hazard is between *registers* and not between the values of the
    representation: taking one value out of another leaves both in one
    register, so a branch can read a parameter's register without any parameter
    appearing among its arguments.
    """
    # A parameter handed its own value is no move at all, and leaving it in
    # would make the loop below invent a spare to break a cycle of one.
    pending = [move for move in moves if not _holds_it_already(move)]
    ordered: list[Move] = []
    while pending:
        ready = [move for move in pending
                 if not any(other is not move and _reads(other.source, move.into)
                            for other in pending)]
        if ready:
            for move in ready:
                ordered.append(move)
                pending.remove(move)
            continue
        # Nothing is ready, so every move left reads a register another writes.
        # Break one link by holding a copy of what it reads.
        stuck = pending[0]
        held = spare(stuck.into)
        ordered.append(Move(into=held, source=MCReg(stuck.into)))
        pending = [Move(into=move.into, source=MCReg(held))
                   if move is not stuck and _reads(move.source, stuck.into)
                   else move
                   for move in pending]
    return ordered


def _holds_it_already(move: Move) -> bool:
    """Whether a move puts a register back where it already is."""
    return isinstance(move.source, MCReg) and interferes(move.source.reg, move.into)


def _moves_of(target: BlockTarget, operands: Operands,
              span: Span) -> list[Move]:
    """Every register a branch writes and what it writes there.

    Built whole before anything is emitted, because asking for a value can emit
    instructions of its own -- a number too wide for an immediate, a
    floating-point constant read out of the image -- and those belong before
    the copy rather than in the middle of it.  What they produce is fresh and
    is never a destination, so the copy cannot disturb it.

    A value of several parts is that many moves, because a cycle may run
    through one part of a value and not another.
    """
    moves: list[Move] = []
    for param, argument in carried_values(target):
        pieces = parts_of(param.ty)
        if len(pieces) == 1:
            moves.append(Move(into=operands.destination(param),
                               source=operands.value(argument, span)))
            continue
        for index in range(len(pieces)):
            moves.append(Move(
                into=operands.part_of(param, index, span),
                source=MCReg(operands.part_of(argument, index, span))))
    return moves


def _pass_arguments(asm: Assembler, target: BlockTarget, operands: Operands,
                    span: Span) -> None:
    """Put a branch's arguments where the block it goes to will look for them.

    A block parameter is a value like any other and lives in a register; what a
    branch carries is the instruction to put something there.  The moves go
    before the jump, which is where they can go because only an unconditional
    branch reaches here -- an edge of a conditional one would need a block of
    its own to hold them.

    They are all made at once and none may read a register another has already
    written, which `sequenced` is what settles.  The spare a cycle needs is a
    fresh virtual register: this runs before anything has been given a physical
    one, so there is always another to be had and no architecture needs an
    instruction that exchanges two.
    """
    if not target.args:
        return
    for move in sequenced(_moves_of(target, operands, span), asm.temporary):
        asm.loadreg(move.into, move.source, span)


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
