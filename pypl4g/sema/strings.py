"""Text, written in the representation.

A `str` is where its bytes are and how many there are, and the bytes are always
well-formed UTF-8.  That last is an invariant rather than a hope, and it is what
makes everything here short: there are two ways to make a string and neither can
produce anything else.  A literal's bytes are what the source held, encoded by
the compiler.  A join puts well-formed bytes after well-formed bytes.  So a walk
over the characters is a decoder and not a validator -- which is where nearly
all of the cost of walking text usually goes -- and nothing that reads a string
has to ask whether it is one.

Two functions are generated for it, in the representation and not as assembly
per target, for the reason the table runtime gives: both have loops, several
live values, and arithmetic that wants a register allocator, which is exactly
what the compiler already does for a program.  They are generated once per
module and a program with no string has neither.

**Taking one character** reads the leading byte, works out how many bytes the
sequence has and what the leading bits of the code point are, and then folds in
six bits per continuation byte.  It answers the code point and where the next
one begins, because the walk wants both and reading the leading byte twice to
get them separately would be the same work done again.

**Joining two** takes room for the two lengths together and copies each side
into it.  The room comes from the arena the compiler provides, which is where
everything that outlives an expression and was not written down already comes
from.
"""

from __future__ import annotations

from typing import Final

from ..ir.builder import IRBuilder
from ..ir.function import FuncAttrs, Function, Linkage
from ..ir.inst import BinOp, CastKind, CmpPred
from ..ir.module import Module
from ..ir.types import ARENA, CHAR, MEM, PtrType, Type, U8, U32, U64
from ..ir.value import Value
from .tables import ALLOC_SYMBOL

#: What each of the two is called.  The names carry the compiler's own prefix
#: for the same reason every generated symbol does: nothing a program can write
#: collides with them.
NEXT_SYMBOL: Final[str] = "__pl4g_str_next"
JOIN_SYMBOL: Final[str] = "__pl4g_str_join"
LENGTH_SYMBOL: Final[str] = "__pl4g_str_length"

#: What the leading byte of a sequence says.  The first number is the value the
#: byte must be below for the row to apply, the second how many bits of the code
#: point the leading byte itself carries, and the third how many bytes the whole
#: sequence has.  Read in order, first match wins -- which is what makes the
#: test a chain of comparisons and not a table lookup.
_LEADING: Final[tuple[tuple[int, int, int], ...]] = (
    (0x80, 7, 1),
    (0xE0, 5, 2),
    (0xF0, 4, 3),
    (0x100, 3, 4),
)


def _generated(module: Module, name: str, params: tuple[Type, ...],
               result: Type, impure: bool) -> tuple[Function, bool]:
    """A function of this module's own, and whether it has yet to be built.

    Whether it is impure is the honest answer and not a convenience: taking one
    character reads bytes and changes nothing, so a function that walks a string
    is as pure as one that reads an array; joining two takes room from an arena,
    which outlives the call, so one that joins is not.
    """
    found = module.functions.get(name)
    if isinstance(found, Function):
        return found, False
    func = module.add_function(Function(
        name, module.types.func_type(params, result),
        FuncAttrs(abi="pl4g.runtime", impure=impure),
        linkage=Linkage.INTERNAL))
    return func, True


def bytes_type(module: Module) -> PtrType:
    """What the bytes of a string are reached by."""
    return module.types.ptr_type(U8, mutable=True)


def taken_type(module: Module) -> Type:
    """What taking one character answers with: the character and where the next
    one begins."""
    return module.types.tuple_type((CHAR, U64))


def next_function(module: Module) -> Function:
    """The one that takes the character at a place, built on first ask."""
    func, fresh = _generated(
        module, NEXT_SYMBOL, (bytes_type(module), U64), taken_type(module),
        impure=False)
    if fresh:
        _build_next(module, func)
    return func


def join_function(module: Module) -> Function:
    """The one that puts two strings' bytes end to end, built on first ask."""
    arena = module.types.ptr_type(ARENA, mutable=True)
    func, fresh = _generated(
        module, JOIN_SYMBOL,
        (arena, bytes_type(module), U64, bytes_type(module), U64),
        bytes_type(module), impure=True)
    if fresh:
        _build_join(module, func)
    return func


def length_function(module: Module) -> Function:
    """The one that counts the characters of a string, built on first ask."""
    func, fresh = _generated(
        module, LENGTH_SYMBOL, (bytes_type(module), U64), U64, impure=False)
    if fresh:
        _build_length(module, func)
    return func


