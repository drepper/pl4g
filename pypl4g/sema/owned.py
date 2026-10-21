"""What a container owns, copied into its allocator and given back from it.

**A container's elements point into the container's allocator.**  A list of
strings, an array of strings, a list of lists: what each element points at is in the
allocator the container was made with, so that what the container holds is never
something another allocator may give back first.  An element whose making the
compiler cannot prove was in that allocator is copied into it on the way in, which is
the defensive answer to not knowing -- and what is replaced, and a temporary nobody
else names, is given back.

**What is given back is the element's own storage**, the bytes of a string or the
run of a list, and not what that points at in turn.  Something made in the
container's own allocator goes in as it is, so two elements -- or an element and a
name -- may share what lies below their own storage, and giving that back for one
would take it from the other.

**The copy is a function per type, generated on first ask**, all the way down: a list
of lists of strings is a loop inside a loop, and the compiler already turns loops
into functions for the hash tables.  A type whose values point nowhere needs none.

**Giving back one object is `__pl4g_free(arena, where, size)`**, which the runtime
supplies for every allocator.  An arena gives back only all at once, so for an arena
it does nothing -- the call is there so that an allocator that can give back one
object at a time takes its place without anything else changing.
"""

from __future__ import annotations

from typing import Final

from ..ir.builder import IRBuilder
from ..ir.function import Function
from ..ir.inst import BinOp, CastKind, CmpPred
from ..ir.layout import (DataLayout, member_offsets_of, offsets_of, parts_of,
                         stride_of, tag_offset_of)
from ..ir.module import Module
from ..ir.types import (ARENA, MEM, ArrayType, DictType, FuncType, IterType,
                        ListType, ProductType, PtrType, RunningType, SetType,
                        StrType, SumType, TupleType, Type, U8, U64, VOID,
                        held_in_memory)
from ..ir.value import Value
from . import lists, strings, tables

#: What the two are called.
OWN_SYMBOL: Final[str] = "__pl4g_own"

_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8)


def can_own(ty: Type, seen: frozenset[int] = frozenset()) -> bool:
    """Whether a value of *ty* can be copied into an allocator and given back.

    Text, and a list of anything that can, or of anything that points nowhere; and
    a record, a tuple or a sum each part of which can, or points nowhere -- copied
    part by part, or by the one part a sum holds, which is all a copy of one is.  A
    record that is moved and never copied -- `@[unique]`, closed, a device -- is
    not, nor is an array held inside something else or a table: what they point
    into would need a walk of its own.

    A type that reaches itself through a list is asked about once: met again on
    the way down, it is taken to be what the rest of the walk finds it to be,
    since the copy of it is the one function calling itself.
    """
    if id(ty) in seen:
        return True
    deeper = seen | {id(ty)}
    match ty:
        case StrType():
            return True
        case ListType():
            return not points(ty.element) or can_own(ty.element, deeper)
        case ProductType() if not (ty.unique or ty.closed_by or ty.device):
            return all(not points(part) or can_own(part, deeper)
                       for _, part in ty.fields)
        case TupleType():
            return all(not points(part) or can_own(part, deeper)
                       for part in ty.members)
        case SumType():
            return all(not points(part) or can_own(part, deeper)
                       for _, part in ty.variants)
        case ArrayType() if not ty.fixed and ty.rank == 1:
            # A view of one dimension: its elements copied into a run of their own.
            return not points(ty.element) or can_own(ty.element, deeper)
    return False


def points(ty: Type) -> bool:
    """Whether a value of *ty* points into room an allocator gave out."""
    match ty:
        case StrType() | ListType():
            return True
        case PtrType() | SetType() | DictType() | FuncType() | IterType() \
                | RunningType():
            # A reference has lifetimes of its own, and a table is not yet held
            # to any of this: its keys and values go in as they are.  A lambda's
            # environment is never copied and never given back, so where it is
            # is asked of a lambda where it goes and nowhere here.
            return False
    from .check import _points_somewhere  # pylint: disable=import-outside-toplevel
    # Inside something else a reference has lifetimes of its own just as much.
    return _points_somewhere(ty, lambdas=False, references=False)


