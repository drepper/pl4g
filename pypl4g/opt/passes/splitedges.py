"""Putting a block on an edge that has moves to make.

A branch carries the values the block it goes to takes as parameters, and those
values have to be put where that block will look for them -- which is a move,
and a move is an instruction, and an instruction has to be in a block.  For a
branch with one way out there is a block: its own, just before the jump.  For a
branch with more than one there is not, since the moves belong on one of the
ways and would be made on both.

So a block is put there.  The edge becomes two: the branch goes to a new block
carrying nothing, and the new block goes on to where the branch was going,
carrying what the branch carried.  That block has one way in and one way out and
nothing in it but the moves, which is exactly the place the moves needed.

**Which edges.**  The ones whose source has more than one way out and which
carry a value.  The classical rule asks for the target to have more than one way
in as well -- a *critical* edge -- because where it has only one the moves can
be put at the top of the target instead.  That is not available here: what puts
the moves anywhere is the backend, which writes them before the branch, and
before a branch with two ways out is the one place they must not be.  An edge
carrying only a memory token is not counted, a token being an ordering and not a
value, so nothing is moved for it.

**Where the new block goes.**  Straight after the block the branch is in.  The
order the blocks are laid out in is the order the backend walks, and a value has
to be computed in a block standing before the one that reads it; what the new
block reads is what the branch carried, which is computed before the branch, so
just after the branch is a place that is right whatever the rest of the layout
looks like -- including a branch that goes backwards, where putting it in front
of its target would put it in front of what it reads.

Nothing generates such a branch yet: every lowering that could is shaped to
carry its values on a jump instead.  The pass is here so that the day one does,
it is generated rather than refused (8501), and it runs at every optimization
level because what it does is not an optimization but a shape the machine needs.
"""

from __future__ import annotations

from ...ir.function import BasicBlock, Function
from ...ir.inst import BlockTarget, BrInst
from ...ir.module import Module
from ...ir.rewrite import carried_values


class SplitEdges:
    """Gives every edge that has moves to make a block to make them in."""

    name = "splitedges"

    def run(self, module: Module) -> bool:
        """Split what has to be split; report whether anything changed."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration:
                continue
            changed |= self._split(func)
        return changed

    def _split(self, func: Function) -> bool:
        """Put a block on every edge of *func* that needs one."""
        changed = False
        at = 0
        while at < len(func.blocks):
            block = func.blocks[at]
            terminator = block.terminator
            made = 0
            if terminator is not None and len(terminator.successors()) > 1:
                for edge in terminator.successors():
                    if not isinstance(edge.block, BasicBlock):
                        continue
                    if not carried_values(edge):
                        continue
                    made += 1
                    self._interpose(func, block, edge, at + made)
            changed |= made > 0
            # Past this block and past everything put after it: what was made
            # holds one jump and nothing that could want splitting.
            at += 1 + made
        return changed

    def _interpose(self, func: Function, source: BasicBlock, edge: BlockTarget,
                   at: int) -> None:
        """Put a fresh block on *edge*, laid out at *at*.

        The new block takes over what the edge carried and the edge is left
        carrying nothing, so the branch has nothing to move and the block it now
        goes to has one way in, one way out, and a jump that may.
        """
        made = func.add_block("edge")
        func.blocks.remove(made)
        func.blocks.insert(at, made)
        made.append(BrInst(BlockTarget(edge.block, edge.args),
                           source.insts[-1].span))
        edge.block = made
        edge.args = []
