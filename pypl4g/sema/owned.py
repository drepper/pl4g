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
from ..ir.layout import DataLayout, parts_of, stride_of
from ..ir.module import Module
from ..ir.types import (ARENA, MEM, DictType, FuncType, ListType, PtrType, SetType,
                        StrType, Type, U8, U64, VOID)
from ..ir.value import Value
from . import strings, tables

#: What the two are called.
OWN_SYMBOL: Final[str] = "__pl4g_own"

_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8)


def can_own(ty: Type) -> bool:
    """Whether a value of *ty* can be copied into an allocator and given back.

    Text, and a list of anything that can, or of anything that points nowhere.  A
    record, a tuple, an array held inside something else and a table are not yet:
    what they point into would need a walk of their own.
    """
    match ty:
        case StrType():
            return True
        case ListType():
            return not points(ty.element) or can_own(ty.element)
    return False


def points(ty: Type) -> bool:
    """Whether a value of *ty* points into room an allocator gave out."""
    match ty:
        case StrType() | ListType():
            return True
        case PtrType() | SetType() | DictType() | FuncType():
            # A reference has lifetimes of its own, and a table is not yet held
            # to any of this: its keys and values go in as they are.  A lambda's
            # environment is never copied and never given back, so where it is
            # is asked of a lambda where it goes and nowhere here.
            return False
    from .check import _points_somewhere  # pylint: disable=import-outside-toplevel
    return _points_somewhere(ty, lambdas=False)


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


def _build_own(module: Module, func: Function, ty: Type) -> None:
    """The copy: the bytes first, then every element that points somewhere."""
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(module.types.ptr_type(ARENA, mutable=True), "arena")
    value = entry.add_param(ty, "v")
    start, count = _parts(builder, value, ty)
    size = _size(builder, ty, count)
    raw = builder.cast(CastKind.BITCAST, start, _bytes(module))
    # A join of the bytes with nothing: the one routine that takes room in an
    # arena and copies into it, so a copy is what a join already is.
    copied = builder.cast(
        CastKind.BITCAST,
        builder.call(strings.join_function(module),
                     (arena, raw, size, raw, builder.int_const(U64, 0)),
                     _bytes(module)),
        parts_of(ty)[0])
    if isinstance(ty, ListType) and points(ty.element):
        inner = own_function(module, ty.element)
        element = ty.element

        def turn(at: Value) -> None:
            place = _element(builder, copied, element, at)
            builder.store(place, builder.call(inner, (arena, builder.load(place)),
                                              element))

        _each(builder, func, count, "own", turn)
    builder.ret(builder.make_tuple((copied, count, arena), ty))


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
    start, count = _parts(builder, value, ty)
    if isinstance(ty, ListType) and points(ty.element):
        inner = disown_function(module, ty.element)
        element = ty.element

        def turn(at: Value) -> None:
            builder.call(inner, (builder.load(_element(builder, start, element,
                                                       at)),), VOID)

        _each(builder, func, count, "disown", turn)
    builder.call(tables.dispose_function(module),
                 (builder.extract(value, 2, parts_of(ty)[2]),
                  builder.cast(CastKind.BITCAST, start, _bytes(module)),
                  _size(builder, ty, count)), VOID)
    builder.ret()
    return func


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
    if known == "heap" and not (isinstance(ty, ListType) and points(ty.element)):
        start, count = _parts(builder, value, ty)
        builder.call(tables.heap_free_function(module),
                     (builder.cast(CastKind.BITCAST, start, _bytes(module)),
                      _size(builder, ty, count)), VOID)
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
    where = builder.cast(CastKind.BITCAST, start, _bytes(module))
    size = _size(builder, ty, count)
    if known == "heap":
        builder.call(tables.heap_free_function(module), (where, size), VOID)
        return
    builder.call(tables.dispose_function(module),
                 (builder.extract(value, 2, parts_of(ty)[2]), where, size), VOID)
