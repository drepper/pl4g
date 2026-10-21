"""Putting a callee's body where the call was.

The order is what makes this cheap.  Functions are walked callees first -- the
order the call graph gives, the same one the backend generates in -- so a callee
reached here has already had everything inlined into it that is going to be: its
size is final, what it calls is final, and both are what the decision rests on.
Nothing is ever looked at twice, and nothing round a cycle is looked at at all.

**What is inlined.**  A function the program said to inline, whatever its size;
a function the whole program calls once, since the copy is then the only one
there is and what is left behind is dropped; and a function small enough that a
copy of it costs less than the call would, a leaf -- one that calls nothing --
being allowed twice the size.  A caller may grow only so much, and what counts
against that is what makes the program larger: neither the copy of a function
called once nor one no larger than the call.  What is never inlined is a function
that can reach itself, one that follows another convention -- the call is the
convention, and inlining it would answer a question nobody asked -- and one the
program marked `@[inline(never)]`.

**What it leaves behind** is a function nothing calls any more, which the pass
that drops what nothing reaches removes.  That is why inlining a function called
once is free: the call goes, the copy stands where it was, and the original goes
with the next pass.

**How a body is put in place.**  The block holding the call is cut in two: what
follows the call becomes a block of its own, taking what the call answered with
as a parameter, and the first half branches into a copy of the callee with the
arguments as the copy's parameters.  Every `ret` in the copy becomes a branch to
the second half.  The callee's `mem.start` is the token in force at the call --
a chain begun again in the middle of a function would be a chain saying nothing
about what came before it.

**The chain runs through the copy and out of it.**  The second half takes a
token as well as the answer, each `ret` handing over the token it stood at, and
everything the second half reaches -- what it dominates -- reads that token
where it read the one in force at the call.  So a read after the body is ordered
after what the body wrote, by the chain and not by the hope that nothing moves
it; which is what lets `samevalues` take a store for what it is.
"""

from __future__ import annotations

from copy import copy
from typing import Final

from ...ir.callgraph import (CALL_COST, called_by_count, cost_of_body,
                             in_a_cycle, in_call_order, is_leaf)
from ...ir.function import (BasicBlock, DEFAULT_CCONV, Function, InlineHint,
                            Linkage)
from ...ir.inst import (BlockTarget, BrInst, CallInst, CondBrInst, Instruction,
                        MemStartInst, RetInst, SwitchInst)
from ...ir.module import Module
from ...ir.reports import ReportKind
from ...ir.rewrite import stands_for
from ...ir.types import MEM, VOID
from ...ir.value import BlockParam, Value
from ..flow import Dominators, dominators

#: What a function may come to where it is generated -- `cost_of_body`, in
#: machine instructions -- and still be copied where it is called.  A handful:
#: the point is the call that goes, and a body much longer than the sequence a
#: call is made of pays for itself only where the copy opens something further
#: up, which nothing here looks for.  Chosen over the language tests: against
#: the twelve instructions of the representation this replaced, it leaves both
#: less code and fewer calls, and a larger one buys calls with code.
SMALL: Final[int] = 16

#: The same for a leaf -- a function that calls nothing -- which may be half as
#: large again and more.  What a call costs a leaf's caller is more than the
#: call: every register the convention lets a callee destroy is one the caller
#: cannot keep a value in across it, and a leaf copied in destroys only what it
#: uses.  `to_odd` of `std.string`, two wide multiplications and a carry, is the
#: size this is for.
SMALL_LEAF: Final[int] = 32

#: And how much a caller may grow in total, in the same measure, so that a
#: function calling many small ones does not become one enormous one.  What a
#: copy of a function called once comes to is not counted, nor that of one no
#: larger than the call: neither makes the program any larger.
BUDGET: Final[int] = 300


