"""Control-flow simplification.

Four things, and each of them is what the one before it leaves behind.

A **branch that asks a question whose answer is already known** is replaced by
the jump it would have taken, which is what makes a condition the folder settled
stop costing a branch.  **Blocks that no branch can reach** are then removed.  A
**parameter every branch supplies the same value for** is replaced by that value
and taken off the block, which is what the block-parameter form calls a phi
worth nothing.  And a **block with one way in, whose one predecessor has one way
out**, is written into that predecessor and removed.

The last two are why the first two are worth doing.  A branch settled leaves a
block that only one branch reaches; a block that only one branch reaches leaves
a parameter with one incoming argument; a parameter replaced by its argument
leaves a block that is nothing but a jump.  So the four run until nothing
changes rather than once each.

What none of this does is reorder blocks.  The order they are laid out in is the
order the backend walks, and a value has to be computed in a block standing
before the one that reads it; merging removes a block and moves nothing, which
keeps that true without having to reason about it.
"""

from __future__ import annotations

from typing import Final

from ...ir.function import BasicBlock, Function
from ...ir.inst import BlockTarget, BrInst, CondBrInst, SwitchInst
from ...ir.module import Module
from ...ir.rewrite import incoming_edges, stands_for
from ...ir.value import BoolConst, EnumConst, IntConst

#: How many times the four are run before it is taken to be a defect rather than
#: a program.  Each round that changes anything removes a block or a parameter,
#: so a function of any size settles long before this.
ROUNDS: Final[int] = 100


class SimplifyCFG:
    """Settles branches, and removes what that leaves without work to do."""

    name = "simplifycfg"

    def run(self, module: Module) -> bool:
        """Simplify every function; report whether anything changed."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration:
                continue
            for _ in range(ROUNDS):
                round_ = (self._settle_branches(func)
                          | self._prune(func)
                          | self._settle_params(func)
                          | self._merge_blocks(func))
                changed |= round_
                if not round_:
                    break
        return changed

    def _settle_branches(self, func: Function) -> bool:
        """Replace a branch on a known condition by the jump it would take.

        The block it would not have gone to is left alone; it becomes
        unreachable if nothing else names it, and the pruning below is what
        notices that.  Doing the two separately is what keeps this from having
        to know whether some other branch also names that block.
        """
        changed = False
        for block in func.blocks:
            terminator = block.terminator
            target = None
            if isinstance(terminator, CondBrInst):
                taken = _known_condition(terminator.operands[0])
                if taken is not None:
                    target = (terminator.true_target if taken
                              else terminator.false_target)
            elif isinstance(terminator, SwitchInst):
                target = _known_case(terminator)
            if target is None:
                continue
            block.insts[-1] = BrInst(target, terminator.span)
            block.insts[-1].parent = block
            changed = True
        return changed

    def _reachable(self, func: Function) -> set[int]:
        """The identities of the blocks control can reach from the entry."""
        entry = func.entry
        if entry is None:
            return set()
        seen: set[int] = {id(entry)}
        queue: list[BasicBlock] = [entry]
        while queue:
            block = queue.pop()
            terminator = block.terminator
            if terminator is None:
                continue
            for target in terminator.successors():
                successor = target.block
                if isinstance(successor, BasicBlock) and id(successor) not in seen:
                    seen.add(id(successor))
                    queue.append(successor)
        return seen

    def _prune(self, func: Function) -> bool:
        """Remove the blocks that are not reachable."""
        reachable = self._reachable(func)
        kept = [b for b in func.blocks if id(b) in reachable]
        if len(kept) == len(func.blocks):
            return False
        func.blocks = kept
        return True

    def _settle_params(self, func: Function) -> bool:
        """Drop a parameter every branch arriving supplies the same value for.

        A parameter is a question about where control came from, and one whose
        answer is the same whichever way it came is not a question.  The
        commonest is a block with one way in, which is what the entry this
        implements names; a loop that carries a value it never changes is the
        same thing said round a cycle, and is why an argument that is the
        parameter itself is not counted -- on the turn that supplies it, the
        parameter already holds whatever the way in supplied.

        What replaces the parameter is defined before every branch that arrives,
        so it is defined before the block: dominance is kept without asking for
        it.
        """
        changed = False
        edges = incoming_edges(func)
        for block in func.blocks:
            if block is func.entry or not block.params:
                continue
            arriving = [target for _, target in edges.get(id(block), ())]
            if not arriving:
                continue
            for at in reversed(range(len(block.params))):
                param = block.params[at]
                supplied = {id(one.args[at]): one.args[at] for one in arriving}
                supplied.pop(id(param), None)
                if len(supplied) != 1:
                    continue
                stands_for(func, param, next(iter(supplied.values())))
                for one in arriving:
                    del one.args[at]
                del block.params[at]
                changed = True
            for index, param in enumerate(block.params):
                param.index = index
        return changed

    def _merge_blocks(self, func: Function) -> bool:
        """Write a block with one way in into the block that way comes from.

        Only where that block's one way out is this one: a predecessor whose
        branch has another arm still has to be able to go the other way, and a
        block anything else can reach has to stay somewhere to be reached.  The
        arguments the branch carried take the place of the parameters, which is
        the same replacement `_settle_params` makes and is made here because the
        branch is about to stop existing.
        """
        changed = False
        for _ in range(ROUNDS):
            edges = incoming_edges(func)
            joined = False
            for block in list(func.blocks):
                terminator = block.terminator
                if not isinstance(terminator, BrInst):
                    continue
                target = terminator.target.block
                if not isinstance(target, BasicBlock) or target is func.entry:
                    continue
                # A block whose one way in is its own way out is a loop of one,
                # and writing it into itself would be writing it twice.
                if target is block or target.terminator is None:
                    continue
                if len(edges.get(id(target), ())) != 1:
                    continue
                self._absorb(func, block, target, terminator)
                joined = changed = True
                break
            if not joined:
                break
        return changed

    def _absorb(self, func: Function, block: BasicBlock, target: BasicBlock,
                branch: BrInst) -> None:
        """Move everything of *target* into *block* and remove it."""
        for param, arg in zip(target.params, branch.target.args):
            stands_for(func, param, arg)
        block.insts.pop()
        for one in target.insts:
            one.parent = block
        block.insts.extend(target.insts)
        target.params = []
        target.insts = []
        func.blocks.remove(target)


def _known_case(switch: SwitchInst) -> BlockTarget | None:
    """Where a switch on a value that is already known goes.

    A `match` over an enumeration whose subject is a constant is the whole of
    what asks: the case whose number the constant is, or the default where no
    case names it.
    """
    number = _known_number(switch.operands[0])
    if number is None:
        return None
    for value, target in switch.cases:
        if value == number:
            return target
    return switch.default


def _known_number(value: object) -> int | None:
    """What a value is, where that is already settled and it is a number."""
    if isinstance(value, EnumConst):
        return value.number
    if isinstance(value, IntConst):
        return value.value
    return None


def _known_condition(value: object) -> bool | None:
    """Which way a branch on *value* goes, where that is already settled."""
    if isinstance(value, BoolConst):
        return value.value
    if isinstance(value, IntConst):
        # A condition that is a number is tested against zero, so a number that
        # is known is a condition that is known.
        return value.value != 0
    return None
