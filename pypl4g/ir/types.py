"""IR types.

No type ever carries a size, an alignment or a field offset.  The specification
lets the compiler reorder the fields of a product type for efficiency, so layout
is a property computed late and held beside the type, never inside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final, Sequence


@dataclass(frozen=True, slots=True)
class Type:
    """Base of every IR type.  Instances compare by structure and are interned."""

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        raise NotImplementedError

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name.

        It differs from ``render`` only in carrying no spaces and no glyphs, so
        that a symbol stays one word.  For every primitive type the two are the
        same string.
        """
        return self.render()


@dataclass(frozen=True, slots=True)
class VoidType(Type):
    """The unit type: one value, no bits."""

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "void"


@dataclass(frozen=True, slots=True)
class BoolType(Type):
    """Truth values.

    Deliberately not a one-bit integer: the backend chooses the representation,
    and the discriminant of a sum type must stay distinguishable from it.
    """

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "bool"


@dataclass(frozen=True, slots=True)
class CharType(Type):
    """One Unicode code point, whatever it takes to write it down elsewhere.

    Held as an unsigned thirty-two bit number, which is what every code point
    fits in with room to spare -- the last of them is U+10FFFF, so eleven of the
    thirty-two bits are always zero.  Those bits are not used for anything: a
    type whose values are code points and a type whose values are numbers are
    two different types, and the room is what makes every code point one value
    rather than a pair.

    It is deliberately not an integer type.  Adding two of them is not a
    character, and neither is a third of one; what the ordering means is the
    order the code points are numbered in, which is a real order and the one
    every collation starts from, so the comparisons are defined and nothing else
    is.  `\N{APL FUNCTIONAL SYMBOL QUAD}ord` is how a program reaches the number, which it has to ask for.
    """

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "char"

    @property
    def holder(self) -> IntType:
        """The integer type one is held as, which is what says how it is stored
        and how it is read out of a register."""
        return U32


@dataclass(frozen=True, slots=True)
class IntType(Type):
    """An integer of a given width and signedness.

    The width is any number of bits up to sixty-four and not only the four a
    machine has registers for.  What that costs is stated once, here: a value
    of such a type is *held* in the narrowest machine width that contains it,
    with every bit above its own width equal to the zero- or sign-extension of
    the value.  Everything else follows -- what it takes in memory, which
    instruction operates on it, and where the check that an answer fits has to
    come from, since the flags a machine writes are about the width it worked
    at and not about the width the type has.
    """

    bits: int
    signed: bool

    #: Every one of them that has been made, so that two asks for one width
    #: answer with the one type.  `TypeContext` interns the types that are
    #: built out of others for the same reason and says why: identity
    #: comparison is what the rest of the compiler asks these with, and there
    #: are now sixty-odd of these rather than eight.
    _made: ClassVar[dict[tuple[int, bool], IntType]] = {}

    def __new__(cls, bits: int, signed: bool = False) -> IntType:
        found = cls._made.get((bits, signed))
        if found is None:
            found = super().__new__(cls)
            cls._made[(bits, signed)] = found
        return found

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("i" if self.signed else "u", str(self.bits)))

    @property
    def held(self) -> int:
        """How many bits the register and the place in memory holding one have.

        The narrowest a machine has that contains the type: a byte for anything
        up to eight bits, and then doubling.  There is no width below a byte
        because there is no register and no load below a byte, and a type that
        occupied part of one would be a bit field -- a different thing, with a
        different question about what lies beside it.
        """
        for width in (8, 16, 32, 64):
            if self.bits <= width:
                return width
        raise AssertionError(self.bits)

    @property
    def whole(self) -> bool:
        """Whether it fills what holds it, which is where nothing has to be put
        back after an operation and where a machine's own flags answer whether
        an answer fits."""
        return self.bits == self.held

    @property
    def low(self) -> int:
        """The smallest value this type can represent."""
        return -(1 << (self.bits - 1)) if self.signed else 0

    @property
    def high(self) -> int:
        """The largest value this type can represent."""
        return (1 << (self.bits - 1)) - 1 if self.signed else (1 << self.bits) - 1

    def holds(self, value: int) -> bool:
        """Whether *value* is representable in this type."""
        return self.low <= value <= self.high


