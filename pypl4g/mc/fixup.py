"""Fixups: the places where a value is patched in once addresses are known.

Emission never blocks on an undefined symbol.  The encoder writes placeholder
bytes and records a fixup; the rule for turning a symbol address into the number
to store is stated once, here, and the encoder never computes a displacement
itself.

Storing that number is a separate question, and one only the target can answer:
on a byte-stream architecture the number goes into a little-endian field, while
on a fixed-width one it goes into a bitfield of an instruction word that already
holds other things.  A kind therefore says how its value is *computed*; each
target says how its own kinds are *stored*.

Kinds are values rather than members of one enumeration, for the same reason
operations are: a target registers the ones it needs without this module having
to know about them.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Final

from ..source.location import INVALID_SPAN, Span
from .operand import BinExpr, ConstExpr, MCExpr, SymExpr


class FixupBase(Enum):
    """What the stored value is measured from."""

    #: The address itself.
    ABSOLUTE = "absolute"
    #: The end of the patched field, plus anything the instruction puts after
    #: it.  This is what an x86-64 displacement is relative to.
    FIELD_END = "field-end"
    #: The start of the patched field, which on a fixed-width architecture is
    #: the address of the instruction itself.
    FIELD_START = "field-start"


@dataclass(frozen=True, slots=True)
class FixupKind:
    """One way of referring to an address from inside an instruction."""

    name: str
    #: The number of bytes the patch reads and writes back.
    size: int
    base: FixupBase = FixupBase.ABSOLUTE

    @property
    def label(self) -> str:
        """The name used in the debugging dump."""
        return self.name

    @property
    def is_pcrel(self) -> bool:
        """Whether the value is relative to where it is stored."""
        return self.base is not FixupBase.ABSOLUTE

    def __repr__(self) -> str:
        return "".join(("FixupKind(", self.name, ")"))


#: The kinds every architecture has.  A target adds its own the same way.
ABS32: Final[FixupKind] = FixupKind("abs32", 4)
ABS64: Final[FixupKind] = FixupKind("abs64", 8)
PCREL8: Final[FixupKind] = FixupKind("pcrel8", 1, FixupBase.FIELD_END)
PCREL32: Final[FixupKind] = FixupKind("pcrel32", 4, FixupBase.FIELD_END)


@dataclass(slots=True)
class MCFixup:
    """One place to patch, once the addresses of the symbols are known."""

    #: Offset of the patched bytes within the fragment that owns the fixup.
    offset: int
    kind: FixupKind
    target: MCExpr
    #: Bytes from the end of the patched field to the end of the instruction.
    #: A displacement measured from the end of the field has to account for an
    #: immediate that follows it.
    trailing: int = 0
    span: Span = INVALID_SPAN


class UnresolvedSymbol(Exception):
    """A fixup refers to a symbol that nothing defines."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class FixupRangeError(Exception):
    """The value a fixup needs does not fit in the field that holds it."""

    def __init__(self, kind: FixupKind, value: int) -> None:
        super().__init__("".join((
            "a value of ", str(value), " does not fit the ", kind.name, " field")))
        self.kind = kind
        self.value = value


def evaluate(expr: MCExpr) -> int:
    """Evaluate an expression to an address.

    Every symbol it mentions must already have been assigned an address.
    """
    match expr:
        case ConstExpr():
            return expr.value
        case SymExpr():
            if expr.symbol.vaddr is None:
                raise UnresolvedSymbol(expr.symbol.name)
            return expr.symbol.vaddr
        case BinExpr():
            lhs = evaluate(expr.lhs)
            rhs = evaluate(expr.rhs)
            return lhs + rhs if expr.op == "+" else lhs - rhs
        case _:
            raise UnresolvedSymbol("<expression>")


def fixup_value(fixup: MCFixup, fixup_vaddr: int) -> int:
    """The number to store for *fixup*, whose field starts at *fixup_vaddr*."""
    target = evaluate(fixup.target)
    match fixup.kind.base:
        case FixupBase.ABSOLUTE:
            return target
        case FixupBase.FIELD_END:
            return target - (fixup_vaddr + fixup.kind.size + fixup.trailing)
        case FixupBase.FIELD_START:
            return target - fixup_vaddr


#: How a target stores a computed fixup value into the bytes it was found in.
type FixupApplier = Callable[[bytearray, int, MCFixup, int], None]


def apply_little_endian(data: bytearray, offset: int, fixup: MCFixup,
                        value: int) -> None:
    """Store *value* as a little-endian field of the kind's width.

    This is how a byte-stream architecture patches: the field holds nothing but
    the value, so it is overwritten outright.
    """
    size = fixup.kind.size
    data[offset:offset + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")


def signed_fits(value: int, bits: int) -> bool:
    """Whether *value* is representable as a signed field of *bits* bits."""
    return -(1 << (bits - 1)) <= value < (1 << (bits - 1))
