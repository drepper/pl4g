"""The register allocator: what it reads, what it decides, and what it refuses."""

from __future__ import annotations

import subprocess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for
from pypl4g.mc.desc import InstFlags, InstrTable, OperandRole
from pypl4g.mc.inst import MCInst
from pypl4g.mc.machine import MachineFunction
from pypl4g.mc.operand import MCImm, MCMem, MCOperand, MCReg
from pypl4g.mc.regalloc import (LinearScan, RegisterPressureError, allocate,
                                defs_and_uses)
from pypl4g.mc.reg import PhysReg, VirtReg
from pypl4g.target.x86_64.abi import CC_PL4G
from pypl4g.target.x86_64.isel import X86Selector
from pypl4g.target.x86_64.opcodes import X86_INSTRS
from pypl4g.target.x86_64.regs import GPR, INFO, reg

TABLE = InstrTable(X86_INSTRS)
ORDER = CC_PL4G.allocation_order
#: What the allocator asks how to reach the frame.
SELECTOR = X86Selector(TABLE)


def virtual(bits: int = 32, hint: PhysReg | None = None) -> VirtReg:
    """A fresh virtual register of the given width."""
    return INFO.new_virtual(GPR, bits, hint=hint)


def inst(mnemonic: str, *operands: MCOperand) -> MCInst:
    """Select *mnemonic* for the given operands."""
    return MCInst(desc=TABLE.select(mnemonic, operands), operands=operands)


def function(*instructions: MCInst) -> MachineFunction:
    """A one-block function holding the given instructions."""
    built = MachineFunction(name="t")
    block = built.add_block("entry")
    for one in instructions:
        block.append(one)
    return built


def assigned(function_: MachineFunction) -> list[MCInst]:
    """Allocate and return the instructions as they came out."""
    allocate(function_, INFO, ORDER, SELECTOR)
    return function_.instructions()


# -- what an instruction does with its operands ---------------------------------

def test_a_move_writes_its_first_operand_and_reads_the_second() -> None:
    """The convention the builder speaks in, which the rows follow."""
    left, right = virtual(), virtual()
    defs, uses = defs_and_uses(inst("mov", MCReg(left), MCReg(right)))
    assert defs == [left]
    assert uses == [right]


def test_a_two_operand_arithmetic_reads_the_operand_it_writes() -> None:
    """The old value is still wanted when the instruction runs, which is what
    keeps the allocator from handing that register to something else."""
    left, right = virtual(), virtual()
    added = inst("add", MCReg(left), MCReg(right))
    assert added.desc.role_of(0) is OperandRole.DEF_USE
    defs, uses = defs_and_uses(added)
    assert left in defs, "the destination is not written"
    assert set(uses) == {left, right}


def test_a_store_writes_nothing_it_names() -> None:
    """Its first operand is a place in memory, not a register it fills."""
    value = virtual()
    base = virtual(64)
    stored = inst("mov", MCMem(base=base, size_bits=32), MCReg(value))
    defs, uses = defs_and_uses(stored)
    assert defs == [], "a store was read as writing a register"
    assert set(uses) == {base, value}


def test_a_register_inside_an_address_is_read_wherever_it_stands() -> None:
    """Computing the address reads it whatever the instruction then does."""
    destination, base = virtual(), virtual(64)
    loaded = inst("mov", MCReg(destination), MCMem(base=base, size_bits=32))
    defs, uses = defs_and_uses(loaded)
    assert defs == [destination]
    assert uses == [base]


def test_what_an_instruction_touches_besides_its_operands_counts() -> None:
    """The flags are written by arithmetic and the rows say so."""
    defs, _ = defs_and_uses(inst("add", MCReg(virtual()), MCReg(virtual())))
    assert any(isinstance(d, PhysReg) and d.name == "eflags" for d in defs)


# -- what it decides ------------------------------------------------------------

def test_two_values_wanted_at_once_get_different_registers() -> None:
    """This is the whole of what the allocator is for."""
    first, second = virtual(), virtual()
    built = function(
        inst("mov", MCReg(first), MCImm(1, 32)),
        inst("mov", MCReg(second), MCImm(2, 32)),
        inst("add", MCReg(first), MCReg(second)))
    out = assigned(built)
    one = out[0].operands[0]
    other = out[1].operands[0]
    assert isinstance(one, MCReg) and isinstance(other, MCReg)
    assert isinstance(one.reg, PhysReg) and isinstance(other.reg, PhysReg)
    assert one.reg.unit is not other.reg.unit