def own_function(module: Module, ty: Type) -> Function:
    """``__pl4g_own.T(arena, v)``: *v* copied into *arena*, all the way down."""
    arena = module.types.ptr_type(ARENA, mutable=True)
    func, fresh = tables.generated(module, ".".join((OWN_SYMBOL, ty.mangled())),
                                   (arena, ty), ty)
    if fresh:
        _build_own(module, func, ty)
    return func


def _bytes(module: Module) -> PtrType:
    return module.types.ptr_type(U8, mutable=True)


def _parts(builder: IRBuilder, value: Value, ty: Type) -> tuple[Value, Value]:
    """Where a string's or a list's elements are, and how many there are."""
    return (builder.extract(value, 0, parts_of(ty)[0]),
            builder.extract(value, 1, U64))


def _size(builder: IRBuilder, ty: Type, count: Value) -> Value:
    """How many bytes the elements of a string or a list take."""
    if isinstance(ty, StrType):
        return count
    assert isinstance(ty, ListType)
    return builder.binary(BinOp.WRAP_MUL, count,
                          builder.int_const(U64, stride_of(ty.element, _LAYOUT)))


def _element(builder: IRBuilder, start: Value, element: Type,
             index: Value) -> Value:
    """Where the element at *index* is."""
    return builder.binary(
        BinOp.ADD, start,
        builder.binary(BinOp.WRAP_MUL, index,
                       builder.int_const(U64, stride_of(element, _LAYOUT))))


def _each(builder: IRBuilder, func: Function, count: Value, name: str,
          turn) -> None:
    """Run *turn(at)* for every index below *count*, carrying the memory through."""
    loop = func.add_block(".".join((name, "loop")))
    body = func.add_block(".".join((name, "body")))
    done = func.add_block(".".join((name, "done")))
    builder.br(loop, (builder.int_const(U64, 0), builder.memory()))
    builder.position_at(loop)
    at = loop.add_param(U64, "at")
    builder.set_memory(loop.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.ULT, at, count), body, done,
                   true_args=(builder.memory(),), false_args=(builder.memory(),))
    builder.position_at(body)
    builder.set_memory(body.add_param(MEM, "mem"))
    turn(at)
    builder.br(loop, (builder.binary(BinOp.WRAP_ADD, at, builder.int_const(U64, 1)),
                      builder.memory()))
    builder.position_at(done)
    builder.set_memory(done.add_param(MEM, "mem"))


def _owned_leaves(builder: IRBuilder, arena: Value, leaves: list[Value],
                  ty: Type) -> list[Value]:
    """The parts a value of *ty* travels in, with every string and list among
    them copied into *arena* -- a record's and a tuple's taken field by field."""
    module = builder.module
    match ty:
        case StrType() | ListType():
            copied = builder.call(own_function(module, ty),
                                  (arena, builder.make_tuple(leaves, ty)), ty)
            return [builder.extract(copied, at, part)
                    for at, part in enumerate(parts_of(ty))]
        case ProductType() | TupleType():
            parts = [part for _, part in ty.fields] if isinstance(ty, ProductType) \
                else list(ty.members)
            found: list[Value] = []
            at = 0
            for part in parts:
                count = len(parts_of(part))
                piece = leaves[at:at + count]
                found.extend(_owned_leaves(builder, arena, piece, part)
                             if points(part) else piece)
                at += count
            return found
    return leaves


def _build_own(module: Module, func: Function, ty: Type) -> None:
    """The copy: the bytes first, then every element that points somewhere --
    or, of a record or a tuple, every part that does."""
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(module.types.ptr_type(ARENA, mutable=True), "arena")
    value = entry.add_param(ty, "v")
    if held_in_memory(ty) and isinstance(ty, (ProductType, TupleType, SumType)):
        _build_own_in_memory(builder, func, arena, value, ty)
        return
    if isinstance(ty, (ProductType, TupleType)):
        builder.ret(builder.make_tuple(
            _owned_leaves(builder, arena, builder.leaves(value, ty), ty), ty))
        return
    if isinstance(ty, ArrayType):
        _build_own_view(builder, func, arena, value, ty)
        return
    start, count = _parts(builder, value, ty)
    size = _size(builder, ty, count)
    raw = builder.cast(CastKind.BITCAST, start, _bytes(module))
    # A join of the bytes with nothing: the one routine that takes room in an
    # arena and copies into it, so a copy is what a join already is -- a list's
    # own, which writes the header its room has.
    joined = builder.call(
        lists.join_function(module),
        (arena, raw, count, raw, builder.int_const(U64, 0), _stride(builder, ty)),
        _bytes(module)) if isinstance(ty, ListType) else builder.call(
            strings.join_function(module),
            (arena, raw, size, raw, builder.int_const(U64, 0)), _bytes(module))
    copied = builder.cast(CastKind.BITCAST, joined, parts_of(ty)[0])
    if isinstance(ty, ListType) and points(ty.element):
        inner = own_function(module, ty.element)
        element = ty.element

        def turn(at: Value) -> None:
            place = _element(builder, copied, element, at)
            builder.store(place, builder.call(inner, (arena, builder.load(place)),
                                              element))

        _each(builder, func, count, "own", turn)
    builder.ret(builder.make_tuple((copied, count, arena), ty))


