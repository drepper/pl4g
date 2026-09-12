"""Instruction descriptors and the table that holds them.

A descriptor says what an instruction *is*: its name, the operands it accepts,
the registers it touches besides those operands, and roughly how large it
encodes.  How it is encoded is not described here, because that differs entirely
between architectures -- a byte stream with prefixes and a ModRM byte on one, a
fixed-width word with bitfields on another.  Each target therefore extends this
descriptor with the fields its own encoder needs, and everything in this module
works on the part they have in common.
"""

from dataclasses import dataclass, field
from enum import Flag, auto
from typing import Iterable, Sequence

from .operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef
from .reg import PhysReg, RegClass, Reg


class OperandKind(Flag):
    """The shapes an operand slot will accept."""

    REG = auto()
    MEM = auto()
    IMM = auto()
    REL = auto()
    SYM = auto()


@dataclass(frozen=True, slots=True)
class OperandSpec:
    """What one operand slot of an instruction accepts."""

    kinds: OperandKind
    rclass: RegClass | None = None
    bits: int | None = None
    #: A slot that is always one particular register, such as the zero register
    #: or the register a shift count must be held in.
    fixed: PhysReg | None = None
    #: The largest value an immediate slot accepts, where the encoding can hold
    #: less than its width suggests.
    imm_max: int | None = None
    #: The smallest value an immediate slot accepts.
    imm_min: int | None = None

    def matches(self, operand: MCOperand) -> bool:
        """Whether *operand* may be used in this slot."""
        match operand:
            case MCReg():
                if OperandKind.REG not in self.kinds:
                    return False
                reg: Reg = operand.reg
                if self.fixed is not None:
                    return isinstance(reg, PhysReg) and reg is self.fixed
                if self.rclass is not None and reg.cls is not self.rclass:
                    return False
                return self.bits is None or reg.bits == self.bits
            case MCMem():
                return OperandKind.MEM in self.kinds
            case MCImm():
                if OperandKind.IMM not in self.kinds:
                    return False
                if self.bits is not None and operand.bits > self.bits:
                    return False
                if self.imm_max is not None and operand.value > self.imm_max:
                    return False
                if self.imm_min is not None and operand.value < self.imm_min:
                    return False
                return True
            case MCSymRef():
                return bool(self.kinds & (OperandKind.REL | OperandKind.SYM))
            case _:
                return False


class InstFlags(Flag):
    """Properties of an instruction that passes need to know."""

    NONE = 0
    TERMINATOR = auto()
    CALL = auto()
    BARRIER = auto()
    MAY_LOAD = auto()
    MAY_STORE = auto()
    #: A write of the narrow view clears the rest of the register.  True of a
    #: 32-bit write on both x86-64 and AArch64, and worth recording because a
    #: peephole that widens a value depends on it.
    ZEXT32 = auto()
    LOCKABLE = auto()


@dataclass(frozen=True, slots=True)
class InstDesc:
    """What an instruction is, without saying how it is encoded.

    A target subclasses this and adds the fields its encoder reads.  Nothing
    outside a target's own encoder looks at those fields.
    """

    mnemonic: str
    operands: tuple[OperandSpec, ...] = ()
    implicit_defs: tuple[PhysReg, ...] = ()
    implicit_uses: tuple[PhysReg, ...] = ()
    flags: InstFlags = InstFlags.NONE
    #: The encoded size in bytes, used to choose between rows that overlap.  On
    #: a fixed-width architecture every row states the same number, and the
    #: choice falls through to the order the rows are written in.
    est_size: int = 0

    def matches(self, operands: Sequence[MCOperand]) -> bool:
        """Whether this row accepts the given operands."""
        if len(operands) != len(self.operands):
            return False
        return all(spec.matches(op) for spec, op in zip(self.operands, operands))


class SelectionError(Exception):
    """No row of the table accepts the given operands."""

    def __init__(self, mnemonic: str, operands: Sequence[MCOperand]) -> None:
        described = ", ".join(o.render() for o in operands)
        super().__init__("".join(("no encoding of '", mnemonic, "' accepts ", described)))
        self.mnemonic = mnemonic
        self.operands = tuple(operands)


class InstrTable:
    """The encoding table of one target, indexed by mnemonic."""

    def __init__(self, rows: Iterable[InstDesc]) -> None:
        self._rows: dict[str, list[InstDesc]] = {}
        for row in rows:
            self._rows.setdefault(row.mnemonic, []).append(row)

    @property
    def mnemonics(self) -> list[str]:
        """Every mnemonic the table knows."""
        return list(self._rows)

    def rows(self, mnemonic: str | None = None) -> list[InstDesc]:
        """Every row, or every row of one mnemonic."""
        if mnemonic is None:
            return [r for rows in self._rows.values() for r in rows]
        return list(self._rows.get(mnemonic, ()))

    def select(self, mnemonic: str, operands: Sequence[MCOperand]) -> InstDesc:
        """Choose the shortest row that accepts *operands*.

        Preferring the shortest encoding is a policy of the assembler rather than
        a decision made at the call site: the compiler must generate small code,
        and nothing that builds an instruction should have to know that one form
        of an instruction is shorter than another.  Where two rows encode to the
        same size the one written first wins, so a table states its preference by
        its order.
        """
        candidates = [r for r in self._rows.get(mnemonic, ()) if r.matches(operands)]
        if not candidates:
            raise SelectionError(mnemonic, operands)
        return min(candidates, key=lambda r: (r.est_size, self._rows[mnemonic].index(r)))