def test_a_register_is_given_again_once_nothing_wants_it() -> None:
    """A value that is read and never read again frees what held it."""
    first, second = virtual(), virtual()
    built = function(
        inst("mov", MCReg(first), MCImm(1, 32)),
        inst("mov", MCMem(disp_sym=None, disp=8, size_bits=32), MCReg(first)),
        inst("mov", MCReg(second), MCImm(2, 32)))
    out = assigned(built)
    one = out[0].operands[0]
    other = out[2].operands[0]
    assert isinstance(one, MCReg) and isinstance(other, MCReg)
    assert one.reg is other.reg, "a register nothing wanted was not given again"


def test_an_instruction_may_write_the_register_it_read() -> None:
    """One instruction is two points, a read and then a write.

    Without that a value could never be moved into the register it is read from,
    which is what makes hinting a value towards where it is wanted worth doing.
    """
    value = virtual()
    target = reg("eax")
    built = function(
        inst("mov", MCReg(value), MCImm(1, 32)),
        inst("mov", MCReg(target), MCReg(value)))
    scan = LinearScan(INFO, ORDER, SELECTOR)
    result = scan.run(built)
    assert result.units[value.ident] is target.unit


def test_a_value_hinted_where_it_is_wanted_needs_no_move() -> None:
    """Granting the hint turns the move into one of a register to itself."""
    target = reg("eax")
    value = virtual(hint=target)
    built = function(
        inst("mov", MCReg(value), MCImm(7, 32)),
        inst("mov", MCReg(target), MCReg(value)))
    result = LinearScan(INFO, ORDER, SELECTOR).run(built)
    assert result.coalesced == 1
    assert [i.mnemonic for i in built.instructions()] == ["mov"]
    remaining = built.instructions()[0].operands[0]
    assert isinstance(remaining, MCReg) and remaining.reg is target


def test_a_register_something_else_holds_is_not_given_out() -> None:
    """A value live across a point that wants a particular register is put
    somewhere else rather than being moved out of the way."""
    held = reg("ecx")
    value = virtual()
    built = function(
        inst("mov", MCReg(value), MCImm(1, 32)),
        inst("mov", MCReg(held), MCImm(2, 32)),
        inst("add", MCReg(value), MCReg(held)))
    result = LinearScan(INFO, ORDER, SELECTOR).run(built)
    assert result.units[value.ident] is not held.unit


def test_an_operand_may_name_a_narrower_part_of_the_register() -> None:
    """A byte store reads the byte view of wherever the value was computed."""
    value = virtual()
    built = function(
        inst("mov", MCReg(value), MCImm(1, 32)),
        inst("mov", MCMem(disp=8, size_bits=8), MCReg(value, bits=8)))
    out = assigned(built)
    wide = out[0].operands[0]
    narrow = out[1].operands[1]
    assert isinstance(wide, MCReg) and isinstance(narrow, MCReg)
    assert isinstance(wide.reg, PhysReg) and isinstance(narrow.reg, PhysReg)
    assert narrow.reg.bits == 8 and wide.reg.bits == 32
    assert narrow.reg.unit is wide.reg.unit, "the two views name different registers"


def test_a_register_inside_an_address_is_assigned_too() -> None:
    """It is a value like any other, which is what lets the backends stop
    setting one aside that nothing else may then use."""
    base = virtual(64)
    built = function(
        inst("lea", MCReg(base), MCMem(disp=16, rip_relative=True)),
        inst("mov", MCReg(virtual()), MCMem(base=base, size_bits=32)))
    out = assigned(built)
    address = out[1].operands[1]
    assert isinstance(address, MCMem)
    assert isinstance(address.base, PhysReg), "the base was left unassigned"


# -- what it refuses ------------------------------------------------------------

def test_more_values_than_registers_go_to_the_frame() -> None:
    """What used to be refused is now put on the stack and read back."""
    values = [virtual() for _ in range(len(ORDER) + 4)]
    instructions = [inst("mov", MCReg(v), MCImm(1, 32)) for v in values]
    # Reading them all at the end is what keeps every one of them wanted.
    instructions += [inst("mov", MCMem(disp=8, size_bits=32), MCReg(v))
                     for v in reversed(values)]
    built = function(*instructions)
    result = allocate(built, INFO, ORDER, SELECTOR)
    assert result.spilled, "nothing was spilled although there were too many"
    assert built.frame.slots == len(result.spilled)
    assert built.virtual_registers() == [], "something was left unassigned"


