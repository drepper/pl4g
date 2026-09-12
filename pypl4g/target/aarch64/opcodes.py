"""The AArch64 encoding table.

Each row is a 32-bit template plus the bits its operands occupy.  The templates
were checked against the GNU assembler, and a test keeps checking them.
"""

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandSpec
from .desc import A64InstDesc, Field, FieldKind, INSTRUCTION_SIZE
from .fixups import ADD_LO12, ADR_PAGE21, BRANCH26
from .regs import GPR, NZCV, X30

#: The word the architecture reserves as permanently undefined.  It is what
#: padding is filled with, so that falling into padding traps.
UDF_WORD: Final[int] = 0x00000000
PAD_BYTE: Final[int] = 0x00


def _r(bits: int) -> OperandSpec:
    """A general-purpose register of the given width."""
    return OperandSpec(OperandKind.REG, rclass=GPR, bits=bits)


def _imm(maximum: int) -> OperandSpec:
    """An immediate the encoding can actually hold.

    What matters on a fixed-width architecture is whether the *value* fits the
    field, not how wide the operand was declared: there is no shorter encoding
    to fall back to, so a narrow constant and a wide one are the same
    instruction.  That is why no width is stated here, unlike in the x86-64
    table where the declared width is what chooses between two encodings.
    """
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=maximum)


def _sym() -> OperandSpec:
    """A branch target or an address, given as a symbol reference."""
    return OperandSpec(OperandKind.REL | OperandKind.SYM)


def _reg_field(operand: int, lsb: int) -> Field:
    """A five-bit register number."""
    return Field(FieldKind.REGISTER, operand, lsb)


def _off(scale: int) -> OperandSpec:
    """The offset of a load, which the encoding holds divided by the access size."""
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=0xFFF << scale)


def _offset_field(operand: int, scale: int) -> Field:
    """The twelve bits a load's offset occupies, scaled by the access size."""
    return Field(FieldKind.IMMEDIATE, operand, 10, 12, shift=scale)


#: Rd, Rn and Rm always sit in the same places.
_RD = 0
_RN = 5
_RM = 16