def _build_own_in_memory(builder: IRBuilder, func: Function, arena: Value,
                         value: Value, ty: ProductType | TupleType | SumType) -> None:
    """A value held in memory -- a sum, or a record or a tuple holding one --
    copied into room of this function's own, and then every part of the copy
    that points somewhere copied into *arena* where it stands.  Of a sum that is
    the one part its tag says it holds, chosen by a switch over the tag.

    The answer is that room, which the caller's storage receives as every
    answer held in memory does.  A sum reaching itself through a list is a copy
    that calls the list's, which calls this one for every element: the function
    exists before its body is written, so the circle closes on a call."""
    module = builder.module
    room = builder.frame(ty)
    builder.copy(room, builder.cast(CastKind.BITCAST, value,
                                    module.types.ptr_type(ty, mutable=True)), ty)
    if isinstance(ty, SumType):
        tag = builder.load(builder.cast(
            CastKind.BITCAST,
            builder.binary(BinOp.ADD, room,
                           builder.int_const(U64, tag_offset_of(ty, _LAYOUT))),
            module.types.ptr_type(U8, mutable=True)))
        # A chain of comparisons, one per part that points anywhere, which is
        # what a `match` is lowered to and for the same reason: each way carries
        # the memory, and a part pointing nowhere is copied by the bytes alone.
        done = func.add_block("owned")
        for at, (_, part) in enumerate(ty.variants):
            if not points(part):
                continue
            arm = func.add_block("own")
            other = func.add_block("other")
            builder.condbr(builder.compare(CmpPred.EQ, tag, builder.int_const(U8, at)),
                           arm, other, true_args=(builder.memory(),),
                           false_args=(builder.memory(),))
            builder.position_at(arm)
            builder.set_memory(arm.add_param(MEM, "mem"))
            _own_where_it_stands(builder, arena, room, 0, part)
            builder.br(done, (builder.memory(),))
            builder.position_at(other)
            builder.set_memory(other.add_param(MEM, "mem"))
        builder.br(done, (builder.memory(),))
        builder.position_at(done)
        builder.set_memory(done.add_param(MEM, "mem"))
    else:
        parts = [part for _, part in ty.fields] if isinstance(ty, ProductType) \
            else list(ty.members)
        offsets = offsets_of(ty, _LAYOUT) if isinstance(ty, ProductType) \
            else member_offsets_of(ty, _LAYOUT)
        for part, offset in zip(parts, offsets):
            if points(part):
                _own_where_it_stands(builder, arena, room, offset, part)
    builder.ret(builder.cast(CastKind.BITCAST, room, ty))


def _own_where_it_stands(builder: IRBuilder, arena: Value, room: Value,
                         offset: int, part: Type) -> None:
    """The part of *part*'s type at *offset* in *room*, copied into *arena* and
    written back where it was read."""
    module = builder.module
    place = builder.cast(CastKind.BITCAST, room,
                         module.types.ptr_type(part, mutable=True))
    if offset:
        place = builder.binary(BinOp.ADD, place, builder.int_const(U64, offset))
    builder.store(place, builder.call(own_function(module, part),
                                      (arena, builder.load(place)), part))


