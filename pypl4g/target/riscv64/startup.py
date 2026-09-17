"""The program's entry point on RISC-V.

Built through the ordinary builder API, so it is itself a test of the assembler
and appears in the debugging dump like any other function.

The binary depends on nothing from the system: this is the first instruction the
kernel runs, and the process leaves through an environment call rather than by
returning to anything.
"""

from __future__ import annotations

from typing import Final, Mapping

from ...ir.mangle import symbol_name
from ...ir.module import Module
from ...mc import ops
from ...mc.ops import Condition
from ...mc.asmbuilder import Assembler
from .. import statuses
from ..callconv import CallConvDesc
from .. import started
from ..tests import Failure, run_by
from . import ops as rvops
from ..allocator import AllocatorRegs, SyscallABI
from .regs import A0, A1, A7, FP, RA, S1, ZERO, reg

#: The number of the Linux system call that ends the whole process.  It is the
#: number the architectures that came after x86-64 share.
NR_EXIT_GROUP: Final[int] = 94

#: The name the entry point is given in the image.
ENTRY_SYMBOL: Final[str] = "_start"


def emit_start(asm: Assembler, module: Module, cconv: CallConvDesc,
               failures: Mapping[int, Failure] | None = None) -> None:
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
    _run_tests(asm, module, cconv, failures or {})
    if module.test_plan:
        # A binary built to run tests and nothing else: every one of them
        # passed, or it left through the helper above and never arrived here.
        asm.loadreg(status, asm.imm(0, 12))
    else:
        if started.wanted_by(module) is not None:
            # Where the record the program was started with is, which is the
            # whole of what is handed over.
            asm.address(cconv.int_arg_regs[0], started.SYMBOL)
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

#: And the one a failing test says so through, which writes the same way and
#: comes back rather than leaving.
REPORT_SYMBOL: Final[str] = "__pl4g_report"

#: Where the count of tests that did not pass is kept while they run.  A
#: register a call leaves alone, so that nothing has to be saved around one and
#: no storage has to be found for a number that lives for a few instructions.
COUNT_REG: Final = reg("s2")


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
    asm.loadreg(A7, asm.imm(NR_WRITE, 12))
    asm.op(rvops.ENVIRONMENT_CALL)
    asm.loadreg(cconv.int_arg_regs[0], asm.imm(statuses.GENERAL, 12))
    asm.loadreg(A7, asm.imm(NR_EXIT_GROUP, 12))
    asm.op(rvops.ENVIRONMENT_CALL)
    # exit_group does not return; trapping makes that explicit rather than
    # letting control run off the end of the section.
    asm.op(ops.TRAP)
    asm.end_function()


def emit_report(asm: Assembler, cconv: CallConvDesc) -> None:
    """Emit the helper a failing test says so through.

    The write the helper above makes, and then a return rather than an exit: a
    test that did not pass is something to say and not something to stop for,
    there being the rest of them still to run.
    """
    first, second, third = cconv.int_arg_regs[:3]
    asm.begin_function(REPORT_SYMBOL, exported=False)
    asm.loadreg(third, asm.reg(second))
    asm.loadreg(second, asm.reg(first))
    asm.loadreg(first, asm.imm(STANDARD_ERROR, 32, signed=False))
    asm.loadreg(A7, asm.imm(NR_WRITE, 12))
    asm.op(rvops.ENVIRONMENT_CALL)
    asm.ret()
    asm.end_function()


#: The numbers of the two system calls the allocator makes, and what a call
#: looks like here: the number in the register the kernel reads it from, the
#: arguments in the first six of the convention's, and the answer back in the
#: first of them.
NR_MMAP: Final[int] = 222
NR_MUNMAP: Final[int] = 215

SYSCALLS: Final[SyscallABI] = SyscallABI(
    mmap=NR_MMAP, munmap=NR_MUNMAP, number=A7,
    arguments=tuple(reg("".join(("a", str(n)))) for n in range(6)),
    answer=A0, enter=lambda asm: asm.op(rvops.ENVIRONMENT_CALL))

#: Where the allocator's own arguments arrive and its answer goes, and two
#: registers a caller does not expect back.
ALLOCATOR_REGS: Final[AllocatorRegs] = AllocatorRegs(
    arena=A0, size=A1, answer=A0,
    scratch=(reg("t0"), reg("t1"), reg("t2")))


def _run_tests(asm: Assembler, module: Module, cconv: CallConvDesc,
               failures: Mapping[int, Failure]) -> None:
    """Call each test this binary runs, and stop if any of them failed.

    A test answers a truth value, which comes back widened to the register the
    compiler's own calls read it out of.

    **Every one of them is run.**  One that answers false names itself and the
    next is tried: a run that stopped at the first would make a reader fix one
    thing and run again to be told the next, and saying what is wrong is what a
    test binary is for.
    """
    ran = [one for one in run_by(module) if id(one) in failures]
    if not ran:
        return
    asm.loadreg(COUNT_REG, asm.reg(ZERO))
    for one in ran:
        found = failures[id(one)]
        asm.call(symbol_name(one))
        passed = asm.reserve_label("test.passed")
        asm.branch(Condition.NE, asm.reg(cconv.int_ret_regs[0]),
                   asm.imm(0, 12), passed)
        asm.address(cconv.int_arg_regs[0], found.symbol)
        asm.loadreg(cconv.int_arg_regs[1], asm.imm(found.length, 12))
        asm.call(REPORT_SYMBOL)
        asm.op(ops.PLUS, COUNT_REG, asm.reg(COUNT_REG), asm.imm(1, 12))
        asm.block(passed)
    fit = asm.reserve_label("tests.passed")
    asm.branch(Condition.EQ, asm.reg(COUNT_REG), asm.reg(ZERO), fit)
    asm.loadreg(cconv.int_arg_regs[0], asm.imm(statuses.TESTS_FAILED, 12))
    asm.loadreg(A7, asm.imm(NR_EXIT_GROUP, 12))
    asm.op(rvops.ENVIRONMENT_CALL)
    asm.op(ops.TRAP)
    asm.block(fit)
