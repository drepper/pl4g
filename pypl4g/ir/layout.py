"""How much room a value of a type takes, and where it must start.

No type carries this.  The specification lets the compiler reorder the fields of
a product type for efficiency, so a size or an offset stored in a type would
throw that freedom away; layout is therefore computed from a type rather than
held inside one, and it is computed against a particular target, because the
width of a pointer is the target's business and not the type's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .types import (ArrayType, BoolType, DictType, EnumType, FloatType,
                    IntType, MemType,
                    ProductType, PtrType, SetType, TupleType,
                    ResultType, SumType, Type, VecType, VoidType)


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
    #: Whether what is laid out has to match what the system's own compilers
    #: would produce.  The specification leaves the compiler free to lay things
    #: out as it likes as long as everything in one image agrees, and a
    #: definition that has to be reachable from a world that has never heard of
    #: this language gives that freedom up.  Everything the language has so far
    #: is laid out the same either way but one: an array takes a wider
    #: alignment where nothing outside is reading it.
    system: bool = False


#: How large an array has to be before it is worth aligning wider than its
#: elements ask for.  Two words: at that size a copy or a comparison of the
#: whole of it is worth doing a word at a time, and a word at a time wants the
#: first word aligned.
WIDE_ENOUGH: Final[int] = 16


def size_of(ty: Type, layout: DataLayout) -> int:
    """The number of bytes a value of *ty* occupies."""
    match ty:
        case IntType() | FloatType():
            return ty.bits // 8
        case BoolType():
            return 1
        case EnumType():
            return size_of(ty.holder, layout)
        case TupleType():
            # Laid out as a product of the same members would be.
            total = 0
            for member in ty.members:
                total = _align_up(total, align_of(member, layout))
                total += size_of(member, layout)
            return _align_up(total, align_of(ty, layout))
        case VecType():
            # Its lanes and nothing else, packed as the run of elements it was
            # read out of is packed -- which is what makes reading a whole one
            # the same bytes as reading each of them.
            return ty.lanes * stride_of(ty.element, layout)
        case ArrayType() if ty.fixed:
            # Its elements and nothing else, which is what "the type carries
            # everything" means: how many there are is in the type, so no room
            # is spent saying it again.  However many dimensions the shape has,
            # the elements are one run: the last dimension is the one whose
            # neighbours are next to each other.
            count = ty.count
            assert count is not None
            return count * stride_of(ty.element, layout)
        case ArrayType():
            # Where the elements are and one count per dimension, which is what
            # an array whose type does not say its shape has to carry with it.
            return (1 + ty.rank) * layout.pointer_size
        case SetType() | DictType():
            # A handle, which is where the table is and nothing else: how many
            # entries it has and how much room it has for them are in the table
            # rather than beside it, so that two names for one collection see
            # one answer.  The table is elsewhere and is not part of the value,
            # which is what lets one be passed and answered with like any other.
            return layout.pointer_size
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
        case SumType():
            # The payload first and the tag after it, which is never larger than
            # the tag first and is sometimes smaller: a tag ahead of a payload
            # that wants eight bytes is seven bytes of padding, and behind it is
            # often none.
            payload = max((size_of(v, layout) for _, v in ty.variants), default=0)
            return _align_up(payload + _TAG_SIZE, align_of(ty, layout))
        case ResultType():
            # The same shape: the answer, and one byte saying whether there is
            # one.  It is not a `SumType` because the error carries nothing and
            # a variant of a sum carries something.
            return _align_up(size_of(ty.ok, layout) + _TAG_SIZE,
                             align_of(ty, layout))
        case _:
            raise NoLayoutError(ty)


def align_of(ty: Type, layout: DataLayout) -> int:
    """The boundary a value of *ty* must start on."""
    match ty:
        case IntType() | FloatType():
            return ty.bits // 8
        case BoolType():
            return 1
        case EnumType():
            return align_of(ty.holder, layout)
        case TupleType():
            return max((align_of(m, layout) for m in ty.members), default=1)
        case VecType():
            # No more than one lane asks for.  What it reads is an array that
            # was laid out before anything knew a whole one would be read at
            # once, so the read has to be one that does not care.
            return align_of(ty.element, layout)
        case ArrayType() if ty.fixed:
            # An array starts where its first element would, so the system's
            # rule is that it is aligned as one element is.  Where nothing
            # outside is reading it, one large enough to be worth reading a word
            # at a time is put where a word can be read: the freedom the
            # specification gives is worth nothing until it is used for
            # something, and this is the smallest something that pays.
            plain = align_of(ty.element, layout)
            if layout.system or size_of(ty, layout) < WIDE_ENOUGH:
                return plain
            return max(plain, WIDE_ENOUGH)
        case ArrayType():
            return layout.pointer_size
        case SetType() | DictType():
            return layout.pointer_size
        case PtrType():
            return layout.pointer_size
        case VoidType():
            return 1
        case ProductType():
            return max((align_of(f, layout) for _, f in ty.fields), default=1)
        case SumType():
            return max((align_of(v, layout) for _, v in ty.variants), default=1)
        case ResultType():
            return align_of(ty.ok, layout)
        case MemType():
            raise NoLayoutError(ty)
        case _:
            raise NoLayoutError(ty)


#: How much room the tag of a sum takes.  One byte holds two hundred and
#: fifty-six variants, and a type with more of them than that is a type whose
#: definition is the thing to look at.
_TAG_SIZE: Final[int] = 1


def stride_of(ty: Type, layout: DataLayout) -> int:
    """How far apart two values of *ty* are where they follow one another.

    Its size brought up to its alignment, which is what puts the second one
    where a value of that type may start.  For everything the language has so
    far the two are already the same; a product type whose last field is narrow
    is the first that will not be.
    """
    return _align_up(size_of(ty, layout), align_of(ty, layout))


def offsets_of(ty: ProductType, layout: DataLayout) -> tuple[int, ...]:
    """Where each field of *ty* starts, in the order the fields were declared.

    Declaration order for now, which is what `size_of` lays out.  The
    specification lets a later pass choose a better order; when one does, this
    is the single place that says where a field went.
    """
    found: list[int] = []
    total = 0
    for _, field in ty.fields:
        total = _align_up(total, align_of(field, layout))
        found.append(total)
        total += size_of(field, layout)
    return tuple(found)


def member_offsets_of(ty: TupleType, layout: DataLayout) -> tuple[int, ...]:
    """Where each member of a tuple starts, in the order they were written.

    The same layout `size_of` gives a tuple, said once so that whatever writes
    the members and whatever reads them agree without either knowing the other.
    """
    found: list[int] = []
    total = 0
    for member in ty.members:
        total = _align_up(total, align_of(member, layout))
        found.append(total)
        total += size_of(member, layout)
    return tuple(found)


def tag_offset_of(ty: SumType | ResultType, layout: DataLayout) -> int:
    """Where the tag of a sum, or the truth value of a result, starts."""
    if isinstance(ty, ResultType):
        return size_of(ty.ok, layout)
    return max((size_of(v, layout) for _, v in ty.variants), default=0)


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


def encode_float(value: float, ty: Type, layout: DataLayout) -> bytes:
    """The bytes a floating-point value occupies in memory.

    Written in the format the type names, which is the one the hardware reads:
    the number goes from the source to the image through one rounding, and the
    bits in the image are the bits the program will load.
    """
    import struct

    size = size_of(ty, layout)
    order = "<" if layout.little_endian else ">"
    if size == 4:
        return struct.pack("".join((order, "f")), value)
    if size == 8:
        return struct.pack("".join((order, "d")), value)
    raise ValueOutOfRangeError(int(size), ty)


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
