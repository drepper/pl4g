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
from ...ir.function import SYSTEM_CCONV
from ...ir.layout import DataLayout
from ...ir.module import Module
from ...mc import ops
from ...mc.asmbuilder import Assembler
from .. import backtrace, statuses
from ..callconv import CallConvDesc
from .abi import lookup as lookup_cconv
from .. import started
from ..tests import Failure, run_by
from . import ops as x86ops
from ..allocator import AllocatorRegs, SyscallABI
from ..stack import StackABI, emit_make
from ...mc.ops import Condition
from . import levels
from .regs import (EAX, EBP, EBX, ECX, EDI, EDX, ESI, INFO, RAX, RCX,
                   RDI, RDX, RSI, RSP, reg)

#: The number of the Linux system call that ends the whole process.
NR_EXIT_GROUP: Final[int] = 231

#: The name the entry point is given in the image.
ENTRY_SYMBOL: Final[str] = "_start"


def emit_start(asm: Assembler, module: Module, cconv: CallConvDesc,
               level: str = levels.DEFAULT,
               refused: str | None = None,
               failures: Mapping[int, Failure] | None = None,
               layout: DataLayout | None = None) -> None:
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
    layout = layout if layout is not None else DataLayout(pointer_size=8)
    status32 = INFO.view(cconv.int_ret_regs[0].unit, 32)

    asm.begin_function(ENTRY_SYMBOL, exported=True)
    # What the kernel set the process up with is at the stack pointer, and this
    # is the only moment it is: everything below pushes something.  It is kept
    # in a register a call leaves alone until the runtime is asked to read it.
    if started.arguments_at(module, layout) is not None \
            or module.environ is not None:
        asm.loadreg(HELD_STACK, asm.reg(RSP))
    # The outermost stack frame is marked by a null frame pointer, so that a
    # debugger unwinding the stack knows where to stop.
    asm.op(ops.XOR, EBP, asm.reg(EBP), asm.reg(EBP))
    if refused is not None:
        _check_level(asm, level, refused)
    _read_environment(asm, module, cconv)
    for ctor in module.ctors:
        asm.call(symbol_name(ctor))
    _run_tests(asm, module, cconv, failures or {})
    # Where the status waits.  A register a call leaves alone where a destructor
    # is going to run, and the one the system call wants where none is: a
    # destructor is an ordinary call and may use that one for its own argument,
    # and a program with no destructor should not pay a move for the question.
    held = HELD_STATUS if module.dtors else EDI
    if module.test_plan:
        # A binary built to run tests and nothing else: every one of them
        # passed, or it left through the helper above and never arrived here.
        asm.loadreg(held, asm.imm(0, 32, signed=False))
    else:
        _make_stack(asm, module)
        _read_arguments(asm, module, layout)
        if started.wanted_by(module) is not None:
            # Where the record the program was started with is, which is the
            # whole of what is handed over.
            asm.address(cconv.int_arg_regs[0], started.SYMBOL)
        asm.call(symbol_name(startup))
        asm.loadreg(held, asm.reg(status32))
    for dtor in module.dtors:
        asm.call(symbol_name(dtor))
    if module.dtors:
        asm.loadreg(EDI, asm.reg(HELD_STATUS))
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

#: And the one a failing test says so through, which writes the same way and
#: comes back rather than leaving.
REPORT_SYMBOL: Final[str] = "__pl4g_report"


#: The number of the Linux system call that writes, and the descriptor the
#: message goes to.
NR_WRITE_HERE: Final[int] = 1
STANDARD_ERROR_HERE: Final[int] = 2


#: Where the count of tests that did not pass is kept while they run.  A
#: register a call leaves alone, so that nothing has to be saved around one and
#: no storage has to be found for a number that lives for a few instructions.
COUNT_REG: Final = reg("ebx")

#: And where what the kernel set the process up with waits until the runtime is
#: asked to read it.  A register a call leaves alone, since the constructors and
#: the level check run in between.
HELD_STACK: Final = reg("r13")

#: And where the exit status waits while the destructors run.  A register a call
#: leaves alone, which the one a system call takes its first argument in is not:
#: that one is where an ordinary call puts its own first argument, so a
#: destructor that calls anything destroys it.  Only used where a destructor is
#: going to run; where none is, the status goes straight where the system call
#: wants it and nothing is moved twice.
HELD_STATUS: Final = reg("r12d")


#: The numbers of the calls the stack is made with, beyond the two the
#: allocator makes, and the registers that work is done in.  The kernel leaves
#: every register alone but the one it answers in and the two the instruction
#: itself destroys, so what is kept across a call is any register the entry
#: point is not otherwise using.
NR_MPROTECT: Final[int] = 10
NR_RT_SIGRETURN: Final[int] = 15
NR_RT_SIGACTION: Final[int] = 13
NR_SIGALTSTACK: Final[int] = 131

