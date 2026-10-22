"""Branching on each part of a condition rather than on the truth value they make.

`if a ∧ b:` asks two questions, and a machine asks a question by comparing and
branching.  What the checker writes for it is one of two shapes, neither of which
is that: where `b` costs nothing to work out and cannot stop the program, the two
truth values made and joined with `and.bool`, and the branch on what that came
to; and where `b` has to wait for `a` -- an index it reads is only good where `a`
said so -- a block that works `b` out, a block that answers `false` without
it, and a block where the two meet taking the answer as a parameter and branching
on it.  Both make a truth value that nothing wants but the branch.  On every one
of the three machines that is a comparison set into a register, the register
tested, and only then the branch -- three instructions or four where the
comparison and the branch would have been one or two.

So this pass gives the branch the parts.  Two rewrites, taken until neither
applies:

- **A branch on a truth value made with `∧`, `∨` or `¬`**, and read by nothing
  else, becomes a branch on its first operand into a block of its own that
  branches on the second: for `∧` the first being false goes where the whole
  being false went, and for `∨` the first being true goes where its being true
  went; `¬` is the two ways swapped.  The second operand's own working out -- a
  comparison, the arithmetic it compares -- moves into that block where nothing
  else reads it, so that it is done only where it is asked.  A load is never
  moved: what it reads is ordered by the memory token and not by where it
  stands, and moving it past a write would read what the write left.
- **A block that is nothing but a branch on its own parameter** -- the block
  where the two ways of a short-circuit meet -- is gone through rather than to:
  a jump into it carrying a value becomes a branch on that value, and one
  carrying a constant becomes the jump the branch would take.  A block that is
  nothing but a jump is then gone through the same way, which is what the arm
  answering `false` comes to once its jump goes straight where `false` goes.

What is left behind is blocks nothing reaches, which are taken out at the end.  It runs before the pass that uses what a branch settled, which then
finds `at < n` on the edge into the block that reads `bytes⟦at⟧` -- where before
it found a truth value made of two comparisons and knew nothing.

Compare: this is what GCC's `fold_truth_andor` and the lowering of `&&` in every
C compiler since the first does at the front, and what LLVM's
`SimplifyCFG` (`FoldBranchToCommonDest` the other way round) and its jump
threading do to a `phi` of truth values a branch tests.  Go, Rust and Zig
inherit it from the back ends they use.  It belongs here rather than in the
selectors because the three of them would each have to recognise both shapes,
and in the representation it is one rewrite of blocks the rest of the passes
already know how to read.
"""

from __future__ import annotations

from ...ir.function import BasicBlock, Function
from ...ir.inst import (BinaryInst, BinOp, BlockTarget, BrInst, CondBrInst,
                        Instruction, LoadInst, Terminator, UnaryInst, UnOp)
from ...ir.module import Module
from ...ir.rewrite import carried_values, incoming_edges
from ...ir.types import BOOL, MEM
from ...ir.value import BlockParam, BoolConst, Value

#: How many times the two rewrites are taken in turn over one function at most.
#: Each that changes anything takes an operation or a block out of a branch's
#: way, so a function settles long before this.
ROUNDS = 100


class BranchConditions:
    """Branches on the parts of a condition, and through the blocks that only
    pass a truth value on to a branch."""

    name = "conditions"

    def run(self, module: Module) -> bool:
        """Rewrite every function; report whether anything changed."""
        self.touched: list[Function] = []
        for func in module.functions.values():
            if func.is_declaration:
                continue
            changed = False
            for _ in range(ROUNDS):
                round_ = _thread(func) | _split(func)
                changed |= round_
                if not round_:
                    break
            if changed:
                _prune(func)
                self.touched.append(func)
        return bool(self.touched)


def _prune(func: Function) -> None:
    """Take out the blocks nothing reaches any more: the ones gone through.

    Here and not left for simplifying the graph, since a block nothing reaches
    still reads what it read, and what it read may now be defined nowhere on
    any way to it.
    """
    entry = func.entry
    if entry is None:
        return
    seen: set[int] = {id(entry)}
    queue: list[BasicBlock] = [entry]
    while queue:
        terminator = queue.pop().terminator
        if terminator is None:
            continue
        for target in terminator.successors():
            if isinstance(target.block, BasicBlock) and id(target.block) not in seen:
                seen.add(id(target.block))
                queue.append(target.block)
    func.blocks = [block for block in func.blocks if id(block) in seen]


def _uses(func: Function) -> dict[int, int]:
    """How many times each value is read, a branch's arguments counted."""
    counted: dict[int, int] = {}
    for block in func.blocks:
        for inst in block.insts:
            for operand in inst.operands:
                counted[id(operand)] = counted.get(id(operand), 0) + 1
            if isinstance(inst, Terminator):
                for target in inst.successors():
                    for arg in target.args:
                        counted[id(arg)] = counted.get(id(arg), 0) + 1
    return counted


