"""The machine-level function.

Instruction selection produces this, not a finished stream of bytes.  It is the
mutable form a register allocator, a peephole pass and a scheduler will work on,
and keeping it separate from the streamer is what lets the debugging dump and
the image share every bit of instruction selection.
"""

from dataclasses import dataclass, field
from typing import Sequence

from .desc import InstFlags
from .inst import MCInst
from .reg import PhysReg, Reg, RegUnit, VirtReg


class MalformedGraph(Exception):
    """A function whose recorded edges do not describe where control goes.

    Not a limit and not a program's fault: everything that walks the graph --
    the liveness the register allocator needs, and whatever reads it after --
    would quietly give a wrong answer, so it is said here rather than found
    later as a program that runs wrongly.
    """


@dataclass(slots=True, eq=False)
class MachineBasicBlock:
    """A straight-line run of machine instructions."""

    label: str
    insts: list[MCInst] = field(default_factory=list)
    #: Labels of the blocks control can reach from here.
    successors: list[str] = field(default_factory=list)

    def append(self, inst: MCInst) -> MCInst:
        """Append an instruction to this block."""
        self.insts.append(inst)
        return inst


@dataclass(slots=True)
class FrameInfo:
    """The stack a function uses for what it could not keep in registers.

    A slot is the width of the widest register rather than of the value in it:
    a frame of a few slots costs nothing to make larger, and one size means one
    rule about alignment instead of one per width.
    """

    #: How wide one slot is, which is also what it is aligned to.
    slot_size: int = 8
    #: What the stack pointer must be a multiple of, which every architecture
    #: has an opinion about and two of them enforce.
    alignment: int = 16
    slots: int = 0

    def allocate(self) -> int:
        """Take a slot and return its offset from the stack pointer."""
        offset = self.slots * self.slot_size
        self.slots += 1
        return offset

    @property
    def size(self) -> int:
        """How far the stack pointer moves, which is nothing where no slot was
        taken: a function that needed no stack does not make a frame."""
        raw = self.slots * self.slot_size
        if raw == 0:
            return 0
        return (raw + self.alignment - 1) // self.alignment * self.alignment


@dataclass(slots=True, eq=False)
class MachineFunction:
    """One function, as machine instructions."""

    name: str
    blocks: list[MachineBasicBlock] = field(default_factory=list)
    #: What the function keeps on the stack.  It is filled in by the register
    #: allocator, which is the only thing that puts anything there.
    frame: FrameInfo = field(default_factory=FrameInfo)
    #: Whether this function's symbol is visible outside the image.
    exported: bool = False
    #: Reserved growth slack to emit after the function, for in-place patching.
    padding: int = 0
    #: The units this function hands back as it found them, which is what it
    #: saved and restored around itself.  Filled in when the frame is made,
    #: which is the pass that decides it.
    preserved: frozenset[RegUnit] = field(default_factory=frozenset)

    def add_block(self, label: str | None = None) -> MachineBasicBlock:
        """Append a new block and return it."""
        block = MachineBasicBlock(
            label if label is not None else "".join((".L", self.name, "_",
                                                     str(len(self.blocks)))))
        self.blocks.append(block)
        return block

    def instructions(self) -> list[MCInst]:
        """Every instruction of the function, in layout order."""
        return [i for b in self.blocks for i in b.insts]

    def index_of(self, label: str) -> "int | None":
        """Where the block called *label* is laid out, or nothing where this
        function has no such block."""
        for index, block in enumerate(self.blocks):
            if block.label == label:
                return index
        return None

    def successor_indices(self) -> list[tuple[int, ...]]:
        """For each block, the blocks control can reach from it.

        The labels are resolved once here rather than at each use: a pass that
        walks the graph wants blocks, and only the builder wants names.  A
        successor naming no block of this function is one control does not come
        back from -- a jump that stands in for a call -- and is left out.
        """
        found: list[tuple[int, ...]] = []
        for block in self.blocks:
            reached = [self.index_of(label) for label in block.successors]
            found.append(tuple(at for at in reached if at is not None))
        return found

    def edges_are_complete(self) -> "str | None":
        """What is wrong with the recorded edges, or nothing where all is well.

        One invariant, and everything that walks the graph rests on it: a block
        whose last instruction neither leaves the function nor is a barrier
        reaches the block laid out after it by going on, so that block has to be
        among its successors.  Nothing derives a fall-through edge from an
        instruction, because there is no instruction to derive it from; it is
        recorded where the block is built, and this is what says it was.
        """
        for index, block in enumerate(self.blocks):
            # A block with nothing in it is one a branch to the block after it
            # turned into nothing; it goes on like any other, and has to say so.
            if block.insts:
                flags = block.insts[-1].desc.flags
                if InstFlags.BARRIER in flags or InstFlags.RETURN in flags:
                    continue
            if index + 1 >= len(self.blocks):
                return "".join((self.name, ": the block '", block.label,
                                "' ends by going on, past the last block"))
            following = self.blocks[index + 1].label
            if following not in block.successors:
                return "".join((self.name, ": the block '", block.label,
                                "' goes on into '", following,
                                "' without saying so"))
        return None

    def virtual_registers(self) -> list[VirtReg]:
        """Every virtual register the function still mentions.

        The encoder refuses to encode one, so an empty result is the condition
        the register allocator will have to establish.
        """
        found: dict[int, VirtReg] = {}
        for inst in self.instructions():
            for operand in inst.operands:
                reg = getattr(operand, "reg", None)
                if isinstance(reg, VirtReg):
                    found[reg.ident] = reg
        return list(found.values())


def clobbered_units(function: MachineFunction) -> frozenset[RegUnit]:
    """Every unit a finished function writes and does not put back.

    What a caller wants to know: a register not in here still holds what it held
    before the call, so nothing has to be saved around one.  It is asked of the
    finished code rather than of the convention, which can only say what a
    function is *allowed* to destroy -- a small function destroys a great deal
    less than that, and the difference is a save and a reload at every call.

    Read after the frame has been made, so that a register the function saved
    and restored is not counted: it was written, and it was put back.
    """
    from .regalloc import defs_and_uses

    found: set[RegUnit] = set()
    for block in function.blocks:
        for inst in block.insts:
            for reg in defs_and_uses(inst)[0]:
                if isinstance(reg, PhysReg):
                    found.add(reg.unit)
    return frozenset(found) - function.preserved


def physical_only(registers: Sequence[Reg]) -> bool:
    """Whether every register in *registers* has been assigned."""
    return all(isinstance(r, PhysReg) for r in registers)
