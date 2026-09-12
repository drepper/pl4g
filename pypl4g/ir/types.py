"""IR types.

No type ever carries a size, an alignment or a field offset.  The specification
lets the compiler reorder the fields of a product type for efficiency, so layout
is a property computed late and held beside the type, never inside it.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Type:
    """Base of every IR type.  Instances compare by structure and are interned."""

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        raise NotImplementedError


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
    """A pointer to a value of another type."""

    pointee: Type

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        return "".join(("ptr<", self.pointee.render(), ">"))


@dataclass(frozen=True, slots=True)
class FuncType(Type):
    """The type of a function."""

    params: tuple[Type, ...]
    ret: Type

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        inner = ", ".join(p.render() for p in self.params)
        return "".join(("fn(", inner, ") \N{RIGHTWARDS ARROW} ", self.ret.render()))


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
class ProductType(Type):
    """A record.  The field order here is the declaration order, not a layout."""

    fields: tuple[tuple[str, Type], ...]

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        inner = ", ".join("".join((n, ": ", t.render())) for n, t in self.fields)
        return "".join(("{", inner, "}"))


@dataclass(frozen=True, slots=True)
class SumType(Type):
    """A choice between named variants, the basis of the language's error model."""

    variants: tuple[tuple[str, Type], ...]

    def render(self) -> str:
        """The name of this type in the textual form of the IR."""
        inner = " | ".join("".join((n, ": ", t.render())) for n, t in self.variants)
        return "".join(("<", inner, ">"))


VOID: Final[VoidType] = VoidType()
BOOL: Final[BoolType] = BoolType()
MEM: Final[MemType] = MemType()

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
BUILTIN_TYPES: Final[dict[str, Type]] = {
    "i8": I8, "i16": I16, "i32": I32, "i64": I64,
    "u8": U8, "u16": U16, "u32": U32, "u64": U64,
    "f32": F32, "f64": F64,
    "bool": BOOL, "void": VOID,
}


class TypeContext:
    """Interns constructed types so that identity comparison is valid."""

    def __init__(self) -> None:
        self._pointers: dict[Type, PtrType] = {}
        self._functions: dict[tuple[tuple[Type, ...], Type], FuncType] = {}
        self._integers: dict[tuple[int, bool], IntType] = {
            (t.bits, t.signed): t for t in (I8, I16, I32, I64, U8, U16, U32, U64)}

    def int_type(self, bits: int, signed: bool) -> IntType:
        """Return the integer type of the given width and signedness."""
        key = (bits, signed)
        found = self._integers.get(key)
        if found is None:
            found = IntType(bits, signed)
            self._integers[key] = found
        return found

    def ptr_type(self, pointee: Type) -> PtrType:
        """Return the pointer type to *pointee*."""
        found = self._pointers.get(pointee)
        if found is None:
            found = PtrType(pointee)
            self._pointers[pointee] = found
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