def _build_length(module: Module, func: Function) -> None:
    """Build the one that counts the characters of a string.

    A walk and a counter, because that is what counting characters of UTF-8 is:
    how many bytes there are is in the value already and is a different number,
    and no arithmetic on it answers this one.  What the walk needs of each
    character is only how long it was, so the leading byte is all it reads --
    which is what makes this cheaper than the walk a `foreach` does, that one
    having to build the code point as well.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    bytes_ = entry.add_param(bytes_type(module), "bytes")
    length = entry.add_param(U64, "length")
    header = builder.new_block("counting")
    body = builder.new_block("count")
    done = builder.new_block("counted")
    builder.br(header, (builder.int_const(U64, 0), builder.int_const(U64, 0)))
    builder.position_at(header)
    at = header.add_param(U64, "at")
    found = header.add_param(U64, "found")
    builder.condbr(builder.compare(CmpPred.ULT, at, length), body, done)
    builder.position_at(body)
    # Every byte that is not a continuation byte begins a character, and a
    # continuation byte is exactly one whose top two bits are `10`.  So the
    # count is the number of bytes that are not those, which needs neither the
    # length of each sequence nor the code point it stands for.
    leading = _byte_at(builder, bytes_, at)
    begins = builder.compare(
        CmpPred.NE,
        builder.binary(BinOp.AND, leading, builder.int_const(U64, 0xC0)),
        builder.int_const(U64, 0x80))
    builder.br(header, (builder.binary(BinOp.WRAP_ADD, at,
                                       builder.int_const(U64, 1)),
                        builder.binary(BinOp.WRAP_ADD, found,
                                       builder.cast(CastKind.ZEXT, begins, U64))))
    builder.position_at(done)
    builder.ret(found)


def _byte_at(builder: IRBuilder, bytes_: Value, at: Value) -> Value:
    """The byte at an offset, widened into a word so that arithmetic reaches it."""
    place = builder.binary(BinOp.ADD, bytes_, at)
    return builder.cast(CastKind.ZEXT, builder.load(place), U64)


def _build_next(module: Module, func: Function) -> None:
    """Build the one that takes the character at a place.

    The leading byte decides everything: how many bytes the sequence has, and
    which of its own bits belong to the code point.  Each following byte carries
    six more, so the answer is built by shifting what there is up by six and
    folding the next six in -- once, twice or three times, which is three
    branches and no loop, there being no fourth.

    Nothing is checked.  The bytes are well-formed by construction, so a leading
    byte is a leading byte, a continuation byte follows it, and the sequence does
    not run past the end -- and a decoder that asked would be asking a question
    whose answer is a property of the type.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    bytes_ = entry.add_param(bytes_type(module), "bytes")
    at = entry.add_param(U64, "at")
    leading = _byte_at(builder, bytes_, at)
    answered = builder.new_block("taken")
    answer = answered.add_param(U64, "point")
    length = answered.add_param(U64, "length")
    for limit, bits, count in _LEADING:
        if count == len(_LEADING):
            # The last row takes whatever is left, so there is nothing to ask.
            _fold(builder, bytes_, at, leading, bits, count, answered)
            break
        shorter = builder.new_block("shorter")
        longer = builder.new_block("longer")
        builder.condbr(
            builder.compare(CmpPred.ULT, leading, builder.int_const(U64, limit)),
            shorter, longer)
        builder.position_at(shorter)
        _fold(builder, bytes_, at, leading, bits, count, answered)
        builder.position_at(longer)
    builder.position_at(answered)
    builder.ret(builder.make_tuple(
        (builder.cast(CastKind.BITCAST,
                      builder.cast(CastKind.TRUNC, answer, U32), CHAR),
         builder.binary(BinOp.WRAP_ADD, at, length)),
        taken_type(module)))


def _fold(builder: IRBuilder, bytes_: Value, at: Value, leading: Value,
          bits: int, count: int, answered: object) -> None:
    """Build the code point out of a sequence of a known length and hand it on."""
    found = builder.binary(BinOp.AND, leading,
                           builder.int_const(U64, (1 << bits) - 1))
    for step in range(1, count):
        following = _byte_at(builder, bytes_,
                             builder.binary(BinOp.WRAP_ADD, at,
                                            builder.int_const(U64, step)))
        found = builder.binary(
            BinOp.OR,
            builder.binary(BinOp.WRAP_SHL, found, builder.int_const(U64, 6)),
            builder.binary(BinOp.AND, following, builder.int_const(U64, 0x3F)))
    builder.br(answered, (found, builder.int_const(U64, count)))  # type: ignore[arg-type]


def _build_join(module: Module, func: Function) -> None:
    """Build the one that puts two strings' bytes end to end.

    Two loops and no cleverness: this is where a copy of a length nobody knows
    while compiling is done, and the run-at-a-time machinery that makes joining
    two arrays one instruction per register needs a length that is written down.
    A to-do line records that.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(module.types.ptr_type(ARENA, mutable=True), "arena")
    first = entry.add_param(bytes_type(module), "first")
    first_len = entry.add_param(U64, "first.length")
    second = entry.add_param(bytes_type(module), "second")
    second_len = entry.add_param(U64, "second.length")
    alloc = _allocator(module)
    room = builder.binary(BinOp.WRAP_ADD, first_len, second_len)
    into = builder.call(alloc, (arena, room), bytes_type(module))
    _copy(builder, into, builder.int_const(U64, 0), first, first_len)
    _copy(builder, into, first_len, second, second_len)
    builder.ret(into)


def _allocator(module: Module) -> Function:
    """The allocator, declared the way the table runtime declares it."""
    from .tables import _declared  # noqa: PLC0415 -- one declaration, one place

    return _declared(module, ALLOC_SYMBOL,
                     (module.types.ptr_type(ARENA, mutable=True), U64),
                     bytes_type(module))


def _copy(builder: IRBuilder, into: Value, offset: Value, source: Value,
          count: Value) -> None:
    """Copy *count* bytes, one at a time, into the place *offset* bytes along."""
    header = builder.new_block("copy")
    body = builder.new_block("copying")
    done = builder.new_block("copied")
    builder.br(header, (builder.int_const(U64, 0), builder.memory()))
    builder.position_at(header)
    at = header.add_param(U64, "at")
    token = header.add_param(MEM, "mem")
    builder.set_memory(token)
    builder.condbr(builder.compare(CmpPred.ULT, at, count), body, done)
    builder.position_at(body)
    builder.store(builder.binary(BinOp.ADD, into,
                                 builder.binary(BinOp.WRAP_ADD, offset, at)),
                  builder.load(builder.binary(BinOp.ADD, source, at)))
    builder.br(header, (builder.binary(BinOp.WRAP_ADD, at,
                                       builder.int_const(U64, 1)),
                        builder.memory()))
    builder.position_at(done)
    # What holds after the loop is what the loop carried, which is the header's
    # own parameter -- the token the body left behind is the body's, and the
    # body is not on every way here.
    builder.set_memory(token)