AARCH64_INSTRS: Final[tuple[A64InstDesc, ...]] = (
    # movz Wd, #imm16                    sf=0 10 100101 hw=00
    A64InstDesc("movz", (_r(32), _imm(0xFFFF)), template=0x52800000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.IMMEDIATE, 1, 5, 16)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # movz Xd, #imm16                    sf=1
    A64InstDesc("movz", (_r(64), _imm(0xFFFF)), template=0xD2800000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.IMMEDIATE, 1, 5, 16)),
                est_size=INSTRUCTION_SIZE),
    # mov Wd, Wm   is  orr Wd, WZR, Wm
    A64InstDesc("mov", (_r(32), _r(32)), template=0x2A0003E0,
                fields=(_reg_field(0, _RD), _reg_field(1, _RM)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # mov Xd, Xm   is  orr Xd, XZR, Xm
    A64InstDesc("mov", (_r(64), _r(64)), template=0xAA0003E0,
                fields=(_reg_field(0, _RD), _reg_field(1, _RM)),
                est_size=INSTRUCTION_SIZE),
    # add Wd, Wn, Wm
    A64InstDesc("add", (_r(32), _r(32), _r(32)), template=0x0B000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # add Xd, Xn, Xm
    A64InstDesc("add", (_r(64), _r(64), _r(64)), template=0x8B000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # add Wd, Wn, #imm12
    A64InstDesc("add", (_r(32), _r(32), _imm(0xFFF)), template=0x11000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # add Xd, Xn, #imm12
    A64InstDesc("add", (_r(64), _r(64), _imm(0xFFF)), template=0x91000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                est_size=INSTRUCTION_SIZE),
    # sub Wd, Wn, Wm
    A64InstDesc("sub", (_r(32), _r(32), _r(32)), template=0x4B000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # sub Xd, Xn, Xm
    A64InstDesc("sub", (_r(64), _r(64), _r(64)), template=0xCB000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # sub Wd, Wn, #imm12
    A64InstDesc("sub", (_r(32), _r(32), _imm(0xFFF)), template=0x51000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # sub Xd, Xn, #imm12
    A64InstDesc("sub", (_r(64), _r(64), _imm(0xFFF)), template=0xD1000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                est_size=INSTRUCTION_SIZE),
    # eor Wd, Wn, Wm
    A64InstDesc("eor", (_r(32), _r(32), _r(32)), template=0x4A000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # eor Xd, Xn, Xm
    A64InstDesc("eor", (_r(64), _r(64), _r(64)), template=0xCA000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # adrp Xd, page-of-symbol
    A64InstDesc("adrp", (_r(64), _sym()), template=0x90000000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.RELOCATION, 1, 5, 19, reloc=ADR_PAGE21)),
                est_size=INSTRUCTION_SIZE),
    # add Xd, Xn, #offset-within-page
    A64InstDesc("add.lo12", (_r(64), _r(64), _sym()), template=0x91000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.RELOCATION, 2, 10, 12, reloc=ADD_LO12)),
                est_size=INSTRUCTION_SIZE),
    # The loads.  The immediate offset is scaled by the size of the access, so
    # each row states its own shift; an offset that is not a multiple of that
    # size has no encoding and is refused rather than rounded.
    # ldrb Wt, [Xn, #imm12]
    A64InstDesc("ldrb", (_r(32), _r(64), _off(0)), template=0x39400000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 0)),
                flags=InstFlags.MAY_LOAD | InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # ldrsb Wt, [Xn, #imm12]
    A64InstDesc("ldrsb", (_r(32), _r(64), _off(0)), template=0x39C00000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 0)),
                flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    # ldrh Wt, [Xn, #imm12]
    A64InstDesc("ldrh", (_r(32), _r(64), _off(1)), template=0x79400000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 1)),
                flags=InstFlags.MAY_LOAD | InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # ldrsh Wt, [Xn, #imm12]
    A64InstDesc("ldrsh", (_r(32), _r(64), _off(1)), template=0x79C00000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 1)),
                flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    # ldr Wt, [Xn, #imm12]
    A64InstDesc("ldr", (_r(32), _r(64), _off(2)), template=0xB9400000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 2)),
                flags=InstFlags.MAY_LOAD | InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # ldrsw Xt, [Xn, #imm12]
    A64InstDesc("ldrsw", (_r(64), _r(64), _off(2)), template=0xB9800000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 2)),
                flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    # ldr Xt, [Xn, #imm12]
    A64InstDesc("ldr", (_r(64), _r(64), _off(3)), template=0xF9400000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 3)),
                flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    # bl label
    A64InstDesc("bl", (_sym(),), template=0x94000000,
                fields=(Field(FieldKind.RELOCATION, 0, 0, 26, shift=2, signed=True,
                              reloc=BRANCH26),),
                implicit_defs=(X30,), flags=InstFlags.CALL, est_size=INSTRUCTION_SIZE),
    # ret   (returns through the link register)
    A64InstDesc("ret", (), template=0xD65F03C0, implicit_uses=(X30,),
                flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE),
    # svc #imm16
    A64InstDesc("svc", (_imm(0xFFFF),), template=0xD4000001,
                fields=(Field(FieldKind.IMMEDIATE, 0, 5, 16),),
                est_size=INSTRUCTION_SIZE),
    # brk #imm16
    A64InstDesc("brk", (_imm(0xFFFF),), template=0xD4200000,
                fields=(Field(FieldKind.IMMEDIATE, 0, 5, 16),),
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER,
                est_size=INSTRUCTION_SIZE),
    # nop
    A64InstDesc("nop", (), template=0xD503201F, est_size=INSTRUCTION_SIZE),
    # cmp Wn, Wm   is  subs WZR, Wn, Wm
    A64InstDesc("cmp", (_r(32), _r(32)), template=0x6B00001F,
                fields=(_reg_field(0, _RN), _reg_field(1, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE),
    # cmp Xn, Xm   is  subs XZR, Xn, Xm
    A64InstDesc("cmp", (_r(64), _r(64)), template=0xEB00001F,
                fields=(_reg_field(0, _RN), _reg_field(1, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE),
)