def test_the_frame_is_as_large_as_the_slots_taken_and_aligned() -> None:
    """A stack pointer that is not aligned is a fault on two of the three."""
    from pypl4g.mc.machine import FrameInfo

    frame = FrameInfo()
    assert frame.size == 0, "a function that needed no stack made a frame"
    first, second = frame.allocate(), frame.allocate()
    assert (first, second) == (0, 8), "slots overlap or are not the width of one"
    assert frame.size == 16
    frame.allocate()
    assert frame.size == 32, "the size was not rounded up to the alignment"


def test_a_register_that_may_not_be_spilled_is_kept() -> None:
    """Two instructions whose relocations refer to each other must stay next to
    each other, so the register held between them cannot go to the frame."""
    held = INFO.new_virtual(GPR, 64, spillable=False)
    values = [virtual() for _ in range(len(ORDER) + 2)]
    instructions = [inst("mov", MCReg(v), MCImm(1, 32)) for v in values]
    instructions.append(inst("mov", MCReg(held), MCImm(1, 32)))
    instructions.append(inst("mov", MCMem(base=held, size_bits=32), MCReg(values[0])))
    instructions += [inst("mov", MCMem(disp=8, size_bits=32), MCReg(v))
                     for v in reversed(values)]
    built = function(*instructions)
    result = allocate(built, INFO, ORDER, SELECTOR)
    assert held.ident not in result.spilled, "the register that may not go went"
    assert result.spilled, "nothing else went either"


def test_nothing_is_left_unassigned() -> None:
    """The encoders refuse a virtual register, so this is their contract."""
    built = function(
        inst("mov", MCReg(virtual()), MCImm(1, 32)),
        inst("mov", MCReg(virtual()), MCImm(2, 32)))
    allocate(built, INFO, ORDER, SELECTOR)
    assert built.virtual_registers() == []


def test_only_a_move_of_a_register_to_itself_is_dropped() -> None:
    """A move between two registers is not an identity and has to stay."""
    left, right = reg("eax"), reg("ecx")
    built = function(inst("mov", MCReg(left), MCReg(right)))
    assert LinearScan(INFO, ORDER, SELECTOR).run(built).coalesced == 0
    assert len(built.instructions()) == 1


def test_the_move_flag_is_what_marks_one() -> None:
    """A pass asks the row rather than the mnemonic."""
    assert InstFlags.MOVE in TABLE.select(
        "mov", (MCReg(reg("eax")), MCReg(reg("ecx")))).flags
    assert InstFlags.MOVE not in TABLE.select(
        "add", (MCReg(reg("eax")), MCReg(reg("ecx")))).flags


# -- through the compiler -------------------------------------------------------

PRESSURE = """let a: u8 = 1u8
let b: u8 = 2u8
let c: u8 = 3u8

let wa: mut u8 = 0u8

@[expect(4007)]
let wb: mut u8 = 0u8

@[expect(4007)]
let wc: mut u8 = 0u8

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u8:
    let va: u8 = a
    let vb: u8 = b
    let vc: u8 = c
    wc \N{LEFTWARDS ARROW} vc
    wb \N{LEFTWARDS ARROW} vb
    wa \N{LEFTWARDS ARROW} va
    wa
"""


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_value_read_first_survives_longest(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Written back in the reverse of the order they were read, so the first is
    wanted across every other one."""
    source = tmp_path / "t.pl4g"
    source.write_text(PRESSURE, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)), str(source)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for(triple), str(output)], capture_output=True,
                         timeout=60)
    assert ran.returncode == 1, describe(ran)


@pytest.mark.parametrize("triple", compiler_targets())
def test_far_more_values_than_registers_still_runs(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Forty values at once is more than any of the three has registers for.

    The first value read is the last written back and is what the program
    returns, so it has to survive the whole function -- through the frame, since
    it cannot have stayed in a register.
    """
    names = [f"v{n}" for n in range(40)]
    lines = ["".join(("let g", n, ": u8 = 1u8")) for n in names]
    # The one the program returns is read, so it is the one with no warning
    # about nothing reading it; every other is written and never looked at.
    lines += ["".join(("let w", n, ": mut u8 = 0u8") if n == names[0]
                      else ("@[expect(4007)]\nlet w", n, ": mut u8 = 0u8"))
              for n in names]
    lines += ["@[startup, impure]", "fn main() \N{RIGHTWARDS ARROW} u8:"]
    lines += ["".join(("    let ", n, ": u8 = g", n)) for n in names]
    lines += ["".join(("    w", n, " \N{LEFTWARDS ARROW} ", n))
              for n in reversed(names)]
    lines.append("    wv0")
    source = tmp_path / "t.pl4g"
    source.write_text("\n".join(lines), encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for(triple), str(output)], capture_output=True,
                         timeout=60)
    assert ran.returncode == 1, describe(ran)


