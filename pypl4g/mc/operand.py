"""Operands and the expressions that can stand for an address.

Expressions exist from the start even though code generation only ever produces
a bare symbol reference: label arithmetic is what inline assembly will need, and
the fixup evaluator is written against expressions so that adding it later is not
a retrofit.
"""

from dataclasses import dataclass
from enum import Enum

from .reg import PhysReg, Reg
from .symbol import MCSymbol


class RelocKind(Enum):
    """How the value of a symbol reference is to be computed."""

    ABS = "abs"
    PCREL = "pcrel"
    GOT = "got"
    PLT = "plt"


@dataclass(frozen=True, slots=True)
class MCExpr:
    """Base of the expressions that may stand where an address is wanted."""

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ConstExpr(MCExpr):
    """A constant."""

    value: int

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        return str(self.value)


@dataclass(frozen=True, slots=True)
class SymExpr(MCExpr):
    """The address of a symbol."""

    symbol: MCSymbol

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        return self.symbol.name


@dataclass(frozen=True, slots=True)
class BinExpr(MCExpr):
    """The sum or difference of two expressions."""

    op: str
    lhs: MCExpr
    rhs: MCExpr

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        return "".join(("(", self.lhs.render(), " ", self.op, " ", self.rhs.render(), ")"))


@dataclass(frozen=True, slots=True)
class MCReg:
    """A register operand."""

    reg: Reg

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        return self.reg.name if isinstance(self.reg, PhysReg) else repr(self.reg)


@dataclass(frozen=True, slots=True)
class MCImm:
    """An immediate operand of a stated width and signedness."""

    value: int
    bits: int
    signed: bool = True

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        return str(self.value)

    def fits(self) -> bool:
        """Whether the value is representable in the stated width."""
        if self.signed:
            return -(1 << (self.bits - 1)) <= self.value < (1 << (self.bits - 1))
        return 0 <= self.value < (1 << self.bits)


@dataclass(frozen=True, slots=True)
class MCMem:
    """A memory operand."""

    base: Reg | None = None
    index: Reg | None = None
    scale: int = 1
    disp: int = 0
    disp_sym: MCExpr | None = None
    seg: PhysReg | None = None
    #: Relative to the end of the instruction rather than to a base register.
    rip_relative: bool = False
    #: The width of the access, where the instruction does not imply it.
    size_bits: int | None = None
    #: Whether a value narrower than the register it lands in arrives widened by
    #: its sign rather than by zeroes.  It is a property of the access, which is
    #: what this operand describes, and not of the address.
    signed: bool = False

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        parts: list[str] = []
        if self.seg is not None:
            parts.append("".join((self.seg.name, ":")))
        inner: list[str] = []
        if self.rip_relative:
            inner.append("rip")
        elif self.base is not None:
            inner.append(self.base.name if isinstance(self.base, PhysReg) else repr(self.base))
        if self.index is not None:
            name = self.index.name if isinstance(self.index, PhysReg) else repr(self.index)
            inner.append("".join((name, "*", str(self.scale))))
        if self.disp_sym is not None:
            inner.append(self.disp_sym.render())
        if self.disp != 0 or not inner:
            inner.append(str(self.disp))
        parts.append("".join(("[", " + ".join(inner), "]")))
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class MCSymRef:
    """A reference to a symbol, as a branch target or an address."""

    expr: MCExpr
    kind: RelocKind = RelocKind.PCREL
    addend: int = 0

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        if self.addend == 0:
            return self.expr.render()
        return "".join((self.expr.render(), "+", str(self.addend)))


type MCOperand = MCReg | MCImm | MCMem | MCSymRef


def reg_of(operand: MCOperand) -> Reg | None:
    """The register an operand names, if it names one."""
    return operand.reg if isinstance(operand, MCReg) else None
