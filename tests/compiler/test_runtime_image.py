"""Calling into the packaged runtime from a generated image.

The runtime is C, compiled ahead of time; placing it means putting its bytes in
the image, defining the names in them, and turning what the object format said
had to be filled in into the fixups the compiler already applies.  These build
the representation directly and take it through code generation and the image
writer -- the same path the driver takes -- and then run the result, which is
the only way to find out that a relocation was filled in correctly.

The language has no way to write any of this yet; `@[external]` is what will.
"""

from __future__ import annotations

import stat
import subprocess

import pytest

from conftest import check_conformance, compiler_targets, describe, runner_for
from pypl4g.diag.engine import collecting_engine
from pypl4g.elf.layout import ImageKind
from pypl4g.elf.writer import ImageSettings, write_image
from pypl4g.ir.function import (DEFAULT_CCONV, SYSTEM_CCONV, FuncAttrs,
                                Function, SpecialKind)
from pypl4g.ir.inst import (AddressInst, CallInst, CastInst, CastKind,
                            MemStartInst, RetInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import I32, I64, U8, U64
from pypl4g.ir.verify import verify
from pypl4g.mc.streamer import MCStreamer
from pypl4g.runtime import blob_for
from pypl4g.target.registry import architecture_of
from pypl4g.target.registry import lookup as lookup_target

#: What the program writes, and where.  Standard error rather than standard
#: output so that a run under the suite says nothing a reader has to filter.
MESSAGE = b"ring\n"
STANDARD_ERROR = 2

#: Which direction the runtime numbers a write as.
WRITING = 1

#: How many machine words the runtime's ring is, which the runtime itself says.
#: Room of the right size, zeroed, is what it asks for and all it asks for.
RING_WORDS = 27


def writing(module: Module) -> Module:
    """A program that hands the runtime a ring and a message and exits with
    however many bytes it says went."""
    ring = GlobalVar("ring", U64, module.types.ptr_type(U64, mutable=True),
                     module.int_const(U64, 0))
    module.add_global(ring)
    # The rest of the ring, which the runtime reads and writes and the program
    # never looks at.  One variable each rather than an array, so that nothing
    # here depends on how an array of words is laid out.
    for index in range(1, RING_WORDS):
        module.add_global(GlobalVar(
            "".join(("ring", str(index))), U64,
            module.types.ptr_type(U64, mutable=True), module.int_const(U64, 0)))
    text = GlobalVar("message", U64, module.types.ptr_type(U64, mutable=True),
                     module.int_const(U64, int.from_bytes(
                         MESSAGE.ljust(8, b"\0"), "little")))
    module.add_global(text)

    place = module.types.ptr_type(U64, mutable=True)
    started = Function(
        "pl4g_io_submit",
        module.types.func_type((place, I32, I32, U64, U64), I64),
        FuncAttrs(external="pl4g_io_submit", impure=True),
        cconv=SYSTEM_CCONV)
    module.add_function(started)
    waited = Function(
        "pl4g_io_wait", module.types.func_type((place, I64), I64),
        FuncAttrs(external="pl4g_io_wait", impure=True), cconv=SYSTEM_CCONV)
    module.add_function(waited)

    main = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP, impure=True),
                    cconv=DEFAULT_CCONV)
    module.add_function(main)
    module.startup = main
    block = main.add_block()
    token = block.append(MemStartInst())
    del token
    slot = block.append(CallInst(
        started,
        (block.append(AddressInst(ring)),
         module.int_const(I32, WRITING),
         module.int_const(I32, STANDARD_ERROR),
         block.append(CastInst(CastKind.BITCAST,
                               block.append(AddressInst(text)), U64)),
         module.int_const(U64, len(MESSAGE))),
        I64))
    answer = block.append(CallInst(
        waited, (block.append(AddressInst(ring)), slot), I64))
    block.append(RetInst(block.append(
        CastInst(CastKind.TRUNC, answer, U8))))
    verify(module)
    return module


def build_and_run(module: Module, triple: str, path) -> subprocess.CompletedProcess:  # noqa: ANN001
    """Generate, write and run the image, and answer what the run said."""
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    asm = target.new_assembler(streamer, 0)
    target.generate(module, asm, engine, 0)
    assert [d.info.number for d in collected] == [], \
        "".join((triple, ": ", "; ".join(d.info.name for d in collected)))
    defaults = target.image_defaults()
    settings = ImageSettings(machine=defaults.machine,
                             base_vaddr=defaults.base_vaddr,
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
    return subprocess.run([*runner_for(triple), str(path)],
                          capture_output=True, timeout=60)


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_runtime_writes_what_it_was_given(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Which is the whole of it: the bytes were placed, the names defined, and
    every relocation filled in with an address that turned out to be right."""
    module = writing(Module("t", triple=triple))
    proc = build_and_run(module, triple, tmp_path / "out")
    assert proc.returncode == len(MESSAGE), describe(proc)
    assert proc.stderr == MESSAGE, describe(proc)


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_does_not_reach_it_carries_none_of_it(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """The runtime is placed where something calls it and nowhere else."""
    module = Module("t", triple=triple)
    main = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    module.add_function(main)
    module.startup = main
    block = main.add_block()
    block.append(RetInst(module.int_const(U8, 0)))
    verify(module)
    proc = build_and_run(module, triple, tmp_path / "out")
    assert proc.returncode == 0, describe(proc)
    blob = blob_for(architecture_of(triple))
    assert blob is not None
    held = (tmp_path / "out").read_bytes()
    assert blob.pieces[0].contents[:32] not in held
