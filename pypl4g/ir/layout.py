"""How much room a value of a type takes, and where it must start.

No type carries this.  The specification lets the compiler reorder the fields of
a product type for efficiency, so a size or an offset stored in a type would
throw that freedom away; layout is therefore computed from a type rather than
held inside one, and it is computed against a particular target, because the
width of a pointer is the target's business and not the type's.
"""

from dataclasses import dataclass

from .types import (BoolType, FloatType, IntType, MemType, ProductType, PtrType,
                    SumType, Type, VoidType)


class NoLayoutError(Exception):
    """The type has no layout in memory."""

    def __init__(self, ty: Type) -> None:
        super().__init__("".join(("'", ty.render(), "' has no layout in memory")))
        self.ty = ty


@dataclass(frozen=True, slots=True)
class DataLayout:
    """What a target's memory looks like."""

    pointer_size: int
    #: Every target so far stores the low-order byte first.
    little_endian: bool = True


def size_of(ty: Type, layout: DataLayout) -> int:
    """The number of bytes a value of *ty* occupies."""
    match ty:
        case IntType() | FloatType():
            return ty.bits // 8
        case BoolType():
            return 1
        case PtrType():
            return layout.pointer_size
        case VoidType():
            return 0
        case ProductType():
            # Laid out in declaration order for now.  Choosing a better order is
            # what the specification permits and what a later pass will do; it
            # belongs here, where nothing about a type has to change for it.
            total = 0
            for _, field in ty.fields:
                total = _align_up(total, align_of(field, layout))
                total += size_of(field, layout)
            return _align_up(total, align_of(ty, layout))
        case _:
            raise NoLayoutError(ty)


def align_of(ty: Type, layout: DataLayout) -> int:
    """The boundary a value of *ty* must start on."""
    match ty:
        case IntType() | FloatType():
            return ty.bits // 8
        case BoolType():
            return 1
        case PtrType():
            return layout.pointer_size
        case VoidType():
            return 1
        case ProductType():
            return max((align_of(f, layout) for _, f in ty.fields), default=1)
        case MemType() | SumType():
            raise NoLayoutError(ty)
        case _:
            raise NoLayoutError(ty)


def _align_up(value: int, alignment: int) -> int:
    """Round *value* up to the next multiple of *alignment*."""
    if alignment <= 1:
        return value
    remainder = value % alignment
    return value if remainder == 0 else value + alignment - remainder


class ValueOutOfRangeError(Exception):
    """A value does not fit the room its type gives it."""

    def __init__(self, value: int, ty: Type) -> None:
        super().__init__("".join((str(value), " does not fit in '", ty.render(), "'")))
        self.value = value
        self.ty = ty


def encode_scalar(value: int, ty: Type, layout: DataLayout) -> bytes:
    """The bytes a scalar occupies in memory.

    A value that does not fit is refused rather than stored with its upper bits
    dropped.  The semantic analysis has already refused every one a program can
    write, so reaching this is a defect in the compiler -- but a wrapped value
    is exactly the kind of quiet reinterpretation the language rules out, and it
    would be as wrong here as anywhere.
    """
    size = size_of(ty, layout)
    low = -(1 << (size * 8 - 1)) if size else 0
    high = (1 << (size * 8)) - 1 if size else 0
    if not low <= value <= high:
        raise ValueOutOfRangeError(value, ty)
    order = "little" if layout.little_endian else "big"
    return (value & ((1 << (size * 8)) - 1)).to_bytes(size, order)  # type: ignore[arg-type]
