"""What every caller hands a function, written into the function.

The compilation is the whole program, so a function nothing outside it can name
-- internal, called and never taken the address of, and not where the program
starts -- has every one of its callers in view.  Where every one of them hands a
parameter the same constant, the parameter *is* that constant, and the body may
be worked out with it: the branches it settles fold, and what they reached may be
dropped.

**A part of a parameter counts as much as the whole.**  A value that travels as
several -- an array whose type does not say its shape, which is where its first
element is, how many there are and how far apart -- is handed over as the tuple
its parts make, and the part that is the same at every call is known even where
the place is not.  That is what makes a view's stride, which is one wherever the
view is a run of elements, one inside a function every caller hands runs to: the
question whether it is a run is answered while compiling, and the copy that would
make it one is never reached.

**A caller's own parameter counts too**, where what is known of it is known: the
facts are worked out over every function in turn until none changes, so a run
handed down through two calls is a run at the bottom of both.
"""

from __future__ import annotations

from typing import Final

from ...ir.function import Function, Linkage
from ...ir.inst import CallInst, ExtractInst, TupleInst
from ...ir.module import Module
from ...ir.rewrite import all_stand_for
from ...ir.value import BlockParam, BoolConst, IntConst, Value
from .dropunreached import DropUnreached

#: How many times every function is looked at at most.  Each round carries what
#: is known one call further down, and call chains that deep are not written.
ROUNDS: Final = 8

#: Which whole parameter, or which part of one, a fact is about: the part's
#: place among the parameter's parts, or -1 for the whole of it.
_WHOLE: Final = -1

type _Fact = dict[tuple[int, int, int], Value]


class KnownArguments:
    """Writes into a function the constants every one of its callers hands it."""

    name = "knownargs"

    def run(self, module: Module) -> bool:
        """Work out what is known of every parameter; report whether anything
        in a body changed for it."""
        calls, closed = self._callers(module)
        facts: _Fact = {}
        for _ in range(ROUNDS):
            grew = False
            for func in closed:
                grew |= self._learn(func, calls[id(func)], facts)
            if not grew:
                break
        changed = False
        for func in closed:
            changed |= self._apply(func, facts)
        return changed

    def _callers(self, module: Module
                 ) -> tuple[dict[int, list[tuple[Function, CallInst]]],
                            list[Function]]:
        """Every direct call of every function, and the functions all of whose
        callers those are."""
        calls: dict[int, list[tuple[Function, CallInst]]] = {}
        taken: set[int] = set()
        for func in module.functions.values():
            for block in func.blocks:
                for inst in block.insts:
                    for named in inst.references():
                        if not isinstance(named, Function):
                            continue
                        if isinstance(inst, CallInst) and inst.callee is named:
                            calls.setdefault(id(named), []).append((func, inst))
                        else:
                            # Named as a value -- a lambda's code, a function
                            # handed over -- and so called from where nothing
                            # here can see.
                            taken.add(id(named))
        roots = {id(one) for one in DropUnreached()._roots(module)}
        closed = [func for func in module.functions.values()
                  if not func.is_declaration and func.entry is not None
                  and func.linkage is Linkage.INTERNAL
                  and id(func) not in roots and id(func) not in taken
                  and id(func) in calls]
        return calls, closed

    def _learn(self, func: Function, calls: list[tuple[Function, CallInst]],
               facts: _Fact) -> bool:
        """Add what every call of *func* hands it alike; answer whether anything
        new was learned."""
        assert func.entry is not None
        params = func.entry.params
        if any(len(call.arguments) != len(params) for _, call in calls):
            return False
        grew = False
        for at, param in enumerate(params):
            for part in (_WHOLE, *range(_parts(calls, at))):
                key = (id(func), at, part)
                if key in facts:
                    continue
                found = _alike([_known(caller, call.arguments[at], part, facts)
                                for caller, call in calls])
                if found is not None and (part != _WHOLE or found.ty == param.ty):
                    facts[key] = found
                    grew = True
        return grew

    def _apply(self, func: Function, facts: _Fact) -> bool:
        """Read the constants known of *func*'s parameters where they are read."""
        assert func.entry is not None
        params = {id(param): at for at, param in enumerate(func.entry.params)}
        instead: dict[int, Value] = {}
        for at, param in enumerate(func.entry.params):
            whole = facts.get((id(func), at, _WHOLE))
            if whole is not None:
                instead[id(param)] = whole
        for block in func.blocks:
            for inst in block.insts:
                if not isinstance(inst, ExtractInst):
                    continue
                at = params.get(id(inst.operands[0]))
                if at is None:
                    continue
                found = facts.get((id(func), at, inst.index))
                if found is not None and found.ty == inst.ty:
                    instead[id(inst)] = found
        all_stand_for(func, instead)
        return bool(instead)


def _parts(calls: list[tuple[Function, CallInst]], at: int) -> int:
    """How many parts the tuples handed for parameter *at* have, where every
    call hands one made where it is called; nought otherwise."""
    counts = {len(call.arguments[at].operands) for _, call in calls
              if isinstance(call.arguments[at], TupleInst)}
    if len(counts) != 1 or not all(isinstance(call.arguments[at], TupleInst)
                                   or isinstance(call.arguments[at], BlockParam)
                                   for _, call in calls):
        return 0
    return counts.pop()


def _known(caller: Function, value: Value, part: int, facts: _Fact) -> Value | None:
    """The constant *value* -- or its part *part* -- is, where that is known in
    *caller*: written there, or what every caller of *caller* hands it."""
    if part != _WHOLE:
        if isinstance(value, TupleInst):
            if part >= len(value.operands):
                return None
            return _known(caller, value.operands[part], _WHOLE, facts)
        return _of_a_parameter(caller, value, part, facts)
    if isinstance(value, (IntConst, BoolConst)):
        return value
    return _of_a_parameter(caller, value, _WHOLE, facts)


def _of_a_parameter(caller: Function, value: Value, part: int,
                    facts: _Fact) -> Value | None:
    """What is known of *value* where it is a parameter of *caller*."""
    entry = caller.entry
    if not isinstance(value, BlockParam) or entry is None \
            or value.block is not entry:
        return None
    for at, param in enumerate(entry.params):
        if param is value:
            return facts.get((id(caller), at, part))
    return None


def _alike(found: list[Value | None]) -> Value | None:
    """The one constant every call handed, where every one handed the same."""
    if not found or any(one is None for one in found):
        return None
    first = found[0]
    assert first is not None
    for one in found[1:]:
        assert one is not None
        if type(one) is not type(first) or one.ty != first.ty \
                or getattr(one, "value") != getattr(first, "value"):
            return None
    return first
