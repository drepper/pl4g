"""A branch with two ways out that carries values, and the block put on its edge.

The moves a branch makes belong on one of its ways out, and a branch with more
than one has nowhere to put them: before the branch is both ways.  So a block is
put on the edge, which is where they go.  The backend refused this shape rather
than getting it wrong, and these are what say it no longer has to.
"""

from __future__ import annotations

import pytest

from conftest import compiler_targets
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BinaryInst, BinOp, BlockTarget, BrInst, CmpInst,
                            CmpPred, CondBrInst, MemStartInst, RetInst)
from pypl4g.ir.module import Module
from pypl4g.ir.types import BOOL, MEM, U8
from pypl4g.ir.verify import verify
from pypl4g.opt.passes.splitedges import SplitEdges
from pypl4g.diag.engine import collecting_engine
from pypl4g.mc.streamer import MCStreamer
from pypl4g.target.registry import lookup as lookup_target
from test_loops import build_and_run


def split(module: Module) -> bool:
    """Run the pass, check the module still verifies, and say what it said."""
    changed = SplitEdges().run(module)
    verify(module)
    return changed


def labels(func: Function) -> list[str]:
    """What the blocks are called, in the order they are laid out."""
    return [block.label for block in func.blocks]


def two_ways_to_one_block(module: Module, left: int, right: int) -> Function:
    """Both arms of one branch carrying a value to the block they share.

    The edge that has to be split however it is looked at: the block has two
    ways in and the branch two ways out, so the moves can go neither before the
    branch nor at the top of the block.
    """
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    joined = func.add_block("joined")
    answer = joined.add_param(U8, "answer")
    going = entry.append(CmpInst(CmpPred.ULT, module.int_const(U8, 1),
                                 module.int_const(U8, 2), BOOL))
    entry.append(CondBrInst(going,
                            BlockTarget(joined, (module.int_const(U8, left),)),
                            BlockTarget(joined, (module.int_const(U8, right),))))
    joined.append(RetInst(answer))
    module.add_function(func)
    module.startup = func
    verify(module)
    return func


def counting_from_the_branch(module: Module, turns: int) -> Function:
    """A loop whose conditional branch carries what it goes round with.

    The shape the front end's loops are written to avoid -- they carry on a jump
    into the header instead -- and the one that says the block goes *after* the
    branch rather than in front of the block it goes to: what it carries is
    computed in the header, so a block in front of the header would read two
    values nothing had worked out yet.
    """
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    header = func.add_block("header")
    after = func.add_block("after")
    entry.append(BrInst(BlockTarget(header, (module.int_const(U8, 0),
                                             module.int_const(U8, 0)))))
    index = header.add_param(U8, "index")
    total = header.add_param(U8, "total")
    going = header.append(CmpInst(CmpPred.ULT, index,
                                  module.int_const(U8, turns), BOOL))
    stepped = header.append(BinaryInst(BinOp.ADD, index, module.int_const(U8, 1)))
    added = header.append(BinaryInst(BinOp.ADD, total, index))
    header.append(CondBrInst(going, BlockTarget(header, (stepped, added)),
                             BlockTarget(after, (total,))))
    answer = after.add_param(U8, "answer")
    after.append(RetInst(answer))
    module.add_function(func)
    module.startup = func
    verify(module)
    return func


# -- what the pass does ---------------------------------------------------------

def test_an_edge_that_carries_something_gets_a_block() -> None:
    """One per arm here, since both arms carry a value."""
    module = Module("t")
    func = two_ways_to_one_block(module, 7, 9)
    assert split(module)
    assert labels(func) == ["block0", "edge", "edge1", "joined"]
    branch = func.blocks[0].terminator
    assert not branch.true_target.args and not branch.false_target.args
    assert branch.true_target.block is func.blocks[1]
    assert branch.false_target.block is func.blocks[2]
    for made in func.blocks[1:3]:
        assert made.terminator.target.block.label == "joined"
        assert len(made.terminator.target.args) == 1


