"""What a list needs beyond what it is.

A list is two words -- where its elements are and how many there are -- and
everything it could do until now made a new one.  Taking an element out is the
first thing that does not: what is left is the same elements with one gone, so
the ones after it move down by one place and the count is one less.

That move is the only piece that has to be generated rather than written where
it is asked for: how far it goes is not known while compiling, so it is a loop,
and a loop written at every place a program takes an element out would be that
loop once per place.  It is one function for every list of every element type,
because what it moves is bytes and how many of them there are is what it is
told.

**It moves down and never up**, which is what makes one loop enough: the bytes
of the element after the one taken out land where that one began, so a walk from
the front reads each byte before anything writes over it.  A move the other way
-- which putting an element *in* would want -- has to walk from the back, and is
not written because nothing yet asks for it.
"""

from __future__ import annotations

from typing import Final

from ..ir.builder import IRBuilder
from ..ir.function import Function
from ..ir.inst import BinOp, CmpPred
from ..ir.module import Module
from ..ir.types import MEM, U8, U64, VOID
from . import tables

#: What the function is called.  It carries the prefix everything of the
#: compiler's carries, so nothing a program can write collides with it.
ERASE_SYMBOL: Final[str] = "__pl4g_list_erase"


def erase_function(module: Module) -> Function:
    """``__pl4g_list_erase(at, stride, count, which)``: one element out.

    *at* is where the elements are, *stride* how long one of them is, *count*
    how many there are, and *which* the one to take out.  What is left is the
    same run with the ones after it moved down; how many there are is the
    caller's to write, since the caller is what holds the list.

    Nothing is checked here.  Where the index may be past the end is where the
    program says so, and that check stops the program before this is called.
    """
    bytes_ = module.types.ptr_type(U8, mutable=True)
    func, fresh = tables.generated(module, ERASE_SYMBOL,
                                   (bytes_, U64, U64, U64), VOID)
    if not fresh:
        return func
    entry = func.add_block()
    walk = func.add_block("walk")
    move = func.add_block("move")
    done = func.add_block("done")
    builder = IRBuilder(module, func)

    builder.position_at(entry)
    at = entry.add_param(bytes_, "at")
    stride = entry.add_param(U64, "stride")
    count = entry.add_param(U64, "count")
    which = entry.add_param(U64, "which")
    # Where the bytes that move begin, and how many of them there are: every
    # element after the one taken out.
    start = builder.binary(BinOp.WRAP_MUL, which, stride)
    moving = builder.binary(
        BinOp.WRAP_MUL, stride,
        builder.binary(BinOp.WRAP_SUB,
                       builder.binary(BinOp.WRAP_SUB, count,
                                      builder.int_const(U64, 1)), which))
    builder.br(walk, (builder.int_const(U64, 0), builder.memory()))

    builder.position_at(walk)
    step = walk.add_param(U64, "step")
    token = walk.add_param(MEM, "mem")
    builder.set_memory(token)
    builder.condbr(builder.compare(CmpPred.ULT, step, moving), move, done)

    # One byte down by one element's worth, from the front, so that each is read
    # before anything writes over it.
    builder.position_at(move)
    where = builder.binary(BinOp.WRAP_ADD, start, step)
    builder.store(builder.binary(BinOp.ADD, at, where),
                  builder.load(builder.binary(
                      BinOp.ADD, at,
                      builder.binary(BinOp.WRAP_ADD, where, stride))))
    builder.br(walk, (builder.binary(BinOp.WRAP_ADD, step,
                                     builder.int_const(U64, 1)),
                      builder.memory()))

    builder.position_at(done)
    # What holds after the loop is what the loop carried: the token the body
    # left is the body's, and the body is not on every way here.
    builder.set_memory(token)
    builder.ret()
    return func
