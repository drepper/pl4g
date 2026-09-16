"""The RISC-V 64-bit encoding table.

Each row is a 32-bit template plus the bits its operands occupy.  Several rows
are what the architecture calls a pseudo-instruction: ``li`` is an add of an
immediate to the register that reads as zero, ``mv`` is an add of zero, and
``ret`` is a jump through the return address register.  They are written as rows
of their own because that is how code generation wants to ask for them, and the
template carries the fixed operand.

The templates were checked against the GNU assembler, and a test keeps checking
them.
"""

from __future__ import annotations

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandRole, OperandSpec
from .desc import INSTRUCTION_SIZE, Field, FieldKind, RVInstDesc
from .fixups import BRANCH, JAL, PCREL_HI20, PCREL_LO12_I, PCREL_PAIR_DISTANCE
from .regs import CALLER_SAVED, FPR, GPR, RA

#: The word the architecture leaves undefined, which is what padding is filled
#: with so that falling into it traps.
UNDEFINED_WORD: Final[int] = 0x00000000
PAD_BYTE: Final[int] = 0x00

#: Where each operand of a standard format sits.
_RD = 7
_RS1 = 15
_RS2 = 20
_IMM12 = 20
_IMM20 = 12

#: The range of the twelve-bit signed immediate most instructions carry.
IMM12_MIN: Final[int] = -2048
IMM12_MAX: Final[int] = 2047


def _r() -> OperandSpec:
    """An integer register.  There is only one width."""
    return OperandSpec(OperandKind.REG, rclass=GPR, bits=64)


def _f() -> OperandSpec:
    """A floating-point register.  There is one width of them, the wider of the
    two formats; a single-precision value sits in the low half."""
    return OperandSpec(OperandKind.REG, rclass=FPR, bits=64)


def _imm12() -> OperandSpec:
    """The signed twelve-bit immediate of the I-type instructions."""
    return OperandSpec(OperandKind.IMM, imm_min=IMM12_MIN, imm_max=IMM12_MAX)


def _imm20() -> OperandSpec:
    """The twenty-bit immediate of the U-type instructions."""
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=0xFFFFF)


def _shamt() -> OperandSpec:
    """How far a shift shifts.  Six bits here, this being the sixty-four bit
    width; a shift of the whole register is the largest that means anything."""
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=63)


def _rm() -> OperandSpec:
    """Which way a conversion rounds, as the three-bit field the architecture
    puts it in."""
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=7)


def _sym() -> OperandSpec:
    """A branch target, given as a symbol reference."""
    return OperandSpec(OperandKind.REL | OperandKind.SYM)


def _fence_bits() -> OperandSpec:
    """Which accesses a fence orders, as the twelve bits holding all of it.

    One operand and not two, because the architecture puts the mode, what comes
    before and what comes after in one run of bits: a fence the compiler ever
    wants is then a number, and one row covers all of them.
    """
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=0xFFF)


def _reg(operand: int, lsb: int) -> Field:
    """A five-bit register number."""
    return Field(FieldKind.REGISTER, operand, lsb)


def _imm(operand: int, lsb: int, width: int) -> Field:
    """A signed immediate held in one run of bits."""
    return Field(FieldKind.IMMEDIATE, operand, lsb, width, signed=True)


def _mode(operand: int) -> Field:
    """The three-bit rounding-mode field, which is not a number to be signed."""
    return Field(FieldKind.IMMEDIATE, operand, 12, 3, signed=False)


def _store_fields() -> tuple[Field, ...]:
    """Where a store puts its value, its base and the two halves of its offset.

    The operands are the value, the base register and the offset, in that order,
    which is the order the assembly writes them: ``sb a0, 8(a1)``.
    """
    return (
        Field(FieldKind.REGISTER, 0, _RS2),
        Field(FieldKind.REGISTER, 1, _RS1),
        # One value, two fields.  Each states the width of the whole value, so
        # that the range is checked once and against the right thing.
        Field(FieldKind.IMMEDIATE, 2, 7, 5, signed=True, value_bits=12),
        Field(FieldKind.IMMEDIATE, 2, 25, 7, shift=5, signed=True, value_bits=12),
    )


