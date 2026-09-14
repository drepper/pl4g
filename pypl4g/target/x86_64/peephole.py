"""Peephole rewrites over the machine form.

These run on a ``MachineFunction`` before it is streamed, which is the same place
the register allocator and the scheduler will run.  Each rewrite states the
condition under which it is valid and checks that condition rather than assuming
it.
"""

from __future__ import annotations

from typing import Sequence

from ...mc.asmbuilder import MachinePass
from ...mc.desc import InstrTable
from ...mc.inst import MCInst
from ...mc.machine import MachineBasicBlock, MachineFunction
from ...mc.operand import MCImm, MCReg
from ...mc.reg import PhysReg
from .regs import EFLAGS


def _defines_flags(inst: MCInst) -> bool:
    """Whether *inst* writes the flags register."""
    return any(r is EFLAGS for r in inst.desc.implicit_defs)


def _uses_flags(inst: MCInst) -> bool:
    """Whether *inst* reads the flags register."""
    return any(r is EFLAGS for r in inst.desc.implicit_uses)


def flags_dead_after(block: MachineBasicBlock, index: int,
                     leaves_function: bool) -> bool:
    """Whether the flags register is dead immediately after instruction *index*.

    The scan stops at the first instruction that reads the flags, which makes
    them live, or at the first that overwrites them, which makes them dead.
    Reaching the end of the block is only conclusive when control leaves the
    function there: no calling convention keeps the flags live across a call or a
    return.  Without a cross-block liveness analysis every other case is treated
    as live, so the rewrite is skipped rather than risked.

    What says control leaves is that the block reaches nothing, and not that it
    is the last one laid out.  The two are the same only while every branch goes
    forward: a loop whose exit was folded away ends the layout with a jump
    backwards, and the block it jumps to may read the flags.
    """
    for later in block.insts[index + 1:]:
        if _uses_flags(later):
            return False
        if _defines_flags(later):
            return True
    return leaves_function


class ClearRegisterWithXor:
    """Rewrites ``mov r32, 0`` as ``xor r32, r32``, which is three bytes shorter.

    Valid only where the flags the exclusive-or writes are dead, which is why
    the encoding table records that it writes them.
    """

    name = "x86.clear-with-xor"

    def __init__(self, table: InstrTable) -> None:
        self._table = table

    def run(self, function: MachineFunction) -> bool:
        """Apply the rewrite wherever it is valid; report whether it fired."""
        changed = False
        for block in function.blocks:
            leaves_function = not block.successors
            for index, inst in enumerate(block.insts):
                replacement = self._rewrite(block, index, inst, leaves_function)
                if replacement is not None:
                    block.insts[index] = replacement
                    changed = True
        return changed

    def _rewrite(self, block: MachineBasicBlock, index: int, inst: MCInst,
                 leaves_function: bool) -> MCInst | None:
        """Return the replacement for *inst*, or ``None`` to leave it alone."""
        if inst.desc.mnemonic != "mov" or len(inst.operands) != 2:
            return None
        dst, src = inst.operands
        if not isinstance(dst, MCReg) or not isinstance(src, MCImm):
            return None
        if src.value != 0 or not isinstance(dst.reg, PhysReg) or dst.reg.bits != 32:
            return None
        if not flags_dead_after(block, index, leaves_function):
            return None
        operands = (dst, dst)
        return MCInst(desc=self._table.select("xor", operands), operands=operands,
                      span=inst.span)


class DropMovesToItself:
    """Removes a move of a register to the register it is already in.

    The lowering writes one wherever an operation's destination is also its
    first source, since which register each of the two ends up in is not known
    until the allocator has run -- and the allocator's hint often makes them the
    same one, which is what this then notices.  It moves nothing and writes no
    flags, so removing it is safe wherever it stands.
    """

    name = "x86.drop-moves-to-itself"

    def run(self, function: MachineFunction) -> bool:
        """Remove them; report whether any went."""
        changed = False
        for block in function.blocks:
            kept = [inst for inst in block.insts if not _moves_to_itself(inst)]
            if len(kept) != len(block.insts):
                block.insts = kept
                changed = True
        return changed


def _moves_to_itself(inst: MCInst) -> bool:
    """Whether this instruction puts a register where it already is."""
    if inst.desc.mnemonic not in ("mov", "movdqu") or len(inst.operands) != 2:
        return False
    dst, src = inst.operands
    return (isinstance(dst, MCReg) and isinstance(src, MCReg)
            and isinstance(dst.reg, PhysReg) and isinstance(src.reg, PhysReg)
            and dst.reg.unit is src.reg.unit and dst.width == src.width
            and dst.reg.byte_off == src.reg.byte_off)


def passes_for(table: InstrTable, opt_level: int) -> Sequence[MachinePass]:
    """The machine passes to run at optimization level *opt_level*."""
    if opt_level <= 0:
        return ()
    return (ClearRegisterWithXor(table), DropMovesToItself())
