"""Conditional branches: what they select, and that the programs run.

The language has no way to write a condition yet, so these build the
representation directly and take it through code generation and the image
writer -- the same path the driver takes -- and then run the result.  That is
what makes this a test of the backends rather than of a printer.
"""

import stat
import subprocess

import pytest

from conftest import check_conformance, compiler_targets, describe, runner_for
from pypl4g.diag.engine import collecting_engine
from pypl4g.elf.layout import ImageKind
from pypl4g.elf.writer import ImageSettings, write_image
from pypl4g.ir.function import BasicBlock, FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BlockTarget, BrInst, CmpInst, CmpPred, CondBrInst,
                            RetInst, UnreachableInst)
from pypl4g.ir.module import Module
from pypl4g.ir.types import BOOL, U8
from pypl4g.ir.verify import verify
from pypl4g.mc.ops import Condition
from pypl4g.mc.streamer import MCStreamer
from pypl4g.target.registry import lookup as lookup_target


def compare_and_branch(module: Module, pred: CmpPred, left: int, right: int,
                       when_true: int, when_false: int) -> Module:
    """A startup function returning one of two values, chosen by a comparison."""
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    yes = func.add_block("yes")
    no = func.add_block("no")
    cond = entry.append(CmpInst(pred, module.int_const(U8, left),
                                module.int_const(U8, right), BOOL))
    entry.append(CondBrInst(cond, BlockTarget(yes), BlockTarget(no)))
    yes.append(RetInst(module.int_const(U8, when_true)))
    no.append(RetInst(module.int_const(U8, when_false)))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


def build(module: Module, triple: str, path) -> list[str]:  # noqa: ANN001
    """Generate code for *module*, write a runnable image as the driver does,
    and return the mnemonics of the startup function's instructions."""
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    asm = target.new_assembler(streamer, 0)
    target.generate(module, asm, engine, 0)
    assert [d.info.number for d in collected] == [], \
        "".join((triple, ": ", "; ".join(d.info.name for d in collected)))
    mnemonics = [i.mnemonic for f in asm.functions if f.name.startswith("main")
                 for i in f.instructions()]
    defaults = target.image_defaults()
    settings = ImageSettings(machine=defaults.machine, base_vaddr=defaults.base_vaddr,
                             page_size=defaults.page_size,
                             entry_symbol=target.entry_symbol,
                             kind=ImageKind.EXECUTABLE)
    image, _ = write_image(settings, list(streamer.sections.values()),
                           list(streamer.symbols.values()), ["t.pl4g"],
                           target.apply_fixup)
    path.write_bytes(image)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    check_conformance(path)
    return mnemonics


def run(triple: str, path) -> int:  # noqa: ANN001
    """Run the image and return the status it exited with."""
    proc = subprocess.run([*runner_for(triple), str(path)], capture_output=True,
                          timeout=60)
    assert proc.returncode >= 0, describe(proc)
    return proc.returncode


# -- every ordering, on every target --------------------------------------------

#: One case per comparison, with two values that make it true and the answer
#: each branch gives.  Signed and unsigned are the same instruction on none of
#: the three architectures, which is why both are here.
CASES = [
    (CmpPred.EQ, 7, 7, 1), (CmpPred.EQ, 7, 8, 2),
    (CmpPred.NE, 7, 8, 1), (CmpPred.NE, 7, 7, 2),
    (CmpPred.SLT, 3, 9, 1), (CmpPred.SLT, 9, 3, 2),
    (CmpPred.SLE, 9, 9, 1), (CmpPred.SLE, 9, 3, 2),
    (CmpPred.SGT, 9, 3, 1), (CmpPred.SGT, 3, 9, 2),
    (CmpPred.SGE, 9, 9, 1), (CmpPred.SGE, 3, 9, 2),
    (CmpPred.ULT, 3, 9, 1), (CmpPred.ULT, 9, 3, 2),
    (CmpPred.ULE, 9, 9, 1), (CmpPred.ULE, 9, 3, 2),
    (CmpPred.UGT, 9, 3, 1), (CmpPred.UGT, 3, 9, 2),
    (CmpPred.UGE, 9, 9, 1), (CmpPred.UGE, 3, 9, 2),
]


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize(("pred", "left", "right", "expected"), CASES,
                         ids=["-".join((c[0].value, str(c[1]), str(c[2])))
                              for c in CASES])
def test_each_comparison_branches_the_right_way(triple: str, pred: CmpPred,
                                                left: int, right: int,
                                                expected: int, tmp_path) -> None:  # noqa: ANN001
    """The program runs and exits with the value the true branch chose."""
    module = compare_and_branch(Module("t", triple=triple), pred, left, right, 1, 2)
    path = tmp_path / "out"
    build(module, triple, path)
    assert run(triple, path) == expected


# -- what the branch is written as ----------------------------------------------

