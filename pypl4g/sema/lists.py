"""What a list needs beyond what it is.

A list is three words -- where its elements are, how many there are, and the
allocator they came from -- and **room to spare beside them**, so that joining
onto one is done where it is: how much room there is is written in a header just
before the elements, sixteen bytes so that they keep the alignment the allocators
hand out.  A list with no allocator -- one in the image, or the zeroed one a record
starts with -- has no header and counts as full, and so does one that points
nowhere, which is what an empty list made while running is: room is taken the
first time there is something to put in it.

Room is taken, grown and given back by the functions here and nowhere else, so
that the header is written and read in one place.  Growing doubles the room, and
gives the old room back only once what it held has been copied out -- the caller
does that, after copying in what is joined on, which may be the list itself.

Taking an element out does not make a new list either: what is left is the same
elements with one gone, so the ones after it move down by one place and the count
is one less.

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
from ..ir.inst import BinOp, CastKind, CmpPred
from ..ir.module import Module
from ..ir.types import ARENA, MEM, U8, U64, VOID, PtrType
from ..ir.value import Value
from . import tables
from .strings import copy_bytes

#: How far before the elements the header is, which holds how many elements there
#: is room for.  Sixteen and not eight, so that the elements are as aligned as the
#: room the allocators hand out.
HEADER: Final[int] = 16

#: How many elements a list's first room has room for, at the least: a list that is
#: joined onto once is joined onto again, and a room of one is a copy every time.
SMALLEST: Final[int] = 4

ROOM_SYMBOL: Final[str] = "__pl4g_list_room"
CAPACITY_SYMBOL: Final[str] = "__pl4g_list_capacity"
FREE_SYMBOL: Final[str] = "__pl4g_list_free"
GROW_SYMBOL: Final[str] = "__pl4g_list_grow"
RELEASE_SYMBOL: Final[str] = "__pl4g_list_release"
JOIN_SYMBOL: Final[str] = "__pl4g_list_join"

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


def _bytes(module: Module) -> PtrType:
    return module.types.ptr_type(U8, mutable=True)


def _allocator(module: Module) -> PtrType:
    return module.types.ptr_type(ARENA, mutable=True)


def _nowhere(builder: IRBuilder, address: Value) -> Value:
    """Whether *address* points nowhere."""
    return builder.compare(CmpPred.EQ, builder.cast(CastKind.BITCAST, address, U64),
                           builder.int_const(U64, 0))


def _header(builder: IRBuilder, where: Value) -> Value:
    """Where the header of the elements at *where* is, as the word it holds."""
    module = builder.module
    return builder.cast(
        CastKind.BITCAST,
        builder.binary(BinOp.SUB, where, builder.int_const(U64, HEADER)),
        module.types.ptr_type(U64, mutable=True))


def room_function(module: Module) -> Function:
    """``__pl4g_list_room(allocator, capacity, stride)``: room for *capacity*
    elements of *stride* bytes from *allocator*, the header written, answering
    where the elements go -- nowhere, for no room at all."""
    func, fresh = tables.generated(module, ROOM_SYMBOL,
                                   (_allocator(module), U64, U64), _bytes(module))
    if not fresh:
        return func
    entry = func.add_block()
    none = func.add_block("none")
    some = func.add_block("some")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    allocator = entry.add_param(_allocator(module), "allocator")
    capacity = entry.add_param(U64, "capacity")
    stride = entry.add_param(U64, "stride")
    builder.condbr(builder.compare(CmpPred.EQ, capacity, builder.int_const(U64, 0)),
                   none, some, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))
    builder.position_at(none)
    builder.set_memory(none.add_param(MEM, "mem"))
    builder.ret(builder.cast(CastKind.BITCAST, builder.int_const(U64, 0),
                             _bytes(module)))
    builder.position_at(some)
    builder.set_memory(some.add_param(MEM, "mem"))
    size = builder.binary(BinOp.WRAP_ADD, builder.int_const(U64, HEADER),
                          builder.binary(BinOp.WRAP_MUL, capacity, stride))
    base = builder.call(tables.allocator_function(module), (allocator, size),
                        _bytes(module))
    builder.store(builder.cast(CastKind.BITCAST, base,
                               module.types.ptr_type(U64, mutable=True)), capacity)
    builder.ret(builder.binary(BinOp.ADD, base, builder.int_const(U64, HEADER)))
    return func


def capacity_function(module: Module) -> Function:
    """``__pl4g_list_capacity(where, allocator)``: how many elements there is room
    for -- what the header says, and nought where there is none, which is a list
    in the image, one never given room, and one that points nowhere."""
    func, fresh = tables.generated(module, CAPACITY_SYMBOL,
                                   (_bytes(module), _allocator(module)), U64)
    if not fresh:
        return func
    entry = func.add_block()
    none = func.add_block("none")
    some = func.add_block("some")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    where = entry.add_param(_bytes(module), "where")
    allocator = entry.add_param(_allocator(module), "allocator")
    builder.condbr(builder.binary(BinOp.OR, _nowhere(builder, where),
                                  _nowhere(builder, allocator)),
                   none, some, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))
    builder.position_at(none)
    builder.set_memory(none.add_param(MEM, "mem"))
    builder.ret(builder.int_const(U64, 0))
    builder.position_at(some)
    builder.set_memory(some.add_param(MEM, "mem"))
    builder.ret(builder.load(_header(builder, where)))
    return func


def free_function(module: Module) -> Function:
    """``__pl4g_list_free(allocator, where, stride)``: the room the elements at
    *where* are in given back, header and all, by the size the header says --
    and nothing for a list with no room of its own."""
    func, fresh = tables.generated(module, FREE_SYMBOL,
                                   (_allocator(module), _bytes(module), U64), VOID)
    if not fresh:
        return func
    entry = func.add_block()
    none = func.add_block("none")
    some = func.add_block("some")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    allocator = entry.add_param(_allocator(module), "allocator")
    where = entry.add_param(_bytes(module), "where")
    stride = entry.add_param(U64, "stride")
    builder.condbr(builder.binary(BinOp.OR, _nowhere(builder, where),
                                  _nowhere(builder, allocator)),
                   none, some, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))
    builder.position_at(none)
    builder.set_memory(none.add_param(MEM, "mem"))
    builder.ret()
    builder.position_at(some)
    builder.set_memory(some.add_param(MEM, "mem"))
    header = _header(builder, where)
    capacity = builder.load(header)
    builder.call(tables.dispose_function(module),
                 (allocator, builder.cast(CastKind.BITCAST, header, _bytes(module)),
                  builder.binary(BinOp.WRAP_ADD, builder.int_const(U64, HEADER),
                                 builder.binary(BinOp.WRAP_MUL, capacity, stride))),
                 VOID)
    builder.ret()
    return func


def grow_function(module: Module) -> Function:
    """``__pl4g_list_grow(where, count, allocator, into, more, stride)``: where
    the elements of a list of *count* are, with room for *more* after them.

    The same place where it has the room.  Otherwise new room from *into* --
    twice what there was, or what is wanted where that is more, and never less
    than `SMALLEST` -- with the elements copied into it.  The old room is not
    given back here: what is joined on may be in it, so that is the caller's,
    once it has been copied (`release_function`).
    """
    bytes_ = _bytes(module)
    func, fresh = tables.generated(
        module, GROW_SYMBOL, (bytes_, U64, _allocator(module), _allocator(module),
                              U64, U64), bytes_)
    if not fresh:
        return func
    entry = func.add_block()
    enough = func.add_block("enough")
    more_room = func.add_block("grow")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    where = entry.add_param(bytes_, "where")
    count = entry.add_param(U64, "count")
    allocator = entry.add_param(_allocator(module), "allocator")
    into = entry.add_param(_allocator(module), "into")
    more = entry.add_param(U64, "more")
    stride = entry.add_param(U64, "stride")
    wanted = builder.binary(BinOp.WRAP_ADD, count, more)
    capacity = builder.call(capacity_function(module), (where, allocator), U64)
    builder.condbr(builder.compare(CmpPred.ULE, wanted, capacity), enough,
                   more_room, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))
    builder.position_at(enough)
    builder.set_memory(enough.add_param(MEM, "mem"))
    builder.ret(where)
    builder.position_at(more_room)
    builder.set_memory(more_room.add_param(MEM, "mem"))
    doubled = builder.binary(BinOp.WRAP_MUL, capacity, builder.int_const(U64, 2))
    room = builder.binary(BinOp.UMAX, builder.binary(BinOp.UMAX, wanted, doubled),
                          builder.int_const(U64, SMALLEST))
    made = builder.call(room_function(module), (into, room, stride), bytes_)
    copy_bytes(builder, made, builder.int_const(U64, 0), where,
               builder.binary(BinOp.WRAP_MUL, count, stride))
    builder.ret(made)
    return func


def release_function(module: Module) -> Function:
    """``__pl4g_list_release(allocator, old, new, stride)``: the room a list
    grew out of given back, where it grew into new room."""
    bytes_ = _bytes(module)
    func, fresh = tables.generated(module, RELEASE_SYMBOL,
                                   (_allocator(module), bytes_, bytes_, U64), VOID)
    if not fresh:
        return func
    entry = func.add_block()
    same = func.add_block("same")
    moved = func.add_block("moved")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    allocator = entry.add_param(_allocator(module), "allocator")
    old = entry.add_param(bytes_, "old")
    new = entry.add_param(bytes_, "new")
    stride = entry.add_param(U64, "stride")
    builder.condbr(builder.compare(CmpPred.EQ,
                                   builder.cast(CastKind.BITCAST, old, U64),
                                   builder.cast(CastKind.BITCAST, new, U64)),
                   same, moved, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))
    builder.position_at(same)
    builder.set_memory(same.add_param(MEM, "mem"))
    builder.ret()
    builder.position_at(moved)
    builder.set_memory(moved.add_param(MEM, "mem"))
    builder.call(free_function(module), (allocator, old, stride), VOID)
    builder.ret()
    return func


def join_function(module: Module) -> Function:
    """``__pl4g_list_join(allocator, first, first_count, second, second_count,
    stride)``: new room from *allocator* holding the two runs end to end, with
    room for exactly that many -- answering where; nowhere for nothing."""
    bytes_ = _bytes(module)
    func, fresh = tables.generated(module, JOIN_SYMBOL,
                                   (_allocator(module), bytes_, U64, bytes_, U64,
                                    U64), bytes_)
    if not fresh:
        return func
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    allocator = entry.add_param(_allocator(module), "allocator")
    first = entry.add_param(bytes_, "first")
    first_count = entry.add_param(U64, "first.count")
    second = entry.add_param(bytes_, "second")
    second_count = entry.add_param(U64, "second.count")
    stride = entry.add_param(U64, "stride")
    made = builder.call(room_function(module),
                        (allocator, builder.binary(BinOp.WRAP_ADD, first_count,
                                                   second_count), stride), bytes_)
    first_size = builder.binary(BinOp.WRAP_MUL, first_count, stride)
    copy_bytes(builder, made, builder.int_const(U64, 0), first, first_size)
    copy_bytes(builder, made, first_size, second,
               builder.binary(BinOp.WRAP_MUL, second_count, stride))
    builder.ret(made)
    return func
