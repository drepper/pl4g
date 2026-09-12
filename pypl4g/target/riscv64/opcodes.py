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

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandSpec
from .desc import INSTRUCTION_SIZE, Field, FieldKind, RVInstDesc
from .fixups import JAL, PCREL_HI20, PCREL_LO12_I, PCREL_PAIR_DISTANCE
from .regs import GPR, RA

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


def _imm12() -> OperandSpec:
    """The signed twelve-bit immediate of the I-type instructions."""
    return OperandSpec(OperandKind.IMM, imm_min=IMM12_MIN, imm_max=IMM12_MAX)


def _imm20() -> OperandSpec:
    """The twenty-bit immediate of the U-type instructions."""
    return OperandSpec(OperandKind.IMM, imm_min=0, imm_max=0xFFFFF)


def _sym() -> OperandSpec:
    """A branch target, given as a symbol reference."""
    return OperandSpec(OperandKind.REL | OperandKind.SYM)


def _reg(operand: int, lsb: int) -> Field:
    """A five-bit register number."""
    return Field(FieldKind.REGISTER, operand, lsb)


def _imm(operand: int, lsb: int, width: int) -> Field:
    """A signed immediate held in one run of bits."""
    return Field(FieldKind.IMMEDIATE, operand, lsb, width, signed=True)


RISCV_INSTRS: Final[tuple[RVInstDesc, ...]] = (
    # li rd, imm12       is  addi rd, zero, imm12
    RVInstDesc("li", (_r(), _imm12()), template=0x00000013,
               fields=(_reg(0, _RD), _imm(1, _IMM12, 12)), est_size=INSTRUCTION_SIZE),
    # mv rd, rs          is  addi rd, rs, 0
    RVInstDesc("mv", (_r(), _r()), template=0x00000013,
               fields=(_reg(0, _RD), _reg(1, _RS1)), est_size=INSTRUCTION_SIZE),
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
    # jal ra, label      (the return address register is part of the template)
    RVInstDesc("jal", (_sym(),), template=0x000000EF,
               fields=(Field(FieldKind.RELOCATION, 0, 12, 20, reloc=JAL),),
               implicit_defs=(RA,), flags=InstFlags.CALL, est_size=INSTRUCTION_SIZE),
    # ret                is  jalr zero, ra, 0
    RVInstDesc("ret", (), template=0x00008067, implicit_uses=(RA,),
               flags=InstFlags.TERMINATOR, est_size=INSTRUCTION_SIZE),
    # ecall
    RVInstDesc("ecall", (), template=0x00000073, est_size=INSTRUCTION_SIZE),
    # ebreak
    RVInstDesc("ebreak", (), template=0x00100073, est_size=INSTRUCTION_SIZE),
    # unimp              a word the architecture guarantees is never valid
    RVInstDesc("unimp", (), template=0xC0001073,
               flags=InstFlags.TERMINATOR | InstFlags.BARRIER,
               est_size=INSTRUCTION_SIZE),
    # nop                is  addi zero, zero, 0
    RVInstDesc("nop", (), template=0x00000013, est_size=INSTRUCTION_SIZE),
)
