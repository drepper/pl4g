"""Making the `constinit` variables read-only once the constructors have run.

A `constinit` variable is written by its initializer, which the entry point
calls before the constructors, and by nothing else, which the checker sees to;
the entry point then makes it read-only, so that what the checker cannot see --
a write through `⎕at` of its address -- stops the program on a fault rather
than changing what every part of it reads.  The table the auxiliary vector is
read into is kept the same way.  The variables are in a section of their own,
`.pl4g.relro`, which the writer puts in a segment of its own: no page of it is a
page of anything else, so taking the right to write away from it takes it from
nothing else.

**One system call, from two symbols the writer defines.**  Where the pages begin
and where they end is known once the image is laid out, so the writer gives the
two symbols their addresses and the entry point asks for
`mprotect(start, end - start, PROT_READ)`.  The start is the first page of the
segment -- the one before it ends on an earlier page -- and the kernel rounds
the length up to the page the end is on, which is the segment's own as well.

**What the kernel answers changes nothing.**  A system that will not have it
leaves the pages writable, which is what they were; what the program reads is
the same either way, and the checker's rule is what keeps it from writing them.

It is written here once and emitted for every architecture, the way the stack
is: what differs between them is the number of the call, which registers its
arguments go in, and which instruction enters the kernel.
"""

from __future__ import annotations

from typing import Final

from ..ir.module import Module
from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCReg
from .allocator import SyscallABI

#: Where the variables written before the startup function alone are put.  Not
#: `.data.rel.ro`, which is what the loader writes and `PT_GNU_RELRO` protects
#: before any constructor runs: a shared object keeps these out of that, and
#: a reader seeing the name knows which of the two it is looking at.
SECTION: Final[str] = ".pl4g.relro"

#: The two symbols the writer gives the bounds of the pages to protect.
START_SYMBOL: Final[str] = "__pl4g_relro_start"
END_SYMBOL: Final[str] = "__pl4g_relro_end"

#: What `mprotect` is asked to leave of a page: reading it.
PROT_READ: Final[int] = 1


def wanted(module: Module) -> bool:
    """Whether *module* has a variable in the section, which is what asks for
    this: a `constinit` one or the auxiliary vector's table."""
    return any(var.relro for var in module.globals.values())


def emit_protect(asm: Assembler, calls: SyscallABI, mprotect: int) -> None:
    """Emit, into the entry point, the call that makes the pages read-only.

    Emitted after the constructors and before anything else: the tests a binary
    runs at start are not constructors and may only read what they find.  The
    registers are the system call's own, which nothing the entry point keeps
    across a call is in.
    """
    first, second, third = calls.arguments[:3]
    asm.address(first, START_SYMBOL)
    asm.address(second, END_SYMBOL)
    asm.op(ops.MINUS, second, MCReg(second), MCReg(first))
    asm.loadreg(third, asm.imm(PROT_READ, 32, signed=False))
    asm.loadreg(calls.number, asm.imm(mprotect, 32, signed=False))
    calls.enter(asm)
