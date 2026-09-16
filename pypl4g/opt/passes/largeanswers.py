"""Answering with more than the registers hold.

A function says how it hands back an answer of more than one value, and the one
style that exists answers two of them in registers and everything larger through
storage the caller provides.  This is where the second half of that is carried
out: the function grows one parameter, which is where to put the answer; its
`ret` becomes writes through that place; and every call to it makes a place,
hands it over, and reads the answer back out of it.

It is done here rather than in the three instruction selectors because there is
nothing about it that differs between targets -- a place, some writes and some
reads -- and doing it once is what makes the three of them agree by construction.
It is done here rather than while the language is being checked because the
signature it produces is not the signature the program wrote: the pointer is not
an argument any program can pass, and no rule about arguments should have to know
that one of them is not one.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from ...ir.reports import ReportKind, ReportLog
from ...ir.function import BasicBlock, Function, ReturnStyle
from ...ir.inst import (BinOp, BinaryInst, CallInst, CastInst, CastKind,
                        ErrorInst, ExtractInst, FailedInst, FrameInst,
                        Instruction, LoadInst,
                        MemStartInst, RetInst, StoreInst, Terminator,
                        TupleInst, UnwrapInst, WrapInst)
from ...ir.layout import (DataLayout, error_offset_of,
                          part_offsets_of, tag_offset_of)
from ...ir.module import Module
from ...ir.types import (MEM, ProductType, ResultType, TupleType, Type, U64,
                         VOID, parts_of)
from ...ir.value import IntConst, Value

#: The one layout this compiler has.  Where it grows a second, the pass is
#: handed the target's rather than knowing one.
LAYOUT = DataLayout(pointer_size=8)


def through_storage(func: Function) -> bool:
    """Whether this function's answer travels through the caller's storage.

    A part that is itself several values -- a result among a tuple's members --
    is left alone, and the instruction selector says what it says about such an
    answer today.  Nothing anywhere counts registers per part recursively, so
    routing one through storage here would only move where it went wrong.
    """
    if func.ty.ret is VOID or func.answering.in_registers(func.ty.ret):
        return False
    return all(len(parts_of(part)) == 1 for part in parts_of(func.ty.ret))


class LargeAnswers:
    """Rewrites the functions whose answer does not fit in registers."""

    name = "largeanswers"

    def run(self, module: Module) -> bool:
        """Rewrite those functions and every call to them."""
        reports = module.reports
        # By identity, since a module may name one function more than once and
        # widening it twice would give it two places to put the answer.
        every = {id(func): func for func in module.functions.values()}
        wanted = {at: func for at, func in every.items()
                  if through_storage(func)}
        if not wanted:
            return False
        answers = {at: func.ty.ret for at, func in wanted.items()}
        for func in wanted.values():
            self._widen(module, func)
        for func in every.values():
            for block in func.blocks:
                self._rewrite_calls(module, block, wanted, answers)
        for func in wanted.values():
            if func.blocks:
                self._store_the_answer(module, func, answers[id(func)])
        for func in wanted.values():
            reports.record(
                ReportKind.ANSWER_IN_STORAGE, func.name,
                "".join(("'", answers[id(func)].render(), "' is more values ",
                         "than the style answers in registers, so the caller ",
                         "provides the place")),
                func.name_span if func.name_span.is_valid else func.span)
        return True

    def _widen(self, module: Module, func: Function) -> None:
        """Give the function the parameter that says where to put the answer.

        It goes last rather than first.  A hidden first argument is what the
        system ABIs do, and they do it because theirs has to be in one known
        register whatever else is passed; this convention is the compiler's own,
        so putting it last leaves every argument the program wrote in the place
        it already had.
        """
        place = module.types.ptr_type(func.ty.ret, mutable=True)
        func.ty = module.types.func_type((*func.ty.params, place), VOID)
        # It writes through a pointer it was handed, which is what impure means
        # once the language is behind us: whether the program was allowed to
        # write it was settled while the program was checked, and what is left
        # is that a call to it now changes memory of the caller's.  Without this
        # a call to a pure function of this shape answers with nothing, is used
        # by nothing, and is swept away with the answer still unwritten.
        func.attrs = replace(func.attrs, impure=True)
        if func.blocks:
            func.blocks[0].add_param(place, "answer")

    def _store_the_answer(self, module: Module, func: Function,
                          answer: Type) -> None:
        """Turn each `ret v` into writes through the place and a bare `ret`."""
        place = func.blocks[0].params[-1]
        for block in func.blocks:
            last = block.terminator
            if not isinstance(last, RetInst) or not last.operands:
                continue
            made: list[Instruction] = []
            # The token first: finding it may put the start of the chain at the
            # top of the entry block, which moves everything below it along.
            token = _token_before(func, block, len(block.insts) - 1)
            at = block.insts.index(last)
            value = last.operands[0]
            for index, (part, offset) in enumerate(
                    zip(parts_of(answer), _offsets(answer))):
                taken = _part(value, index, part, answer, last.span)
                made.append(taken)
                token = StoreInst(token, _at(module, place, part, offset,
                                             made, last.span), taken, last.span)
                made.append(token)
            made.append(RetInst(None, last.span))
            _splice(block, at, made)

    def _rewrite_calls(self, module: Module, block: BasicBlock,
                       wanted: dict[int, Function],
                       answers: dict[int, Type]) -> None:
        """Make a place for each such call, hand it over, and read it back."""
        index = 0
        while index < len(block.insts):
            inst = block.insts[index]
            callee = getattr(inst, "callee", None)
            # A call already rewritten answers with nothing, which is what says
            # it has been: the place it writes into is among its arguments and
            # giving it a second one would be giving it two.
            if not isinstance(inst, CallInst) or id(callee) not in wanted \
                    or inst.ty is VOID:
                index += 1
                continue
            answer = answers[id(callee)]
            span = inst.span
            # The token first: finding it may put the start of the chain at the
            # top of the entry block, which moves this call along with it.
            token = _token_before(block.parent, block, index)
            index = block.insts.index(inst)
            made: list[Instruction] = []
            slot = FrameInst(answer, module.types.ptr_type(answer, mutable=True),
                             span)
            made.append(slot)
            made.append(CallInst(callee, (*inst.operands, slot), VOID, span))
            taken: list[Value] = []
            for part, offset in zip(parts_of(answer), _offsets(answer)):
                read = LoadInst(part, (token, _at(module, slot, part, offset,
                                                  made, span)), span)
                made.append(read)
                taken.append(read)
            rebuilt = _whole(taken, answer, span)
            made.append(rebuilt)
            _splice(block, index, made)
            _stands_for(block.parent, inst, rebuilt)
            index += len(made)


def _offsets(answer: Type) -> tuple[int, ...]:
    """Where each part of the answer goes in the place that holds it."""
    if isinstance(answer, ResultType):
        assert answer.err is not None
        return (0, tag_offset_of(answer, LAYOUT),
                error_offset_of(answer, LAYOUT))
    assert isinstance(answer, TupleType | ProductType), answer
    return part_offsets_of(answer, LAYOUT)


def _part(value: Value, index: int, part: Type, answer: Type,
          span) -> Instruction:  # noqa: ANN001
    """One part of an answer, taken out of it.

    A tuple's parts are its members and a result's are the answer, whether
    there is one, and what the error carries -- three different instructions
    rather than one indexed by number, because they are of three types and the
    representation says so.
    """
    if not isinstance(answer, ResultType):
        return ExtractInst(value, index, part, span)
    return (UnwrapInst(value, part, span) if index == 0
            else FailedInst(value, part, span) if index == 1
            else ErrorInst(value, part, span))


def _whole(taken: Sequence[Value], answer: Type,
           span) -> Instruction:  # noqa: ANN001
    """The parts put back together into the one value they are."""
    if not isinstance(answer, ResultType):
        return TupleInst(list(taken), answer, span)
    return WrapInst(taken[0], taken[1], answer, span, taken[2])


def _at(module: Module, place: Value, part: Type, offset: int,
        made: list[Instruction], span) -> Value:  # noqa: ANN001
    """The address one part of the answer lives at, as a pointer to that part."""
    base = CastInst(CastKind.BITCAST, place,
                    module.types.ptr_type(part, mutable=True), span)
    made.append(base)
    if offset == 0:
        return base
    moved = BinaryInst(BinOp.ADD, base, IntConst(U64, offset), span)
    made.append(moved)
    return moved


def _token_before(func: Function | None, block: BasicBlock,
                  at: int) -> Value:
    """The memory token in force just before instruction *at* of *block*.

    What is written here has to be ordered after everything the function wrote
    before it, so the chain is joined where it stands rather than started again:
    the nearest memory-valued instruction above, or the parameter the block took
    it in, or -- in a function that has not touched memory at all -- the start of
    a chain, put where every chain starts.
    """
    for earlier in reversed(block.insts[:at]):
        if earlier.ty is MEM:
            return earlier
    for param in block.params:
        if param.ty is MEM:
            return param
    assert func is not None
    entry = func.blocks[0]
    if entry.insts and isinstance(entry.insts[0], MemStartInst):
        return entry.insts[0]
    start = MemStartInst()
    start.parent = entry
    entry.insts.insert(0, start)
    return start


def _splice(block: BasicBlock, at: int, made: list[Instruction]) -> None:
    """Put *made* where the instruction at *at* stood."""
    for one in made:
        one.parent = block
    block.insts[at:at + 1] = made


def _stands_for(func: Function, gone: Value, instead: Value) -> None:
    """Make everything that read *gone* read *instead*.

    A branch's arguments count: a call whose answer is carried to another block
    is the ordinary way a value leaves the block it was made in.
    """
    for block in func.blocks:
        for one in block.insts:
            for index, operand in enumerate(one.operands):
                if operand is gone:
                    one.operands[index] = instead
            if isinstance(one, Terminator):
                for target in one.successors():
                    target.args = tuple(instead if arg is gone else arg
                                        for arg in target.args)
