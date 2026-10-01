"""Small things about a function that more than one place wants answered.

What is here is what several passes and the backend need and none of them owns:
making every reader of a value read another one instead, asking which branches
arrive at a block, and asking what a branch actually has to move.  Each was
written out where it was wanted -- two of them more than once and not quite
alike -- which is the reason for a place to put them.
"""

from __future__ import annotations

from .function import BasicBlock, Function
from .inst import BlockTarget, Terminator
from .types import MEM
from .value import BlockParam, Value


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


def all_stand_for(func: Function, instead: dict[int, Value]) -> None:
    """`stands_for` for many values at once, by identity: one walk of *func*."""
    if not instead:
        return
    for block in func.blocks:
        for one in block.insts:
            if any(id(operand) in instead for operand in one.operands):
                one.operands = [instead.get(id(operand), operand)
                                for operand in one.operands]
            if isinstance(one, Terminator):
                for target in one.successors():
                    if any(id(arg) in instead for arg in target.args):
                        target.args = [instead.get(id(arg), arg)
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


def carried_values(target: BlockTarget) -> list[tuple[BlockParam, Value]]:
    """The parameters a branch to *target* actually has to put something in.

    A memory token is not held anywhere: it exists to order the operations that
    touch memory, and a parameter of one says only which path's ordering holds
    from here.  There is nothing to move for it, which is why an edge carrying
    only one costs no instruction -- and why nothing has to be done to such an
    edge before a branch with more than one way out can take it.
    """
    block = target.block
    assert isinstance(block, BasicBlock)
    return [(param, arg) for param, arg in zip(block.params, target.args)
            if param.ty is not MEM]
