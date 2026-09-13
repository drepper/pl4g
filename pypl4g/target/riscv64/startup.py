"""The program's entry point on RISC-V.

Built through the ordinary builder API, so it is itself a test of the assembler
and appears in the debugging dump like any other function.

The binary depends on nothing from the system: this is the first instruction the
kernel runs, and the process leaves through an environment call rather than by
returning to anything.
"""

from typing import Final

from ...ir.mangle import symbol_name
from ...ir.module import Module
from ...mc import ops
from ...mc.asmbuilder import Assembler
from ..callconv import CallConvDesc
from . import ops as rvops
from .regs import A7, FP, RA, S1, ZERO

#: The number of the Linux system call that ends the whole process.  It is the
#: number the architectures that came after x86-64 share.
NR_EXIT_GROUP: Final[int] = 94

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
    asm.loadreg(FP, asm.reg(ZERO))
    asm.loadreg(RA, asm.reg(ZERO))
    for ctor in module.ctors:
        asm.call(symbol_name(ctor))
    asm.call(symbol_name(startup))
    # The value a function returns is already in the register a system call
    # takes its first argument in, so nothing has to be moved -- unless a
    # destructor runs in between and is free to clobber it.
    if module.dtors:
        asm.loadreg(S1, asm.reg(status))
        for dtor in module.dtors:
            asm.call(symbol_name(dtor))
        asm.loadreg(status, asm.reg(S1))
    asm.loadreg(A7, asm.imm(NR_EXIT_GROUP, 12))
    asm.op(rvops.ENVIRONMENT_CALL)
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
    asm.loadreg(A7, asm.imm(NR_WRITE, 12))
    asm.op(rvops.ENVIRONMENT_CALL)
    asm.op(ops.TRAP)
    asm.end_function()
