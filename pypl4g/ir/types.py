"""IR types.

No type ever carries a size, an alignment or a field offset.  The specification
lets the compiler reorder the fields of a product type for efficiency, so layout
is a property computed late and held beside the type, never inside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import ClassVar, Final, Sequence


@dataclass(frozen=True, slots=True)
class Type:
    """Base of every IR type.  Instances compare by structure and are interned."""

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        raise NotImplementedError

    def written(self) -> str:
        """The name of this type as a program writes it.

        What a message about a type says, and what the language server shows.
        Most types are written the way the IR renders them -- the IR borrowed
        the language's own notation wherever it could -- and the ones that are
        not say so here rather than in every place that reports a type.

        It is a second method and not a change to `render` because the IR's
        textual form is read back as well as written: `ir/reader.py` parses what
        the printer produced, and a form that said `&mut u8` where the reader
        expects `ptr<mut u8>` would be a form that no longer round-trips.
        """
        return self.render()

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
class SyntaxType(Type):
    """A piece of the program, which is what a macro is handed and what it answers.

    **A value of one never reaches the run time.**  It exists while the compiler
    runs, where a macro takes a program apart and puts one back together, and there
    is nothing for it to be afterwards: the program a macro wrote is the program,
    and a tree of it in the binary would be a second copy of what the binary already
    is.  So the checker refuses one anywhere a value has to outlive the compilation
    -- a parameter or a result of a function the program calls, a field of a type, a
    variable at the top level -- and what is left is the macros and the functions
    they call.

    It is a machine word wide because it is a handle: what it points at is a table
    the compiler holds, and the interpreter that runs a macro is the only thing that
    looks inside.
    """

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "syntax"

    @property
    def holder(self) -> IntType:
        """The integer a handle is held as."""
        return U64


@dataclass(frozen=True, slots=True)
class Unit:
    """What a number counts, as base units with exponents and a scale.

    A unit is not a name: `\N{CURRENCY SIGN}meter/second` and `\N{CURRENCY SIGN}meter\N{SUPERSCRIPT TWO}/meter\N{SUPERSCRIPT TWO}\N{MULTIPLICATION SIGN}meter/second` are the
    same unit, and a product of two is worked out rather than looked up.  So
    what is kept is the exponent of each base unit, in a canonical order, and
    `\N{MULTIPLICATION SIGN}` adds those exponents while `\N{DIVISION SIGN}` subtracts them -- which is what makes the
    seconds cancel when a speed is multiplied by a time.

    `scale` is what one of these is in terms of its base units: a mile an hour
    is 1609344/3600000 of a metre a second, so two units with the same
    exponents and different scales are two units.  Nothing yet converts between
    them -- what it records is that they measure the same thing.

    The empty one is no unit at all, which is what every number has that was
    never given one.  It is a unit like any other so that there is one rule
    rather than two: a value may stand where another does when the units are
    equal, and "no unit" is equal only to itself.
    """

    #: Base unit names and their exponents, sorted by name, with no zeros.
    powers: tuple[tuple[str, int], ...] = ()
    #: How many of the base units one of these is, as a fraction.
    scale: Fraction = Fraction(1)

    def render(self) -> str:
        """How the unit is written in a type."""
        if not self.powers:
            return ""
        above = [(n, e) for n, e in self.powers if e > 0]
        below = [(n, -e) for n, e in self.powers if e < 0]
        made = _raised(above) if above else "1"
        # Each one below the line gets its own sign, because a unit is read
        # left to right: `a\N{DIVISION SIGN}b\N{MULTIPLICATION SIGN}c` is `c` times `a` over `b`, and what is wanted
        # here is `a` over both.
        return "".join((made, *("".join(("\N{DIVISION SIGN}", one))
                                for one in _each(below))))

    def mangled(self) -> str:
        """The unit as one word, for a symbol name."""
        return "".join((n, str(e)) for n, e in self.powers)

    def times(self, other: Unit) -> Unit:
        """The unit of a product: the exponents added, the scales multiplied."""
        return _made_of(dict(self.powers), other.powers, 1,
                        self.scale * other.scale)

    def over(self, other: Unit) -> Unit:
        """The unit of a quotient: the exponents subtracted, the scales divided."""
        return _made_of(dict(self.powers), other.powers, -1,
                        self.scale / other.scale)

    def raised(self, exponent: int) -> Unit:
        """The unit of a power: every exponent multiplied by it."""
        return _made_of({}, tuple((n, e * exponent) for n, e in self.powers), 1,
                        self.scale ** exponent)

    @property
    def is_none(self) -> bool:
        """Whether this is no unit at all."""
        return not self.powers and self.scale == 1


def _raised(powers: Sequence[tuple[str, int]]) -> str:
    """One side of a unit written out, with the raised digits a power takes."""
    return "\N{MULTIPLICATION SIGN}".join(_each(powers))


def _each(powers: Sequence[tuple[str, int]]) -> list[str]:
    """Every base unit written out, with its exponent where it has one."""
    return [n if e == 1 else "".join((n, _superscript(e))) for n, e in powers]


#: The digits written raised, which is how an exponent is written in a unit as
#: well as in an expression.
_RAISED_DIGITS: Final[str] = "\N{SUPERSCRIPT ZERO}\N{SUPERSCRIPT ONE}\N{SUPERSCRIPT TWO}\N{SUPERSCRIPT THREE}\N{SUPERSCRIPT FOUR}\N{SUPERSCRIPT FIVE}\N{SUPERSCRIPT SIX}\N{SUPERSCRIPT SEVEN}\N{SUPERSCRIPT EIGHT}\N{SUPERSCRIPT NINE}"


def _superscript(value: int) -> str:
    """*value* written in raised digits."""
    return "".join(_RAISED_DIGITS[int(d)] for d in str(value))


def _made_of(powers: dict[str, int], more: Sequence[tuple[str, int]], sign: int,
             scale: Fraction) -> Unit:
    """A unit out of exponents gathered together, dropping the ones that cancel."""
    for name, exponent in more:
        powers[name] = powers.get(name, 0) + sign * exponent
    return Unit(tuple(sorted((n, e) for n, e in powers.items() if e != 0)), scale)


#: No unit at all, which is what a number has that was never given one.
NO_UNIT: Final[Unit] = Unit()


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
    #: What a value of it counts.  Part of the type, so `u64` and `u64 \N{CURRENCY SIGN}meter`
    #: are two types and neither stands where the other is wanted; no unit at
    #: all is the empty one, which is equal only to itself.
    unit: Unit = NO_UNIT

    #: Every one of them that has been made, so that two asks for one width
    #: answer with the one type.  `TypeContext` interns the types that are
    #: built out of others for the same reason and says why: identity
    #: comparison is what the rest of the compiler asks these with, and there
    #: are now sixty-odd of these rather than eight.
    _made: ClassVar[dict[tuple[int, bool, Unit], IntType]] = {}

    def __new__(cls, bits: int, signed: bool = False,
                unit: Unit = NO_UNIT) -> IntType:
        found = cls._made.get((bits, signed, unit))
        if found is None:
            found = super().__new__(cls)
            cls._made[(bits, signed, unit)] = found
        return found

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        made = "".join(("i" if self.signed else "u", str(self.bits)))
        return made if self.unit.is_none else \
            "".join((made, " \N{CURRENCY SIGN}", self.unit.render()))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name.

        The unit is left out: it says nothing about what the bits are, and a
        unit that changed a symbol name would be a unit that changed the code.
        """
        return "".join(("i" if self.signed else "u", str(self.bits)))

    @property
    def bare(self) -> IntType:
        """The same width and signedness with no unit, which is what the bits are."""
        return IntType(self.bits, self.signed)

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
    #: What a value of it counts, as an integer type carries one and for the
    #: same reason.
    unit: Unit = NO_UNIT

    #: Interned like the integer types and for the same reason: identity
    #: comparison is what the rest of the compiler asks a type with, and a unit
    #: makes many of these where there were two.
    _made: ClassVar[dict[tuple[int, Unit], FloatType]] = {}

    def __new__(cls, bits: int, unit: Unit = NO_UNIT) -> FloatType:
        found = cls._made.get((bits, unit))
        if found is None:
            found = super().__new__(cls)
            cls._made[(bits, unit)] = found
        return found

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        made = "".join(("f", str(self.bits)))
        return made if self.unit.is_none else \
            "".join((made, " \N{CURRENCY SIGN}", self.unit.render()))

    def mangled(self) -> str:
        """The normalized name of this type, with the unit left out."""
        return "".join(("f", str(self.bits)))

    @property
    def bare(self) -> FloatType:
        """The same width with no unit, which is what the bits are."""
        return FloatType(self.bits)