class Inlining:
    """Copies small callees into their callers, callees first."""

    name = "inline"

    def run(self, module: Module) -> bool:
        """Inline what is worth inlining; report whether anything changed."""
        cyclic = in_a_cycle(module)
        counted = called_by_count(module)
        self.touched: list[Function] = []
        for func in in_call_order(module):
            if not func.blocks or id(func) in cyclic:
                continue
            if self._into(module, func, cyclic, counted):
                self.touched.append(func)
        return bool(self.touched)

    def _into(self, module: Module, caller: Function, cyclic: frozenset[int],
              counted: dict[int, int]) -> bool:
        """Inline into one caller, until nothing worth inlining is left.

        The walk is over the blocks as they stand, and a body that is put in
        place adds blocks after them -- so what was inlined is never walked
        again, which is what keeps this finite without counting depth.
        """
        grown = 0
        changed = False
        at_block = 0
        while at_block < len(caller.blocks):
            block = caller.blocks[at_block]
            at = 0
            while at < len(block.insts):
                inst = block.insts[at]
                callee = getattr(inst, "callee", None)
                if not isinstance(inst, CallInst) \
                        or not isinstance(callee, Function) \
                        or not self._worth_it(caller, callee, cyclic, counted,
                                              grown):
                    at += 1
                    continue
                grown += self._growth(callee, counted)
                module.reports.record(
                    ReportKind.INLINE, callee.name,
                    "".join(("put where ", caller.name, " calls it, ",
                             self._because(callee, counted))),
                    _where(callee))
                self._splice(caller, block, at, callee)
                changed = True
                # The block is now what stood before the call; what followed it
                # is a block of its own, further down the list.
                break
            else:
                at_block += 1
                continue
            at_block += 1
        return changed

    def _worth_it(self, caller: Function, callee: Function,
                  cyclic: frozenset[int], counted: dict[int, int],
                  grown: int) -> bool:
        """Whether this call is one to put the callee's body in place of."""
        if not callee.blocks or callee is caller or id(callee) in cyclic:
            return False
        if callee.cconv != DEFAULT_CCONV or callee.attrs.special is not None:
            # A convention is a promise to whoever calls, and a function the
            # entry point or the testing machinery calls by name has to be
            # there to be called.
            return False
        if callee.attrs.inline is InlineHint.NEVER or callee.attrs.opaque:
            # What the program said -- `@[opaque]` saying it too, a call of one
            # being a black box -- whatever the size or the count says.
            return False
        if callee.attrs.inline is InlineHint.ALWAYS:
            return True
        # Called once and named by nothing else: the copy is the only one there
        # is, so what this costs is nothing and what it saves is the call --
        # which is why the budget does not stand in its way either.
        if _called_once(callee, counted):
            return True
        cost = cost_of_body(callee)
        if cost <= CALL_COST:
            # No larger than the call it replaces, so it costs nothing however
            # much the caller has grown.
            return True
        if grown >= BUDGET:
            return False
        return cost <= _limit(callee)

    def _growth(self, callee: Function, counted: dict[int, int]) -> int:
        """What a copy of *callee* adds to the program, in the budget's measure:
        nothing where the original goes or the copy is no larger than a call."""
        cost = cost_of_body(callee)
        if callee.attrs.inline is not InlineHint.ALWAYS \
                and (_called_once(callee, counted) or cost <= CALL_COST):
            return 0
        return cost

    def _because(self, callee: Function, counted: dict[int, int]) -> str:
        """Why it was inlined, for the log."""
        if callee.attrs.inline is InlineHint.ALWAYS:
            return "which the program asked for"
        if cost_of_body(callee) <= _limit(callee):
            return "".join(("coming to about ", str(cost_of_body(callee)),
                            " machine instructions"))
        return "the whole program calling it once"

    def _splice(self, caller: Function, block: BasicBlock, at: int,
                callee: Function) -> None:
        """Put a copy of *callee* where the call at *at* stands."""
        call = block.insts[at]
        assert isinstance(call, CallInst)
        # The token first, and then where the call is again: finding the token
        # may put the start of a chain at the top of the entry block, which
        # moves everything below it along.
        token = _token_before(caller, block, at)
        at = block.insts.index(call)
        # What follows the call, in a block of its own that takes what the call
        # answered with.
        after = caller.add_block("inlined.after")
        after.insts = block.insts[at + 1:]
        for one in after.insts:
            one.parent = after
        del block.insts[at:]
        answer = (after.add_param(call.ty, "answer") if call.ty is not VOID
                  else None)
        # And the token the body ends at, which what follows the call reads.
        resumed = after.add_param(MEM, "memory")

        # The copy: one block per block of the callee, its parameters carried
        # over so that the entry block's are where the arguments arrive.
        blocks: dict[int, BasicBlock] = {}
        values: dict[int, Value] = {}
        # Who dominates whom in the callee, for the token a `ret` stands at: a
        # block that says nothing of the chain is on the one its dominator was.
        tree = dominators(callee)
        for original in callee.blocks:
            made = caller.add_block("".join(("inlined.", original.label)))
            blocks[id(original)] = made
            for param in original.params:
                values[id(param)] = made.add_param(param.ty, param.name_hint)
        # The instructions first and what they name afterwards: a block may pass
        # a value defined in a block that stands after it in the list -- the
        # list is a layout and not an order of definitions -- so nothing can be
        # mapped until every copy exists.
        copies: list[Instruction] = []
        for original in callee.blocks:
            made = blocks[id(original)]
            for one in original.insts:
                if isinstance(one, MemStartInst):
                    # The chain the callee began is the chain the caller is on.
                    values[id(one)] = token
                    continue
                if isinstance(one, RetInst):
                    # What it answered with is carried to what follows the call,
                    # and the token this way through the body ended at.
                    ended = _last_token(original, original.insts.index(one), tree)
                    made.append(BrInst(
                        BlockTarget(after,
                                    (ended if ended is not None else token,)
                                    if answer is None
                                    else (one.operands[0],
                                          ended if ended is not None else token)),
                        one.span))
                    continue
                clone = copy(one)
                clone.parent = None
                values[id(one)] = clone
                made.append(clone)
                copies.append(clone)
        for clone in copies:
            _map_into(clone, values, blocks)
        for original in callee.blocks:
            last = blocks[id(original)].insts[-1]
            if isinstance(last, BrInst) and last.target.block is after:
                last.target.args = [_mapped(one, values)
                                    for one in last.target.args]
        # Into the copy, with the arguments where its parameters are.
        block.append(BrInst(
            BlockTarget(blocks[id(callee.blocks[0])],
                        [_mapped(one, values) for one in call.operands]),
            call.span))
        if answer is not None:
            stands_for(caller, call, answer)
        # And where the copy goes in the list: straight after the block that
        # branches into it, with what follows the call last of them.  The order
        # of the blocks is the order the backend walks them in, so a value has
        # to be computed in a block that stands before the one that reads it --
        # which a copy appended at the end would not be.
        made = [blocks[id(one)] for one in callee.blocks] + [after]
        fresh = {id(one) for one in made}
        rest = [one for one in caller.blocks if id(one) not in fresh]
        position = next(at for at, one in enumerate(rest)
                        if one is block) + 1
        caller.blocks = [*rest[:position], *made, *rest[position:]]
        # Where every way out of the body hands on one token -- a body that wrote
        # nothing hands on the one it was given -- that token is what follows
        # the call reads, and a parameter for it would only stand between two
        # reads of one place that are one read.
        endings = [block.insts[-1].target for block in made[:-1]
                   if isinstance(block.insts[-1], BrInst)
                   and block.insts[-1].target.block is after]
        handed = {id(target.args[-1]) for target in endings}
        if len(handed) == 1:
            one = endings[0].args[-1]
            after.params.remove(resumed)
            for target in endings:
                target.args = list(target.args[:-1])
            if one is not token:
                _resume_chain(caller, after, token, one)
        else:
            _resume_chain(caller, after, token, resumed)