def test_the_block_goes_straight_after_the_branch() -> None:
    """Even where the edge goes backwards, which is what makes the rule simple:
    what the block reads is computed before the branch, so after the branch is
    somewhere it is always allowed to be."""
    module = Module("t")
    func = counting_from_the_branch(module, 5)
    assert split(module)
    assert labels(func) == ["block0", "header", "edge", "edge1", "after"]
    branch = func.blocks[1].terminator
    assert branch.true_target.block.label == "edge"
    assert branch.true_target.block.terminator.target.block.label == "header"


def test_a_branch_with_one_way_out_is_left_alone() -> None:
    """It has a block to make its moves in already: its own."""
    module = Module("t")
    func = Function("main", module.types.func_type((), U8), FuncAttrs())
    entry = func.add_block()
    second = func.add_block("second")
    answer = second.add_param(U8, "answer")
    entry.append(BrInst(BlockTarget(second, (module.int_const(U8, 3),))))
    second.append(RetInst(answer))
    module.add_function(func)
    assert not split(module)
    assert labels(func) == ["block0", "second"]


def test_an_edge_that_carries_nothing_is_left_alone() -> None:
    """There is nothing to move, so there is nothing to make room for."""
    module = Module("t")
    func = Function("main", module.types.func_type((), U8), FuncAttrs())
    entry = func.add_block()
    yes = func.add_block("yes")
    no = func.add_block("no")
    going = entry.append(CmpInst(CmpPred.ULT, module.int_const(U8, 1),
                                 module.int_const(U8, 2), BOOL))
    entry.append(CondBrInst(going, BlockTarget(yes), BlockTarget(no)))
    yes.append(RetInst(module.int_const(U8, 1)))
    no.append(RetInst(module.int_const(U8, 2)))
    module.add_function(func)
    assert not split(module)
    assert labels(func) == ["block0", "yes", "no"]


def test_an_edge_carrying_only_a_memory_token_is_left_alone() -> None:
    """A token is an ordering and not a value: nothing is moved for it, so the
    edge has nothing to make room for either."""
    module = Module("t")
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    joined = func.add_block("joined")
    token = entry.append(MemStartInst())
    going = entry.append(CmpInst(CmpPred.ULT, module.int_const(U8, 1),
                                 module.int_const(U8, 2), BOOL))
    entry.append(CondBrInst(going, BlockTarget(joined, (token,)),
                            BlockTarget(joined, (token,))))
    joined.add_param(MEM, "mem")
    joined.append(RetInst(module.int_const(U8, 0)))
    module.add_function(func)
    module.startup = func
    assert not split(module)
    assert labels(func) == ["block0", "joined"]


def test_running_it_again_finds_nothing_to_do() -> None:
    """What it makes is a branch with one way out, which is not its business."""
    module = Module("t")
    two_ways_to_one_block(module, 7, 9)
    assert split(module)
    assert not split(module)


# -- and what comes out of the backend ------------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
def test_the_answer_is_the_one_the_branch_carried(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Which is the whole point: the value arrives where the block looks for it,
    and it is the arm's own value and not the other arm's."""
    module = Module("t", triple=triple)
    two_ways_to_one_block(module, 7, 9)
    split(module)
    assert build_and_run(module, triple, tmp_path / "out") == 7


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_loop_going_round_from_the_branch_counts(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Five turns adding up what it counted, carried on the branch itself."""
    module = Module("t", triple=triple)
    counting_from_the_branch(module, 5)
    split(module)
    assert build_and_run(module, triple, tmp_path / "out") == 10


def test_without_the_pass_the_backend_still_says_so() -> None:
    """The refusal is what kept this from being got wrong, and it stays: such a
    branch reaching a backend means the pass did not run, which is worth being
    told about rather than guessed at."""
    triple = compiler_targets()[0]
    module = Module("t", triple=triple)
    two_ways_to_one_block(module, 7, 9)
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    target.generate(module, target.new_assembler(streamer, 0), engine, 0)
    assert [one.info.number for one in collected] == [8501]