@dataclass(frozen=True, slots=True)
class PtrType(Type):
    """A pointer to a value of another type.

    Whether what it points at may be changed is part of the pointer's type, not
    of the thing it points at: a value is a value, and it is the *place* that is
    writable or not.  A variable in memory is a pointer, so this is where the
    language's ``mut`` ends up.

    So is how long what it names lives, and for the same reason: it is a fact
    about the place and both the one who made the reference and the one who
    reads it have to agree about it.  There are two lifetimes a type can say --
    as long as the program, or no longer than the call -- because there are two
    a place can have: a variable at the top level has the first and everything
    else has the second.  A reference that is neither, because it names what a
    parameter named, says so in the signature it appears in rather than in its
    type, there being nothing in a type to name a parameter with.
    """

    pointee: Type
    mutable: bool = False
    #: Whether what it names lives as long as the program does.
    lasting: bool = False

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("ptr<", "mut " if self.mutable else "",
                        "static " if self.lasting else "",
                        self.pointee.render(), ">"))

    def written(self) -> str:
        """The name of this type as a program writes it: `&mut static T`."""
        return "".join(("&", "mut " if self.mutable else "",
                        "static " if self.lasting else "",
                        self.pointee.written()))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("ptr<", "mut " if self.mutable else "",
                        "static " if self.lasting else "",
                        self.pointee.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class FuncType(Type):
    """The type of a function."""

    params: tuple[Type, ...]
    ret: Type
    #: Whether an array handed where one of its elements is wanted is walked.
    #: It is part of the type because the caller is who does the walking, so a
    #: value that travels has to carry the promise with it -- the same reason
    #: a reference carries whether its place may be written.
    listable: bool = False
    #: Whether a function of this type hands out an arena of its own with its
    #: answer, which a call receives by binding it: `→ T in pool`.  Its code
    #: takes one parameter more than the type says, last: where the caller keeps
    #: that arena.
    hands_out: bool = False
    #: Whether what a function of this type brought in lasts as long as the
    #: program -- kept in `⎕heap`, or nothing at all -- so that a value of it
    #: may go anywhere: `fn(u8) in ⎕heap → u8`.
    lasting: bool = False

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        inner = ", ".join(p.render() for p in self.params)
        return "".join(("listable " if self.listable else "",
                        "fn(", inner, ")", " lasting" if self.lasting else "",
                        " \N{RIGHTWARDS ARROW} ", self.ret.render(),
                        " in arena" if self.hands_out else ""))

    def written(self) -> str:
        """The name of this type as a program writes it."""
        inner = ", ".join(p.written() for p in self.params)
        return "".join(("@[listable] " if self.listable else "",
                        "fn(", inner, ")",
                        " in \N{APL FUNCTIONAL SYMBOL QUAD}heap" if self.lasting else "",
                        " \N{RIGHTWARDS ARROW} ", self.ret.written(),
                        " in its own arena" if self.hands_out else ""))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name.

        A function type is spelled the way a function symbol is: the parameters
        in parentheses, then the result.
        """
        inner = ",".join(p.mangled() for p in self.params)
        return "".join(("listable " if self.listable else "",
                        "fn(", inner, ")", " lasting" if self.lasting else "",
                        self.ret.mangled(),
                        " in" if self.hands_out else ""))


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


@dataclass(frozen=True, slots=True, eq=False)
class ProductType(Type):
    """A record.  The field order here is the declaration order, not a layout.

    A type a program defined is **nominal**: two definitions with the same
    fields are two types, because a definition is what says what a value *is*
    and two things that happen to be laid out alike are not one thing.  That is
    what `name` and `origin` are for -- the name as the source wrote it, and the
    file that wrote it, so that two files each defining `Point` define two.

    Being nominal is why two of these are the same one only where they are the
    same object.  What that buys is a type that reaches itself through a
    reference: the object exists before its fields are known, so a field may
    name it, and asking whether two are equal never has to walk round the
    circle.
    """

    fields: tuple[tuple[str, Type], ...]
    #: The name the program gave it, or nothing for one the compiler made.
    name: str = ""
    #: The file the definition is in, which is part of which type this is and
    #: no part of what it is called.
    origin: str = ""
    #: Whether the record is laid out the way the system's C ABI lays one out,
    #: which is what `@[abi]` says.  Such a record is shared with something
    #: compiled by something else, so its fields are never reordered and a value
    #: of it never crosses a call: what crosses is a reference, which every
    #: convention agrees about and which this one does not have to classify.
    abi: bool = False
    #: Whether there is one of a value of this type and it is never copied, which
    #: `@[unique]` says.  It is a rule about the *program* and nothing about the
    #: representation: such a record travels and is laid out like any other, and
    #: what changes is which places it may be written into.
    unique: bool = False
    #: Whether a value of this type is permission to do input or output, which
    #: `@[device]` says.  A function handed one may do it, which is what makes
    #: `@[impure]` mean "changes something global" rather than "touches a device
    #: somebody handed over".
    device: bool = False

    def __eq__(self, other: object) -> bool:
        """Nominal, so one of these is the same type only as itself.

        The base compares by structure, which for a type that reaches itself
        through a reference would walk round the circle for ever -- and would
        answer the wrong question anyway, two definitions with the same parts
        being two types.
        """
        return self is other

    def __hash__(self) -> int:
        """By identity, since that is what equality is."""
        return id(self)

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

    def written(self) -> str:
        """The name of this type as a program writes it: `T ? E`, or `T?`."""
        return "".join((self.ok.written(), "?")) if self.err is None else \
            " ? ".join((self.ok.written(), self.err.written()))

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

    def written(self) -> str:
        """The same, with each member written as a program writes it."""
        return "".join(("\N{LEFT ANGLE BRACKET}",
                        ", ".join(m.written() for m in self.members),
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

    def written(self) -> str:
        """The same, with the element written as a program writes it."""
        return "".join((self.element.written(),
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

    def written(self) -> str:
        """The same, with the element written as a program writes it.

        A program cannot write a vector type either: it is what an operator
        walked over an array works on, and a message about one says what it is.
        """
        return "".join((self.element.written(), "\N{MULTIPLICATION SIGN}",
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

    def written(self) -> str:
        """The same, with the element written as a program writes it."""
        return "".join(("[", self.element.written(), "]"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("list<", self.element.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class CursorType(Type):
    """Where a walk over a list has got to.

    Two words: where the list *lives* and how far along it the walk is.  The
    place and not the list, because a walk may take an element out, and what a
    list is is where its elements are and how many there are -- so a shorter
    list is a different pair of words, and the one who is walking has to be able
    to put it where the one who is holding the list will read it.

    How far along is counted in elements and not in bytes: a cursor past the
    last element is one whose index is the count, which is what a walk that is
    over looks like and what nothing may read through.
    """

    element: Type

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("cursor<", self.element.render(), ">"))

    def written(self) -> str:
        """What a message calls it.

        A program cannot write this type at all -- a cursor lives in a name
        whose type is read off its value -- so what a message says is what it
        is rather than a spelling nobody could have written.
        """
        return "".join(("cursor over [", self.element.written(), "]"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("cursor<", self.element.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class SetType(Type):
    """A set: the keys it holds, and nothing said about them beyond membership."""

    element: Type
    #: Whether what it holds may be changed through a value of this type.  It
    #: is part of the type for the reason a reference's `mut` is: a collection
    #: is a handle, so the one who made it and the one who was handed it reach
    #: the same table, and what may be done to it is what the type says.
    mutable: bool = False

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("mut " if self.mutable else "",
                        "\N{LEFT DOUBLE PARENTHESIS}", self.element.render(), "\N{RIGHT DOUBLE PARENTHESIS}"))

    def written(self) -> str:
        """The same, with what it holds written as a program writes it."""
        return "".join(("mut " if self.mutable else "",
                        "\N{LEFT DOUBLE PARENTHESIS}", self.element.written(), "\N{RIGHT DOUBLE PARENTHESIS}"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("set<", "mut " if self.mutable else "",
                        self.element.mangled(), ">"))


@dataclass(frozen=True, slots=True)
class DictType(Type):
    """A dictionary: what a key is, and what it stands for."""

    key: Type
    value: Type
    #: Whether an entry may be put in through a value of this type.  See
    #: `SetType.mutable`, which says the same thing about a set.
    mutable: bool = False

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("mut " if self.mutable else "",
                        "\N{LEFT DOUBLE PARENTHESIS}", self.key.render(), ": ",
                        self.value.render(), "\N{RIGHT DOUBLE PARENTHESIS}"))

    def written(self) -> str:
        """The same, with both written as a program writes them."""
        return "".join(("mut " if self.mutable else "",
                        "\N{LEFT DOUBLE PARENTHESIS}", self.key.written(), ": ",
                        self.value.written(), "\N{RIGHT DOUBLE PARENTHESIS}"))

    def mangled(self) -> str:
        """The normalized name of this type, for use inside a symbol name."""
        return "".join(("dict<", "mut " if self.mutable else "",
                        self.key.mangled(), ",", self.value.mangled(), ">"))


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


@dataclass(frozen=True, slots=True, eq=False)
class SumType(Type):
    """A choice between named variants, the basis of the language's error model.

    Nominal for the same reason a product is, and by the same two fields -- and
    the same one object, for the same reason.
    """

    variants: tuple[tuple[str, Type], ...]
    #: The name the program gave it, or nothing for one the compiler made.
    name: str = ""
    #: The file the definition is in, which is part of which type this is.
    origin: str = ""

    def __eq__(self, other: object) -> bool:
        """Nominal, so one of these is the same type only as itself.

        The base compares by structure, which for a type that reaches itself
        through a reference would walk round the circle for ever -- and would
        answer the wrong question anyway, two definitions with the same parts
        being two types.
        """
        return self is other

    def __hash__(self) -> int:
        """By identity, since that is what equality is."""
        return id(self)

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
SYNTAX: Final[SyntaxType] = SyntaxType()

#: What a program writes for a piece of the program.  An ordinary word rather than
#: one of the compiler's `⎕` names, because a macro's parameter is written with it and a
#: parameter's type is a type like any other.
SYNTAX_NAME: Final[str] = "syntax"
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
    (("next", U64), ("limit", U64), ("chunk", U64)), name=ARENA_NAME,
    # Never copied: two of one arena would be two makers of the same room, and the
    # second to give it back would give back what the first already had.
    unique=True)


#: Which widths each signedness has a type for.  Unsigned from one bit, signed
#: from two: a signed type of one bit holds zero and minus one, which is a pair
#: of values no program wants and a name every reader would misread.
UNSIGNED_WIDTHS: Final[tuple[int, ...]] = (*range(1, 33), 64)
SIGNED_WIDTHS: Final[tuple[int, ...]] = (*range(2, 33), 64)

#: What went wrong when a value would not fit the type it was being narrowed
#: to.  It is the compiler's rather than a program's because `\N{APL FUNCTIONAL SYMBOL QUAD}narrow` answers
#: with it and every program that narrows anything needs the same three names:
#: a condition each program spelled for itself would be three spellings of one
#: thing, and a `match` over one would not carry from one file to the next.
#:
#: **Overflow is first, and so is numbered nought**, which is what makes it the
#: condition a failure reports where neither of the others holds -- above the
#: top of the type is the ordinary way not to fit.  `sign` is the case of
#: underflow the language can say more about: a negative number put where an
#: unsigned type wants one is not merely below the bottom, it is of the wrong
#: kind, and a reader told "sign" knows which mistake was made.
NARROWING_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}narrowing"

#: `absent` is the fourth and is about a type whose values are not a range: an
#: enumeration holds the numbers its definition named and nothing between them,
#: so a number that is none of them does not fit -- which is the same question
#: the other three answer and a different way of not fitting.
NARROWING: Final[EnumType] = EnumType(
    members=("overflow", "underflow", "sign", "absent"), values=(0, 1, 2, 3),
    holder=IntType(8, False), name=NARROWING_NAME)


BUILTIN_TYPES: Final[dict[str, Type]] = {
    **{"".join(("u", str(bits))): IntType(bits, False)
       for bits in UNSIGNED_WIDTHS},
    **{"".join(("i", str(bits))): IntType(bits, True) for bits in SIGNED_WIDTHS},
    "f32": F32, "f64": F64,
    "bool": BOOL, "char": CHAR, "str": STR, "void": VOID,
    ARENA_NAME: ARENA,
    NARROWING_NAME: NARROWING,
    SYNTAX_NAME: SYNTAX,
}


class TypeContext:
    """Interns constructed types so that identity comparison is valid."""

    def __init__(self) -> None:
        self._results: dict[tuple[Type, Type | None], ResultType] = {}
        self._tuples: dict[tuple[Type, ...], TupleType] = {}
        self._arrays: dict[tuple[Type, tuple[int | None, ...]], ArrayType] = {}
        self._vectors: dict[tuple[Type, int], VecType] = {}
        self._lists: dict[Type, ListType] = {}
        self._cursors: dict[Type, CursorType] = {}
        self._sets: dict[tuple[Type, bool], SetType] = {}
        self._dicts: dict[tuple[Type, Type, bool], DictType] = {}
        self._pointers: dict[tuple[Type, bool, bool], PtrType] = {}
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

    def cursor_type(self, element: Type) -> CursorType:
        """Return the cursor type over a list of *element*."""
        found = self._cursors.get(element)
        if found is None:
            found = CursorType(element)
            self._cursors[element] = found
        return found

    def set_type(self, element: Type, mutable: bool = False) -> SetType:
        """Return the set type over *element*, writable where *mutable*."""
        found = self._sets.get((element, mutable))
        if found is None:
            found = SetType(element, mutable)
            self._sets[(element, mutable)] = found
        return found

    def dict_type(self, key: Type, value: Type,
                  mutable: bool = False) -> DictType:
        """Return the dictionary type from *key* to *value*."""
        found = self._dicts.get((key, value, mutable))
        if found is None:
            found = DictType(key, value, mutable)
            self._dicts[(key, value, mutable)] = found
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

    def ptr_type(self, pointee: Type, mutable: bool = False,
                 lasting: bool = False) -> PtrType:
        """Return the pointer type to *pointee*."""
        key = (pointee, mutable, lasting)
        found = self._pointers.get(key)
        if found is None:
            found = PtrType(pointee, mutable, lasting)
            self._pointers[key] = found
        return found

    def func_type(self, params: tuple[Type, ...], ret: Type,
                  listable: bool = False, hands_out: bool = False,
                  lasting: bool = False) -> FuncType:
        """Return the function type with the given signature."""
        key = (params, ret, listable, hands_out, lasting)
        found = self._functions.get(key)
        if found is None:
            found = FuncType(params, ret, listable, hands_out, lasting)
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


def without_units(ty: Type) -> Type:
    """The same type with no unit on it, which is what the bits are.

    A unit is part of a type and no part of the code: it decides what may be
    written where, and by the time there are instructions it has said all it has
    to say.  So the checks about what bits an instruction takes and answers with
    ask this, and the checks about what a program may write ask the type.
    """
    return ty.bare if isinstance(ty, (IntType, FloatType)) else ty


def held_in_memory(ty: Type) -> bool:
    """Whether a value of *ty* is bytes in a place rather than values.

    A sum is its largest part with a tag after it, and an array whose type says
    its shape is its elements: neither is something a register holds, so what a
    value of one *is*, is where those bytes are.  Everything that has to know
    whether a value travels in registers asks this, so the answer cannot drift
    between the parts of the compiler that ask it.
    """
    return isinstance(ty, SumType) or (isinstance(ty, ArrayType) and ty.fixed)


def made_of_parts(ty: Type) -> bool:
    """Whether a value of *ty* is several values travelling as one.

    Not "more than one part": a record of one field is made of parts and has
    one, and what tells the two apart is whether the parts are something other
    than the type itself.  Everything that chooses between the one-register path
    and the part-by-part one asks this, so the two answers cannot drift.
    """
    return parts_of(ty) != (ty,)


def parts_within(ty: Type) -> tuple[tuple[int, int], ...]:
    """Where each member of *ty* begins among its parts, and how many it has.

    A member of a tuple or a record is one value to a program and may be several
    to a machine, and `parts_of` answers the machine.  This is the other half of
    that: what reads a member out of one asks where its parts begin and how many
    to take.

    A type with no members answers with nothing, there being no question.
    """
    if isinstance(ty, TupleType):
        held: tuple[Type, ...] = ty.members
    elif isinstance(ty, ProductType):
        held = tuple(one for _, one in ty.fields)
    else:
        return ()
    found: list[tuple[int, int]] = []
    at = 0
    for one in held:
        count = len(parts_of(one))
        found.append((at, count))
        at += count
    return tuple(found)


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

    **It answers the leaves and not the members.**  A member that is itself
    several values -- a result inside a tuple, a string inside a record -- is
    that many parts here, because what a part *is* is one value in one register.
    A member counted as one part would be a member given one register and needing
    two, which is a program refused with a message about an encoding of `mov`.
    `parts_within` is what says which of the leaves belong to which member, for
    the places that read a member out.
    """
    if isinstance(ty, ResultType):
        # The answer, whether there is one, and -- where the error carries a
        # value of its own -- that value.  The truth value stays the *second*
        # part whatever else there is, because everything that reads one asks
        # for it by that place and a part added after it changes nothing.
        return (ty.ok, BOOL) if ty.err is None else (ty.ok, BOOL, ty.err)
    if isinstance(ty, TupleType):
        # Its members, and a member that is itself several values spread out
        # where it stands.  What travels is values, so a part that was itself
        # several of them would be a part nothing could put in a register --
        # which is the same rule a record follows below, and for the same
        # reason.  Which of them belongs to which member is `parts_within`.
        return tuple(one for member in ty.members for one in parts_of(member))
    if isinstance(ty, ProductType):
        # Its fields, in the order the definition wrote them, and a field that
        # is itself a record spread out where it stands.  A record and a tuple
        # are the same thing to everything below here -- several values
        # travelling together -- and what travels is values, so a part that was
        # itself several of them would be a part nothing could put in a
        # register.  Where each of them went is the checker's to remember.
        return tuple(one for _, held in ty.fields for one in parts_of(held))
    if isinstance(ty, ArrayType) and not ty.fixed:
        return (_pointer_to(ty.element), *(U64 for _ in ty.shape))
    if isinstance(ty, ListType):
        # Where the elements are, how many there are, and the allocator they came
        # from: a list carries what gives it back, so that nothing that holds one
        # has to be told.
        return (_pointer_to(ty.element), U64, _pointer_to(ARENA))
    if isinstance(ty, CursorType):
        # Where the list is -- the place, not the elements -- and how far along
        # the walk is.
        return (_pointer_to(ListType(ty.element)), U64)
    if isinstance(ty, FuncType):
        # A function written where a value is wanted is three words: where its
        # code is, where what it brought in with it is, and the allocator that
        # room came from -- none where it is in a frame or there is none, as a
        # string carries its own.  One type covers both the lambda that brought
        # something in and the one that brought nothing, which is what lets
        # either stand where the type says a function stands.
        return (_pointer_to(U8), _pointer_to(U8), _pointer_to(ARENA))
    if isinstance(ty, StrType):
        # Where the bytes are, how many there are, and the allocator they came
        # from -- nothing for text in the image, which is never given back.  The
        # first two are what an array whose type does not say its length is as
        # well; the third is what makes a string something that can be given back
        # by whoever holds it.
        return (_pointer_to(U8), U64, _pointer_to(ARENA))
    return (ty,)
