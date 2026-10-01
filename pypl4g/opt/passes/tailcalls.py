"""A function calling itself in tail position is a loop, and takes no stack.

The language promises it (see the specification, "Calling itself last"): a call of a
function to itself whose answer is what the function answers, with nothing left to do
after it, is a jump back to the function's start with the arguments as the new
parameters.  So it runs at every level, before anything else could move the call out
of tail position or the answer into the caller's storage.

**What a tail call looks like here** is what the checker writes for one: the call and
then the `ret` of what it answered, or the call and then a branch to a block that does
nothing but return what it is handed -- which is how the arm of an `if` or a `match`
hands back its value.  Anything between the call and the return -- a deferred
statement run on the way out, a post-condition checked, the answer used in a sum -- is
work that needs the frame afterwards, and the call is not in tail position.

**The loop** needs a block to jump to that is not the entry, since the entry is where
the frame is set up and a jump there would set it up again.  So a new entry is made that
only branches to the old one, whose parameters stop being the function's and become
the loop's: the first time round they are what the caller handed over, every time after
what the tail call handed over.  Where the function touches memory, the memory token
travels round the loop as one parameter more, as it does round any loop.

**A call that hands over a reference into the call's own storage is left alone**: the
loop reuses that storage, so what the reference names would be overwritten by the turn
it was handed to.  The report log says so, as it says every call it did turn into a
jump (`tail-call`), and every call of a function to itself that it could not
(`self-call`), with why.
"""

from __future__ import annotations

from ...ir.function import BasicBlock, Function
from ...ir.inst import (BlockTarget, BrInst, CallInst, FrameInst, Instruction,
                        MemStartInst, RetInst)
from ...ir.module import Module
from ...ir.reports import ReportKind
from ...ir.rewrite import all_stand_for
from ...ir.types import MEM, VOID
from ...ir.value import BlockParam, Value


class TailCalls:
    """Turns every call of a function to itself in tail position into a jump."""

    name = "tailcalls"

    def run(self, module: Module) -> bool:
        """Do it for every function that has such a call."""
        changed = False
        for func in list(module.functions.values()):
            if func.blocks:
                changed |= self._loop(module, func)
        return changed

    def _loop(self, module: Module, func: Function) -> bool:
        """Turn *func*'s tail calls to itself into jumps to a loop's head."""
        found: list[tuple[BasicBlock, CallInst]] = []
        for block in func.blocks:
            for at, inst in enumerate(block.insts):
                if not isinstance(inst, CallInst) or inst.callee is not func:
                    continue
                why = _why_not(func, block, at, inst)
                if why is None:
                    found.append((block, inst))
                else:
                    module.reports.record(
                        ReportKind.SELF_CALL, func.name,
                        "".join(("'", func.name, "' calls itself here and the "
                                 "call takes stack: ", why)),
                        inst.span if inst.span.is_valid else func.span)
        if not found:
            return False
        head = func.blocks[0]
        entry = BasicBlock(func._unique("entry"))  # noqa: SLF001
        entry.parent = func
        handed = [entry.add_param(param.ty, param.name_hint)
                  for param in head.params]
        for param, new in zip(head.params, handed):
            new.name_span = param.name_span
        made: list[Instruction] = []
        token: BlockParam | None = None
        start = next((inst for inst in head.insts
                      if isinstance(inst, MemStartInst)), None)
        if start is not None:
            # The memory goes round the loop, starting where it started.
            head.insts.remove(start)
            token = head.add_param(MEM, "mem")
            start.parent = entry
            made.append(start)
            all_stand_for(func, {id(start): token})
        args: list[Value] = [*handed, *( [start] if start is not None else [])]
        made.append(BrInst(BlockTarget(head, args)))
        for one in made:
            one.parent = entry
        entry.insts = made
        func.blocks.insert(0, entry)
        for block, call in found:
            at = block.insts.index(call)
            carried: list[Value] = list(call.operands)
            if token is not None:
                carried.append(_token_at(func, block, at, token))
            jump = BrInst(BlockTarget(head, carried), call.span)
            jump.parent = block
            block.insts[at:] = [jump]
            module.reports.record(
                ReportKind.TAIL_CALL, func.name,
                "".join(("'", func.name, "' calls itself last here: the call is a "
                         "jump back to its start, and takes no stack")),
                call.span if call.span.is_valid else func.span)
        return True


def _why_not(func: Function, block: BasicBlock, at: int,
             call: CallInst) -> str | None:
    """Why a call of *func* to itself is not in tail position, or nothing if it is."""
    rest = block.insts[at + 1:]
    if len(rest) != 1:
        return "something is done after it before the function returns"
    if not _returns(rest[0], call, func, set()):
        return "something is done with what it answers, or runs after it before "\
            "the function returns -- a deferred statement, a post-condition"
    if any(_reaches_frame(arg, set()) for arg in call.operands):
        return "it hands over a reference into the call's own storage, which the "\
            "next turn would reuse"
    return None


def _returns(terminator: Instruction, value: Value, func: Function,
             seen: set[int]) -> bool:
    """Whether *terminator* returns *value* from *func*, and does nothing else.

    Straight away, or through blocks that do nothing but hand on what they were
    handed and return it.
    """
    if isinstance(terminator, RetInst):
        if not terminator.operands:
            return value.ty is VOID
        return terminator.operands[0] is value
    if not isinstance(terminator, BrInst):
        return False
    target = terminator.target
    block = target.block
    if id(block) in seen or len(block.insts) != 1:
        return False
    seen.add(id(block))
    if value.ty is VOID:
        return _returns(block.insts[0], value, func, seen)
    for at, arg in enumerate(target.args):
        if arg is value and at < len(block.params) \
                and _returns(block.insts[0], block.params[at], func, seen):
            return True
    return False


def _reaches_frame(value: Value, seen: set[int]) -> bool:
    """Whether *value* may be, or be made from, an address of the call's storage."""
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, FrameInst):
        return True
    if isinstance(value, BlockParam):
        return False
    return any(_reaches_frame(one, seen)
               for one in getattr(value, "operands", ()))


def _token_at(func: Function, block: BasicBlock, at: int,
              head_token: BlockParam) -> Value:
    """The memory token in force just before instruction *at* of *block*."""
    seen: set[int] = set()
    while True:
        for inst in reversed(block.insts[:at]):
            if inst.ty is MEM:
                return inst
        for param in reversed(block.params):
            if param.ty is MEM:
                return param
        if block is func.blocks[1] or id(block) in seen:
            return head_token
        seen.add(id(block))
        # One token reaches a block without a parameter for it, whichever way
        # it came: two that differed would have needed one.
        before = next((other for other in func.blocks
                       if any(target.block is block
                              for target in (other.insts[-1].successors()
                                             if other.insts and hasattr(
                                                 other.insts[-1], "successors")
                                             else ()))), None)
        if before is None:
            return head_token
        block, at = before, len(before.insts) - 1
