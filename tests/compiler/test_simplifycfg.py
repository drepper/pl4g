"""What `simplifycfg` removes: a block with nothing to decide, and a parameter
with nothing to answer.

The four things it does feed each other -- a settled branch leaves a block one
branch reaches, a block one branch reaches leaves a parameter with one incoming
argument, and a parameter replaced by its argument leaves a block that is
nothing but a jump -- so most of these check a chain rather than a step.
"""

from __future__ import annotations

from conftest import describe
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BinaryInst, BinOp, BlockTarget, BrInst, CmpInst,
                            CmpPred, CondBrInst, RetInst)
from pypl4g.ir.module import Module
from pypl4g.ir.types import BOOL, U8
from pypl4g.ir.verify import verify
from pypl4g.opt.passes.simplifycfg import SimplifyCFG


def startup(module: Module) -> Function:
    """A startup function with one block, added to *module*."""
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    func.add_block()
    module.add_function(func)
    module.startup = func
    return func


def simplified(module: Module) -> bool:
    """Run the pass, check the module still verifies, and say what it said."""
    changed = SimplifyCFG().run(module)
    verify(module)
    return changed


def labels(func: Function) -> list[str]:
    """What the blocks are called, which is how a test names one that went."""
    return [block.label for block in func.blocks]


# -- merging a block into its only predecessor ----------------------------------

def test_a_block_with_one_way_in_is_written_into_the_block_it_comes_from() -> None:
    """Which is a block that need not exist: nothing decides anything there."""
    module = Module("t")
    func = startup(module)
    entry, second = func.entry, func.add_block("second")
    assert entry is not None
    entry.append(BrInst(BlockTarget(second)))
    added = second.append(BinaryInst(BinOp.ADD, module.int_const(U8, 1),
                                     module.int_const(U8, 2)))
    second.append(RetInst(added))
    assert simplified(module)
    assert labels(func) == ["block0"]
    assert [one.opcode for one in entry.insts] == ["add", "ret"]


def test_a_block_two_branches_reach_stays() -> None:
    """There is no one block to write it into, and it has to be somewhere."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    joined = func.add_block("joined")
    other = func.add_block("other")
    entry.append(CondBrInst(_unknown(module, func), BlockTarget(joined),
                            BlockTarget(other)))
    other.append(BrInst(BlockTarget(joined)))
    joined.append(RetInst(module.int_const(U8, 0)))
    simplified(module)
    assert "joined" in labels(func)


def test_a_predecessor_with_two_ways_out_keeps_the_block_it_may_go_to() -> None:
    """One way in is not enough: the branch has another arm to take."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    taken = func.add_block("taken")
    other = func.add_block("other")
    entry.append(CondBrInst(_unknown(module, func), BlockTarget(taken),
                            BlockTarget(other)))
    taken.append(RetInst(module.int_const(U8, 1)))
    other.append(RetInst(module.int_const(U8, 2)))
    simplified(module)
    assert labels(func) == ["block0", "taken", "other"]


def test_the_block_control_enters_at_is_never_written_into_another() -> None:
    """Nothing branches to it, and the function's parameters arrive there."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    entry.append(RetInst(module.int_const(U8, 0)))
    assert not simplified(module)
    assert labels(func) == ["block0"]


# -- a parameter with one answer ------------------------------------------------

def test_a_parameter_one_branch_supplies_becomes_what_it_supplied() -> None:
    """Which is what a block with one way in always has."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    joined = func.add_block("joined")
    answer = joined.add_param(U8, "answer")
    other = func.add_block("other")
    entry.append(CondBrInst(_unknown(module, func), BlockTarget(other),
                            BlockTarget(other)))
    other.append(BrInst(BlockTarget(joined, (module.int_const(U8, 7),))))
    joined.append(RetInst(answer))
    assert simplified(module)
    ret = func.blocks[-1].insts[-1]
    assert ret.operands[0] is module.int_const(U8, 7)


