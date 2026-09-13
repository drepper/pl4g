"""A function that answers with nothing has no answer to use.

The language has no way to *call* a function yet, so none of this can be written
in a program: these build the representation directly, which is the level the
rule lives at anyway.  A call to a function that answers with nothing is the
only thing in the representation whose type is `void`, and what these check is
that such a thing cannot be stored, compared, given back, or used as an operand
of anything.

The one exception is not an exception here but a front-end abbreviation, and the
last test is what says so: `return f()` in a function that itself answers with
nothing is two things -- the call, and then a return carrying nothing -- and
that is what it becomes.  Nothing of it reaches the representation as a value.
"""

import pytest

from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BinaryInst, BinOp, CallInst, CmpInst, CmpPred,
                            MemStartInst, RetInst, StoreInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import BOOL, U8, VOID
from pypl4g.diag.engine import InternalError
from pypl4g.ir.verify import verify


def module_with_a_silent_function() -> tuple[Module, Function, Function]:
    """A module holding a function that answers with nothing and one that calls
    it, with the caller's body left for each test to fill in."""
    module = Module("t")
    silent = Function("silent", module.types.func_type((), VOID))
    silent.add_block().append(RetInst())
    module.add_function(silent)
    caller = Function("main", module.types.func_type((), U8),
                      FuncAttrs(special=SpecialKind.STARTUP))
    module.add_function(caller)
    module.startup = caller
    return module, silent, caller


def test_a_call_that_answers_with_nothing_is_well_formed(  # noqa: ANN201
) -> None:
    """The call itself is fine.  It is naming its answer that is not."""
    module, silent, caller = module_with_a_silent_function()
    block = caller.add_block()
    block.append(CallInst(silent, (), VOID))
    block.append(RetInst(module.int_const(U8, 0)))
    verify(module)


def test_its_answer_cannot_start_a_variable() -> None:
    """What would be written into the variable?"""
    module, silent, caller = module_with_a_silent_function()
    kept = GlobalVar("kept", U8, module.types.ptr_type(U8),
                     initializer=module.int_const(U8, 0))
    module.add_global(kept)
    block = caller.add_block()
    token = block.append(MemStartInst())
    answer = block.append(CallInst(silent, (), VOID))
    block.append(StoreInst(token, kept, answer))
    block.append(RetInst(module.int_const(U8, 0)))
    with pytest.raises(InternalError, match="has no value"):
        verify(module)


def test_its_answer_cannot_be_an_operand() -> None:
    """Nor added to anything, nor compared with anything."""
    for shape in ("add", "compare"):
        module, silent, caller = module_with_a_silent_function()
        block = caller.add_block()
        answer = block.append(CallInst(silent, (), VOID))
        if shape == "add":
            block.append(BinaryInst(BinOp.ADD, answer, module.int_const(U8, 1)))
        else:
            block.append(CmpInst(CmpPred.EQ, answer, module.int_const(U8, 1), BOOL))
        block.append(RetInst(module.int_const(U8, 0)))
        with pytest.raises(InternalError, match="has no value"):
            verify(module)


def test_its_answer_cannot_be_given_back() -> None:
    """A function that answers with a number has not answered by handing over
    something that is not one."""
    module, silent, caller = module_with_a_silent_function()
    block = caller.add_block()
    answer = block.append(CallInst(silent, (), VOID))
    block.append(RetInst(answer))
    with pytest.raises(InternalError, match="has no value"):
        verify(module)


def test_returning_it_from_a_function_that_answers_with_nothing_is_two_things() -> None:
    """The abbreviation, written out.

    `return f()` in a function that itself answers with nothing is the call and
    then a return that carries nothing.  There is no value anywhere in it, which
    is why it is well formed where every case above is not -- and why it is an
    abbreviation rather than an exception to the rule.
    """
    module, silent, _ = module_with_a_silent_function()
    quiet = Function("quiet", module.types.func_type((), VOID))
    block = quiet.add_block()
    block.append(CallInst(silent, (), VOID))
    block.append(RetInst())
    module.add_function(quiet)
    verify(module)
