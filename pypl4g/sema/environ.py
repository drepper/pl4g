"""The environment, and the table the compiler builds it into.

`⎕environ` is a name the compiler provides, of type `⸨str: str⸩`.  What it
stands for is a variable of the image holding one table, and what fills that
variable is written here: a function generated the way the table runtime is
generated, called by the entry point before anything of the program runs.

**Why it is generated rather than written.**  Neither half of the compiler can
build it alone.  The strings are on the stack the kernel set the process up on,
which only the entry point can reach and only before anything else runs; what
they have to become is a hash table, whose layout only the compiler knows.  So
the runtime answers with the strings -- each name followed by what it stands
for, split where its bytes already are -- and this walks them two at a time and
puts each pair in a table.

**A program that never names it carries none of this.**  The variable is made on
first ask, as the heap is, so a program that does not read the environment has no
table, no builder, and nothing asked of the system.

**What it holds is what the program started with.**  The table is built once,
before the program runs, and nothing writes it afterwards -- the type says so,
and the entry point is the only thing that fills it.  So a change to the
process's own environment, whenever the language grows a way to make one, leaves
this dictionary saying what it said.
"""

from __future__ import annotations

from typing import Final

from ..ir.builder import IRBuilder
from ..ir.function import Function, Linkage
from ..ir.inst import BinOp, CastKind, CmpPred
from ..ir.module import GlobalVar, Module
from ..ir.types import DictType, MEM, STR, U64
from ..target.started import ENVIRON_MAKE as MAKE_SYMBOL, ENVIRON_SYMBOL as SYMBOL
from . import tables

#: How far into a variable's two strings what its name stands for begins: a
#: string is three words -- where, how many, and its allocator -- and the name is
#: the first of the two.
VALUE_AT: Final[int] = 3 * tables.WORD


def shape() -> tables.Shape:
    """What the table holds: text standing for text."""
    return tables.Shape(key=STR, value=STR)


def held_type(module: Module) -> DictType:
    """The type of the name: a dictionary nothing may put anything in."""
    return module.types.dict_type(STR, STR)


def variable(module: Module) -> GlobalVar:
    """The variable the table waits in, made once for the program.

    Writable in the image because the entry point fills it; not writable by the
    program, which the type says -- a read-only collection is not a place to put
    another one.
    """
    found = module.globals.get(SYMBOL)
    if isinstance(found, GlobalVar):
        return found
    ty = held_type(module)
    return module.add_global(GlobalVar(
        name=SYMBOL, value_type=ty,
        ptr_type=module.types.ptr_type(ty, mutable=True),
        initializer=None, linkage=Linkage.INTERNAL), key=SYMBOL)


def make_function(module: Module, heap: GlobalVar) -> Function:
    """``__pl4g_environ_make(at, many)``: the table the strings come to.

    *at* is where the runtime put them and *many* is how many there are, which
    is two per variable.  The walk takes them two at a time; a count that is odd
    -- which the runtime does not produce -- leaves the last one out rather than
    reading past the end.

    The pairs go in through the same `put` a program's own dictionary uses, so
    a name looked up here and a name looked up there are one key by one rule.
    """
    held = shape()
    tables.ensure_runtime(module, held)
    table_ptr = tables.table_type(module)
    words = module.types.ptr_type(U64, mutable=True)
    func, fresh = tables.generated(module, MAKE_SYMBOL, (words, U64), table_ptr)
    if not fresh:
        return func
    put = module.functions[tables.named(tables.PUT_SYMBOL, held)]
    text = module.types.ptr_type(STR, mutable=True)
    entry = func.add_block()
    turn = func.add_block("turn")
    one = func.add_block("one")
    done = func.add_block("done")
    builder = IRBuilder(module, func)

    builder.position_at(entry)
    at = entry.add_param(words, "at")
    many = entry.add_param(U64, "many")
    table = builder.call(module.functions[tables.NEW_SYMBOL],
                         (builder.address(heap),
                          builder.int_const(U64, held.stride)), table_ptr)
    builder.br(turn, (builder.int_const(U64, 0), builder.memory()))

    # Two strings are one variable, so what says there is another one is that
    # both of them are there.
    builder.position_at(turn)
    which = turn.add_param(U64, "which")
    builder.set_memory(turn.add_param(MEM, "mem"))
    builder.condbr(
        builder.compare(CmpPred.ULT,
                        builder.binary(BinOp.WRAP_ADD, which,
                                       builder.int_const(U64, 1)), many),
        one, done, true_args=(builder.memory(),),
        false_args=(builder.memory(),))

    builder.position_at(one)
    builder.set_memory(one.add_param(MEM, "mem"))
    where = builder.binary(
        BinOp.ADD, at,
        builder.binary(BinOp.WRAP_MUL, which,
                       builder.int_const(U64, VALUE_AT)))
    name = builder.load(builder.cast(CastKind.BITCAST, where, text))
    value = builder.load(builder.cast(
        CastKind.BITCAST,
        builder.binary(BinOp.ADD, where, builder.int_const(U64, VALUE_AT)),
        text))
    place = builder.call(put, (table, name), table_ptr)
    builder.store(builder.cast(
        CastKind.BITCAST,
        builder.binary(BinOp.ADD, place,
                       builder.int_const(U64, held.value_at)), text), value)
    builder.br(turn, (builder.binary(BinOp.WRAP_ADD, which,
                                     builder.int_const(U64, 2)),
                      builder.memory()))

    builder.position_at(done)
    builder.set_memory(done.add_param(MEM, "mem"))
    builder.ret(table)
    return func
