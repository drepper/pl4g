"""Text, written in the representation.

A `str` is where its bytes are and how many there are, and the bytes are always
well-formed UTF-8.  That last is an invariant rather than a hope, and it is what
makes everything here short: every way to make a string makes only that.  A
literal's bytes are what the source held, encoded by the compiler.  A join puts
well-formed bytes after well-formed bytes, and `⎕str` encodes one code point.
`⎕text` takes bytes from anywhere, and is the one place that asks: the check
below.  So a walk over the characters is a decoder and not a validator -- which
is where nearly all of the cost of walking text usually goes -- and nothing that
reads a string has to ask whether it is one.

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
from ..ir.types import ARENA, CHAR, I64, MEM, VOID, PtrType, Type, U8, U32, U64
from ..ir.value import Value
from ..source.location import INVALID_SPAN, Span

#: What each of the two is called.  The names carry the compiler's own prefix
#: for the same reason every generated symbol does: nothing a program can write
#: collides with them.
NEXT_SYMBOL: Final[str] = "__pl4g_str_next"
JOIN_SYMBOL: Final[str] = "__pl4g_str_join"
LENGTH_SYMBOL: Final[str] = "__pl4g_str_length"
COMPARE_SYMBOL: Final[str] = "__pl4g_str_compare"
CHAR_SYMBOL: Final[str] = "__pl4g_str_of_char"
BYTE_COUNT_SYMBOL: Final[str] = "__pl4g_str_char_bytes"
C_STRING_SYMBOL: Final[str] = "__pl4g_str_c"

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


def join_function(module: Module, heap: bool = False) -> Function:
    """The one that puts two strings' bytes end to end, built on first ask.

    Two of them: one taking the allocator to take room from, and one for `⎕heap`,
    which is the allocator a join names where nothing says another and which is
    then called straight away rather than through the dispatch.
    """
    arena = module.types.ptr_type(ARENA, mutable=True)
    pieces = (bytes_type(module), U64, bytes_type(module), U64)
    func, fresh = _generated(
        module, ".".join((JOIN_SYMBOL, "heap")) if heap else JOIN_SYMBOL,
        pieces if heap else (arena, *pieces), bytes_type(module), impure=True)
    if fresh:
        _build_join(module, func, heap)
    return func


def joined(builder: IRBuilder, arena: Value, pieces: tuple[Value, ...],
           answer: Type, span=None) -> Value:
    """Call the join: the one for `⎕heap` where *arena* is known to be the heap."""
    from .tables import is_heap  # noqa: PLC0415 -- one place
    from ..source.location import INVALID_SPAN  # noqa: PLC0415
    where = INVALID_SPAN if span is None else span
    if is_heap(arena):
        return builder.call(join_function(builder.module, heap=True), pieces,
                            answer, where)
    return builder.call(join_function(builder.module), (arena, *pieces), answer,
                        where)


def c_string_function(module: Module) -> Function:
    """The one that makes C's text of a string, built on first ask.

    C means by text the address of bytes ending in a nul, which a `str` is not:
    it holds how many bytes it has beside them, and has no nul.  So this makes a
    copy in the heap one byte longer and puts the nul there, and whoever asked
    for it gives it back.
    """
    func, fresh = _generated(module, C_STRING_SYMBOL, (bytes_type(module), U64),
                             bytes_type(module), impure=True)
    if not fresh:
        return func
    from .tables import heap_new_function  # noqa: PLC0415 -- one place
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    text = entry.add_param(bytes_type(module), "text")
    length = entry.add_param(U64, "text.length")
    into = builder.call(heap_new_function(module),
                        (builder.binary(BinOp.WRAP_ADD, length,
                                        builder.int_const(U64, 1)),),
                        bytes_type(module))
    copy_bytes(builder, into, builder.int_const(U64, 0), text, length)
    builder.store(builder.binary(BinOp.ADD, into, length), builder.int_const(U8, 0))
    builder.ret(into)
    return func


def char_function(module: Module) -> Function:
    """The one that makes a string of one character, built on first ask.

    It answers where the bytes are; how many there are is worked out beside the
    call, the two together being what a `str` is.
    """
    arena = module.types.ptr_type(ARENA, mutable=True)
    func, fresh = _generated(module, CHAR_SYMBOL, (arena, CHAR),
                             bytes_type(module), impure=True)
    if fresh:
        _build_char(module, func)
    return func


def _build_char(module: Module, func: Function) -> None:
    """Build the one that encodes a code point as the bytes of a string.

    UTF-8, written out: the four lengths and nothing clever.  It is here and not
    in the runtime because it allocates out of an arena, which is the compiler's
    to name, and because what it answers has to be a `str` -- whose bytes being
    well formed is an invariant, and encoding them here is what keeps it one by
    construction rather than by inspection.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(module.types.ptr_type(ARENA, mutable=True), "arena")
    # The character itself and not its number: a cast at the call would be a cast
    # of whatever the caller had, and a character written down is a constant -- which
    # a bitcast has nowhere to read from, emitting no instruction of its own.  Here
    # the operand is a parameter and never a constant.
    code = entry.add_param(CHAR, "code")
    wide = builder.cast(CastKind.ZEXT,
                        builder.cast(CastKind.BITCAST, code, U32), U64)
    into = builder.call(_allocator(module),
                        (arena, builder.int_const(U64, 4)), bytes_type(module))
    # The memory as the entry block leaves it.  Each arm below writes from here,
    # which is what dominates all four of them: the token one arm leaves behind is
    # that arm's and no other arm is reached through it.
    start = builder.memory()
    # Which of the four lengths it is, asked largest first so that each test is
    # about one boundary.
    one = builder.new_block("one")
    two = builder.new_block("two")
    three = builder.new_block("three")
    four = builder.new_block("four")
    ask_two = builder.new_block("ask.two")
    ask_three = builder.new_block("ask.three")
    builder.condbr(builder.compare(CmpPred.ULT, wide,
                                   builder.int_const(U64, 0x80)), one, ask_two)
    builder.position_at(ask_two)
    builder.condbr(builder.compare(CmpPred.ULT, wide,
                                   builder.int_const(U64, 0x800)), two,
                   ask_three)
    builder.position_at(ask_three)
    builder.condbr(builder.compare(CmpPred.ULT, wide,
                                   builder.int_const(U64, 0x10000)), three, four)
    for block, lengths in ((one, ((0, 0x00, 0),),),
                           (two, ((0, 0xc0, 6), (1, 0x80, 0))),
                           (three, ((0, 0xe0, 12), (1, 0x80, 6), (2, 0x80, 0))),
                           (four, ((0, 0xf0, 18), (1, 0x80, 12), (2, 0x80, 6),
                                   (3, 0x80, 0)))):
        builder.position_at(block)
        builder.set_memory(start)
        for at, mark, shift in lengths:
            part = builder.binary(BinOp.LSHR, wide,
                                  builder.int_const(U64, shift))
            kept = builder.binary(BinOp.AND, part, builder.int_const(
                U64, 0x7f if mark == 0x00 else 0x3f if mark == 0x80
                else 0x1f if mark == 0xc0 else 0x0f if mark == 0xe0 else 0x07))
            byte = builder.binary(BinOp.OR, kept,
                                  builder.int_const(U64, mark))
            place = builder.binary(BinOp.ADD, into,
                                   builder.int_const(U64, at))
            builder.store(place, builder.cast(CastKind.TRUNC, byte, U8))
        builder.ret(into)


