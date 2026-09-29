"""The many-way branch: one terminator saying the whole question at once.

`match` over an enumeration builds one, and a backend makes of it what it can
make -- a chain of comparisons today, one per case, with what falls off the end
going the default way.  These build the representation directly and run what
comes out, so what is checked is where control actually went.
"""

from __future__ import annotations

import pytest

from conftest import compiler_targets, describe
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BlockTarget, LoadInst, MemStartInst, RetInst,
                            SwitchInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import U8
from pypl4g.ir.verify import verify
from pypl4g.opt.passes.simplifycfg import SimplifyCFG
from test_loops import build_and_run

#: What each case of the programs below answers with, by the value switched on.
ANSWERS = {0: 11, 5: 22, 200: 33}

#: And what a value no case names answers with.
OTHERWISE = 44


def switching(triple: str, subject: int) -> Module:
    """A program that switches on a variable holding *subject* and exits with
    what the case it takes answers.

    The value is read out of a variable rather than written in place, so that
    nothing folds the question away before it is asked.
    """
    module = Module("t", triple=triple)
    held = GlobalVar("held", U8, module.types.ptr_type(U8),
                     module.int_const(U8, subject))
    module.add_global(held)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    token = entry.append(MemStartInst())
    value = entry.append(LoadInst(U8, (token, held)))
    default = func.add_block("otherwise")
    cases = []
    for number, answer in ANSWERS.items():
        block = func.add_block("".join(("case", str(number))))
        block.append(RetInst(module.int_const(U8, answer)))
        cases.append((number, BlockTarget(block)))
    default.append(RetInst(module.int_const(U8, OTHERWISE)))
    entry.append(SwitchInst(value, cases, BlockTarget(default)))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


@pytest.mark.parametrize("subject", (*ANSWERS, 1, 199, 255))
@pytest.mark.parametrize("triple", compiler_targets())
def test_control_goes_the_way_the_value_names(triple: str, subject: int,
                                              tmp_path) -> None:  # noqa: ANN001
    """Each case, and three values no case names."""
    assert build_and_run(switching(triple, subject), triple,
                         tmp_path / "out") == ANSWERS.get(subject, OTHERWISE)


@pytest.mark.parametrize("triple", compiler_targets())
def test_one_case_is_a_switch_like_any_other(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Nothing about the lowering counts the cases, so one is not a special
    shape -- which is worth saying, since one comparison and a jump is exactly
    what a conditional branch would have been."""
    module = Module("t", triple=triple)
    held = GlobalVar("held", U8, module.types.ptr_type(U8),
                     module.int_const(U8, 7))
    module.add_global(held)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    token = entry.append(MemStartInst())
    value = entry.append(LoadInst(U8, (token, held)))
    only = func.add_block("only")
    only.append(RetInst(module.int_const(U8, 1)))
    default = func.add_block("otherwise")
    default.append(RetInst(module.int_const(U8, 2)))
    entry.append(SwitchInst(value, [(7, BlockTarget(only))],
                            BlockTarget(default)))
    module.add_function(func)
    module.startup = func
    verify(module)
    assert build_and_run(module, triple, tmp_path / "out") == 1


# -- what the front end builds --------------------------------------------------

def test_a_match_over_an_enumeration_is_one_switch(compile_source) -> None:  # noqa: ANN001
    """One terminator where there used to be a block per value."""
    proc, output = compile_source("""\
enum Colour { red ; green ; blue }
let chosen: Colour = Colour.green

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    match chosen:
        red: 1u6
        green: 0u6
        blue: 2u6
""", "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert text.count("switch ") == 1, text
    assert "icmp" not in text, text


def test_a_match_over_a_result_is_still_two_ways(compile_source) -> None:  # noqa: ANN001
    """It has two alternatives and asks about one of them, which is a
    conditional branch and not a question with an answer per value."""
    proc, output = compile_source("""\
let hundred: u8 = 100u8
let seven: u8 = 7u8

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let taken: u8 = match hundred \N{DIVISION SIGN} seven:
        u8(q):
            q
        \N{UP TACK}:
            0u8
    if taken \N{NOT EQUAL TO} 14u8: 1u6 else: 0u6
""", "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "switch " not in text, text


# -- and what the simplifier makes of one -------------------------------------

def settled(module: Module) -> bool:
    """Run the simplifier, check the module still verifies, and say what it said."""
    changed = SimplifyCFG().run(module)
    verify(module)
    return changed


def constant_switch(subject: int) -> tuple[Module, Function]:
    """A switch on a value that is already known."""
    module = Module("t")
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    taken = func.add_block("taken")
    taken.append(RetInst(module.int_const(U8, 1)))
    other = func.add_block("other")
    other.append(RetInst(module.int_const(U8, 2)))
    default = func.add_block("otherwise")
    default.append(RetInst(module.int_const(U8, 3)))
    entry.append(SwitchInst(module.int_const(U8, subject),
                            [(1, BlockTarget(taken)), (2, BlockTarget(other))],
                            BlockTarget(default)))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module, func


def test_a_switch_on_a_known_value_becomes_the_jump_it_would_take() -> None:
    """The case whose number the value is, and everything else then unreachable."""
    module, func = constant_switch(2)
    assert settled(module)
    assert [block.label for block in func.blocks] == ["block0"]
    assert func.blocks[0].insts[-1].operands[0] is module.int_const(U8, 2)


def test_a_switch_no_case_names_becomes_the_default() -> None:
    """Which is the other half of the same question."""
    module, func = constant_switch(9)
    assert settled(module)
    assert [block.label for block in func.blocks] == ["block0"]
    assert func.blocks[0].insts[-1].operands[0] is module.int_const(U8, 3)


def test_a_switch_on_something_unknown_is_left_alone() -> None:
    """There is nothing to settle, and guessing would be guessing."""
    module = Module("t")
    held = GlobalVar("held", U8, module.types.ptr_type(U8),
                     module.int_const(U8, 1))
    module.add_global(held)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    token = entry.append(MemStartInst())
    value = entry.append(LoadInst(U8, (token, held)))
    taken = func.add_block("taken")
    taken.append(RetInst(module.int_const(U8, 1)))
    default = func.add_block("otherwise")
    default.append(RetInst(module.int_const(U8, 2)))
    entry.append(SwitchInst(value, [(1, BlockTarget(taken))],
                            BlockTarget(default)))
    module.add_function(func)
    module.startup = func
    verify(module)
    settled(module)
    assert isinstance(func.blocks[0].insts[-1], SwitchInst)
