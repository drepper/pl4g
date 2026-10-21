"""Giving every value a register.

Instruction selection names each value it computes with a virtual register and
says nothing about where it lives.  This decides that, and the encoders refuse a
virtual register, so nothing can quietly skip it.

The method is linear scan.  Every instruction of the function is given a
position, each register gets the hull of the positions at which it is live and
the stretches inside it where it actually is, and the ranges are walked in order
of their start, holding a unit for as long as a range needs one and releasing it
as soon as the range ends.  Two ranges whose stretches never meet may hold one
unit however their hulls overlap -- the holes in a lifetime, which is what a
value written in both arms of a branch, or one a loop carries round, has -- so
what linear scan gives up next to a colouring allocator is little more than
which order it decides in.

Which positions a register is live at is settled over the control-flow graph and
not read off the layout.  That distinction does nothing while control only falls
through and is the whole of what makes a loop safe: the span between the first
write and the last read in layout order stops being a superset of the live set
as soon as control can come back, and a value last read in the middle of a loop
would have its register handed to something later in the same loop.

Where there are not enough registers, a value is spilled, and which value that
is asks what spilling each would cost and what it would give back.  The cost is
every read and every write of it, each weighed by ten to the depth of the loop
it stands in; what it gives back is the stretch of register time it was holding.
The value with the lowest cost per point is the one that goes.  Linear scan was
described with "the range that reaches furthest", which is that with the cost
left out -- and in a loop it names the value the loop carries, whose reload then
runs every turn.

A spilled value is given a slot in the function's frame, written there when it
is computed and read back before each use.  That is the whole of it -- the value is in memory for its whole life, and
what occupies a register is a fresh one that lives for the single instruction
that reads or writes it.  Splitting a range so that a value is in a register
where it is busy and in memory where it is not would generate better code and is
a great deal more machinery; this is the version that is obviously right, and
the shape of the loop is what makes the better version a change in one place.

**One split is made: around a call.**  A value live across a call needs a
register the call does not destroy, and there are five or six of those; where
the value is wanted far more often than the calls it lives across are made --
by `block_frequencies`, which guesses at branches as well as counting loops --
it is put in the frame just before each such call and read back just after,
and is in an ordinary register everywhere else.  A loop that calls a function
on one way of a branch then pays for the call where the call is made, rather
than with a read of the frame on every turn.  A value split so that is still in
the way in the next round goes to the frame everywhere, as any other.

**A value moved to or from another is given the other's register** where that
is free, as a hint is honoured: a block's parameter and what a branch hands it,
the first operand of an instruction that writes over it and what is written.
The move is then a move of a register to itself, which goes -- on the edge that
goes round a loop, that is every value the loop carries.

Spilling is done by rewriting and starting again rather than by patching the
assignment as it goes.  A spill adds instructions, which moves every position
after it and so changes every range; recomputing is simpler than repairing, and
a function is small.

One thing it does not do: put one value in two places.  A value is in one unit
for its whole life, holes and all, so a value live across a point where the
convention demands a particular register is simply not given that register.
Nothing yet produces such a point except the move to the register a result is
returned in, which nothing outlives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from fractions import Fraction
from typing import Final, Protocol, Sequence

from .desc import InstFlags, OperandRole
from .inst import MCInst
from .loops import block_frequencies, loop_depths
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
    """Where a register starts being wanted and where it stops, and the
    stretches in between where it is.

    `start` and `end` are the hull, which is what the scan walks by; `stretches`
    are the points the register is actually live at, in order and apart.  A
    value is often not live everywhere in its hull: one written in two arms of
    a branch is live in each and in neither of the other's, and a value that
    reaches round a loop is live at the top and the bottom and not in a stretch
    nothing between reads it.  Two ranges whose stretches never meet may hold
    one register however their hulls overlap.  Nothing written means the hull.
    """

    reg: Reg
    start: int
    end: int
    #: What spilling it would cost: every read and every write of it, each
    #: counted as ten to the depth of the loop it stands in.  A value the code
    #: only mentions outside a loop is counted once per mention, and one read
    #: inside a loop is counted as the ten times it runs.
    cost: int = 0
    stretches: tuple[tuple[int, int], ...] = ()

    def _parts(self) -> tuple[tuple[int, int], ...]:
        """The stretches, or the hull where none were worked out."""
        return self.stretches or ((self.start, self.end),)

    def covers(self, other: LiveRange) -> bool:
        """Whether the two ranges are wanted at the same time: whether a
        stretch of one meets a stretch of the other."""
        if not (self.start <= other.end and other.start <= self.end):
            return False
        mine, theirs = self._parts(), other._parts()
        i = j = 0
        while i < len(mine) and j < len(theirs):
            (a, b), (c, d) = mine[i], theirs[j]
            if a <= d and c <= b:
                return True
            if b < d:
                i += 1
            else:
                j += 1
        return False

    @property
    def length(self) -> int:
        """How many points it holds a unit for, which is never fewer than one."""
        return sum(b - a + 1 for a, b in self._parts())

    @property
    def price(self) -> Fraction:
        """What it costs to spill, per point of register time that gives back.

        The measure the victim is chosen by, and both halves of it matter: the
        cost alone would keep a value that is read twice over the whole function
        and give up one read twice in three instructions, and the length alone
        -- which is what linear scan was described with -- gives up the value a
        loop carries, since that is the one that reaches furthest.
        """
        return Fraction(self.cost, self.length)


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
    #: The branches that leave it before its end, each with the blocks it
    #: reaches; and the block cut there, from its start to each such branch and
    #: from the last of them to its end, by what each piece reads before
    #: writing and writes at all.  Nothing where it is left only at its end.
    exits: list[tuple[int, tuple[int, ...]]] = field(default_factory=list)
    pieces: list[tuple[set[int], set[int]]] = field(default_factory=list)
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


def _branch_targets(inst: MCInst) -> list[str]:
    """The labels a branch names: every symbol among its operands."""
    found: list[str] = []
    for operand in inst.operands:
        symbol = getattr(getattr(operand, "expr", None), "symbol", None)
        name = getattr(symbol, "name", None)
        if isinstance(name, str):
            found.append(name)
    return found


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
            arriving = _arriving(flow, flows, leaving)
            if arriving != flow.live_in:
                flow.live_in = arriving
                changed = True


def _arriving(flow: BlockFlow, flows: Sequence[BlockFlow],
              leaving: set[int]) -> set[int]:
    """What is live where *flow* begins, *leaving* being what is live where it
    ends.

    A block left only at its end wants what it reads before writing and what it
    does not write and something after it wants.  One with a branch before its
    end is walked back a piece at a time: what is live just after such a branch
    is what is live going on together with what the blocks it reaches want --
    which is what keeps a value one of them reads alive up to the branch, even
    where the block writes it again after.
    """
    if not flow.exits:
        return flow.uses | (leaving - flow.kills)
    live = set(leaving)
    for piece in range(len(flow.pieces) - 1, -1, -1):
        if piece < len(flow.exits):
            for at in flow.exits[piece][1]:
                live |= flows[at].live_in
        uses, kills = flow.pieces[piece]
        live = uses | (live - kills)
    return live


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
    #: The reads and writes of the frame the spilling put in, by the identity
    #: of the instruction: which slot, and the register standing in.
    reloads: dict[int, tuple[int, VirtReg]] = field(default_factory=dict)
    stores: dict[int, tuple[int, VirtReg]] = field(default_factory=dict)
    #: Reads of the frame made into nothing or into a move between registers,
    #: because what the slot holds was still in a register.
    forwarded: int = 0
    #: The virtual registers that are put in the frame only across the calls
    #: they live across, by their identity, and the slot each is put in; and
    #: the reads and writes of the frame that put them there, by the identity
    #: of the instruction.
    split: dict[int, int] = field(default_factory=dict)
    split_code: dict[int, set[int]] = field(default_factory=dict)


def _merged(stretches: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Stretches in order, with those that meet or touch made one."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(stretches):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


def _victim_order(found: LiveRange) -> tuple[Fraction, int]:
    """How good a victim a range is: the smaller this is, the sooner it goes.

    Its price first, and where two are worth the same the one that reaches
    furthest, which is what linear scan chose on its own and is still the right
    thing to break a tie with -- of two values that cost the same to spill, the
    one holding a register longer is the one giving more back.
    """
    return (found.price, -found.end)


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


def _move_partners(function: MachineFunction) -> dict[int, list[int]]:
    """For each virtual register, the virtual registers it is moved to or from,
    by their identity and in the order the moves stand in."""
    found: dict[int, list[int]] = {}
    for inst in function.instructions():
        if InstFlags.MOVE not in inst.desc.flags or len(inst.operands) != 2:
            continue
        first, second = inst.operands
        if not isinstance(first, MCReg) or not isinstance(second, MCReg):
            continue
        if not isinstance(first.reg, VirtReg) or not isinstance(second.reg, VirtReg) \
                or first.reg is second.reg:
            continue
        found.setdefault(first.reg.ident, []).append(second.reg.ident)
        found.setdefault(second.reg.ident, []).append(first.reg.ident)
    return found


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
        #: What each virtual register is moved to or from, worked out afresh
        #: for every round of `run`.
        self._partners: dict[int, list[int]] = {}
        #: How often each register is read and written, in the measure of
        #: `block_frequencies`, and every call with how often it runs; both of
        #: the round being allocated.
        self._often: dict[int, float] = {}
        self._calls: list[tuple[int, float]] = []
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
        # Twice as many rounds as there are registers: a value may be put in
        # the frame across its calls in one and everywhere in the next.
        for _ in range(2 * self._widest_order + 2):
            ranges, blocked = self._live_ranges(function)
            self._partners = _move_partners(function)
            assignment.ranges = ranges
            crowded = self._assign(ranges, blocked, assignment)
            if not crowded:
                self._forward_reloads(function, assignment)
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
        weights = self._weights(function, flows)
        often = self._frequencies(function, flows)
        first: dict[int, int] = {}
        last: dict[int, int] = {}
        cost: dict[int, int] = {}
        self._often = {}
        self._calls = []

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
            weight = weights[position]
            runs = often[position]
            # A call that destroys nothing -- the one to the fault path, which
            # does not come back -- is no reason to move a value anywhere.
            if InstFlags.CALL in inst.desc.flags and inst.clobbers:
                self._calls.append((position, runs))
            for reg in uses:
                key = self._key(reg)
                touch(key, position * 2)
                # A read of a spilled value is a read of the frame here, and a
                # write of one is a write of the frame here; both are paid for
                # as often as this instruction runs.
                cost[key] = cost.get(key, 0) + weight
                self._often[key] = self._often.get(key, 0.0) + runs
            for reg in defs:
                key = self._key(reg)
                touch(key, position * 2 + 1)
                cost[key] = cost.get(key, 0) + weight
                self._often[key] = self._often.get(key, 0.0) + runs
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
            # And at a branch before the end, what the blocks it reaches want.
            for position, targets in flow.exits:
                for target in targets:
                    for key in flows[target].live_in:
                        touch(key, position * 2)
        stretched = self._stretches(function, flows, seen, virtual=True)
        virtual = [LiveRange(reg=reg, start=first[key], end=last[key],
                             cost=cost.get(key, 0),
                             stretches=_merged(stretched.get(key, ())))
                   for key, reg in seen.items() if isinstance(reg, VirtReg)]
        physical = [LiveRange(reg=seen[key], start=a, end=b)
                    for key, found in self._stretches(function, flows, seen,
                                                      virtual=False).items()
                    for a, b in found]
        virtual.sort(key=lambda r: (r.start, r.end))
        return virtual, physical

    def _weights(self, function: MachineFunction,
                 flows: Sequence[BlockFlow]) -> list[int]:
        """What one run of each instruction is worth, in layout order.

        Ten to the depth of the loops the instruction stands in, which is the
        weight priority-based colouring was first described with and what every
        allocator since has some version of.  It is a guess at how often the
        instruction runs and is deliberately a crude one: nothing here knows how
        many turns a loop takes, and the thing the weight has to get right is
        only that inside is worth more than outside.
        """
        depths = loop_depths(function)
        found: list[int] = []
        for flow, depth in zip(flows, depths):
            if flow.empty:
                continue
            found.extend([10 ** depth] * (flow.last - flow.first + 1))
        return found

    def _frequencies(self, function: MachineFunction,
                     flows: Sequence[BlockFlow]) -> list[float]:
        """How often each instruction is taken to run, in layout order: its
        block's frequency, which the depth of its loops and a guess at its
        branches make.  What decides whether a value is put in the frame
        everywhere or only across its calls, the two being costs of different
        places in the same loops."""
        frequencies = block_frequencies(function)
        found: list[float] = []
        for flow, frequency in zip(flows, frequencies):
            if flow.empty:
                continue
            found.extend([frequency] * (flow.last - flow.first + 1))
        return found

    def _stretches(self, function: MachineFunction, flows: Sequence[BlockFlow],
                   seen: Mapping[int, Reg],
                   virtual: bool) -> dict[int, list[tuple[int, int]]]:
        """Every stretch over which each register is holding something: the
        virtual ones, or the physical ones.

        A physical register is written where a convention says it is -- an
        argument into the register the callee reads it from, an answer into the
        register the caller reads it from -- and between one such write and the
        read that takes the value away it holds nothing.  A hull over all of them
        would say it was busy the whole time, and a value that could have had it
        would be sent somewhere else; the code that comes out is a move into a
        register and a move straight back out of it.  Each stretch of one is a
        range of its own.

        A virtual register is one value, but it is not live everywhere between
        its first write and its last read either -- a parameter of a block is
        written in each block that branches to it, and is live in none of them
        but where it is written and what follows -- so its stretches are kept
        on its one range, and asked where it meets another.

        Within a block the stretch runs from a write to the last read before the
        next write; at the edges of a block it is what the graph says is live
        coming in and going out.  Nothing is lost by splitting: every read of
        the register is a point the stretch holding that value covers.
        """
        wanted = {key for key, reg in seen.items()
                  if isinstance(reg, VirtReg) is virtual}
        found: dict[int, list[tuple[int, int]]] = {}
        for block, flow in zip(function.blocks, flows):
            if flow.empty:
                continue
            start: dict[int, int] = {}
            reached: dict[int, int] = {}
            for key in flow.live_in:
                if key in wanted:
                    start[key] = reached[key] = flow.entry_point
            exits = dict(flow.exits)
            for at, inst in enumerate(block.insts):
                position = flow.first + at
                defs, uses = defs_and_uses(inst)
                read = {self._key(r) for r in uses
                        if isinstance(r, VirtReg) is virtual}
                # A branch before the end reads what the blocks it reaches want.
                for target in exits.get(position, ()):
                    read |= flows[target].live_in & wanted
                written = {self._key(r) for r in defs
                           if isinstance(r, VirtReg) is virtual}
                for key in read:
                    start.setdefault(key, position * 2)
                    reached[key] = position * 2
                for key in written:
                    if key not in read:
                        # What it held before this is gone, and what it holds
                        # from here is another value.
                        if key in start:
                            found.setdefault(key, []).append((start[key], reached[key]))
                        start[key] = position * 2 + 1
                    reached[key] = position * 2 + 1
            for key, began in start.items():
                end = (flow.exit_point if key in flow.live_out else reached[key])
                found.setdefault(key, []).append((began, end))
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
            exits: list[tuple[int, tuple[int, ...]]] = []
            pieces: list[tuple[set[int], set[int]]] = []
            piece_uses: set[int] = set()
            piece_kills: set[int] = set()
            for at, inst in enumerate(block.insts):
                defs, used = defs_and_uses(inst)
                for reg in used:
                    key = self._key(reg)
                    seen.setdefault(key, reg)
                    if key not in kills:
                        uses.add(key)
                    if key not in piece_kills:
                        piece_uses.add(key)
                for reg in defs:
                    key = self._key(reg)
                    seen.setdefault(key, reg)
                    kills.add(key)
                    piece_kills.add(key)
                if at + 1 < len(block.insts) and InstFlags.TERMINATOR in inst.desc.flags:
                    reached = tuple(found for found in (
                        function.index_of(label) for label in _branch_targets(inst))
                        if found is not None)
                    if reached:
                        exits.append((position, reached))
                        pieces.append((piece_uses, piece_kills))
                        piece_uses, piece_kills = set(), set()
                position += 1
            if exits:
                pieces.append((piece_uses, piece_kills))
            flows.append(BlockFlow(first=begins, last=position - 1,
                                   successors=successors, uses=uses, kills=kills,
                                   exits=exits, pieces=pieces))
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
        free, the value to send to the frame is the one whose `price` is lowest
        -- what spilling it costs, per point of register time it gives back.
        Linear scan was described with the range that reaches furthest, which is
        that measure with the cost left out; the cost is what keeps a value a
        loop carries out of the frame, it being the one that both reaches
        furthest and is read every turn.

        The incoming range is a candidate like any other: where it is the
        cheapest, it is the one that goes and the unit stays where it is.

        A unit is taken only by the ranges whose stretches meet the incoming
        one's: two values never live at one point may hold one register, however
        their hulls overlap.  So several active ranges may hold one unit, and
        giving one up frees the unit only where it is the one there that meets
        the incoming range -- and only for a range of the class the unit is.
        """
        active: list[tuple[LiveRange, RegUnit]] = []
        crowded: list[VirtReg] = []
        for found in ranges:
            active = [(r, u) for r, u in active if r.end >= found.start]
            meeting = [(r, u) for r, u in active if r.covers(found)]
            fixed = self._blocked_by(found, blocked)
            taken = {u for _, u in meeting} | fixed
            unit = self._choose(found, taken, assignment)
            assert isinstance(found.reg, VirtReg)
            if unit is not None:
                assignment.units[found.reg.ident] = unit
                active.append((found, unit))
                continue
            # Only a value that may be spilled can be the one given up, and one
            # that may not has to be kept whatever else goes; and giving it up
            # has to leave its unit free for this one.
            holders: dict[RegUnit, int] = {}
            for _, u in meeting:
                holders[u] = holders.get(u, 0) + 1
            usable = set(self._order_for(found.reg))
            candidates = [pair for pair in meeting
                          if isinstance(pair[0].reg, VirtReg) and pair[0].reg.spillable
                          and holders[pair[1]] == 1 and pair[1] not in fixed
                          and pair[1] in usable]
            cheapest = min(candidates, key=lambda pair: _victim_order(pair[0]),
                           default=None)
            if cheapest is None:
                # Nothing here may be given up.  The incoming range goes, and
                # where it may not be spilled either the pressure is reported.
                crowded.append(found.reg)
                continue
            if found.reg.spillable \
                    and _victim_order(found) < _victim_order(cheapest[0]):
                crowded.append(found.reg)
                continue
            victim, freed = cheapest
            assert isinstance(victim.reg, VirtReg)
            crowded.append(victim.reg)
            assignment.units.pop(victim.reg.ident, None)
            active.remove(cheapest)
            assignment.units[found.reg.ident] = freed
            active.append((found, freed))
        return crowded

    def _blocked_by(self, found: LiveRange,
                    blocked: Sequence[LiveRange]) -> set[RegUnit]:
        """The units a physical register holds while *found* is wanted."""
        units: set[RegUnit] = set()
        for other in blocked:
            if other.covers(found) and isinstance(other.reg, PhysReg):
                units.add(other.reg.unit)
        return units

    def _choose(self, found: LiveRange, taken: set[RegUnit],
                assignment: Assignment) -> RegUnit | None:
        """The unit to give *found*, preferring the one it asked for, and then
        one a value it is moved to or from already holds.

        A hint is where the value is wanted anyway -- the register a result is
        returned in, most often -- so taking it turns the move that would put it
        there into a move of a register to itself, which then goes.  A value
        moved to or from another that already has a unit is the same thing with
        the other value for the hint: a block's parameter and what a branch
        hands it, the operand of an instruction that writes over its first
        operand and what is written.  Where that unit is free here, the two
        never live at once, and the move between them is a move of a register
        to itself.
        """
        reg = found.reg
        order = self._order_for(reg)
        if isinstance(reg, VirtReg) and reg.hint is not None:
            if reg.hint.unit not in taken and reg.hint.unit in order:
                return reg.hint.unit
        if isinstance(reg, VirtReg):
            for partner in self._partners.get(reg.ident, ()):
                unit = assignment.units.get(partner)
                if unit is not None and unit not in taken and unit in order:
                    return unit
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
        ranges = {found.reg.ident: found for found in assignment.ranges
                  if isinstance(found.reg, VirtReg)}
        split: dict[int, tuple[VirtReg, frozenset[int]]] = {}
        for victim in victims:
            if not victim.spillable:
                raise RegisterPressureError(function.name, len(victims),
                                            len(self._order_for(victim)))
            if victim.ident in assignment.spilled:
                continue
            kept = assignment.split.get(victim.ident)
            if kept is not None:
                # Put in the frame across its calls already and still in the
                # way: everywhere, then, in the slot it has -- and what put it
                # there across the calls goes, every read and write of it
                # reaching the frame now.
                slots[victim.ident] = kept
                gone = assignment.split_code.pop(victim.ident, set())
                for block in function.blocks:
                    block.insts = [one for one in block.insts if id(one) not in gone]
                continue
            across = self._calls_across(ranges.get(victim.ident))
            if across and 2 * sum(runs for _, runs in across) \
                    < self._often.get(self._key(victim), 0.0):
                slot = function.frame.allocate(victim.bits // 8)
                assignment.split[victim.ident] = slot
                split[victim.ident] = (victim, frozenset(at for at, _ in across))
                continue
            slots[victim.ident] = function.frame.allocate(victim.bits // 8)
        if split:
            self._split_at_calls(function, split, assignment)
        assignment.spilled.update(slots)
        wanted = {v.ident: v for v in victims if v.ident in slots}
        if not wanted:
            return
        for block in function.blocks:
            block.insts = self._with_spill_code(block.insts, wanted, assignment)

    def _calls_across(self, found: LiveRange | None) -> list[tuple[int, float]]:
        """The calls *found* lives across, and how often each runs: the ones
        it is live both before and after, and not as what the call answers."""
        if found is None:
            return []
        parts = found._parts()
        return [(at, runs) for at, runs in self._calls
                if any(a <= at * 2 and b >= at * 2 + 1 for a, b in parts)]

    def _split_at_calls(self, function: MachineFunction,
                        split: Mapping[int, tuple[VirtReg, frozenset[int]]],
                        assignment: Assignment) -> None:
        """Put each value of *split* in its slot just before every call it lives
        across, and back in its register just after.

        Its register then holds nothing across the call, so that one a call
        destroys will do -- the value is in a register where it is read and
        written, and in the frame only where the call would have destroyed it.
        A spill everywhere is a read of the frame at every use: in a loop that
        calls something on one way of a branch, every turn of the loop pays for
        the turns that call.  The stores and loads stand next to the call,
        after the arguments are put where it wants them and before the answer
        is taken from where it leaves it.
        """
        position = 0
        for block in function.blocks:
            out: list[MCInst] = []
            for inst in block.insts:
                before: list[MCInst] = []
                after: list[MCInst] = []
                for ident, (victim, calls) in split.items():
                    if position not in calls:
                        continue
                    slot = assignment.split[ident]
                    stored = self._selector.select_spill(slot, victim, inst.span)
                    loaded = self._selector.select_reload(victim, slot, inst.span)
                    assignment.split_code.setdefault(ident, set()).update(
                        id(one) for one in (*stored, *loaded))
                    before.extend(stored)
                    after.extend(loaded)
                out.extend(before)
                out.append(inst)
                out.extend(after)
                position += 1
            block.insts = out

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
                        loaded = self._selector.select_reload(fresh, slot, inst.span)
                        if len(loaded) == 1:
                            assignment.reloads[id(loaded[0])] = (slot, fresh)
                        before.extend(loaded)
                replacement[ident] = fresh
                if writes:
                    stored = self._selector.select_spill(slot, fresh, inst.span)
                    if len(stored) == 1:
                        assignment.stores[id(stored[0])] = (slot, fresh)
                    after.extend(stored)
                held[ident] = (fresh, index)
            out.extend(before)
            out.append(self._substituted(inst, replacement))
            out.extend(after)
        return out

    def _forward_reloads(self, function: MachineFunction,
                         assignment: Assignment) -> None:
        """Read a spilled value from the register it is still in, where it is.

        Within a block, the register a slot's value was last written from or read
        into holds it until something writes that register.  A read of the slot
        before then is the value already at hand: nothing at all where it reads
        into that very register, and a move between registers where it reads into
        another.  This runs once every value has its unit, so it holds nothing it
        was not holding anyway -- the cheap half of keeping a spilled value in a
        register, the half that needs no reckoning of what holding it costs.

        Only within a block, and only the frame reads the spilling put in: what
        reaches a block from more than one way is not one register's.
        """
        move = getattr(self._selector, "select_move", None)
        for block in function.blocks:
            held: dict[int, RegUnit] = {}
            out: list[MCInst] = []
            for inst in block.insts:
                loaded = assignment.reloads.get(id(inst))
                if loaded is not None:
                    slot, fresh = loaded
                    target = assignment.units.get(fresh.ident)
                    source = held.get(slot)
                    if target is not None and source is not None:
                        assignment.forwarded += 1
                        if source is not target:
                            if move is None:
                                out.append(inst)
                            else:
                                out.extend(move(fresh, MCReg(self._registers.view(
                                    source, fresh.bits)), inst.span))
                        self._forget(held, target)
                        held[slot] = target
                        continue
                out.append(inst)
                defs, _ = defs_and_uses(inst)
                for reg in defs:
                    unit = (assignment.units.get(reg.ident) if isinstance(reg, VirtReg)
                            else reg.unit)
                    if unit is not None:
                        self._forget(held, unit)
                if loaded is not None:
                    unit = assignment.units.get(loaded[1].ident)
                    if unit is not None:
                        held[loaded[0]] = unit
                stored = assignment.stores.get(id(inst))
                if stored is not None:
                    unit = assignment.units.get(stored[1].ident)
                    if unit is not None:
                        held[stored[0]] = unit
            block.insts = out

    @staticmethod
    def _forget(held: dict[int, RegUnit], unit: RegUnit) -> None:
        """Note that *unit* no longer holds what any slot holds."""
        for slot in [s for s, u in held.items() if u is unit]:
            del held[slot]

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
