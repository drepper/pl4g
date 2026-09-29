"""Giving every value a register.

Instruction selection names each value it computes with a virtual register and
says nothing about where it lives.  This decides that, and the encoders refuse a
virtual register, so nothing can quietly skip it.

The method is linear scan.  Every instruction of the function is given a
position, each register gets the hull of the positions at which it is live, and
the ranges are walked in order of their start, holding a unit for as long as a
range needs one and releasing it as soon as the range ends.  That is a great
deal less than a colouring allocator would do, and it is the right amount here:
what it gives up is the ability to say that two values whose spans overlap are
never live at the same moment, which costs a register now and again and never
costs correctness.

Which positions a register is live at is settled over the control-flow graph and
not read off the layout.  That distinction does nothing while control only falls
through and is the whole of what makes a loop safe: the span between the first
write and the last read in layout order stops being a superset of the live set
as soon as control can come back, and a value last read in the middle of a loop
would have its register handed to something later in the same loop.

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

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Final, Protocol, Sequence

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

    def covers(self, other: LiveRange) -> bool:
        """Whether the two ranges are wanted at the same time."""
        return self.start <= other.end and other.start <= self.end


@dataclass(slots=True)
class BlockFlow:
    """One block's place in the layout and in the graph, and what lives there.

    The positions are the ones a range is measured in: instruction *p* reads at
    ``2p`` and writes at ``2p+1``.  A block with no instructions has ``last``
    one before ``first``, which is what `empty` reads.
    """

    #: Where its first and last instructions are laid out.
    first: int
    last: int
    #: The blocks control can reach from it, by their place in the layout.
    successors: tuple[int, ...]
    #: What it reads before writing, and what it writes at all.
    uses: set[int] = field(default_factory=set)
    kills: set[int] = field(default_factory=set)
    #: What is live where it begins and where it ends, once settled.
    live_in: set[int] = field(default_factory=set)
    live_out: set[int] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        """Whether the block holds no instruction, and so names no point."""
        return self.last < self.first

    @property
    def entry_point(self) -> int:
        """The point at which control arrives, which is its first read."""
        return self.first * 2

    @property
    def exit_point(self) -> int:
        """The point at which control leaves, which is its last write."""
        return self.last * 2 + 1


def _propagate(flows: Sequence[BlockFlow]) -> None:
    """Settle what is live where each block begins and ends.

    The ordinary backward fixpoint: what is live leaving a block is what is
    live entering any block it reaches, and what is live entering it is what it
    reads before writing together with what it does not write and something
    after it wants.  Walking the blocks backwards makes a graph with no cycle
    settle in one pass and one with a loop in two.
    """
    changed = True
    while changed:
        changed = False
        for flow in reversed(flows):
            leaving: set[int] = set()
            for at in flow.successors:
                leaving |= flows[at].live_in
            if leaving != flow.live_out:
                flow.live_out = leaving
                changed = True
            arriving = flow.uses | (leaving - flow.kills)
            if arriving != flow.live_in:
                flow.live_in = arriving
                changed = True


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
    """The registers an instruction writes and the registers it reads.

    What a call destroys is on the instruction rather than in its row, because
    it is the callee's to say; to everything that asks what is written, the two
    are one answer.
    """
    defs: list[Reg] = [*inst.desc.implicit_defs, *inst.clobbers]
    uses: list[Reg] = [*inst.desc.implicit_uses, *inst.reads]
    for index, operand in enumerate(inst.operands):
        role = inst.desc.role_of(index)
        for reg, writable in registers_of(operand):
            if writable and role is not OperandRole.USE:
                defs.append(reg)
            if not writable or role is not OperandRole.DEF:
                uses.append(reg)
    return defs, uses


#: What the one class is called where a target names no classes at all.  Every
#: value then belongs to it, which is the arrangement a target has until it has
#: a value that is not an integer.
_ONE_CLASS: Final[str] = ""


class LinearScan:
    """Assigns a register to every virtual register of a function."""

    def __init__(self, registers: RegisterInfo,
                 order: Sequence[RegUnit] | Mapping[str, Sequence[RegUnit]],
                 selector: SpillSelector) -> None:
        self._registers = registers
        #: What the target says a read or a write of a frame slot looks like.
        self._selector = selector
        #: The units that may be given out, in the order they are preferred,
        #: for each class of register.  The convention states it, since which
        #: registers are free to use is what a convention is about.
        #:
        #: A value belongs to one class and can only be held by a register of
        #: that class: an integer cannot go in a floating-point register and the
        #: other way round, whatever the widths say.  So the orders are kept
        #: apart and a value is given one from its own -- which is the whole of
        #: what having more than one class comes to here, the rest of the
        #: allocator working on units and never asking what kind they are.
        self._orders: dict[str, list[RegUnit]] = (
            {name: list(units) for name, units in order.items()}
            if isinstance(order, Mapping)
            else {_ONE_CLASS: list(order)})

    def _order_for(self, reg: Reg) -> list[RegUnit]:
        """The units *reg* may be given, in the order they are preferred."""
        if len(self._orders) == 1 and _ONE_CLASS in self._orders:
            return self._orders[_ONE_CLASS]
        return self._orders.get(reg.cls.name, [])

    @property
    def _widest_order(self) -> int:
        """How many registers the largest class has, which is how many rounds of
        spilling can be needed before every value has somewhere to be."""
        return max((len(units) for units in self._orders.values()), default=0)

    def run(self, function: MachineFunction) -> Assignment:
        """Assign every virtual register of *function* and rewrite it.

        Where there are not enough registers, the values that could not have one
        are given a slot in the frame and the function is rewritten to read and
        write them there; then it all starts again.  Each round spills at least
        one value and a value once spilled needs no register across its life, so
        the rounds run out.
        """
        assignment = Assignment()
        for _ in range(self._widest_order + 1):
            ranges, blocked = self._live_ranges(function)
            assignment.ranges = ranges
            crowded = self._assign(ranges, blocked, assignment)
            if not crowded:
                self._rewrite(function, assignment)
                assignment.coalesced = self._drop_identity_moves(function)
                return assignment
            self._spill(function, crowded, assignment)
        raise RegisterPressureError(function.name, len(assignment.spilled) + 1,
                                    self._widest_order)

    # -- liveness --------------------------------------------------------------

    def _live_ranges(self, function: MachineFunction
                     ) -> tuple[list[LiveRange], list[LiveRange]]:
        """The range of every register, virtual and physical.

        A range is the hull of the points at which the register is live, and
        liveness is settled over the control-flow graph rather than read off
        the layout.  That distinction is the whole of what makes a loop safe:
        the hull of the *live* points contains every one of them whichever way
        control reaches them, while the span between the first write and the
        last read in layout order does not once control can come back.

        Correctness asks nothing of the block order.  Tightness does: where a
        loop's blocks lie next to each other, the hull is the span of the loop
        and nothing outside it pays, and where they do not the code is still
        right and merely spills more.
        """
        flows, seen = self._flow(function)
        _propagate(flows)
        first: dict[int, int] = {}
        last: dict[int, int] = {}

        def touch(key: int, point: int) -> None:
            """Note that the register is live at *point*."""
            found = first.get(key)
            if found is None or point < found:
                first[key] = point
            found = last.get(key)
            if found is None or point > found:
                last[key] = point

        # One instruction is two points: it reads at the first and writes at
        # the second.  That is not a detail -- it is what lets the register a
        # value is read from be the register the same instruction writes,
        # which is how a move into the register a result is returned in ends
        # up moving a register to itself and going away.
        for position, inst in enumerate(function.instructions()):
            defs, uses = defs_and_uses(inst)
            for reg in uses:
                touch(self._key(reg), position * 2)
            for reg in defs:
                touch(self._key(reg), position * 2 + 1)
        # And a register live where a block begins or ends is live there even
        # though no instruction of that block names it.  A block with nothing
        # in it has no point to name, and skipping it opens no hole: what is
        # live across it is live in a block on either side.
        for flow in flows:
            if flow.empty:
                continue
            for key in flow.live_in:
                touch(key, flow.entry_point)
            for key in flow.live_out:
                touch(key, flow.exit_point)
        virtual = [LiveRange(reg=reg, start=first[key], end=last[key])
                   for key, reg in seen.items() if isinstance(reg, VirtReg)]
        physical = self._physical_ranges(function, flows, seen)
        virtual.sort(key=lambda r: (r.start, r.end))
        return virtual, physical

    def _physical_ranges(self, function: MachineFunction,
                         flows: Sequence[BlockFlow],
                         seen: Mapping[int, Reg]) -> list[LiveRange]:
        """Every stretch over which each physical register is holding something.

        Several stretches per register and not one, which is what a virtual
        register gets.  A physical register is written where a convention says
        it is -- an argument into the register the callee reads it from, an
        answer into the register the caller reads it from -- and between one
        such write and the read that takes the value away it holds nothing.  A
        hull over all of them would say it was busy the whole time, and a value
        that could have had it would be sent somewhere else; the code that comes
        out is a move into a register and a move straight back out of it.

        Within a block the stretch runs from a write to the last read before the
        next write; at the edges of a block it is what the graph says is live
        coming in and going out.  Nothing is lost by splitting: every read of
        the register is a point the stretch holding that value covers.
        """
        physical = {key: reg for key, reg in seen.items()
                    if not isinstance(reg, VirtReg)}
        found: list[LiveRange] = []
        for block, flow in zip(function.blocks, flows):
            if flow.empty:
                continue
            start: dict[int, int] = {}
            reached: dict[int, int] = {}
            for key in flow.live_in:
                if key in physical:
                    start[key] = reached[key] = flow.entry_point
            for at, inst in enumerate(block.insts):
                position = flow.first + at
                defs, uses = defs_and_uses(inst)
                read = {self._key(r) for r in uses if not isinstance(r, VirtReg)}
                written = {self._key(r) for r in defs if not isinstance(r, VirtReg)}
                for key in read:
                    start.setdefault(key, position * 2)
                    reached[key] = position * 2
                for key in written:
                    if key not in read:
                        # What it held before this is gone, and what it holds
                        # from here is another value.
                        if key in start:
                            found.append(LiveRange(reg=physical[key],
                                                   start=start[key],
                                                   end=reached[key]))
                        start[key] = position * 2 + 1
                    reached[key] = position * 2 + 1
            for key, began in start.items():
                end = (flow.exit_point if key in flow.live_out else reached[key])
                found.append(LiveRange(reg=physical[key], start=began, end=end))
        return found

    def _flow(self, function: MachineFunction
              ) -> tuple[list[BlockFlow], dict[int, Reg]]:
        """Number every instruction, resolve every edge, and say of each block
        what it reads before it writes and what it writes at all.

        Reads are counted before writes within one instruction, so that the
        operand a two-address instruction both reads and writes counts as
        something the block wants from outside it.
        """
        reachable = function.successor_indices()
        flows: list[BlockFlow] = []
        seen: dict[int, Reg] = {}
        position = 0
        for block, successors in zip(function.blocks, reachable):
            begins = position
            uses: set[int] = set()
            kills: set[int] = set()
            for inst in block.insts:
                defs, used = defs_and_uses(inst)
                for reg in used:
                    key = self._key(reg)
                    seen.setdefault(key, reg)
                    if key not in kills:
                        uses.add(key)
                for reg in defs:
                    key = self._key(reg)
                    seen.setdefault(key, reg)
                    kills.add(key)
                position += 1
            flows.append(BlockFlow(first=begins, last=position - 1,
                                   successors=successors, uses=uses, kills=kills))
        return flows, seen

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
        order = self._order_for(reg)
        if isinstance(reg, VirtReg) and reg.hint is not None:
            if reg.hint.unit not in taken and reg.hint.unit in order:
                return reg.hint.unit
        for unit in order:
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
                                            len(self._order_for(victim)))
            if victim.ident in assignment.spilled:
                continue
            slots[victim.ident] = function.frame.allocate(victim.bits // 8)
        assignment.spilled.update(slots)
        wanted = {v.ident: v for v in victims if v.ident in assignment.spilled}
        for block in function.blocks:
            block.insts = self._with_spill_code(block.insts, wanted, assignment)

    def _with_spill_code(self, insts: Sequence[MCInst], wanted: dict[int, VirtReg],
                         assignment: Assignment) -> list[MCInst]:
        """One block's instructions, with the reads and writes of the frame in.

        A value that is read again by the instruction straight after the one
        that read it is not read from the frame twice: the register it is
        already in serves both, and the register is held across exactly as many
        instructions as are reading it.  That is what keeps a value in a
        register where it is busy without keeping it there where it is not.

        The same rule covers a value read straight after it was computed, which
        is written to the frame and then used from the register it was written
        from rather than read back at once.
        """
        out: list[MCInst] = []
        #: For each spilled value that is in a register just now, which register
        #: and which instruction last touched it.
        held: dict[int, tuple[VirtReg, int]] = {}
        for index, inst in enumerate(insts):
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
                slot = assignment.spilled[ident]
                reads = any(isinstance(r, VirtReg) and r.ident == ident for r in uses)
                writes = any(isinstance(r, VirtReg) and r.ident == ident for r in defs)
                carried = held.get(ident)
                if reads and not writes and carried is not None and carried[1] == index - 1:
                    fresh = carried[0]
                else:
                    fresh = self._registers.new_virtual(victim.cls, victim.bits)
                    if reads:
                        before.extend(
                            self._selector.select_reload(fresh, slot, inst.span))
                replacement[ident] = fresh
                if writes:
                    after.extend(self._selector.select_spill(slot, fresh, inst.span))
                held[ident] = (fresh, index)
            out.extend(before)
            out.append(self._substituted(inst, replacement))
            out.extend(after)
        return out

    def _substituted(self, inst: MCInst, replacement: dict[int, VirtReg]) -> MCInst:
        """One instruction, with the spilled registers standing in for their own.

        What it destroys and what it reads beyond its row travel with it.  A
        call carries both -- they are the callee's to say -- and an instruction
        rebuilt without them is a call that looks as though it destroyed
        nothing, which is what a caller would then believe.
        """
        operands = tuple(self._substitute_operand(o, replacement)
                         for o in inst.operands)
        return MCInst(desc=inst.desc, operands=operands, span=inst.span,
                      prefixes=inst.prefixes, clobbers=inst.clobbers,
                      reads=inst.reads)

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
        """One instruction, with its registers assigned.

        As above, what it destroys and what it reads travel with it: this is the
        rewrite every instruction goes through, so an instruction that lost them
        here would have lost them for good.
        """
        operands = tuple(self._rewrite_operand(o, assignment) for o in inst.operands)
        if operands == inst.operands:
            return inst
        return MCInst(desc=inst.desc, operands=operands, span=inst.span,
                      prefixes=inst.prefixes, clobbers=inst.clobbers,
                      reads=inst.reads)

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
             order: Sequence[RegUnit] | Mapping[str, Sequence[RegUnit]],
             selector: SpillSelector) -> Assignment:
    """Assign every virtual register of *function*.

    *order* is either the units of the one class a target has values in, or a
    mapping from the name of a class to its units.
    """
    return LinearScan(registers, order, selector).run(function)
