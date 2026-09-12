"""The program's entry point.

``_start`` is built through the ordinary builder API rather than written out as a
string of bytes, so it is itself a test of the assembler and appears in the
debugging dump like any other function.

The binary depends on nothing from the system: there is no dynamic linker and no
C runtime, so this is the first instruction the kernel runs and the process
leaves through a system call rather than by returning to anything.
"""

from typing import Final

from ...ir.module import Module
from ...mc import ops
from ...mc.asmbuilder import Assembler
from ..callconv import CallConvDesc
from . import ops as x86ops
from .regs import EAX, EBP, EDI, INFO

#: The number of the Linux system call that ends the whole process.
NR_EXIT_GROUP: Final[int] = 231

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
    status32 = INFO.view(cconv.int_ret_regs[0].unit, 32)

    asm.begin_function(ENTRY_SYMBOL, exported=True)
    # The outermost stack frame is marked by a null frame pointer, so that a
    # debugger unwinding the stack knows where to stop.
    asm.op(ops.XOR, EBP, asm.reg(EBP), asm.reg(EBP))
    for ctor in module.ctors:
        asm.call(ctor.name)
    asm.call(startup.name)
    # The status is moved out of the return register before the destructors run,
    # because a destructor is an ordinary call and may use that register.
    asm.loadreg(EDI, asm.reg(status32))
    for dtor in module.dtors:
        asm.call(dtor.name)
    asm.loadreg(EAX, asm.imm(NR_EXIT_GROUP, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    # exit_group does not return; trapping makes that explicit rather than
    # letting control run off the end of the section.
    asm.op(ops.TRAP)
    asm.end_function()