#: The unconditional jump of each target, which a two-way branch should not need
#: when one of its two sides is the block that follows it.
UNCONDITIONAL = {"x86_64-linux-none": "jmp", "aarch64-linux-none": "b",
                 "riscv64-linux-none": "j"}


@pytest.mark.parametrize("triple", compiler_targets())
def test_falling_through_costs_no_jump(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The block after the branch is reached by going on, not by jumping to it.

    A two-way branch is a conditional branch and a jump, and the jump is not
    needed when one of the two blocks is the one that follows.  Turning the
    condition round is what makes that always be so for a branch with two blocks
    after it, which is every branch the language will generate.
    """
    module = compare_and_branch(Module("t", triple=triple), CmpPred.SLT, 1, 1, 1, 2)
    mnemonics = build(module, triple, tmp_path / "out")
    assert UNCONDITIONAL[triple] not in mnemonics, mnemonics


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_condition_is_turned_round_to_get_that(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The branch tests the opposite of what the program asked, and goes the
    other way: "less than" becomes "branch away if greater or equal"."""
    module = compare_and_branch(Module("t", triple=triple), CmpPred.SLT, 1, 1, 1, 2)
    mnemonics = build(module, triple, tmp_path / "out")
    inverted = {"x86_64-linux-none": "jge", "aarch64-linux-none": "b.ge",
                "riscv64-linux-none": "bge"}[triple]
    assert inverted in mnemonics, mnemonics


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_branch_backwards_reaches_the_block_it_names(triple: str,
                                                       tmp_path) -> None:  # noqa: ANN001
    """A loop is a branch to a block already emitted, which is the case a
    forward-only fixup would get wrong."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    again = func.add_block("again")
    done = func.add_block("done")
    entry.append(BrInst(BlockTarget(again)))
    # The comparison is false, so control leaves at once; what matters is that
    # the branch back is encoded against a block that is behind it.
    cond = again.append(CmpInst(CmpPred.NE, module.int_const(U8, 1),
                                module.int_const(U8, 1), BOOL))
    again.append(CondBrInst(cond, BlockTarget(again), BlockTarget(done)))
    done.append(RetInst(module.int_const(U8, 5)))
    module.add_function(func)
    module.startup = func
    verify(module)
    path = tmp_path / "out"
    build(module, triple, path)
    assert run(triple, path) == 5


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_unreachable_point_traps(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Control proved never to arrive there had better not run on if it does."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    dead = func.add_block("dead")
    live = func.add_block("live")
    cond = entry.append(CmpInst(CmpPred.EQ, module.int_const(U8, 1),
                                module.int_const(U8, 1), BOOL))
    entry.append(CondBrInst(cond, BlockTarget(live), BlockTarget(dead)))
    dead.append(UnreachableInst())
    live.append(RetInst(module.int_const(U8, 3)))
    module.add_function(func)
    module.startup = func
    verify(module)
    path = tmp_path / "out"
    build(module, triple, path)
    assert run(triple, path) == 3


# -- what it refuses ------------------------------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
def test_a_comparison_wanted_as_a_value_is_reported(triple: str) -> None:
    """Folding one into a branch is all that is generated; anything else would
    need it computed into a register, which is reported rather than got wrong."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    yes = func.add_block("yes")
    no = func.add_block("no")
    cond = entry.append(CmpInst(CmpPred.EQ, module.int_const(U8, 1),
                                module.int_const(U8, 1), BOOL))
    # Read twice: once by each branch, so neither can simply absorb it.
    entry.append(CondBrInst(cond, BlockTarget(yes), BlockTarget(no)))
    yes.append(CondBrInst(cond, BlockTarget(no), BlockTarget(no)))
    no.append(RetInst(module.int_const(U8, 0)))
    module.add_function(func)
    module.startup = func
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    target.generate(module, target.new_assembler(streamer, 0), engine, 0)
    assert 8501 in [d.info.number for d in collected], \
        [d.info.name for d in collected]


# -- the condition vocabulary ---------------------------------------------------

def test_inverting_a_condition_twice_gives_it_back() -> None:
    """Turning a branch round has to be exact, since it is done silently."""
    for condition in Condition:
        assert condition.inverted().inverted() is condition
        assert condition.inverted() is not condition


def test_swapping_the_operands_swaps_the_ordering() -> None:
    """An architecture missing half the orderings gets them this way."""
    assert Condition.SLT.swapped() is Condition.SGT
    assert Condition.ULE.swapped() is Condition.UGE
    assert Condition.EQ.swapped() is Condition.EQ
    for condition in Condition:
        assert condition.swapped().swapped() is condition


def test_every_comparison_of_the_representation_has_a_condition() -> None:
    """A predicate added later must not be silently unbranchable."""
    from pypl4g.target.branches import CONDITIONS

    assert set(CONDITIONS) == set(CmpPred)


def _unused(block: BasicBlock) -> None:
    """Referenced so the import of BasicBlock is not decoration."""
    assert block.insts is not None
