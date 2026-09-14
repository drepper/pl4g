"""The hash table a set and a dictionary are, written in the representation.

A collection is a *table*: a block of memory somewhere in an arena, holding how
much room it has and the entries themselves.  The value a program passes around
is where that block is and nothing else, so two names for one collection see one
answer.

**The table is open-addressed with linear probing.**  Every entry is in the
block rather than in a chain hanging off it, so a lookup touches one cache line
where a chain would touch one per link, and a probe that runs on walks forward
rather than following a pointer.  That is what the specification's O(1) means in
practice, and it is what Python, Rust, Go, Swift and every recent implementation
do; separate chaining is what the older ones did and what they moved away from.

**The three it needs are generated as functions of the representation** rather
than written as assembly for each target.  Nothing in the compiler did that
before -- the entry point and the allocator are written in instructions -- and
the reason to do it here is that these have loops, several live values, and
arithmetic that wants a register allocator: exactly the things the compiler
already does for a program.  They are generated once per module, and a program
that has no collection has none of them.

**Every key and every value is one word.**  What may be a key is an integer, a
truth value or an enumeration, and all of them fit; what may be a value is the
same, which is a restriction the to-do list records rather than a decision.
That is what lets one table serve every instantiation -- there is no code per
key type at all, and the hash is one multiplication.

**The hash is Fibonacci hashing**: the key multiplied by the closest odd number
to two to the sixty-fourth over the golden ratio, with the high bits folded down
into the low ones.  The multiplication wraps, which no program of the language
may write and which this is: a hash is defined on the bits, and there is nothing
about an overflow here to report to anyone.
"""

from typing import Final

from ..ir.builder import IRBuilder
from ..ir.function import SYSTEM_CCONV, FuncAttrs, Function, Linkage
from ..ir.inst import BinOp, CastKind, CmpPred
from ..ir.module import Module
from ..ir.types import ARENA, MEM, PtrType, Type, U8, U64, VOID
from ..ir.value import Value

#: What the allocator is called, and what it takes.  Declared here rather than
#: reached for: the backend supplies the body, and what says it is wanted is
#: that a module declares it.
ALLOC_SYMBOL: Final[str] = "__pl4g_alloc"

#: How wide a word is.  Every field of a table, every key and every value is
#: one, which is what lets one table serve every instantiation.
WORD: Final[int] = 8

#: Where the fields of a table block are.  `mask` is the capacity less one, so
#: that an index is a bitwise and.  The entries are a separate allocation rather
#: than part of the block, so that the block never moves: what a program passes
#: around is where the block is, and a table that grew under it would otherwise
#: leave every name for it pointing at the old entries.
ARENA_FIELD: Final[int] = 0
MASK_FIELD: Final[int] = WORD
COUNT_FIELD: Final[int] = 2 * WORD
STRIDE_FIELD: Final[int] = 3 * WORD
ENTRIES_FIELD: Final[int] = 4 * WORD

#: How much room a block takes, on the grain the allocator hands out.
BLOCK_SIZE: Final[int] = 6 * WORD

#: The fields of one entry.  A set's entries are two words and a dictionary's
#: are three; which it is, is the stride the table was made with.
STATE_AT: Final[int] = 0
KEY_AT: Final[int] = WORD
VALUE_AT: Final[int] = 2 * WORD

SET_STRIDE: Final[int] = 2 * WORD
DICT_STRIDE: Final[int] = 3 * WORD

#: What the first word of an entry says about it.  There are two states and not
#: three: nothing takes an entry out of a table yet, so no entry is ever given
#: up, and a probe therefore stops at the first empty one.  The to-do list says
#: what taking one out would need.
EMPTY: Final[int] = 0
LIVE: Final[int] = 1

#: How many entries a table has to begin with.  A power of two, because the
#: index is the hash masked; eight is enough that a collection written down with
#: a handful of entries never grows while it is being built.
FIRST_CAPACITY: Final[int] = 8

#: How full it gets before it grows, as a fraction: three of every four.  A
#: linear probe lengthens sharply past that, and doubling before it does is what
#: keeps the expected cost constant.
LOAD_NUMERATOR: Final[int] = 3
LOAD_DENOMINATOR: Final[int] = 4