def _called_once(callee: Function, counted: dict[int, int]) -> bool:
    """Whether *callee* is named by one call of the whole program and can be
    reached from nowhere outside it."""
    return (counted.get(id(callee), 0) == 1
            and callee.linkage is not Linkage.VISIBLE)


def _limit(callee: Function) -> int:
    """How large *callee* may be and still be small: more for a leaf."""
    return SMALL_LEAF if is_leaf(callee) else SMALL


def _map_into(made: Instruction, values: dict[int, Value],
              blocks: dict[int, BasicBlock]) -> None:
    """Point one copied instruction at the copies rather than the originals.

    Every instruction is its class, its operands and whatever else it carries,
    and what has to change is the two that name things of the function it was
    in: the values it reads and the blocks it goes to.
    """
    made.operands = [_mapped(one, values) for one in made.operands]
    if isinstance(made, BrInst):
        made.target = _to(made.target, values, blocks)
    elif isinstance(made, CondBrInst):
        made.true_target = _to(made.true_target, values, blocks)
        made.false_target = _to(made.false_target, values, blocks)
    elif isinstance(made, SwitchInst):
        made.cases = [(number, _to(target, values, blocks))
                      for number, target in made.cases]
        made.default = _to(made.default, values, blocks)


def _to(target: BlockTarget, values: dict[int, Value],
        blocks: dict[int, BasicBlock]) -> BlockTarget:
    """One branch destination, as a destination of the copy."""
    return BlockTarget(blocks[id(target.block)],
                       [_mapped(one, values) for one in target.args])