def length_in_bytes(module: Module) -> Function:
    """How many bytes a code point takes, built on first ask.

    Beside `char_function` rather than inside it because what a `str` is, is two
    values and a call answers one: the bytes come from the one and the count from
    this, and the two are put together where the call is.
    """
    func, fresh = _generated(module, BYTE_COUNT_SYMBOL, (CHAR,), U64, impure=False)
    if fresh:
        entry = func.add_block()
        builder = IRBuilder(module, func)
        builder.position_at(entry)
        code = entry.add_param(CHAR, "code")
        wide = builder.cast(CastKind.ZEXT,
                            builder.cast(CastKind.BITCAST, code, U32), U64)
        answers = [builder.new_block("".join(("bytes.", str(n))))
                   for n in (1, 2, 3, 4)]
        asks = [builder.new_block("".join(("ask.", str(n)))) for n in (2, 3)]
        for at, (bound, taken) in enumerate(((0x80, answers[0]),
                                             (0x800, answers[1]),
                                             (0x10000, answers[2]))):
            builder.condbr(
                builder.compare(CmpPred.ULT, wide, builder.int_const(U64, bound)),
                taken, asks[at] if at < len(asks) else answers[3])
            if at < len(asks):
                builder.position_at(asks[at])
        for n, block in enumerate(answers, start=1):
            builder.position_at(block)
            builder.ret(builder.int_const(U64, n))
    return func