STACK_ABI: Final[StackABI] = StackABI(
    mprotect=NR_MPROTECT, sigaltstack=NR_SIGALTSTACK,
    rt_sigaction=NR_RT_SIGACTION, write=NR_WRITE, exit_group=NR_EXIT_GROUP,
    # Every processor this runs on has four-kilobyte pages, whatever else it
    # may also have.
    page=4096,
    kept=(reg("rbx"), reg("r14"), reg("r15")),
    scratch=(RDX, RCX, reg("r11")),
    read_sp=lambda asm, dst: asm.loadreg(dst, asm.reg(RSP)),
    write_sp=lambda asm, src: asm.loadreg(RSP, asm.reg(src)),
    # This kernel does not return from a handler by itself.
    rt_sigreturn=NR_RT_SIGRETURN)


def _make_stack(asm: Assembler, module: Module) -> None:
    """Put the program on a stack of its own, where it asked for one.

    Asked for after the constructors would have run and before anything else,
    so that what runs on the kernel's stack is only what has to.  Where the
    system will not have it nothing is switched and the program carries on with
    the stack it was given, which is what every program had before this.
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

    **The system's convention and not the language's.**  What is being called is
    the runtime, which is C: the two put their arguments in different registers
    here, and the callee's is the one that decides.
    """
    at = started.arguments_at(module, layout)
    if at is None:
        return
    theirs = lookup_cconv(SYSTEM_CCONV)
    asm.loadreg(theirs.int_arg_regs[0], asm.reg(HELD_STACK))
    asm.address(theirs.int_arg_regs[1], started.SYMBOL)
    if at:
        asm.op(ops.PLUS, theirs.int_arg_regs[1],
               asm.reg(theirs.int_arg_regs[1]), asm.imm(at, 32, signed=True))
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

    A test answers a truth value, which comes back widened to the whole of the
    register the compiler's own calls read it out of -- so it is compared the
    way they compare it.

    **Every one of them is run.**  One that answers false names itself and the
    next is tried: a run that stopped at the first would make a reader fix one
    thing and run again to be told the next, and saying what is wrong is what a
    test binary is for.  What it exits with says that something was, and the
    messages say what.
    """
    ran = [one for one in run_by(module) if id(one) in failures]
    if not ran:
        return
    asm.op(ops.XOR, COUNT_REG, asm.reg(COUNT_REG), asm.reg(COUNT_REG))
    for one in ran:
        found = failures[id(one)]
        asm.call(symbol_name(one))
        passed = asm.reserve_label("test.passed")
        answer = INFO.view(cconv.int_ret_regs[0].unit, 32)
        asm.branch(Condition.NE, asm.reg(answer),
                   asm.imm(0, 32, signed=False), passed)
        asm.address(RDI, found.symbol)
        asm.loadreg(ESI, asm.imm(found.length, 32, signed=False))
        asm.call(REPORT_SYMBOL)
        asm.op(ops.PLUS, COUNT_REG, asm.reg(COUNT_REG),
               asm.imm(1, 32, signed=False))
        asm.block(passed)
    fit = asm.reserve_label("tests.passed")
    asm.branch(Condition.EQ, asm.reg(COUNT_REG),
               asm.imm(0, 32, signed=False), fit)
    asm.loadreg(EDI, asm.imm(statuses.TESTS_FAILED, 32, signed=False))
    asm.loadreg(EAX, asm.imm(NR_EXIT_GROUP, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    asm.op(ops.TRAP)
    asm.block(fit)


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
    # The three arguments of the system call are the two this was given, moved
    # up one place, with the descriptor put in front of them.
    asm.loadreg(third, asm.reg(second))
    asm.loadreg(second, asm.reg(first))
    asm.loadreg(first, asm.imm(STANDARD_ERROR, 32, signed=False))
    asm.loadreg(EAX, asm.imm(NR_WRITE, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    if walk:
        # Where the faulting function was and where its stack stood.  The call
        # that reached here pushed the first, so the second is the eight bytes
        # further up that took.
        asm.loadreg(first, asm.mem(base=RSP, disp=0, size_bits=64))
        asm.loadreg(second, asm.reg(RSP))
        asm.op(ops.PLUS, second, asm.reg(second), asm.imm(8, 32, signed=False))
        asm.call(backtrace.SYMBOL)
    asm.loadreg(EDI, asm.imm(statuses.GENERAL, 32, signed=False))
    asm.loadreg(EAX, asm.imm(NR_EXIT_GROUP, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    # exit_group does not return; trapping makes that explicit rather than
    # letting control run off the end of the section.
    asm.op(ops.TRAP)
    asm.end_function()


#: How the walk writes here, and which registers it may keep across doing so.
#: The kept four are callee-saved, which is what the system call leaves alone;
#: nothing will miss them, the helper that calls the walk being about to end the
#: program.
WALK_ABI: Final[backtrace.WalkABI] = backtrace.WalkABI(
    write=NR_WRITE, number=RAX,
    arguments=(RDI, RSI, RDX),
    enter=lambda asm: asm.op(x86ops.SYSCALL),
    kept=(reg("rbx"), reg("r12"), reg("r13"), reg("r14")),
    scratch=(reg("rcx"), reg("r11")))


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
    asm.loadreg(EAX, asm.imm(NR_WRITE, 32, signed=False))
    asm.op(x86ops.SYSCALL)
    asm.ret()
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