#: A store names the value, the base register and the offset, and reads all of
#: them: nothing it names is written, only the place they point at.
_READS_ALL_THREE: Final[tuple[OperandRole, ...]] = (
    OperandRole.USE, OperandRole.USE, OperandRole.USE)

#: A branch compares two registers and names a place to go; it writes none
#: of them, having no condition codes to write.
_READS_BOTH_AND_TARGET: Final[tuple[OperandRole, ...]] = (
    OperandRole.USE, OperandRole.USE, OperandRole.USE)


RISCV_INSTRS: Final[tuple[RVInstDesc, ...]] = (
    # li rd, imm12       is  addi rd, zero, imm12
    RVInstDesc("li", (_r(), _imm12()), template=0x00000013,
               fields=(_reg(0, _RD), _imm(1, _IMM12, 12)), est_size=INSTRUCTION_SIZE),
    # mv rd, rs          is  addi rd, rs, 0
    RVInstDesc("mv", (_r(), _r()), template=0x00000013,
               fields=(_reg(0, _RD), _reg(1, _RS1)), est_size=INSTRUCTION_SIZE, flags=InstFlags.MOVE),
    # addi rd, rs1, imm12
    RVInstDesc("addi", (_r(), _r(), _imm12()), template=0x00000013,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               est_size=INSTRUCTION_SIZE),
    # addiw rd, rs1, imm12   (the result is the low half, sign extended)
    RVInstDesc("addiw", (_r(), _r(), _imm12()), template=0x0000001B,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               est_size=INSTRUCTION_SIZE),
    # add rd, rs1, rs2
    RVInstDesc("add", (_r(), _r(), _r()), template=0x00000033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # addw rd, rs1, rs2
    RVInstDesc("addw", (_r(), _r(), _r()), template=0x0000003B,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # sub rd, rs1, rs2
    RVInstDesc("sub", (_r(), _r(), _r()), template=0x40000033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # subw rd, rs1, rs2
    RVInstDesc("subw", (_r(), _r(), _r()), template=0x4000003B,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # xor rd, rs1, rs2
    RVInstDesc("xor", (_r(), _r(), _r()), template=0x00004033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # and rd, rs1, rs2
    RVInstDesc("and", (_r(), _r(), _r()), template=0x00007033,
               fields=(Field(FieldKind.REGISTER, 0, 7), Field(FieldKind.REGISTER, 1, 15),
                       Field(FieldKind.REGISTER, 2, 20)),
               est_size=INSTRUCTION_SIZE),
    # or rd, rs1, rs2
    RVInstDesc("or", (_r(), _r(), _r()), template=0x00006033,
               fields=(Field(FieldKind.REGISTER, 0, 7), Field(FieldKind.REGISTER, 1, 15),
                       Field(FieldKind.REGISTER, 2, 20)),
               est_size=INSTRUCTION_SIZE),
    # not rd, rs   is  xori rd, rs, -1
    RVInstDesc("not", (_r(), _r()), template=0xFFF04013,
               fields=(Field(FieldKind.REGISTER, 0, 7), Field(FieldKind.REGISTER, 1, 15)),
               est_size=INSTRUCTION_SIZE),
    # The only comparison the architecture has is "less than", which writes one
    # or zero into a register.  Every other ordering is this instruction with
    # the operands exchanged, its answer inverted, or both.
    # slt rd, rs1, rs2
    RVInstDesc("slt", (_r(), _r(), _r()), template=0x00002033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # sltu rd, rs1, rs2
    RVInstDesc("sltu", (_r(), _r(), _r()), template=0x00003033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # sltiu rd, rs1, imm12
    RVInstDesc("sltiu", (_r(), _r(), _imm12()), template=0x00003013,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               est_size=INSTRUCTION_SIZE),
    # xori rd, rs1, imm12
    RVInstDesc("xori", (_r(), _r(), _imm12()), template=0x00004013,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               est_size=INSTRUCTION_SIZE),
    # srai rd, rs1, shamt    an arithmetic shift, bringing in copies of the sign
    RVInstDesc("srai", (_r(), _r(), _shamt()), template=0x40005013,
               fields=(_reg(0, _RD), _reg(1, _RS1),
                       Field(FieldKind.IMMEDIATE, 2, 20, 6)),
               est_size=INSTRUCTION_SIZE),
    # slli rd, rs1, shamt        the shift is six bits wide on this width
    RVInstDesc("slli", (_r(), _r(), _shamt()), template=0x00001013,
               fields=(_reg(0, _RD), _reg(1, _RS1),
                       Field(FieldKind.IMMEDIATE, 2, 20, 6)),
               est_size=INSTRUCTION_SIZE),
    # The multiply instructions of the M extension.  `mul` is the low half of
    # the product and `mulh`/`mulhu` the high half, which is the only way to
    # see that a sixty-four bit product overflowed.
    # mul rd, rs1, rs2
    RVInstDesc("mul", (_r(), _r(), _r()), template=0x02000033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # mulh rd, rs1, rs2
    RVInstDesc("mulh", (_r(), _r(), _r()), template=0x02001033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # mulhu rd, rs1, rs2
    RVInstDesc("mulhu", (_r(), _r(), _r()), template=0x02003033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # div rd, rs1, rs2 and its relatives, all truncating toward zero
    RVInstDesc("div", (_r(), _r(), _r()), template=0x02004033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("divu", (_r(), _r(), _r()), template=0x02005033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("rem", (_r(), _r(), _r()), template=0x02006033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("remu", (_r(), _r(), _r()), template=0x02007033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("sll", (_r(), _r(), _r()), template=0x00001033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("srl", (_r(), _r(), _r()), template=0x00005033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("sra", (_r(), _r(), _r()), template=0x40005033,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # The floating-point instructions of the F and D extensions.  The rounding
    # field says "the one the rounding mode register names", which is what an
    # assembler writes when none is spelled out.
    RVInstDesc("fadd.s", (_f(), _f(), _f()), template=0x00007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fadd.d", (_f(), _f(), _f()), template=0x02007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # The larger and the smaller of two, which these machines have as
    # instructions of their own.  The rounding-mode field names which of the
    # two the instruction is rather than a rounding, neither of them rounding
    # anything.  What they answer where one of the two is not a number differs
    # between architectures; nothing here can be, an operation whose answer is
    # not a number having stopped the program where it arose.
    RVInstDesc("fmin.s", (_f(), _f(), _f()), template=0x28000053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fmax.s", (_f(), _f(), _f()), template=0x28001053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fmin.d", (_f(), _f(), _f()), template=0x2A000053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fmax.d", (_f(), _f(), _f()), template=0x2A001053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fsub.s", (_f(), _f(), _f()), template=0x08007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fsub.d", (_f(), _f(), _f()), template=0x0A007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fmul.s", (_f(), _f(), _f()), template=0x10007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fmul.d", (_f(), _f(), _f()), template=0x12007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fdiv.s", (_f(), _f(), _f()), template=0x18007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fdiv.d", (_f(), _f(), _f()), template=0x1A007053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    # fcvt.d.s: a single-precision value in the double-precision format, which
    # holds every one of them exactly, so the rounding mode says nothing.
    RVInstDesc("fcvt.d.s", (_f(), _f()), template=0x42000053,
               fields=(_reg(0, _RD), _reg(1, _RS1)),
               est_size=INSTRUCTION_SIZE),
    # Converting between a floating-point number and an integer.  The rounding
    # is an operand here rather than baked into the mnemonic, because the whole
    # reason these are wanted is the four different roundings: the field is the
    # architecture's own, and `rne`, `rdn`, `rup` and `dyn` are what the
    # assembler writes for the four this compiler asks for.
    RVInstDesc("fcvt.l.d", (_r(), _f(), _rm()), template=0xC2200053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _mode(2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fcvt.w.s", (_r(), _f(), _rm()), template=0xC0000053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _mode(2)),
               est_size=INSTRUCTION_SIZE),
    # Rounding a floating-point number where it stands, which is the Zfa
    # extension and not the base -- the application profiles have had it since
    # 2023, and what a program built for a base without it pays instead is the
    # round trip through an integer above.
    RVInstDesc("fround.s", (_f(), _f(), _rm()), template=0x40400053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _mode(2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fround.d", (_f(), _f(), _rm()), template=0x42400053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _mode(2)),
               est_size=INSTRUCTION_SIZE),
    # Back again, where no rounding can happen: every integer this compiler
    # converts back came out of a value smaller than the format's own limit for
    # whole numbers, so it is representable exactly.
    RVInstDesc("fcvt.d.l", (_f(), _r()), template=0xD2207053,
               fields=(_reg(0, _RD), _reg(1, _RS1)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fcvt.s.w", (_f(), _r()), template=0xD0007053,
               fields=(_reg(0, _RD), _reg(1, _RS1)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fsgnj.s", (_f(), _f(), _f()), template=0x20000053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fsgnj.d", (_f(), _f(), _f()), template=0x22000053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fsgnjx.s", (_f(), _f(), _f()), template=0x20002053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fsgnjx.d", (_f(), _f(), _f()), template=0x22002053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("feq.s", (_r(), _f(), _f()), template=0xA0002053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("feq.d", (_r(), _f(), _f()), template=0xA2002053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("flt.s", (_r(), _f(), _f()), template=0xA0001053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("flt.d", (_r(), _f(), _f()), template=0xA2001053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fle.s", (_r(), _f(), _f()), template=0xA0000053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fle.d", (_r(), _f(), _f()), template=0xA2000053,
               fields=(_reg(0, _RD), _reg(1, _RS1), _reg(2, _RS2)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("flw", (_f(), _r(), _imm12()), template=0x00002007,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fld", (_f(), _r(), _imm12()), template=0x00003007,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               est_size=INSTRUCTION_SIZE),
    RVInstDesc("fsw", (_f(), _r(), _imm12()), template=0x00002027,
               fields=_store_fields(), flags=InstFlags.MAY_STORE,
               est_size=INSTRUCTION_SIZE, roles=_READS_ALL_THREE),
    RVInstDesc("fsd", (_f(), _r(), _imm12()), template=0x00003027,
               fields=_store_fields(), flags=InstFlags.MAY_STORE,
               est_size=INSTRUCTION_SIZE, roles=_READS_ALL_THREE),
    # lui rd, imm20
    RVInstDesc("lui", (_r(), _imm20()), template=0x00000037,
               fields=(_reg(0, _RD),
                       Field(FieldKind.IMMEDIATE, 1, _IMM20, 20)),
               est_size=INSTRUCTION_SIZE),
    # auipc rd, imm20
    RVInstDesc("auipc", (_r(), _imm20()), template=0x00000017,
               fields=(_reg(0, _RD),
                       Field(FieldKind.IMMEDIATE, 1, _IMM20, 20)),
               est_size=INSTRUCTION_SIZE),
    # auipc rd, %pcrel_hi(symbol)
    RVInstDesc("auipc.hi20", (_r(), _sym()), template=0x00000017,
               fields=(_reg(0, _RD),
                       Field(FieldKind.RELOCATION, 1, _IMM20, 20, reloc=PCREL_HI20)),
               est_size=INSTRUCTION_SIZE),
    # addi rd, rs1, %pcrel_lo(the auipc above)
    RVInstDesc("addi.lo12", (_r(), _r(), _sym()), template=0x00000013,
               fields=(_reg(0, _RD), _reg(1, _RS1),
                       Field(FieldKind.RELOCATION, 2, _IMM12, 12,
                             reloc=PCREL_LO12_I,
                             base_adjust=-PCREL_PAIR_DISTANCE)),
               est_size=INSTRUCTION_SIZE),
    # The loads.  The offset is a signed twelve-bit immediate, unscaled, and the
    # instruction says whether a narrow value arrives widened by its sign or by
    # zeroes -- there being no narrower register to leave it in.
    RVInstDesc("lbu", (_r(), _r(), _imm12()), template=0x00004003,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    RVInstDesc("lb", (_r(), _r(), _imm12()), template=0x00000003,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    RVInstDesc("lhu", (_r(), _r(), _imm12()), template=0x00005003,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    RVInstDesc("lh", (_r(), _r(), _imm12()), template=0x00001003,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    RVInstDesc("lwu", (_r(), _r(), _imm12()), template=0x00006003,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    RVInstDesc("lw", (_r(), _r(), _imm12()), template=0x00002003,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    RVInstDesc("ld", (_r(), _r(), _imm12()), template=0x00003003,
               fields=(_reg(0, _RD), _reg(1, _RS1), _imm(2, _IMM12, 12)),
               flags=InstFlags.MAY_LOAD, est_size=INSTRUCTION_SIZE),
    # The stores.  Their offset is not one run of bits: the architecture puts its
    # low five bits where a destination register would sit and the rest at the
    # top, so each row names the two halves of one value.
    RVInstDesc("sb", (_r(), _r(), _imm12()), template=0x00000023,
               fields=_store_fields(), flags=InstFlags.MAY_STORE,
               est_size=INSTRUCTION_SIZE, roles=_READS_ALL_THREE),
    RVInstDesc("sh", (_r(), _r(), _imm12()), template=0x00001023,
               fields=_store_fields(), flags=InstFlags.MAY_STORE,
               est_size=INSTRUCTION_SIZE, roles=_READS_ALL_THREE),
    RVInstDesc("sw", (_r(), _r(), _imm12()), template=0x00002023,
               fields=_store_fields(), flags=InstFlags.MAY_STORE,
               est_size=INSTRUCTION_SIZE, roles=_READS_ALL_THREE),
    RVInstDesc("sd", (_r(), _r(), _imm12()), template=0x00003023,
               fields=_store_fields(), flags=InstFlags.MAY_STORE,
               est_size=INSTRUCTION_SIZE, roles=_READS_ALL_THREE),
    # j label            is  jal zero, label
    RVInstDesc("j", (_sym(),), template=0x0000006F,
               fields=(Field(FieldKind.RELOCATION, 0, 12, 20, reloc=JAL),),
               flags=InstFlags.TERMINATOR | InstFlags.BARRIER,
               est_size=INSTRUCTION_SIZE),
    # beq rs1, rs2, label
    RVInstDesc("beq", (_r(), _r(), _sym()), template=0x00000063,
               fields=(Field(FieldKind.REGISTER, 0, 15), Field(FieldKind.REGISTER, 1, 20),
                       Field(FieldKind.RELOCATION, 2, 0, 32, reloc=BRANCH)),
               flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
               roles=_READS_BOTH_AND_TARGET),
    # bne rs1, rs2, label
    RVInstDesc("bne", (_r(), _r(), _sym()), template=0x00001063,
               fields=(Field(FieldKind.REGISTER, 0, 15), Field(FieldKind.REGISTER, 1, 20),
                       Field(FieldKind.RELOCATION, 2, 0, 32, reloc=BRANCH)),
               flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
               roles=_READS_BOTH_AND_TARGET),
    # blt rs1, rs2, label
    RVInstDesc("blt", (_r(), _r(), _sym()), template=0x00004063,
               fields=(Field(FieldKind.REGISTER, 0, 15), Field(FieldKind.REGISTER, 1, 20),
                       Field(FieldKind.RELOCATION, 2, 0, 32, reloc=BRANCH)),
               flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
               roles=_READS_BOTH_AND_TARGET),
    # bge rs1, rs2, label
    RVInstDesc("bge", (_r(), _r(), _sym()), template=0x00005063,
               fields=(Field(FieldKind.REGISTER, 0, 15), Field(FieldKind.REGISTER, 1, 20),
                       Field(FieldKind.RELOCATION, 2, 0, 32, reloc=BRANCH)),
               flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
               roles=_READS_BOTH_AND_TARGET),
    # bltu rs1, rs2, label
    RVInstDesc("bltu", (_r(), _r(), _sym()), template=0x00006063,
               fields=(Field(FieldKind.REGISTER, 0, 15), Field(FieldKind.REGISTER, 1, 20),
                       Field(FieldKind.RELOCATION, 2, 0, 32, reloc=BRANCH)),
               flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
               roles=_READS_BOTH_AND_TARGET),
    # bgeu rs1, rs2, label
    RVInstDesc("bgeu", (_r(), _r(), _sym()), template=0x00007063,
               fields=(Field(FieldKind.REGISTER, 0, 15), Field(FieldKind.REGISTER, 1, 20),
                       Field(FieldKind.RELOCATION, 2, 0, 32, reloc=BRANCH)),
               flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE,
               roles=_READS_BOTH_AND_TARGET),
    # jal ra, label      (the return address register is part of the template)
    # A call destroys every register the convention calls caller-saved, so the
    # allocator has to be told -- otherwise a value held across one is silently
    # lost.  Naming them here puts a convention's business in the instruction
    # table, which is not where it belongs; it costs nothing today, every
    # convention this target has calling the same registers caller-saved, and
    # the entry in the to-do list says what to do when one does not.
    RVInstDesc("jal", (_sym(),), template=0x000000EF,
               fields=(Field(FieldKind.RELOCATION, 0, 12, 20, reloc=JAL),),
    # What a call destroys is the *callee's* to say and is carried on the
    # instruction rather than stated here; the link register, which the
    # instruction itself writes, is not.
               implicit_defs=(RA,), flags=InstFlags.CALL,
               est_size=INSTRUCTION_SIZE),
    # jalr ra, rs, 0
    # A call through a register, which is what a function held in a value is
    # called by.  The link register is written, as `jal` writes it; what it
    # destroys beyond that is the callee's to say.
    RVInstDesc("jalr", (_r(),), template=0x000000E7,
               fields=(_reg(0, _RS1),),
               implicit_defs=(RA,), flags=InstFlags.CALL,
               est_size=INSTRUCTION_SIZE),
    # ret                is  jalr zero, ra, 0
    RVInstDesc("ret", (), template=0x00008067, implicit_uses=(RA,),
               flags=InstFlags.TERMINATOR | InstFlags.RETURN, est_size=INSTRUCTION_SIZE),
    # ecall
    RVInstDesc("ecall", (), template=0x00000073, est_size=INSTRUCTION_SIZE),
    # fence pred, succ
    RVInstDesc("fence", (_fence_bits(),), template=0x0000000F,
               fields=(Field(FieldKind.IMMEDIATE, 0, _IMM12, 12, signed=False),),
               flags=InstFlags.MAY_LOAD | InstFlags.MAY_STORE,
               est_size=INSTRUCTION_SIZE),
    # ebreak
    RVInstDesc("ebreak", (), template=0x00100073, est_size=INSTRUCTION_SIZE),
    # unimp              a word the architecture guarantees is never valid
    RVInstDesc("unimp", (), template=0xC0001073,
               flags=InstFlags.TERMINATOR | InstFlags.BARRIER,
               est_size=INSTRUCTION_SIZE),
    # nop                is  addi zero, zero, 0
    RVInstDesc("nop", (), template=0x00000013, est_size=INSTRUCTION_SIZE),
)
