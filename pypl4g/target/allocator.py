"""The allocator, and the arenas it hands storage out of.

Nothing in a program can put a value in memory that was not there when the
program started until something asks the system for more, and this is what
asks.  It is written once and emitted for every architecture: what differs
between them is the number of a system call, which registers its arguments go
in and which instruction enters the kernel, and that much is a small record
each backend fills in.

**The shape is GNU's obstacks: a bump pointer over a list of chunks.**  An
allocation is an addition and a comparison.  When the current chunk is too
small the allocator asks the system for another and links it on; a chunk is
never given back on its own, and a whole arena is given back at once.  That is
the cheapest allocator there is and it is the right first one: a compiler
builds a great deal that lives exactly as long as the compilation, and nothing
in the language yet says that one value outlives another.  What it is not is a
general-purpose allocator -- an arena that frees nothing will not do for a
program that runs for a long time -- and the entry in the to-do list says so.

**There are as many arenas as a program makes.**  An arena is three words and
nothing else, so one is a value like any other: the compiler provides a default
one and a program may make more.  An allocation names the arena it comes out
of, and giving an arena back gives back everything that came out of it, which
is the whole of what makes this safe to use for storage with a known lifetime.

**An allocation that cannot be met stops the program.**  It goes through the
same `__pl4g_abort` a fault goes through, for the same reason: answering with a
result would put a `?` on every value that is built rather than computed, and
there is nothing a program could usefully do at that point that the operating
system will not do better by refusing to start it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, Sequence

from ..ir.mangle import symbol_name
from ..ir.module import Module
from . import statuses
from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg

#: How wide a word is here.  All three targets have eight-byte pointers, and
#: the arena is written in pointers.
WORD: Final[int] = 8

#: Where the three words of an arena are.  ``next`` is the first byte not yet
#: handed out and ``limit`` one past the last byte of the current chunk, so an
#: allocation is the addition of those two and a comparison between them;
#: ``chunk`` is the head of the list, which is only read when the arena is
#: given back.
NEXT: Final[int] = 0
LIMIT: Final[int] = WORD
CHUNK: Final[int] = 2 * WORD

#: How much room an arena takes, which is what a program that makes one has to
#: set aside.
ARENA_SIZE: Final[int] = 3 * WORD

#: And the two words at the front of a chunk: the chunk before it in the list,
#: and how many bytes were asked for, which is what giving it back needs.  The
#: bytes handed out start after them, on the grain.
CHUNK_NEXT: Final[int] = 0
CHUNK_BYTES: Final[int] = WORD
CHUNK_BODY: Final[int] = 2 * WORD

#: Every allocation starts on this many bytes.  Sixteen is the strictest
#: alignment any value of the language wants, and asking the question once
#: here is cheaper than carrying an alignment through every allocation.
GRAIN: Final[int] = 16

#: The smallest a chunk is asked for.  A program that allocates at all almost
#: always allocates more than once, so the first ask is for a great deal more
#: than the first allocation needs.
CHUNK_MINIMUM: Final[int] = 64 * 1024

#: PROT_READ | PROT_WRITE, and MAP_PRIVATE | MAP_ANONYMOUS.  They are the same
#: numbers on every architecture Linux runs on that this compiler targets.
PROT_READ_WRITE: Final[int] = 3
MAP_PRIVATE_ANONYMOUS: Final[int] = 0x22

#: A system call answers with a small negative number where it failed, and
#: those are the only values it can answer with that are not addresses.
ERROR_WINDOW: Final[int] = 4096

#: The names the three the runtime provides are known by.
ALLOC_SYMBOL: Final[str] = "__pl4g_alloc"
GROW_SYMBOL: Final[str] = "__pl4g_grow"
RELEASE_SYMBOL: Final[str] = "__pl4g_release"
FREE_SYMBOL: Final[str] = "__pl4g_free"

#: What is said where the system will give no more memory.  It is built here
#: rather than where a fault's message is built because it belongs to no
#: function of the program: there is no line to name.
OUT_OF_MEMORY: Final[str] = "pl4g: out of memory\n"


#: What a program has to name to have the allocator emitted.  A module that
#: calls one of them declares it, and the backend supplies the body; nothing
#: else says the allocator is wanted, so a program that never allocates carries
#: none of it.
RUNTIME_SYMBOLS: Final[frozenset[str]] = frozenset((ALLOC_SYMBOL, RELEASE_SYMBOL,
                                                    FREE_SYMBOL))


def wanted_by(module: Module) -> bool:
    """Whether anything in *module* calls into the allocator."""
    return any(func.is_declaration and symbol_name(func) in RUNTIME_SYMBOLS
               for func in module.functions.values())


@dataclass(frozen=True, slots=True)
class SyscallABI:
    """How one architecture asks the kernel for something.

    The registers are named rather than derived from the calling convention
    because a system call's registers are the kernel's choice and not the
    language's, and on one of the three they differ from the convention's.
    """

    #: The numbers of the two calls the allocator makes.
    mmap: int
    munmap: int
    #: Where the number of the call goes, and where the arguments go.
    number: Reg
    arguments: Sequence[Reg]
    #: Where the answer comes back.
    answer: Reg
    #: The instruction that enters the kernel.
    enter: Callable[[Assembler], None]
    #: What the kernel does not give back, beyond the register it answers in.
    #: Two of the three give everything else back; the one that does not says so
    #: here rather than having the fact written into its selector.
    clobbers: Sequence[Reg] = ()


@dataclass(frozen=True, slots=True)
class AllocatorRegs:
    """The registers the allocator's own code works in.

    The first two are where its arguments arrive and the third is where its
    answer goes, which the calling convention settles.  The rest are registers
    a caller does not expect back, used for whatever a few instructions need.
    """

    arena: Reg
    size: Reg
    answer: Reg
    scratch: Sequence[Reg]


def emit_allocator(asm: Assembler, abi: SyscallABI, regs: AllocatorRegs,
                   abort_symbol: str, message_symbol: str) -> None:
    """Emit the four the runtime provides, in the order they call each other."""
    _emit_alloc(asm, regs)
    _emit_grow(asm, abi, regs, abort_symbol, message_symbol)
    _emit_release(asm, abi, regs)
    _emit_free(asm)


def _emit_free(asm: Assembler) -> None:
    """Emit the giving back of one object: nothing, for an arena.

    `__pl4g_free(arena, where, size)` is what a container calls for an element it
    replaces and for a temporary it copied.  An arena gives back only all at once,
    so it returns at once; an allocator that can give back one object at a time
    supplies a body here and nothing that calls it changes.
    """
    asm.begin_function(FREE_SYMBOL, exported=False)
    asm.ret()
    asm.end_function()


def _round_up(asm: Assembler, value: Reg, grain: int, scratch: Reg) -> None:
    """Bring *value* up to the next multiple of *grain*.

    The mask is built rather than written as an immediate: what it is depends on
    the width of the register, and only one of the three architectures would
    take a sixty-four-bit one in an instruction anyway.
    """
    asm.loadreg(scratch, asm.imm(grain - 1, 32, signed=False))
    asm.op(ops.PLUS, value, MCReg(value), MCReg(scratch))
    asm.op(ops.NOT, scratch, MCReg(scratch))
    asm.op(ops.AND, value, MCReg(value), MCReg(scratch))


def _emit_alloc(asm: Assembler, regs: AllocatorRegs) -> None:
    """Emit the allocation itself, which is an addition and a comparison.

    It makes no call and needs no stack of its own, which is the whole point of
    a bump allocator: the case where there is room is a handful of
    instructions, and the case where there is not goes somewhere else and does
    not come back here.

    Everything is worked out in registers a caller does not expect back before
    the answer is put where an answer goes, because on two of the three targets
    the register an answer goes in is the register the first argument arrived
    in, and the first argument is the arena every one of these reads.
    """
    place, limit, room = regs.scratch[0], regs.scratch[1], regs.scratch[2]
    asm.begin_function(ALLOC_SYMBOL, exported=False)
    _round_up(asm, regs.size, GRAIN, room)
    asm.loadreg(place, asm.mem(base=regs.arena, disp=NEXT, size_bits=64))
    asm.loadreg(limit, asm.mem(base=regs.arena, disp=LIMIT, size_bits=64))
    asm.op(ops.PLUS, room, MCReg(place), MCReg(regs.size))
    # Past the end of this chunk?  Then this arena wants another one, and
    # getting it is a great deal more than this; it answers in this one's place,
    # with the arena and the rounded-up size exactly as they are here.
    short = asm.reserve_label("no.room")
    asm.branch(Condition.UGT, MCReg(room), MCReg(limit), short)
    asm.store(asm.mem(base=regs.arena, disp=NEXT, size_bits=64), MCReg(room))
    asm.loadreg(regs.answer, MCReg(place))
    asm.ret()
    asm.block(short)
    asm.tail_jump(GROW_SYMBOL)
    asm.end_function()


#: Where the three things the slow path has to keep across the system call go.
#: A register would be cheaper, but every register a caller does not expect back
#: is one the call itself uses or destroys.
_KEPT_ARENA: Final[int] = 0
_KEPT_SIZE: Final[int] = WORD
_KEPT_BYTES: Final[int] = 2 * WORD
_KEPT_ROOM: Final[int] = 4 * WORD


def _emit_grow(asm: Assembler, abi: SyscallABI, regs: AllocatorRegs,
               abort_symbol: str, message_symbol: str) -> None:
    """Emit the path that asks the system for another chunk and allocates from it.

    Three things have to survive the system call -- which arena, how much was
    asked for, and how much was mapped -- and none of them can stay in a
    register: the call's own arguments take every register a caller does not
    expect back, and the instruction that enters the kernel destroys two more
    on one of the three targets.  So they go on the stack, which is what the
    room made here is for.
    """
    asked, other, third = regs.scratch[0], regs.scratch[1], regs.scratch[2]
    asm.begin_function(GROW_SYMBOL, exported=False)
    asm.frame(_KEPT_ROOM)
    asm.put_aside(_KEPT_ARENA, regs.arena)
    asm.put_aside(_KEPT_SIZE, regs.size)
    # How much to ask for: the allocation and a chunk's header, brought up to
    # the smallest chunk worth asking for.  The system rounds it up again to a
    # page, and the room that gives is simply not used -- asking the system how
    # large a page is would be another call, for bytes a later allocation takes
    # anyway.
    asm.loadreg(asked, MCReg(regs.size))
    asm.op(ops.PLUS, asked, MCReg(asked), MCImm(CHUNK_BODY, 32, signed=False))
    asm.loadreg(other, asm.imm(CHUNK_MINIMUM, 32, signed=False))
    enough = asm.reserve_label("enough")
    asm.branch(Condition.UGE, MCReg(asked), MCReg(other), enough)
    asm.loadreg(asked, MCReg(other))
    asm.block(enough)
    asm.put_aside(_KEPT_BYTES, asked)
    # mmap(0, bytes, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0).
    # The descriptor is all ones made from zero rather than written as a
    # number, because a register holding a negative number the whole width of
    # the register takes two instructions on two of the three and this takes
    # one on all of them.  Linux does not look at it for an anonymous mapping;
    # it is passed because that is what the call says it takes.
    first, second, third_arg, fourth, fifth, sixth = abi.arguments[:6]
    asm.loadreg(second, MCReg(asked))
    asm.loadreg(first, asm.imm(0, 32, signed=False))
    asm.loadreg(third_arg, asm.imm(PROT_READ_WRITE, 32, signed=False))
    asm.loadreg(fourth, asm.imm(MAP_PRIVATE_ANONYMOUS, 32, signed=False))
    asm.loadreg(fifth, asm.imm(0, 32, signed=False))
    asm.op(ops.NOT, fifth, MCReg(fifth))
    asm.loadreg(sixth, asm.imm(0, 32, signed=False))
    asm.loadreg(abi.number, asm.imm(abi.mmap, 32, signed=False))
    abi.enter(asm)
    # A system call that failed answers with a small negative number, and every
    # other answer it can give is an address.  Read unsigned, those are the
    # largest values there are, which is one comparison rather than two.
    base = regs.scratch[0]
    asm.loadreg(base, MCReg(abi.answer))
    asm.loadreg(other, asm.imm(ERROR_WINDOW, 32, signed=False))
    asm.op(ops.NOT, other, MCReg(other))
    got_it = asm.reserve_label("mapped")
    asm.branch(Condition.ULE, MCReg(base), MCReg(other), got_it)
    # Nothing comes back from here, so the room is given up first: what the
    # message says is that the program is over.
    asm.unframe(_KEPT_ROOM)
    asm.address(abi.arguments[0], message_symbol)
    asm.loadreg(abi.arguments[1],
                asm.imm(len(OUT_OF_MEMORY.encode("utf-8")), 32, signed=False))
    asm.loadreg(abi.arguments[2],
                asm.imm(statuses.OUT_OF_MEMORY, 32, signed=False))
    asm.tail_jump(abort_symbol)
    asm.block(got_it)
    asm.take_back(regs.arena, _KEPT_ARENA)
    asm.take_back(regs.size, _KEPT_SIZE)
    asm.take_back(other, _KEPT_BYTES)
    # Link the chunk on, so that giving the arena back gives this back too.
    asm.loadreg(third, asm.mem(base=regs.arena, disp=CHUNK, size_bits=64))
    asm.store(asm.mem(base=base, disp=CHUNK_NEXT, size_bits=64), MCReg(third))
    asm.store(asm.mem(base=base, disp=CHUNK_BYTES, size_bits=64), MCReg(other))
    asm.store(asm.mem(base=regs.arena, disp=CHUNK, size_bits=64), MCReg(base))
    # What is left of the chunk after this allocation is what the next one
    # bumps through.
    asm.op(ops.PLUS, third, MCReg(base), MCReg(other))
    asm.store(asm.mem(base=regs.arena, disp=LIMIT, size_bits=64), MCReg(third))
    asm.op(ops.PLUS, base, MCReg(base), MCImm(CHUNK_BODY, 32, signed=False))
    asm.op(ops.PLUS, third, MCReg(base), MCReg(regs.size))
    asm.store(asm.mem(base=regs.arena, disp=NEXT, size_bits=64), MCReg(third))
    # Last, because where the answer goes is where the arena arrived on two of
    # the three targets.
    asm.loadreg(regs.answer, MCReg(base))
    asm.unframe(_KEPT_ROOM)
    asm.ret()
    asm.end_function()


def _emit_release(asm: Assembler, abi: SyscallABI, regs: AllocatorRegs) -> None:
    """Emit the giving back of a whole arena.

    Every chunk goes at once, which is the only granularity this allocator has.
    The arena is left as an arena with nothing in it rather than as rubble, so
    that allocating from it again simply asks for a first chunk.
    """
    chunk, other, third = regs.scratch[0], regs.scratch[1], regs.scratch[2]
    asm.begin_function(RELEASE_SYMBOL, exported=False)
    asm.frame(_KEPT_ROOM)
    # Empty it before anything is unmapped: what says how much to unmap is in
    # the chunk being unmapped, so the list is walked with the arena already
    # saying it holds nothing.
    asm.loadreg(chunk, asm.mem(base=regs.arena, disp=CHUNK, size_bits=64))
    asm.loadreg(other, asm.imm(0, 32, signed=False))
    asm.store(asm.mem(base=regs.arena, disp=NEXT, size_bits=64), MCReg(other))
    asm.store(asm.mem(base=regs.arena, disp=LIMIT, size_bits=64), MCReg(other))
    asm.store(asm.mem(base=regs.arena, disp=CHUNK, size_bits=64), MCReg(other))
    again = asm.reserve_label("next.chunk")
    done = asm.reserve_label("all.gone")
    asm.jump(again)
    asm.block(again)
    asm.loadreg(other, asm.imm(0, 32, signed=False))
    asm.branch(Condition.EQ, MCReg(chunk), MCReg(other), done)
    # What comes after it has to be read out before it goes, and it has to
    # survive the call that takes this one away.
    asm.loadreg(third, asm.mem(base=chunk, disp=CHUNK_NEXT, size_bits=64))
    asm.put_aside(_KEPT_SIZE, third)
    asm.loadreg(other, asm.mem(base=chunk, disp=CHUNK_BYTES, size_bits=64))
    asm.loadreg(abi.arguments[0], MCReg(chunk))
    asm.loadreg(abi.arguments[1], MCReg(other))
    asm.loadreg(abi.number, asm.imm(abi.munmap, 32, signed=False))
    abi.enter(asm)
    # Whether it worked is not asked.  There is nothing a program could do
    # about a mapping the system will not take back, and the arena already says
    # it holds nothing.
    asm.take_back(chunk, _KEPT_SIZE)
    asm.jump(again)
    asm.block(done)
    asm.unframe(_KEPT_ROOM)
    asm.ret()
    asm.end_function()
