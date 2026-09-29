"""Small changes to a function that several passes make the same way.

What is here is what more than one pass needs and none of them owns: making
every reader of a value read another one instead, and asking which branches
arrive at a block.  Both were written out where they were wanted, more than
once and not quite alike, which is the reason for a place to put them.
"""

from __future__ import annotations

from .function import BasicBlock, Function
from .inst import BlockTarget, Terminator
from .value import Value


def stands_for(func: Function, gone: Value, instead: Value) -> None:
    """Make everything in *func* that read *gone* read *instead*.

    A branch's arguments count as reads: a value carried to another block is
    the ordinary way one leaves the block it was made in, and a pass that
    replaced only the operands would leave the old value alive on the edges.
    """
    for block in func.blocks:
        for one in block.insts:
            one.operands = [instead if operand is gone else operand
                            for operand in one.operands]
            if isinstance(one, Terminator):
                for target in one.successors():
                    target.args = [instead if arg is gone else arg
                                   for arg in target.args]


def incoming_edges(func: Function) -> dict[int, list[tuple[BasicBlock,
                                                           BlockTarget]]]:
    """For each block, every branch that arrives at it and where it is from.

    Edges and not blocks, because the two are not the same thing: a branch
    whose arms both name one block arrives twice, and a pass that counted
    blocks would think that block had one way in.
    """
    found: dict[int, list[tuple[BasicBlock, BlockTarget]]] = {
        id(block): [] for block in func.blocks}
    for block in func.blocks:
        terminator = block.terminator
        if terminator is None:
            continue
        for target in terminator.successors():
            if isinstance(target.block, BasicBlock):
                found.setdefault(id(target.block), []).append((block, target))
    return found
