"""The program's entry point on AArch64.

Built through the ordinary builder API, so it is itself a test of the assembler
and appears in the debugging dump like any other function.

The binary depends on nothing from the system: this is the first instruction the
kernel runs, and the process leaves through a supervisor call rather than by
returning to anything.
"""

from __future__ import annotations

from typing import Final, Mapping

from ...ir.mangle import symbol_name
from ...ir.function import SYSTEM_CCONV
from ...ir.layout import DataLayout
from ...ir.module import Module
from ...mc import ops
from ...mc.ops import Condition
from ...mc.asmbuilder import Assembler
from .. import backtrace, statuses
from ..callconv import CallConvDesc
from .abi import lookup as lookup_cconv
from .. import started
from ..tests import Failure, run_by
from . import ops as a64ops
from ..allocator import AllocatorRegs, SyscallABI
from ..stack import StackABI, emit_make
from .regs import SP, X0, X1, X8, X19, X29, X30, XZR, reg

#: The number of the Linux system call that ends the whole process.  The number
#: differs from the one x86-64 uses, which is why it belongs to the backend.
NR_EXIT_GROUP: Final[int] = 94

#: The register a system call number is passed in.
SYSCALL_NUMBER_REG: Final = X8

#: The name the entry point is given in the image.
ENTRY_SYMBOL: Final[str] = "_start"


def emit_start(asm: Assembler, module: Module, cconv: CallConvDesc,
               failures: Mapping[int, Failure] | None = None,
               layout: DataLayout | None = None) -> None:
    """Emit the entry point for *module*.

    The constructor and destructor loops emit nothing while a program has none,
    but they are real code: adding a constructor is a change in the semantic
    analysis alone, never in the backend.
    """
    startup = module.startup
    assert startup is not None
    layout = layout if layout is not None else DataLayout(pointer_size=8)
    status = cconv.int_ret_regs[0]

    asm.begin_function(ENTRY_SYMBOL, exported=True)
    # What the kernel set the process up with is at the stack pointer, and this
    # is the only moment it is: everything below puts something there.  It waits
    # in a register a call leaves alone until the runtime is asked to read it.
    if started.arguments_at(module, layout) is not None \
            or module.environ is not None:
        # An addition of nothing and not a move: the stack pointer and the zero
        # register share an encoding here, and the move between registers is the
        # form that reads that encoding as the zero.  The addition of an
        # immediate is the form that reads it as the stack pointer.
        asm.op(ops.PLUS, HELD_STACK, asm.reg(SP), asm.imm(0, 12, signed=False))
    # The outermost stack frame is marked by a null frame pointer and a null
    # return address, so that a debugger unwinding the stack knows where to stop.
    asm.loadreg(X29, asm.reg(XZR))
    asm.loadreg(X30, asm.reg(XZR))
    _read_environment(asm, module, cconv)
    for ctor in module.ctors:
        asm.call(symbol_name(ctor))
    _run_tests(asm, module, cconv, failures or {})
    if module.test_plan:
        # A binary built to run tests and nothing else: every one of them
        # passed, or it left through the helper above and never arrived here.
        asm.loadreg(status, asm.imm(0, 16, signed=False))
    else:
        _make_stack(asm, module)
        _read_arguments(asm, module, layout)
        if started.wanted_by(module) is not None:
            # Where the record the program was started with is, which is the
            # whole of what is handed over.
            asm.address(cconv.int_arg_regs[0], started.SYMBOL)
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

#: And the one a failing test says so through, which writes the same way and
#: comes back rather than leaving.
REPORT_SYMBOL: Final[str] = "__pl4g_report"

#: Where the count of tests that did not pass is kept while they run.  A
#: register a call leaves alone, so that nothing has to be saved around one and
#: no storage has to be found for a number that lives for a few instructions.
COUNT_REG: Final = reg("x20")

#: And where what the kernel set the process up with waits until the runtime is
#: asked to read it.  A register a call leaves alone, since the constructors run
#: in between.
HELD_STACK: Final = reg("x21")


