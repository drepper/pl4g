"""The x86-64 encoding table.

Each row states how one form of one instruction is encoded.  The generic emitter
in ``encoder`` walks the rows; adding an instruction is a row here, and adding a
whole prefix family is one more emitter in the encoder's prefix phase plus the
fields it needs in ``desc``.
"""

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandSpec
from .desc import ModRMUse, OpMap, OpSize, X86InstDesc
from .regs import EFLAGS, GPR, R11, RCX


def _r(bits: int) -> OperandSpec:
    """A general-purpose register of the given width."""
    return OperandSpec(OperandKind.REG, rclass=GPR, bits=bits)


def _rm(bits: int) -> OperandSpec:
    """A general-purpose register or a memory operand of the given width."""
    return OperandSpec(OperandKind.REG | OperandKind.MEM, rclass=GPR, bits=bits)


def _imm(bits: int) -> OperandSpec:
    """An immediate of at most the given width."""
    return OperandSpec(OperandKind.IMM, bits=bits)


def _rel(bits: int) -> OperandSpec:
    """A branch target, given as a symbol reference."""
    return OperandSpec(OperandKind.REL | OperandKind.SYM, bits=bits)


def _mem() -> OperandSpec:
    """A memory operand of any width."""
    return OperandSpec(OperandKind.MEM)


X86_INSTRS: Final[tuple[X86InstDesc, ...]] = (
    # mov r32, imm32                     B8+rd id
    X86InstDesc("mov", (_r(32), _imm(32)), opcode=0xB8, plus_reg=True, reg_op=0,
                imm_op=1, imm_bits=32, flags=InstFlags.ZEXT32, est_size=5),
    # mov r64, imm64                     REX.W B8+rd io
    X86InstDesc("mov", (_r(64), _imm(64)), opcode=0xB8, plus_reg=True, reg_op=0,
                opsize=OpSize.REXW, imm_op=1, imm_bits=64, est_size=10),
    # mov r/m64, imm32 (sign extended)   REX.W C7 /0 id
    X86InstDesc("mov", (_rm(64), _imm(32)), opcode=0xC7, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0, imm_op=1, imm_bits=32,
                est_size=7),
    # mov r/m8, r8                       88 /r
    X86InstDesc("mov", (_rm(8), _r(8)), opcode=0x88, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=2),
    # mov r8, r/m8                       8A /r
    X86InstDesc("mov", (_r(8), _rm(8)), opcode=0x8A, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=2),
    # mov r/m32, r32                     89 /r
    X86InstDesc("mov", (_rm(32), _r(32)), opcode=0x89, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, flags=InstFlags.ZEXT32, est_size=2),
    # mov r32, r/m32                     8B /r   (the load direction)
    X86InstDesc("mov", (_r(32), _rm(32)), opcode=0x8B, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, flags=InstFlags.ZEXT32, est_size=2),
    # mov r/m64, r64                     REX.W 89 /r
    X86InstDesc("mov", (_rm(64), _r(64)), opcode=0x89, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, est_size=3),
    # mov r64, r/m64                     REX.W 8B /r
    X86InstDesc("mov", (_r(64), _rm(64)), opcode=0x8B, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=3),
    # xor r/m32, r32                     31 /r
    X86InstDesc("xor", (_rm(32), _r(32)), opcode=0x31, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2),
    # xor r/m64, r64                     REX.W 31 /r
    X86InstDesc("xor", (_rm(64), _r(64)), opcode=0x31, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3),
    # add r/m32, r32                     01 /r
    X86InstDesc("add", (_rm(32), _r(32)), opcode=0x01, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2),
    # add r/m64, r64                     REX.W 01 /r
    X86InstDesc("add", (_rm(64), _r(64)), opcode=0x01, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3),
    # sub r/m32, r32                     29 /r
    X86InstDesc("sub", (_rm(32), _r(32)), opcode=0x29, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2),
    # sub r/m64, r64                     REX.W 29 /r
    X86InstDesc("sub", (_rm(64), _r(64)), opcode=0x29, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3),
    # movzx r32, r/m8                    0F B6 /r
    X86InstDesc("movzx", (_r(32), _rm(8)), opcode=0xB6, map=OpMap.M0F,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, flags=InstFlags.ZEXT32,
                est_size=3),
    # movzx r32, r/m16                   0F B7 /r
    X86InstDesc("movzx", (_r(32), _rm(16)), opcode=0xB7, map=OpMap.M0F,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, flags=InstFlags.ZEXT32,
                est_size=3),
    # movsx r32, r/m8                    0F BE /r
    X86InstDesc("movsx", (_r(32), _rm(8)), opcode=0xBE, map=OpMap.M0F,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, flags=InstFlags.ZEXT32,
                est_size=3),
    # movsx r32, r/m16                   0F BF /r
    X86InstDesc("movsx", (_r(32), _rm(16)), opcode=0xBF, map=OpMap.M0F,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, flags=InstFlags.ZEXT32,
                est_size=3),
    # movsxd r64, r/m32                  REX.W 63 /r
    X86InstDesc("movsxd", (_r(64), _rm(32)), opcode=0x63, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=4),
    # lea r64, m                         REX.W 8D /r
    X86InstDesc("lea", (_r(64), _mem()), opcode=0x8D, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=7),
    # call rel32                         E8 cd
    X86InstDesc("call", (_rel(32),), opcode=0xE8, rel_op=0, rel_bits=32,
                flags=InstFlags.CALL, est_size=5),
    # ret                                C3
    X86InstDesc("ret", (), opcode=0xC3, flags=InstFlags.TERMINATOR, est_size=1),
    # syscall                            0F 05
    X86InstDesc("syscall", (), opcode=0x05, map=OpMap.M0F,
                implicit_defs=(RCX, R11, EFLAGS), est_size=2),
    # ud2                                0F 0B
    X86InstDesc("ud2", (), opcode=0x0B, map=OpMap.M0F,
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER, est_size=2),
)
