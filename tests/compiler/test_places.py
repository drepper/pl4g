"""Places reached through an address held in a register.

Until now every load and every store named a variable, and the instruction
reached it relative to itself.  A heap needs the other kind: an address the
program computed, in a register, with the place it names known only while the
program runs.  The language has no way to write one yet, so these build the
representation directly and take it through code generation and the image
writer -- the same path the driver takes -- and then run the result.
"""

import stat
import subprocess

import pytest

from conftest import check_conformance, compiler_targets, describe, runner_for
from pypl4g.diag.engine import collecting_engine
from pypl4g.elf.layout import ImageKind
from pypl4g.elf.writer import ImageSettings, write_image
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (AddressInst, BinaryInst, BinOp, CastInst, CastKind,
                            LoadInst, MemStartInst, RetInst, StoreInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.diag.engine import InternalError
from pypl4g.ir.types import U8, U64
from pypl4g.ir.verify import verify
from pypl4g.mc.streamer import MCStreamer
from pypl4g.target.registry import lookup as lookup_target

#: What the variable holds, chosen so that every byte of it differs: a read or
#: a write that loses its offset lands on another byte and says so.
CONTENTS = 0x0102030405060708


def with_a_variable(module: Module) -> tuple[Function, GlobalVar, object]:
    """A startup function and a writable variable of eight distinct bytes."""
    var = GlobalVar("v", U64, module.types.ptr_type(U64, mutable=True),
                    module.int_const(U64, CONTENTS))
    module.add_global(var)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    return func, var, entry.append(MemStartInst())


def reading(module: Module, offset: int) -> Module:
    """A program answering with the byte of the variable at *offset*."""
    func, var, token = with_a_variable(module)
    module.add_function(func)
    module.startup = func
    block = func.blocks[0]
    address = block.append(AddressInst(var))
    bytewise = block.append(
        CastInst(CastKind.BITCAST, address, module.types.ptr_type(U8, mutable=True)))
    place = bytewise if offset == 0 else block.append(
        BinaryInst(BinOp.ADD, bytewise, module.int_const(U64, offset)))
    block.append(RetInst(block.append(LoadInst(U8, (token, place)))))
    verify(module)
    return module


def writing(module: Module, written_at: int, read_at: int, value: int) -> Module:
    """A program writing one byte through an address and answering with another."""
    func, var, token = with_a_variable(module)
    module.add_function(func)
    module.startup = func
    block = func.blocks[0]
    address = block.append(AddressInst(var))
    bytewise = block.append(
        CastInst(CastKind.BITCAST, address, module.types.ptr_type(U8, mutable=True)))

    def at(offset: int):  # noqa: ANN202
        if offset == 0:
            return bytewise
        return block.append(
            BinaryInst(BinOp.ADD, bytewise, module.int_const(U64, offset)))

    written = block.append(
        StoreInst(token, at(written_at), module.int_const(U8, value)))
    block.append(RetInst(block.append(LoadInst(U8, (written, at(read_at))))))
    verify(module)
    return module


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


# -- reading ---------------------------------------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize("offset", [0, 1, 3, 7])
def test_reading_through_an_address_in_a_register(triple: str, offset: int,
                                                  tmp_path) -> None:  # noqa: ANN001
    """The byte read is the one the offset names, not the one the variable starts at."""
    module = reading(Module("t", triple=triple), offset)
    expected = (CONTENTS >> (8 * offset)) & 0xFF
    assert build_and_run(module, triple, tmp_path / "out") == expected


# -- writing ---------------------------------------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
def test_writing_through_an_address_in_a_register(triple: str, tmp_path) -> None:  # noqa: ANN001
    """What was written is there to be read back."""
    module = writing(Module("t", triple=triple), 2, 2, 9)
    assert build_and_run(module, triple, tmp_path / "out") == 9


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_write_lands_where_the_offset_says(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The byte beside the one written is the one it was.

    A write that lost its offset would land here instead, and this is what
    says it did not.
    """
    module = writing(Module("t", triple=triple), 2, 0, 9)
    assert build_and_run(module, triple, tmp_path / "out") == CONTENTS & 0xFF


# -- what the representation allows ----------------------------------------------

def test_an_address_is_moved_by_a_number_of_bytes_and_not_by_an_address() -> None:
    """An address is not a number, and what is added to it is not an address."""
    module = Module("t")
    func, var, _ = with_a_variable(module)
    module.add_function(func)
    module.startup = func
    block = func.blocks[0]
    address = block.append(AddressInst(var))
    block.append(BinaryInst(BinOp.ADD, address, address, ty=address.ty))
    block.append(RetInst(module.int_const(U8, 0)))
    with pytest.raises(InternalError, match="moving an address by"):
        verify(module)


def test_only_an_address_is_read_as_another_type() -> None:
    """The same bits in another register bank is a question this does not ask."""
    module = Module("t")
    func, var, _ = with_a_variable(module)
    module.add_function(func)
    module.startup = func
    block = func.blocks[0]
    address = block.append(AddressInst(var))
    block.append(RetInst(block.append(
        CastInst(CastKind.BITCAST, address, U8))))
    with pytest.raises(InternalError, match="not the same kind of thing"):
        verify(module)
