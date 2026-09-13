"""The optimizer: what each pass removes, and what it must not."""

import pytest

from conftest import describe
from pypl4g.ir.function import FuncAttrs, Function, Linkage, SpecialKind
from pypl4g.ir.inst import (BinaryInst, BinOp, CallInst, LoadInst, MemStartInst,
                            RetInst, StoreInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import U8, VOID
from pypl4g.ir.verify import verify
from pypl4g.opt.pass_ import pipeline_for
from pypl4g.opt.passes.dce import DeadCodeElimination
from pypl4g.opt.passes.dropunreached import DropUnreached


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
def test_the_sweep_runs_where_anything_is_optimized(level: int) -> None:
    """It runs after the two that can leave dead code, and not before -O1."""
    pipeline = pipeline_for(level)
    if level == 0:
        assert "dce" not in pipeline
    else:
        assert pipeline.index("dce") > pipeline.index("simplifycfg")


# -- functions nothing can reach -----------------------------------------------

def _helper(module: Module, name: str, *, exported: bool = False) -> Function:
    """A function returning a constant, added to *module*."""
    func = Function(name, module.types.func_type((), U8),
                    linkage=Linkage.VISIBLE if exported else Linkage.INTERNAL)
    block = func.add_block()
    block.append(RetInst(module.int_const(U8, 1)))
    module.add_function(func)
    return func


def test_a_function_no_root_reaches_is_dropped() -> None:
    """Whole-program compilation makes unreached and uncallable the same thing."""
    module = Module("t")
    _helper(module, "orphan")
    func = _startup(module)
    block = func.entry
    assert block is not None
    block.append(RetInst(module.int_const(U8, 0)))
    assert DropUnreached().run(module)
    assert list(module.functions) == ["main"]
    verify(module)


def test_what_a_kept_function_calls_is_kept() -> None:
    """Reachability is transitive, so the whole chain from a root survives."""
    module = Module("t")
    inner = _helper(module, "inner")
    outer = Function("outer", module.types.func_type((), U8))
    block = outer.add_block()
    block.append(CallInst(inner, (), U8))
    block.append(RetInst(module.int_const(U8, 1)))
    module.add_function(outer)
    func = _startup(module)
    entry = func.entry
    assert entry is not None
    entry.append(CallInst(outer, (), U8))
    entry.append(RetInst(module.int_const(U8, 0)))
    assert not DropUnreached().run(module)
    assert set(module.functions) == {"inner", "outer", "main"}


def test_being_called_only_from_something_unreachable_is_not_being_called() -> None:
    """Which is why this asks what the roots reach, not what has a caller."""
    module = Module("t")
    inner = _helper(module, "inner")
    outer = Function("outer", module.types.func_type((), U8))
    block = outer.add_block()
    block.append(CallInst(inner, (), U8))
    block.append(RetInst(module.int_const(U8, 1)))
    module.add_function(outer)
    func = _startup(module)
    entry = func.entry
    assert entry is not None
    entry.append(RetInst(module.int_const(U8, 0)))
    assert DropUnreached().run(module)
    assert list(module.functions) == ["main"]


def test_what_the_image_offers_is_a_root() -> None:
    """It is callable from outside, so nothing here can know it is unreachable.

    Being exported from a module is a different thing and is not a root: within
    one program, what a module offers and nothing imports is unreachable.
    """
    module = Module("t")
    _helper(module, "shared", exported=True)
    func = _startup(module)
    entry = func.entry
    assert entry is not None
    entry.append(RetInst(module.int_const(U8, 0)))
    assert not DropUnreached().run(module)
    assert set(module.functions) == {"shared", "main"}


@pytest.mark.parametrize("special", [
    SpecialKind.CONSTRUCTOR, SpecialKind.DESTRUCTOR, SpecialKind.TEST_SUITE,
])
def test_a_function_the_program_takes_part_through_is_a_root(
        special: SpecialKind) -> None:
    """The entry point calls the first two; the testing machinery will the third.

    A test has no caller yet only because what calls one is not written, which
    would be the wrong reason to drop it.
    """
    module = Module("t")
    func = Function("side", module.types.func_type((), VOID),
                    FuncAttrs(special=special))
    func.add_block().append(RetInst())
    module.add_function(func)
    if special is SpecialKind.CONSTRUCTOR:
        module.ctors.append(func)
    elif special is SpecialKind.DESTRUCTOR:
        module.dtors.append(func)
    else:
        module.tests.append(func)
    start = _startup(module)
    entry = start.entry
    assert entry is not None
    entry.append(RetInst(module.int_const(U8, 0)))
    assert not DropUnreached().run(module)
    assert set(module.functions) == {"side", "main"}


def test_a_declaration_nothing_calls_goes_too() -> None:
    """A name for something elsewhere that nothing names is nothing at all."""
    module = Module("t")
    module.add_function(Function("foreign", module.types.func_type((), U8),
                                 linkage=Linkage.IMPORTED))
    func = _startup(module)
    entry = func.entry
    assert entry is not None
    entry.append(RetInst(module.int_const(U8, 0)))
    assert DropUnreached().run(module)
    assert list(module.functions) == ["main"]


UNREACHED = """let g: mut u8 = 0u8

@[constructor]
fn prepare():
    g \N{LEFTWARDS ARROW} 7u8

fn unreached() \N{RIGHTWARDS ARROW} u8:
    2u8

@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    g
"""


@pytest.mark.parametrize("level", ["-O0", "-O1"])
def test_an_unreachable_function_is_gone_at_every_level(compile_source,  # noqa: ANN001
                                                        level: str) -> None:
    """Dropping it is not an optimization, so it does not wait to be asked for."""
    proc, output = compile_source(UNREACHED, level, "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "@unreached" not in text, text
    assert "@prepare" in text and "@main" in text, text


# -- variables nothing can reach -----------------------------------------------

def _global(module: Module, name: str, *, exported: bool = False) -> GlobalVar:
    """A variable holding a constant, added to *module*."""
    var = GlobalVar(name, U8, module.types.ptr_type(U8, mutable=True),
                    module.int_const(U8, 1),
                    linkage=Linkage.VISIBLE if exported else Linkage.INTERNAL)
    module.add_global(var)
    return var


def test_a_variable_nothing_names_is_dropped() -> None:
    """Nothing can read it and nothing can write it, so it holds nothing."""
    module = Module("t")
    _global(module, "orphan")
    func = _startup(module)
    block = func.entry
    assert block is not None
    block.append(RetInst(module.int_const(U8, 0)))
    assert DropUnreached().run(module)
    assert list(module.globals) == []


def test_a_variable_a_reached_function_reads_is_kept() -> None:
    """Naming it from a function a root reaches is what keeps it."""
    module = Module("t")
    var = _global(module, "g")
    func = _startup(module)
    block = func.entry
    assert block is not None
    token = block.append(MemStartInst())
    value = block.append(LoadInst(U8, (token, var)))
    block.append(RetInst(value))
    assert not DropUnreached().run(module)
    assert list(module.globals) == ["g"]


def test_a_variable_only_a_dropped_function_named_goes_too() -> None:
    """Dropping a function is what can make a variable unreachable.

    Both are settled in one pass and in that order, rather than by two that
    would have to be run until they agreed.
    """
    module = Module("t")
    var = _global(module, "g")
    orphan = Function("orphan", module.types.func_type((), U8))
    block = orphan.add_block()
    token = block.append(MemStartInst())
    block.append(RetInst(block.append(LoadInst(U8, (token, var)))))
    module.add_function(orphan)
    func = _startup(module)
    entry = func.entry
    assert entry is not None
    entry.append(RetInst(module.int_const(U8, 0)))
    assert DropUnreached().run(module)
    assert list(module.functions) == ["main"]
    assert list(module.globals) == []


def test_a_variable_that_is_only_written_is_kept() -> None:
    """A write outlives the function, so it is not the pass's call to remove it.

    Whether anyone should have written it is reported instead.
    """
    module = Module("t")
    var = _global(module, "g")
    func = _startup(module)
    block = func.entry
    assert block is not None
    token = block.append(MemStartInst())
    block.append(StoreInst(token, var, module.int_const(U8, 7)))
    block.append(RetInst(module.int_const(U8, 0)))
    assert not DropUnreached().run(module)
    assert list(module.globals) == ["g"]


def test_a_variable_the_image_offers_is_a_root() -> None:
    """Something outside this compilation may name it, as for a function."""
    module = Module("t")
    _global(module, "shared", exported=True)
    func = _startup(module)
    block = func.entry
    assert block is not None
    block.append(RetInst(module.int_const(U8, 0)))
    assert not DropUnreached().run(module)
    assert list(module.globals) == ["shared"]


UNREACHED_VAR = """let used: u8 = 7u8
let only_by_dropped: u8 = 9u8

fn unreached() \N{RIGHTWARDS ARROW} u8:
    only_by_dropped

@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    used
"""


def test_the_whole_chain_goes_through_the_compiler(compile_source) -> None:  # noqa: ANN001
    """The function and the variable only it named are both left out."""
    proc, output = compile_source(UNREACHED_VAR, "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "@used" in text, text
    assert "@only_by_dropped" not in text and "@unreached" not in text, text


# -- what was decided -----------------------------------------------------------

def test_dropping_something_is_recorded_as_a_decision() -> None:
    """The pass says what it left out, whether or not anyone asked for the log.

    A decision recorded only when someone is watching is one a test cannot check.
    """
    from pypl4g.ir.decisions import DecisionKind

    module = Module("t")
    _helper(module, "orphan")
    _global(module, "unused")
    func = _startup(module)
    block = func.entry
    assert block is not None
    block.append(RetInst(module.int_const(U8, 0)))
    assert DropUnreached().run(module)
    dropped = module.decisions
    assert [d.subject for d in dropped.of_kind(DecisionKind.DROP_FUNCTION)] == ["orphan"]
    assert [d.subject for d in dropped.of_kind(DecisionKind.DROP_VARIABLE)] == ["unused"]
    assert all(d.reason for d in dropped.entries), "a decision with no reason"


def test_nothing_kept_is_recorded_as_dropped() -> None:
    """A log that said things went that did not would be worse than none."""
    from pypl4g.ir.decisions import DecisionKind

    module = Module("t")
    var = _global(module, "g", exported=True)
    _helper(module, "shared", exported=True)
    func = _startup(module)
    block = func.entry
    assert block is not None
    token = block.append(MemStartInst())
    block.append(StoreInst(token, var, module.int_const(U8, 1)))
    block.append(RetInst(module.int_const(U8, 0)))
    assert not DropUnreached().run(module)
    assert module.decisions.entries == []
    assert module.decisions.of_kind(DecisionKind.DROP_FUNCTION) == []


def test_a_dropped_local_is_recorded_by_the_name_it_was_given() -> None:
    """A value with no name is an intermediate of an expression and nothing the
    program can ask about; one with a name is a local the program wrote down."""
    from pypl4g.ir.decisions import DecisionKind

    module = Module("t")
    var = GlobalVar("g", U8, module.types.ptr_type(U8), module.int_const(U8, 3))
    module.add_global(var)
    func = _startup(module)
    block = func.entry
    assert block is not None
    token = block.append(MemStartInst())
    named = block.append(LoadInst(U8, (token, var)))
    named.name_hint = "unread"
    block.append(LoadInst(U8, (token, var)))          # no name: an intermediate
    block.append(RetInst(module.int_const(U8, 5)))
    assert DeadCodeElimination().run(module)
    dropped = module.decisions.of_kind(DecisionKind.DROP_LOCAL)
    assert [d.subject for d in dropped] == ["unread"], \
        "an unnamed value was reported as a local, or a named one was not"


def test_a_local_something_reads_is_not_recorded() -> None:
    """A log that said things went that did not would be worse than none."""
    from pypl4g.ir.decisions import DecisionKind

    module = Module("t")
    var = GlobalVar("g", U8, module.types.ptr_type(U8), module.int_const(U8, 3))
    module.add_global(var)
    func = _startup(module)
    block = func.entry
    assert block is not None
    token = block.append(MemStartInst())
    named = block.append(LoadInst(U8, (token, var)))
    named.name_hint = "kept"
    block.append(RetInst(named))
    assert not DeadCodeElimination().run(module)
    assert module.decisions.of_kind(DecisionKind.DROP_LOCAL) == []

