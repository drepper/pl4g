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

**An entry is shaped by what the table holds.**  A key that fits a word is one
word, whatever its type says; a key that does not -- text is the one there is --
is as many words as it takes, and so is the value.  `Shape` answers all of it:
where the value of an entry begins, how long an entry is, and which of the
generated functions to call.  The three that depend on any of that are generated
once per shape and named for it, so a program using two kinds of table carries
two probes and one of everything else.

**The hash is whatever the key is.**  A key that is a word is hashed by Fibonacci
hashing: multiplied by the closest odd number to two to the sixty-fourth over the
golden ratio, with the high bits folded down into the low ones.  A key that is
text is hashed over its bytes with FNV-1a, which is what makes two strings that
say the same thing one key wherever their bytes are -- and is compared by the same
walk the language's own `=` uses, so a key found in a table and a comparison
written in a program cannot disagree.  Both multiplications wrap, which no
program of the language may write and which these are: a hash is defined on the
bits, and there is nothing about an overflow here to report to anyone.
"""

from __future__ import annotations

from typing import Final

from dataclasses import dataclass

from ..ir.builder import IRBuilder
from ..ir.function import SYSTEM_CCONV, FuncAttrs, Function, Linkage
from ..ir.inst import BinOp, CastKind, CmpPred
from ..ir.layout import DataLayout, size_of
from ..ir.module import Module
from ..ir.layout import parts_of
from ..ir.types import (ARENA, I64, MEM, PtrType, STR, Type, U8, U64,
                         VOID)
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

#: The fields of one entry.  The state is always the first word; where the key
#: is is always the second; where the *value* is depends on how wide the key is,
#: and how wide an entry is depends on both.  A `Shape` answers all three.
STATE_AT: Final[int] = 0
KEY_AT: Final[int] = WORD


@dataclass(frozen=True, slots=True)
class Shape:
    """What one table holds, which is what its entries are shaped by.

    A set has a key and no value.  Everything that depends on how wide either of
    them is -- where the value sits in an entry, how long an entry is, and which
    of the generated functions to call -- is asked of this, so that nothing
    computes it twice and nothing assumes a word.
    """

    key: Type
    value: Type | None = None

    @property
    def key_words(self) -> int:
        """How many words the key takes, which is at least one."""
        return _words(self.key)

    @property
    def value_at(self) -> int:
        """Where the value of an entry begins."""
        return KEY_AT + self.key_words * WORD

    @property
    def stride(self) -> int:
        """How long one entry is: the state, the key, and the value if any."""
        return self.value_at + (0 if self.value is None
                                else _words(self.value) * WORD)

    @property
    def tag(self) -> str:
        """What the functions generated for this shape are called after.

        The types as the language writes them, which is what makes a dump of the
        image readable: `__pl4g_table_slot.str` is the one for a table keyed by
        strings and there is no wondering which instantiation it belongs to.
        """
        return self.key.render() if self.value is None \
            else ":".join((self.key.render(), self.value.render()))


def _words(ty: Type) -> int:
    """How many words a value of *ty* takes in an entry.

    Rounded up, because an entry is words: a key and a value each begin on a
    word so that reading one is a load and not a shift.
    """
    return max(1, (size_of(ty, _LAYOUT) + WORD - 1) // WORD)


#: What a word is here.  A table is words whatever the target's alignment rules
#: are, and every target this compiler has is sixty-four bit; the layout is
#: therefore the same everywhere and is asked for once.
_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=WORD)

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


def key_ir_type(shape: Shape) -> Type:
    """What a key of this shape is passed and stored as.

    A key that fits a word is a word, whatever the type says: a truth value and
    a value of an enumeration are both whole numbers, and holding them all as
    one thing is what let one table serve every instantiation.  A key that does
    not fit a word is held as what it is, and the functions for that shape are
    generated for it.
    """
    return U64 if _words(shape.key) == 1 else shape.key


def _load_key(builder: IRBuilder, shape: Shape, place: Value) -> Value:
    """The key of the entry at *place*."""
    where = _field(builder, place, KEY_AT)
    held = key_ir_type(shape)
    if held is U64:
        return builder.load(where)
    return builder.load(_as(builder, where, held))


def _store_key(builder: IRBuilder, shape: Shape, place: Value,
               key: Value) -> None:
    """Put a key into the entry at *place*."""
    where = _field(builder, place, KEY_AT)
    held = key_ir_type(shape)
    if held is U64:
        builder.store(where, key)
        return
    builder.store(_as(builder, where, held), key)


def _load_value(builder: IRBuilder, shape: Shape, place: Value) -> Value:
    """The value of the entry at *place*."""
    assert shape.value is not None
    return builder.load(_as(builder, _field(builder, place, shape.value_at),
                            shape.value))


def _store_value(builder: IRBuilder, shape: Shape, place: Value,
                 value: Value) -> None:
    """Put a value into the entry at *place*."""
    assert shape.value is not None
    builder.store(_as(builder, _field(builder, place, shape.value_at),
                      shape.value), value)


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
        # The runtime allocates and writes tables, all of which outlives the
        # call: what the language calls impure is what these are for.
        FuncAttrs(abi=SYSTEM_CCONV, impure=True), cconv=SYSTEM_CCONV,
        linkage=Linkage.VISIBLE))


def _generated(module: Module, name: str, params: tuple[Type, ...],
               result: Type) -> tuple[Function, bool]:
    """A function of this module's own, and whether it has yet to be built."""
    found = module.functions.get(name)
    if isinstance(found, Function):
        return found, False
    func = module.add_function(Function(
        name, module.types.func_type(params, result),
        FuncAttrs(abi="pl4g.runtime", impure=True), linkage=Linkage.INTERNAL))
    return func, True


