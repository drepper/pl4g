"""Keeping a string or a list two words wide for as long as its allocator is known.

A string and a list are three words: where, how many, and the allocator.  The third is
what lets whatever holds one give it back without being told -- but where the compiler
knows which allocator that is, the word tells nobody anything, and the compiler holds
it instead of the value.  It is put back only where something needs the value whole:
handed to a call, stored, answered.

Inside one block that is already so: a value put together from its parts and taken
apart again is its parts (the first thing done here, folding an `extract` of a
`tuple`).  What makes a value wide is where it meets others -- the parameter of the
block a `then` and an `else` join at, or the head of a loop that carries it round.
There the third word has to travel unless every value that arrives at the parameter
has the *same* allocator, one the compiler can name again in the block itself:
`⎕heap`'s address, none (text in the image), or a parameter of the function.  Then the
parameter is two words, the allocator is put back at the head of the block from what
the compiler knows, and every branch hands over two words.

Which allocator a parameter carries is decided the way a constant is propagated: each
starts as not yet known, every value that arrives narrows it, and two allocators that
differ -- or one known only at run time -- make it carry the word.  A loop is settled by
going round until nothing narrows further.

Every parameter decided either way is said in the report log: `lean-value` where it
stays two words, `fat-value` where it carries its allocator and why.
"""

from __future__ import annotations

from typing import Final

from ...ir.function import BasicBlock, Function
from ...ir.inst import (AddressInst, CastInst, CastKind, ExtractInst, Instruction,
                        TupleInst)
from ...ir.module import GlobalVar, Module
from ...ir.reports import ReportKind
from ...ir.rewrite import all_stand_for
from ...ir.types import ListType, StrType, U64, parts_of
from ...ir.value import BlockParam, IntConst, Value

#: Not yet known, and known to be carried: the two ends of what a parameter may be.
_UNKNOWN: Final = ("unknown",)
_CARRIED: Final = ("carried",)


class LeanValues:
    """Keeps strings and lists two words wide where their allocator is known."""

    name = "leanvalues"

    def run(self, module: Module) -> bool:
        """Fold, narrow every joining parameter, and sweep what is left unread."""
        changed = False
        for func in list(module.functions.values()):
            if not func.blocks:
                continue
            changed |= _fold(func)
            changed |= self._narrow(module, func)
            changed |= _sweep(func)
        return changed

    def _narrow(self, module: Module, func: Function) -> bool:
        """Make every parameter whose values share one known allocator two words."""
        entry = func.blocks[0]
        wanted = [param for block in func.blocks if block is not entry
                  for param in block.params
                  if isinstance(param.ty, (StrType, ListType))]
        if not wanted:
            return False
        incoming = _incoming(func)
        held: dict[int, tuple] = {id(param): _UNKNOWN for param in wanted}
        settled = False
        while not settled:
            settled = True
            for param in wanted:
                found = _UNKNOWN
                for arg in incoming.get((id(param.block), param.index), ()):
                    found = _meet(found, _allocator_of(arg, held, param))
                if found != held[id(param)]:
                    held[id(param)] = found
                    settled = False
        lean = [param for param in wanted
                if held[id(param)] not in (_UNKNOWN, _CARRIED)]
        for param in wanted:
            if held[id(param)] is _CARRIED:
                module.reports.record(
                    ReportKind.FAT_VALUE, func.name,
                    "".join((_named(param), " carries its allocator where ",
                             param.block.label,
                             " joins: what it is given there is made in more "
                             "than one allocator, or in one known only at run "
                             "time")), _where(param, func))
        if not lean:
            return False
        instead: dict[int, Value] = {}
        thin_of: dict[int, BlockParam] = {}
        for param in lean:
            block = param.block
            thin = module.types.tuple_type(parts_of(param.ty)[:2])
            narrow = BlockParam(thin, block, param.index, param.name_hint)
            narrow.name_span = param.name_span
            block.params[param.index] = narrow
            thin_of[id(param)] = narrow
            allocator, made = _again(module, held[id(param)], param)
            first = ExtractInst(narrow, 0, parts_of(param.ty)[0])
            second = ExtractInst(narrow, 1, parts_of(param.ty)[1])
            whole = TupleInst([first, second, allocator], param.ty)
            _prepend(block, [*made, first, second, whole])
            instead[id(param)] = whole
            module.reports.record(
                ReportKind.LEAN_VALUE, func.name,
                "".join((_named(param), " stays two words where ", block.label,
                         " joins: everything it is given there is made in ",
                         _said(held[id(param)]), ", which the compiler holds and "
                         "adds where the value is needed whole")), _where(param, func))
        all_stand_for(func, instead)
        # Every branch to a narrowed parameter hands over the first two words of
        # what it handed over before -- all of them whole values by now.
        for block in func.blocks:
            terminator = block.insts[-1] if block.insts else None
            for target in (terminator.successors()
                           if terminator is not None
                           and hasattr(terminator, "successors") else ()):
                for at, arg in enumerate(target.args):
                    narrow = target.block.params[at] \
                        if at < len(target.block.params) else None
                    if narrow is None or narrow.ty is arg.ty \
                            or id(narrow) not in {id(one) for one in thin_of.values()}:
                        continue
                    pieces = parts_of(arg.ty)
                    if isinstance(arg, TupleInst):
                        parts = [arg.operands[0], arg.operands[1]]
                        made: list[Instruction] = []
                    else:
                        parts = [ExtractInst(arg, 0, pieces[0]),
                                 ExtractInst(arg, 1, pieces[1])]
                        made = list(parts)
                    two = TupleInst(parts, narrow.ty)
                    made.append(two)
                    for one in made:
                        one.parent = block
                    block.insts[-1:-1] = made
                    target.args[at] = two
        return True


