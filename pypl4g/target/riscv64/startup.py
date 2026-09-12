"""The program's entry point on RISC-V.

Built through the ordinary builder API, so it is itself a test of the assembler
and appears in the debugging dump like any other function.

The binary depends on nothing from the system: this is the first instruction the
kernel runs, and the process leaves through an environment call rather than by
returning to anything.
"""

from typing import Final

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
        asm.call(ctor.name)
    asm.call(startup.name)
    # The value a function returns is already in the register a system call
    # takes its first argument in, so nothing has to be moved -- unless a
    # destructor runs in between and is free to clobber it.
    if module.dtors:
        asm.loadreg(S1, asm.reg(status))
        for dtor in module.dtors:
            asm.call(dtor.name)
        asm.loadreg(status, asm.reg(S1))
    asm.loadreg(A7, asm.imm(NR_EXIT_GROUP, 12))
    asm.op(rvops.ENVIRONMENT_CALL)
    # exit_group does not return; trapping makes that explicit rather than
    # letting control run off the end of the section.
    asm.op(ops.TRAP)
    asm.end_function()
