"""Loops: a branch backwards, and what the backend has to get right for one.

The language has no way to write a loop yet, so these build the representation
directly and take it through code generation and the image writer -- the same
path the driver takes -- and then run the result.  Everything they are about is
in the backend: liveness that follows the graph rather than the layout, and a
branch that hands a block its own parameters back rearranged.
"""

import stat
import subprocess

import pytest

from conftest import check_conformance, compiler_targets, describe, runner_for
from pypl4g.diag.engine import collecting_engine
from pypl4g.elf.layout import ImageKind
from pypl4g.elf.writer import ImageSettings, write_image
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BinaryInst, BinOp, BlockTarget, BrInst, CmpInst,
                            CmpPred, CondBrInst, RetInst)
from pypl4g.ir.module import Module
from pypl4g.ir.types import BOOL, U8
from pypl4g.ir.verify import verify
from pypl4g.mc.operand import MCImm, MCReg
from pypl4g.mc.reg import RegisterInfo
from pypl4g.mc.streamer import MCStreamer
from pypl4g.target.branches import _Move, _sequenced
from pypl4g.target.registry import lookup as lookup_target


def build_and_run(module: Module, triple: str, path) -> int:  # noqa: ANN001
    """Generate, write and run the image, and answer with its exit status."""
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    asm = target.new_assembler(streamer, 0)
    target.generate(module, asm, engine, 0)
    assert [d.info.number for d in collected] == [], \
        "".join((triple, ": ", "; ".join(d.info.name for d in collected)))
    defaults = target.image_defaults()
    settings = ImageSettings(machine=defaults.machine, base_vaddr=defaults.base_vaddr,
                             page_size=defaults.page_size,
                             entry_symbol=target.entry_symbol,
                             header_flags=defaults.header_flags,
                             kind=ImageKind.EXECUTABLE)
    image, _ = write_image(settings, list(streamer.sections.values()),
                           list(streamer.symbols.values()), ["t.pl4g"],
                           target.apply_fixup)
    path.write_bytes(image)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    check_conformance(path)
    proc = subprocess.run([*runner_for(triple), str(path)], capture_output=True,
                          timeout=60)
    assert proc.returncode >= 0, describe(proc)
    return proc.returncode


def counting(module: Module, turns: int) -> Module:
    """A program adding up the numbers below *turns*, in a loop.

    The shape every loop the front end will build has: a block before it hands
    the carried values over, a header takes them as parameters and tests, the
    body computes and hands the next ones back, and what the block after the
    loop reads are the header's parameters.  No conditional branch carries
    anything.
    """
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    header = func.add_block("header")
    body = func.add_block("body")
    after = func.add_block("after")
    entry.append(BrInst(BlockTarget(header, (module.int_const(U8, 0),
                                             module.int_const(U8, 0)))))
    index = header.add_param(U8)
    total = header.add_param(U8)
    going = header.append(CmpInst(CmpPred.ULT, index,
                                  module.int_const(U8, turns), BOOL))
    header.append(CondBrInst(going, BlockTarget(body), BlockTarget(after)))
    stepped = body.append(BinaryInst(BinOp.ADD, index, module.int_const(U8, 1)))
    added = body.append(BinaryInst(BinOp.ADD, total, index))
    body.append(BrInst(BlockTarget(header, (stepped, added))))
    after.append(RetInst(total))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


def exchanging(module: Module, turns: int) -> Module:
    """A program whose loop hands its two carried values back the other way round.

    Nothing else in the language produces this, and it is the case a copy that
    emitted its moves in order would get wrong: the second move would read a
    register the first had already written.
    """
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    header = func.add_block("header")
    body = func.add_block("body")
    after = func.add_block("after")
    entry.append(BrInst(BlockTarget(header, (module.int_const(U8, 1),
                                             module.int_const(U8, 2),
                                             module.int_const(U8, turns)))))
    first = header.add_param(U8)
    second = header.add_param(U8)
    left = header.add_param(U8)
    going = header.append(CmpInst(CmpPred.UGT, left, module.int_const(U8, 0), BOOL))
    header.append(CondBrInst(going, BlockTarget(body), BlockTarget(after)))
    fewer = body.append(BinaryInst(BinOp.SUB, left, module.int_const(U8, 1)))
    body.append(BrInst(BlockTarget(header, (second, first, fewer))))
    after.append(RetInst(first))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