def _incoming(func: Function) -> dict[tuple[int, int], list[Value]]:
    """What every branch hands each block parameter, by block and place."""
    found: dict[tuple[int, int], list[Value]] = {}
    for block in func.blocks:
        terminator = block.insts[-1] if block.insts else None
        if terminator is None or not hasattr(terminator, "successors"):
            continue
        for target in terminator.successors():
            for at, arg in enumerate(target.args):
                found.setdefault((id(target.block), at), []).append(arg)
    return found


def _key(value: Value) -> tuple | None:
    """What an allocator value is, where the compiler can name it again."""
    if isinstance(value, AddressInst) and isinstance(value.operands[0], GlobalVar):
        return ("global", value.operands[0])
    if isinstance(value, CastInst) and value.kind is CastKind.BITCAST \
            and isinstance(value.operands[0], IntConst) \
            and value.operands[0].value == 0:
        return ("none",)
    if isinstance(value, BlockParam) and value.block.parent is not None \
            and value.block is value.block.parent.blocks[0]:
        return ("param", value)
    return None


def _allocator_of(arg: Value, held: dict[int, tuple], param: BlockParam) -> tuple:
    """Which allocator a value handed to *param* carries, as far as it is known."""
    if arg is param:
        return _UNKNOWN
    if id(arg) in held:
        return held[id(arg)]
    if isinstance(arg, TupleInst) and len(arg.operands) == 3:
        found = _key(arg.operands[2])
        return found if found is not None else _CARRIED
    return _CARRIED


def _meet(one: tuple, other: tuple) -> tuple:
    """What a parameter is, given two of the things that arrive at it."""
    if one == _UNKNOWN:
        return other
    if other == _UNKNOWN:
        return one
    if one == other:
        return one
    return _CARRIED


def _again(module: Module, key: tuple, param: BlockParam) -> tuple[Value,
                                                                    list[Instruction]]:
    """The allocator *key* names, made again at the head of *param*'s block."""
    arena = parts_of(param.ty)[2]
    match key[0]:
        case "global":
            made = AddressInst(key[1])
            return made, [made]
        case "none":
            made = CastInst(CastKind.BITCAST, module.int_const(U64, 0), arena)
            return made, [made]
    return key[1], []


def _said(key: tuple) -> str:
    """What a report calls the allocator *key* names."""
    match key[0]:
        case "global":
            return "\N{APL FUNCTIONAL SYMBOL QUAD}heap" \
                if key[1].name == "__pl4g_heap" else "".join(("'", key[1].name, "'"))
        case "none":
            return "no allocator: text in the image"
    return "".join(("what parameter '", key[1].name_hint or "?", "' is given"))


def _where(param: BlockParam, func: Function):  # noqa: ANN202
    """Where a report about *param* points: at its name, where it has one."""
    return param.name_span if param.name_span.is_valid else func.span


def _named(param: BlockParam) -> str:
    """What a report calls a parameter."""
    return "".join(("'", param.name_hint, "'")) if param.name_hint else "a value"


def _prepend(block: BasicBlock, made: list[Instruction]) -> None:
    """Put *made* at the head of *block*."""
    for one in made:
        one.parent = block
    block.insts[0:0] = made


def _fold(func: Function) -> bool:
    """Take a part out of a value put together from its parts: that part."""
    instead: dict[int, Value] = {}
    for block in func.blocks:
        for inst in block.insts:
            if isinstance(inst, ExtractInst):
                whole = inst.operands[0]
                while id(whole) in instead:
                    whole = instead[id(whole)]
                if isinstance(whole, TupleInst) and inst.index < len(whole.operands) \
                        and whole.operands[inst.index].ty is inst.ty:
                    instead[id(inst)] = whole.operands[inst.index]
    for key, found in list(instead.items()):
        while id(found) in instead:
            found = instead[id(found)]
        instead[key] = found
    all_stand_for(func, instead)
    return bool(instead)


#: What may go when nothing reads it: making a value, taking one apart, an address,
#: the same bits read as something else.  None of them does anything else.
_PURE: Final = (TupleInst, ExtractInst, AddressInst)


def _sweep(func: Function) -> bool:
    """Drop the pure instructions nothing reads, until none is left."""
    changed = False
    while True:
        used: set[int] = set()
        for block in func.blocks:
            for inst in block.insts:
                used.update(id(one) for one in inst.operands)
                if hasattr(inst, "successors"):
                    for target in inst.successors():
                        used.update(id(arg) for arg in target.args)
        gone = False
        for block in func.blocks:
            kept = [inst for inst in block.insts
                    if id(inst) in used or not (
                        isinstance(inst, _PURE) or (
                            isinstance(inst, CastInst)
                            and inst.kind is CastKind.BITCAST))]
            if len(kept) != len(block.insts):
                block.insts = kept
                gone = True
        if not gone:
            return changed
        changed = True