def _parts(cond: Value) -> tuple[str, Value, Value | None] | None:
    """What a truth value is made of, where it is made with `∧`, `∨` or `¬`:
    which, and its operands."""
    if isinstance(cond, BinaryInst) and cond.ty is BOOL:
        lhs, rhs = cond.operands
        if cond.op is BinOp.AND:
            return "and", lhs, rhs
        if cond.op is BinOp.OR:
            return "or", lhs, rhs
        if cond.op is BinOp.XOR and isinstance(rhs, BoolConst) and rhs.value:
            return "not", lhs, None
        if cond.op is BinOp.XOR and isinstance(lhs, BoolConst) and lhs.value:
            return "not", rhs, None
    if isinstance(cond, UnaryInst) and cond.op is UnOp.NOT and cond.ty is BOOL:
        return "not", cond.operands[0], None
    return None


def _copy(target: BlockTarget) -> BlockTarget:
    """Another branch to where *target* goes, carrying what it carries."""
    return BlockTarget(target.block, list(target.args))


def _split(func: Function) -> bool:
    """Branch on the parts of every condition made with `∧`, `∨` or `¬`."""
    changed = False
    counted = _uses(func)
    at = 0
    while at < len(func.blocks):
        block = func.blocks[at]
        branch = block.terminator
        at += 1
        if not isinstance(branch, CondBrInst):
            continue
        cond = branch.operands[0]
        made = _parts(cond)
        if made is None or not isinstance(cond, Instruction) \
                or cond.parent is not block or counted.get(id(cond), 0) != 1:
            continue
        kind, first, second = made
        block.insts.remove(cond)
        changed = True
        # A constant operand of `¬` written as an exclusive or is read no more.
        for operand in cond.operands:
            if operand is not first and operand is not second:
                counted[id(operand)] = counted.get(id(operand), 0) - 1
        branch.operands[0] = first
        if kind == "not":
            branch.true_target, branch.false_target = (branch.false_target,
                                                       branch.true_target)
            # The same block again: what it branches on now may be made of parts.
            at -= 1
            continue
        assert second is not None
        rest = func.add_block("".join((block.label, ".then" if kind == "and"
                                       else ".else")))
        func.blocks.remove(rest)
        func.blocks.insert(at, rest)
        # The way both branches share is carried by both, so what it carries is
        # read once more.
        shared = branch.false_target if kind == "and" else branch.true_target
        for arg in shared.args:
            counted[id(arg)] = counted.get(id(arg), 0) + 1
        rest.append(CondBrInst(second, _copy(branch.true_target),
                               _copy(branch.false_target), branch.span))
        if kind == "and":
            branch.true_target = BlockTarget(rest, [])
        else:
            branch.false_target = BlockTarget(rest, [])
        _sink(block, rest, second, counted)
        # This block again, and the new one after it: either may branch on a
        # value made of further parts.
        at -= 1
    return changed


def _sink(block: BasicBlock, into: BasicBlock, value: Value,
          counted: dict[int, int]) -> None:
    """Move what works *value* out from *block* into *into*, where nothing else
    reads it: the instructions it is made from, in the order they stood.

    Only what has no effect and touches no memory, and only what *value* alone
    reads -- an operand something else in *block* also wants has to stay where
    that something can see it.
    """
    moving: set[int] = set()
    pending: list[Value] = [value]
    while pending:
        one = pending.pop()
        if not isinstance(one, Instruction) or one.parent is not block \
                or id(one) in moving or not _movable(one):
            continue
        # Read once, and that once by what is moving: the condition itself is
        # read by the branch that now stands in *into* and by nothing else.
        if counted.get(id(one), 0) != 1:
            continue
        moving.add(id(one))
        pending.extend(one.operands)
    if not moving:
        return
    kept: list[Instruction] = []
    moved: list[Instruction] = []
    for inst in block.insts:
        (moved if id(inst) in moving else kept).append(inst)
    block.insts = kept
    for inst in moved:
        inst.parent = into
    into.insts[0:0] = moved


def _movable(inst: Instruction) -> bool:
    """Whether *inst* may be worked out later than where it stands."""
    if inst.has_effects or isinstance(inst, (LoadInst, Terminator)):
        return False
    return not any(operand.ty is MEM for operand in inst.operands) \
        and inst.ty is not MEM


