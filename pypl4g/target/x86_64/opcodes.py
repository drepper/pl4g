"""The x86-64 encoding table.

Each row states how one form of one instruction is encoded.  The generic emitter
in ``encoder`` walks the rows; adding an instruction is a row here, and adding a
whole prefix family is one more emitter in the encoder's prefix phase plus the
fields it needs in ``desc``.
"""

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandRole, OperandSpec
from .desc import ModRMUse, OpMap, OpSize, X86InstDesc
from .regs import CALLER_SAVED, EFLAGS, GPR, R11, RAX, RCX, RDX, VEC


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


def _cl() -> OperandSpec:
    """The one register a variable shift takes its count in."""
    return OperandSpec(OperandKind.REG, rclass=GPR, bits=8)


def _x() -> OperandSpec:
    """One of the vector registers, named at the width the whole of it has."""
    return OperandSpec(OperandKind.REG, rclass=VEC, bits=128)


def _xm(bits: int = 64) -> OperandSpec:
    """A vector register, or a place in memory holding *bits* of it.

    The register is named as the whole of one whichever format it holds, since
    that is the value's own register; the place holds exactly the format the
    mnemonic names, and nothing beside it is part of the value.
    """
    return OperandSpec(OperandKind.REG | OperandKind.MEM, rclass=VEC, bits=128,
                       mem_bits=bits)


def _mem(bits: int | None = None) -> OperandSpec:
    """A memory operand, of any width or of the one named."""
    return OperandSpec(OperandKind.MEM, bits=bits)


#: This architecture's arithmetic takes two operands, so the destination is also
#: the first source: the value it held is still wanted when the instruction runs.
_ACCUMULATE: Final[tuple[OperandRole, ...]] = (OperandRole.DEF_USE, OperandRole.USE)