@dataclass(frozen=True, slots=True)
class FloatType(Type):
    """A binary floating-point type."""

    bits: int

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("f", str(self.bits)))


@dataclass(frozen=True, slots=True)
class PtrType(Type):
    """A pointer to a value of another type.

    Whether what it points at may be changed is part of the pointer's type, not
    of the thing it points at: a value is a value, and it is the *place* that is
    writable or not.  A variable in memory is a pointer, so this is where the
    language's ``mut`` ends up.
    """

    pointee: Type
    mutable: bool = False

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("ptr<", "mut " if self.mutable else "",
                        self.pointee.render(), ">"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("ptr<", "mut " if self.mutable else "",
                        self.pointee.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class FuncType(Type):
    """The type of a function."""

    params: tuple[Type, ...]
    ret: Type

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        inner = ", ".join(p.render() for p in self.params)
        return "".join(("fn(", inner, ") \N{RIGHTWARDS ARROW} ", self.ret.render()))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name.

        A function type is spelled the way a function symbol is: the parameters
        in parentheses, then the result.
        """
        inner = ",".join(p.mangled() for p in self.params)
        return "".join(("fn(", inner, ")", self.ret.mangled()))


@dataclass(frozen=True, slots=True)
class MemType(Type):
    """The token type that threads memory effects through the dataflow graph.

    Making the ordering of memory operations explicit in the graph is what turns
    the specification's requirement -- that no implicit dependency such as memory
    aliasing may force an order -- into something a pass can check rather than
    merely assume.
    """

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "mem"


@dataclass(frozen=True, slots=True)
class ErrorType(Type):
    """Stands in for a type that could not be worked out.

    It exists so that one mistake is reported once: a value of this type matches
    anything, so nothing downstream reports a second complaint about a value
    that was already the subject of a first.  It never appears in a module that
    verified, because a module containing one has already failed to compile.
    """

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "<error>"


@dataclass(frozen=True, slots=True)
class ProductType(Type):
    """A record.  The field order here is the declaration order, not a layout.

    A type a program defined is **nominal**: two definitions with the same
    fields are two types, because a definition is what says what a value *is*
    and two things that happen to be laid out alike are not one thing.  That is
    what `name` and `origin` are for -- the name as the source wrote it, and the
    file that wrote it, so that two files each defining `Point` define two.
    """

    fields: tuple[tuple[str, Type], ...]
    #: The name the program gave it, or nothing for one the compiler made.
    name: str = ""
    #: The file the definition is in, which is part of which type this is and
    #: no part of what it is called.
    origin: str = ""

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        if self.name:
            return self.name
        inner = ", ".join("".join((n, ": ", t.render())) for n, t in self.fields)
        return "".join(("{", inner, "}"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name.

        The fields appear in the order they were declared.  A later pass may
        reorder the *layout* for efficiency, but the declaration is what
        identifies the type, so that is what the name records.
        """
        if self.name:
            return self.name
        inner = ",".join("".join((n, ":", t.mangled())) for n, t in self.fields)
        return "".join(("{", inner, "}"))


@dataclass(frozen=True, slots=True)
class ResultType(Type):
    """A value of one type, or the fact that it could not be produced.

    The sum type in the one shape the language needs before it has sum types:
    two variants, one of which is the answer and the other of which says there
    is none.  Where the error carries nothing -- which is every result the
    compiler produces so far -- ``err`` is nothing and the value is the answer
    and one truth value beside it.
    """

    ok: Type
    err: Type | None = None

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join((self.ok.render(), "?",
                        self.err.render() if self.err is not None else ""))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join((self.ok.mangled(), "?",
                        self.err.mangled() if self.err is not None else ""))


@dataclass(frozen=True, slots=True)
class TupleType(Type):
    """Several values travelling as one, reached by position rather than by name.

    A product with no names, which is what makes it the right thing for a
    function that answers with two of something: naming the parts of an answer
    that is taken apart on the spot would be naming something that does not
    outlive the line it is written on.
    """

    members: tuple[Type, ...]

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("\N{LEFT ANGLE BRACKET}",
                        ", ".join(m.render() for m in self.members),
                        "\N{RIGHT ANGLE BRACKET}"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("tuple<", ",".join(m.mangled() for m in self.members), ">"))


@dataclass(frozen=True, slots=True)
class ArrayType(Type):
    """Several values of one type, laid out one after another.

    `shape` is how many along each dimension: one number for a vector, two for
    a table, and so on.  A dimension is a number where the type says how many
    and nothing where it does not, and the two are not mixed within one type --
    an array either carries its shape in the type or carries the whole of it
    beside the elements.

    A type that says carries everything about the array but the elements
    themselves, so a value of one needs no room beyond theirs.  A type that
    does not is a place and one count per dimension, and says nothing about
    where the elements are -- they may be an array's, or part of one.

    The elements are in row-major order: the last dimension is the one whose
    neighbours are next to each other.  That is what every language but Fortran
    does, and it is what makes taking a row out of a table a run of elements
    rather than a stride.
    """

    element: Type
    shape: tuple[int | None, ...] = (None,)

    @property
    def rank(self) -> int:
        """How many dimensions it has, which is at least one."""
        return len(self.shape)

    @property
    def fixed(self) -> bool:
        """Whether the type says how many elements there are."""
        return all(along is not None for along in self.shape)

    @property
    def count(self) -> int | None:
        """How many elements in all, where the type says."""
        if not self.fixed:
            return None
        total = 1
        for along in self.shape:
            assert along is not None
            total *= along
        return total

    def _dimensions(self) -> str:
        """The shape as it is written between the brackets."""
        return ",".join("" if along is None else str(along)
                        for along in self.shape)

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join((self.element.render(),
                        "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}", self._dimensions(),
                        "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("array<", self.element.mangled(), ",",
                        self._dimensions(), ">"))


@dataclass(frozen=True, slots=True)
class VecType(Type):
    """Several values of one type held as one value, side by side.

    This is what an array becomes where an operation is done to every element of
    it at once: a value of one is a whole register on a machine that has such
    registers, and the operation on it is one instruction.  It is never a type
    the language can write down.  The front end makes one where an operator
    reaches every element of a run, and a backend that cannot do the operation
    to a whole one takes it apart again -- so the meaning of a program does not
    depend on what the machine can do, only how many instructions it takes.

    In memory it is exactly the run of elements it came from: packed, in order,
    and aligned no more than they are, since what it reads is an array that was
    laid out without knowing this would read it.
    """

    element: Type
    lanes: int

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join((self.element.render(), "\N{MULTIPLICATION SIGN}",
                        str(self.lanes)))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("vec<", self.element.mangled(), ",", str(self.lanes), ">"))


@dataclass(frozen=True, slots=True)
class StrType(Type):
    """Text, encoded as UTF-8 and nothing else.

    A value of one is where the bytes are and how many there are -- two words,
    the same shape an array whose type does not say its length has.  It owns
    nothing: the bytes are a literal's, in the image, or an arena's, put there
    by something that made a string out of two others.

    **Every value of it is well-formed UTF-8**, which is an invariant and not a
    hope.  The only two ways to make one are a literal, whose bytes are what the
    source held and which the compiler encoded itself, and joining two of them,
    which puts well-formed bytes after well-formed bytes.  So nothing that reads
    one has to check it, and a walk over the characters is a decoder and not a
    validator -- which is where nearly all of the cost of walking text usually
    goes.

    There is no index.  The *n*-th byte of UTF-8 is not the *n*-th character and
    a type that let one be asked for would be a type whose obvious use is wrong;
    `foreach` is how the characters are reached, and it reaches them in order
    because that is the order they are encoded in.
    """

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "str"


@dataclass(frozen=True, slots=True)
class ListType(Type):
    """Several values one after another, however many there turn out to be.

    A value of one is where the elements are and how many there are -- two
    words, the shape a string and an array of unstated length both have.  The
    elements live in an arena, because how many there are is not a property of
    the type and room for them cannot be taken where the list is written.

    **The element type is in the type, and that is temporary and deliberate.**
    A list is the sequence whose elements need not be of one type; what makes
    that work is boxing, which this compiler does not do yet, so for now they
    must be.  Recording the one type they are is what makes the case where they
    happen to agree recognisable later: a list of one type is the case worth not
    boxing, and a compiler that had thrown the type away could not find it.
    """

    element: Type

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("[", self.element.render(), "]"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("list<", self.element.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class SetType(Type):
    """A set: the keys it holds, and nothing said about them beyond membership."""

    element: Type

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("\N{LEFT DOUBLE PARENTHESIS}", self.element.render(), "\N{RIGHT DOUBLE PARENTHESIS}"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("set<", self.element.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class DictType(Type):
    """A dictionary: what a key is, and what it stands for."""

    key: Type
    value: Type

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("\N{LEFT DOUBLE PARENTHESIS}", self.key.render(), ": ",
                        self.value.render(), "\N{RIGHT DOUBLE PARENTHESIS}"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("dict<", self.key.mangled(), ",", self.value.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class EnumType(Type):
    """A fixed set of named values, and nothing else.

    Nominal, as a product and a sum are, and for the same reason.  `holder` is
    how much room a value takes and says nothing else: a value of this type is
    not a number of that type and does not become one, which is what keeps an
    enumeration from being an integer with a nicer spelling.
    """

    members: tuple[str, ...]
    #: The number each value is stored as, in the order the names are written.
    #: Two names may share one where a definition says so outright.
    values: tuple[int, ...]
    holder: IntType
    #: Whether its values are meant to be combined, which is what `@[flag]`
    #: says.  A flag enumeration has more values than it has names, and the
    #: bitwise operators are defined on it for that reason.
    flag: bool = False
    name: str = ""
    origin: str = ""

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        if self.name:
            return self.name
        return "".join(("enum<", ", ".join(self.members), ">"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        if self.name:
            return self.name
        return "".join(("enum<", ",".join(self.members), ">"))

    def index_of(self, name: str) -> int | None:
        """Which of the names *name* is, or nothing where it is not one."""
        try:
            return self.members.index(name)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class SumType(Type):
    """A choice between named variants, the basis of the language's error model.

    Nominal for the same reason a product is, and by the same two fields.
    """

    variants: tuple[tuple[str, Type], ...]
    #: The name the program gave it, or nothing for one the compiler made.
    name: str = ""
    #: The file the definition is in, which is part of which type this is.
    origin: str = ""

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        if self.name:
            return self.name
        inner = " | ".join("".join((n, ": ", t.render())) for n, t in self.variants)
        return "".join(("<", inner, ">"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        if self.name:
            return self.name
        inner = "|".join("".join((n, ":", t.mangled())) for n, t in self.variants)
        return "".join(("<", inner, ">"))


VOID: Final[VoidType] = VoidType()
BOOL: Final[BoolType] = BoolType()
MEM: Final[MemType] = MemType()
ERROR: Final[ErrorType] = ErrorType()

I8: Final[IntType] = IntType(8, True)
I16: Final[IntType] = IntType(16, True)
I32: Final[IntType] = IntType(32, True)
I64: Final[IntType] = IntType(64, True)
U8: Final[IntType] = IntType(8, False)
U16: Final[IntType] = IntType(16, False)
U32: Final[IntType] = IntType(32, False)
U64: Final[IntType] = IntType(64, False)
F32: Final[FloatType] = FloatType(32)
F64: Final[FloatType] = FloatType(64)

CHAR: Final[CharType] = CharType()
STR: Final[StrType] = StrType()

#: The last code point there is.  Unicode says so and will not say otherwise:
#: the range was fixed at this when UTF-16 was given its surrogate pairs, which
#: can reach no further, and every encoding has had to agree with it since.
MAX_CODE_POINT: Final[int] = 0x10FFFF

#: The types the language names directly, in the order they are documented.
#: What the allocator's own record holds: the first byte not yet handed out,
#: one past the end of the chunk it is in, and the head of the chunk list.  It
#: is a type the language names, because an arena is a thing a program makes
#: and gives to a collection, and it is a record rather than anything of the
#: compiler's own so that its layout is computed the way every layout is.
ARENA_NAME: Final[str] = "arena"

ARENA: Final[ProductType] = ProductType(
    (("next", U64), ("limit", U64), ("chunk", U64)), name=ARENA_NAME)


#: Which widths each signedness has a type for.  Unsigned from one bit, signed
#: from two: a signed type of one bit holds zero and minus one, which is a pair
#: of values no program wants and a name every reader would misread.
UNSIGNED_WIDTHS: Final[tuple[int, ...]] = (*range(1, 33), 64)
SIGNED_WIDTHS: Final[tuple[int, ...]] = (*range(2, 33), 64)

BUILTIN_TYPES: Final[dict[str, Type]] = {
    **{"".join(("u", str(bits))): IntType(bits, False)
       for bits in UNSIGNED_WIDTHS},
    **{"".join(("i", str(bits))): IntType(bits, True) for bits in SIGNED_WIDTHS},
    "f32": F32, "f64": F64,
    "bool": BOOL, "char": CHAR, "str": STR, "void": VOID,
    ARENA_NAME: ARENA,
}


class TypeContext:
    """Interns constructed types so that identity comparison is valid."""

    def __init__(self) -> None:
        self._results: dict[tuple[Type, Type | None], ResultType] = {}
        self._tuples: dict[tuple[Type, ...], TupleType] = {}
        self._arrays: dict[tuple[Type, tuple[int | None, ...]], ArrayType] = {}
        self._vectors: dict[tuple[Type, int], VecType] = {}
        self._lists: dict[Type, ListType] = {}
        self._sets: dict[Type, SetType] = {}
        self._dicts: dict[tuple[Type, Type], DictType] = {}
        self._pointers: dict[tuple[Type, bool], PtrType] = {}
        self._functions: dict[tuple[tuple[Type, ...], Type], FuncType] = {}
        self._integers: dict[tuple[int, bool], IntType] = {
            (t.bits, t.signed): t for t in (I8, I16, I32, I64, U8, U16, U32, U64)}

    #: How much room an enumeration takes where its definition does not say.
    #: The smallest unsigned type that holds every one of its values, the values
    #: being numbered from zero in the order they were written.
    HOLDERS: Final[tuple[tuple[int, int], ...]] = (
        (1 << 8, 8), (1 << 16, 16), (1 << 32, 32), (1 << 64, 64))

    def holder_for(self, count: int) -> IntType:
        """The smallest unsigned type that holds *count* values."""
        for limit, bits in self.HOLDERS:
            if count <= limit:
                return self.int_type(bits, False)
        return self.int_type(64, False)

    def tuple_type(self, members: Sequence[Type]) -> TupleType:
        """Return the tuple type over *members*."""
        key = tuple(members)
        found = self._tuples.get(key)
        if found is None:
            found = TupleType(key)
            self._tuples[key] = found
        return found

    def array_type(self, element: Type,
                   shape: Sequence[int | None] = (None,)) -> ArrayType:
        """Return the array type over *element* with this shape."""
        key = (element, tuple(shape))
        found = self._arrays.get(key)
        if found is None:
            found = ArrayType(element, tuple(shape))
            self._arrays[key] = found
        return found

    def vec_type(self, element: Type, lanes: int) -> VecType:
        """Return the vector type of *lanes* values of *element*."""
        key = (element, lanes)
        found = self._vectors.get(key)
        if found is None:
            found = VecType(element, lanes)
            self._vectors[key] = found
        return found

    def list_type(self, element: Type) -> ListType:
        """Return the list type over *element*."""
        found = self._lists.get(element)
        if found is None:
            found = ListType(element)
            self._lists[element] = found
        return found

    def set_type(self, element: Type) -> SetType:
        """Return the set type over *element*."""
        found = self._sets.get(element)
        if found is None:
            found = SetType(element)
            self._sets[element] = found
        return found

    def dict_type(self, key: Type, value: Type) -> DictType:
        """Return the dictionary type from *key* to *value*."""
        found = self._dicts.get((key, value))
        if found is None:
            found = DictType(key, value)
            self._dicts[(key, value)] = found
        return found

    def result_type(self, ok: Type, err: Type | None = None) -> ResultType:
        """Return the result type with this answer type and error type."""
        key = (ok, err)
        found = self._results.get(key)
        if found is None:
            found = ResultType(ok, err)
            self._results[key] = found
        return found

    def int_type(self, bits: int, signed: bool) -> IntType:
        """Return the integer type of the given width and signedness."""
        key = (bits, signed)
        found = self._integers.get(key)
        if found is None:
            found = IntType(bits, signed)
            self._integers[key] = found
        return found

    def ptr_type(self, pointee: Type, mutable: bool = False) -> PtrType:
        """Return the pointer type to *pointee*."""
        key = (pointee, mutable)
        found = self._pointers.get(key)
        if found is None:
            found = PtrType(pointee, mutable)
            self._pointers[key] = found
        return found

    def func_type(self, params: tuple[Type, ...], ret: Type) -> FuncType:
        """Return the function type with the given signature."""
        key = (params, ret)
        found = self._functions.get(key)
        if found is None:
            found = FuncType(params, ret)
            self._functions[key] = found
        return found

    def builtin(self, name: str) -> Type | None:
        """Return the built-in type named *name*, if there is one."""
        return BUILTIN_TYPES.get(name)


#: The pointer types the parts of a dynamic array are made of, kept so that two
#: asks for one answer with the same type.  `parts_of` has no type context to
#: ask, and a fresh one each time would be a type that compares equal to the
#: interned one and is not it.
_ELEMENT_POINTERS: dict[Type, PtrType] = {}


def _pointer_to(element: Type) -> PtrType:
    """The type of an address of an element, made once per element type."""
    found = _ELEMENT_POINTERS.get(element)
    if found is None:
        found = PtrType(element, mutable=True)
        _ELEMENT_POINTERS[element] = found
    return found


def parts_of(ty: Type) -> tuple[Type, ...]:
    """What a value of *ty* is, where it is more than one value travelling as one.

    A result is its answer and the truth value beside it; a tuple is its
    members; an array whose type does not say its shape is where the elements
    are and one count per dimension; anything else is itself.  Everything that
    has to say where such a value goes -- a register, an argument, an answer --
    asks this rather than knowing the shapes, so a shape added later is added
    here.

    An array whose type *does* say how many is not among them: it is its
    elements and nothing else, which is a place in memory and never a register.
    """
    if isinstance(ty, ResultType):
        # The answer, whether there is one, and -- where the error carries a
        # value of its own -- that value.  The truth value stays the *second*
        # part whatever else there is, because everything that reads one asks
        # for it by that place and a part added after it changes nothing.
        return (ty.ok, BOOL) if ty.err is None else (ty.ok, BOOL, ty.err)
    if isinstance(ty, TupleType):
        return ty.members
    if isinstance(ty, ArrayType) and not ty.fixed:
        return (_pointer_to(ty.element), *(U64 for _ in ty.shape))
    if isinstance(ty, ListType):
        # Where the elements are and how many there are, which is the shape a
        # string and an array of unstated length both have and for the same
        # reason: how many is not in the type.
        return (_pointer_to(ty.element), U64)
    if isinstance(ty, StrType):
        # Where the bytes are and how many there are, which is what an array
        # whose type does not say its length is as well -- the difference
        # between the two is what may be done with them, not what they are.
        return (_pointer_to(U8), U64)
    return (ty,)