class _Ways:
    """What `_thread` asks of a function as it changes it: how often each value
    is read, and every branch arriving at each block.

    Worked out once per walk and kept up as the walk changes branches, rather
    than worked out again over the whole function for every block it looks at
    -- which, over a function that inlining had made a few thousand blocks
    long, was most of what compiling it cost.  Both are what `_uses` and
    `incoming_edges` would answer of the function as it stands: a block gone
    through stays in the function until the end and goes on reading what it
    read, and a way into a block is asked of the branch that is there now.
    """

    __slots__ = ("counted", "arriving")

    def __init__(self, func: Function) -> None:
        self.counted = _uses(func)
        self.arriving = incoming_edges(func)

    def read(self, values: list[Value], by: int) -> None:
        """Count each of *values* read *by* times more."""
        counted = self.counted
        for one in values:
            counted[id(one)] = counted.get(id(one), 0) + by

    def into(self, block: BasicBlock) -> list[tuple[BasicBlock, BlockTarget]]:
        """Every branch arriving at *block* now, and the block it is from: each
        once, and only where it still goes there and is still its block's."""
        found: list[tuple[BasicBlock, BlockTarget]] = []
        seen: set[int] = set()
        for source, edge in self.arriving.get(id(block), ()):
            if edge.block is not block or id(edge) in seen:
                continue
            terminator = source.terminator
            if terminator is None \
                    or not any(one is edge for one in terminator.successors()):
                continue
            seen.add(id(edge))
            found.append((source, edge))
        self.arriving[id(block)] = list(found)
        return found

    def arrives(self, source: BasicBlock, edge: BlockTarget) -> None:
        """Note that *edge*, of *source*'s branch, goes where it now says."""
        if isinstance(edge.block, BasicBlock):
            self.arriving.setdefault(id(edge.block), []).append((source, edge))

    def moved(self, source: BasicBlock, edge: BlockTarget, block: object,
              args: list[Value]) -> None:
        """Send *edge* to *block* with *args* instead of where it went."""
        self.read(edge.args, -1)
        edge.block, edge.args = block, args
        self.read(args, 1)
        self.arrives(source, edge)


def _thread(func: Function) -> bool:
    """Go through every block that only branches on its parameter, or only
    jumps, rather than into it."""
    changed = False
    ways = _Ways(func)
    for block in list(func.blocks):
        if block is func.entry or len(block.insts) != 1:
            continue
        only = block.insts[0]
        if isinstance(only, CondBrInst):
            changed |= _through_branch(block, only, ways)
        elif isinstance(only, BrInst) and not block.params:
            changed |= _through_jump(block, only, ways)
    return changed


def _substituted(target: BlockTarget, block: BasicBlock,
                 args: list[Value]) -> BlockTarget:
    """A branch to where *target* goes, its parameters of *block* read as
    *args* -- what a jump into *block* carried."""
    given = {id(param): arg for param, arg in zip(block.params, args)}
    return BlockTarget(target.block, [given.get(id(arg), arg)
                                      for arg in target.args])


def _through_branch(block: BasicBlock, branch: CondBrInst, ways: _Ways) -> bool:
    """Have every jump into *block* -- nothing but a branch on one of its
    parameters -- branch itself, and every branch into it carrying a constant
    go where that constant goes."""
    cond = branch.operands[0]
    if not isinstance(cond, BlockParam) or cond.block is not block \
            or cond.ty is not BOOL:
        return False
    # Its parameters are read by its branch and nowhere else, so that once
    # nothing arrives here nothing is left reading them.
    counted = ways.counted
    inside: dict[int, int] = {}
    for one in (branch.operands[0], *branch.true_target.args,
                *branch.false_target.args):
        inside[id(one)] = inside.get(id(one), 0) + 1
    if any(counted.get(id(param), 0) != inside.get(id(param), 0)
           for param in block.params):
        return False
    # A loop of one would be threaded into itself.
    if branch.true_target.block is block or branch.false_target.block is block:
        return False
    changed = False
    for source, edge in ways.into(block):
        known = edge.args[cond.index]
        last = source.insts[-1]
        if isinstance(known, BoolConst):
            chosen = branch.true_target if known.value else branch.false_target
            made = _substituted(chosen, block, list(edge.args))
            ways.moved(source, edge, made.block, made.args)
            changed = True
            continue
        if not isinstance(last, BrInst) or last.target is not edge:
            continue
        args = list(edge.args)
        replaced = CondBrInst(known,
                              _substituted(branch.true_target, block, args),
                              _substituted(branch.false_target, block, args),
                              last.span)
        replaced.parent = source
        source.insts[-1] = replaced
        ways.read(edge.args, -1)
        ways.read([known, *replaced.true_target.args, *replaced.false_target.args], 1)
        ways.arrives(source, replaced.true_target)
        ways.arrives(source, replaced.false_target)
        changed = True
    return changed


def _through_jump(block: BasicBlock, jump: BrInst, ways: _Ways) -> bool:
    """Have every branch into *block* -- nothing but a jump, taking nothing --
    go where the jump goes instead."""
    if jump.target.block is block:
        return False
    # A branch of two ways carrying values would only be given a block of its
    # own again, to make the moves in: that is this block.
    carries = bool(carried_values(jump.target))
    changed = False
    for source, edge in ways.into(block):
        if carries and not isinstance(source.insts[-1], BrInst):
            continue
        ways.moved(source, edge, jump.target.block, list(jump.target.args))
        changed = True
    return changed
