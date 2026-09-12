"""Instruction descriptors and the table that holds them.

An encoding is described by data, not by code: a row states which prefixes it
needs, which opcode map it lives in, how its operands map onto ModRM and which
immediate it carries.  One generic emitter walks a fixed sequence of phases over
that data, so adding an operation is a row, and adding a whole new prefix family
-- VEX, EVEX, the APX two-byte REX -- is one more emitter in the prefix phase
plus optional fields here.
"""

from dataclasses import dataclass
from enum import Enum, Flag, auto
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
    #: A slot that is always one particular register, such as cl for a shift.
    fixed: PhysReg | None = None

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
                return True
            case MCSymRef():
                return bool(self.kinds & (OperandKind.REL | OperandKind.SYM))
            case _:
                return False


class EncKind(Enum):
    """Which prefix family an encoding belongs to."""

    LEGACY = "legacy"
    REX2 = "rex2"
    VEX = "vex"
    EVEX = "evex"


class OpMap(Enum):
    """Which opcode map the opcode byte lives in.

    The numbering is the one the VEX and EVEX ``mmmmm`` field uses, so those
    encodings reuse this field unchanged.
    """

    PRIMARY = 0
    M0F = 1
    M0F38 = 2
    M0F3A = 3


class OpSize(Enum):
    """Which operand size the encoding selects."""

    DEFAULT = "default"
    P66 = "p66"
    REXW = "rexw"


class ModRMUse(Enum):
    """How the ModRM byte is filled in, if there is one."""

    NONE = "none"
    #: ModRM.reg names an operand, ModRM.rm names another.
    REG_RM = "reg_rm"
    #: ModRM.reg holds a fixed extension digit, ModRM.rm names an operand.
    EXT_RM = "ext_rm"


class InstFlags(Flag):
    """Properties of an instruction that passes need to know."""

    NONE = 0
    TERMINATOR = auto()
    CALL = auto()
    BARRIER = auto()
    MAY_LOAD = auto()
    MAY_STORE = auto()
    #: A 32-bit write clears the upper half of the 64-bit register.
    ZEXT32 = auto()
    LOCKABLE = auto()


@dataclass(frozen=True, slots=True)
class VexInfo:
    """Reserved: the extra fields a VEX or EVEX encoding needs."""

    length: int = 128
    vvvv_op: int | None = None
    mask_op: int | None = None
    broadcast: bool = False


@dataclass(frozen=True, slots=True)
class InstDesc:
    """One row of the encoding table."""

    mnemonic: str
    operands: tuple[OperandSpec, ...]
    opcode: int
    map: OpMap = OpMap.PRIMARY
    enc: EncKind = EncKind.LEGACY
    opsize: OpSize = OpSize.DEFAULT
    #: A prefix byte that selects the instruction rather than the operand size.
    mandatory_prefix: int | None = None
    modrm: ModRMUse = ModRMUse.NONE
    #: The digit stored in ModRM.reg for an EXT_RM encoding.
    ext: int | None = None
    reg_op: int | None = None
    rm_op: int | None = None
    #: The low three bits of the register are added to the opcode byte.
    plus_reg: bool = False
    imm_op: int | None = None
    imm_bits: int | None = None
    rel_op: int | None = None
    rel_bits: int | None = None
    implicit_defs: tuple[PhysReg, ...] = ()
    implicit_uses: tuple[PhysReg, ...] = ()
    flags: InstFlags = InstFlags.NONE
    vex: VexInfo | None = None
    #: A rough encoded size, used to choose between overlapping rows.
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
        of ``mov`` is four bytes shorter than another.
        """
        candidates = [r for r in self._rows.get(mnemonic, ()) if r.matches(operands)]
        if not candidates:
            raise SelectionError(mnemonic, operands)
        return min(candidates, key=lambda r: (r.est_size, self._rows[mnemonic].index(r)))
