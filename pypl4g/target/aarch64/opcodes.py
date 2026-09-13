"""The AArch64 encoding table.

Each row is a 32-bit template plus the bits its operands occupy.  The templates
were checked against the GNU assembler, and a test keeps checking them.
"""

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandRole, OperandSpec
from .desc import A64InstDesc, Field, FieldKind, INSTRUCTION_SIZE
from .fixups import ADD_LO12, ADR_PAGE21, BRANCH19, BRANCH26
from .regs import CALLER_SAVED, GPR, NZCV, X30

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


def _shift() -> OperandSpec:
    """How far a move-wide instruction shifts its sixteen bits.

    One of four places in a word, named in bits rather than as an index, because
    that is how the assembly writes it: `lsl #32` and not `hw=2`.
    """
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=48)


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

#: A store names the value, the base register and the offset, and reads all of
#: them: nothing it names is written, only the place they point at.
_READS_ALL_THREE: Final[tuple[OperandRole, ...]] = (
    OperandRole.USE, OperandRole.USE, OperandRole.USE)

#: A comparison writes only the flags, which it declares separately.
_READS_BOTH: Final[tuple[OperandRole, ...]] = (OperandRole.USE, OperandRole.USE)


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
    # A constant wider than sixteen bits is written a quarter of a word at a
    # time: one instruction sets a quarter and clears the rest, and each that
    # follows sets a quarter and leaves the rest alone.  `movn` sets a quarter
    # and turns every bit round, which is what makes a small negative number one
    # instruction instead of four.
    #
    # The shift is written in bits and encoded as the count of quarters, which
    # is what the field's own shift is for.
    # movz Xd, #imm16, lsl #shift
    A64InstDesc("movz", (_r(64), _imm(0xFFFF), _shift()), template=0xD2800000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.IMMEDIATE, 1, 5, 16),
                        Field(FieldKind.IMMEDIATE, 2, 21, 2, shift=4)),
                est_size=INSTRUCTION_SIZE),
    # movk Xd, #imm16, lsl #shift
    A64InstDesc("movk", (_r(64), _imm(0xFFFF), _shift()), template=0xF2800000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.IMMEDIATE, 1, 5, 16),
                        Field(FieldKind.IMMEDIATE, 2, 21, 2, shift=4)),
                roles=(OperandRole.DEF_USE, OperandRole.USE, OperandRole.USE),
                est_size=INSTRUCTION_SIZE),
    # movn Xd, #imm16, lsl #shift
    A64InstDesc("movn", (_r(64), _imm(0xFFFF), _shift()), template=0x92800000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.IMMEDIATE, 1, 5, 16),
                        Field(FieldKind.IMMEDIATE, 2, 21, 2, shift=4)),
                est_size=INSTRUCTION_SIZE),
    # mov Wd, Wm   is  orr Wd, WZR, Wm
    A64InstDesc("mov", (_r(32), _r(32)), template=0x2A0003E0,
                fields=(_reg_field(0, _RD), _reg_field(1, _RM)),
                flags=InstFlags.MOVE | InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # mov Xd, Xm   is  orr Xd, XZR, Xm
    A64InstDesc("mov", (_r(64), _r(64)), template=0xAA0003E0,
                fields=(_reg_field(0, _RD), _reg_field(1, _RM)),
                est_size=INSTRUCTION_SIZE, flags=InstFlags.MOVE),
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
    # and Wd, Wn, Wm
    A64InstDesc("and", (_r(32), _r(32), _r(32)), template=0x0A000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # and Xd, Xn, Xm
    A64InstDesc("and", (_r(64), _r(64), _r(64)), template=0x8A000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # orr Wd, Wn, Wm
    A64InstDesc("orr", (_r(32), _r(32), _r(32)), template=0x2A000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # orr Xd, Xn, Xm
    A64InstDesc("orr", (_r(64), _r(64), _r(64)), template=0xAA000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # mvn Wd, Wm   is  orn Wd, WZR, Wm
    A64InstDesc("mvn", (_r(32), _r(32)), template=0x2A2003E0,
                fields=(_reg_field(0, _RD), _reg_field(1, _RM)),
                flags=InstFlags.ZEXT32, est_size=INSTRUCTION_SIZE),
    # mvn Xd, Xm   is  orn Xd, XZR, Xm
    A64InstDesc("mvn", (_r(64), _r(64)), template=0xAA2003E0,
                fields=(_reg_field(0, _RD), _reg_field(1, _RM)),
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
    # The stores, which mirror the loads.  There is no signed form: what is
    # written is whatever the register holds, narrowed to the width the
    # instruction names -- which the program has already been checked to allow.
    # strb Wt, [Xn, #imm12]
    A64InstDesc("strb", (_r(32), _r(64), _off(0)), template=0x39000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 0)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_ALL_THREE),
    # strh Wt, [Xn, #imm12]
    A64InstDesc("strh", (_r(32), _r(64), _off(1)), template=0x79000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 1)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_ALL_THREE),
    # str Wt, [Xn, #imm12]
    A64InstDesc("str", (_r(32), _r(64), _off(2)), template=0xB9000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 2)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_ALL_THREE),
    # str Xt, [Xn, #imm12]
    A64InstDesc("str", (_r(64), _r(64), _off(3)), template=0xF9000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _offset_field(2, 3)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_ALL_THREE),
    # b label
    A64InstDesc("b", (_sym(),), template=0x14000000,
                fields=(Field(FieldKind.RELOCATION, 0, 0, 26, shift=2, signed=True,
                              reloc=BRANCH26),),
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER,
                est_size=INSTRUCTION_SIZE),
    # b.eq label
    A64InstDesc("b.eq", (_sym(),), template=0x54000000,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.ne label
    A64InstDesc("b.ne", (_sym(),), template=0x54000001,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.hs label
    A64InstDesc("b.hs", (_sym(),), template=0x54000002,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.lo label
    A64InstDesc("b.lo", (_sym(),), template=0x54000003,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.hi label
    A64InstDesc("b.hi", (_sym(),), template=0x54000008,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.ls label
    A64InstDesc("b.ls", (_sym(),), template=0x54000009,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.ge label
    A64InstDesc("b.ge", (_sym(),), template=0x5400000A,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.lt label
    A64InstDesc("b.lt", (_sym(),), template=0x5400000B,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.gt label
    A64InstDesc("b.gt", (_sym(),), template=0x5400000C,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # b.le label
    A64InstDesc("b.le", (_sym(),), template=0x5400000D,
                fields=(Field(FieldKind.RELOCATION, 0, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19),),
                implicit_uses=(NZCV,), flags=InstFlags.TERMINATOR,
                est_size=INSTRUCTION_SIZE),
    # cset Wd, <cond> is csinc Wd, wzr, wzr, <the opposite condition>, so the
    # condition in the word is the opposite of the one the name says.  One row
    # per condition, as for the branches above: the condition is a field of the
    # instruction, and baking it into the template is what lets a row be chosen
    # by naming it.
    # cset Wd, eq
    A64InstDesc("cset.eq", (_r(32),), template=0x1A9F17E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, ne
    A64InstDesc("cset.ne", (_r(32),), template=0x1A9F07E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, lt
    A64InstDesc("cset.lt", (_r(32),), template=0x1A9FA7E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, le
    A64InstDesc("cset.le", (_r(32),), template=0x1A9FC7E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, gt
    A64InstDesc("cset.gt", (_r(32),), template=0x1A9FD7E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, ge
    A64InstDesc("cset.ge", (_r(32),), template=0x1A9FB7E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, lo
    A64InstDesc("cset.lo", (_r(32),), template=0x1A9F27E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, ls
    A64InstDesc("cset.ls", (_r(32),), template=0x1A9F87E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, hi
    A64InstDesc("cset.hi", (_r(32),), template=0x1A9F97E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # cset Wd, hs
    A64InstDesc("cset.hs", (_r(32),), template=0x1A9F37E0,
                fields=(_reg_field(0, _RD),), implicit_uses=(NZCV,),
                est_size=INSTRUCTION_SIZE),
    # mul Wd, Wn, Wm   is  madd Wd, Wn, Wm, WZR
    A64InstDesc("mul", (_r(32), _r(32), _r(32)), template=0x1B007C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # mul Xd, Xn, Xm
    A64InstDesc("mul", (_r(64), _r(64), _r(64)), template=0x9B007C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # umulh Xd, Xn, Xm   the upper half of an unsigned product
    A64InstDesc("umulh", (_r(64), _r(64), _r(64)), template=0x9BC07C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # smulh Xd, Xn, Xm   the upper half of a signed product
    A64InstDesc("smulh", (_r(64), _r(64), _r(64)), template=0x9B407C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, eq
    A64InstDesc("csel.eq", (_r(64), _r(64), _r(64)), template=0x9A800000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, ne
    A64InstDesc("csel.ne", (_r(64), _r(64), _r(64)), template=0x9A801000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, lt
    A64InstDesc("csel.lt", (_r(64), _r(64), _r(64)), template=0x9A80B000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, le
    A64InstDesc("csel.le", (_r(64), _r(64), _r(64)), template=0x9A80D000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, gt
    A64InstDesc("csel.gt", (_r(64), _r(64), _r(64)), template=0x9A80C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, ge
    A64InstDesc("csel.ge", (_r(64), _r(64), _r(64)), template=0x9A80A000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, lo
    A64InstDesc("csel.lo", (_r(64), _r(64), _r(64)), template=0x9A803000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, ls
    A64InstDesc("csel.ls", (_r(64), _r(64), _r(64)), template=0x9A809000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, hi
    A64InstDesc("csel.hi", (_r(64), _r(64), _r(64)), template=0x9A808000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # csel Xd, Xn, Xm, hs
    A64InstDesc("csel.hs", (_r(64), _r(64), _r(64)), template=0x9A802000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                implicit_uses=(NZCV,), est_size=INSTRUCTION_SIZE),
    # sxtw Xd, Wn   is  sbfm Xd, Xn, 0, 31
    A64InstDesc("sxtw", (_r(64), _r(32)), template=0x93407C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # cbnz Wt, label
    A64InstDesc("cbnz", (_r(32), _sym()), template=0x35000000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.RELOCATION, 1, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19)),
                flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # cbnz Xt, label
    A64InstDesc("cbnz", (_r(64), _sym()), template=0xB5000000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.RELOCATION, 1, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19)),
                flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # cbz Wt, label
    A64InstDesc("cbz", (_r(32), _sym()), template=0x34000000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.RELOCATION, 1, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19)),
                flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # cbz Xt, label
    A64InstDesc("cbz", (_r(64), _sym()), template=0xB4000000,
                fields=(_reg_field(0, _RD),
                        Field(FieldKind.RELOCATION, 1, 5, 19, shift=2, signed=True,
                              reloc=BRANCH19)),
                flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # cmp Wn, #imm12   is  subs WZR, Wn, #imm12
    A64InstDesc("cmp", (_r(32), _imm(0xFFF)), template=0x7100001F,
                fields=(_reg_field(0, _RN),
                        Field(FieldKind.IMMEDIATE, 1, 10, 12)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # cmp Xn, #imm12   is  subs XZR, Xn, #imm12
    A64InstDesc("cmp", (_r(64), _imm(0xFFF)), template=0xF100001F,
                fields=(_reg_field(0, _RN),
                        Field(FieldKind.IMMEDIATE, 1, 10, 12)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # bl label
    # A call destroys every register the convention calls caller-saved, so the
    # allocator has to be told -- otherwise a value held across one is silently
    # lost.  Naming them here puts a convention's business in the instruction
    # table, which is not where it belongs; it costs nothing today, every
    # convention this target has calling the same registers caller-saved, and
    # the entry in the to-do list says what to do when one does not.
    A64InstDesc("bl", (_sym(),), template=0x94000000,
                fields=(Field(FieldKind.RELOCATION, 0, 0, 26, shift=2, signed=True,
                              reloc=BRANCH26),),
                implicit_defs=(X30, *CALLER_SAVED), flags=InstFlags.CALL,
                est_size=INSTRUCTION_SIZE),
    # ret   (returns through the link register)
    A64InstDesc("ret", (), template=0xD65F03C0, implicit_uses=(X30,),
                flags=InstFlags.TERMINATOR | InstFlags.RETURN, est_size=INSTRUCTION_SIZE),
    # svc #imm16
    A64InstDesc("svc", (_imm(0xFFFF),), template=0xD4000001,
                fields=(Field(FieldKind.IMMEDIATE, 0, 5, 16),),
                est_size=INSTRUCTION_SIZE),
    # brk #imm16
    # udf #imm16   permanently undefined, which is what a trap should be: the
    # program is dying, and it should die the same way on every architecture.
    # `brk` would raise a different signal here than the other two raise.
    A64InstDesc("udf", (_imm(0xFFFF),), template=0x00000000,
                fields=(Field(FieldKind.IMMEDIATE, 0, 0, 16),),
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER,
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("brk", (_imm(0xFFFF),), template=0xD4200000,
                fields=(Field(FieldKind.IMMEDIATE, 0, 5, 16),),
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER,
                est_size=INSTRUCTION_SIZE),
    # nop
    A64InstDesc("nop", (), template=0xD503201F, est_size=INSTRUCTION_SIZE),
    # cmp Wn, Wm   is  subs WZR, Wn, Wm
    A64InstDesc("cmp", (_r(32), _r(32)), template=0x6B00001F,
                fields=(_reg_field(0, _RN), _reg_field(1, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # cmp Xn, Xm   is  subs XZR, Xn, Xm
    A64InstDesc("cmp", (_r(64), _r(64)), template=0xEB00001F,
                fields=(_reg_field(0, _RN), _reg_field(1, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
)