def test_a_parameter_every_branch_agrees_on_goes_as_well() -> None:
    """Two ways in, one answer: a question whose answer does not depend on the
    way control came is not a question."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    joined = func.add_block("joined")
    answer = joined.add_param(U8, "answer")
    left = func.add_block("left")
    right = func.add_block("right")
    entry.append(CondBrInst(_unknown(module, func), BlockTarget(left),
                            BlockTarget(right)))
    left.append(BrInst(BlockTarget(joined, (module.int_const(U8, 7),))))
    right.append(BrInst(BlockTarget(joined, (module.int_const(U8, 7),))))
    joined.append(RetInst(answer))
    assert simplified(module)
    assert not joined.params
    assert joined.insts[-1].operands[0] is module.int_const(U8, 7)


def test_a_parameter_the_branches_disagree_about_stays() -> None:
    """It is exactly the question the block-parameter form is there to ask."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    joined = func.add_block("joined")
    answer = joined.add_param(U8, "answer")
    left = func.add_block("left")
    right = func.add_block("right")
    entry.append(CondBrInst(_unknown(module, func), BlockTarget(left),
                            BlockTarget(right)))
    left.append(BrInst(BlockTarget(joined, (module.int_const(U8, 1),))))
    right.append(BrInst(BlockTarget(joined, (module.int_const(U8, 2),))))
    joined.append(RetInst(answer))
    simplified(module)
    assert len(joined.params) == 1


def test_a_loop_carrying_what_it_never_changes_loses_the_parameter() -> None:
    """The way round supplies the parameter itself, which says nothing about it:
    on the turn that does, the parameter already holds what the way in gave."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    header = func.add_block("header")
    carried = header.add_param(U8, "carried")
    exit_ = func.add_block("exit")
    entry.append(BrInst(BlockTarget(header, (module.int_const(U8, 3),))))
    header.append(CondBrInst(_unknown(module, func),
                             BlockTarget(header, (carried,)),
                             BlockTarget(exit_)))
    exit_.append(RetInst(carried))
    assert simplified(module)
    assert not header.params
    assert exit_.insts[-1].operands[0] is module.int_const(U8, 3)
    assert "header" in labels(func), "the loop itself was not supposed to go"


# -- and the four of them together ----------------------------------------------

def test_a_settled_branch_leaves_nothing_at_all() -> None:
    """Which is the chain the whole thing is for: the branch becomes a jump, the
    block it jumps to has one way in, its parameter has one answer, and what is
    left is one block."""
    module = Module("t")
    func = startup(module)
    entry = func.entry
    assert entry is not None
    joined = func.add_block("joined")
    answer = joined.add_param(U8, "answer")
    yes = func.add_block("yes")
    no = func.add_block("no")
    entry.append(CondBrInst(module.bool_const(BOOL, True), BlockTarget(yes),
                            BlockTarget(no)))
    yes.append(BrInst(BlockTarget(joined, (module.int_const(U8, 1),))))
    no.append(BrInst(BlockTarget(joined, (module.int_const(U8, 2),))))
    joined.append(RetInst(answer))
    assert simplified(module)
    assert labels(func) == ["block0"]
    assert [one.opcode for one in entry.insts] == ["ret"]
    assert entry.insts[-1].operands[0] is module.int_const(U8, 1)


# -- through the whole compiler -------------------------------------------------

def test_what_the_inliner_leaves_is_merged_away(compile_source) -> None:  # noqa: ANN001
    """The three blocks a call turns into once its body is put in its place."""
    source = """\
fn add(a: u8, b: u8) \N{RIGHTWARDS ARROW} u8:
    a + b

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(add(1u8, 2u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
"""
    proc, output = compile_source(source, "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "inlined." not in text, text


def _unknown(module: Module, func: Function) -> object:
    """A condition the folder cannot settle, put at the top of the entry block.

    Every test that wants a branch to survive needs one: a constant condition is
    exactly what the first of the four removes.
    """
    entry = func.entry
    assert entry is not None
    made = CmpInst(CmpPred.EQ, module.int_const(U8, 1), module.int_const(U8, 2),
                   BOOL)
    made.parent = entry
    entry.insts.insert(0, made)
    return made
