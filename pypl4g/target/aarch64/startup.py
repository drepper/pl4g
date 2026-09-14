"""The program's entry point on AArch64.

Built through the ordinary builder API, so it is itself a test of the assembler
and appears in the debugging dump like any other function.

The binary depends on nothing from the system: this is the first instruction the
kernel runs, and the process leaves through a supervisor call rather than by
returning to anything.
"""

from typing import Final

from ...ir.mangle import symbol_name
from ...ir.module import Module
from ...mc import ops
from ...mc.asmbuilder import Assembler
from ..callconv import CallConvDesc
from . import ops as a64ops
from ..allocator import AllocatorRegs, SyscallABI
from .regs import X0, X1, X8, X19, X29, X30, XZR, reg

#: The number of the Linux system call that ends the whole process.  The number
#: differs from the one x86-64 uses, which is why it belongs to the backend.
NR_EXIT_GROUP: Final[int] = 94

#: The register a system call number is passed in.
SYSCALL_NUMBER_REG: Final = X8

#: The name the entry point is given in the image.
ENTRY_SYMBOL: Final[str] = "_start"


def emit_start(asm: Assembler, module: Module, cconv: CallConvDesc) -> None:
    """Emit the entry point for *module*.

    The constructor and destructor loops emit nothing while a program has none,
    but they are real code: adding a constructor is a change in the semantic
    analysis alone, never in the backend.
    """
    startup = module.startup
    assert startup is not None
    status = cconv.int_ret_regs[0]

    asm.begin_function(ENTRY_SYMBOL, exported=True)
    # The outermost stack frame is marked by a null frame pointer and a null
    # return address, so that a debugger unwinding the stack knows where to stop.
    asm.loadreg(X29, asm.reg(XZR))
    asm.loadreg(X30, asm.reg(XZR))
    for ctor in module.ctors:
        asm.call(symbol_name(ctor))
    asm.call(symbol_name(startup))
    # The value a function returns is already in the register a system call
    # takes its first argument in, so nothing has to be moved -- unless a
    # destructor runs in between and is free to clobber it.
    if module.dtors:
        asm.loadreg(X19, asm.reg(status))
        for dtor in module.dtors:
            asm.call(symbol_name(dtor))
        asm.loadreg(status, asm.reg(X19))
    asm.loadreg(SYSCALL_NUMBER_REG,
                asm.imm(NR_EXIT_GROUP, 16, signed=False))
    asm.op(a64ops.SUPERVISOR_CALL)
    # exit_group does not return; trapping makes that explicit rather than
    # letting control run off the end of the section.
    asm.op(ops.TRAP)
    asm.end_function()


#: The number of the Linux system call that writes to a file descriptor, and
#: the descriptor a fault reports through.  A fault has nowhere else to go: the
#: program depends on nothing from the system, and the input and output the
#: language will have does not exist at the point where the first faults can
#: happen.
NR_WRITE: Final[int] = 64

#: What a fault writes to, which is standard error by the number every system
#: gives it.  Whether it is open is not checked: a program started with it
#: closed would have the message go to whatever was opened next, and that is
#: still better than the message going nowhere.
STANDARD_ERROR: Final[int] = 2

#: The name the helper a fault leaves the program through is given.
ABORT_SYMBOL: Final[str] = "__pl4g_abort"


def emit_abort(asm: Assembler, cconv: CallConvDesc) -> None:
    """Emit the helper that reports a fault and stops the program.

    It takes the message and its length, writes them, and traps.  Everything
    that could be worked out beforehand was: the message is built whole at
    compile time, so there is no formatting here, no number to turn into text,
    and nothing that could itself fail.

    It ends by trapping rather than by exiting, so the program dies by a signal
    at the point of the fault with its stack still standing, which is what a
    debugger wants to be handed.  The message has already been written by then,
    so nothing is lost to the signal.
    """
    first, second, third = cconv.int_arg_regs[:3]
    asm.begin_function(ABORT_SYMBOL, exported=False)
    # The three arguments of the system call are the two this was given, moved
    # up one place, with the descriptor put in front of them.
    asm.loadreg(third, asm.reg(second))
    asm.loadreg(second, asm.reg(first))
    asm.loadreg(first, asm.imm(STANDARD_ERROR, 32, signed=False))
    asm.loadreg(SYSCALL_NUMBER_REG,
                asm.imm(NR_WRITE, 16, signed=False))
    asm.op(a64ops.SUPERVISOR_CALL)
    asm.op(ops.TRAP)
    asm.end_function()


#: The numbers of the two system calls the allocator makes, and what a call
#: looks like here: the number in the register the kernel reads it from, the
#: arguments in the first six of the convention's, and the answer back in the
#: first of them.
NR_MMAP: Final[int] = 222
NR_MUNMAP: Final[int] = 215

SYSCALLS: Final[SyscallABI] = SyscallABI(
    mmap=NR_MMAP, munmap=NR_MUNMAP, number=SYSCALL_NUMBER_REG,
    arguments=tuple(reg("".join(("x", str(n)))) for n in range(6)),
    answer=X0, enter=lambda asm: asm.op(a64ops.SUPERVISOR_CALL))

#: Where the allocator's own arguments arrive and its answer goes, and two
#: registers a caller does not expect back.
ALLOCATOR_REGS: Final[AllocatorRegs] = AllocatorRegs(
    arena=X0, size=X1, answer=X0,
    scratch=(reg("x9"), reg("x10"), reg("x11")))