def length_function(module: Module) -> Function:
    """The one that counts the characters of a string, built on first ask."""
    func, fresh = _generated(
        module, LENGTH_SYMBOL, (bytes_type(module), U64), U64, impure=False)
    if fresh:
        _build_length(module, func)
    return func


def compare_function(module: Module) -> Function:
    """The one that says which of two strings comes first, built on first ask."""
    func, fresh = _generated(
        module, COMPARE_SYMBOL,
        (bytes_type(module), U64, bytes_type(module), U64), I64, impure=False)
    if fresh:
        _build_compare(module, func)
    return func


def _build_compare(module: Module, func: Function) -> None:
    """Build the one that says which of two strings comes first.

    **Nothing is decoded.**  UTF-8 was designed so that comparing the bytes of
    two strings gives the same answer as comparing the code points they stand
    for -- a longer sequence begins with a higher leading byte than any shorter
    one, and within a length the bits of the code point go in in order.  So the
    comparison is a walk of bytes, which is what makes it the same loop C's
    `memcmp` is and not the one `foreach` is.

    Two strings that agree as far as the shorter one goes are ordered by their
    lengths, which is what makes `"ab"` come before `"abc"` -- the shorter is a
    prefix, and a prefix comes first.  Every ordering of strings anyone uses
    says that, and it is the only answer that makes the order a total one.

    What comes back is negative, zero or positive, and nothing about how far
    from zero it is means anything.  The byte arm answers with the difference
    because the two are already in registers; the length arm cannot, two lengths
    being able to differ by more than a signed number holds, so it answers with
    the two comparisons subtracted from each other, which is branch-free and
    exactly as informative.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    first = entry.add_param(bytes_type(module), "first")
    first_len = entry.add_param(U64, "first.length")
    second = entry.add_param(bytes_type(module), "second")
    second_len = entry.add_param(U64, "second.length")
    # As far as both go, which is where the bytes can be compared at all.
    limit = builder.binary(BinOp.UMIN, first_len, second_len)
    header = builder.new_block("comparing")
    body = builder.new_block("compare")
    differ = builder.new_block("differ")
    ended = builder.new_block("alike")
    builder.br(header, (builder.int_const(U64, 0),))
    builder.position_at(header)
    at = header.add_param(U64, "at")
    builder.condbr(builder.compare(CmpPred.ULT, at, limit), body, ended)
    builder.position_at(body)
    here = _signed_byte_at(builder, first, at)
    there = _signed_byte_at(builder, second, at)
    again = builder.new_block("next")
    builder.condbr(builder.compare(CmpPred.EQ, here, there), again, differ)
    builder.position_at(again)
    builder.br(header, (builder.binary(BinOp.WRAP_ADD, at,
                                       builder.int_const(U64, 1)),))
    builder.position_at(differ)
    builder.ret(builder.binary(BinOp.WRAP_SUB, here, there))
    builder.position_at(ended)
    builder.ret(_which_way(builder, first_len, second_len))


def _which_way(builder: IRBuilder, left: Value, right: Value) -> Value:
    """Which of two counts is the greater, as a negative, zero or positive
    number, without a branch and without a subtraction that could go past."""
    return builder.binary(
        BinOp.WRAP_SUB,
        builder.cast(CastKind.ZEXT,
                     builder.compare(CmpPred.UGT, left, right), I64),
        builder.cast(CastKind.ZEXT,
                     builder.compare(CmpPred.ULT, left, right), I64))


def _signed_byte_at(builder: IRBuilder, bytes_: Value, at: Value) -> Value:
    """The byte at an offset, widened into a signed word so that the difference
    of two of them is the answer the comparison wants."""
    place = builder.binary(BinOp.ADD, bytes_, at)
    return builder.cast(CastKind.ZEXT, builder.load(place), I64)


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


def _build_join(module: Module, func: Function, heap: bool = False) -> None:
    """Build the one that puts two strings' bytes end to end.

    Two loops and no cleverness: this is where a copy of a length nobody knows
    while compiling is done, and the run-at-a-time machinery that makes joining
    two arrays one instruction per register needs a length that is written down.
    A to-do line records that.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = None if heap else entry.add_param(
        module.types.ptr_type(ARENA, mutable=True), "arena")
    first = entry.add_param(bytes_type(module), "first")
    first_len = entry.add_param(U64, "first.length")
    second = entry.add_param(bytes_type(module), "second")
    second_len = entry.add_param(U64, "second.length")
    room = builder.binary(BinOp.WRAP_ADD, first_len, second_len)
    if arena is None:
        from .tables import heap_new_function  # noqa: PLC0415 -- one place
        into = builder.call(heap_new_function(module), (room,), bytes_type(module))
    else:
        into = builder.call(_allocator(module), (arena, room), bytes_type(module))
    copy_bytes(builder, into, builder.int_const(U64, 0), first, first_len)
    copy_bytes(builder, into, first_len, second, second_len)
    builder.ret(into)