def _build_own_view(builder: IRBuilder, func: Function, arena: Value, value: Value,
                    ty: ArrayType) -> None:
    """A view's elements copied into a run of their own in *arena*, one after
    another, and the view of that run: where, how many, and a stride of one.
    What each element points into is copied too, where it points anywhere."""
    module = builder.module
    pointer, _, _ = parts_of(ty)
    start = builder.extract(value, 0, pointer)
    count = builder.extract(value, 1, U64)
    apart = builder.extract(value, 2, U64)
    size = stride_of(ty.element, _LAYOUT)
    room = builder.cast(
        CastKind.BITCAST,
        tables.allocate(builder, arena,
                        builder.binary(BinOp.WRAP_MUL, count,
                                       builder.int_const(U64, size))),
        pointer)
    element = ty.element
    inner = own_function(module, element) if points(element) else None

    def turn(at: Value) -> None:
        read = builder.load(_element(builder, start, element,
                                     builder.binary(BinOp.WRAP_MUL, at, apart)))
        if inner is not None:
            read = builder.call(inner, (arena, read), element)
        builder.store(_element(builder, room, element, at), read)

    _each(builder, func, count, "own", turn)
    builder.ret(builder.make_tuple((room, count, builder.int_const(U64, 1)), ty))


DISOWN_SYMBOL: Final[str] = "__pl4g_disown"


def disown_function(module: Module, ty: Type) -> Function:
    """``__pl4g_disown.T(v)``: *v* given back, all the way down.

    Every value carries its allocator, so this asks the value: what each element
    points into is given back to the allocator that element names, and then the
    value's own storage to its own.  For a pool that is nothing and for the image it
    is nothing; for the heap it is the object, by its size.
    """
    func, fresh = tables.generated(module, ".".join((DISOWN_SYMBOL, ty.mangled())),
                                   (ty,), VOID)
    if not fresh:
        return func
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    value = entry.add_param(ty, "v")
    if isinstance(ty, (ProductType, TupleType, SumType)):
        # A record, a tuple or a sum: what it owns is what its parts own, and
        # it has no storage of its own to give back -- it is in whatever holds
        # it, a list's run or a frame.
        if held_in_memory(ty):
            _disown_in_memory(builder, func, builder.cast(
                CastKind.BITCAST, value, module.types.ptr_type(ty, mutable=True)),
                ty)
        else:
            _disown_leaves(builder, builder.leaves(value, ty), ty)
        builder.ret()
        return func
    start, count = _parts(builder, value, ty)
    if isinstance(ty, ListType) and points(ty.element):
        inner = disown_function(module, ty.element)
        element = ty.element

        def turn(at: Value) -> None:
            place = _element(builder, start, element, at)
            # An element held in memory is where it is, which is what a value of
            # it is: read, it would be copied into a frame to be given back.
            read = builder.cast(CastKind.BITCAST, place, element) \
                if held_in_memory(element) else builder.load(place)
            builder.call(inner, (read,), VOID)

        _each(builder, func, count, "disown", turn)
    _dispose(builder, builder.extract(value, 2, parts_of(ty)[2]), start, count, ty)
    builder.ret()
    return func


def _disown_leaves(builder: IRBuilder, leaves: list[Value], ty: Type) -> None:
    """Give back what the parts of a value travelling in registers own, a
    record's and a tuple's taken field by field as `_owned_leaves` copies them."""
    module = builder.module
    if isinstance(ty, (ProductType, TupleType)) and not held_in_memory(ty):
        parts = [part for _, part in ty.fields] if isinstance(ty, ProductType) \
            else list(ty.members)
        at = 0
        for part in parts:
            count = len(parts_of(part))
            if points(part):
                _disown_leaves(builder, leaves[at:at + count], part)
            at += count
        return
    whole = leaves[0] if parts_of(ty) == (ty,) else builder.make_tuple(leaves, ty)
    builder.call(disown_function(module, ty), (whole,), VOID)