def emit_abort(asm: Assembler, cconv: CallConvDesc,
               walk: bool = False) -> None:
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
    # Which kind of stop this is, put somewhere the write below will not touch
    # and the walk of the stack does not keep anything in.  It arrives in the
    # register the write's own third argument goes in, so it has to move before
    # anything else does.
    held = reg("x23")
    asm.loadreg(held, asm.reg(third))
    # The three arguments of the system call are the two this was given, moved
    # up one place, with the descriptor put in front of them.
    asm.loadreg(third, asm.reg(second))
    asm.loadreg(second, asm.reg(first))
    asm.loadreg(first, asm.imm(STANDARD_ERROR, 32, signed=False))
    asm.loadreg(SYSCALL_NUMBER_REG,
                asm.imm(NR_WRITE, 16, signed=False))
    asm.op(a64ops.SUPERVISOR_CALL)
    if walk:
        # The call that reached here left the return address in a register and
        # touched nothing else, so where the faulting function was and where
        # its stack stands are both to hand -- and the first has to be taken
        # out of that register before the call below writes it.
        asm.loadreg(first, asm.reg(X30))
        asm.op(ops.PLUS, second, asm.reg(SP), asm.imm(0, 12, signed=False))
        asm.call(backtrace.SYMBOL)
    asm.loadreg(cconv.int_arg_regs[0], asm.reg(held))
    asm.loadreg(SYSCALL_NUMBER_REG,
                asm.imm(NR_EXIT_GROUP, 16, signed=False))
    asm.op(a64ops.SUPERVISOR_CALL)
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
    asm.loadreg(first, asm.imm(STANDARD_ERROR, 16, signed=False))
    asm.loadreg(SYSCALL_NUMBER_REG, asm.imm(NR_WRITE, 16, signed=False))
    asm.op(a64ops.SUPERVISOR_CALL)
    asm.ret()
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


def _a_number(asm: Assembler, where: object, value: int) -> None:
    """Put a number in a register, however many instructions that takes here.

    A megabyte does not fit the immediate either of these carries, so the move
    is what works out how to build it; asking for it this way is what keeps the
    entry point from knowing.
    """
    asm.loadreg(where, asm.imm(value, 32, signed=False))  # type: ignore[arg-type]


#: The numbers of the calls the stack is made with, beyond the two the
#: allocator makes, and the registers that work is done in.  The kernel leaves
#: every register alone but the one it answers in, so what is kept across a call
#: is any register the entry point is not otherwise using.
NR_MPROTECT: Final[int] = 226
NR_RT_SIGACTION: Final[int] = 134
NR_SIGALTSTACK: Final[int] = 132


def _read_sp(asm: Assembler, dst: object) -> None:
    """Read the stack pointer, which an addition of nothing is the way to do.

    A move between registers reads that encoding as the zero register; this is
    the form that reads it as the stack pointer.
    """
    asm.op(ops.PLUS, dst, asm.reg(SP),  # type: ignore[arg-type]
           asm.imm(0, 12, signed=False))


def _write_sp(asm: Assembler, src: object) -> None:
    """And write it, for the same reason."""
    asm.op(ops.PLUS, SP, asm.reg(src),  # type: ignore[arg-type]
           asm.imm(0, 12, signed=False))


STACK_ABI: Final[StackABI] = StackABI(
    mprotect=NR_MPROTECT, sigaltstack=NR_SIGALTSTACK,
    rt_sigaction=NR_RT_SIGACTION, write=NR_WRITE, exit_group=NR_EXIT_GROUP,
    # The one of the three architectures whose page size is configured rather
    # than fixed: sixty-four kilobytes is the largest a kernel may choose, and
    # what is protected has to be a whole number of whatever it chose.
    page=64 * 1024,
    kept=(reg("x22"), reg("x23"), reg("x24")),
    scratch=(reg("x9"), reg("x10"), reg("x11")),
    read_sp=_read_sp, write_sp=_write_sp)


def _make_stack(asm: Assembler, module: Module) -> None:
    """Put the program on a stack of its own, where it asked for one.

    Where the system will not have it nothing is switched and the program
    carries on with the stack it was given.
    """
    if module.stack_size <= 0:
        return
    emit_make(asm, SYSCALLS, STACK_ABI, module.stack_size, module.guard_size)


def _read_arguments(asm: Assembler, module: Module,
                    layout: DataLayout) -> None:
    """Have the runtime read the arguments into the record, where one is taken.

    Two arguments: what the kernel set the process up with, and where in the
    record the run of them goes.  Where that is comes from the program's own
    declaration of the type, so the runtime is told and does not have to know.

    The system's convention and not the language's, since what is being called
    is the runtime and the callee's convention is the one that decides.
    """
    at = started.arguments_at(module, layout)
    if at is None:
        return
    theirs = lookup_cconv(SYSTEM_CCONV)
    asm.loadreg(theirs.int_arg_regs[0], asm.reg(HELD_STACK))
    asm.address(theirs.int_arg_regs[1], started.SYMBOL)
    if at:
        asm.op(ops.PLUS, theirs.int_arg_regs[1],
               asm.reg(theirs.int_arg_regs[1]), asm.imm(at, 12, signed=False))
    asm.call(started.READS_ARGUMENTS)


