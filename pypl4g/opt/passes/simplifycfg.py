"""Control-flow simplification.

Two things so far.  A branch that asks a question whose answer is already known
is replaced by the jump it would have taken, which is what makes a condition the
folder settled stop costing a branch.  And blocks that no branch can reach are
removed, which is both what the first leaves behind and what keeps later passes
from having to reason about dead code.  The entry block is never removed.
"""

from ...ir.function import BasicBlock, Function
from ...ir.inst import BrInst, CondBrInst
from ...ir.module import Module
from ...ir.value import BoolConst, IntConst


class SimplifyCFG:
    """Removes unreachable blocks."""

    name = "simplifycfg"

    def run(self, module: Module) -> bool:
        """Drop unreachable blocks; report whether anything changed."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration:
                continue
            if self._settle_branches(func):
                changed = True
            if self._prune(func):
                changed = True
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
            if not isinstance(terminator, CondBrInst):
                continue
            taken = _known_condition(terminator.operands[0])
            if taken is None:
                continue
            target = terminator.true_target if taken else terminator.false_target
            block.insts[-1] = BrInst(target, terminator.span)
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


def _known_condition(value: object) -> bool | None:
    """Which way a branch on *value* goes, where that is already settled."""
    if isinstance(value, BoolConst):
        return value.value
    if isinstance(value, IntConst):
        # A condition that is a number is tested against zero, so a number that
        # is known is a condition that is known.
        return value.value != 0
    return None
