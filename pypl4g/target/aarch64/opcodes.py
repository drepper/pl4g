"""The AArch64 encoding table.

Each row is a 32-bit template plus the bits its operands occupy.  The templates
were checked against the GNU assembler, and a test keeps checking them.
"""

from __future__ import annotations

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandRole, OperandSpec
from .desc import A64InstDesc, Field, FieldKind, INSTRUCTION_SIZE
from .fixups import ADD_LO12, ADR_PAGE21, BRANCH19, BRANCH26
from .regs import GPR, NZCV, VEC, X30

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


def _v(bits: int) -> OperandSpec:
    """One of the floating-point registers, named at the width of the value."""
    return OperandSpec(OperandKind.REG, rclass=VEC, bits=bits)


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
    # adds Wd, Wn, Wm / subs, and their immediate forms.  The flag-setting
    # pair, which differ from the plain ones by one bit and by leaving behind
    # whether the answer went past the end of the width they were done at.
    A64InstDesc("adds", (_r(32), _r(32), _r(32)), template=0x2B000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                implicit_defs=(NZCV,), flags=InstFlags.ZEXT32,
                est_size=INSTRUCTION_SIZE),
    # adds Xd, Xn, Xm
    A64InstDesc("adds", (_r(64), _r(64), _r(64)), template=0xAB000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE),
    # adds Wd, Wn, #imm12
    A64InstDesc("adds", (_r(32), _r(32), _imm(0xFFF)), template=0x31000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                implicit_defs=(NZCV,), flags=InstFlags.ZEXT32,
                est_size=INSTRUCTION_SIZE),
    # adds Xd, Xn, #imm12
    A64InstDesc("adds", (_r(64), _r(64), _imm(0xFFF)), template=0xB1000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE),
    # subs Wd, Wn, Wm
    A64InstDesc("subs", (_r(32), _r(32), _r(32)), template=0x6B000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                implicit_defs=(NZCV,), flags=InstFlags.ZEXT32,
                est_size=INSTRUCTION_SIZE),
    # subs Xd, Xn, Xm
    A64InstDesc("subs", (_r(64), _r(64), _r(64)), template=0xEB000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE),
    # subs Wd, Wn, #imm12
    A64InstDesc("subs", (_r(32), _r(32), _imm(0xFFF)), template=0x71000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                implicit_defs=(NZCV,), flags=InstFlags.ZEXT32,
                est_size=INSTRUCTION_SIZE),
    # subs Xd, Xn, #imm12
    A64InstDesc("subs", (_r(64), _r(64), _imm(0xFFF)), template=0xF1000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        Field(FieldKind.IMMEDIATE, 2, 10, 12)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE),
    # -- a whole run of elements in one register -------------------------------
    # The Advanced SIMD instructions, which are in the base this compiler builds
    # for: a processor of this architecture that runs Linux has them, so a run of
    # elements is done this way and there is no level to ask for it with.
    #
    # The arrangement -- how the sixteen bytes are divided into lanes -- is part
    # of the instruction and not of its operands, so it is part of the mnemonic
    # here, written the way the assembly writes it.  Two registers named the same
    # way differ only in how the bits in them are grouped, which no operand can
    # say.
    #
    # ldr Qt, [Xn, #imm12]
    A64InstDesc("ldr", (_v(128), _r(64), _off(4)), template=0x3DC00000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _offset_field(2, 4)),
                flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    # str Qt, [Xn, #imm12]
    A64InstDesc("str", (_v(128), _r(64), _off(4)), template=0x3D800000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _offset_field(2, 4)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_ALL_THREE),
    # The bitwise three, whose answer does not depend on how the bits are
    # divided into lanes, so there is one arrangement and it is bytes.
    A64InstDesc("and.16b", (_v(128), _v(128), _v(128)), template=0x4E201C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("orr.16b", (_v(128), _v(128), _v(128)), template=0x4EA01C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("eor.16b", (_v(128), _v(128), _v(128)), template=0x6E201C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # not Vd.16B, Vn.16B
    A64InstDesc("not.16b", (_v(128), _v(128)), template=0x6E205800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # The arithmetic, one instruction per arrangement.  What each does to a
    # lane it does to that lane alone: there is no carry between them.
    A64InstDesc("add.16b", (_v(128), _v(128), _v(128)), template=0x4E208400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("add.8h", (_v(128), _v(128), _v(128)), template=0x4E608400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("add.4s", (_v(128), _v(128), _v(128)), template=0x4EA08400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("add.2d", (_v(128), _v(128), _v(128)), template=0x4EE08400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sub.16b", (_v(128), _v(128), _v(128)), template=0x6E208400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sub.8h", (_v(128), _v(128), _v(128)), template=0x6E608400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sub.4s", (_v(128), _v(128), _v(128)), template=0x6EA08400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sub.2d", (_v(128), _v(128), _v(128)), template=0x6EE08400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # mul Vd.<T>, Vn.<T>, Vm.<T> -- the low half of the product in every lane,
    # which is all a multiplication that may wrap wants.  There is no form for
    # the widest lane.
    A64InstDesc("mul.16b", (_v(128), _v(128), _v(128)), template=0x4E209C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("mul.8h", (_v(128), _v(128), _v(128)), template=0x4E609C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("mul.4s", (_v(128), _v(128), _v(128)), template=0x4EA09C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # The saturating forms, which stop at the end of the lane's type rather
    # than going past it.  Unlike the other machine here, this one has them
    # at every width.
    A64InstDesc("uqadd.16b", (_v(128), _v(128), _v(128)), template=0x6E200C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("uqadd.8h", (_v(128), _v(128), _v(128)), template=0x6E600C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("uqadd.4s", (_v(128), _v(128), _v(128)), template=0x6EA00C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("uqadd.2d", (_v(128), _v(128), _v(128)), template=0x6EE00C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqadd.16b", (_v(128), _v(128), _v(128)), template=0x4E200C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqadd.8h", (_v(128), _v(128), _v(128)), template=0x4E600C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqadd.4s", (_v(128), _v(128), _v(128)), template=0x4EA00C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqadd.2d", (_v(128), _v(128), _v(128)), template=0x4EE00C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("uqsub.16b", (_v(128), _v(128), _v(128)), template=0x6E202C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("uqsub.8h", (_v(128), _v(128), _v(128)), template=0x6E602C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("uqsub.4s", (_v(128), _v(128), _v(128)), template=0x6EA02C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("uqsub.2d", (_v(128), _v(128), _v(128)), template=0x6EE02C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqsub.16b", (_v(128), _v(128), _v(128)), template=0x4E202C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqsub.8h", (_v(128), _v(128), _v(128)), template=0x4E602C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqsub.4s", (_v(128), _v(128), _v(128)), template=0x4EA02C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sqsub.2d", (_v(128), _v(128), _v(128)), template=0x4EE02C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # dup Vd.<T>, Wn -- one ordinary register's value in every lane.
    A64InstDesc("dup.16b", (_v(128), _r(32)), template=0x4E010C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("dup.8h", (_v(128), _r(32)), template=0x4E020C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("dup.4s", (_v(128), _r(32)), template=0x4E040C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("dup.2d", (_v(128), _r(64)), template=0x4E080C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # umaxv Bd, Vn.16B -- the largest byte of the register, in the low byte of
    # the destination.  This is how a question asked in every lane at once
    # becomes the one question a branch asks: the bytes that answer "yes" hold
    # the top bit and no others hold anything, so the largest of them is not
    # zero exactly when one of them said yes.
    A64InstDesc("umaxv.16b", (_v(128), _v(128)), template=0x6E30A800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # cmlt Vd.<T>, Vn.<T>, #0 -- every bit of a lane set where that lane's own
    # top bit was, and every bit clear where it was not.  That is what turns
    # "the top bit of each lane says whether it went past" into a value whose
    # every byte says it, which is what lets the answer be reduced by looking at
    # bytes without knowing how they were grouped.
    A64InstDesc("cmlt.16b", (_v(128), _v(128)), template=0x4E20A800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("cmlt.8h", (_v(128), _v(128)), template=0x4E60A800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("cmlt.4s", (_v(128), _v(128)), template=0x4EA0A800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("cmlt.2d", (_v(128), _v(128)), template=0x4EE0A800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # umaxv Bd, Vn.8B -- the same over the low eight bytes only.
    A64InstDesc("umaxv.8b", (_v(128), _v(128)), template=0x2E30A800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # fmov Xd, Dn -- the low eight bytes into an ordinary register.
    A64InstDesc("fmov", (_r(64), _v(64)), template=0x9E660000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # fmov Wd, Sn -- and out of the register it ended up in.
    A64InstDesc("fmov", (_r(32), _v(32)), template=0x1E260000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
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
    # The ordered forms.  They carry no offset -- the address is a register and
    # nothing else -- which is why what selects one adds the offset first.
    # ldarb Wt, [Xn]
    A64InstDesc("ldarb", (_r(32), _r(64)), template=0x08DFFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_LOAD | InstFlags.ZEXT32,
                est_size=INSTRUCTION_SIZE),
    # ldarh Wt, [Xn]
    A64InstDesc("ldarh", (_r(32), _r(64)), template=0x48DFFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_LOAD | InstFlags.ZEXT32,
                est_size=INSTRUCTION_SIZE),
    # ldar Wt, [Xn]
    A64InstDesc("ldar", (_r(32), _r(64)), template=0x88DFFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_LOAD | InstFlags.ZEXT32,
                est_size=INSTRUCTION_SIZE),
    # ldar Xt, [Xn]
    A64InstDesc("ldar", (_r(64), _r(64)), template=0xC8DFFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    # stlrb Wt, [Xn]
    A64InstDesc("stlrb", (_r(32), _r(64)), template=0x089FFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # stlrh Wt, [Xn]
    A64InstDesc("stlrh", (_r(32), _r(64)), template=0x489FFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # stlr Wt, [Xn]
    A64InstDesc("stlr", (_r(32), _r(64)), template=0x889FFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    # stlr Xt, [Xn]
    A64InstDesc("stlr", (_r(64), _r(64)), template=0xC89FFC00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                flags=InstFlags.MAY_STORE, est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
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
    # b.vc label
    # The overflow flag clear, which for a flag-setting signed operation is the
    # whole of "it did not go past the end of its width".
    A64InstDesc("b.vc", (_sym(),), template=0x54000007,
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
    # sxtb Xd, Wn   is  sbfm Xd, Xn, 0, 7
    A64InstDesc("sxtb", (_r(64), _r(32)), template=0x93401C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # sxth Xd, Wn   is  sbfm Xd, Xn, 0, 15
    A64InstDesc("sxth", (_r(64), _r(32)), template=0x93403C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # sxtw Xd, Wn   is  sbfm Xd, Xn, 0, 31
    A64InstDesc("sxtw", (_r(64), _r(32)), template=0x93407C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # sdiv Wd, Wn, Wm / Xd, Xn, Xm    -- truncating toward zero
    A64InstDesc("sdiv", (_r(32), _r(32), _r(32)), template=0x1AC00C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("sdiv", (_r(64), _r(64), _r(64)), template=0x9AC00C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("udiv", (_r(32), _r(32), _r(32)), template=0x1AC00800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("udiv", (_r(64), _r(64), _r(64)), template=0x9AC00800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # msub Wd, Wn, Wm, Wa   is  Wa - Wn*Wm, which is how a remainder is had
    # from a quotient here: there is no instruction that gives one directly.
    A64InstDesc("msub", (_r(32), _r(32), _r(32), _r(32)), template=0x1B008000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM),
                        _reg_field(3, 10)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("msub", (_r(64), _r(64), _r(64), _r(64)), template=0x9B008000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN), _reg_field(2, _RM),
                        _reg_field(3, 10)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("lslv", (_r(32), _r(32), _r(32)), template=0x1AC02000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("lslv", (_r(64), _r(64), _r(64)), template=0x9AC02000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("lsrv", (_r(32), _r(32), _r(32)), template=0x1AC02400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("lsrv", (_r(64), _r(64), _r(64)), template=0x9AC02400,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("asrv", (_r(32), _r(32), _r(32)), template=0x1AC02800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("asrv", (_r(64), _r(64), _r(64)), template=0x9AC02800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("rorv", (_r(32), _r(32), _r(32)), template=0x1AC02C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("rorv", (_r(64), _r(64), _r(64)), template=0x9AC02C00,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # The scalar floating-point instructions.  Floating point is in the base of
    # the AArch64 ABI, so a binary that uses them requires nothing a binary that
    # does not would not already have.
    A64InstDesc("fmov", (_v(32), _v(32)), template=0x1E204000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fmov", (_v(64), _v(64)), template=0x1E604000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fabs", (_v(32), _v(32)), template=0x1E20C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fabs", (_v(64), _v(64)), template=0x1E60C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    # fcvt d, s: a single-precision value in the double-precision format,
    # which holds every one of them exactly.
    A64InstDesc("fcvt", (_v(64), _v(32)), template=0x1E22C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fadd", (_v(32), _v(32), _v(32)), template=0x1E202800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fadd", (_v(64), _v(64), _v(64)), template=0x1E602800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # The larger and the smaller of two, which these machines have as
    # instructions of their own.  What they answer where one of the two is not
    # a number differs between architectures; nothing here can be, an operation
    # whose answer is not a number having stopped the program where it arose.
    A64InstDesc("fmax", (_v(32), _v(32), _v(32)), template=0x1E204800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fmax", (_v(64), _v(64), _v(64)), template=0x1E604800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fmin", (_v(32), _v(32), _v(32)), template=0x1E205800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fmin", (_v(64), _v(64), _v(64)), template=0x1E605800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    # The whole number a floating-point number rounds to.  Four of the seven
    # this architecture has: the three that name a direction and the one that
    # asks the processor's own rounding mode.
    A64InstDesc("frintm", (_v(32), _v(32)), template=0x1E254000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("frintm", (_v(64), _v(64)), template=0x1E654000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("frintp", (_v(32), _v(32)), template=0x1E24C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("frintp", (_v(64), _v(64)), template=0x1E64C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("frintn", (_v(32), _v(32)), template=0x1E244000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("frintn", (_v(64), _v(64)), template=0x1E644000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("frinti", (_v(32), _v(32)), template=0x1E27C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("frinti", (_v(64), _v(64)), template=0x1E67C000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fsub", (_v(32), _v(32), _v(32)), template=0x1E203800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fsub", (_v(64), _v(64), _v(64)), template=0x1E603800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fmul", (_v(32), _v(32), _v(32)), template=0x1E200800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fmul", (_v(64), _v(64), _v(64)), template=0x1E600800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fdiv", (_v(32), _v(32), _v(32)), template=0x1E201800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fdiv", (_v(64), _v(64), _v(64)), template=0x1E601800,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _reg_field(2, _RM)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("fcmp", (_v(32), _v(32)), template=0x1E202000,
                fields=(_reg_field(0, _RN), _reg_field(1, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    A64InstDesc("fcmp", (_v(64), _v(64)), template=0x1E602000,
                fields=(_reg_field(0, _RN), _reg_field(1, _RM)),
                implicit_defs=(NZCV,), est_size=INSTRUCTION_SIZE,
                roles=_READS_BOTH),
    A64InstDesc("ldr", (_v(32), _r(64), _off(2)), template=0xBD400000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _offset_field(2, 2)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("ldr", (_v(64), _r(64), _off(3)), template=0xFD400000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _offset_field(2, 3)),
                est_size=INSTRUCTION_SIZE),
    A64InstDesc("str", (_v(32), _r(64), _off(2)), template=0xBD000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _offset_field(2, 2)),
                est_size=INSTRUCTION_SIZE,
                flags=InstFlags.MAY_STORE, roles=_READS_ALL_THREE),
    A64InstDesc("str", (_v(64), _r(64), _off(3)), template=0xFD000000,
                fields=(_reg_field(0, _RD), _reg_field(1, _RN),
                        _offset_field(2, 3)),
                est_size=INSTRUCTION_SIZE,
                flags=InstFlags.MAY_STORE, roles=_READS_ALL_THREE),
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
    # What a call destroys is the *callee's* to say: two functions of one
    # compilation may follow different conventions, and a function that destroys
    # little is one a caller has to save little around -- neither of which the
    # table can know.  So the call carries it, per call, and what is named here
    # is only what the instruction itself writes.
    A64InstDesc("bl", (_sym(),), template=0x94000000,
                fields=(Field(FieldKind.RELOCATION, 0, 0, 26, shift=2, signed=True,
                              reloc=BRANCH26),),
                implicit_defs=(X30,), flags=InstFlags.CALL,
                est_size=INSTRUCTION_SIZE),
    # blr Xn
    # A call through a register, which is what a function held in a value is
    # called by.  It writes the link register as `bl` does; what it destroys
    # beyond that is the callee's to say and travels on the instruction.
    A64InstDesc("blr", (_r(64),), template=0xD63F0000,
                fields=(_reg_field(0, _RN),),
                implicit_defs=(X30,), flags=InstFlags.CALL,
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
