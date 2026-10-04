"""One value for what is worked out twice.

Where an instruction computes what one before it already computed -- the same
operation over the same operands, and the earlier one dominating it -- the later
one stands for the earlier and goes.  It is value numbering over the dominator
tree: the blocks are walked down the tree, and what each has worked out is in
view for the blocks it dominates and for nothing else.

What the checker writes makes this common: each mention of an array takes its
place and its length out of the value again, each index works out the element's
address again, and a mask asked a place at a time reads the element the turn
then reads once more.  Each is cheap; together they are most of a loop.

**What may be shared is what has no effect.**  An instruction answers that for
itself; the ones that answer "none" and still may not be shared are a frame,
whose every instance is room of its own, and the memory chain's beginning.  A
call is never shared here -- `purecalls` is what knows which calls may be.

**A read is shared where nothing could have written between.**  Two loads from
one address taking one memory token read the same thing: a store makes a new
token for what follows it, so a read after a store takes another token and is no
match for a read before it.  The token is among what a load is numbered by, and
that is the whole of how a store is accounted for -- a body inlined in place of a
call hands on the token it ended at, so the chain runs through it too.  What the
chain does not cover is a call, which is not in it and may write what it was
handed or, answering through the caller's storage, the caller's own room; and a
request to the kernel.  So a load stands for an earlier one only where no call
and no request to the kernel lies on any way from the one to the other.
"""

from __future__ import annotations

from typing import Hashable

from ...ir.function import BasicBlock, Function
from ...ir.inst import (CallInst, FrameInst, Instruction, LoadInst,
                        MemStartInst, Ordering, SyscallInst,
                        Terminator)
from ...ir.module import Module
from ...ir.value import Value
from ..flow import dominators, predecessors, replace_uses, successors

#: The slots every instruction has, which say nothing about what it computes.
_COMMON = frozenset(("operands", "parent", "span", "ty", "name_hint",
                     "name_span", "__weakref__"))


_ATTRIBUTES: dict[type, tuple[tuple[str, bool], ...]] = {}


def _attributes(cls: type) -> tuple[tuple[str, bool], ...]:
    """The slots of an instruction class that say what it computes, and whether
    each is told by its value or by its identity."""
    found = _ATTRIBUTES.get(cls)
    if found is None:
        names = [name for one in cls.__mro__
                 for name in getattr(one, "__slots__", ()) if name not in _COMMON]
        # What a slot holds is told by its value where it is a number, a name or
        # one of the enumerations an operation is chosen by; anything else -- a
        # function a `code` names -- by which one it is.
        found = tuple((name, name not in ("callee",)) for name in names)
        _ATTRIBUTES[cls] = found
    return found


def _shareable(inst: Instruction) -> bool:
    """Whether *inst* computes a value another instruction like it could stand
    for."""
    if not inst.has_result or inst.has_effects:
        return False
    return not isinstance(inst, (Terminator, FrameInst, MemStartInst, CallInst,
                                 SyscallInst))


def _writes_unseen(inst: Instruction) -> bool:
    """Whether *inst* may change memory a later read with the same token reads.

    A store is not among them: it makes a token of its own, and a read after it
    takes that one -- inlined bodies included, which hand on the token they
    ended at.  A call and a request to the kernel are not in the chain, and an
    ordered load is a fence as well as a read.
    """
    return isinstance(inst, (CallInst, SyscallInst)) or (
        isinstance(inst, LoadInst) and inst.ordering is not Ordering.PLAIN)


class SameValues:
    """Lets an instruction stand for an earlier one that computed the same."""

    name = "samevalues"

    def run(self, module: Module) -> bool:
        """Share what can be shared; report whether anything was."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration or not func.blocks:
                continue
            changed |= self._share(func)
        return changed

    def _share(self, func: Function) -> bool:
        """Walk the dominator tree once, sharing as it goes."""
        tree = dominators(func)
        instead: dict[int, Value] = {}
        writers = any(_writes_unseen(inst) for block in func.blocks
                      for inst in block.insts)
        where: dict[int, tuple[BasicBlock, int]] = {}
        layout = {id(one): at for at, one in enumerate(func.blocks)}
        seen: dict[Hashable, Instruction] = {}
        added: list[list[Hashable]] = []

        def standing(value: Value) -> Value:
            while id(value) in instead:
                value = instead[id(value)]
            return value

        stack: list[tuple[BasicBlock, bool]] = [(tree.order[0], False)]
        while stack:
            block, done = stack.pop()
            if done:
                for key in added.pop():
                    del seen[key]
                continue
            mine: list[Hashable] = []
            for at, inst in enumerate(block.insts):
                where[id(inst)] = (block, at)
                if not _shareable(inst):
                    continue
                key = self._key(inst, standing)
                if key is None:
                    continue
                earlier = seen.get(key)
                if earlier is None:
                    seen[key] = inst
                    mine.append(key)
                    continue
                if where[id(earlier)][0] is not block \
                        and layout[id(where[id(earlier)][0])] > layout[id(block)]:
                    # The back ends read the blocks in the order they are laid
                    # out and want a value worked out before it is read, so one
                    # laid out later is not shared however it dominates.
                    continue
                if isinstance(inst, LoadInst) and writers \
                        and self._written_between(func, where[id(earlier)],
                                                  (block, at)):
                    continue
                instead[id(inst)] = earlier
            added.append(mine)
            stack.append((block, True))
            for child in reversed(tree.children[id(block)]):
                stack.append((child, False))
        if not instead:
            return False
        replace_uses(func, instead)
        return True

    def _key(self, inst: Instruction, standing) -> Hashable | None:  # noqa: ANN001
        """What *inst* computes, as something two of them share where they
        compute the same.

        A type is told by its identity: the module's type table makes each type
        once, so two of one type are one object -- and hashing one by what it
        holds walks everything it holds, which was most of what this pass cost.
        """
        return (type(inst), inst.opcode, id(inst.ty),
                tuple([id(standing(one)) for one in inst.operands]),
                tuple([getattr(inst, name, None) if hashed else
                       id(getattr(inst, name, None))
                       for name, hashed in _attributes(type(inst))]))

    def _written_between(self, func: Function, first: tuple[BasicBlock, int],
                         second: tuple[BasicBlock, int]) -> bool:
        """Whether something that writes unseen lies on a way from *first* to
        *second*."""
        (start, at), (end, upto) = first, second
        if start is end and at < upto:
            # The same block, the first before the second: what is between them
            # is all there is, unless the block is a loop of its own -- and then
            # the way round goes through the first again, which is not between.
            return any(_writes_unseen(one) for one in start.insts[at + 1:upto])
        reached: set[int] = set()
        waiting = list(successors(start))
        while waiting:
            one = waiting.pop()
            if id(one) not in reached:
                reached.add(id(one))
                waiting.extend(successors(one))
        preds = predecessors(func)
        reaching: set[int] = set()
        waiting = list(preds[id(end)])
        while waiting:
            one = waiting.pop()
            if id(one) not in reaching:
                reaching.add(id(one))
                waiting.extend(preds[id(one)])
        if any(_writes_unseen(one) for one in start.insts[at + 1:]) \
                or any(_writes_unseen(one) for one in end.insts[:upto]):
            return True
        return any(_writes_unseen(one) for block in func.blocks
                   if id(block) in reached and id(block) in reaching
                   for one in block.insts)

