"""The machine-level function.

Instruction selection produces this, not a finished stream of bytes.  It is the
mutable form a register allocator, a peephole pass and a scheduler will work on,
and keeping it separate from the streamer is what lets the debugging dump and
the image share every bit of instruction selection.
"""

from dataclasses import dataclass, field
from typing import Sequence

from .inst import MCInst
from .reg import PhysReg, Reg, VirtReg


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


@dataclass(slots=True, eq=False)
class MachineFunction:
    """One function, as machine instructions."""

    name: str
    blocks: list[MachineBasicBlock] = field(default_factory=list)
    #: Whether this function's symbol is visible outside the image.
    exported: bool = False
    #: Reserved growth slack to emit after the function, for in-place patching.
    padding: int = 0

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


def physical_only(registers: Sequence[Reg]) -> bool:
    """Whether every register in *registers* has been assigned."""
    return all(isinstance(r, PhysReg) for r in registers)