#: The closest odd number to two to the sixty-fourth divided by the golden
#: ratio, which is the multiplier Knuth describes and every implementation of
#: this hash uses.
GOLDEN: Final[int] = 0x9E3779B97F4A7C15

#: What the three are called.  They are the compiler's and a program cannot
#: name them, so the names carry the prefix everything of the compiler's does.
NEW_SYMBOL: Final[str] = "__pl4g_table_new"
SLOT_SYMBOL: Final[str] = "__pl4g_table_slot"
PUT_SYMBOL: Final[str] = "__pl4g_table_put"


def _declared(module: Module, name: str, params: tuple[Type, ...],
              result: Type) -> Function:
    """The declaration of a function the backend supplies, made once.

    It follows the system's convention, because it is written as instructions
    rather than lowered: hand-written code names its registers outright, so it
    has one settled convention rather than whatever the compiler would have
    chosen for it.
    """
    found = module.functions.get(name)
    if isinstance(found, Function):
        return found
    return module.add_function(Function(
        name, module.types.func_type(params, result),
        FuncAttrs(abi=SYSTEM_CCONV), cconv=SYSTEM_CCONV,
        linkage=Linkage.VISIBLE))


def _generated(module: Module, name: str, params: tuple[Type, ...],
               result: Type) -> "tuple[Function, bool]":
    """A function of this module's own, and whether it has yet to be built."""
    found = module.functions.get(name)
    if isinstance(found, Function):
        return found, False
    func = module.add_function(Function(
        name, module.types.func_type(params, result),
        FuncAttrs(abi="pl4g.runtime"), linkage=Linkage.INTERNAL))
    return func, True


def arena_of(builder: IRBuilder, table: Value) -> Value:
    """Which arena a table came out of, which is a field of the table itself.

    A collection made out of two others comes out of the same arena the first of
    them did, and the only place that is written down is the table.
    """
    return _read_address(builder, table, ARENA_FIELD, ARENA)


def table_type(module: Module) -> PtrType:
    """What a table is reached by: a pointer to its first word."""
    return module.types.ptr_type(U64, mutable=True)


def ensure_runtime(module: Module) -> None:
    """Build the three a collection needs, once for the whole module."""
    _build_new(module)
    _build_slot(module)
    _build_put(module)
    _build_select(module)
    _build_next(module)


# -- the pieces every one of them uses -------------------------------------------

def _field(builder: IRBuilder, table: Value, offset: int) -> Value:
    """The address of one field of a table block."""
    if offset == 0:
        return table
    return builder.binary(BinOp.ADD, table, builder.int_const(U64, offset))


def _read(builder: IRBuilder, table: Value, offset: int) -> Value:
    """One field of a table block."""
    return builder.load(_field(builder, table, offset))


def _write(builder: IRBuilder, table: Value, offset: int, value: Value) -> None:
    """Put something in one field of a table block."""
    builder.store(_field(builder, table, offset), value)


def _hashed(builder: IRBuilder, key: Value, mask: Value) -> Value:
    """Where a probe for *key* starts.

    Fibonacci hashing puts what the key says into the high bits of the product,
    and the fold brings them down where the mask can read them.  Without the
    fold the low bits of the product would be the low bits of the key, and a
    table of keys that are all multiples of its capacity would put every one of
    them in one place.
    """
    mixed = builder.binary(BinOp.WRAP_MUL, key, builder.int_const(U64, GOLDEN))
    folded = builder.binary(BinOp.LSHR, mixed, builder.int_const(U64, 32))
    return builder.binary(BinOp.AND,
                          builder.binary(BinOp.XOR, mixed, folded), mask)


def _as(builder: IRBuilder, address: Value, pointee: Type) -> Value:
    """The same address, read as pointing at something else.

    Every field of a table is a word wide, and two of them hold addresses; this
    is what says so where one is read or written, since the bits are the same
    either way.
    """
    return builder.cast(CastKind.BITCAST, address,
                        builder.module.types.ptr_type(pointee, mutable=True))


def _read_address(builder: IRBuilder, table: Value, offset: int,
                  pointee: Type) -> Value:
    """One field of a table block, which holds an address."""
    return builder.load(_as(builder, _field(builder, table, offset),
                            builder.module.types.ptr_type(pointee, mutable=True)))