def test_a_value_read_twice_running_is_read_from_the_frame_once() -> None:
    """The register it is already in serves the second read.

    This is the whole of what splitting a range comes to here: the register is
    held across exactly as many instructions as are reading the value, and no
    further.
    """
    values = [virtual() for _ in range(len(ORDER) + 2)]
    instructions = [inst("mov", MCReg(v), MCImm(1, 32)) for v in values]
    # Everything else dies here, leaving the first value as the one spilled.
    instructions += [inst("mov", MCMem(disp=8, size_bits=32), MCReg(v))
                     for v in values[1:]]
    # Then it is read three times running.
    instructions += [inst("mov", MCMem(disp=16, size_bits=32), MCReg(values[0]))
                     for _ in range(3)]
    built = function(*instructions)
    result = allocate(built, INFO, ORDER, SELECTOR)
    assert values[0].ident in result.spilled, "the value under test was not spilled"
    slot = result.spilled[values[0].ident]
    reloads = [i for i in built.instructions()
               if _reads_slot(i, slot)]
    assert len(reloads) == 1, \
        "".join((str(len(reloads)), " reloads where one serves all three reads"))


def _reads_slot(inst: MCInst, slot: int) -> bool:
    """Whether *inst* reads the frame slot at *slot* into a register."""
    if len(inst.operands) != 2:
        return False
    destination, source = inst.operands
    return (isinstance(destination, MCReg) and isinstance(source, MCMem)
            and source.base is not None and source.disp == slot
            and getattr(source.base, "name", "") == "rsp")


def test_a_value_used_right_after_it_is_computed_is_not_read_back() -> None:
    """It is written to the frame and then used from the register it came from."""
    values = [virtual() for _ in range(len(ORDER) + 2)]
    instructions = [inst("mov", MCReg(v), MCImm(1, 32)) for v in values]
    instructions += [inst("mov", MCMem(disp=8, size_bits=32), MCReg(v))
                     for v in reversed(values)]
    built = function(*instructions)
    result = allocate(built, INFO, ORDER, SELECTOR)
    assert result.spilled
    # Every spilled value here is written once and read once, so the number of
    # reloads can never be more than the number of values that went.
    reloads = sum(1 for i in built.instructions()
                  for slot in result.spilled.values() if _reads_slot(i, slot))
    assert reloads <= len(result.spilled), reloads



# -- more than one kind of register ---------------------------------------------

def test_the_two_kinds_do_not_take_registers_from_each_other() -> None:
    """An integer cannot live in a floating-point register nor the other way
    round, whatever the widths say.

    Until there was a second kind the allocator had one list and gave from it,
    which was right while every value was an integer and would silently have
    been wrong the moment one was not.  Now it has a list per kind and asks the
    value which it belongs to -- so one kind running out says nothing about the
    other, which is the point of keeping the lists apart.
    """
    from pypl4g.mc.regalloc import Assignment, LiveRange
    from pypl4g.target.x86_64.regs import VEC

    orders = {GPR.name: list(CC_PL4G.allocation_order)[:1],
              VEC.name: INFO.members_of(VEC)[:1]}
    scan = LinearScan(INFO, orders, SELECTOR)
    whole = INFO.new_virtual(GPR, 64)
    fractional = INFO.new_virtual(VEC, 64)
    # Both are wanted over the same stretch, so one list of one register would
    # have had to send one of them to the frame.
    ranges = [LiveRange(reg=whole, start=0, end=10),
              LiveRange(reg=fractional, start=0, end=10)]
    assignment = Assignment()
    assert scan._assign(ranges, [], assignment) == []
    assert assignment.units[whole.ident] is orders[GPR.name][0]
    assert assignment.units[fractional.ident] is orders[VEC.name][0]


def test_a_target_with_one_kind_needs_no_list_of_lists() -> None:
    """Handing the allocator a plain list still works and means what it did:
    every value belongs to the one kind there is.  That is what keeps this
    change from reaching a target that has nothing to say about it."""
    from pypl4g.mc.regalloc import Assignment, LiveRange

    scan = LinearScan(INFO, ORDER, SELECTOR)
    whole = INFO.new_virtual(GPR, 64)
    assignment = Assignment()
    assert scan._assign([LiveRange(reg=whole, start=0, end=4)], [], assignment) == []
    assert assignment.units[whole.ident] in ORDER