def _read_environment(asm: Assembler, module: Module,
                      cconv: CallConvDesc) -> None:
    """Build the environment, where the program named it.

    Two halves, because neither side can do both: the runtime knows where the
    strings are and nothing of how a table is laid out, and the compiler knows
    how to build one and has no way to reach the stack the process started on.
    So the runtime is asked for the strings, with the address the kernel set the
    process up at, and they are handed to the function `sema/environ.py`
    generated; what comes back is the one word that goes in the variable
    `⎕environ` stands for.

    Before the constructors, so that one may read the environment: what it
    needs is the stack address, which is in hand from the first instruction.

    Two conventions, because there are two callees: the runtime is C and the
    builder is the program's own.
    """
    if module.environ is None:
        return
    builder = module.functions.get(started.ENVIRON_MAKE)
    assert builder is not None, "the variable is there and nothing fills it"
    theirs = lookup_cconv(SYSTEM_CCONV)
    asm.loadreg(theirs.int_arg_regs[0], asm.reg(HELD_STACK))
    asm.call(started.READS_ENVIRONMENT)
    # What the runtime answers with is a run of strings, which is two words, and
    # they go straight on as the one argument the function of `std` takes.  The
    # second is moved first where the first of them would land in the register
    # the second came back in; nothing here can need a third register.
    handed = (cconv.int_arg_regs[0], cconv.int_arg_regs[1])
    came = (theirs.int_ret_regs[0], theirs.int_ret_regs[1])
    assert not (handed[0] == came[1] and handed[1] == came[0]), \
        "the two would have to be exchanged"
    order = (1, 0) if handed[0] == came[1] else (0, 1)
    for which in order:
        if handed[which] != came[which]:
            asm.loadreg(handed[which], asm.reg(came[which]))
    asm.call(symbol_name(builder))
    # The second argument register rather than the first: on one of these
    # targets the first is also the one an answer comes back in.
    where = cconv.int_arg_regs[1]
    asm.address(where, started.ENVIRON_SYMBOL)
    asm.store(asm.mem(base=where, disp=0, size_bits=64),
              asm.reg(cconv.int_ret_regs[0]))


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
    asm.loadreg(COUNT_REG, asm.reg(XZR))
    for one in ran:
        found = failures[id(one)]
        asm.call(symbol_name(one))
        passed = asm.reserve_label("test.passed")
        asm.branch(Condition.NE, asm.reg(cconv.int_ret_regs[0]),
                   asm.imm(0, 32, signed=False), passed)
        asm.address(cconv.int_arg_regs[0], found.symbol)
        asm.loadreg(cconv.int_arg_regs[1],
                    asm.imm(found.length, 16, signed=False))
        asm.call(REPORT_SYMBOL)
        asm.op(ops.PLUS, COUNT_REG, asm.reg(COUNT_REG),
               asm.imm(1, 12, signed=False))
        asm.block(passed)
    fit = asm.reserve_label("tests.passed")
    asm.branch(Condition.EQ, asm.reg(COUNT_REG), asm.reg(XZR), fit)
    asm.loadreg(cconv.int_ret_regs[0],
                asm.imm(statuses.TESTS_FAILED, 16, signed=False))
    asm.loadreg(SYSCALL_NUMBER_REG,
                asm.imm(NR_EXIT_GROUP, 16, signed=False))
    asm.op(a64ops.SUPERVISOR_CALL)
    asm.op(ops.TRAP)
    asm.block(fit)


#: How the walk writes here, and which registers it may keep across doing so.
#: The kept four are callee-saved, which is what the system call leaves alone;
#: nothing will miss them, the helper that calls the walk being about to end the
#: program.
WALK_ABI: Final[backtrace.WalkABI] = backtrace.WalkABI(
    write=NR_WRITE, number=SYSCALL_NUMBER_REG,
    arguments=(X0, X1, reg("x2")),
    enter=lambda asm: asm.op(a64ops.SUPERVISOR_CALL),
    kept=(X19, reg("x20"), reg("x21"), reg("x22")),
    scratch=(reg("x9"), reg("x10")))