def crowded(module: Module, carried: int) -> Module:
    """A loop carrying more values than the target has registers.

    What it answers with is the last of them, which was live across the whole
    loop and so had to be put in the frame and read back.
    """
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    header = func.add_block("header")
    body = func.add_block("body")
    after = func.add_block("after")
    entry.append(BrInst(BlockTarget(
        header, tuple(module.int_const(U8, n) for n in range(carried)))))
    params = [header.add_param(U8) for _ in range(carried)]
    going = header.append(CmpInst(CmpPred.UGT, params[0],
                                  module.int_const(U8, 0), BOOL))
    header.append(CondBrInst(going, BlockTarget(body), BlockTarget(after)))
    fewer = body.append(BinaryInst(BinOp.SUB, params[0],
                                   module.int_const(U8, 1)))
    body.append(BrInst(BlockTarget(header, (fewer, *params[1:]))))
    after.append(RetInst(params[-1]))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


# -- the loops run ---------------------------------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
def test_a_loop_carrying_two_values_runs(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Five turns adding up nought to four, which is ten."""
    module = counting(Module("t", triple=triple), 5)
    assert build_and_run(module, triple, tmp_path / "out") == 10


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_loop_that_exchanges_what_it_carries(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Three turns of swapping one and two leaves two in front."""
    module = exchanging(Module("t", triple=triple), 3)
    assert build_and_run(module, triple, tmp_path / "out") == 2


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_loop_that_exchanges_an_even_number_of_times(triple: str,
                                                       tmp_path) -> None:  # noqa: ANN001
    """And four turns leaves it where it started, which the odd case cannot say."""
    module = exchanging(Module("t", triple=triple), 4)
    assert build_and_run(module, triple, tmp_path / "out") == 1


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_loop_carrying_more_values_than_there_are_registers(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """A value live across the whole loop is put in the frame and read back."""
    module = crowded(Module("t", triple=triple), 20)
    assert build_and_run(module, triple, tmp_path / "out") == 19


#: How many values the body keeps live at once past the read of the value the
#: loop carries in from outside.  It has to be more than the target with the
#: most registers has left over, or the allocator never has to choose and the
#: test says nothing; sixteen is past all three.
BUSY = 16


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_value_read_only_inside_the_loop_keeps_its_register(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """Liveness read off the layout gives this one's register away.

    The value is computed before the loop and read once near the top of the
    body, and nowhere after the loop.  Its span in layout order therefore ends
    in the middle of the body -- and everything the body computes after that
    point is free to take its register, which the next turn then reads instead
    of the value.  Following the graph is what says it is live all the way to
    the branch backwards.

    The answer is the value added up once a turn, so a turn that read the wrong
    register gives a different number rather than merely a different register
    assignment.
    """
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    header = func.add_block("header")
    body = func.add_block("body")
    after = func.add_block("after")
    kept = entry.append(BinaryInst(BinOp.ADD, module.int_const(U8, 3),
                                   module.int_const(U8, 4)))
    entry.append(BrInst(BlockTarget(header, (module.int_const(U8, 0),
                                             module.int_const(U8, 3)))))
    total = header.add_param(U8)
    left = header.add_param(U8)
    going = header.append(CmpInst(CmpPred.UGT, left,
                                  module.int_const(U8, 0), BOOL))
    header.append(CondBrInst(going, BlockTarget(body), BlockTarget(after)))
    added = body.append(BinaryInst(BinOp.ADD, total, kept))
    # Enough work after that read to make the allocator want the register, and
    # written so that every one of these is live at once: they are built up the
    # way they are and then read back down.
    busy = [body.append(BinaryInst(BinOp.OR, module.int_const(U8, n),
                                   module.int_const(U8, 0)))
            for n in range(BUSY)]
    gathered = busy[-1]
    for one in reversed(busy[:-1]):
        gathered = body.append(BinaryInst(BinOp.OR, gathered, one))
    fewer = body.append(BinaryInst(BinOp.SUB, left, module.int_const(U8, 1)))
    body.append(BrInst(BlockTarget(header, (added, fewer))))
    after.append(RetInst(total))
    module.add_function(func)
    module.startup = func
    verify(module)
    assert build_and_run(module, triple, tmp_path / "out") == 21


# -- the copy that makes a latch work --------------------------------------------

#: One register file for every move built by hand below, so that two registers
#: are two and read differently in a failure.
_FILE = RegisterInfo()
_GPR = _FILE.add_class("gpr", 64)


def _spares():  # noqa: ANN202
    """A source of fresh registers, for asking the sequencer on its own."""
    return lambda like: _FILE.new_virtual(_GPR, like.bits)


def _register():  # noqa: ANN202
    """One virtual register, for building moves by hand."""
    return _FILE.new_virtual(_GPR, 64)


def _stands_for(source) -> object:  # noqa: ANN001
    """What a move's source names, as something that can be compared."""
    return id(source.reg) if isinstance(source, MCReg) else ("number", source.value)


def _ends_up_right(wanted: list, ordered: list) -> bool:
    """Whether running *ordered* leaves every register *wanted* asked for.

    The moves are made one after another over registers that start out holding
    themselves, and then every one of the moves that was asked for is checked
    against what its destination turned out to hold.  That is the property
    -- not any rule about the order, which the spare a cycle needs breaks by
    design: it writes a register and the move after it reads that register back.
    """
    holds: dict[int, object] = {}

    def value(reg) -> object:  # noqa: ANN001
        return holds.setdefault(id(reg), id(reg))

    for move in ordered:
        holds[id(move.into)] = (value(move.source.reg)
                                if isinstance(move.source, MCReg)
                                else _stands_for(move.source))
    for move in wanted:
        asked = (id(move.source.reg) if isinstance(move.source, MCReg)
                 else _stands_for(move.source))
        if value(move.into) != asked:
            return False
    return True


def test_a_chain_of_moves_is_put_in_order() -> None:
    """``a from b`` and ``b from c`` have to happen in that order."""
    a, b, c = _register(), _register(), _register()
    wanted = [_Move(into=b, source=MCReg(c)), _Move(into=a, source=MCReg(b))]
    ordered = _sequenced(wanted, _spares())
    assert [m.into for m in ordered] == [a, b]
    assert _ends_up_right(wanted, ordered)


def test_a_move_of_a_register_to_itself_goes() -> None:
    """A parameter handed its own value is no move, and no cycle either."""
    a = _register()
    assert _sequenced([_Move(into=a, source=MCReg(a))], _spares()) == []


def test_two_moves_that_exchange_need_one_spare() -> None:
    """The cycle a loop carrying two values makes on every turn."""
    a, b = _register(), _register()
    wanted = [_Move(into=a, source=MCReg(b)), _Move(into=b, source=MCReg(a))]
    ordered = _sequenced(wanted, _spares())
    assert len(ordered) == 3, "one held aside, and then the two"
    assert _ends_up_right(wanted, ordered)


def test_a_cycle_of_three_needs_one_spare() -> None:
    """However long the cycle, one register held aside makes it a chain."""
    a, b, c = _register(), _register(), _register()
    wanted = [_Move(into=a, source=MCReg(b)), _Move(into=b, source=MCReg(c)),
              _Move(into=c, source=MCReg(a))]
    ordered = _sequenced(wanted, _spares())
    assert len(ordered) == 4, "one held aside, and then the three"
    assert _ends_up_right(wanted, ordered)


def test_a_constant_never_holds_anything_up() -> None:
    """Nothing reads a number, so a move of one is ready from the start."""
    a, b = _register(), _register()
    wanted = [_Move(into=a, source=MCReg(b)), _Move(into=b, source=MCImm(7, 32))]
    ordered = _sequenced(wanted, _spares())
    assert [m.into for m in ordered] == [a, b]
    assert _ends_up_right(wanted, ordered)


# -- what a branch backwards means for the flags ---------------------------------

def test_the_flags_are_not_called_dead_at_the_end_of_a_loop() -> None:
    """What says control leaves is that a block reaches nothing.

    The rewrite that turns a move of zero into an exclusive-or writes the flags,
    so it is valid only where nothing reads them afterwards.  Reading "nothing
    afterwards" as "this is the last block laid out" is the same thing only
    while every branch goes forward: a loop whose exit was folded away ends the
    layout with a jump backwards into a block that may well read them.
    """
    from pypl4g.mc.machine import MachineBasicBlock
    from pypl4g.target.x86_64.peephole import flags_dead_after

    block = MachineBasicBlock("body", successors=["header"])
    assert not flags_dead_after(block, 0, leaves_function=not block.successors)
    leaving = MachineBasicBlock("after")
    assert flags_dead_after(leaving, 0, leaves_function=not leaving.successors)