def _disown_in_memory(builder: IRBuilder, func: Function, place: Value,
                      ty: ProductType | TupleType | SumType) -> None:
    """Give back what the parts of a value held in memory own, read where they
    stand: every part of a record or a tuple that points somewhere, and of a sum
    the one part its tag says it holds."""
    module = builder.module

    def one(offset: int, part: Type) -> None:
        where = builder.cast(CastKind.BITCAST, place,
                             module.types.ptr_type(part, mutable=True))
        if offset:
            where = builder.binary(BinOp.ADD, where, builder.int_const(U64, offset))
        # A part held in memory is where it is, which is what a value of it is.
        read = builder.cast(CastKind.BITCAST, where, part) if held_in_memory(part) \
            else builder.load(where)
        builder.call(disown_function(module, part), (read,), VOID)

    if isinstance(ty, SumType):
        tag = builder.load(builder.cast(
            CastKind.BITCAST,
            builder.binary(BinOp.ADD, place,
                           builder.int_const(U64, tag_offset_of(ty, _LAYOUT))),
            module.types.ptr_type(U8, mutable=True)))
        done = func.add_block("disowned")
        for at, (_, part) in enumerate(ty.variants):
            if not points(part):
                continue
            arm = func.add_block("disown")
            other = func.add_block("other")
            builder.condbr(builder.compare(CmpPred.EQ, tag, builder.int_const(U8, at)),
                           arm, other, true_args=(builder.memory(),),
                           false_args=(builder.memory(),))
            builder.position_at(arm)
            builder.set_memory(arm.add_param(MEM, "mem"))
            one(0, part)
            builder.br(done, (builder.memory(),))
            builder.position_at(other)
            builder.set_memory(other.add_param(MEM, "mem"))
        builder.br(done, (builder.memory(),))
        builder.position_at(done)
        builder.set_memory(done.add_param(MEM, "mem"))
        return
    parts = [part for _, part in ty.fields] if isinstance(ty, ProductType) \
        else list(ty.members)
    offsets = offsets_of(ty, _LAYOUT) if isinstance(ty, ProductType) \
        else member_offsets_of(ty, _LAYOUT)
    for part, offset in zip(parts, offsets):
        if points(part):
            one(offset, part)


def _stride(builder: IRBuilder, ty: ListType) -> Value:
    """How far apart a list's elements are, as a number in the program."""
    return builder.int_const(U64, stride_of(ty.element, _LAYOUT))


def _dispose(builder: IRBuilder, allocator: Value, start: Value, count: Value,
             ty: Type) -> None:
    """A string's bytes or a list's room given back to *allocator*: a list's by
    the size its header says, which is more than its elements where it has
    room to spare or has had an element taken out."""
    module = builder.module
    where = builder.cast(CastKind.BITCAST, start, _bytes(module))
    if isinstance(ty, ListType):
        builder.call(lists.free_function(module), (allocator, where, _stride(builder, ty)),
                     VOID)
        return
    builder.call(tables.dispose_function(module),
                 (allocator, where, _size(builder, ty, count)), VOID)


def give_back(builder: IRBuilder, value: Value, ty: Type,
              known: str | None) -> None:
    """Give *value* back, using what the compiler knows of its allocator.

    *known* is ``"pool"`` where every allocator it can name is a pool or the image,
    and then nothing is emitted at all; ``"heap"`` where it is the heap and nothing
    it holds points anywhere, and then the heap is called straight away; and nothing
    where it has to be asked of the value, which is the dispatch.
    """
    if known == "pool" or not points(ty):
        return
    module = builder.module
    if known == "heap" and isinstance(ty, StrType):
        start, count = _parts(builder, value, ty)
        builder.call(tables.heap_free_function(module),
                     (builder.cast(CastKind.BITCAST, start, _bytes(module)),
                      _size(builder, ty, count)), VOID)
        return
    if known == "heap" and isinstance(ty, (ListType, ArrayType)) \
            and not points(ty.element):
        start, count = _parts(builder, value, ty)
        _dispose(builder, builder.extract(value, 2, parts_of(ty)[2]), start, count,
                 ty)
        return
    builder.call(disown_function(module, ty), (value,), VOID)


def give_back_storage(builder: IRBuilder, value: Value, ty: Type,
                      known: str | None) -> None:
    """Give back a string's bytes or a list's run, and nothing they point at.

    What a join of two lists copied out of a temporary: the elements went into the
    answer, so only the run they were in is the temporary's to give back.
    """
    if known == "pool":
        return
    module = builder.module
    start, count = _parts(builder, value, ty)
    if known == "heap" and isinstance(ty, StrType):
        builder.call(tables.heap_free_function(module),
                     (builder.cast(CastKind.BITCAST, start, _bytes(module)),
                      _size(builder, ty, count)), VOID)
        return
    _dispose(builder, builder.extract(value, 2, parts_of(ty)[2]), start, count, ty)
