"""The program's entry point.

``_start`` is built through the ordinary builder API rather than written out as a
string of bytes, so it is itself a test of the assembler and appears in the
debugging dump like any other function.

The binary depends on nothing from the system: there is no dynamic linker and no
C runtime, so this is the first instruction the kernel runs and the process
leaves through a system call rather than by returning to anything.
"""

from __future__ import annotations

from typing import Final, Mapping

from ...ir.mangle import symbol_name
from ...ir.module import Module
from ...mc import ops
from ...mc.asmbuilder import Assembler
from .. import statuses
from ..callconv import CallConvDesc
from ..tests import Failure, run_by
from . import ops as x86ops
from ..allocator import AllocatorRegs, SyscallABI
from ...mc.ops import Condition
from . import levels
from .regs import (EAX, EBP, EBX, ECX, EDI, EDX, ESI, INFO, RAX, RCX,
                   RDI, RDX, RSI, reg)

#: The number of the Linux system call that ends the whole process.
NR_EXIT_GROUP: Final[int] = 231

#: The name the entry point is given in the image.
ENTRY_SYMBOL: Final[str] = "_start"


def emit_start(asm: Assembler, module: Module, cconv: CallConvDesc,
               level: str = levels.DEFAULT,
               refused: str | None = None,
               failures: Mapping[int, Failure] | None = None) -> None:
    """Emit the entry point for *module*.

    The constructor and destructor loops emit nothing while a program has none,
    but they are real code: adding a constructor is a change in the semantic
    analysis alone, never in the backend.

    Before any of it, a program built for more than the oldest x86-64 asks the
    processor whether it can run at all.  *refused* is where the message it
    prints if it cannot was put; nothing is emitted without one, and nothing is
    emitted for the oldest level, which every x86-64 processor has by being one.
    """
    startup = module.startup
    assert startup is not None
    status32 = INFO.view(cconv.int_ret_regs[0].unit, 32)

    asm.begin_function(ENTRY_SYMBOL, exported=True)
    # The outermost stack frame is marked by a null frame pointer, so that a
    # debugger unwinding the stack knows where to stop.
    asm.op(ops.XOR, EBP, asm.reg(EBP), asm.reg(EBP))
    if refused is not None:
        _check_level(asm, level, refused)
    for ctor in module.ctors:
        asm.call(symbol_name(ctor))
    _run_tests(asm, module, cconv, failures or {})
    if module.test_plan:
        # A binary built to run tests and nothing else: every one of them
        # passed, or it left through the helper above and never arrived here.
        asm.loadreg(EDI, asm.imm(0, 32, signed=False))
    else:
        asm.call(symbol_name(startup))
        # The status is moved out of the return register before the destructors
        # run, because a destructor is an ordinary call and may use that
        # register.
        asm.loadreg(EDI, asm.reg(status32))
    for dtor in module.dtors:
        asm.call(symbol_name(dtor))
    asm.loadreg(EAX, asm.imm(NR_EXIT_GROUP, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    # exit_group does not return; trapping makes that explicit rather than
    # letting control run off the end of the section.
    asm.op(ops.TRAP)
    asm.end_function()


#: The number of the Linux system call that writes to a file descriptor, and
#: the descriptor a fault reports through.  A fault has nowhere else to go: the
#: program depends on nothing from the system, and the input and output the
#: language will have does not exist at the point where the first faults can
#: happen.
NR_WRITE: Final[int] = 1

#: What a fault writes to, which is standard error by the number every system
#: gives it.  Whether it is open is not checked: a program started with it
#: closed would have the message go to whatever was opened next, and that is
#: still better than the message going nowhere.
STANDARD_ERROR: Final[int] = 2

#: The name the helper a fault leaves the program through is given.
ABORT_SYMBOL: Final[str] = "__pl4g_abort"


#: The number of the Linux system call that writes, and the descriptor the
#: message goes to.
NR_WRITE_HERE: Final[int] = 1
STANDARD_ERROR_HERE: Final[int] = 2


def _run_tests(asm: Assembler, module: Module, cconv: CallConvDesc,
               failures: Mapping[int, Failure]) -> None:
    """Call each test this binary runs, leaving through the fault helper.

    A test answers a truth value, which comes back widened to the whole of the
    register the compiler's own calls read it out of -- so it is compared the
    way they compare it.  One that answers false names itself and
    stops the program: it is a program that has been found to be wrong, which is
    what that helper is for, and there is nothing further a binary could
    usefully do after being told it is not fit to run.
    """
    for one in run_by(module):
        found = failures.get(id(one))
        if found is None:
            continue
        asm.call(symbol_name(one))
        passed = asm.reserve_label("test.passed")
        answer = INFO.view(cconv.int_ret_regs[0].unit, 32)
        asm.branch(Condition.NE, asm.reg(answer),
                   asm.imm(0, 32, signed=False), passed)
        asm.address(RDI, found.symbol)
        asm.loadreg(ESI, asm.imm(found.length, 32, signed=False))
        asm.call(ABORT_SYMBOL)
        asm.block(passed)


def _check_level(asm: Assembler, level: str, refused: str) -> None:
    """Ask the processor whether it has what this program was built to use.

    One `CPUID` per leaf, its answer masked down to the bits the level wants and
    compared against that mask: all of them or none of it.  The leaf numbers and
    the bits come from `levels`, which is where the architecture's own tables
    are written down; nothing about which bits mean what is decided here.

    A leaf has to exist before it can be asked.  Leaf zero answers with the
    highest ordinary leaf and leaf 0x80000000 with the highest extended one, so
    each is asked once before anything that needs it -- a processor old enough
    not to have leaf seven is old enough not to have what leaf seven reports.
    """
    wanted = levels.requirements(level)
    if not wanted:
        return
    fails = asm.reserve_label("mclevel.no")
    runs = asm.reserve_label("mclevel.yes")
    for highest in sorted({0x80000000 if one.leaf >= 0x80000000 else 0
                           for one in wanted}):
        asm.loadreg(EAX, asm.imm(highest, 32, signed=False))
        asm.op(x86ops.CPUID)
        needed = max(one.leaf for one in wanted
                     if (one.leaf >= 0x80000000) == (highest != 0))
        asm.branch(Condition.ULT, asm.reg(EAX), asm.imm(needed, 32, signed=False),
                   fails)
    for one in wanted:
        asm.loadreg(EAX, asm.imm(one.leaf, 32, signed=False))
        asm.loadreg(ECX, asm.imm(one.subleaf, 32, signed=False))
        asm.op(x86ops.CPUID)
        answer = {"eax": EAX, "ebx": EBX, "ecx": ECX, "edx": EDX}[one.register]
        asm.op(ops.AND, answer, asm.reg(answer),
               asm.imm(one.bits, 32, signed=False))
        asm.branch(Condition.NE, asm.reg(answer),
                   asm.imm(one.bits, 32, signed=False), fails)
    asm.jump(runs)
    asm.block(fails)
    asm.loadreg(EDI, asm.imm(STANDARD_ERROR_HERE, 32, signed=False))
    asm.address(RSI, refused)
    asm.loadreg(EDX, asm.imm(len(levels.described(level).encode("utf-8")), 32,
                             signed=False))
    asm.loadreg(EAX, asm.imm(NR_WRITE_HERE, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    asm.loadreg(EDI, asm.imm(statuses.WRONG_PROCESSOR, 32, signed=False))
    asm.loadreg(EAX, asm.imm(NR_EXIT_GROUP, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    asm.op(ops.TRAP)
    asm.block(runs)


def emit_abort(asm: Assembler, cconv: CallConvDesc) -> None:
    """Emit the helper that reports a fault and stops the program.

    It takes the message and its length, writes them, and traps.  Everything
    that could be worked out beforehand was: the message is built whole at
    compile time, so there is no formatting here, no number to turn into text,
    and nothing that could itself fail.

    It ends by exiting rather than by trapping, with a status out of the range
    the runtime reserves.  A signal is not a status: a shell reports one as 128
    plus the number, which collides with whatever the program might have chosen
    to exit with, and a caller has to know to look for it.  A program that dies
    of a signal really did die of one, and that is worth being able to believe.
    """
    first, second, third = cconv.int_arg_regs[:3]
    asm.begin_function(ABORT_SYMBOL, exported=False)
    # The three arguments of the system call are the two this was given, moved
    # up one place, with the descriptor put in front of them.
    asm.loadreg(third, asm.reg(second))
    asm.loadreg(second, asm.reg(first))
    asm.loadreg(first, asm.imm(STANDARD_ERROR, 32, signed=False))
    asm.loadreg(EAX, asm.imm(NR_WRITE, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    asm.loadreg(EDI, asm.imm(statuses.GENERAL, 32, signed=False))
    asm.loadreg(EAX, asm.imm(NR_EXIT_GROUP, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    # exit_group does not return; trapping makes that explicit rather than
    # letting control run off the end of the section.
    asm.op(ops.TRAP)
    asm.end_function()


#: The numbers of the two system calls the allocator makes, and what a call
#: looks like here: the number in the accumulator, the arguments in the
#: registers the kernel names -- which are the convention's first three and then
#: three the convention does not use in that order -- and the answer back in the
#: accumulator.  The instruction destroys two further registers, which is why
#: the allocator keeps nothing in one across it.
NR_MMAP: Final[int] = 9
NR_MUNMAP: Final[int] = 11

SYSCALLS: Final[SyscallABI] = SyscallABI(
    mmap=NR_MMAP, munmap=NR_MUNMAP, number=EAX,
    arguments=(RDI, RSI, RDX, reg("r10"), reg("r8"), reg("r9")),
    answer=RAX, enter=lambda asm: asm.op(x86ops.SYSCALL),
    # What `syscall` itself destroys: it puts the return address in rcx and the
    # flags in r11, which is the instruction's doing and not the kernel's.
    clobbers=(RCX, reg("r11")))

#: Where the allocator's own arguments arrive and its answer goes, and two
#: registers a caller does not expect back.
ALLOCATOR_REGS: Final[AllocatorRegs] = AllocatorRegs(
    arena=RDI, size=RSI, answer=RAX,
    scratch=(RDX, RCX, reg("r11")))
