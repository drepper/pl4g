"""Answering a string or a list without the allocator, where the caller knows it.

A string and a list are three words: where, how many, and the allocator they came
from.  The third is what lets whatever holds one give it back without being told.
But a function whose answers are all made in one allocator the caller can name --
`⎕heap`, or what the caller hands to one parameter -- tells the caller nothing by
handing the third word back: the caller has it already.  So such a function answers
the two words, and every call puts the third back on.

Which functions those are is written on them while they are checked
(`Function.answer_from`): by the signature, `→ str in ⎕heap` or `→ str in a`, or by
the body, where every way out answers something the heap just made.  This pass only
carries it out, the way `largeanswers` carries out answering through storage: the
function's answer type becomes two words, its `ret`s drop the third, and every call
rebuilds the value with the allocator it knows.

A function named anywhere but as the callee of a direct call is left alone -- a call
through a function value cannot know which allocator the function would have named
-- and so is one seen from outside the image, whose callers are not here to rewrite.
"""

from __future__ import annotations

from ...ir.function import Function, Linkage
from ...ir.inst import (AddressInst, CallInst, ExtractInst, Instruction,
                        RetInst, TupleInst)
from ...ir.module import Module
from ...ir.reports import ReportKind
from ...ir.rewrite import stands_for
from ...ir.types import ListType, StrType, Type, parts_of
from ...sema.tables import heap_global


class ThinAnswers:
    """Rewrites the functions whose answer's allocator the caller knows."""

    name = "thinanswers"

    def run(self, module: Module) -> bool:
        """Rewrite those functions and every call to them."""
        every = {id(func): func for func in module.functions.values()}
        wanted = {at: func for at, func in every.items() if _thin(func)}
        if not wanted:
            return False
        for func in every.values():
            for block in func.blocks:
                for inst in block.insts:
                    for named in inst.references():
                        if id(named) in wanted and not (
                                isinstance(inst, CallInst) and inst.callee is named):
                            del wanted[id(named)]
        if not wanted:
            return False
        fat = {at: func.ty.ret for at, func in wanted.items()}
        for at, func in wanted.items():
            thin = module.types.tuple_type(parts_of(fat[at])[:2])
            func.ty = module.types.func_type(func.ty.params, thin,
                                             func.ty.listable)
            self._answer_thin(func, thin)
        for func in every.values():
            for block in func.blocks:
                self._rewrite_calls(module, block, wanted, fat)
        for at, func in wanted.items():
            where = "\N{APL FUNCTIONAL SYMBOL QUAD}heap" \
                if func.answer_from[0] == "heap" else "".join(
                    ("what parameter ", str(func.answer_from[1] + 1), " is given"))
            module.reports.record(
                ReportKind.ANSWER_THIN, func.name,
                "".join(("'", fat[at].written(), "' is answered without its "
                         "allocator, which is ", where, " and which the caller "
                         "adds")),
                func.name_span if func.name_span.is_valid else func.span)
        return True

    def _answer_thin(self, func: Function, thin: Type) -> None:
        """Make every `ret` of *func* answer the first two words."""
        for block in func.blocks:
            index = 0
            while index < len(block.insts):
                inst = block.insts[index]
                if not isinstance(inst, RetInst) or not inst.operands:
                    index += 1
                    continue
                value = inst.operands[0]
                pieces = parts_of(value.ty)
                made: list[Instruction] = [
                    ExtractInst(value, 0, pieces[0], inst.span),
                    ExtractInst(value, 1, pieces[1], inst.span)]
                made.append(TupleInst(list(made), thin, inst.span))
                made.append(RetInst(made[-1], inst.span))
                for one in made:
                    one.parent = block
                block.insts[index:index + 1] = made
                index += len(made)

    def _rewrite_calls(self, module: Module, block, wanted: dict[int, Function],
                       fat: dict[int, Type]) -> None:  # noqa: ANN001
        """Put the allocator back on what each call to such a function answers."""
        index = 0
        while index < len(block.insts):
            inst = block.insts[index]
            callee = getattr(inst, "callee", None)
            # A call already rewritten answers the two words, which is what says
            # it has been.
            if not isinstance(inst, CallInst) or id(callee) not in wanted \
                    or inst.ty is callee.ty.ret:
                index += 1
                continue
            answer = fat[id(callee)]
            pieces = parts_of(answer)
            kind, at = callee.answer_from
            made: list[Instruction] = []
            call = CallInst(callee, list(inst.operands), callee.ty.ret, inst.span)
            made.append(call)
            if kind == "heap":
                allocator: object = AddressInst(heap_global(module), inst.span)
                made.append(allocator)
            else:
                allocator = inst.operands[at]
            first = ExtractInst(call, 0, pieces[0], inst.span)
            second = ExtractInst(call, 1, pieces[1], inst.span)
            whole = TupleInst([first, second, allocator], answer, inst.span)
            made.extend((first, second, whole))
            for one in made:
                one.parent = block
            block.insts[index:index + 1] = made
            stands_for(block.parent, inst, whole)
            index += len(made)


def _thin(func: Function) -> bool:
    """Whether *func* is one this pass rewrites."""
    return (func.answer_from is not None and bool(func.blocks)
            and func.linkage is Linkage.INTERNAL
            and isinstance(func.ty.ret, (StrType, ListType)))
