"""The hash table a set and a dictionary are, written in the representation.

A collection is a *table*: a block of memory somewhere in an arena, holding how
much room it has and the entries themselves.  The value a program passes around
is where that block is and nothing else, so two names for one collection see one
answer.

**The table is compact, and open-addressed with linear probing.**  Its entries
are one dense array in the order their keys were first put in, and beside it an
*index*: a power of two of words, each saying where in the dense array the entry
whose key hashes there is, probed linearly.  The layout is Python's since 3.6
(and its promise since 3.7): a walk is a walk of the dense array, which is the
order the keys came in, and a lookup is a probe of the index -- a word a step,
eight to a cache line -- and then the one entry it names.  The index word carries
the top bits of the key's hash beside where the entry is, so a probe that meets
another key's word rejects it without reading that entry at all.  Every entry is
in the block rather than in a chain hanging off it, which is what Python, Rust,
Go, Swift and every recent implementation do; separate chaining is what the
older ones did and what they moved away from.

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

**The hash is keyed, and every table has its own key.**  A key is hashed with
rapidhash -- a word as its eight bytes, text over its bytes, which is what makes
two strings that say the same thing one key wherever their bytes are -- with a
seed the table carries, which the runtime works out from a base the process
takes from the kernel's random bytes, or from `PL4G_HASH_SEED`.  So where a key
lands is nothing a program can work out, and keys chosen to land together cannot
be chosen.  Text is compared by the same walk the language's own `=` uses, so a
key found in a table and a comparison written in a program cannot disagree.
"""

from __future__ import annotations

from typing import Final

from dataclasses import dataclass

from ..ir.builder import IRBuilder
from ..ir.function import SYSTEM_CCONV, BasicBlock, FuncAttrs, Function, Linkage
from ..ir.inst import AddressInst
from ..ir.module import GlobalVar
from ..target import statuses
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
RELEASE_SYMBOL: Final[str] = "__pl4g_release"

#: What `⎕heap` is: an allocator that gives back one object at a time, in the
#: packaged runtime.  Its two take and give back by size, which the compiler always
#: knows.
HEAP_ALLOC_SYMBOL: Final[str] = "pl4g_heap_alloc"
HEAP_FREE_SYMBOL: Final[str] = "pl4g_heap_free"
#: The compiler's own around them: room from the heap or the end of the program,
#: and room from whichever allocator a value names.
HEAP_NEW_SYMBOL: Final[str] = "__pl4g_heap_new"
ALLOCATE_SYMBOL: Final[str] = "__pl4g_allocate"
DISPOSE_SYMBOL: Final[str] = "__pl4g_dispose"

#: The variable `⎕heap` is.  Its address is what names the heap wherever an
#: allocator is named; nothing is kept in it, the heap's state being the runtime's.
HEAP_SYMBOL: Final[str] = "__pl4g_heap"
HEAP_KEY: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}heap"

#: How wide a word is.  Every field of a table, every key and every value is
#: one, which is what lets one table serve every instantiation.
WORD: Final[int] = 8

#: Where the fields of a table block are.  `mask` is the size of the index less
#: one, so that a place in it is a bitwise and.  The index and the entries are
#: one allocation separate from the block, the index first and the entries
#: straight after it, so that the block never moves: what a program passes around
#: is where the block is, and a table that grew under it would otherwise leave
#: every name for it pointing at the old entries.
ARENA_FIELD: Final[int] = 0
MASK_FIELD: Final[int] = WORD
COUNT_FIELD: Final[int] = 2 * WORD
STRIDE_FIELD: Final[int] = 3 * WORD
#: Where the dense array of entries begins, which a walk reads; a probe reads
#: `INDEX_FIELD` and nothing else, every word of the index saying how far from
#: the index's own start the entry it names is.
ENTRIES_FIELD: Final[int] = 4 * WORD
#: How many entries of the dense array have been written: the ones holding a
#: key and the holes taking one out left.  `count` says how many keys there
#: are, which is what `#` answers; this is where the next key goes, how far a
#: walk goes, and -- the index holding a word for every one of them -- how full
#: the index is, which decides when the table is rebuilt.  The two differ only
#: where something has been taken out.
USED_FIELD: Final[int] = 5 * WORD

#: What the table's keys are hashed with, its own and no other table's: worked
#: out when the table is made, again whenever it grows, and -- for a table laid
#: out in the image empty, which says nought here -- when the first key goes in.
SEED_FIELD: Final[int] = 6 * WORD

#: Where the index is.
INDEX_FIELD: Final[int] = 7 * WORD

#: How much room a block takes, on the grain the allocator hands out.
BLOCK_SIZE: Final[int] = 8 * WORD

#: What a word of the index holds.  Nought is a place nothing hashed to.  Any
#: other word is two things: in its low `OFFSET_BITS` bits, how many bytes after
#: the start of the index the entry is -- never nought, the entries coming after
#: the index -- and in the bits above, the top bits of the hash of that entry's
#: key.  A probe compares those with the top bits of the hash it is looking for
#: before it reads the entry: a word of another key is then turned away by one
#: comparison, and the entry it names, which is in another cache line, is not
#: read at all.  Forty bits of offset is a terabyte of entries; twenty-four bits
#: of hash turn away all but one in sixteen million of the keys a probe meets.
OFFSET_BITS: Final[int] = 40
OFFSET_MASK: Final[int] = (1 << OFFSET_BITS) - 1
HASH_TOP: Final[int] = ((1 << 64) - 1) ^ OFFSET_MASK

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