def _write_address(builder: IRBuilder, table: Value, offset: int,
                   value: Value) -> None:
    """Put an address in one field of a table block."""
    builder.store(_as(builder, _field(builder, table, offset), value.ty), value)


def _entry(builder: IRBuilder, entries: Value, index: Value,
           stride: Value) -> Value:
    """The address of the entry at *index*."""
    return builder.binary(BinOp.ADD, entries,
                          builder.binary(BinOp.WRAP_MUL, index, stride))


def _stepped(builder: IRBuilder, index: Value, mask: Value) -> Value:
    """The next place a probe looks, which is the one after and round again."""
    return builder.binary(BinOp.AND,
                          builder.binary(BinOp.WRAP_ADD, index,
                                         builder.int_const(U64, 1)),
                          mask)


# -- making one ------------------------------------------------------------------

def _build_new(module: Module) -> Function:
    """``__pl4g_table_new(arena, stride)``: a table with room for a few entries.

    Two allocations: the block, which never moves, and the entries, which are
    replaced when the table grows.  Neither is zeroed here -- what the arena
    hands out has never been handed out before, and what the system gave the
    arena was zeroed when it gave it.
    """
    table_ptr = table_type(module)
    arena_ptr = module.types.ptr_type(ARENA, mutable=True)
    func, fresh = _generated(module, NEW_SYMBOL, (arena_ptr, U64), table_ptr)
    if not fresh:
        return func
    alloc = _declared(module, ALLOC_SYMBOL, (arena_ptr, U64),
                      module.types.ptr_type(U8, mutable=True))
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(arena_ptr, "arena")
    stride = entry.add_param(U64, "stride")
    block = _as(builder, builder.call(alloc, (arena, builder.int_const(U64, BLOCK_SIZE)),
                                      module.types.ptr_type(U8, mutable=True)), U64)
    room = builder.binary(BinOp.WRAP_MUL, stride,
                          builder.int_const(U64, FIRST_CAPACITY))
    entries = builder.call(alloc, (arena, room),
                           module.types.ptr_type(U8, mutable=True))
    _write_address(builder, block, ARENA_FIELD, arena)
    _write(builder, block, MASK_FIELD, builder.int_const(U64, FIRST_CAPACITY - 1))
    _write(builder, block, COUNT_FIELD, builder.int_const(U64, 0))
    _write(builder, block, STRIDE_FIELD, stride)
    _write_address(builder, block, ENTRIES_FIELD, entries)
    builder.ret(block)
    return func


# -- finding where a key is, or where it would go --------------------------------

def _build_slot(module: Module) -> Function:
    """``__pl4g_table_slot(table, key)``: the entry the key is in, or would go in.

    One walk serves both questions, which is what makes a lookup and an
    insertion the same cost: the probe stops at the key or at the first empty
    place, and which of the two it stopped at is what the caller reads off the
    entry's own first word.  Nothing is ever taken out of a table, so an empty
    place is the end of the probe and not a hole in the middle of one.
    """
    table_ptr = table_type(module)
    func, fresh = _generated(module, SLOT_SYMBOL, (table_ptr, U64), table_ptr)
    if not fresh:
        return func
    entry = func.add_block()
    loop = func.add_block("probe")
    here = func.add_block("here")
    occupied = func.add_block("occupied")
    same = func.add_block("same")
    onward = func.add_block("onward")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    key = entry.add_param(U64, "key")
    mask = _read(builder, table, MASK_FIELD)
    stride = _read(builder, table, STRIDE_FIELD)
    entries = _read_address(builder, table, ENTRIES_FIELD, U64)
    builder.br(loop, (_hashed(builder, key, mask),))

    builder.position_at(loop)
    index = loop.add_param(U64, "at")
    place = _entry(builder, entries, index, stride)
    empty = builder.compare(CmpPred.EQ, builder.load(place),
                            builder.int_const(U64, EMPTY))
    builder.condbr(empty, here, occupied)

    builder.position_at(here)
    builder.ret(place)

    builder.position_at(occupied)
    held = builder.load(_field(builder, place, KEY_AT))
    builder.condbr(builder.compare(CmpPred.EQ, held, key), same, onward)

    builder.position_at(same)
    builder.ret(place)

    builder.position_at(onward)
    builder.br(loop, (_stepped(builder, index, mask),))
    return func