def arena_of(builder: IRBuilder, table: Value) -> Value:
    """Which arena a table came out of, which is a field of the table itself.

    A collection made out of two others comes out of the same arena the first of
    them did, and the only place that is written down is the table.
    """
    return _read_address(builder, table, ARENA_FIELD, ARENA)


def count_of(builder: IRBuilder, table: Value) -> Value:
    """How many entries a table holds, which is a field of the table itself.

    Kept rather than counted: a walk of the entries would have to step past the
    places holding nothing, and how many there are is a number the two that put
    things in already maintain.
    """
    return _read(builder, table, COUNT_FIELD)


def table_type(module: Module) -> PtrType:
    """What a table is reached by: a pointer to its first word."""
    return module.types.ptr_type(U64, mutable=True)


def ensure_allocator(module: Module) -> None:
    """Declare the allocator, for something that wants room and not a table."""
    _declared(module, ALLOC_SYMBOL,
              (module.types.ptr_type(ARENA, mutable=True), U64),
              module.types.ptr_type(U8, mutable=True))


def ensure_operators(module: Module, shape: Shape) -> Function:
    """Build what the four operators a set answers are made out of."""
    ensure_runtime(module, shape)
    return _build_select(module, shape)


def ensure_runtime(module: Module, shape: Shape) -> None:
    """Build what a collection of this shape needs.

    Two of them are the same for every table -- making one, and walking one --
    since what they do is decided by the stride the table carries.  The others
    are generated for the shape: where the value of an entry sits and how a key
    is hashed and compared are settled by the types, and a table keyed by text
    has a different answer to all three than one keyed by a word.
    """
    _build_new(module)
    _build_next(module)
    _build_slot(module, shape)
    _build_put(module, shape)