#: What the first word of an entry says about it.  Three states, because a key
#: may be taken out: an entry that held one and does not any more is *given up*
#: -- a hole in the dense array, which a walk steps past and which the word of
#: the index naming it goes on naming, so that a probe walking past it to what
#: was put in after it still walks past it.  An entry not yet written is empty,
#: which is what the one a key would go in next is.
EMPTY: Final[int] = 0
LIVE: Final[int] = 1
GONE: Final[int] = 2

#: How many words the index of a table has to begin with.  A power of two,
#: because a place in it is the hash masked; eight is enough that a collection
#: written down with a handful of entries never grows while it is being built.
FIRST_CAPACITY: Final[int] = 8

#: How full the index gets before the table is rebuilt, as a fraction: three of
#: every four.  A linear probe lengthens sharply past that, and doubling before
#: it does is what keeps the expected cost constant.
LOAD_NUMERATOR: Final[int] = 3
LOAD_DENOMINATOR: Final[int] = 4


def room_for(capacity: int) -> int:
    """How many entries the dense array of an index of *capacity* words holds.

    As many as the index may name before the table is rebuilt, and one more: the
    one past the last written is where a key not in the table would go, and a
    lookup of such a key reads that entry -- its state written empty -- rather
    than asking a second question of its answer.
    """
    return capacity * LOAD_NUMERATOR // LOAD_DENOMINATOR + 1


#: The seed of a table the compiler lays out in the image with its keys in it.
#: Such a table is never written, so nothing a program does can make use of
#: knowing it; one laid out empty says nought instead, and takes a seed of its
#: own with its first key.
IMAGE_SEED: Final[int] = 0x6A09E667F3BCC908

#: What the four are called.  They are the compiler's and a program cannot
#: name them, so the names carry the prefix everything of the compiler's does.
NEW_SYMBOL: Final[str] = "__pl4g_table_new"
SLOT_SYMBOL: Final[str] = "__pl4g_table_slot"
PUT_SYMBOL: Final[str] = "__pl4g_table_put"
GROW_SYMBOL: Final[str] = "__pl4g_table_grow"


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


def heap_global(module: Module) -> GlobalVar:
    """The variable whose address names `⎕heap`, made once per module.

    Three words, an arena's size, so that every allocator is reached the same way;
    nothing reads them.  A program that allocates nowhere carries none of it.
    """
    found = module.globals.get(HEAP_KEY)
    if isinstance(found, GlobalVar):
        return found
    return module.add_global(GlobalVar(
        name=HEAP_SYMBOL, value_type=ARENA,
        ptr_type=module.types.ptr_type(ARENA, mutable=True),
        initializer=None, linkage=Linkage.INTERNAL), key=HEAP_KEY)


def is_heap(value: Value) -> bool:
    """Whether *value* is, while compiling, the address `⎕heap` is named by."""
    return isinstance(value, AddressInst) and isinstance(value.operands[0], GlobalVar) \
        and value.operands[0].name == HEAP_SYMBOL


def heap_new_function(module: Module) -> Function:
    """``__pl4g_heap_new(size)``: room from the heap, or the program stopped.

    The runtime answers nothing where the system has no more to give, and this is
    where that becomes the stop every other allocation makes.
    """
    bytes_ = module.types.ptr_type(U8, mutable=True)
    func, fresh = generated(module, HEAP_NEW_SYMBOL, (U64,), bytes_)
    if not fresh:
        return func
    alloc = _declared(module, HEAP_ALLOC_SYMBOL, (U64,), bytes_)
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    size = entry.add_param(U64, "size")
    made = builder.call(alloc, (size,), bytes_)
    builder.check(builder.compare(CmpPred.NE, builder.cast(CastKind.BITCAST, made,
                                                           U64),
                                  builder.int_const(U64, 0)),
                  "out of memory", statuses.OUT_OF_MEMORY)
    builder.ret(made)
    return func


def heap_free_function(module: Module) -> Function:
    """The runtime's ``pl4g_heap_free(where, size)``."""
    return _declared(module, HEAP_FREE_SYMBOL,
                     (module.types.ptr_type(U8, mutable=True), U64), VOID)


def allocator_function(module: Module) -> Function:
    """``__pl4g_allocate(allocator, size)``: room from whichever allocator is named.

    The heap where the address is `⎕heap`'s, and an arena's bump otherwise -- the two
    kinds there are.  Where the compiler knows which, `allocate` calls that one
    straight away and this is not reached.
    """
    arena_ptr = module.types.ptr_type(ARENA, mutable=True)
    bytes_ = module.types.ptr_type(U8, mutable=True)
    func, fresh = generated(module, ALLOCATE_SYMBOL, (arena_ptr, U64), bytes_)
    if not fresh:
        return func
    bump = _declared(module, ALLOC_SYMBOL, (arena_ptr, U64), bytes_)
    entry = func.add_block()
    heap = func.add_block("heap")
    pool = func.add_block("pool")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(arena_ptr, "allocator")
    size = entry.add_param(U64, "size")
    builder.condbr(_same_address(builder, arena,
                                 builder.address(heap_global(module))), heap, pool)
    builder.position_at(heap)
    builder.ret(builder.call(heap_new_function(module), (size,), bytes_))
    builder.position_at(pool)
    builder.ret(builder.call(bump, (arena, size), bytes_))
    return func