# -- putting a key in ------------------------------------------------------------

def _build_put(module: Module) -> Function:
    """``__pl4g_table_put(table, key)``: the entry for the key, made if it is new.

    The table is grown *before* the probe rather than after it, so that the
    entry answered with is one of the new array's and not one of an array the
    caller is about to be handed a pointer past.  Growing when the key turns out
    to be there already merely grows a little early, which costs a doubling that
    would have happened anyway.

    Nothing but the memory token travels on a conditional branch here, and a
    memory token is nowhere: it says which path's ordering of the operations on
    memory holds from here, and costs no instruction to hand over.
    """
    table_ptr = table_type(module)
    func, fresh = _generated(module, PUT_SYMBOL, (table_ptr, U64), table_ptr)
    if not fresh:
        return func
    slot_of = _build_slot(module)
    arena_ptr = module.types.ptr_type(ARENA, mutable=True)
    alloc = _declared(module, ALLOC_SYMBOL, (arena_ptr, U64),
                      module.types.ptr_type(U8, mutable=True))
    entry = func.add_block()
    grow = func.add_block("grow")
    rehash = func.add_block("rehash")
    one = func.add_block("one")
    move = func.add_block("move")
    copy = func.add_block("copy")
    stepped = func.add_block("stepped")
    grown = func.add_block("grown")
    ready = func.add_block("ready")
    there = func.add_block("there")
    added = func.add_block("added")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    key = entry.add_param(U64, "key")
    # One more than it holds, which is what it would hold if this key is new.
    wanted = builder.binary(BinOp.WRAP_ADD, _read(builder, table, COUNT_FIELD),
                            builder.int_const(U64, 1))
    capacity = builder.binary(BinOp.WRAP_ADD, _read(builder, table, MASK_FIELD),
                              builder.int_const(U64, 1))
    crowded = builder.compare(
        CmpPred.UGT,
        builder.binary(BinOp.WRAP_MUL, wanted,
                       builder.int_const(U64, LOAD_DENOMINATOR)),
        builder.binary(BinOp.WRAP_MUL, capacity,
                       builder.int_const(U64, LOAD_NUMERATOR)))
    builder.condbr(crowded, grow, ready, false_args=(builder.memory(),))

    # -- twice as much room, and everything moved into it ------------------------
    builder.position_at(grow)
    stride = _read(builder, table, STRIDE_FIELD)
    old_mask = _read(builder, table, MASK_FIELD)
    old_entries = _read_address(builder, table, ENTRIES_FIELD, U64)
    doubled = builder.binary(BinOp.WRAP_MUL, capacity, builder.int_const(U64, 2))
    roomier = builder.call(
        alloc, (_read_address(builder, table, ARENA_FIELD, ARENA),
                builder.binary(BinOp.WRAP_MUL, doubled, stride)),
        module.types.ptr_type(U8, mutable=True))
    _write_address(builder, table, ENTRIES_FIELD, roomier)
    _write(builder, table, MASK_FIELD,
           builder.binary(BinOp.WRAP_SUB, doubled, builder.int_const(U64, 1)))
    _write(builder, table, COUNT_FIELD, builder.int_const(U64, 0))
    builder.br(rehash, (builder.int_const(U64, 0), builder.memory()))

    # Every entry of the old array, put where the new array's own probe says.
    builder.position_at(rehash)
    at = rehash.add_param(U64, "at")
    builder.set_memory(rehash.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.UGT, at, old_mask), grown, one,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(one)
    builder.set_memory(one.add_param(MEM, "mem"))
    old_place = _entry(builder, old_entries, at, stride)
    builder.condbr(builder.compare(CmpPred.EQ, builder.load(old_place),
                                   builder.int_const(U64, LIVE)),
                   move, stepped,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(move)
    builder.set_memory(move.add_param(MEM, "mem"))
    moved_key = builder.load(_field(builder, old_place, KEY_AT))
    into = builder.call(slot_of, (table, moved_key), table_ptr)
    _write(builder, into, STATE_AT, builder.int_const(U64, LIVE))
    _write(builder, into, KEY_AT, moved_key)
    _write(builder, table, COUNT_FIELD,
           builder.binary(BinOp.WRAP_ADD, _read(builder, table, COUNT_FIELD),
                          builder.int_const(U64, 1)))
    builder.condbr(builder.compare(CmpPred.EQ, stride,
                                   builder.int_const(U64, DICT_STRIDE)),
                   copy, stepped,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(copy)
    builder.set_memory(copy.add_param(MEM, "mem"))
    _write(builder, into, VALUE_AT,
           builder.load(_field(builder, old_place, VALUE_AT)))
    builder.br(stepped, (builder.memory(),))

    builder.position_at(stepped)
    builder.set_memory(stepped.add_param(MEM, "mem"))
    builder.br(rehash, (builder.binary(BinOp.WRAP_ADD, at,
                                       builder.int_const(U64, 1)),
                        builder.memory()))

    builder.position_at(grown)
    builder.set_memory(grown.add_param(MEM, "mem"))
    builder.br(ready, (builder.memory(),))

    # -- and now the entry itself ------------------------------------------------
    builder.position_at(ready)
    builder.set_memory(ready.add_param(MEM, "mem"))
    place = builder.call(slot_of, (table, key), table_ptr)
    builder.condbr(builder.compare(CmpPred.EQ, builder.load(place),
                                   builder.int_const(U64, LIVE)),
                   there, added,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(there)
    there.add_param(MEM, "mem")
    builder.ret(place)

    builder.position_at(added)
    builder.set_memory(added.add_param(MEM, "mem"))
    _write(builder, place, STATE_AT, builder.int_const(U64, LIVE))
    _write(builder, place, KEY_AT, key)
    _write(builder, table, COUNT_FIELD,
           builder.binary(BinOp.WRAP_ADD, _read(builder, table, COUNT_FIELD),
                          builder.int_const(U64, 1)))
    builder.ret(place)
    return func


# -- what the four set operators are built out of --------------------------------

#: What `__pl4g_table_select` takes from the table it walks: the keys the other
#: table does not have, the keys it does have, or every key whatever the other
#: table holds.  The four operators a set answers are two walks between them.
WANT_ABSENT: Final[int] = 0
WANT_PRESENT: Final[int] = 1
WANT_EITHER: Final[int] = 2

SELECT_SYMBOL: Final[str] = "__pl4g_table_select"


def _build_select(module: Module) -> Function:
    """``__pl4g_table_select(out, walked, other, want)``.

    Every live key of *walked* is looked up in *other*, and put into *out* where
    what was found is what *want* asked for.  That is the whole of what the four
    operators a set answers need: what is in both is one walk asking for what
    the other has, what is in one and not the other is two walks each asking for
    what the other has not, and what is in either is two walks asking for
    everything.

    Writing it once rather than four times is not only shorter: the four differ
    in one comparison, and four copies of a probe loop would be four places for
    the same defect to be.
    """
    table_ptr = table_type(module)
    func, fresh = _generated(module, SELECT_SYMBOL,
                             (table_ptr, table_ptr, table_ptr, U64), VOID)
    if not fresh:
        return func
    slot_of = _build_slot(module)
    put = _build_put(module)
    entry = func.add_block()
    walk = func.add_block("walk")
    look = func.add_block("look")
    live = func.add_block("live")
    ask = func.add_block("ask")
    when_present = func.add_block("present")
    when_absent = func.add_block("absent")
    take = func.add_block("take")
    copy = func.add_block("copy")
    onward = func.add_block("onward")
    done = func.add_block("done")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    out = entry.add_param(table_ptr, "out")
    walked = entry.add_param(table_ptr, "walked")
    other = entry.add_param(table_ptr, "other")
    want = entry.add_param(U64, "want")
    mask = _read(builder, walked, MASK_FIELD)
    stride = _read(builder, walked, STRIDE_FIELD)
    entries = _read_address(builder, walked, ENTRIES_FIELD, U64)
    builder.br(walk, (builder.int_const(U64, 0), builder.memory()))

    builder.position_at(walk)
    at = walk.add_param(U64, "at")
    builder.set_memory(walk.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.UGT, at, mask), done, look,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(look)
    builder.set_memory(look.add_param(MEM, "mem"))
    place = _entry(builder, entries, at, stride)
    builder.condbr(builder.compare(CmpPred.EQ, builder.load(place),
                                   builder.int_const(U64, LIVE)),
                   live, onward,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(live)
    builder.set_memory(live.add_param(MEM, "mem"))
    key = builder.load(_field(builder, place, KEY_AT))
    found = builder.compare(
        CmpPred.EQ,
        builder.load(builder.call(slot_of, (other, key), table_ptr)),
        builder.int_const(U64, LIVE))
    builder.condbr(builder.compare(CmpPred.EQ, want,
                                   builder.int_const(U64, WANT_EITHER)),
                   take, ask,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(ask)
    builder.set_memory(ask.add_param(MEM, "mem"))
    builder.condbr(found, when_present, when_absent,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    # The two arms differ only in which way round the question is asked, which
    # is what keeps `want` a number rather than a truth value: there is no truth
    # value to compare it with until the lookup has happened.
    builder.position_at(when_present)
    builder.set_memory(when_present.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.EQ, want,
                                   builder.int_const(U64, WANT_PRESENT)),
                   take, onward,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(when_absent)
    builder.set_memory(when_absent.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.EQ, want,
                                   builder.int_const(U64, WANT_ABSENT)),
                   take, onward,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(take)
    builder.set_memory(take.add_param(MEM, "mem"))
    into = builder.call(put, (out, key), table_ptr)
    builder.condbr(builder.compare(CmpPred.EQ, stride,
                                   builder.int_const(U64, DICT_STRIDE)),
                   copy, onward,
                   true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(copy)
    builder.set_memory(copy.add_param(MEM, "mem"))
    _write(builder, into, VALUE_AT,
           builder.load(_field(builder, place, VALUE_AT)))
    builder.br(onward, (builder.memory(),))

    builder.position_at(onward)
    builder.set_memory(onward.add_param(MEM, "mem"))
    builder.br(walk, (builder.binary(BinOp.WRAP_ADD, at,
                                     builder.int_const(U64, 1)),
                      builder.memory()))

    builder.position_at(done)
    done.add_param(MEM, "mem")
    builder.ret()
    return func


# -- walking one ------------------------------------------------------------------

NEXT_SYMBOL: Final[str] = "__pl4g_table_next"


def _build_next(module: Module) -> Function:
    """``__pl4g_table_next(table, from)``: where the next entry holding a key is.

    Answered as a place in the array of entries, at or after *from*, and as one
    past the last place where there is none -- which is the same number a walk
    compares against to know it is done, so a caller needs no second answer for
    "there is no next one".

    A table is not walked in the order its keys were put in it.  It has no such
    order: where a key lands is where its hash puts it, and growing the table
    moves everything.  Python promises the order keys were added in and pays for
    it with a second array; Go deliberately randomises its walk so that no
    program can come to depend on an order it never promised.  This promises
    nothing and pays nothing, and the specification says so.
    """
    table_ptr = table_type(module)
    func, fresh = _generated(module, NEXT_SYMBOL, (table_ptr, U64), U64)
    if not fresh:
        return func
    entry = func.add_block()
    walk = func.add_block("walk")
    look = func.add_block("look")
    onward = func.add_block("onward")
    done = func.add_block("done")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    start = entry.add_param(U64, "from")
    capacity = builder.binary(BinOp.WRAP_ADD, _read(builder, table, MASK_FIELD),
                              builder.int_const(U64, 1))
    stride = _read(builder, table, STRIDE_FIELD)
    entries = _read_address(builder, table, ENTRIES_FIELD, U64)
    builder.br(walk, (start,))

    builder.position_at(walk)
    at = walk.add_param(U64, "at")
    builder.condbr(builder.compare(CmpPred.UGE, at, capacity), done, look)

    builder.position_at(look)
    builder.condbr(
        builder.compare(CmpPred.EQ,
                        builder.load(_entry(builder, entries, at, stride)),
                        builder.int_const(U64, LIVE)),
        done, onward)

    builder.position_at(onward)
    builder.br(walk, (builder.binary(BinOp.WRAP_ADD, at,
                                     builder.int_const(U64, 1)),))

    builder.position_at(done)
    builder.ret(at)
    return func


def entry_at(builder: IRBuilder, table: Value, at: Value) -> Value:
    """Where the entry at one place in a table's array of entries is."""
    return _entry(builder, _read_address(builder, table, ENTRIES_FIELD, U64), at,
                  _read(builder, table, STRIDE_FIELD))
