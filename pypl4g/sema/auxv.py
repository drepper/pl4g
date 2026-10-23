"""The auxiliary vector, and the table the entry point reads it into.

`⎕auxv(TYPE)` answers what the kernel said about `TYPE` in the auxiliary vector
it put after the environment -- where the vDSO is, how large a page is, what
the processor can do -- or nothing where it said nothing.  What it reads is a
variable of the image holding one table, and what fills that table is written
here: a function generated the way the environment's builder is, called by the
entry point with the stack the process was started on, before the constructors
and before anything of the program runs.

**Read once, into a table indexed by the type.**  The vector is a run of pairs
ending in a type of nought, `AT_NULL`, and every type the kernel defines is
small: the largest is `AT_MINSIGSTKSZ`, 51.  So the table is fifty-two words,
one for each type, and the word of `AT_NULL` -- which is never an entry, being
the end of them -- holds a bit for each type the kernel gave.  A lookup is that
bit and one load, with no walk and no branch; a type past the end of the table
is one the kernel does not yet define and is answered nothing, as one the kernel
left out is.

**A program that never asks carries none of this.**  The variable is made on
first ask, as the environment's is, so a program that does not name `⎕auxv` has
no table, no builder, and nothing done at its start.

**Written once, read ever after.**  The entry point is the only thing that writes
the table; a program reads it through `⎕auxv` and has no name for the variable,
so what it says is what the process was started with.
"""

from __future__ import annotations

from typing import Final

from ..ir.builder import IRBuilder
from ..ir.function import Function
from ..ir.inst import BinOp, CastKind, CmpPred
from ..ir.module import GlobalVar, Module
from ..ir.types import ArrayType, MEM, U64, VOID
from ..ir.value import Value
from ..target.started import AUXV_MAKE as MAKE_SYMBOL, AUXV_SYMBOL as SYMBOL
from . import tables

#: The largest type the kernel defines, `AT_MINSIGSTKSZ`, and so how many words
#: the table has: one for every type up to it, nought among them.
LARGEST: Final[int] = 51
SLOTS: Final[int] = LARGEST + 1

#: How far apart two words of the table are, and two of the vector's pairs.
WORD: Final[int] = 8
PAIR: Final[int] = 2 * WORD


def held_type(module: Module) -> ArrayType:
    """The type of the table: a word for every type, the presence bits first."""
    return module.types.array_type(U64, (SLOTS,))


def variable(module: Module) -> GlobalVar:
    """The variable the table waits in, made once for the program.

    Writable in the image because the entry point fills it, and among the
    `@[relro]` variables, which the entry point makes read-only once the
    constructors have run: it is filled before the first of them, nothing the
    program writes names it, and a write through `⎕at` of its address then
    stops the program instead of changing what every question of it answers.
    """
    found = module.globals.get(SYMBOL)
    if isinstance(found, GlobalVar):
        return found
    ty = held_type(module)
    return module.add_global(GlobalVar(
        name=SYMBOL, value_type=ty,
        ptr_type=module.types.ptr_type(ty, mutable=True),
        initializer=None, relro=True), key=SYMBOL)


def words_of(builder: IRBuilder, table: GlobalVar) -> Value:
    """Where the table's first word is, as a place of one word."""
    return builder.cast(CastKind.BITCAST, builder.address(table),
                        builder.module.types.ptr_type(U64, mutable=True))


def make_function(module: Module, table: GlobalVar) -> Function:
    """``__pl4g_auxv_make(at)``: the vector, read off the stack into the table.

    *at* is the stack pointer the kernel started the process with: the count of
    the arguments, the arguments and a null, the environment and a null, and
    then the pairs.  The walk steps over the first two by the count and by the
    null that ends the environment, then takes the pairs until `AT_NULL`, keeping
    each one whose type the table has a word for.  The presence bits are
    gathered as it goes and written last, once.
    """
    words = module.types.ptr_type(U64, mutable=True)
    func, fresh = tables.generated(module, MAKE_SYMBOL, (words,), VOID)
    if not fresh:
        return func
    entry = func.add_block()
    skip = func.add_block("skip")
    pairs = func.add_block("pairs")
    one = func.add_block("one")
    keep = func.add_block("keep")
    done = func.add_block("done")
    builder = IRBuilder(module, func)

    builder.position_at(entry)
    at = entry.add_param(words, "at")
    # Past the count, the arguments and the null after them: the count and two
    # more words.
    count = builder.load(at)
    builder.br(skip, (builder.binary(
        BinOp.ADD, at,
        builder.binary(BinOp.WRAP_MUL,
                       builder.binary(BinOp.WRAP_ADD, count,
                                      builder.int_const(U64, 2)),
                       builder.int_const(U64, WORD))), builder.memory()))

    # The environment, to the null that ends it.
    builder.position_at(skip)
    walked = skip.add_param(words, "walked")
    builder.set_memory(skip.add_param(MEM, "mem"))
    after = builder.binary(BinOp.ADD, walked, builder.int_const(U64, WORD))
    builder.condbr(
        builder.compare(CmpPred.EQ, builder.load(walked),
                        builder.int_const(U64, 0)),
        pairs, skip,
        true_args=(after, builder.int_const(U64, 0), builder.memory()),
        false_args=(after, builder.memory()))

    # A pair, or the `AT_NULL` that ends them.
    builder.position_at(pairs)
    pair = pairs.add_param(words, "pair")
    seen = pairs.add_param(U64, "seen")
    builder.set_memory(pairs.add_param(MEM, "mem"))
    kind = builder.load(pair)
    builder.condbr(
        builder.compare(CmpPred.EQ, kind, builder.int_const(U64, 0)),
        done, one, true_args=(builder.memory(),),
        false_args=(builder.memory(),))

    # One the table has a word for, or one it has not.
    builder.position_at(one)
    builder.set_memory(one.add_param(MEM, "mem"))
    following = builder.binary(BinOp.ADD, pair, builder.int_const(U64, PAIR))
    builder.condbr(
        builder.compare(CmpPred.ULT, kind, builder.int_const(U64, SLOTS)),
        keep, pairs, true_args=(builder.memory(),),
        false_args=(following, seen, builder.memory()))

    builder.position_at(keep)
    builder.set_memory(keep.add_param(MEM, "mem"))
    first = words_of(builder, table)
    builder.store(
        builder.binary(BinOp.ADD, first,
                       builder.binary(BinOp.WRAP_MUL, kind,
                                      builder.int_const(U64, WORD))),
        builder.load(builder.binary(BinOp.ADD, pair,
                                    builder.int_const(U64, WORD))))
    builder.br(pairs, (following,
                       builder.binary(BinOp.OR, seen,
                                      builder.binary(BinOp.WRAP_SHL,
                                                     builder.int_const(U64, 1),
                                                     kind)),
                       builder.memory()))

    # The bits of the types there were, in the word `AT_NULL` would have had.
    builder.position_at(done)
    builder.set_memory(done.add_param(MEM, "mem"))
    builder.store(words_of(builder, table), seen)
    builder.ret()
    return func