def dispose_function(module: Module) -> Function:
    """``__pl4g_dispose(allocator, where, size)``: one object given back.

    To the heap where the allocator is `⎕heap`; nothing otherwise, an arena giving
    back only all at once and text in the image never.  Where the compiler knows the
    allocator, `dispose` gives back to the heap straight away or does nothing at all.
    """
    arena_ptr = module.types.ptr_type(ARENA, mutable=True)
    bytes_ = module.types.ptr_type(U8, mutable=True)
    func, fresh = generated(module, DISPOSE_SYMBOL, (arena_ptr, bytes_, U64), VOID)
    if not fresh:
        return func
    entry = func.add_block()
    heap = func.add_block("heap")
    done = func.add_block("done")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(arena_ptr, "allocator")
    where = entry.add_param(bytes_, "where")
    size = entry.add_param(U64, "size")
    builder.condbr(_same_address(builder, arena,
                                 builder.address(heap_global(module))), heap, done,
                   true_args=(builder.memory(),), false_args=(builder.memory(),))
    builder.position_at(heap)
    builder.set_memory(heap.add_param(MEM, "mem"))
    builder.call(heap_free_function(module), (where, size), VOID)
    builder.ret()
    builder.position_at(done)
    builder.set_memory(done.add_param(MEM, "mem"))
    builder.ret()
    return func


def _same_address(builder: IRBuilder, one: Value, other: Value) -> Value:
    """Whether two addresses are the same, asked of the numbers they are."""
    return builder.compare(CmpPred.EQ, builder.cast(CastKind.BITCAST, one, U64),
                           builder.cast(CastKind.BITCAST, other, U64))


def no_allocator(builder: IRBuilder) -> Value:
    """The allocator of something in the image, which nothing gives back."""
    module = builder.module
    return builder.cast(CastKind.BITCAST, builder.int_const(U64, 0),
                        module.types.ptr_type(ARENA, mutable=True))


def allocate(builder: IRBuilder, arena: Value, size: Value) -> Value:
    """Room for *size* bytes from *arena*: the heap called straight away where the
    compiler knows it is the heap, and the dispatch where it does not."""
    module = builder.module
    bytes_ = module.types.ptr_type(U8, mutable=True)
    if is_heap(arena):
        return builder.call(heap_new_function(module), (size,), bytes_)
    return builder.call(allocator_function(module), (arena, size), bytes_)


def release_function(module: Module) -> Function:
    """The one that gives a whole arena back, declared on first ask."""
    return _declared(module, RELEASE_SYMBOL,
                     (module.types.ptr_type(ARENA, mutable=True),), VOID)


def _declared(module: Module, name: str, params: tuple[Type, ...],
              result: Type, impure: bool = True) -> Function:
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
        FuncAttrs(abi=SYSTEM_CCONV, impure=impure), cconv=SYSTEM_CCONV,
        linkage=Linkage.VISIBLE))


#: What the runtime hashes a key with, and gives a table its seed with.
HASH_SYMBOL: Final[str] = "pl4g_hash"
HASH_WORD_SYMBOL: Final[str] = "pl4g_hash_word"
HASH_SEED_SYMBOL: Final[str] = "pl4g_hash_seed"


def hash_function(module: Module) -> Function:
    """The runtime's ``pl4g_hash(bytes, count, seed)``, which changes nothing."""
    return _declared(module, HASH_SYMBOL,
                     (module.types.ptr_type(U8, mutable=True), U64, U64), U64,
                     impure=False)


def hash_word_function(module: Module) -> Function:
    """The runtime's ``pl4g_hash_word(word, seed)``, which changes nothing."""
    return _declared(module, HASH_WORD_SYMBOL, (U64, U64), U64, impure=False)


def seed_function(module: Module) -> Function:
    """The runtime's ``pl4g_hash_seed()``: a seed no other table has."""
    return _declared(module, HASH_SEED_SYMBOL, (), U64)


def generated(module: Module, name: str, params: tuple[Type, ...],
              result: Type) -> tuple[Function, bool]:
    """A function of this module's own, and whether it has yet to be built.

    Public because the environment's builder is one of these without being a
    table operation: what makes it the same kind of thing is the convention and
    the linkage, and those are settled here.
    """
    found = module.functions.get(name)
    if isinstance(found, Function):
        return found, False
    func = module.add_function(Function(
        name, module.types.func_type(params, result),
        FuncAttrs(abi="pl4g.runtime", impure=True), linkage=Linkage.INTERNAL))
    return func, True


#: What answers the arena a new table made out of another comes out of.
ARENA_OF_SYMBOL: Final[str] = "__pl4g_table_arena"


def arena_of(builder: IRBuilder, table: Value) -> Value:
    """Which arena a table came out of, which is a field of the table itself.

    A collection made out of two others comes out of the same arena the first of
    them did, and the only place that is written down is the table -- except
    that one in the image came out of none, and what is made from it comes out
    of `⎕heap`, as anything saying nothing does.
    """
    module = builder.module
    arena_ptr = module.types.ptr_type(ARENA, mutable=True)
    func, fresh = generated(module, ARENA_OF_SYMBOL, (table_type(module),),
                            arena_ptr)
    if fresh:
        entry = func.add_block()
        none = func.add_block("none")
        some = func.add_block("some")
        inner = IRBuilder(module, func)
        inner.position_at(entry)
        held = _read_address(inner, entry.add_param(table_type(module), "table"),
                             ARENA_FIELD, ARENA)
        inner.condbr(inner.compare(CmpPred.EQ,
                                   inner.cast(CastKind.BITCAST, held, U64),
                                   inner.int_const(U64, 0)), none, some)
        inner.position_at(none)
        inner.ret(inner.address(heap_global(module)))
        inner.position_at(some)
        inner.ret(held)
    return builder.call(func, (builder.cast(CastKind.BITCAST, table,
                                            table_type(module)),), arena_ptr)


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
    """Make the allocator, for something that wants room and not a table."""
    allocator_function(module)


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


