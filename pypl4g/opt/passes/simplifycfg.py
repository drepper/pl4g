"""Control-flow simplification.

For now it removes blocks that no branch can reach, which is the part that keeps
later passes from having to reason about dead code.  The entry block is never
removed.
"""

from ...ir.function import BasicBlock, Function
from ...ir.module import Module


class SimplifyCFG:
    """Removes unreachable blocks."""

    name = "simplifycfg"

    def run(self, module: Module) -> bool:
        """Drop unreachable blocks; report whether anything changed."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration:
                continue
            if self._prune(func):
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
