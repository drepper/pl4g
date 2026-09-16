"""Reads and writes that say what another observer sees.

The memory token orders the accesses of one program; it says nothing about the
order something else sees them in, and a ring shared with the kernel is exactly
that question.  So a load may acquire and a store may release, and the three
back ends answer for their own machine: x86-64 already promises both and emits
what it would have emitted, AArch64 has an instruction for each, and RISC-V has
a fence.

The language has no way to write one yet, so these build the representation
directly and take it through code generation the way `test_places` does.
"""

from __future__ import annotations

import stat
import subprocess

import pytest

from conftest import check_conformance, compiler_targets, describe, runner_for
from pypl4g.diag.engine import collecting_engine
from pypl4g.elf.layout import ImageKind
from pypl4g.elf.writer import ImageSettings, write_image
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (AddressInst, CastInst, CastKind, LoadInst,
                            MemStartInst, Ordering, RetInst, StoreInst)
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import U8, U64
from pypl4g.ir.verify import verify
from pypl4g.diag.engine import InternalError
from pypl4g.mc.streamer import MCStreamer
from pypl4g.target.registry import lookup as lookup_target

#: What the variable holds before anything writes to it, chosen so that reading
#: the wrong place answers something other than what was written.
BEFORE = 0x5A

#: What the program writes and then reads back.
AFTER = 0x2B


def ordered(module: Module, writing: Ordering, reading: Ordering) -> Module:
    """A program that writes a byte and reads it back, with those orderings."""
    var = GlobalVar("v", U64, module.types.ptr_type(U64, mutable=True),
                    module.int_const(U64, BEFORE))
    module.add_global(var)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    module.add_function(func)
    module.startup = func
    block = func.add_block()
    token = block.append(MemStartInst())
    address = block.append(AddressInst(var))
    place = block.append(CastInst(CastKind.BITCAST, address,
                                  module.types.ptr_type(U8, mutable=True)))
    written = block.append(StoreInst(token, place, module.int_const(U8, AFTER),
                                     ordering=writing))
    block.append(RetInst(block.append(
        LoadInst(U8, (written, place), ordering=reading))))
    verify(module)
    return module


def generated(module: Module, triple: str) -> tuple[MCStreamer, object]:
    """Take the module through code generation for *triple*."""
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    asm = target.new_assembler(streamer, 0)
    target.generate(module, asm, engine, 0)
    assert [d.info.number for d in collected] == [], \
        "".join((triple, ": ", "; ".join(d.info.name for d in collected)))
    return streamer, target


def build_and_run(module: Module, triple: str, path) -> int:  # noqa: ANN001
    """Generate, write and run the image, and answer with its exit status."""
    streamer, target = generated(module, triple)
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
    proc = subprocess.run([*runner_for(triple), str(path)], capture_output=True,
                          timeout=60)
    assert proc.returncode >= 0, describe(proc)
    return proc.returncode


def mnemonics(module: Module, triple: str) -> list[str]:
    """Every instruction the module came to, in the order it was emitted.

    Everything of the program comes before the entry point the target appends,
    so what the ordered access turned into is at the front of this list.
    """
    streamer, _ = generated(module, triple)
    return [fragment.inst.desc.mnemonic
            for fragment in streamer.sections[".text"].fragments
            if getattr(fragment, "inst", None) is not None]


#: What each machine's answer to "release this write, acquire that read" is.
#: x86-64 promises both of its own accord, so it emits what it would have
#: emitted; AArch64 has an instruction for each; RISC-V has one fence before the
#: write and another after the read.
EXPECTED = {
    "x86_64-linux-none": ["lea", "mov", "movzx", "ret"],
    "aarch64-linux-none": ["adrp", "add.lo12", "movz", "stlrb", "ldarb", "ret"],
    "riscv64-linux-none": ["auipc.hi20", "addi.lo12", "fence", "li", "sb",
                           "lbu", "fence", "ret"],
}


@pytest.mark.parametrize("triple", compiler_targets())
def test_each_machine_says_it_its_own_way(triple: str) -> None:
    """Which is the whole of what an ordering costs, target by target."""
    module = ordered(Module("t", triple=triple), Ordering.RELEASE,
                     Ordering.ACQUIRE)
    assert mnemonics(module, triple)[:len(EXPECTED[triple])] == EXPECTED[triple]


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_plain_access_costs_no_instruction(triple: str) -> None:
    """The ordered ones are what differ, and only where the machine needs them."""
    plain = mnemonics(ordered(Module("t", triple=triple), Ordering.PLAIN,
                              Ordering.PLAIN), triple)
    assert "fence" not in plain
    assert "stlrb" not in plain and "ldarb" not in plain


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_ordered_write_and_read_still_answer_what_was_written(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """The ordering says what others see and nothing about the value."""
    module = ordered(Module("t", triple=triple), Ordering.RELEASE, Ordering.ACQUIRE)
    assert build_and_run(module, triple, tmp_path / "out") == AFTER


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_plain_write_and_read_answer_the_same(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """Which is what says the ordering changed nothing about the program."""
    module = ordered(Module("t", triple=triple), Ordering.PLAIN, Ordering.PLAIN)
    assert build_and_run(module, triple, tmp_path / "out") == AFTER


def test_the_textual_form_says_the_ordering() -> None:
    """And reads back the same, which is what the round trip demands."""
    from pypl4g.ir.printer import render_module
    from pypl4g.ir.reader import read_module
    text = render_module(ordered(Module("t"), Ordering.RELEASE,
                                 Ordering.ACQUIRE))
    assert "store.release.u8" in text
    assert "load.acquire.u8" in text
    assert render_module(read_module(text)) == text


def test_a_load_cannot_release() -> None:
    """What a read promises is about what comes after it, not before."""
    module = Module("t")
    with pytest.raises(InternalError) as raised:
        ordered(module, Ordering.PLAIN, Ordering.RELEASE)
    assert "a load cannot release" in str(raised.value)


def test_a_store_cannot_acquire() -> None:
    """And what a write promises is about what came before it."""
    module = Module("t")
    with pytest.raises(InternalError) as raised:
        ordered(module, Ordering.ACQUIRE, Ordering.PLAIN)
    assert "a store cannot acquire" in str(raised.value)
