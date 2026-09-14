"""Dead-code elimination.

A local variable is a value, so a local nothing refers to is an instruction
nothing uses.  Dropping it is therefore not a question about variables at all
but the ordinary one about instructions: if nothing uses what it computes and
computing it has no effect, it does not need to be there.

Asking it that way is what makes the rule hold once the language can keep a
reference to a local.  A reference will be a use like any other, so a local that
one points at stops being dead without this pass having to learn about it.

What survives with no user is what says it has an effect -- a write, a call, a
terminator.  Each instruction answers that for itself, so a shape added later
cannot be overlooked here.
"""

from __future__ import annotations

from ...ir.decisions import DecisionKind, DecisionLog
from ...ir.function import Function
from ...ir.inst import CallInst, Terminator
from ...ir.module import Module


class DeadCodeElimination:
    """Removes instructions that nothing uses and that do nothing."""

    name = "dce"

    def run(self, module: Module) -> bool:
        """Drop what is dead; report whether anything changed."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration:
                continue
            while self._sweep(func, module.decisions):
                changed = True
        return changed

    def _sweep(self, func: Function, decisions: DecisionLog) -> bool:
        """Drop what is dead now, and say whether anything went.

        Dropping an instruction can leave the ones it used with no user, so this
        runs until nothing more goes.  Each round walks the function again to
        find the uses; with a use list on every value it would be one walk and a
        worklist, and that is the change to make when functions are large enough
        for it to matter.

        What went is recorded where a reader could have asked about it.  A value
        with a name is a local the program wrote down.  A call is the other one:
        the program wrote the call, and a call not made is the largest thing
        this pass does -- what the program asked for was a function to run, and
        it does not run.  Everything else is an intermediate of an expression,
        which nothing in the program names.
        """
        used = self._used(func)
        removed = False
        for block in func.blocks:
            kept = [i for i in block.insts if i.has_effects or id(i) in used]
            if len(kept) == len(block.insts):
                continue
            surviving = {id(i) for i in kept}
            for inst in block.insts:
                if id(inst) in surviving:
                    continue
                if isinstance(inst, CallInst):
                    self._not_called(func, inst, decisions)
                elif inst.name_hint is not None:
                    decisions.record(
                        DecisionKind.DROP_LOCAL, inst.name_hint,
                        "".join(("nothing reads it, and computing it does nothing "
                                 "else, so it is not in ", func.name)),
                        inst.name_span if inst.name_span.is_valid
                        else inst.span)
            block.insts = kept
            removed = True
        return removed

    def _not_called(self, func: Function, inst: CallInst,
                    decisions: DecisionLog) -> None:
        """Record a call the program wrote and the program does not make.

        It is here because the callee said it changes nothing that outlives the
        call, so making it and not making it are the same thing to everything
        else -- and nothing reads what it answered.  That is a thing a reader is
        entitled to be told, both because the program wrote the call and because
        the attribute is what made it droppable: a reader checking whether
        `@[impure]` is missing from a function looks here.
        """
        name = getattr(inst.callee, "name", None)
        decisions.record(
            DecisionKind.DROP_CALL, name if name is not None else "a call",
            "".join(("nothing reads what it answers with and it changes nothing "
                     "that outlives the call, so ", func.name, " does not make "
                     "it")),
            inst.span)

    def _used(self, func: Function) -> set[int]:
        """The identities of the values something in *func* reads.

        A branch argument counts: it is what a block parameter is given, so a
        value reaching one is read even though no instruction names it.
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