#: A comparison writes only the flags, which it declares separately.
_READS_BOTH: Final[tuple[OperandRole, ...]] = (OperandRole.USE, OperandRole.USE)


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
    # mov r/m8, imm8                     C6 /0 ib
    X86InstDesc("mov", (_rm(8), _imm(8)), opcode=0xC6, modrm=ModRMUse.EXT_RM, ext=0,
                rm_op=0, imm_op=1, imm_bits=8, est_size=3),
    # mov r/m16, imm16                   66 C7 /0 iw
    X86InstDesc("mov", (_rm(16), _imm(16)), opcode=0xC7, opsize=OpSize.P66,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0, imm_op=1, imm_bits=16,
                est_size=5),
    # mov r/m32, imm32                   C7 /0 id
    X86InstDesc("mov", (_rm(32), _imm(32)), opcode=0xC7, modrm=ModRMUse.EXT_RM, ext=0,
                rm_op=0, imm_op=1, imm_bits=32, est_size=6),
    # mov r/m16, r16                     66 89 /r
    X86InstDesc("mov", (_rm(16), _r(16)), opcode=0x89, opsize=OpSize.P66,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, est_size=3, flags=InstFlags.MOVE),
    # mov r/m8, r8                       88 /r
    X86InstDesc("mov", (_rm(8), _r(8)), opcode=0x88, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=2, flags=InstFlags.MOVE),
    # mov r8, r/m8                       8A /r
    X86InstDesc("mov", (_r(8), _rm(8)), opcode=0x8A, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=2, flags=InstFlags.MOVE),
    # mov r/m32, r32                     89 /r
    X86InstDesc("mov", (_rm(32), _r(32)), opcode=0x89, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, flags=InstFlags.MOVE | InstFlags.ZEXT32, est_size=2),
    # mov r32, r/m32                     8B /r   (the load direction)
    X86InstDesc("mov", (_r(32), _rm(32)), opcode=0x8B, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, flags=InstFlags.MOVE | InstFlags.ZEXT32, est_size=2),
    # mov r/m64, r64                     REX.W 89 /r
    X86InstDesc("mov", (_rm(64), _r(64)), opcode=0x89, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, est_size=3, flags=InstFlags.MOVE),
    # mov r64, r/m64                     REX.W 8B /r
    X86InstDesc("mov", (_r(64), _rm(64)), opcode=0x8B, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=3, flags=InstFlags.MOVE),
    # xor r/m32, r32                     31 /r
    X86InstDesc("xor", (_rm(32), _r(32)), opcode=0x31, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2, roles=_ACCUMULATE),
    # xor r/m64, r64                     REX.W 31 /r
    X86InstDesc("xor", (_rm(64), _r(64)), opcode=0x31, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    # add r/m32, r32                     01 /r
    X86InstDesc("add", (_rm(32), _r(32)), opcode=0x01, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2, roles=_ACCUMULATE),
    # add r/m64, r64                     REX.W 01 /r
    X86InstDesc("add", (_rm(64), _r(64)), opcode=0x01, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    # sub r/m32, r32                     29 /r
    X86InstDesc("sub", (_rm(32), _r(32)), opcode=0x29, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2, roles=_ACCUMULATE),
    # sub r/m64, r64                     REX.W 29 /r
    X86InstDesc("sub", (_rm(64), _r(64)), opcode=0x29, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
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
    # movsx r64, r/m8                    REX.W 0F BE /r
    X86InstDesc("movsx", (_r(64), _rm(8)), opcode=0xBE, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                est_size=4),
    # movsx r64, r/m16                   REX.W 0F BF /r
    X86InstDesc("movsx", (_r(64), _rm(16)), opcode=0xBF, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                est_size=4),
    # movsxd r64, r/m32                  REX.W 63 /r
    X86InstDesc("movsxd", (_r(64), _rm(32)), opcode=0x63, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=4),
    # lea r64, m                         REX.W 8D /r
    X86InstDesc("lea", (_r(64), _mem()), opcode=0x8D, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=7),
    # and r/m32, r32                     21 /r
    X86InstDesc("and", (_rm(32), _r(32)), opcode=0x21, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2, roles=_ACCUMULATE),
    # and r/m64, r64                     REX.W 21 /r
    X86InstDesc("and", (_rm(64), _r(64)), opcode=0x21, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    # or r/m32, r32                      09 /r
    X86InstDesc("or", (_rm(32), _r(32)), opcode=0x09, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=2, roles=_ACCUMULATE),
    # or r/m64, r64                      REX.W 09 /r
    X86InstDesc("or", (_rm(64), _r(64)), opcode=0x09, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    # not r/m32                          F7 /2
    X86InstDesc("not", (_rm(32),), opcode=0xF7, modrm=ModRMUse.EXT_RM, ext=2,
                rm_op=0, flags=InstFlags.ZEXT32, est_size=2,
                roles=(OperandRole.DEF_USE,)),
    # not r/m64                          REX.W F7 /2
    X86InstDesc("not", (_rm(64),), opcode=0xF7, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=2, rm_op=0, est_size=3,
                roles=(OperandRole.DEF_USE,)),
    # add r/m64, imm8 (sign extended)    REX.W 83 /0 ib
    X86InstDesc("add", (_rm(64), _imm(8)), opcode=0x83, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0, imm_op=1, imm_bits=8,
                implicit_defs=(EFLAGS,), est_size=4, roles=_ACCUMULATE),
    # add r/m64, imm32 (sign extended)   REX.W 81 /0 id
    X86InstDesc("add", (_rm(64), _imm(32)), opcode=0x81, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0, imm_op=1, imm_bits=32,
                implicit_defs=(EFLAGS,), est_size=7, roles=_ACCUMULATE),
    # sub r/m64, imm8 (sign extended)    REX.W 83 /5 ib
    X86InstDesc("sub", (_rm(64), _imm(8)), opcode=0x83, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=5, rm_op=0, imm_op=1, imm_bits=8,
                implicit_defs=(EFLAGS,), est_size=4, roles=_ACCUMULATE),
    # sub r/m64, imm32 (sign extended)   REX.W 81 /5 id
    X86InstDesc("sub", (_rm(64), _imm(32)), opcode=0x81, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=5, rm_op=0, imm_op=1, imm_bits=32,
                implicit_defs=(EFLAGS,), est_size=7, roles=_ACCUMULATE),
    # cmp r/m32, r32                     39 /r
    X86InstDesc("cmp", (_rm(32), _r(32)), opcode=0x39, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), est_size=2,
                roles=_READS_BOTH),
    # cmp r/m64, r64                     REX.W 39 /r
    X86InstDesc("cmp", (_rm(64), _r(64)), opcode=0x39, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_READS_BOTH),
    # cmp r/m8, imm8                     80 /7 ib
    X86InstDesc("cmp", (_rm(8), _imm(8)), opcode=0x80, modrm=ModRMUse.EXT_RM, ext=7,
                rm_op=0, imm_op=1, imm_bits=8, implicit_defs=(EFLAGS,), est_size=3,
                roles=_READS_BOTH),
    # cmp r/m32, imm32                   81 /7 id
    X86InstDesc("cmp", (_rm(32), _imm(32)), opcode=0x81, modrm=ModRMUse.EXT_RM, ext=7,
                rm_op=0, imm_op=1, imm_bits=32, implicit_defs=(EFLAGS,), est_size=6,
                roles=_READS_BOTH),
    # cmp r/m64, imm32 (sign extended)   REX.W 81 /7 id
    X86InstDesc("cmp", (_rm(64), _imm(32)), opcode=0x81, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=7, rm_op=0, imm_op=1, imm_bits=32,
                implicit_defs=(EFLAGS,), est_size=7, roles=_READS_BOTH),
    # test r/m32, r32                    85 /r
    X86InstDesc("test", (_rm(32), _r(32)), opcode=0x85, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,), est_size=2,
                roles=_READS_BOTH),
    # test r/m64, r64                    REX.W 85 /r
    X86InstDesc("test", (_rm(64), _r(64)), opcode=0x85, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_READS_BOTH),
    # The condition is part of the opcode, as it is for a jump, so one row per
    # condition.  What is written is a byte, and only a byte: the rest of the
    # register keeps whatever it held, which is why a truth value is widened
    # into it afterwards rather than being taken from here directly.
    # sete r/m8                        0F 94 /0
    X86InstDesc("sete", (_rm(8),), opcode=0x94, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setne r/m8                       0F 95 /0
    X86InstDesc("setne", (_rm(8),), opcode=0x95, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setl r/m8                        0F 9C /0
    X86InstDesc("setl", (_rm(8),), opcode=0x9C, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setle r/m8                       0F 9E /0
    X86InstDesc("setle", (_rm(8),), opcode=0x9E, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setg r/m8                        0F 9F /0
    X86InstDesc("setg", (_rm(8),), opcode=0x9F, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setge r/m8                       0F 9D /0
    X86InstDesc("setge", (_rm(8),), opcode=0x9D, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setb r/m8                        0F 92 /0
    X86InstDesc("setb", (_rm(8),), opcode=0x92, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setbe r/m8                       0F 96 /0
    X86InstDesc("setbe", (_rm(8),), opcode=0x96, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # seta r/m8                        0F 97 /0
    X86InstDesc("seta", (_rm(8),), opcode=0x97, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setae r/m8                       0F 93 /0
    X86InstDesc("setae", (_rm(8),), opcode=0x93, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # imul r32, r/m32                    0F AF /r
    X86InstDesc("imul", (_r(32), _rm(32)), opcode=0xAF, map=OpMap.M0F,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, implicit_defs=(EFLAGS,),
                flags=InstFlags.ZEXT32, est_size=3, roles=_ACCUMULATE),
    # imul r64, r/m64                    REX.W 0F AF /r
    X86InstDesc("imul", (_r(64), _rm(64)), opcode=0xAF, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_defs=(EFLAGS,), est_size=4, roles=_ACCUMULATE),
    # cmove r64, r/m64                   REX.W 0F 44 /r
    X86InstDesc("cmove", (_r(64), _rm(64)), opcode=0x44, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovne r64, r/m64                  REX.W 0F 45 /r
    X86InstDesc("cmovne", (_r(64), _rm(64)), opcode=0x45, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovl r64, r/m64                   REX.W 0F 4C /r
    X86InstDesc("cmovl", (_r(64), _rm(64)), opcode=0x4C, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovle r64, r/m64                  REX.W 0F 4E /r
    X86InstDesc("cmovle", (_r(64), _rm(64)), opcode=0x4E, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovg r64, r/m64                   REX.W 0F 4F /r
    X86InstDesc("cmovg", (_r(64), _rm(64)), opcode=0x4F, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovge r64, r/m64                  REX.W 0F 4D /r
    X86InstDesc("cmovge", (_r(64), _rm(64)), opcode=0x4D, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovb r64, r/m64                   REX.W 0F 42 /r
    X86InstDesc("cmovb", (_r(64), _rm(64)), opcode=0x42, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovbe r64, r/m64                  REX.W 0F 46 /r
    X86InstDesc("cmovbe", (_r(64), _rm(64)), opcode=0x46, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmova r64, r/m64                   REX.W 0F 47 /r
    X86InstDesc("cmova", (_r(64), _rm(64)), opcode=0x47, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # cmovae r64, r/m64                  REX.W 0F 43 /r
    X86InstDesc("cmovae", (_r(64), _rm(64)), opcode=0x43, map=OpMap.M0F,
                opsize=OpSize.REXW, modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_uses=(EFLAGS,), est_size=4,
                roles=(OperandRole.DEF_USE, OperandRole.USE)),
    # The division instructions take their dividend in a fixed pair of
    # registers and write their quotient and remainder to the same pair.  That
    # is said here as implicit uses and defs, which is all the allocator needs:
    # it already keeps a value out of a register whose life overlaps its own, so
    # the divisor cannot land in one of the two.
    # cdq                                99          (sign-extends eax into edx)
    X86InstDesc("cdq", (), opcode=0x99, implicit_uses=(RAX,), implicit_defs=(RDX,),
                est_size=1),
    # cqo                                REX.W 99
    X86InstDesc("cqo", (), opcode=0x99, opsize=OpSize.REXW, implicit_uses=(RAX,),
                implicit_defs=(RDX,), est_size=2),
    # idiv r/m32                         F7 /7
    X86InstDesc("idiv", (_rm(32),), opcode=0xF7, modrm=ModRMUse.EXT_RM, ext=7,
                rm_op=0, implicit_uses=(RAX, RDX), implicit_defs=(RAX, RDX, EFLAGS),
                est_size=2, roles=(OperandRole.USE,)),
    # idiv r/m64                         REX.W F7 /7
    X86InstDesc("idiv", (_rm(64),), opcode=0xF7, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=7, rm_op=0,
                implicit_uses=(RAX, RDX), implicit_defs=(RAX, RDX, EFLAGS),
                est_size=3, roles=(OperandRole.USE,)),
    # div r/m32                          F7 /6
    X86InstDesc("div", (_rm(32),), opcode=0xF7, modrm=ModRMUse.EXT_RM, ext=6,
                rm_op=0, implicit_uses=(RAX, RDX), implicit_defs=(RAX, RDX, EFLAGS),
                est_size=2, roles=(OperandRole.USE,)),
    # div r/m64                          REX.W F7 /6
    X86InstDesc("div", (_rm(64),), opcode=0xF7, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=6, rm_op=0,
                implicit_uses=(RAX, RDX), implicit_defs=(RAX, RDX, EFLAGS),
                est_size=3, roles=(OperandRole.USE,)),
    # The variable-count shifts take their count in the low byte of one fixed
    # register, which is said here as an implicit use: the allocator then keeps
    # every other value out of it while the shift wants it.
    X86InstDesc("shl", (_rm(32), _cl()), opcode=0xD3,
                modrm=ModRMUse.EXT_RM, ext=4, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=2, roles=_ACCUMULATE),
    X86InstDesc("shl", (_rm(64), _cl()), opcode=0xD3, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=4, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    X86InstDesc("shr", (_rm(32), _cl()), opcode=0xD3,
                modrm=ModRMUse.EXT_RM, ext=5, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=2, roles=_ACCUMULATE),
    X86InstDesc("shr", (_rm(64), _cl()), opcode=0xD3, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=5, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    X86InstDesc("sar", (_rm(32), _cl()), opcode=0xD3,
                modrm=ModRMUse.EXT_RM, ext=7, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=2, roles=_ACCUMULATE),
    X86InstDesc("sar", (_rm(64), _cl()), opcode=0xD3, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=7, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    X86InstDesc("rol", (_rm(32), _cl()), opcode=0xD3,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=2, roles=_ACCUMULATE),
    X86InstDesc("rol", (_rm(64), _cl()), opcode=0xD3, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    X86InstDesc("ror", (_rm(32), _cl()), opcode=0xD3,
                modrm=ModRMUse.EXT_RM, ext=1, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=2, roles=_ACCUMULATE),
    X86InstDesc("ror", (_rm(64), _cl()), opcode=0xD3, opsize=OpSize.REXW,
                modrm=ModRMUse.EXT_RM, ext=1, rm_op=0,
                implicit_uses=(RCX,), implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    # The scalar floating-point instructions of SSE2, which is in the base of
    # the x86-64 ABI: a binary that uses them requires nothing a binary that
    # does not would not already have.  Each names the width in its own opcode
    # through the prefix byte, so the register operand is the same either way.
    X86InstDesc("movss", (_x(), _xm(32)), opcode=0x10, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    X86InstDesc("movsd", (_x(), _xm()), opcode=0x10, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    X86InstDesc("addss", (_x(), _xm(32)), opcode=0x58, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("addsd", (_x(), _xm()), opcode=0x58, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("subss", (_x(), _xm(32)), opcode=0x5C, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("subsd", (_x(), _xm()), opcode=0x5C, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("mulss", (_x(), _xm(32)), opcode=0x59, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("mulsd", (_x(), _xm()), opcode=0x59, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("divss", (_x(), _xm(32)), opcode=0x5E, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("divsd", (_x(), _xm()), opcode=0x5E, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    # movss m, x                           F3 0F 11 /r
    X86InstDesc("movss", (_mem(32), _x()), opcode=0x11, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=4,
                flags=InstFlags.MAY_STORE,
                roles=(OperandRole.USE, OperandRole.USE)),
    # movsd m, x                           F2 0F 11 /r
    X86InstDesc("movsd", (_mem(64), _x()), opcode=0x11, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=4,
                flags=InstFlags.MAY_STORE,
                roles=(OperandRole.USE, OperandRole.USE)),
    X86InstDesc("ucomiss", (_x(), _xm(32)), opcode=0x2E, map=OpMap.M0F,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_defs=(EFLAGS,), est_size=4, roles=_READS_BOTH),
    X86InstDesc("ucomisd", (_x(), _xm()), opcode=0x2E, map=OpMap.M0F,
                mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                implicit_defs=(EFLAGS,), est_size=4, roles=_READS_BOTH),
    # setp r/m8                         0F 9A /0   (the parity flag, which a
    # floating-point comparison sets where the two are not ordered at all)
    X86InstDesc("setp", (_rm(8),), opcode=0x9A, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # setnp r/m8                         0F 9B /0   (the parity flag, which a
    # floating-point comparison sets where the two are not ordered at all)
    X86InstDesc("setnp", (_rm(8),), opcode=0x9B, map=OpMap.M0F,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0,
                implicit_uses=(EFLAGS,), est_size=3),
    # jmp rel32                          E9 cd
    X86InstDesc("jmp", (_rel(32),), opcode=0xE9, rel_op=0, rel_bits=32,
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER, est_size=5),
    # je rel32                           0F 84 cd
    X86InstDesc("je", (_rel(32),), opcode=0x84, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jne rel32                          0F 85 cd
    X86InstDesc("jne", (_rel(32),), opcode=0x85, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jl rel32                           0F 8C cd
    X86InstDesc("jl", (_rel(32),), opcode=0x8C, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jle rel32                          0F 8E cd
    X86InstDesc("jle", (_rel(32),), opcode=0x8E, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jg rel32                           0F 8F cd
    X86InstDesc("jg", (_rel(32),), opcode=0x8F, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jge rel32                          0F 8D cd
    X86InstDesc("jge", (_rel(32),), opcode=0x8D, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jb rel32                           0F 82 cd
    X86InstDesc("jb", (_rel(32),), opcode=0x82, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jbe rel32                          0F 86 cd
    X86InstDesc("jbe", (_rel(32),), opcode=0x86, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # ja rel32                           0F 87 cd
    X86InstDesc("ja", (_rel(32),), opcode=0x87, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # jae rel32                          0F 83 cd
    X86InstDesc("jae", (_rel(32),), opcode=0x83, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # A call destroys every register the convention calls caller-saved, so the
    # allocator has to be told -- otherwise a value held across one is silently
    # lost.  Naming them here puts a convention's business in the instruction
    # table, which is not where it belongs; it costs nothing today, every
    # convention this target has calling the same registers caller-saved, and
    # the entry in the to-do list says what to do when one does not.
    # call rel32                         E8 cd
    X86InstDesc("call", (_rel(32),), opcode=0xE8, rel_op=0, rel_bits=32,
                implicit_defs=CALLER_SAVED, flags=InstFlags.CALL, est_size=5),
    # ret                                C3
    X86InstDesc("ret", (), opcode=0xC3, flags=InstFlags.TERMINATOR | InstFlags.RETURN, est_size=1),
    # syscall                            0F 05
    X86InstDesc("syscall", (), opcode=0x05, map=OpMap.M0F,
                implicit_defs=(RCX, R11, EFLAGS), est_size=2),
    # ud2                                0F 0B
    X86InstDesc("ud2", (), opcode=0x0B, map=OpMap.M0F,
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER, est_size=2),
)
