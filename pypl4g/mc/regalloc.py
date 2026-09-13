"""Giving every value a register.

Instruction selection names each value it computes with a virtual register and
says nothing about where it lives.  This decides that, and the encoders refuse a
virtual register, so nothing can quietly skip it.

The method is linear scan.  Every instruction of the function is given a
position, each register gets the range between the first position that writes it
and the last that reads it, and the ranges are walked in order of their start,
holding a unit for as long as a range needs one and releasing it as soon as the
range ends.  That is a great deal less than a colouring allocator would do, and
it is the right amount for straight-line code, which is all the language can
express: with no branches there is no interference graph to speak of, only an
interval on a line.

Two things it does not do, deliberately.

It does not spill.  A function needing more registers than the target has is
reported rather than compiled wrongly, which is how everything else this
compiler cannot yet do behaves.  Spilling needs a stack frame, and the frame is
worth designing once, with the unwinder that also needs one, rather than twice.

It does not split a range.  A value is in one unit for its whole life, so a
value that is live across a point where the convention demands a particular
register is simply not given that register.  Nothing yet produces such a point
except the move to the register a result is returned in, which nothing outlives.
"""

from dataclasses import dataclass, field
from typing import Sequence

from .desc import OperandRole
from .inst import MCInst
from .machine import MachineFunction
from .operand import MCMem, MCOperand, MCReg
from .reg import PhysReg, Reg, RegisterInfo, RegUnit, VirtReg


class RegisterPressureError(Exception):
    """More values are wanted at once than the target has registers."""

    def __init__(self, function: str, wanted: int, available: int) -> None:
        super().__init__("".join((
            "a function needing ", str(wanted), " values at once, which is more than "
            "the ", str(available), " registers this compiler has to give and it "
            "cannot spill yet")))
        self.function = function
        self.wanted = wanted
        self.available = available


@dataclass(slots=True)
class LiveRange:
    """Where a register starts being wanted and where it stops."""

    reg: Reg
    start: int
    end: int

    def covers(self, other: "LiveRange") -> bool:
        """Whether the two ranges are wanted at the same time."""
        return self.start <= other.end and other.start <= self.end


@dataclass(slots=True)
class Assignment:
    """What the allocator decided, for the dump and for the tests."""

    #: The unit chosen for each virtual register, by its identity.
    units: dict[int, RegUnit] = field(default_factory=dict)
    #: Ranges in the order they were considered, for reading a decision back.
    ranges: list[LiveRange] = field(default_factory=list)
    #: Moves removed because the allocator put both ends in one register.
    coalesced: int = 0


def registers_of(operand: MCOperand) -> list[tuple[Reg, bool]]:
    """Every register an operand names, and whether the operand may write it.

    A register inside a memory operand is read wherever the operand stands: the
    address is computed from it whatever the instruction then does with the place
    it names.  That is why this asks the operand and not only the slot.
    """
    match operand:
        case MCReg():
            return [(operand.reg, True)]
        case MCMem():
            found: list[tuple[Reg, bool]] = []
            if operand.base is not None:
                found.append((operand.base, False))
            if operand.index is not None:
                found.append((operand.index, False))
            return found
        case _:
            return []


def defs_and_uses(inst: MCInst) -> tuple[list[Reg], list[Reg]]:
    """The registers an instruction writes and the registers it reads."""
    defs: list[Reg] = list(inst.desc.implicit_defs)
    uses: list[Reg] = list(inst.desc.implicit_uses)
    for index, operand in enumerate(inst.operands):
        role = inst.desc.role_of(index)
        for reg, writable in registers_of(operand):
            if writable and role is not OperandRole.USE:
                defs.append(reg)
            if not writable or role is not OperandRole.DEF:
                uses.append(reg)
    return defs, uses