def _allocator(module: Module) -> Function:
    """The allocator: whichever the arena handed over names."""
    from .tables import allocator_function  # noqa: PLC0415 -- one place

    return allocator_function(module)


def copy_bytes(builder: IRBuilder, into: Value, offset: Value, source: Value,
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


#: The two that let text in from bytes: one asking whether bytes are UTF-8, and
#: one moving a run of bytes a word at a time.
VALID_SYMBOL: Final[str] = "__pl4g_str_valid"
MOVE_SYMBOL: Final[str] = "__pl4g_bytes_move"

#: The top bit of every byte of a word.  A word of bytes none of which has it set
#: is eight characters of ASCII, which is what makes the common case one load, one
#: `and` and one branch per eight bytes.
_HIGH_BITS: Final[int] = 0x8080_8080_8080_8080

#: How many bytes a word holds, which is how far the fast paths go at a time.
_WORD: Final[int] = 8


def valid_function(module: Module) -> Function:
    """``__pl4g_str_valid(bytes, length)``: how far *bytes* are well-formed
    UTF-8 -- *length* where all of them are, and otherwise where the first
    sequence that is not begins.  Built on first ask."""
    func, fresh = _generated(module, VALID_SYMBOL, (bytes_type(module), U64),
                             U64, impure=False)
    if fresh:
        _build_valid(module, func)
    return func


def move_function(module: Module) -> Function:
    """``__pl4g_bytes_move(into, source, count)``: *count* bytes copied, a
    word at a time and the few left over one at a time.  Built on first ask."""
    func, fresh = _generated(
        module, MOVE_SYMBOL, (bytes_type(module), bytes_type(module), U64), VOID,
        impure=True)
    if fresh:
        _build_move(module, func)
    return func


def moved(builder: IRBuilder, into: Value, source: Value, count: Value,
          span: Span = INVALID_SPAN) -> None:
    """Call the move: *count* bytes from *source* to *into*, which do not
    overlap."""
    builder.call(move_function(builder.module), (into, source, count), VOID,
                 span)


def _build_move(module: Module, func: Function) -> None:
    """Build the one that moves a run of bytes.

    **A word at a time**, which is what C's `memcpy` does for a length nobody
    knew while compiling, and the rest one byte at a time.  The words are read
    and written where they are, whatever their alignment: every one of the three
    machines reads a word that straddles two as one instruction -- RISC-V's
    kernel finishing it where the hardware does not -- and lining the two up
    first would cost a loop of its own whenever source and destination are not
    aligned alike, which for bytes going into fresh room is nearly always.

    The two runs do not overlap.  Every caller copies into room it has just made,
    or into the spare room after a list's elements, which nothing reads from.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    into = entry.add_param(bytes_type(module), "into")
    source = entry.add_param(bytes_type(module), "source")
    count = entry.add_param(U64, "count")
    word_ptr = module.types.ptr_type(U64, mutable=True)
    words = builder.new_block("words")
    word = builder.new_block("word")
    tail = builder.new_block("tail")
    byte = builder.new_block("byte")
    done = builder.new_block("moved")
    builder.br(words, (builder.int_const(U64, 0), builder.memory()))
    builder.position_at(words)
    at = words.add_param(U64, "at")
    token = words.add_param(MEM, "mem")
    builder.set_memory(token)
    ahead = builder.binary(BinOp.WRAP_ADD, at, builder.int_const(U64, _WORD))
    builder.condbr(builder.compare(CmpPred.ULE, ahead, count), word, tail,
                   false_args=(at, token))
    builder.position_at(word)
    builder.store(
        builder.cast(CastKind.BITCAST, builder.binary(BinOp.ADD, into, at),
                     word_ptr),
        builder.load(builder.cast(CastKind.BITCAST,
                                  builder.binary(BinOp.ADD, source, at),
                                  word_ptr)))
    builder.br(words, (ahead, builder.memory()))
    builder.position_at(tail)
    left = tail.add_param(U64, "at")
    left_token = tail.add_param(MEM, "mem")
    builder.set_memory(left_token)
    builder.condbr(builder.compare(CmpPred.ULT, left, count), byte, done)
    builder.position_at(byte)
    builder.store(builder.binary(BinOp.ADD, into, left),
                  builder.load(builder.binary(BinOp.ADD, source, left)))
    builder.br(tail, (builder.binary(BinOp.WRAP_ADD, left,
                                     builder.int_const(U64, 1)),
                      builder.memory()))
    builder.position_at(done)
    builder.set_memory(left_token)
    builder.ret()


def _build_valid(module: Module, func: Function) -> None:
    """Build the one that asks whether bytes are well-formed UTF-8.

    **Eight bytes at a time while they are ASCII**, which is what most text is
    most of the way through: a word is read, and where none of its bytes has its
    top bit set all eight are characters and the next word is read.  A word
    that has one is gone through a sequence at a time up to its end, and then the
    words begin again from wherever the last sequence ended -- so a word is never
    read twice for the sake of one byte in it, and a sequence that straddles the
    end of a word is checked whole.  Fewer than eight left at the end are gone
    through a sequence at a time.

    **A sequence is checked as Unicode's table of well-formed byte sequences
    has it** (Table 3-7), which is the whole of what makes UTF-8 well formed:
    a leading byte from `C2` to `F4` -- `80` to `BF` are continuation bytes, `C0`
    and `C1` could only begin an overlong two-byte form, and above `F4` only
    numbers above `10FFFF` -- followed by as many continuation bytes as it says,
    the first of which is narrowed for four leading bytes: after `E0` it is from
    `A0` (anything less is an overlong three-byte form), after `ED` up to `9F`
    (anything more is a surrogate), after `F0` from `90` (an overlong four-byte
    form), after `F4` up to `8F` (above `10FFFF`).  One comparison of the
    difference against the width of the range asks both ends at once.

    What it answers is where the first sequence that is not well formed begins,
    which is the first byte that is not part of one, or *length* where every
    byte is: a sequence cut short by the end of the bytes is as ill formed as one
    cut short by anything else.
    """
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    bytes_ = entry.add_param(bytes_type(module), "bytes")
    length = entry.add_param(U64, "length")
    word_ptr = module.types.ptr_type(U64, mutable=True)

    def const(value: int) -> Value:
        return builder.int_const(U64, value)

    words = builder.new_block("words")
    word = builder.new_block("word")
    last = builder.new_block("last")
    sequence = builder.new_block("sequence")
    lead = builder.new_block("lead")
    bad = builder.new_block("bad")
    good = builder.new_block("good")
    builder.br(words, (const(0),))

    # The words: eight bytes at a time while there are eight, and the sequences
    # up to the end of a word that is not all ASCII.
    builder.position_at(words)
    at = words.add_param(U64, "at")
    ahead = builder.binary(BinOp.WRAP_ADD, at, const(_WORD))
    builder.condbr(builder.compare(CmpPred.ULE, ahead, length), word, last)
    builder.position_at(word)
    read = builder.load(builder.cast(CastKind.BITCAST,
                                     builder.binary(BinOp.ADD, bytes_, at),
                                     word_ptr))
    builder.condbr(
        builder.compare(CmpPred.EQ,
                        builder.binary(BinOp.AND, read, const(_HIGH_BITS)),
                        const(0)),
        words, sequence, true_args=(ahead,), false_args=(at, ahead))
    builder.position_at(last)
    builder.condbr(builder.compare(CmpPred.ULT, at, length), sequence, good,
                   true_args=(at, length))

    # One sequence at a time, up to *until*: then the words again.
    builder.position_at(sequence)
    here = sequence.add_param(U64, "at")
    until = sequence.add_param(U64, "until")
    builder.condbr(builder.compare(CmpPred.ULT, here, until), lead, words,
                   false_args=(here,))
    builder.position_at(lead)
    first = _byte_at(builder, bytes_, here)
    ascii = builder.new_block("ascii")
    above = builder.new_block("above")
    starts = builder.new_block("starts")
    over_two = builder.new_block("over.two")
    over_three = builder.new_block("over.three")
    lengths = {count: builder.new_block("".join(("bytes.", str(count))))
               for count in (2, 3, 4)}
    builder.condbr(builder.compare(CmpPred.ULT, first, const(0x80)), ascii, above)
    builder.position_at(ascii)
    builder.br(sequence, (builder.binary(BinOp.WRAP_ADD, here, const(1)), until))
    # Below `C2` is a continuation byte, or the start of an overlong form.
    builder.position_at(above)
    builder.condbr(builder.compare(CmpPred.ULT, first, const(0xC2)), bad, starts)
    builder.position_at(starts)
    builder.condbr(builder.compare(CmpPred.ULT, first, const(0xE0)), lengths[2],
                   over_two)
    builder.position_at(over_two)
    builder.condbr(builder.compare(CmpPred.ULT, first, const(0xF0)), lengths[3],
                   over_three)
    builder.position_at(over_three)
    builder.condbr(builder.compare(CmpPred.ULT, first, const(0xF5)), lengths[4],
                   bad)
    for count, block in lengths.items():
        builder.position_at(block)
        # The whole sequence has to be there before any of it is read.
        whole = builder.new_block("".join(("whole.", str(count))))
        builder.condbr(
            builder.compare(CmpPred.ULT,
                            builder.binary(BinOp.WRAP_ADD, here, const(count - 1)),
                            length),
            whole, bad)
        builder.position_at(whole)
        low, width = _first_following(builder, first, count)
        for step in range(1, count):
            following = _byte_at(builder, bytes_,
                                 builder.binary(BinOp.WRAP_ADD, here, const(step)))
            then = builder.new_block("".join(("following.", str(count), ".",
                                              str(step))))
            builder.condbr(
                builder.compare(
                    CmpPred.ULE,
                    builder.binary(BinOp.WRAP_SUB, following,
                                   low if step == 1 else const(0x80)),
                    width if step == 1 else const(0x3F)),
                then, bad)
            builder.position_at(then)
        builder.br(sequence, (builder.binary(BinOp.WRAP_ADD, here, const(count)),
                              until))
    builder.position_at(bad)
    builder.ret(here)
    builder.position_at(good)
    builder.ret(length)


def _first_following(builder: IRBuilder, first: Value,
                     count: int) -> tuple[Value, Value]:
    """Where the range the byte after *first* has to be in begins, and how wide
    it is, for a sequence of *count* bytes.

    Any continuation byte after a leading byte of two; after one of three, from
    `A0` after `E0` and up to `9F` after `ED`; after one of four, from `90`
    after `F0` and up to `8F` after `F4`.  Chosen without a branch, the two
    leading bytes that narrow it being compared and the answer selected.
    """
    def const(value: int) -> Value:
        return builder.int_const(U64, value)

    if count == 2:
        return const(0x80), const(0x3F)
    lowest, highest = (0xE0, 0xED) if count == 3 else (0xF0, 0xF4)
    low = builder.select(builder.compare(CmpPred.EQ, first, const(lowest)),
                         const(0xA0 if count == 3 else 0x90), const(0x80))
    high = builder.select(builder.compare(CmpPred.EQ, first, const(highest)),
                          const(0x9F if count == 3 else 0x8F), const(0xBF))
    return low, builder.binary(BinOp.WRAP_SUB, high, low)
