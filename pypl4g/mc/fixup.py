"""Fixups: the places where a value is patched in once addresses are known.

Emission never blocks on an undefined symbol.  The encoder writes placeholder
bytes and records a fixup; the rule for turning a symbol address into the bytes
to store is stated once, here, and the encoder never computes a displacement
itself.
"""

from dataclasses import dataclass
from enum import Enum

from ..source.location import INVALID_SPAN, Span
from .operand import BinExpr, ConstExpr, MCExpr, SymExpr


class FixupKind(Enum):
    """How a fixup's value is computed and how wide it is."""

    ABS32 = ("abs32", 4)
    ABS64 = ("abs64", 8)
    PCREL8 = ("pcrel8", 1)
    PCREL32 = ("pcrel32", 4)

    def __init__(self, label: str, size: int) -> None:
        self.label = label
        self.size = size

    @property
    def is_pcrel(self) -> bool:
        """Whether the value is relative to the end of the fixup."""
        return self in (FixupKind.PCREL8, FixupKind.PCREL32)


@dataclass(slots=True)
class MCFixup:
    """One place to patch, once the addresses of the symbols are known."""

    #: Offset of the patched bytes within the fragment that owns the fixup.
    offset: int
    kind: FixupKind
    target: MCExpr
    #: Bytes from the end of the patched field to the end of the instruction.
    #: A displacement is relative to the end of the whole instruction, so an
    #: immediate that follows the field has to be accounted for.
    trailing: int = 0
    span: Span = INVALID_SPAN


class UnresolvedSymbol(Exception):
    """A fixup refers to a symbol that nothing defines."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


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
    """The value to store for *fixup*, whose field starts at *fixup_vaddr*."""
    target = evaluate(fixup.target)
    if not fixup.kind.is_pcrel:
        return target
    return target - (fixup_vaddr + fixup.kind.size + fixup.trailing)


def encode_fixup(fixup: MCFixup, fixup_vaddr: int) -> bytes:
    """The little-endian bytes to store for *fixup*."""
    value = fixup_value(fixup, fixup_vaddr)
    size = fixup.kind.size
    return (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")
