"""Every frame size, on the two architectures whose offsets reach only so far.

An AArch64 load or store carries twelve bits of offset, counted in what it
reads, and what moves the stack pointer twelve bits -- or twelve more a page at a
time; a RISC-V one twelve signed bits.  Past that a place is built in a register
the function sets aside, and the builder sets it aside only in a function whose
frame turned out to need it, allocating that function again.
"""

from __future__ import annotations

import pytest

from pypl4g.mc.asmbuilder import FrameOutOfReach
from pypl4g.source.location import INVALID_SPAN
from pypl4g.target.aarch64.isel import FRAME_SPARE, A64Selector
from pypl4g.target.aarch64.regs import reg as a64_reg
from pypl4g.target.riscv64.isel import RVSelector
from pypl4g.target.riscv64.regs import SPARE, reg as rv_reg


def mnemonics(insts) -> list[str]:  # noqa: ANN001
    """What each instruction is, by the name of its row."""
    return [inst.desc.mnemonic for inst in insts]


def test_a_frame_of_a_page_or_less_is_one_instruction() -> None:
    """Twelve bits, as before."""
    assert mnemonics(A64Selector().select_frame(4080, INVALID_SPAN)) == ["sub"]


def test_a_frame_of_up_to_sixteen_megabytes_needs_no_register() -> None:
    """The pages and the rest, two instructions, and nothing set aside."""
    found = A64Selector().select_frame(70000, INVALID_SPAN)
    assert mnemonics(found) == ["sub.lsl12", "sub"]
    assert mnemonics(A64Selector().select_unframe(70000, INVALID_SPAN)) \
        == ["add.lsl12", "add"]
    assert mnemonics(A64Selector().select_frame(65536, INVALID_SPAN)) == ["sub.lsl12"]


def test_a_larger_frame_asks_for_the_register() -> None:
    """Without one set aside the builder is told; with one, the number is built
    in it and taken off the stack pointer in the form that reads register 31 as
    the stack pointer."""
    selector = A64Selector()
    with pytest.raises(FrameOutOfReach):
        selector.select_frame(20_000_000, INVALID_SPAN)
    selector.spare = FRAME_SPARE
    found = selector.select_frame(20_000_000, INVALID_SPAN)
    assert mnemonics(found)[-1] == "sub.uxtx"
    assert all(operand.reg is FRAME_SPARE for operand in found[-1].operands[2:])


def test_a_slot_past_the_reach_of_a_store_is_reached_through_the_register() -> None:
    """A word's offset reaches 32760 bytes; one past it is built in the register
    set aside, and a load builds it in its own destination instead."""
    selector = A64Selector()
    source = a64_reg("x3")
    assert mnemonics(selector.select_spill(32760, source, INVALID_SPAN)) == ["str"]
    with pytest.raises(FrameOutOfReach):
        selector.select_spill(32768, source, INVALID_SPAN)
    assert mnemonics(selector.select_reload(source, 32768, INVALID_SPAN)) \
        == ["add.lsl12", "ldr"]
    selector.spare = FRAME_SPARE
    stored = selector.select_spill(40000, source, INVALID_SPAN)
    assert mnemonics(stored) == ["add.lsl12", "add", "str"]
    assert stored[-1].operands[1].reg is FRAME_SPARE


def test_risc_v_asks_for_its_register_past_2047() -> None:
    """The one a function working on runs keeps already."""
    selector = RVSelector()
    selector.spare = None
    assert mnemonics(selector.select_frame(2048, INVALID_SPAN)) == ["addi"]
    with pytest.raises(FrameOutOfReach):
        selector.select_frame(4096, INVALID_SPAN)
    with pytest.raises(FrameOutOfReach):
        selector.select_spill(3000, rv_reg("a0"), INVALID_SPAN)
    selector.spare = SPARE
    found = selector.select_frame(4096, INVALID_SPAN)
    assert mnemonics(found)[-1] == "sub"
    assert selector.frame_spare() is SPARE
