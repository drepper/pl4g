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

Where there are not enough registers, a value is spilled: given a slot in the
function's frame, written there when it is computed and read back before each
use.  That is the whole of it -- the value is in memory for its whole life, and
what occupies a register is a fresh one that lives for the single instruction
that reads or writes it.  Splitting a range so that a value is in a register
where it is busy and in memory where it is not would generate better code and is
a great deal more machinery; this is the version that is obviously right, and
the shape of the loop is what makes the better version a change in one place.

Spilling is done by rewriting and starting again rather than by patching the
assignment as it goes.  A spill adds instructions, which moves every position
after it and so changes every range; recomputing is simpler than repairing, and
a function is small.

One thing it does not do: split a range.  A value is in one place for its whole
life, so a value live across a point where the convention demands a particular
register is simply not given that register.  Nothing yet produces such a point
except the move to the register a result is returned in, which nothing outlives.
"""

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from .desc import OperandRole
from .inst import MCInst
from .machine import MachineFunction
from .operand import MCMem, MCOperand, MCReg
from ..source.location import Span
from .reg import PhysReg, Reg, RegisterInfo, RegUnit, VirtReg


class RegisterPressureError(Exception):
    """Not even spilling made the values fit.

    Reaching this means one instruction wants more registers at once than the
    target has, which no amount of spilling can help with: the operands of a
    single instruction all have to be somewhere at the same moment.
    """

    def __init__(self, function: str, wanted: int, available: int) -> None:
        super().__init__("".join((
            "a function needing ", str(wanted), " values at once, which is more than "
            "the ", str(available), " registers this compiler has to give, and which "
            "spilling cannot help with")))
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
    #: The virtual registers that had to go to the frame, by their identity.
    spilled: dict[int, int] = field(default_factory=dict)


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

    def __init__(self, registers: RegisterInfo, order: Sequence[RegUnit],
                 selector: "SpillSelector") -> None:
        self._registers = registers
        #: What the target says a read or a write of a frame slot looks like.
        self._selector = selector
        #: The units that may be given out, in the order they are preferred.
        #: The convention states it, since which registers are free to use is
        #: what a convention is about.
        self._order = list(order)

    def run(self, function: MachineFunction) -> Assignment:
        """Assign every virtual register of *function* and rewrite it.

        Where there are not enough registers, the values that could not have one
        are given a slot in the frame and the function is rewritten to read and
        write them there; then it all starts again.  Each round spills at least
        one value and a value once spilled needs no register across its life, so
        the rounds run out.
        """
        assignment = Assignment()
        for _ in range(len(self._order) + 1):
            ranges, blocked = self._live_ranges(function.instructions())
            assignment.ranges = ranges
            crowded = self._assign(ranges, blocked, assignment)
            if not crowded:
                self._rewrite(function, assignment)
                assignment.coalesced = self._drop_identity_moves(function)
                return assignment
            self._spill(function, crowded, assignment)
        raise RegisterPressureError(function.name, len(assignment.spilled) + 1,
                                    len(self._order))

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

    def _assign(self, ranges: Sequence[LiveRange], blocked: Sequence[LiveRange],
                assignment: Assignment) -> list[VirtReg]:
        """Give each range a unit, and say which ranges could not have one.

        A unit is released as soon as the range holding it ends.  Where none is
        free, the value whose range reaches furthest is the one to send to the
        frame: it is the one that would hold a register longest, so giving it up
        frees the most.  That is the choice linear scan was described with, and
        it is still the right one for straight-line code.
        """
        active: list[tuple[LiveRange, RegUnit]] = []
        crowded: list[VirtReg] = []
        for found in ranges:
            active = [(r, u) for r, u in active if r.end >= found.start]
            taken = {u for _, u in active}
            taken.update(self._blocked_by(found, blocked))
            unit = self._choose(found, taken)
            assert isinstance(found.reg, VirtReg)
            if unit is not None:
                assignment.units[found.reg.ident] = unit
                active.append((found, unit))
                continue
            # Only a value that may be spilled can be the one given up, and one
            # that may not has to be kept whatever else goes.
            candidates = [pair for pair in active
                          if isinstance(pair[0].reg, VirtReg) and pair[0].reg.spillable]
            furthest = max(candidates, key=lambda pair: pair[0].end, default=None)
            steal = furthest is not None and (furthest[0].end > found.end
                                              or not found.reg.spillable)
            if steal:
                assert furthest is not None
                victim, freed = furthest
                assert isinstance(victim.reg, VirtReg)
                crowded.append(victim.reg)
                assignment.units.pop(victim.reg.ident, None)
                active.remove(furthest)
                assignment.units[found.reg.ident] = freed
                active.append((found, freed))
            else:
                crowded.append(found.reg)
        return crowded

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

    # -- spilling --------------------------------------------------------------

    def _spill(self, function: MachineFunction, victims: Sequence[VirtReg],
               assignment: Assignment) -> None:
        """Put *victims* in the frame and rewrite the function to use it there.

        A value that is spilled is in memory for its whole life.  What occupies
        a register is a fresh one per instruction: read back into it just before
        the instruction that wants the value, written from it just after the
        instruction that computes one.  Those live for a single instruction, so
        there is always somewhere to put them.
        """
        slots = {}
        for victim in victims:
            if not victim.spillable:
                raise RegisterPressureError(function.name, len(victims),
                                            len(self._order))
            if victim.ident in assignment.spilled:
                continue
            slots[victim.ident] = function.frame.allocate()
        assignment.spilled.update(slots)
        wanted = {v.ident: v for v in victims if v.ident in assignment.spilled}
        for block in function.blocks:
            block.insts = self._with_spill_code(block.insts, wanted, assignment)

    def _with_spill_code(self, insts: Sequence[MCInst], wanted: dict[int, VirtReg],
                         assignment: Assignment) -> list[MCInst]:
        """One block's instructions, with the reads and writes of the frame in."""
        out: list[MCInst] = []
        for inst in insts:
            defs, uses = defs_and_uses(inst)
            touched = {r.ident: r for r in (*defs, *uses)
                       if isinstance(r, VirtReg) and r.ident in wanted}
            if not touched:
                out.append(inst)
                continue
            replacement: dict[int, VirtReg] = {}
            before: list[MCInst] = []
            after: list[MCInst] = []
            for ident, victim in touched.items():
                fresh = self._registers.new_virtual(victim.cls, victim.bits)
                replacement[ident] = fresh
                slot = assignment.spilled[ident]
                if any(isinstance(r, VirtReg) and r.ident == ident for r in uses):
                    before.extend(self._selector.select_reload(fresh, slot, inst.span))
                if any(isinstance(r, VirtReg) and r.ident == ident for r in defs):
                    after.extend(self._selector.select_spill(slot, fresh, inst.span))
            out.extend(before)
            out.append(self._substituted(inst, replacement))
            out.extend(after)
        return out

    def _substituted(self, inst: MCInst, replacement: dict[int, VirtReg]) -> MCInst:
        """One instruction, with the spilled registers standing in for their own."""
        operands = tuple(self._substitute_operand(o, replacement)
                         for o in inst.operands)
        return MCInst(desc=inst.desc, operands=operands, span=inst.span,
                      prefixes=inst.prefixes)

    def _substitute_operand(self, operand: MCOperand,
                            replacement: dict[int, VirtReg]) -> MCOperand:
        """One operand, with a spilled register replaced by the one standing in."""
        match operand:
            case MCReg(reg=VirtReg() as reg) if reg.ident in replacement:
                return MCReg(replacement[reg.ident], operand.bits)
            case MCMem():
                base, index = operand.base, operand.index
                changed = MCMem(
                    base=(replacement[base.ident]
                          if isinstance(base, VirtReg) and base.ident in replacement
                          else base),
                    index=(replacement[index.ident]
                           if isinstance(index, VirtReg) and index.ident in replacement
                           else index),
                    scale=operand.scale, disp=operand.disp, disp_sym=operand.disp_sym,
                    seg=operand.seg, rip_relative=operand.rip_relative,
                    size_bits=operand.size_bits, signed=operand.signed)
                return changed
            case _:
                return operand

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


class SpillSelector(Protocol):
    """The part of a target the allocator needs: how it reaches the frame."""

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*."""
        ...

    def select_reload(self, destination: Reg, slot: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that read the frame slot at *slot* into *destination*."""
        ...


def allocate(function: MachineFunction, registers: RegisterInfo,
             order: Sequence[RegUnit], selector: SpillSelector) -> Assignment:
    """Assign every virtual register of *function*."""
    return LinearScan(registers, order, selector).run(function)
