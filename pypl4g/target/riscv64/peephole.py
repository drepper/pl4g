"""Rewrites over the machine form of a RISC-V function.

One today, and it is about the vector extension: every group of vector
instructions the selector writes begins by saying how wide an element is and
how many there are, because a group cannot know what was said before it.
Within one block it can be known, so what says again what was just said goes.
"""

from __future__ import annotations

from ...mc.desc import InstFlags
from ...mc.machine import MachineFunction


class SaidOnce:
    """Drops a `vsetivli` that says what the one before it in its block said.

    What it says lasts until another says something else -- or until a call or
    a request to the kernel, after which nothing is promised: a function called
    may say what it likes, and the kernel's convention here is that a system
    call leaves the vector state as it pleases.  So each of those forgets what
    was said, and so does the start of every block, which control may reach
    from anywhere.  Nothing else in a function touches it: setting a register
    aside in the frame and fetching it back is done whole, whatever was said.
    """

    name = "riscv.said-once"

    def run(self, function: MachineFunction) -> bool:
        """Drop what is said twice; report whether anything went."""
        changed = False
        for block in function.blocks:
            said: tuple[object, ...] | None = None
            kept = []
            for inst in block.insts:
                if inst.desc.mnemonic == "vsetivli":
                    if inst.operands == said:
                        changed = True
                        continue
                    said = inst.operands
                elif InstFlags.CALL in inst.desc.flags \
                        or inst.desc.mnemonic == "ecall":
                    said = None
                kept.append(inst)
            block.insts = kept
        return changed
