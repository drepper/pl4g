"""The stack a program runs on, and the guard below it.

A program does not run on the stack the kernel gave it.  Before anything of it
runs, its entry point asks the system for one of the size the program was built
with, with a guard below that nothing may touch, and moves the stack pointer
there.  Running off the bottom is then a fault in the guard, which a handler
recognizes and reports as a status of its own rather than as a signal.

**Why not the stack the kernel supplied.**  Its size belongs to whoever started
the program and is a `ulimit` away from being something else; its guard is one
page, which a single large frame steps over; and exhausting it is a SIGSEGV
indistinguishable from following a bad address.  A generator emitting a program
knows how deep that program goes, and none of the three things it wants -- to
say how much, to be sure the bottom is caught, and to be told apart when it is
-- can be asked of the environment the program is started in.

**It is written here once and emitted for every architecture**, the way the
allocator beside it is: what differs between them is the number of a system
call, which registers its arguments go in, and which instruction enters the
kernel.  That much is a small record each backend fills in.  Nothing of this is
compiled from C and carried in the image -- it is a few dozen instructions, and
a few dozen instructions the compiler selects are cheaper to place, cheaper to
read in a dump, and do not pull a packaged object into a program that wanted
nothing else from one.

**One mapping, then part of it taken away.**  The whole of it is asked for
readable and writable and not populated, and the guard is then turned to no
access at all.  What is mapped is `guard + size + ALT_STACK`: the guard at the
bottom, the program's stack above it, and above that the little stack the
handler runs on -- there being no room on the stack that just ran out, which is
the whole difficulty of catching this.  One mapping rather than three is one
call rather than three, and it puts the three regions in a known order.

**It is asked for below the stack the kernel made.**  The address is a hint and
not a demand, so a kernel that would rather put it elsewhere does; what the hint
is worth is that the program's stack stays in the part of the address space a
stack lives in, rather than in the middle of where mappings are handed out.

**Nothing of it is required to succeed.**  Where the system will not have it the
program carries on with the stack it was given, which is what every program had
before this.  A stack that could not be guarded is not used at all: it is given
back, and the program stays where it was.  A stack without a guard is the thing
this exists to avoid, and having one silently would be worse than having none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, Sequence

from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg
from ..mc.symbol import SymBinding, SymKind, SymVisibility
from . import statuses
from .allocator import SyscallABI

#: How wide a word is here.  All three targets have eight-byte pointers.
WORD: Final[int] = 8

#: How much room the handler runs in.  It uses almost none itself; what wants
#: the room is the frame the kernel pushes in front of it, which on one of these
#: architectures holds the whole of the processor's floating-point state.
ALT_STACK: Final[int] = 16 * 1024

#: How far below the stack the kernel made the program's own is asked for, and
#: the grain the address is brought down to.  Linux leaves a gap below a stack
#: that nothing may be mapped into -- a megabyte of one, by default -- so a hint
#: any closer than this would simply be ignored.
GAP: Final[int] = 2 << 20
HINT_GRAIN: Final[int] = 2 << 20

#: PROT_NONE and PROT_READ | PROT_WRITE, and MAP_PRIVATE | MAP_ANONYMOUS with
#: MAP_NORESERVE: the room is address space and not memory, and it is only
#: memory where it is used.  A stack that reserved a megabyte of swap on a
#: system that counts it would be asking for what it does not want.
PROT_NONE: Final[int] = 0
PROT_READ_WRITE: Final[int] = 3
MAP_PRIVATE_ANON_NORESERVE: Final[int] = 0x4022

#: The signal a fault in the guard arrives as, and how the action asking for it
#: is written: the address that faulted is wanted, which is what SA_SIGINFO
#: says, and the handler must not run on the stack that ran out, which is what
#: SA_ONSTACK says.  SA_RESTORER is one kernel's business and is added there.
SIGSEGV: Final[int] = 11
SA_SIGINFO: Final[int] = 0x00000004
SA_ONSTACK: Final[int] = 0x08000000
SA_RESTORER: Final[int] = 0x04000000

#: How large the kernel's idea of a signal mask is, which `rt_sigaction` is told
#: so that it knows which of the two shapes it is being handed.
SIGSET_SIZE: Final[int] = 8

#: Where the address that faulted is in what the kernel puts in front of a
#: signal's information: three ints, a word of padding, and then the address.
#: The place is the same on all three of these architectures.
SI_ADDR: Final[int] = 16

#: A system call answers with a small negative number where it failed, and
#: every other answer `mmap` can give is an address.
ERROR_WINDOW: Final[int] = 4096

#: The state the handler reads and the two structures the calls are handed, all
#: in one object: there is one of each per program, none of them is ever read by
#: anything else, and one object is one address to find them all from.
LOW: Final[int] = 0
HIGH: Final[int] = WORD
#: The action that asks for the handler, and the one that puts the default back:
#: a handler, flags, a restorer where the architecture has one, and a mask.
ACTION: Final[int] = 2 * WORD
PLAIN: Final[int] = 6 * WORD
ACTION_HANDLER: Final[int] = 0
ACTION_FLAGS: Final[int] = WORD
ACTION_RESTORER: Final[int] = 2 * WORD
#: And where the handler runs: where it is, its flags, and how large it is.
ALT: Final[int] = 10 * WORD
ALT_SP: Final[int] = 0
ALT_FLAGS: Final[int] = WORD
ALT_SIZE: Final[int] = 2 * WORD

STATE_SIZE: Final[int] = 13 * WORD

#: What the program says when it runs out, which is the whole of what it says:
#: there is no unwinder, so what it was doing at the time cannot be told.
MESSAGE: Final[str] = "pl4g: the stack ran out\n"

#: The names the three pieces are known by.
STATE_SYMBOL: Final[str] = "__pl4g_stack_state"
HANDLER_SYMBOL: Final[str] = "__pl4g_stack_fault"
RESTORER_SYMBOL: Final[str] = "__pl4g_stack_return"
MESSAGE_SYMBOL: Final[str] = ".Lstack.message"

#: Where the state lives, which has to be writable: the bounds of the guard are
#: not known until the program runs.
STATE_SECTION: Final[str] = ".data"
#: And where the message lives, which does not.
MESSAGE_SECTION: Final[str] = ".rodata"


@dataclass(frozen=True, slots=True)
class StackABI:
    """What one architecture needs beyond what the allocator's record says.

    The system calls are numbered by the architecture, the registers are the
    ones free at the entry point and in the handler, and the stack pointer is
    not read and written the same way everywhere.
    """

    #: The numbers of the calls made beyond `mmap` and `munmap`.
    mprotect: int
    sigaltstack: int
    rt_sigaction: int
    write: int
    exit_group: int
    #: The largest page this architecture may be configured with.  What is
    #: protected has to be a whole number of pages of whatever the running
    #: kernel chose, and the program is built once for all of them.
    page: int
    #: Three registers a system call leaves alone and the entry point is not
    #: otherwise using, and three the handler may destroy.
    kept: Sequence[Reg]
    scratch: Sequence[Reg]
    #: Reading and writing the stack pointer, which is a move on two of these
    #: and an addition of nothing on the third.
    read_sp: Callable[[Assembler, Reg], None]
    write_sp: Callable[[Assembler, Reg], None]
    #: The number of the call that returns from a handler, for the kernel that
    #: does not return from one itself, and nothing for the two that do.
    rt_sigreturn: int | None = None


def rounded(size: int, grain: int) -> int:
    """*size* brought up to a whole number of *grain*."""
    return (size + grain - 1) & ~(grain - 1)


def _a_number(asm: Assembler, where: Reg, value: int) -> None:
    """Put a number in a register, however many instructions that takes.

    A megabyte does not fit the immediate any of these carries, so the move is
    what works out how to build it.
    """
    asm.loadreg(where, asm.imm(value, 32 if value < (1 << 31) else 64,
                               signed=False))


def _mask_down(asm: Assembler, value: Reg, grain: int, scratch: Reg) -> None:
    """Bring *value* down to the next multiple of *grain* below it."""
    _a_number(asm, scratch, grain - 1)
    asm.op(ops.NOT, scratch, MCReg(scratch))
    asm.op(ops.AND, value, MCReg(value), MCReg(scratch))


def emit_make(asm: Assembler, calls: SyscallABI, abi: StackABI,
              stack_size: int, guard_size: int) -> None:
    """Emit, into the entry point, the making of the stack and the switch to it.

    Nothing is called: this is the entry point's own code, since a call would
    need the stack this is making one of.  What it costs is a few dozen
    instructions run once, and four system calls.
    """
    guard = rounded(guard_size, abi.page)
    size = rounded(stack_size, abi.page)
    total = guard + size + ALT_STACK
    first, second, third, fourth = calls.arguments[:4]
    fifth, sixth = calls.arguments[4:6]
    base, other, top = abi.kept[:3]
    keep = asm.reserve_label("stack.as.it.was")

    # Where to ask for it: below the stack the kernel made, by more than the gap
    # it leaves below one, and on a grain coarse enough to be a page anywhere.
    abi.read_sp(asm, base)
    _a_number(asm, other, GAP + total)
    asm.op(ops.MINUS, base, MCReg(base), MCReg(other))
    _mask_down(asm, base, HINT_GRAIN, other)
    # mmap(hint, total, PROT_READ|PROT_WRITE, MAP_PRIVATE|ANONYMOUS|NORESERVE,
    #      -1, 0).  The descriptor is all ones made from zero, which is one
    # instruction everywhere where writing the number is two on two of them.
    asm.loadreg(first, MCReg(base))
    _a_number(asm, second, total)
    _a_number(asm, third, PROT_READ_WRITE)
    _a_number(asm, fourth, MAP_PRIVATE_ANON_NORESERVE)
    _a_number(asm, fifth, 0)
    asm.op(ops.NOT, fifth, MCReg(fifth))
    _a_number(asm, sixth, 0)
    _a_number(asm, calls.number, calls.mmap)
    calls.enter(asm)
    # Read unsigned, the answers that are refusals are the largest values there
    # are, so one comparison tells them from every address.
    asm.loadreg(base, MCReg(calls.answer))
    _a_number(asm, other, ERROR_WINDOW)
    asm.op(ops.NOT, other, MCReg(other))
    asm.branch(Condition.UGT, MCReg(base), MCReg(other), keep)
    # mprotect(base, guard, PROT_NONE): the guard is what is left of the mapping
    # once the rest of it has been made reachable, so there is no moment at
    # which it is ordinary memory and no second mapping to place.
    asm.loadreg(first, MCReg(base))
    _a_number(asm, second, guard)
    _a_number(asm, third, PROT_NONE)
    _a_number(asm, calls.number, abi.mprotect)
    calls.enter(asm)
    guarded = asm.reserve_label("stack.guarded")
    _a_number(asm, other, 0)
    asm.branch(Condition.EQ, MCReg(calls.answer), MCReg(other), guarded)
    # A stack that cannot be guarded is not used: it is given back, and the
    # program stays on the one it was given.  Having one silently unguarded is
    # the thing this exists to avoid.
    asm.loadreg(first, MCReg(base))
    _a_number(asm, second, total)
    _a_number(asm, calls.number, calls.munmap)
    calls.enter(asm)
    asm.jump(keep)
    asm.block(guarded)
    # What the handler reads: where the guard begins and where it ends.
    asm.address(other, STATE_SYMBOL)
    asm.store(asm.mem(base=other, disp=LOW, size_bits=64), MCReg(base))
    _a_number(asm, top, guard)
    asm.op(ops.PLUS, top, MCReg(top), MCReg(base))
    asm.store(asm.mem(base=other, disp=HIGH, size_bits=64), MCReg(top))
    # The top of the program's stack, which is also the bottom of the little one
    # the handler runs on.
    _a_number(asm, base, size)
    asm.op(ops.PLUS, top, MCReg(top), MCReg(base))
    asm.store(asm.mem(base=other, disp=ALT + ALT_SP, size_bits=64), MCReg(top))
    _a_number(asm, base, 0)
    asm.store(asm.mem(base=other, disp=ALT + ALT_FLAGS, size_bits=64),
              MCReg(base))
    _a_number(asm, base, ALT_STACK)
    asm.store(asm.mem(base=other, disp=ALT + ALT_SIZE, size_bits=64),
              MCReg(base))
    # sigaltstack(&it, 0)
    asm.loadreg(first, MCReg(other))
    _a_number(asm, second, ALT)
    asm.op(ops.PLUS, first, MCReg(first), MCReg(second))
    _a_number(asm, second, 0)
    _a_number(asm, calls.number, abi.sigaltstack)
    calls.enter(asm)
    # The action: the handler, the flags that ask for the address and for the
    # stack of its own, and -- where the architecture has one -- the few
    # instructions a handler returns through.  The mask is left as it was made,
    # which is nothing: a fault in the guard is not returned from.
    asm.address(base, HANDLER_SYMBOL)
    asm.store(asm.mem(base=other, disp=ACTION + ACTION_HANDLER, size_bits=64),
              MCReg(base))
    flags = SA_SIGINFO | SA_ONSTACK
    if abi.rt_sigreturn is not None:
        flags |= SA_RESTORER
    _a_number(asm, base, flags)
    asm.store(asm.mem(base=other, disp=ACTION + ACTION_FLAGS, size_bits=64),
              MCReg(base))
    if abi.rt_sigreturn is not None:
        asm.address(base, RESTORER_SYMBOL)
        asm.store(
            asm.mem(base=other, disp=ACTION + ACTION_RESTORER, size_bits=64),
            MCReg(base))
    # rt_sigaction(SIGSEGV, &action, 0, sizeof mask)
    asm.loadreg(second, MCReg(other))
    _a_number(asm, first, ACTION)
    asm.op(ops.PLUS, second, MCReg(second), MCReg(first))
    _a_number(asm, first, SIGSEGV)
    _a_number(asm, third, 0)
    _a_number(asm, fourth, SIGSET_SIZE)
    _a_number(asm, calls.number, abi.rt_sigaction)
    calls.enter(asm)
    # Last of all, because everything above ran on the stack being left.
    abi.write_sp(asm, top)
    asm.falls_through(keep)
    asm.block(keep)


def emit_handler(asm: Assembler, calls: SyscallABI, abi: StackABI) -> None:
    """Emit what the kernel calls when the program follows a bad address.

    It is handed the signal, what the kernel knows about the fault, and the
    state the program was stopped in; only the second is read, for the address,
    which is the whole of what SA_SIGINFO is asked for.

    **A fault anywhere else is not this to report.**  The handler puts the
    default back and returns, so the instruction runs again and the program dies
    of the signal it really got -- core file and all.  A handler that reported
    every bad address as a stack that ran out would be wrong about most of them.
    """
    first, second, third, fourth = calls.arguments[:4]
    at, state, edge = abi.scratch[:3]
    asm.begin_function(HANDLER_SYMBOL, exported=False)
    asm.loadreg(at, asm.mem(base=calls.arguments[1], disp=SI_ADDR,
                            size_bits=64))
    asm.address(state, STATE_SYMBOL)
    elsewhere = asm.reserve_label("not.the.guard")
    asm.loadreg(edge, asm.mem(base=state, disp=LOW, size_bits=64))
    asm.branch(Condition.ULT, MCReg(at), MCReg(edge), elsewhere)
    asm.loadreg(edge, asm.mem(base=state, disp=HIGH, size_bits=64))
    asm.branch(Condition.UGE, MCReg(at), MCReg(edge), elsewhere)
    # write(2, message, length)
    _a_number(asm, first, 2)
    asm.address(second, MESSAGE_SYMBOL)
    _a_number(asm, third, len(MESSAGE.encode("utf-8")))
    _a_number(asm, calls.number, abi.write)
    calls.enter(asm)
    # exit_group(the status that says the stack ran out)
    _a_number(asm, first, statuses.STACK_OVERFLOW)
    _a_number(asm, calls.number, abi.exit_group)
    calls.enter(asm)
    asm.op(ops.TRAP)
    asm.block(elsewhere)
    # rt_sigaction(SIGSEGV, &nothing, 0, sizeof mask), which is the default
    # back: the structure has been zero since the image was written.
    asm.loadreg(second, MCReg(state))
    _a_number(asm, first, PLAIN)
    asm.op(ops.PLUS, second, MCReg(second), MCReg(first))
    _a_number(asm, first, SIGSEGV)
    _a_number(asm, third, 0)
    _a_number(asm, fourth, SIGSET_SIZE)
    _a_number(asm, calls.number, abi.rt_sigaction)
    calls.enter(asm)
    asm.ret()
    asm.end_function()
    if abi.rt_sigreturn is not None:
        _emit_restorer(asm, calls, abi)


def _emit_restorer(asm: Assembler, calls: SyscallABI, abi: StackABI) -> None:
    """Emit the few instructions a handler returns through.

    One kernel does not return from a handler by itself: what returns is code
    the program supplies, whose address goes in the action.  Nothing calls this
    -- the kernel arranges for the handler to return to it -- so it is a
    function only in the sense that it is a run of instructions with a name.
    """
    assert abi.rt_sigreturn is not None
    asm.begin_function(RESTORER_SYMBOL, exported=False)
    _a_number(asm, calls.number, abi.rt_sigreturn)
    calls.enter(asm)
    asm.op(ops.TRAP)
    asm.end_function()


def emit_state(asm: Assembler) -> None:
    """Put the state and the message into the image.

    The state is nothing but zeros: what goes in it is not known until the
    program runs.  It is written out rather than left to a section of no
    contents because there is no such section in this image yet, and a hundred
    bytes is not worth inventing one for.
    """
    asm.section(STATE_SECTION, writable=True, alignment=WORD)
    # The section may already exist, and what it was made with is what it keeps,
    # so what this needs is asked for here rather than assumed.
    asm.align(WORD)
    symbol = asm.label(STATE_SYMBOL, binding=SymBinding.LOCAL,
                       kind=SymKind.OBJECT, visibility=SymVisibility.HIDDEN)
    asm.bytes(bytes(STATE_SIZE))
    asm.end_label(symbol)
    asm.section(MESSAGE_SECTION, writable=False, alignment=1)
    said = asm.label(MESSAGE_SYMBOL, binding=SymBinding.LOCAL,
                     kind=SymKind.OBJECT, visibility=SymVisibility.HIDDEN)
    asm.bytes(MESSAGE.encode("utf-8"))
    asm.end_label(said)
