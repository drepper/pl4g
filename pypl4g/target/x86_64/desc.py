"""How an x86-64 instruction is encoded.

An encoding is described by data, not by code: a row states which prefixes it
needs, which opcode map it lives in, how its operands map onto the ModRM byte,
and which immediate it carries.  One generic emitter walks a fixed sequence of
phases over that data, so adding an operation is a row, and adding a whole
prefix family -- VEX, EVEX, the two-byte REX of the extended registers -- is one
more emitter in the prefix phase plus the fields it needs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...mc.desc import InstDesc


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


@dataclass(frozen=True, slots=True)
class VexInfo:
    """The extra fields a VEX or EVEX encoding needs.

    *length* is how wide the vector registers the instruction names are, which
    is the one field that makes the sixteen-byte and the thirty-two-byte form of
    an instruction the same row with one number changed.

    *vvvv_op* names the operand that goes in the prefix's own register field,
    which is what makes these forms take three operands and need no move before
    them.  The other two fields are for what EVEX adds and nothing reads them
    yet.
    """

    length: int = 128
    vvvv_op: int | None = None
    mask_op: int | None = None
    broadcast: bool = False


@dataclass(frozen=True, slots=True)
class X86InstDesc(InstDesc):
    """One row of the x86-64 encoding table."""

    opcode: int = 0
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
    vex: VexInfo | None = None
