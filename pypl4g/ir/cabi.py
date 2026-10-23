"""How the system's C answers a record, on each architecture.

A record marked `@[abi]` answered by a function of the system's convention is
answered the way a C compiler answers the `struct` it is laid out as, since what
calls the function, or what the function calls, may be one.  The three systems
agree on the shape of the rule and on nothing else in it:

- **x86-64** (the System V psABI) cuts a record of up to sixteen bytes into
  eightbytes and answers each in a register of its class: one holding nothing
  but floating-point numbers in the next of `xmm0`, `xmm1`, any other in the
  next of `rax`, `rdx`.  Two `float`s in one eightbyte are one `xmm` register,
  the second in its upper half.
- **AArch64** (AAPCS64) answers a homogeneous floating-point aggregate -- one to
  four members, all `float` or all `double` -- one member to a register of
  `s0`/`d0` to `s3`/`d3`, whatever its size; anything else of up to sixteen
  bytes in `x0` and `x1`, as the bytes of memory are.
- **RISC-V** (LP64D) answers a record of one floating-point number, of two, or
  of one and one integer, member by member in `fa0`, `fa1` and `a0`; anything
  else of up to sixteen bytes in `a0` and `a1`, as the bytes are.

A record none of those take is answered through room the caller makes, whose
address is handed over as an argument before the others -- in the first
argument register on x86-64 and RISC-V, in `x8` on AArch64, which is no
argument register -- and on x86-64 answered back in `rax`.

What is said here is only the classification: which registers, holding which
bytes of the record as what.  `largeanswers` is what rewrites a function and its
calls to answer that way, so the three instruction selectors never see a record
answered whole and need know nothing of the rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .layout import (DataLayout, member_offsets_of, offsets_of, part_offsets_of,
                     size_of, stride_of)
from .types import (ArrayType, BoolType, CharType, EnumType, F64, FloatType,
                    IntType, ProductType, PtrType, TupleType, Type, U64, U8,
                    parts_of)

#: The layout C lays a record out with, which an `@[abi]` record asks for and
#: everything inside it follows.
SYSTEM_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8, system=True)

#: What each architecture calls the system's convention, besides `cdecl`: a
#: function naming either is answered the way C answers.  Said here rather than
#: asked of the back end, which the passes are not to import.
SYSTEM_CONVENTIONS: Final[dict[str, frozenset[str]]] = {
    "x86_64": frozenset(("cdecl", "sysv64")),
    "aarch64": frozenset(("cdecl", "aapcs64")),
    "riscv64": frozenset(("cdecl", "lp64")),
}

#: The most a record answered in registers takes, on every one of them but for
#: AArch64's homogeneous aggregates: two registers of eight bytes.
IN_REGISTERS: Final[int] = 16


@dataclass(frozen=True, slots=True)
class Piece:
    """One register's worth of a record answered in registers.

    *offset* and *size* say which bytes of the record it holds and *ty* what
    the register holds them as: a member's own type where it holds exactly that
    member, `u64` for several members or for a floating-point number in an
    ordinary register, `f64` for the two `float`s of one of x86-64's eightbytes.
    """

    ty: Type
    offset: int
    size: int


def architecture(triple: str) -> str:
    """The architecture a triple names, which is its first component."""
    return triple.split("-", 1)[0]


def follows_system(triple: str, cconv: str) -> bool:
    """Whether *cconv* is the system's convention on *triple*'s architecture."""
    return cconv in SYSTEM_CONVENTIONS.get(architecture(triple), frozenset())


def answers_its_place(triple: str) -> bool:
    """Whether a function answering through room its caller made answers with
    where that room is, as x86-64's convention has it (`rax`).  The other two
    leave it unsaid, and a caller that made the room knows where it is."""
    return architecture(triple) == "x86_64"


def answered_by_c(triple: str, record: ProductType) -> tuple[Piece, ...] | None:
    """How C answers *record* on *triple*: the registers in order, or nothing
    where it is answered through room the caller makes.

    The registers are in the order of the bytes they hold, which is the order
    each system takes a register of each kind in: a caller placing them asks
    for the next of its kind for each, as it does for the parts of any answer.
    """
    size = size_of(record, SYSTEM_LAYOUT)
    scalars = scalars_of(record, 0)
    match architecture(triple):
        case "aarch64":
            kind = scalars[0][1] if scalars else None
            # A unit is no part of what C sees: all of one width is all alike.
            if isinstance(kind, FloatType) and len(scalars) <= 4 \
                    and all(isinstance(one, FloatType) and one.bits == kind.bits
                            for _, one in scalars):
                return tuple(Piece(one, at, size_of(one, SYSTEM_LAYOUT))
                             for at, one in scalars)
            return _words(scalars, size, classed=False) if size <= IN_REGISTERS else None
        case "riscv64":
            floats = [one for _, one in scalars if isinstance(one, FloatType)]
            if floats and (len(scalars) == 1 or len(scalars) == 2) \
                    and all(plain(one) for _, one in scalars):
                return tuple(Piece(one, at, size_of(one, SYSTEM_LAYOUT))
                             for at, one in scalars)
            return _words(scalars, size, classed=False) if size <= IN_REGISTERS else None
        case _:
            return _words(scalars, size, classed=True) if size <= IN_REGISTERS else None


def _words(scalars: list[tuple[int, Type]], size: int,
           classed: bool) -> tuple[Piece, ...]:
    """The record cut into words of eight bytes, a register each.

    *classed* says a word of nothing but floating-point numbers is one of
    those registers, as x86-64 has it; elsewhere every word is an ordinary
    register holding the bytes as memory holds them.
    """
    found: list[Piece] = []
    for start in range(0, size, 8):
        width = min(8, size - start)
        inside = [(at, one) for at, one in scalars if start <= at < start + 8]
        if classed and inside and all(isinstance(one, FloatType) for _, one in inside):
            found.append(Piece(inside[0][1] if len(inside) == 1 else F64, start, width))
            continue
        if len(inside) == 1 and inside[0][0] == start and plain(inside[0][1]) \
                and not isinstance(inside[0][1], FloatType):
            found.append(Piece(inside[0][1], start, width))
            continue
        found.append(Piece(U64, start, width))
    return tuple(found)


def plain(ty: Type) -> bool:
    """Whether *ty* is one value a register holds as C would: a number, a truth
    value, a character, a value of an enumeration or an address."""
    return isinstance(ty, (IntType, FloatType, BoolType, CharType, EnumType, PtrType))


def scalars_of(ty: Type, at: int) -> list[tuple[int, Type]]:
    """Every value *ty* is made of in memory, each with where it starts.

    C's classifications are of the members a record is flattened to, an array's
    elements among them, so this walks into arrays where `parts_of` stops at
    one.  What C has no word for -- a string, a list -- is the parts it travels
    as; and one held as bytes, a sum, is bytes and nothing C would put in a
    floating-point register.
    """
    if isinstance(ty, ProductType):
        found: list[tuple[int, Type]] = []
        for (_, field), start in zip(ty.fields, offsets_of(ty, SYSTEM_LAYOUT)):
            found.extend(scalars_of(field, at + start))
        return found
    if isinstance(ty, TupleType):
        found = []
        for member, start in zip(ty.members, member_offsets_of(ty, SYSTEM_LAYOUT)):
            found.extend(scalars_of(member, at + start))
        return found
    if isinstance(ty, ArrayType) and ty.fixed:
        stride = stride_of(ty.element, SYSTEM_LAYOUT)
        found = []
        for index in range(ty.count or 0):
            found.extend(scalars_of(ty.element, at + index * stride))
        return found
    if plain(ty):
        return [(at, ty)]
    pieces = parts_of(ty)
    if pieces != (ty,):
        return [(at + start, one)
                for one, start in zip(pieces, part_offsets_of(ty, SYSTEM_LAYOUT))]
    return [(at, U8)]
