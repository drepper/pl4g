"""A call whose answer the program said it did not want.

`_ \N{LEFTWARDS ARROW} f()` is the program saying that what `f` answers with is not wanted, and
a function that says nothing about changing what outlives it changes nothing; so
there is nothing left for the call to do and the program does not make it.

That is not an optimization and so does not wait for one to be asked for, which
is the rule the reachability pass already follows: what it acts on is not
something the compiler noticed about the program but something the program
said.  A reader who writes `_ \N{LEFTWARDS ARROW}` and then asks what the program does should be
told the same thing at every optimization level, since what they wrote means the
same thing at every one.

The rest of what is dead waits for `-O1` as it did: a local nothing reads is the
compiler noticing, and an unoptimized build keeps what the program wrote.
"""

from __future__ import annotations

from ...ir.decisions import DecisionKind, DecisionLog
from ...ir.function import Function
from ...ir.inst import CallInst, Terminator
from ...ir.module import Module


class DropIgnoredCalls:
    """Removes calls that answer with what nothing reads and do nothing else."""

    name = "dropignored"

    def run(self, module: Module) -> bool:
        """Drop them; report whether any went."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration:
                continue
            while self._sweep(func, module.decisions):
                changed = True
        return changed

    def _sweep(self, func: Function, decisions: DecisionLog) -> bool:
        """Drop the ones dead now, and say whether any went.

        Again until none does: a call may be what read another one's answer, so
        the first to go can be what makes the second unread.
        """
        used = _read_in(func)
        gone = False
        for block in func.blocks:
            kept = [inst for inst in block.insts
                    if not isinstance(inst, CallInst) or inst.has_effects
                    or id(inst) in used]
            if len(kept) == len(block.insts):
                continue
            surviving = {id(inst) for inst in kept}
            for inst in block.insts:
                if id(inst) not in surviving:
                    _record(func, inst, decisions)
            block.insts = kept
            gone = True
        return gone


def _record(func: Function, inst: CallInst, decisions: DecisionLog) -> None:
    """Say which call went and why, the why being a thing the reader can change."""
    name = getattr(inst.callee, "name", None)
    decisions.record(
        DecisionKind.DROP_CALL, name if name is not None else "a call",
        "".join(("nothing reads what it answers with and it changes nothing "
                 "that outlives the call, so ", func.name, " does not make it")),
        inst.span)


def _read_in(func: Function) -> set[int]:
    """The identities of the values something in *func* reads.

    A branch argument counts, being what a block parameter is given.
    """
    used: set[int] = set()
    for block in func.blocks:
        for inst in block.insts:
            for operand in inst.operands:
                used.add(id(operand))
            if isinstance(inst, Terminator):
                for target in inst.successors():
                    for arg in target.args:
                        used.add(id(arg))
    return used