def _hashed(builder: IRBuilder, shape: Shape, key: Value, seed: Value) -> Value:
    """The hash of *key* with the table's seed, whatever kind of key it is.

    A key that is a word is hashed as its eight bytes, and text over its bytes
    -- two strings that say the same thing are one key, and what they have in
    common is the bytes and not where they are.  Both with the seed, which is
    what keeps a program from choosing keys that land together: where a key goes
    is nothing it can work out.  All sixty-four bits: the low ones say where a
    probe starts and the top ones are what the index keeps beside the entry.
    """
    module = builder.module
    if key_ir_type(shape) is U64:
        return builder.call(hash_word_function(module), (key, seed), U64)
    return builder.call(hash_function(module),
                        (*_taken_apart(builder, key), seed), U64)


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

    Two allocations: the block, which never moves, and the index with the dense
    array of entries straight after it, which are replaced when the table is
    rebuilt.

    **The index is cleared here, and the first entry's state**, which is all of
    the run a table reads before it writes: `⎕heap` hands out again what was
    given back, holding whatever it held, and a word of the index that is not
    nought names an entry nobody wrote.  The rest of the entries are written
    before they are read -- each `put` marks the entry after the one it fills
    as empty (`_build_put`) -- so clearing them would be stores nothing reads.
    The eight words of a first index are one run of zeroes, which each machine
    stores as wide as its registers are.
    """
    table_ptr = table_type(module)
    arena_ptr = module.types.ptr_type(ARENA, mutable=True)
    func, fresh = generated(module, NEW_SYMBOL, (arena_ptr, U64), table_ptr)
    if not fresh:
        return func
    alloc = allocator_function(module)
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    arena = entry.add_param(arena_ptr, "arena")
    stride = entry.add_param(U64, "stride")
    block = _as(builder, builder.call(alloc, (arena, builder.int_const(U64, BLOCK_SIZE)),
                                      module.types.ptr_type(U8, mutable=True)), U64)
    room = builder.binary(BinOp.WRAP_ADD,
                          builder.binary(BinOp.WRAP_MUL, stride,
                                         builder.int_const(U64, room_for(FIRST_CAPACITY))),
                          builder.int_const(U64, FIRST_CAPACITY * WORD))
    run = builder.call(alloc, (arena, room), module.types.ptr_type(U8, mutable=True))
    _write_address(builder, block, ARENA_FIELD, arena)
    _write(builder, block, MASK_FIELD, builder.int_const(U64, FIRST_CAPACITY - 1))
    _write(builder, block, COUNT_FIELD, builder.int_const(U64, 0))
    _write(builder, block, USED_FIELD, builder.int_const(U64, 0))
    _write(builder, block, STRIDE_FIELD, stride)
    _write_address(builder, block, INDEX_FIELD, run)
    _write_address(builder, block, ENTRIES_FIELD,
                   builder.binary(BinOp.ADD, run,
                                  builder.int_const(U64, FIRST_CAPACITY * WORD)))
    _write(builder, block, SEED_FIELD,
           builder.call(seed_function(module), (), U64))
    _clear_words(builder, _as(builder, run, U64), FIRST_CAPACITY)
    _write(builder, _as(builder, builder.binary(
        BinOp.ADD, run, builder.int_const(U64, FIRST_CAPACITY * WORD)), U64),
           STATE_AT, builder.int_const(U64, EMPTY))
    builder.ret(block)
    return func


#: How many words of an index are cleared at a time: as many as the smallest
#: index has, which every index is a multiple of, being a larger power of two.
CLEARED_AT_ONCE: Final[int] = FIRST_CAPACITY


def _clear_words(builder: IRBuilder, at: Value, words: int) -> None:
    """Store nought over *words* words from *at*, as one run.

    A run of one value stored is what the back ends make as wide as a machine's
    registers are -- four stores of sixteen bytes for eight words where the
    machine has them, and eight of one word where it has not.
    """
    module = builder.module
    run = module.types.vec_type(U64, words)
    builder.store(builder.cast(CastKind.BITCAST, at,
                               module.types.ptr_type(run, mutable=True)),
                  builder.splat(builder.int_const(U64, 0), run))


def _clear_index(func: Function, builder: IRBuilder, index: Value,
                 words: Value) -> None:
    """Store nought over the *words* words of an index at *index*.

    A loop of runs of `CLEARED_AT_ONCE` words: an index is a power of two of
    words and never fewer than that many, so the runs cover it exactly.
    """
    loop = func.add_block("clear")
    body = func.add_block("clearing")
    done = func.add_block("cleared")
    end = builder.binary(BinOp.ADD, index,
                         builder.binary(BinOp.WRAP_MUL, words,
                                        builder.int_const(U64, WORD)))
    builder.br(loop, (index, builder.memory()))
    builder.position_at(loop)
    at = loop.add_param(index.ty, "at")
    builder.set_memory(loop.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.ULT,
                                   builder.cast(CastKind.BITCAST, at, U64),
                                   builder.cast(CastKind.BITCAST, end, U64)),
                   body, done, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))
    builder.position_at(body)
    builder.set_memory(body.add_param(MEM, "mem"))
    _clear_words(builder, at, CLEARED_AT_ONCE)
    builder.br(loop, (builder.binary(BinOp.ADD, at,
                                     builder.int_const(U64, CLEARED_AT_ONCE * WORD)),
                      builder.memory()))
    builder.position_at(done)
    builder.set_memory(done.add_param(MEM, "mem"))


# -- finding where a key is ------------------------------------------------------

def _probe(func: Function, builder: IRBuilder, shape: Shape, key: Value,
           hashed: Value, index: Value, mask: Value, threaded: bool
           ) -> tuple[BasicBlock, Value, BasicBlock, Value]:
    """Walk the index from where *hashed* puts *key*, to the entry holding it or
    to the first word of the index nothing holds.

    Answers the two blocks the walk ends in, each with what it ends with: the
    entry, where the key was found, and the place in the index, where it was
    not.  *threaded* is whether the memory token is carried along, which it is in
    a function that writes: a probe only reads, but what it reads is what the
    writes before it left.

    **A word of the index is asked three things, the cheapest first.**  Nought
    ends the walk, the key being nowhere further along it.  The top bits of the
    hash it keeps are compared with the key's own next, which turns away all but
    a few of the other keys a walk meets without reading the entry they name.
    Only then is the entry read: whether it still holds a key -- one given up is
    walked past, its word being what keeps the walk going past it -- and whether
    that key is this one.
    """
    probe = func.add_block("probe")
    tagged = func.add_block("tagged")
    near = func.add_block("near")
    live = func.add_block("live")
    onward = func.add_block("onward")
    found = func.add_block("found")
    missing = func.add_block("missing")

    def carried() -> tuple[Value, ...]:
        return (builder.memory(),) if threaded else ()

    def taken(block: BasicBlock) -> None:
        if threaded:
            builder.set_memory(block.add_param(MEM, "mem"))

    builder.br(probe, (builder.binary(BinOp.AND, hashed, mask), *carried()))

    builder.position_at(probe)
    at = probe.add_param(U64, "at")
    taken(probe)
    word = builder.load(_entry(builder, index, at, builder.int_const(U64, WORD)))
    builder.condbr(builder.compare(CmpPred.EQ, word, builder.int_const(U64, 0)),
                   missing, tagged, true_args=(at, *carried()),
                   false_args=carried())

    builder.position_at(tagged)
    taken(tagged)
    builder.condbr(builder.compare(CmpPred.ULT,
                                   builder.binary(BinOp.XOR, word, hashed),
                                   builder.int_const(U64, 1 << OFFSET_BITS)),
                   near, onward, true_args=carried(), false_args=carried())

    builder.position_at(near)
    taken(near)
    place = builder.binary(BinOp.ADD, index,
                           builder.binary(BinOp.AND, word,
                                          builder.int_const(U64, OFFSET_MASK)))
    builder.condbr(builder.compare(CmpPred.EQ, builder.load(place),
                                   builder.int_const(U64, LIVE)),
                   live, onward, true_args=carried(), false_args=carried())

    builder.position_at(live)
    taken(live)
    builder.condbr(_same_key(builder, shape, _load_key(builder, shape, place), key),
                   found, onward, true_args=(place, *carried()),
                   false_args=carried())

    builder.position_at(onward)
    taken(onward)
    builder.br(probe, (_stepped(builder, at, mask), *carried()))

    holding = found.add_param(table_type(builder.module), "place")
    taken(found)
    nowhere = missing.add_param(U64, "at")
    taken(missing)
    return found, holding, missing, nowhere


def _build_slot(module: Module, shape: Shape) -> Function:
    """``__pl4g_table_slot(table, key)``: the entry the key is in, or would go in.

    One answer serves both questions a program asks: whether the key is there
    is what the entry's own first word says, and what it stands for is read from
    the entry either way.  Where the key is not there, the entry answered is the
    one after the last written -- where a key put in next would go, and empty:
    its first word says nothing is there, and its value, which a lookup reads
    unasked, is whatever the room held, which the truth value beside the answer
    says nobody may read.  That entry is always there to be read, the dense
    array having room for one more than the index may name (`room_for`), and
    its first word is written empty whenever it becomes the one after the last
    (`_build_new`).
    """
    table_ptr = table_type(module)
    held = key_ir_type(shape)
    func, fresh = generated(module, named(SLOT_SYMBOL, shape),
                             (table_ptr, held), table_ptr)
    if not fresh:
        return func
    entry = func.add_block()
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    key = entry.add_param(held, "key")
    mask = _read(builder, table, MASK_FIELD)
    index = _read_address(builder, table, INDEX_FIELD, U64)
    hashed = _hashed(builder, shape, key, _read(builder, table, SEED_FIELD))
    found, place, missing, _ = _probe(func, builder, shape, key, hashed, index,
                                      mask, threaded=False)

    builder.position_at(found)
    builder.ret(place)

    builder.position_at(missing)
    builder.ret(_entry(builder, _read_address(builder, table, ENTRIES_FIELD, U64),
                       _read(builder, table, USED_FIELD),
                       builder.int_const(U64, shape.stride)))
    return func


# -- rebuilding one --------------------------------------------------------------

def _build_grow(module: Module, shape: Shape) -> Function:
    """``__pl4g_table_grow(table)``: the table rebuilt, with room for more.

    **The keys are moved in the order they are in**, each to the next entry of
    the new dense array, which is what keeps the order they were put in through
    every rebuild; the entries given up are left behind, which is what clears
    the holes taking keys out made.  The index is built afresh beside them: a
    key is hashed with the table's new seed and put in the first word of its
    walk nothing holds.  No key is compared with another -- every key moved is
    different from every other -- so the walk asks one question of each word.

    **Twice the room where the keys themselves are what filled it**, which is
    where more than half of the index would hold them with one more, and the
    same room otherwise: then what filled it was holes -- at least a quarter of
    the index -- and rebuilding at the size it has is what clears them.  A
    table that lost as many keys as it gained would otherwise double for ever,
    and one rebuilt the moment it was full would be rebuilt again a key later.

    Its own function rather than a part of `put`, because it is what `put`
    almost never does: the loop that runs on every insertion is then not laid out
    around the one that runs on one in many.
    """
    table_ptr = table_type(module)
    func, fresh = generated(module, named(GROW_SYMBOL, shape), (table_ptr,), VOID)
    if not fresh:
        return func
    alloc = allocator_function(module)
    entry = func.add_block()
    bigger = func.add_block("bigger")
    afresh = func.add_block("afresh")
    sized = func.add_block("sized")
    walk = func.add_block("walk")
    look = func.add_block("look")
    move = func.add_block("move")
    seek = func.add_block("seek")
    skip = func.add_block("skip")
    settle = func.add_block("settle")
    onward = func.add_block("onward")
    done = func.add_block("done")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    stride = builder.int_const(U64, shape.stride)
    capacity = builder.binary(BinOp.WRAP_ADD, _read(builder, table, MASK_FIELD),
                              builder.int_const(U64, 1))
    old_used = _read(builder, table, USED_FIELD)
    old_entries = _read_address(builder, table, ENTRIES_FIELD, U64)
    keys = builder.binary(BinOp.WRAP_ADD, _read(builder, table, COUNT_FIELD),
                          builder.int_const(U64, 1))
    builder.condbr(builder.compare(CmpPred.UGT,
                                   builder.binary(BinOp.WRAP_MUL, keys,
                                                  builder.int_const(U64, 2)),
                                   capacity),
                   bigger, afresh)

    builder.position_at(bigger)
    builder.br(sized, (builder.binary(BinOp.WRAP_MUL, capacity,
                                      builder.int_const(U64, 2)),))

    builder.position_at(afresh)
    builder.br(sized, (capacity,))

    builder.position_at(sized)
    words = sized.add_param(U64, "words")
    # Three quarters of the index and one more, as `room_for` says.
    room = builder.binary(BinOp.WRAP_ADD,
                          builder.binary(BinOp.WRAP_SUB, words,
                                         builder.binary(BinOp.LSHR, words,
                                                        builder.int_const(U64, 2))),
                          builder.int_const(U64, 1))
    first = builder.binary(BinOp.WRAP_MUL, words, builder.int_const(U64, WORD))
    run = builder.call(
        alloc, (_read_address(builder, table, ARENA_FIELD, ARENA),
                builder.binary(BinOp.WRAP_ADD, first,
                               builder.binary(BinOp.WRAP_MUL, room, stride))),
        module.types.ptr_type(U8, mutable=True))
    index = _as(builder, run, U64)
    entries = _as(builder, builder.binary(BinOp.ADD, run, first), U64)
    mask = builder.binary(BinOp.WRAP_SUB, words, builder.int_const(U64, 1))
    # A seed of its own again: the keys are all being put somewhere new anyway,
    # and whatever could have been learned of where the old seed put them is
    # learned of a seed the table no longer has.
    seed = builder.call(seed_function(module), (), U64)
    _write_address(builder, table, INDEX_FIELD, index)
    _write_address(builder, table, ENTRIES_FIELD, entries)
    _write(builder, table, MASK_FIELD, mask)
    _write(builder, table, SEED_FIELD, seed)
    # The room may be the heap's given back and taken again, holding anything:
    # what the walk below reads of it before writing is the index.
    _clear_index(func, builder, index, words)
    builder.br(walk, (builder.int_const(U64, 0), builder.int_const(U64, 0),
                      builder.memory()))

    # Every entry of the old dense array, in order: `at` is where the walk is in
    # it, and `moved` how many keys the new one holds so far.
    builder.position_at(walk)
    at = walk.add_param(U64, "at")
    moved = walk.add_param(U64, "moved")
    builder.set_memory(walk.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.UGE, at, old_used), done, look,
                   true_args=(moved, builder.memory()),
                   false_args=(builder.memory(),))

    builder.position_at(look)
    builder.set_memory(look.add_param(MEM, "mem"))
    old_place = _entry(builder, old_entries, at, stride)
    builder.condbr(builder.compare(CmpPred.EQ, builder.load(old_place),
                                   builder.int_const(U64, LIVE)),
                   move, onward, true_args=(builder.memory(),),
                   false_args=(moved, builder.memory()))

    builder.position_at(move)
    builder.set_memory(move.add_param(MEM, "mem"))
    moved_key = _load_key(builder, shape, old_place)
    into = _entry(builder, entries, moved, stride)
    _write(builder, into, STATE_AT, builder.int_const(U64, LIVE))
    _store_key(builder, shape, into, moved_key)
    if shape.value is not None:
        _store_value(builder, shape, into,
                     _load_value(builder, shape, old_place))
    hashed = _hashed(builder, shape, moved_key, seed)
    builder.br(seek, (builder.binary(BinOp.AND, hashed, mask), builder.memory()))

    builder.position_at(seek)
    where = seek.add_param(U64, "where")
    builder.set_memory(seek.add_param(MEM, "mem"))
    word = _entry(builder, index, where, builder.int_const(U64, WORD))
    builder.condbr(builder.compare(CmpPred.EQ, builder.load(word),
                                   builder.int_const(U64, 0)),
                   settle, skip, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(skip)
    builder.set_memory(skip.add_param(MEM, "mem"))
    builder.br(seek, (_stepped(builder, where, mask), builder.memory()))

    builder.position_at(settle)
    builder.set_memory(settle.add_param(MEM, "mem"))
    builder.store(word, _index_word(builder, hashed,
                                    builder.binary(BinOp.WRAP_ADD, first,
                                                   builder.binary(BinOp.WRAP_MUL,
                                                                  moved, stride))))
    builder.br(onward, (builder.binary(BinOp.WRAP_ADD, moved,
                                       builder.int_const(U64, 1)),
                        builder.memory()))

    builder.position_at(onward)
    kept = onward.add_param(U64, "kept")
    builder.set_memory(onward.add_param(MEM, "mem"))
    builder.br(walk, (builder.binary(BinOp.WRAP_ADD, at, builder.int_const(U64, 1)),
                      kept, builder.memory()))

    # No holes in the new array: as many entries written as there are keys,
    # and the one after them, which a missing key's lookup reads, empty.
    builder.position_at(done)
    total = done.add_param(U64, "total")
    builder.set_memory(done.add_param(MEM, "mem"))
    _write(builder, table, COUNT_FIELD, total)
    _write(builder, table, USED_FIELD, total)
    _write(builder, _entry(builder, entries, total, stride), STATE_AT,
           builder.int_const(U64, EMPTY))
    builder.ret()
    return func


def _index_word(builder: IRBuilder, hashed: Value, offset: Value) -> Value:
    """The word of the index naming the entry *offset* bytes past its start,
    whose key hashed to *hashed*."""
    return builder.binary(BinOp.OR,
                          builder.binary(BinOp.AND, hashed,
                                         builder.int_const(U64, HASH_TOP)),
                          offset)


# -- putting a key in ------------------------------------------------------------

def _build_put(module: Module, shape: Shape) -> Function:
    """``__pl4g_table_put(table, key)``: the entry for the key, made if it is new.

    **A new key goes last**: into the entry after the last written, with the
    word of the index its probe stopped at naming it.  That is the whole of
    keeping the order keys were put in -- a key already there keeps its entry and
    has its value replaced where it is, and a key put back after it was taken
    out is a new key, its old entry a hole the walk steps past.

    The table is rebuilt *before* the probe rather than after it, so that the
    entry answered with is one of the new array's and not one of an array the
    caller is about to be handed a pointer past.  Rebuilding when the key turns
    out to be there already merely rebuilds a little early, which costs a
    rebuilding that would have happened anyway.

    **What crowds a table is what a probe must walk past**, which is a word of
    the index for every entry written -- the keys it holds *and* the holes -- so
    that is what the load is measured on, and what `__pl4g_table_grow` then
    decides between doubling and clearing the holes by.

    Nothing but the memory token travels on a conditional branch here, and a
    memory token is nowhere: it says which path's ordering of the operations on
    memory holds from here, and costs no instruction to hand over.
    """
    table_ptr = table_type(module)
    held = key_ir_type(shape)
    func, fresh = generated(module, named(PUT_SYMBOL, shape),
                             (table_ptr, held), table_ptr)
    if not fresh:
        return func
    grow_it = _build_grow(module, shape)
    entry = func.add_block()
    # Before everything else, being what everything else reads: the blocks are
    # laid out in the order they are made.
    seeding = func.add_block("seeding")
    seeded = func.add_block("seeded")
    grow = func.add_block("grow")
    ready = func.add_block("ready")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    key = entry.add_param(held, "key")
    # A table laid out in the image empty has no seed of its own yet: the
    # first key is what it takes one for, and it has no entries to move.
    builder.condbr(builder.compare(CmpPred.EQ, _read(builder, table, SEED_FIELD),
                                   builder.int_const(U64, 0)),
                   seeding, seeded, false_args=(builder.memory(),))

    builder.position_at(seeding)
    _write(builder, table, SEED_FIELD,
           builder.call(seed_function(module), (), U64))
    builder.br(seeded, (builder.memory(),))

    builder.position_at(seeded)
    builder.set_memory(seeded.add_param(MEM, "mem"))
    # One more word of the index than is written now, which is what it would
    # hold if this key is new.
    wanted = builder.binary(BinOp.WRAP_ADD, _read(builder, table, USED_FIELD),
                            builder.int_const(U64, 1))
    capacity = builder.binary(BinOp.WRAP_ADD, _read(builder, table, MASK_FIELD),
                              builder.int_const(U64, 1))
    crowded = builder.compare(
        CmpPred.UGT,
        builder.binary(BinOp.WRAP_MUL, wanted,
                       builder.int_const(U64, LOAD_DENOMINATOR)),
        builder.binary(BinOp.WRAP_MUL, capacity,
                       builder.int_const(U64, LOAD_NUMERATOR)))
    builder.condbr(crowded, grow, ready, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(grow)
    builder.set_memory(grow.add_param(MEM, "mem"))
    builder.call(grow_it, (table,), VOID)
    builder.br(ready, (builder.memory(),))

    # -- and now the entry itself ------------------------------------------------
    builder.position_at(ready)
    builder.set_memory(ready.add_param(MEM, "mem"))
    mask = _read(builder, table, MASK_FIELD)
    index = _read_address(builder, table, INDEX_FIELD, U64)
    hashed = _hashed(builder, shape, key, _read(builder, table, SEED_FIELD))
    found, place, missing, at = _probe(func, builder, shape, key, hashed, index,
                                       mask, threaded=True)

    builder.position_at(found)
    builder.ret(place)

    # Where the next entry is is how far past the index it is: the index's own
    # words, and the entries written so far.
    builder.position_at(missing)
    used = _read(builder, table, USED_FIELD)
    offset = builder.binary(
        BinOp.WRAP_ADD,
        builder.binary(BinOp.WRAP_MUL,
                       builder.binary(BinOp.WRAP_ADD, mask,
                                      builder.int_const(U64, 1)),
                       builder.int_const(U64, WORD)),
        builder.binary(BinOp.WRAP_MUL, used, builder.int_const(U64, shape.stride)))
    made = builder.binary(BinOp.ADD, index, offset)
    builder.store(_entry(builder, index, at, builder.int_const(U64, WORD)),
                  _index_word(builder, hashed, offset))
    _write(builder, made, STATE_AT, builder.int_const(U64, LIVE))
    _store_key(builder, shape, made, key)
    # The entry after it is where the next key goes and what a lookup of a
    # missing key reads: empty, whatever the room held before the table had it.
    # It is there to be written -- the dense array has room for one entry more
    # than the index may name, and the table was rebuilt above if this one
    # would have been the last.
    _write(builder, builder.binary(BinOp.ADD, made,
                                   builder.int_const(U64, shape.stride)),
           STATE_AT, builder.int_const(U64, EMPTY))
    _write(builder, table, USED_FIELD,
           builder.binary(BinOp.WRAP_ADD, used, builder.int_const(U64, 1)))
    _write(builder, table, COUNT_FIELD,
           builder.binary(BinOp.WRAP_ADD, _read(builder, table, COUNT_FIELD),
                          builder.int_const(U64, 1)))
    builder.ret(made)
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
    func, fresh = generated(module, named(SELECT_SYMBOL, shape),
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
    # The walked table's entries in the order they were put in, which is the
    # order the answer gets them in: what is in either is the left's keys and
    # then the right's new ones, as Python's `a | b` has them.
    used = _read(builder, walked, USED_FIELD)
    stride = builder.int_const(U64, shape.stride)
    entries = _read_address(builder, walked, ENTRIES_FIELD, U64)
    builder.br(walk, (builder.int_const(U64, 0), builder.memory()))

    builder.position_at(walk)
    at = walk.add_param(U64, "at")
    builder.set_memory(walk.add_param(MEM, "mem"))
    builder.condbr(builder.compare(CmpPred.UGE, at, used), done, look,
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


# -- taking one out --------------------------------------------------------------

GIVE_UP_SYMBOL: Final[str] = "__pl4g_table_give_up"


def ensure_take(module: Module, shape: Shape) -> Function:
    """Build what taking a key out of a table is made of."""
    ensure_runtime(module, shape)
    return _build_give_up(module)


def _build_give_up(module: Module) -> Function:
    """``__pl4g_table_give_up(table, place)``: the entry holds no key any more.

    Given up rather than emptied: the word of the index naming it goes on
    naming it, so that a probe walking past it to what was put in after it
    still walks past it, and a walk of the dense array steps past the hole.
    Nothing is put in that entry again -- a key put in next goes last, whatever
    it is -- and the hole is left behind when the table is next rebuilt.

    **The entry is not cleared.**  Its key and its value are still there when
    this answers, which is what lets the one who took it out read what was
    under it.

    **Nothing happens where the entry holds no key.**  Taking out a key that is
    not there is not an error and not a count going wrong: the probe answered
    with the entry the key *would* go in, which is empty, and it is left as it
    was.

    One for every table rather than one per shape, since neither the key nor the
    value is touched: what it reads and writes is the entry's first word and the
    count, and both are the same in every table.
    """
    table_ptr = table_type(module)
    func, fresh = generated(module, GIVE_UP_SYMBOL, (table_ptr, table_ptr), VOID)
    if not fresh:
        return func
    entry = func.add_block()
    held = func.add_block("held")
    done = func.add_block("done")
    builder = IRBuilder(module, func)
    builder.position_at(entry)
    table = entry.add_param(table_ptr, "table")
    place = entry.add_param(table_ptr, "place")
    builder.condbr(builder.compare(CmpPred.EQ, builder.load(place),
                                   builder.int_const(U64, LIVE)),
                   held, done, true_args=(builder.memory(),),
                   false_args=(builder.memory(),))

    builder.position_at(held)
    builder.set_memory(held.add_param(MEM, "mem"))
    _write(builder, place, STATE_AT, builder.int_const(U64, GONE))
    # One key fewer; what a probe must walk past is unchanged, which is the
    # whole point of giving the entry up rather than emptying it.
    _write(builder, table, COUNT_FIELD,
           builder.binary(BinOp.WRAP_SUB, _read(builder, table, COUNT_FIELD),
                          builder.int_const(U64, 1)))
    builder.br(done, (builder.memory(),))

    builder.position_at(done)
    builder.set_memory(done.add_param(MEM, "mem"))
    builder.ret()
    return func


# -- walking one ------------------------------------------------------------------

NEXT_SYMBOL: Final[str] = "__pl4g_table_next"


def _build_next(module: Module) -> Function:
    """``__pl4g_table_next(table, from)``: where the next entry holding a key is.

    Answered as a place in the dense array of entries, at or after *from*, and as
    the number of entries written where there is none -- which is the same
    number a walk compares against to know it is done, so a caller needs no
    second answer for "there is no next one".

    **A table is walked in the order its keys were first put in it**, which is
    the order of the dense array: a walk steps through it and past the holes
    taking keys out left.  Python's dictionaries have walked so since 3.6 and
    promised it since 3.7, for the same layout; Go deliberately randomises its
    walk, and Rust's `HashMap` and C++'s `unordered_map` walk in whatever order
    their buckets are in.  A table read and written back -- a JSON object, a
    configuration -- then comes out as it went in.
    """
    table_ptr = table_type(module)
    func, fresh = generated(module, NEXT_SYMBOL, (table_ptr, U64), U64)
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
    used = _read(builder, table, USED_FIELD)
    stride = _read(builder, table, STRIDE_FIELD)
    entries = _read_address(builder, table, ENTRIES_FIELD, U64)
    builder.br(walk, (start,))

    builder.position_at(walk)
    at = walk.add_param(U64, "at")
    builder.condbr(builder.compare(CmpPred.UGE, at, used), done, look)

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