class LinearScan:
    """Assigns a register to every virtual register of a function."""

    def __init__(self, registers: RegisterInfo,
                 order: Sequence[RegUnit]) -> None:
        self._registers = registers
        #: The units that may be given out, in the order they are preferred.
        #: The convention states it, since which registers are free to use is
        #: what a convention is about.
        self._order = list(order)

    def run(self, function: MachineFunction) -> Assignment:
        """Assign every virtual register of *function* and rewrite it."""
        assignment = Assignment()
        instructions = function.instructions()
        ranges, blocked = self._live_ranges(instructions)
        assignment.ranges = ranges
        self._assign(function.name, ranges, blocked, assignment)
        self._rewrite(function, assignment)
        assignment.coalesced = self._drop_identity_moves(function)
        return assignment

    # -- liveness --------------------------------------------------------------

    def _live_ranges(self, instructions: Sequence[MCInst]
                     ) -> tuple[list[LiveRange], list[LiveRange]]:
        """The range of every register, virtual and physical.

        Positions run over the instructions in the order they are laid out.
        That is the order control takes while it only falls through, which is
        all there is today; the entry that adds a branch backwards has to
        replace this with an analysis over the control-flow graph, and until
        then a range computed this way covers every point between a write and a
        read whichever way control reaches them.
        """
        first: dict[int, int] = {}
        last: dict[int, int] = {}
        seen: dict[int, Reg] = {}
        for position, inst in enumerate(instructions):
            defs, uses = defs_and_uses(inst)
            # One instruction is two points: it reads at the first and writes at
            # the second.  That is not a detail -- it is what lets the register a
            # value is read from be the register the same instruction writes,
            # which is how a move into the register a result is returned in ends
            # up moving a register to itself and going away.
            for reg in uses:
                key = self._key(reg)
                seen.setdefault(key, reg)
                first.setdefault(key, position * 2)
                last[key] = position * 2
            for reg in defs:
                key = self._key(reg)
                seen.setdefault(key, reg)
                first.setdefault(key, position * 2 + 1)
                last[key] = max(last.get(key, 0), position * 2 + 1)
        virtual: list[LiveRange] = []
        physical: list[LiveRange] = []
        for key, reg in seen.items():
            found = LiveRange(reg=reg, start=first[key], end=last[key])
            (virtual if isinstance(reg, VirtReg) else physical).append(found)
        virtual.sort(key=lambda r: (r.start, r.end))
        return virtual, physical

    def _key(self, reg: Reg) -> int:
        """What makes two register operands the same storage.

        Two views of one unit are one register here: writing ``al`` and reading
        ``eax`` is one value in one place, and an allocator that treated them as
        two would hand the same unit to something else in between.
        """
        return id(reg) if isinstance(reg, VirtReg) else id(reg.unit)

    # -- assignment ------------------------------------------------------------

    def _assign(self, name: str, ranges: Sequence[LiveRange],
                blocked: Sequence[LiveRange], assignment: Assignment) -> None:
        """Give each range a unit, releasing one as soon as its range ends."""
        active: list[tuple[LiveRange, RegUnit]] = []
        for found in ranges:
            active = [(r, u) for r, u in active if r.end >= found.start]
            taken = {u for _, u in active}
            taken.update(self._blocked_by(found, blocked))
            unit = self._choose(found, taken)
            if unit is None:
                raise RegisterPressureError(name, len(taken) + 1, len(self._order))
            assert isinstance(found.reg, VirtReg)
            assignment.units[found.reg.ident] = unit
            active.append((found, unit))

    def _blocked_by(self, found: LiveRange,
                    blocked: Sequence[LiveRange]) -> set[RegUnit]:
        """The units a physical register holds while *found* is wanted."""
        units: set[RegUnit] = set()
        for other in blocked:
            if other.covers(found) and isinstance(other.reg, PhysReg):
                units.add(other.reg.unit)
        return units

    def _choose(self, found: LiveRange, taken: set[RegUnit]) -> RegUnit | None:
        """The unit to give *found*, preferring the one it asked for.

        A hint is where the value is wanted anyway -- the register a result is
        returned in, most often -- so taking it turns the move that would put it
        there into a move of a register to itself, which then goes.
        """
        reg = found.reg
        if isinstance(reg, VirtReg) and reg.hint is not None:
            if reg.hint.unit not in taken and reg.hint.unit in self._order:
                return reg.hint.unit
        for unit in self._order:
            if unit not in taken:
                return unit
        return None

    # -- rewriting -------------------------------------------------------------

    def _rewrite(self, function: MachineFunction, assignment: Assignment) -> None:
        """Replace every virtual register with the one it was given."""
        for block in function.blocks:
            block.insts = [self._rewrite_inst(i, assignment) for i in block.insts]

    def _rewrite_inst(self, inst: MCInst, assignment: Assignment) -> MCInst:
        """One instruction, with its registers assigned."""
        operands = tuple(self._rewrite_operand(o, assignment) for o in inst.operands)
        if operands == inst.operands:
            return inst
        return MCInst(desc=inst.desc, operands=operands, span=inst.span,
                      prefixes=inst.prefixes)

    def _rewrite_operand(self, operand: MCOperand, assignment: Assignment) -> MCOperand:
        """One operand, with its registers assigned."""
        match operand:
            case MCReg(reg=VirtReg() as reg):
                return MCReg(self._physical(reg, operand.width, assignment))
            case MCMem():
                base = operand.base
                index = operand.index
                if not isinstance(base, VirtReg) and not isinstance(index, VirtReg):
                    return operand
                return MCMem(
                    base=(self._physical(base, base.bits, assignment)
                          if isinstance(base, VirtReg) else base),
                    index=(self._physical(index, index.bits, assignment)
                           if isinstance(index, VirtReg) else index),
                    scale=operand.scale, disp=operand.disp, disp_sym=operand.disp_sym,
                    seg=operand.seg, rip_relative=operand.rip_relative,
                    size_bits=operand.size_bits, signed=operand.signed)
            case _:
                return operand

    def _physical(self, reg: VirtReg, bits: int, assignment: Assignment) -> PhysReg:
        """The view of the assigned unit that the operand asked for."""
        unit = assignment.units.get(reg.ident)
        if unit is None:
            raise KeyError("".join(("no register was assigned to ", repr(reg))))
        return self._registers.view(unit, bits)

    def _drop_identity_moves(self, function: MachineFunction) -> int:
        """Remove a move of a register to itself, and say how many went.

        These are what a hint that was granted leaves behind, and removing them
        is the whole point of hinting.  It is safe even where the instruction
        widens what it writes: on the architectures that do, a narrow write
        already cleared the rest of the unit, and the value being moved got there
        by such a write, so there is nothing left for this one to clear.
        """
        removed = 0
        for block in function.blocks:
            kept: list[MCInst] = []
            for inst in block.insts:
                if self._is_identity_move(inst):
                    removed += 1
                    continue
                kept.append(inst)
            block.insts = kept
        return removed

    def _is_identity_move(self, inst: MCInst) -> bool:
        """Whether the instruction moves a register to itself and nothing else."""
        from .desc import InstFlags

        if InstFlags.MOVE not in inst.desc.flags or len(inst.operands) != 2:
            return False
        first, second = inst.operands
        if not isinstance(first, MCReg) or not isinstance(second, MCReg):
            return False
        return first.reg is second.reg and first.width == second.width


def allocate(function: MachineFunction, registers: RegisterInfo,
             order: Sequence[RegUnit]) -> Assignment:
    """Assign every virtual register of *function*."""
    return LinearScan(registers, order).run(function)
