"""What the passes ask about a function's control-flow graph.

Which blocks follow which, and which dominate which.  Dominators are worked out
by Cooper, Harvey and Kennedy's algorithm -- the immediate dominator of each
block, found by intersecting over the blocks in reverse postorder until nothing
moves -- which is a handful of passes over the blocks for any graph a program
writes, where intersecting sets of every dominator is a set per block per pass.

Nothing is cached: a pass that changes the graph asks again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..ir.function import BasicBlock, Function
from ..ir.value import Value


def successors(block: BasicBlock) -> list[BasicBlock]:
    """The blocks *block* may go to next, in the order its terminator names them."""
    last = block.insts[-1] if block.insts else None
    found = getattr(last, "successors", None)
    return [one.block for one in found()] if found is not None else []


def predecessors(func: Function) -> dict[int, list[BasicBlock]]:
    """For each block, by identity, the blocks that may go to it -- once each."""
    found: dict[int, list[BasicBlock]] = {id(one): [] for one in func.blocks}
    for block in func.blocks:
        for one in dict.fromkeys(successors(block)):
            found[id(one)].append(block)
    return found


def reverse_postorder(func: Function) -> list[BasicBlock]:
    """The blocks reachable from the entry, each before everything it reaches
    that does not reach back to it."""
    order: list[BasicBlock] = []
    seen: set[int] = set()
    stack: list[tuple[BasicBlock, int]] = [(func.blocks[0], 0)]
    seen.add(id(func.blocks[0]))
    while stack:
        block, at = stack.pop()
        following = successors(block)
        if at < len(following):
            stack.append((block, at + 1))
            one = following[at]
            if id(one) not in seen:
                seen.add(id(one))
                stack.append((one, 0))
        else:
            order.append(block)
    order.reverse()
    return order


@dataclass(slots=True)
class Dominators:
    """Which block dominates which, for the blocks reachable from the entry."""

    #: The blocks in reverse postorder, which puts every block after its
    #: immediate dominator.
    order: list[BasicBlock]
    #: Each block's immediate dominator, by identity; the entry has none.
    idom: dict[int, BasicBlock | None]
    #: Each block's children in the dominator tree, by identity.
    children: dict[int, list[BasicBlock]] = field(default_factory=dict)
    #: When the walk of the tree reaches each block and leaves it, which is what
    #: answers "does this one dominate that one" in two comparisons.
    entered: dict[int, int] = field(default_factory=dict)
    left: dict[int, int] = field(default_factory=dict)

    def dominates(self, one: BasicBlock, other: BasicBlock) -> bool:
        """Whether every way to *other* goes through *one*."""
        if id(one) not in self.entered or id(other) not in self.entered:
            return False
        return self.entered[id(one)] <= self.entered[id(other)] \
            and self.left[id(other)] <= self.left[id(one)]


def dominators(func: Function) -> Dominators:
    """Work out who dominates whom in *func*."""
    order = reverse_postorder(func)
    number = {id(one): at for at, one in enumerate(order)}
    preds = predecessors(func)
    entry = order[0]
    idom: dict[int, BasicBlock | None] = {id(entry): entry}

    def meet(one: BasicBlock, other: BasicBlock) -> BasicBlock:
        while one is not other:
            while number[id(one)] > number[id(other)]:
                found = idom[id(one)]
                assert found is not None
                one = found
            while number[id(other)] > number[id(one)]:
                found = idom[id(other)]
                assert found is not None
                other = found
        return one

    changed = True
    while changed:
        changed = False
        for block in order[1:]:
            new: BasicBlock | None = None
            for pred in preds[id(block)]:
                if id(pred) not in idom:
                    continue
                new = pred if new is None else meet(pred, new)
            if new is not None and idom.get(id(block)) is not new:
                idom[id(block)] = new
                changed = True
    idom[id(entry)] = None
    tree = Dominators(order=order, idom=idom)
    for block in order:
        tree.children.setdefault(id(block), [])
        parent = idom.get(id(block))
        if parent is not None:
            tree.children.setdefault(id(parent), []).append(block)
    clock = 0
    stack: list[tuple[BasicBlock, bool]] = [(entry, False)]
    while stack:
        block, done = stack.pop()
        if done:
            tree.left[id(block)] = clock
            clock += 1
            continue
        tree.entered[id(block)] = clock
        clock += 1
        stack.append((block, True))
        for child in reversed(tree.children[id(block)]):
            stack.append((child, False))
    return tree


def replace_uses(func: Function, instead: Mapping[int, Value],
                 dropped: set[int] | None = None) -> None:
    """Point every use of a value in *instead* at what it stands for, and drop
    those instructions -- and the ones in *dropped*.

    What a value stands for may itself stand for something, a chain the
    replacements made one after another; every use is pointed at the end of it.
    """
    def settled(value: Value) -> Value:
        while id(value) in instead:
            value = instead[id(value)]
        return value

    gone = set(instead) | (dropped or set())
    for block in func.blocks:
        for inst in block.insts:
            for index, operand in enumerate(inst.operands):
                if id(operand) in instead:
                    inst.operands[index] = settled(operand)
            for target in (inst.successors() if hasattr(inst, "successors") else ()):
                for index, arg in enumerate(target.args):
                    if id(arg) in instead:
                        target.args[index] = settled(arg)
        block.insts = [one for one in block.insts if id(one) not in gone]