def _mapped(value: Value, values: dict[int, Value]) -> Value:
    """What a value of the callee is in the copy.

    A constant is itself: it belongs to the module rather than to the function,
    so there is nothing to copy and nothing to map.
    """
    return values.get(id(value), value)


def _last_token(block: BasicBlock, at: int,
                tree: Dominators | None = None) -> Value | None:
    """The token in force just before instruction *at* of *block*: the nearest
    memory-valued instruction above, or the parameter it took one in -- or,
    where the block says neither, the token its immediate dominator ends on,
    and so up the tree.  A block that takes no token is reached only on that
    one chain: a write on any way into it would have needed a parameter where
    the ways met.  Nothing where the token comes from the function's start.

    The tree is what a body that had a call inlined into it already needs: the
    block holding its `ret` is what followed that call, and the token it is on
    is the one the inlined body ended at, written in a block above it.
    """
    current, end = block, at
    while True:
        for earlier in reversed(current.insts[:end]):
            if earlier.ty is MEM:
                return earlier
        for param in current.params:
            if param.ty is MEM:
                return param
        above = tree.idom.get(id(current)) if tree is not None else None
        if above is None:
            return None
        current, end = above, len(above.insts)


def _resume_chain(func: Function, after: BasicBlock, token: Value,
                  resumed: Value) -> None:
    """Have what follows an inlined body read the token the body ended at.

    Every block *after* dominates read *token* -- the one in force at the call
    -- where it read the chain at all; each reads *resumed* now, which is in
    view there because *after* takes it.  A block *after* does not dominate is
    left alone: control reaches it some other way too, and what it reads is
    already handed to it by a branch.
    """
    found = dominators(func)
    for block in func.blocks:
        if not found.dominates(after, block):
            continue
        for inst in block.insts:
            inst.operands = [resumed if one is token else one
                             for one in inst.operands]
            for target in (inst.successors() if hasattr(inst, "successors") else ()):
                target.args = [resumed if one is token else one
                               for one in target.args]


def _token_before(func: Function, block: BasicBlock, at: int) -> Value:
    """The memory token in force just before instruction *at* of *block*.

    What the callee's chain is joined to, so that what it writes is ordered
    after what the caller wrote before the call.  The nearest memory-valued
    instruction above, or the parameter the block took it in, or the start of a
    chain put where every chain starts.
    """
    # Up the dominator tree where the block says nothing, as `_last_token` goes:
    # a block that takes no token is on the chain of the one above it, which is
    # what a block standing after an inlined body is.
    found = _last_token(block, at, dominators(func))
    if found is not None:
        return found
    entry = func.blocks[0]
    if entry.insts and isinstance(entry.insts[0], MemStartInst):
        return entry.insts[0]
    start = MemStartInst()
    start.parent = entry
    entry.insts.insert(0, start)
    return start


def _where(func: Function) -> object:
    """Where a function is, for the log."""
    return func.name_span if func.name_span.is_valid else func.span
