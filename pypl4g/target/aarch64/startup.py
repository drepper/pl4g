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
from .regs import X8, X19, X29, X30, XZR

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
