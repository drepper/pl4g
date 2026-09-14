"""IR types.

No type ever carries a size, an alignment or a field offset.  The specification
lets the compiler reorder the fields of a product type for efficiency, so layout
is a property computed late and held beside the type, never inside it.
"""

from dataclasses import dataclass
from typing import Final, Sequence


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
class IntType(Type):
    """An integer of a given width and signedness."""

    bits: int
    signed: bool

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("i" if self.signed else "u", str(self.bits)))

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
    def count(self) -> "int | None":
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

#: The types the language names directly, in the order they are documented.
#: What the allocator's own record holds: the first byte not yet handed out,
#: one past the end of the chunk it is in, and the head of the chunk list.  It
#: is a type the language names, because an arena is a thing a program makes
#: and gives to a collection, and it is a record rather than anything of the
#: compiler's own so that its layout is computed the way every layout is.
ARENA_NAME: Final[str] = "arena"

ARENA: Final[ProductType] = ProductType(
    (("next", U64), ("limit", U64), ("chunk", U64)), name=ARENA_NAME)


BUILTIN_TYPES: Final[dict[str, Type]] = {
    "i8": I8, "i16": I16, "i32": I32, "i64": I64,
    "u8": U8, "u16": U16, "u32": U32, "u64": U64,
    "f32": F32, "f64": F64,
    "bool": BOOL, "void": VOID,
    ARENA_NAME: ARENA,
}


class TypeContext:
    """Interns constructed types so that identity comparison is valid."""

    def __init__(self) -> None:
        self._results: dict[tuple[Type, Type | None], ResultType] = {}
        self._tuples: dict[tuple[Type, ...], TupleType] = {}
        self._arrays: dict[tuple[Type, tuple[int | None, ...]], ArrayType] = {}
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

    def tuple_type(self, members: "Sequence[Type]") -> TupleType:
        """Return the tuple type over *members*."""
        key = tuple(members)
        found = self._tuples.get(key)
        if found is None:
            found = TupleType(key)
            self._tuples[key] = found
        return found

    def array_type(self, element: Type,
                   shape: "Sequence[int | None]" = (None,)) -> ArrayType:
        """Return the array type over *element* with this shape."""
        key = (element, tuple(shape))
        found = self._arrays.get(key)
        if found is None:
            found = ArrayType(element, tuple(shape))
            self._arrays[key] = found
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
_ELEMENT_POINTERS: "dict[Type, PtrType]" = {}


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
        return (ty.ok, BOOL)
    if isinstance(ty, TupleType):
        return ty.members
    if isinstance(ty, ArrayType) and not ty.fixed:
        return (_pointer_to(ty.element), *(U64 for _ in ty.shape))
    return (ty,)