def named(symbol: str, shape: Shape) -> str:
    """What the function of *shape* is called."""
    return ".".join((symbol, shape.tag))


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
    """Where a probe for a key that is one word starts.

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


def _hash_of(builder: IRBuilder, shape: Shape, key: Value,
             mask: Value) -> Value:
    """Where a probe for *key* starts, whatever kind of key it is.

    A key that is a word is hashed by the multiplication above.  A key that is
    text is hashed over its bytes -- two strings that say the same thing are one
    key, and what they have in common is the bytes and not where they are -- and
    the answer is then folded the same way, so that everything below this knows
    one kind of hash.
    """
    from .strings import hash_function

    if key_ir_type(shape) is U64:
        return _hashed(builder, key, mask)
    return builder.binary(BinOp.AND,
                          builder.call(hash_function(builder.module),
                                       _taken_apart(builder, key), U64),
                          mask)


def _same_key(builder: IRBuilder, shape: Shape, held: Value,
              key: Value) -> Value:
    """Whether the key in an entry is the key being looked for.

    For a word that is what `=` is.  For text it is what `=` is for text: the
    same bytes, whoever holds them -- which is the walk the language's own
    comparison does, asked here through the same generated function so that a
    key found in a table and a comparison written in a program cannot disagree.
    """
    from .strings import compare_function

    if key_ir_type(shape) is U64:
        return builder.compare(CmpPred.EQ, held, key)
    return builder.compare(
        CmpPred.EQ,
        builder.call(compare_function(builder.module),
                     (*_taken_apart(builder, held), *_taken_apart(builder, key)),
                     I64),
        builder.int_const(I64, 0))


def _taken_apart(builder: IRBuilder, text: Value) -> tuple[Value, Value]:
    """A string as the two things it is: where its bytes are, and how many.

    Which is what the generated functions over strings take, since a value of
    several parts crosses a call as its parts.
    """
    return (builder.extract(text, 0, parts_of(STR)[0]),
            builder.extract(text, 1, U64))


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

def _build_slot(module: Module, shape: Shape) -> Function:
    """``__pl4g_table_slot(table, key)``: the entry the key is in, or would go in.

    One walk serves both questions, which is what makes a lookup and an
    insertion the same cost: the probe stops at the key or at the first empty
    place, and which of the two it stopped at is what the caller reads off the
    entry's own first word.  Nothing is ever taken out of a table, so an empty
    place is the end of the probe and not a hole in the middle of one.
    """
    table_ptr = table_type(module)
    held = key_ir_type(shape)
    func, fresh = _generated(module, named(SLOT_SYMBOL, shape),
                             (table_ptr, held), table_ptr)
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
    key = entry.add_param(held, "key")
    mask = _read(builder, table, MASK_FIELD)
    stride = _read(builder, table, STRIDE_FIELD)
    entries = _read_address(builder, table, ENTRIES_FIELD, U64)
    builder.br(loop, (_hash_of(builder, shape, key, mask),))

    builder.position_at(loop)
    index = loop.add_param(U64, "at")
    place = _entry(builder, entries, index, stride)
    empty = builder.compare(CmpPred.EQ, builder.load(place),
                            builder.int_const(U64, EMPTY))
    builder.condbr(empty, here, occupied)

    builder.position_at(here)
    builder.ret(place)

    builder.position_at(occupied)
    builder.condbr(_same_key(builder, shape, _load_key(builder, shape, place),
                             key), same, onward)

    builder.position_at(same)
    builder.ret(place)

    builder.position_at(onward)
    builder.br(loop, (_stepped(builder, index, mask),))
    return func


# -- putting a key in ------------------------------------------------------------

def _build_put(module: Module, shape: Shape) -> Function:
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
    held = key_ir_type(shape)
    func, fresh = _generated(module, named(PUT_SYMBOL, shape),
                             (table_ptr, held), table_ptr)
    if not fresh:
        return func
    slot_of = _build_slot(module, shape)
    arena_ptr = module.types.ptr_type(ARENA, mutable=True)
    alloc = _declared(module, ALLOC_SYMBOL, (arena_ptr, U64),
                      module.types.ptr_type(U8, mutable=True))
    entry = func.add_block()
    grow = func.add_block("grow")
    rehash = func.add_block("rehash")
    one = func.add_block("one")
    move = func.add_block("move")
    stepped = func.add_block("stepped")
    grown = func.add_block("grown")
    ready = func.add_block("ready")
    there = func.add_block("there")
    added = func.add_block("added")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    key = entry.add_param(held, "key")
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
    moved_key = _load_key(builder, shape, old_place)
    into = builder.call(slot_of, (table, moved_key), table_ptr)
    _write(builder, into, STATE_AT, builder.int_const(U64, LIVE))
    _store_key(builder, shape, into, moved_key)
    _write(builder, table, COUNT_FIELD,
           builder.binary(BinOp.WRAP_ADD, _read(builder, table, COUNT_FIELD),
                          builder.int_const(U64, 1)))
    if shape.value is not None:
        _store_value(builder, shape, into,
                     _load_value(builder, shape, old_place))
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
    _store_key(builder, shape, place, key)
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


def _build_select(module: Module, shape: Shape) -> Function:
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
    func, fresh = _generated(module, named(SELECT_SYMBOL, shape),
                             (table_ptr, table_ptr, table_ptr, U64), VOID)
    if not fresh:
        return func
    slot_of = _build_slot(module, shape)
    put = _build_put(module, shape)
    entry = func.add_block()
    walk = func.add_block("walk")
    look = func.add_block("look")
    live = func.add_block("live")
    ask = func.add_block("ask")
    when_present = func.add_block("present")
    when_absent = func.add_block("absent")
    take = func.add_block("take")
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
    key = _load_key(builder, shape, place)
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
    # The four operators are a set's, and a set has no value to carry over: what
    # this used to ask of the stride at run time the shape now says outright.
    builder.call(put, (out, key), table_ptr)
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
