"""The optimizer: what each pass removes, and what it must not."""

import pytest

from conftest import describe
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BinaryInst, BinOp, CallInst, LoadInst, MemStartInst,
                            RetInst, StoreInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import U8
from pypl4g.ir.verify import verify
from pypl4g.opt.pass_ import pipeline_for
from pypl4g.opt.passes.dce import DeadCodeElimination


def _startup(module: Module) -> Function:
    """A startup function with one block, added to *module*."""
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    func.add_block()
    module.add_function(func)
    module.startup = func
    return func


def test_an_instruction_nothing_uses_is_removed() -> None:
    """Nothing refers to what it computes, and computing it does nothing."""
    module = Module("t")
    func = _startup(module)
    block = func.entry
    assert block is not None
    block.append(BinaryInst(BinOp.ADD, module.int_const(U8, 1), module.int_const(U8, 2)))
    block.append(RetInst(module.int_const(U8, 0)))
    assert DeadCodeElimination().run(module)
    assert [i.opcode for i in block.insts] == ["ret"]
    verify(module)


def test_what_the_dead_instruction_used_goes_too() -> None:
    """Dropping one leaves the next with no user, so the sweep is repeated.

    A local read from a variable is a load on a memory token; removing the load
    is what makes the token dead, and the token is what a later pass would have
    to reason about if it stayed.
    """
    module = Module("t")
    var = GlobalVar("g", U8, module.types.ptr_type(U8), module.int_const(U8, 3))
    module.add_global(var)
    func = _startup(module)
    block = func.entry
    assert block is not None
    token = block.append(MemStartInst())
    block.append(LoadInst(U8, (token, var)))
    block.append(RetInst(module.int_const(U8, 5)))
    assert DeadCodeElimination().run(module)
    assert [i.opcode for i in block.insts] == ["ret"]
    verify(module)


def test_a_write_stays_even_though_nothing_reads_it_back() -> None:
    """A write is visible after the function that made it has returned."""
    module = Module("t")
    var = GlobalVar("g", U8, module.types.ptr_type(U8, mutable=True),
                    module.int_const(U8, 1))
    module.add_global(var)
    func = _startup(module)
    block = func.entry
    assert block is not None
    token = block.append(MemStartInst())
    block.append(StoreInst(token, var, module.int_const(U8, 7)))
    block.append(RetInst(module.int_const(U8, 0)))
    assert not DeadCodeElimination().run(module)
    assert [i.opcode for i in block.insts] == ["mem.start", "store", "ret"]


def test_a_call_stays_because_nothing_here_knows_what_it_does() -> None:
    """Purity is not inferred or declared yet, so every call is kept."""
    module = Module("t")
    callee = Function("side", module.types.func_type((), U8), FuncAttrs())
    module.add_function(callee)
    func = _startup(module)
    block = func.entry
    assert block is not None
    block.append(CallInst(callee, (), U8))
    block.append(RetInst(module.int_const(U8, 0)))
    assert not DeadCodeElimination().run(module)
    assert [i.opcode for i in block.insts] == ["call", "ret"]


def test_every_shape_says_for_itself_whether_it_has_effects() -> None:
    """A shape added later cannot be forgotten by a list kept in the pass."""
    module = Module("t")
    var = GlobalVar("g", U8, module.types.ptr_type(U8, mutable=True),
                    module.int_const(U8, 1))
    token = MemStartInst()
    assert not token.has_effects
    assert not LoadInst(U8, (token, var)).has_effects
    assert not BinaryInst(BinOp.ADD, module.int_const(U8, 1),
                          module.int_const(U8, 2)).has_effects
    assert StoreInst(token, var, module.int_const(U8, 7)).has_effects
    assert RetInst(module.int_const(U8, 0)).has_effects


# -- through the compiler ------------------------------------------------------

UNREAD = """let g: u8 = 3u8

@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    @[ignore(4006)]
    let unread: u8 = g
    5u8
"""


def test_a_local_nothing_refers_to_leaves_nothing_behind(compile_source) -> None:  # noqa: ANN001
    """A local is a value, so one nothing refers to is an unused instruction."""
    proc, output = compile_source(UNREAD, "-O1", "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "ret.u8 5" in text, text
    assert "load" not in text and "mem.start" not in text, text


def test_it_is_kept_where_nothing_asked_for_it_to_be_dropped(compile_source) -> None:  # noqa: ANN001
    """Dropping it is permitted, not required, so an unoptimized build keeps it.

    What the program wrote is what an unoptimized build contains, which is what
    makes stepping through one match the source.
    """
    proc, output = compile_source(UNREAD, "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    assert "load.u8" in output.read_text(encoding="utf-8")


def test_dropping_it_does_not_take_the_warning_with_it(compile_source) -> None:  # noqa: ANN001
    """The programmer is told either way; the pass runs long after the message."""
    proc, _ = compile_source(UNREAD.replace("    @[ignore(4006)]\n", ""), "-O1")
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4006]" in proc.stderr, proc.stderr


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_the_sweep_runs_wherever_anything_is_optimized(level: int) -> None:
    """It runs last, since both of the other passes can leave dead code."""
    pipeline = pipeline_for(level)
    if level == 0:
        assert pipeline == ()
    else:
        assert pipeline[-1] == "dce"
