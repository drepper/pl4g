"""The allocator: what it hands out, and that a program can use it.

The language has no way to allocate yet, so these build the representation
directly -- declaring the runtime's entry point and calling it -- and take it
through code generation and the image writer, the same path the driver takes,
and then run the result on every target.
"""

import stat
import subprocess

import pytest

from conftest import check_conformance, compiler_targets, describe, runner_for
from pypl4g.diag.engine import collecting_engine
from pypl4g.elf.layout import ImageKind
from pypl4g.elf.writer import ImageSettings, write_image
from pypl4g.ir.function import FuncAttrs, Function, Linkage, SpecialKind
from pypl4g.ir.inst import (AddressInst, BinaryInst, BinOp, CallInst,
                            LoadInst, MemStartInst, RetInst, StoreInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import U8, U64, VOID
from pypl4g.ir.verify import verify
from pypl4g.mc.streamer import MCStreamer
from pypl4g.target.allocator import (ALLOC_SYMBOL, CHUNK_MINIMUM, GRAIN,
                                     RELEASE_SYMBOL)
from pypl4g.target.registry import lookup as lookup_target


class Program:
    """A program with an arena, being built one instruction at a time."""

    def __init__(self, triple: str) -> None:
        self.module = Module("t", triple=triple)
        types = self.module.types
        arena_ty = types.tuple_type((U64, U64, U64))
        self.arena_ptr = types.ptr_type(arena_ty, mutable=True)
        self.byte_ptr = types.ptr_type(U8, mutable=True)
        # An arena is three words, and a variable with no initializer starts as
        # zeroes -- which is an arena that has asked for nothing yet.
        self.heap = GlobalVar("heap", arena_ty, self.arena_ptr)
        self.module.add_global(self.heap)
        self.alloc = self._runtime(ALLOC_SYMBOL, (self.arena_ptr, U64),
                                   self.byte_ptr)
        self.release = self._runtime(RELEASE_SYMBOL, (self.arena_ptr,), VOID)
        self.func = Function("main", types.func_type((), U8),
                             FuncAttrs(special=SpecialKind.STARTUP))
        self.block = self.func.add_block()
        self.module.add_function(self.func)
        self.module.startup = self.func
        self.token = self.block.append(MemStartInst())
        self.heap_address = self.block.append(AddressInst(self.heap))

    def _runtime(self, name: str, params: tuple, result) -> Function:  # noqa: ANN001
        """Declare one of the entry points the backend supplies."""
        func = Function(name, self.module.types.func_type(params, result),
                        FuncAttrs(abi="pl4g.runtime"), linkage=Linkage.VISIBLE)
        self.module.add_function(func)
        return func

    def allocate(self, size: int):  # noqa: ANN201
        """Append a call asking the arena for *size* bytes."""
        return self.block.append(
            CallInst(self.alloc, (self.heap_address,
                                  self.module.int_const(U64, size)),
                     self.byte_ptr))

    def give_back(self) -> None:
        """Append a call giving the whole arena back."""
        self.block.append(CallInst(self.release, (self.heap_address,), VOID))

    def at(self, place, offset: int):  # noqa: ANN001, ANN201
        """Append the computing of the place *offset* bytes past *place*."""
        if offset == 0:
            return place
        return self.block.append(
            BinaryInst(BinOp.ADD, place, self.module.int_const(U64, offset)))

    def write(self, place, offset: int, value: int) -> None:  # noqa: ANN001
        """Append a store of one byte."""
        self.token = self.block.append(
            StoreInst(self.token, self.at(place, offset),
                      self.module.int_const(U8, value)))

    def read(self, place, offset: int):  # noqa: ANN001, ANN201
        """Append a load of one byte."""
        return self.block.append(
            LoadInst(U8, (self.token, self.at(place, offset))))

    def answer(self, value) -> Module:  # noqa: ANN001
        """Finish the program, answering with *value*."""
        self.block.append(RetInst(value))
        verify(self.module)
        return self.module


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


@pytest.mark.parametrize("triple", compiler_targets())
def test_what_is_written_into_an_allocation_is_there_to_be_read(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """The first allocation a program makes is memory it can use."""
    program = Program(triple)
    place = program.allocate(40)
    program.write(place, 0, 37)
    program.write(place, 7, 5)
    first = program.read(place, 0)
    second = program.read(place, 7)
    module = program.answer(
        program.block.append(BinaryInst(BinOp.ADD, first, second)))
    assert build_and_run(module, triple, tmp_path / "out") == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_two_allocations_do_not_overlap(triple: str, tmp_path) -> None:  # noqa: ANN001
    """What is written into one is still there after the next is handed out."""
    program = Program(triple)
    first = program.allocate(1)
    second = program.allocate(1)
    program.write(first, 0, 3)
    program.write(second, 0, 4)
    module = program.answer(
        program.block.append(BinaryInst(BinOp.ADD, program.read(first, 0),
                                        program.read(second, 0))))
    assert build_and_run(module, triple, tmp_path / "out") == 7


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_allocation_starts_on_the_grain(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The next allocation starts one grain on, however little was asked for.

    Said without reading the address as a number, which nothing in the
    representation can do: what is written one grain past the first allocation
    is what the second allocation reads back at its own start.
    """
    program = Program(triple)
    first = program.allocate(1)
    program.write(first, GRAIN, 42)
    second = program.allocate(1)
    module = program.answer(program.read(second, 0))
    assert build_and_run(module, triple, tmp_path / "out") == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_allocation_larger_than_a_chunk(triple: str, tmp_path) -> None:  # noqa: ANN001
    """A chunk is asked for large enough to hold what was asked for."""
    size = 3 * CHUNK_MINIMUM
    program = Program(triple)
    place = program.allocate(size)
    program.write(place, 0, 11)
    program.write(place, size - 1, 31)
    module = program.answer(
        program.block.append(BinaryInst(BinOp.ADD, program.read(place, 0),
                                        program.read(place, size - 1))))
    assert build_and_run(module, triple, tmp_path / "out") == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_more_than_one_chunk(triple: str, tmp_path) -> None:  # noqa: ANN001
    """An arena that runs out of room asks for another chunk and goes on."""
    program = Program(triple)
    first = program.allocate(CHUNK_MINIMUM - 64)
    program.write(first, 0, 20)
    second = program.allocate(CHUNK_MINIMUM - 64)
    program.write(second, 0, 22)
    module = program.answer(
        program.block.append(BinaryInst(BinOp.ADD, program.read(first, 0),
                                        program.read(second, 0))))
    assert build_and_run(module, triple, tmp_path / "out") == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_arena_given_back_can_be_used_again(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Giving an arena back leaves an arena holding nothing, not rubble."""
    program = Program(triple)
    program.write(program.allocate(64), 0, 1)
    program.give_back()
    place = program.allocate(64)
    program.write(place, 0, 42)
    module = program.answer(program.read(place, 0))
    assert build_and_run(module, triple, tmp_path / "out") == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_never_allocates_carries_none_of_it(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """The allocator is emitted where something calls it and nowhere else."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    func.add_block().append(RetInst(module.int_const(U8, 0)))
    module.add_function(func)
    module.startup = func
    verify(module)
    target = lookup_target(triple)
    assert target is not None
    engine, _ = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    asm = target.new_assembler(streamer, 0)
    target.generate(module, asm, engine, 0)
    assert [f.name for f in asm.functions if f.name.startswith("__pl4g_")] == []


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_allocation_the_system_will_not_meet_stops_the_program(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """It says so and dies by a signal, which is the fault path everything uses.

    Answering with a result instead would put a `?` on every value a program
    builds rather than computes, and there is nothing a program could usefully
    do at this point that the system will not do better by refusing to start
    it.
    """
    program = Program(triple)
    place = program.allocate(1 << 62)
    module = program.answer(program.read(place, 0))
    path = tmp_path / "out"
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    asm = target.new_assembler(streamer, 0)
    target.generate(module, asm, engine, 0)
    assert [d.info.number for d in collected] == []
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
    assert proc.returncode != 0, describe(proc)
    assert b"out of memory" in proc.stderr, describe(proc)
