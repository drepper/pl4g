"""Walking the stack, so that a fault says who called and not only where it was.

Written once and emitted for every architecture, the way the allocator is: what
differs between the three is which registers a system call uses and which
instruction enters the kernel, and that much is a small record each backend
fills in.  Everything else -- the walk, the search, what is written -- is the
same three dozen instructions everywhere, because the table it reads was built
to make it so.

**What it is handed** is where the program was and where its stack was, which
the fault helper works out for itself: the architecture whose call instruction
pushes a return address finds it on the stack, and the two that leave it in a
register find it there.  That is the last thing about the three that differs,
and it is two instructions in each of them rather than anything here.

**What it does** is a loop over frames.  For each, it finds the row whose
function the address falls in -- the rows are in address order, so that is the
last row beginning at or before it -- writes the line the row names, and steps:
the return address is where the row says it is, measured from the stack pointer,
and the caller's stack pointer is as much further up as the row says.  It stops
where an address falls in no function, which is what reaching the entry point
comes to, and it stops after a fixed number of frames whatever the stack says,
because a walk that ran away would turn a program that reported a fault into one
that did not.

**Nothing here can fail.**  There is no formatting, nothing is allocated, and
the only memory read is the table and the stack the program is standing on.  The
innermost frame is not written, its name being in the message already.

**It may use any register it likes.**  It is called by the helper that is about
to end the program, so nothing it destroys will be missed -- which is what lets
it keep its state in registers a system call leaves alone rather than on a stack
it is in the middle of reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, Sequence

from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg
from . import frames

#: What the helper is called.
SYMBOL: Final[str] = "__pl4g_backtrace"

#: The descriptor everything a stopping program says goes to.
STANDARD_ERROR: Final[int] = 2

#: How many frames are written before the walk gives up.  A stack deeper than
#: this is a program whose report would be unreadable anyway, and a stack that
#: is not a stack is one this must not follow forever.
LIMIT: Final[int] = 64


@dataclass(frozen=True, slots=True)
class WalkABI:
    """How one architecture writes, and which registers the walk may keep."""

    #: The number of the write system call.
    write: int
    #: Where the number of the call goes, and where its arguments go.
    number: Reg
    arguments: Sequence[Reg]
    #: The instruction that enters the kernel.
    enter: Callable[[Assembler], None]
    #: Four registers the walk keeps its state in: where the program was, where
    #: its stack is, which row is being looked at, and how many frames have been
    #: written.  They have to be registers the system call leaves alone.
    kept: Sequence[Reg]
    #: Two more for whatever an instruction needs for a moment.  These need not
    #: survive anything.
    scratch: Sequence[Reg]


def emit_backtrace(asm: Assembler, abi: WalkABI,
                   arguments: Sequence[Reg]) -> None:
    """Emit the helper that walks the stack and writes a line per frame.

    *arguments* is where its own two arrive, which is the system convention's
    first two argument registers -- the same convention the helper that calls it
    follows, it being hand-written code like this.
    """
    where, stack, row, count = abi.kept[:4]
    first, second = abi.scratch[:2]
    asm.begin_function(SYMBOL, exported=False)
    asm.loadreg(where, MCReg(arguments[0]))
    asm.loadreg(stack, MCReg(arguments[1]))
    asm.op(ops.XOR, count, MCReg(count), MCReg(count))

    frame = asm.reserve_label("frame")
    search = asm.reserve_label("search")
    found = asm.reserve_label("found")
    done = asm.reserve_label("done")
    written = asm.reserve_label("written")

    # -- one frame ------------------------------------------------------------
    asm.falls_through(frame)
    asm.block(frame)
    asm.address(row, frames.SYMBOL)
    asm.falls_through(search)
    asm.block(search)
    # The sentinel is the row with no line, and it is the last: reaching it is
    # having looked at every function there is.  It is asked about first so that
    # the row after this one is only ever read where there is one.
    _four(asm, first, row, _LENGTH)
    asm.branch(Condition.EQ, MCReg(first), MCImm(0, 32, signed=False), done)
    # Before the first function?  Then this is not an address this program
    # emitted, and there is nothing to say about it.
    asm.loadreg(second, asm.mem(base=row, disp=_START, size_bits=64))
    asm.branch(Condition.ULT, MCReg(where), MCReg(second), done)
    # And in this row's function, if the next row begins above it.
    asm.loadreg(second, asm.mem(base=row, disp=frames.ROW_BYTES + _START,
                                size_bits=64))
    asm.branch(Condition.ULT, MCReg(where), MCReg(second), found)
    asm.op(ops.PLUS, row, MCReg(row), MCImm(frames.ROW_BYTES, 32, signed=False))
    asm.jump(search)

    # -- what it says, and where it goes next ---------------------------------
    asm.block(found)
    # The innermost frame is where the fault was, which the message already
    # said.  What this is for is the frames above it.
    asm.branch(Condition.EQ, MCReg(count), MCImm(0, 32, signed=False), written)
    third = abi.arguments[2]
    asm.loadreg(third, MCReg(first))
    asm.address(abi.arguments[1], frames.SYMBOL)
    _four(asm, first, row, _LINE)
    asm.op(ops.PLUS, abi.arguments[1], MCReg(abi.arguments[1]), MCReg(first))
    asm.loadreg(abi.arguments[0], asm.imm(STANDARD_ERROR, 32, signed=False))
    asm.loadreg(abi.number, asm.imm(abi.write, 32, signed=False))
    abi.enter(asm)
    asm.falls_through(written)
    asm.block(written)
    # Where the caller is, and where its stack is.  A row that says the return
    # address is in no frame is a function that calls nothing, which cannot
    # have anything above it.
    _four(asm, first, row, _RETURN)
    asm.branch(Condition.EQ, MCReg(first),
               MCImm(frames.NO_RETURN_ADDRESS, 32, signed=False), done)
    asm.op(ops.PLUS, first, MCReg(first), MCReg(stack))
    _four(asm, second, row, _CALLER)
    asm.op(ops.PLUS, stack, MCReg(stack), MCReg(second))
    asm.loadreg(where, asm.mem(base=first, disp=0, size_bits=64))
    asm.op(ops.PLUS, count, MCReg(count), MCImm(1, 32, signed=False))
    asm.branch(Condition.ULT, MCReg(count), MCImm(LIMIT, 32, signed=False),
               frame)
    asm.falls_through(done)
    asm.block(done)
    asm.ret()
    asm.end_function()


def _four(asm: Assembler, dst: Reg, row: Reg, at: int) -> None:
    """Read one of a row's four-byte fields into the whole of *dst*.

    Without a sign, which is what makes the same four bytes the same number on
    all three: one of them copies the sign of a narrow load into the rest of the
    register and the other two copy nought, and a field read one way on one
    machine and the other way on another would be a field that means two things.
    """
    asm.widen(dst, asm.mem(base=row, disp=at, size_bits=32), 32, False)


#: Where each of a row's five fields is.  They are written here rather than in
#: the module that builds the rows because this is the only thing that reads
#: them, and a reader of either wants the two beside each other.
_START: Final[int] = 0
_RETURN: Final[int] = 8
_CALLER: Final[int] = 12
_LINE: Final[int] = 16
_LENGTH: Final[int] = 20
