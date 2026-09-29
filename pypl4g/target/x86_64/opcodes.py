"""The x86-64 encoding table.

Each row states how one form of one instruction is encoded.  The generic emitter
in ``encoder`` walks the rows; adding an instruction is a row here, and adding a
whole prefix family is one more emitter in the encoder's prefix phase plus the
fields it needs in ``desc``.
"""

from __future__ import annotations

from typing import Final

from ...mc.desc import InstFlags, OperandKind, OperandRole, OperandSpec
from .desc import EncKind, ModRMUse, OpMap, OpSize, VexInfo, X86InstDesc
from .regs import (EFLAGS, GPR, R11, RAX, RBX, RCX, RDX,
                   VEC)


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


def _y() -> OperandSpec:
    """One of the vector registers, named at the wider width the newer levels
    give it."""
    return OperandSpec(OperandKind.REG, rclass=VEC, bits=256)


def _ym() -> OperandSpec:
    """The same, or thirty-two bytes of memory."""
    return OperandSpec(OperandKind.REG | OperandKind.MEM, rclass=VEC, bits=256,
                       mem_bits=256)


def _mem(bits: int | None = None) -> OperandSpec:
    """A memory operand, of any width or of the one named."""
    return OperandSpec(OperandKind.MEM, bits=bits)


#: This architecture's arithmetic takes two operands, so the destination is also
#: the first source: the value it held is still wanted when the instruction runs.
_ACCUMULATE: Final[tuple[OperandRole, ...]] = (OperandRole.DEF_USE, OperandRole.USE)

#: A comparison writes only the flags, which it declares separately.
_READS_BOTH: Final[tuple[OperandRole, ...]] = (OperandRole.USE, OperandRole.USE)

#: The newer forms name their destination separately from both of their
#: sources, so the value the destination held before is not wanted.
_THREE_OPERAND: Final[tuple[OperandRole, ...]] = (
    OperandRole.DEF, OperandRole.USE, OperandRole.USE)


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
    # add r/m8, r8                       00 /r
    # The narrow widths are here because arithmetic on a narrow type is done at
    # that width: what the flags then say is whether the answer went past the
    # end of *that* type, which is the question being asked.
    X86InstDesc("add", (_rm(8), _r(8)), opcode=0x00, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=2, roles=_ACCUMULATE),
    # add r/m16, r16                     66 01 /r
    X86InstDesc("add", (_rm(16), _r(16)), opcode=0x01, opsize=OpSize.P66,
                modrm=ModRMUse.REG_RM, reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=3, roles=_ACCUMULATE),
    # sub r/m8, r8                       28 /r
    X86InstDesc("sub", (_rm(8), _r(8)), opcode=0x28, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, implicit_defs=(EFLAGS,),
                est_size=2, roles=_ACCUMULATE),
    # sub r/m16, r16                     66 29 /r
    X86InstDesc("sub", (_rm(16), _r(16)), opcode=0x29, opsize=OpSize.P66,
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
    # and r/m32, imm32                   81 /4 id
    X86InstDesc("and", (_rm(32), _imm(32)), opcode=0x81,
                modrm=ModRMUse.EXT_RM, ext=4, rm_op=0, imm_op=1, imm_bits=32,
                implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=6, roles=_ACCUMULATE),
    # and r/m32, imm8 (sign extended)    83 /4 ib
    X86InstDesc("and", (_rm(32), _imm(8)), opcode=0x83,
                modrm=ModRMUse.EXT_RM, ext=4, rm_op=0, imm_op=1, imm_bits=8,
                implicit_defs=(EFLAGS,), flags=InstFlags.ZEXT32,
                est_size=3, roles=_ACCUMULATE),
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
    # add r/m8, imm8                     80 /0 ib
    # The narrow immediate forms, for the same reason the narrow register forms
    # are here: arithmetic on a narrow type is done at that type's width.
    X86InstDesc("add", (_rm(8), _imm(8)), opcode=0x80,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0, imm_op=1, imm_bits=8,
                implicit_defs=(EFLAGS,), est_size=3, roles=_ACCUMULATE),
    # sub r/m8, imm8                     80 /5 ib
    X86InstDesc("sub", (_rm(8), _imm(8)), opcode=0x80,
                modrm=ModRMUse.EXT_RM, ext=5, rm_op=0, imm_op=1, imm_bits=8,
                implicit_defs=(EFLAGS,), est_size=3, roles=_ACCUMULATE),
    # add r/m16, imm8 (sign extended)    66 83 /0 ib
    X86InstDesc("add", (_rm(16), _imm(8)), opcode=0x83, opsize=OpSize.P66,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0, imm_op=1, imm_bits=8,
                implicit_defs=(EFLAGS,), est_size=4, roles=_ACCUMULATE),
    # add r/m16, imm16                   66 81 /0 iw
    X86InstDesc("add", (_rm(16), _imm(16)), opcode=0x81, opsize=OpSize.P66,
                modrm=ModRMUse.EXT_RM, ext=0, rm_op=0, imm_op=1, imm_bits=16,
                implicit_defs=(EFLAGS,), est_size=5, roles=_ACCUMULATE),
    # sub r/m16, imm8 (sign extended)    66 83 /5 ib
    X86InstDesc("sub", (_rm(16), _imm(8)), opcode=0x83, opsize=OpSize.P66,
                modrm=ModRMUse.EXT_RM, ext=5, rm_op=0, imm_op=1, imm_bits=8,
                implicit_defs=(EFLAGS,), est_size=4, roles=_ACCUMULATE),
    # sub r/m16, imm16                   66 81 /5 iw
    X86InstDesc("sub", (_rm(16), _imm(16)), opcode=0x81, opsize=OpSize.P66,
                modrm=ModRMUse.EXT_RM, ext=5, rm_op=0, imm_op=1, imm_bits=16,
                implicit_defs=(EFLAGS,), est_size=5, roles=_ACCUMULATE),
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
    # The larger and the smaller of two, which these machines have as
    # instructions of their own.  What they answer where one of the two is not
    # a number differs between architectures; nothing here can be, an operation
    # whose answer is not a number having stopped the program where it arose.
    X86InstDesc("maxss", (_x(), _xm(32)), opcode=0x5F, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("maxsd", (_x(), _xm()), opcode=0x5F, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("minss", (_x(), _xm(32)), opcode=0x5D, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("minsd", (_x(), _xm()), opcode=0x5D, map=OpMap.M0F,
                mandatory_prefix=0xF2, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    # The whole number a floating-point number rounds to.  These are SSE4.1,
    # which is what the architecture's second level promises and the first does
    # not; the immediate says which way to go, and the value four says to ask
    # the processor's own rounding mode rather than to name one.
    # roundss xmm, xmm/m32, imm8        66 0F 3A 0A /r ib
    X86InstDesc("roundss", (_x(), _xm(32), _imm(8)), opcode=0x0A, map=OpMap.M0F3A,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, imm_op=2, imm_bits=8, est_size=6,
                roles=(OperandRole.DEF, OperandRole.USE, OperandRole.USE)),
    # roundsd xmm, xmm/m64, imm8        66 0F 3A 0B /r ib
    X86InstDesc("roundsd", (_x(), _xm(64), _imm(8)), opcode=0x0B, map=OpMap.M0F3A,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, imm_op=2, imm_bits=8, est_size=6,
                roles=(OperandRole.DEF, OperandRole.USE, OperandRole.USE)),
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
    # andps xmm, xmm/m128                   0F 54 /r
    # andpd xmm, xmm/m128                66 0F 54 /r
    # The magnitude of a floating-point number is the number with its sign bit
    # cleared, and there is no instruction that clears one bit of a vector
    # register: the mask is a constant in the image, and a place in memory that
    # one of these reads must be sixteen bytes aligned.
    X86InstDesc("andps", (_x(), _xm(128)), opcode=0x54, map=OpMap.M0F,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=4,
                roles=_ACCUMULATE),
    X86InstDesc("andpd", (_x(), _xm(128)), opcode=0x54, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # -- a whole run of elements in one register -------------------------------
    # These are the SSE2 integer instructions, which every x86-64 processor has:
    # the architecture's own oldest level includes them, so a run of elements is
    # done this way at every level rather than only at the newer ones.  A read
    # names the number of bytes it actually reads, which is what lets a run
    # shorter than a register be read without touching what lies beyond it, and
    # what makes the lanes above it zero rather than whatever was there.
    #
    # movdqu xmm, xmm/m128               F3 0F 6F /r
    X86InstDesc("movdqu", (_x(), _xm(128)), opcode=0x6F, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # movdqu m128, xmm                   F3 0F 7F /r
    X86InstDesc("movdqu", (_mem(128), _x()), opcode=0x7F, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=4, roles=_READS_BOTH),
    # movq xmm, xmm/m64                  F3 0F 7E /r
    # Eight bytes read into the low half and the high half cleared, which is
    # what makes a run of eight bytes a value with nothing of its neighbours in
    # it.
    X86InstDesc("movq", (_x(), _xm(64)), opcode=0x7E, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # movq xmm/m64, xmm                  66 0F D6 /r
    X86InstDesc("movq", (_mem(64), _x()), opcode=0xD6, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=4, roles=_READS_BOTH),
    # movd xmm, r/m32                    66 0F 6E /r
    X86InstDesc("movd", (_x(), _mem(32)), opcode=0x6E, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    X86InstDesc("movd", (_x(), _r(32)), opcode=0x6E, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # movd r/m32, xmm                    66 0F 7E /r
    X86InstDesc("movd", (_mem(32), _x()), opcode=0x7E, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=4, roles=_READS_BOTH),
    X86InstDesc("movd", (_r(32), _x()), opcode=0x7E, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # movq xmm, r/m64                    66 REX.W 0F 6E /r
    X86InstDesc("movq", (_x(), _r(64)), opcode=0x6E, map=OpMap.M0F,
                mandatory_prefix=0x66, opsize=OpSize.REXW,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1, est_size=5,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # The arithmetic over a whole run, one instruction per lane width.  What
    # each does to a lane it does to that lane alone: there is no carry between
    # them, which is what makes a run of additions one addition.
    # paddb xmm, xmm/m128                66 0F FC /r
    X86InstDesc("paddb", (_x(), _xm(128)), opcode=0xFC, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # paddw xmm, xmm/m128                66 0F FD /r
    X86InstDesc("paddw", (_x(), _xm(128)), opcode=0xFD, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # paddd xmm, xmm/m128                66 0F FE /r
    X86InstDesc("paddd", (_x(), _xm(128)), opcode=0xFE, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # paddq xmm, xmm/m128                66 0F D4 /r
    X86InstDesc("paddq", (_x(), _xm(128)), opcode=0xD4, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubb xmm, xmm/m128                66 0F F8 /r
    X86InstDesc("psubb", (_x(), _xm(128)), opcode=0xF8, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubw xmm, xmm/m128                66 0F F9 /r
    X86InstDesc("psubw", (_x(), _xm(128)), opcode=0xF9, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubd xmm, xmm/m128                66 0F FA /r
    X86InstDesc("psubd", (_x(), _xm(128)), opcode=0xFA, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubq xmm, xmm/m128                66 0F FB /r
    X86InstDesc("psubq", (_x(), _xm(128)), opcode=0xFB, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # pmullw xmm, xmm/m128               66 0F D5 /r
    # The low half of the product in every lane, which is all a multiplication
    # that may wrap wants.  There is no byte-wide form and the word-wide one is
    # SSE4.1, which is why which widths this exists at is a property of the
    # level and not of the architecture.
    X86InstDesc("pmullw", (_x(), _xm(128)), opcode=0xD5, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # pmulld xmm, xmm/m128               66 0F 38 40 /r
    X86InstDesc("pmulld", (_x(), _xm(128)), opcode=0x40, map=OpMap.M0F38,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=5, roles=_ACCUMULATE),
    # The saturating forms, which answer with the nearest value the lane's type
    # can hold rather than going past it.  Only the two narrow widths have them,
    # which is why the wider ones are still done an element at a time.
    # paddusb xmm, xmm/m128              66 0F DC /r
    X86InstDesc("paddusb", (_x(), _xm(128)), opcode=0xDC, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # paddusw xmm, xmm/m128              66 0F DD /r
    X86InstDesc("paddusw", (_x(), _xm(128)), opcode=0xDD, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubusb xmm, xmm/m128              66 0F D8 /r
    X86InstDesc("psubusb", (_x(), _xm(128)), opcode=0xD8, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubusw xmm, xmm/m128              66 0F D9 /r
    X86InstDesc("psubusw", (_x(), _xm(128)), opcode=0xD9, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # paddsb xmm, xmm/m128               66 0F EC /r
    X86InstDesc("paddsb", (_x(), _xm(128)), opcode=0xEC, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # paddsw xmm, xmm/m128               66 0F ED /r
    X86InstDesc("paddsw", (_x(), _xm(128)), opcode=0xED, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubsb xmm, xmm/m128               66 0F E8 /r
    X86InstDesc("psubsb", (_x(), _xm(128)), opcode=0xE8, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # psubsw xmm, xmm/m128               66 0F E9 /r
    X86InstDesc("psubsw", (_x(), _xm(128)), opcode=0xE9, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # pmovmskb r32, xmm                  66 0F D7 /r
    # The top bit of every byte, gathered into an ordinary register.  This is
    # how a question asked in every lane at once becomes the one question a
    # branch asks: the lanes that went past have their top bit set, and what is
    # wanted is whether any of them did.
    X86InstDesc("pmovmskb", (_r(32), _x()), opcode=0xD7, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # pand xmm, xmm/m128                 66 0F DB /r
    X86InstDesc("pand", (_x(), _xm(128)), opcode=0xDB, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # por xmm, xmm/m128                  66 0F EB /r
    X86InstDesc("por", (_x(), _xm(128)), opcode=0xEB, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # pxor xmm, xmm/m128                 66 0F EF /r
    X86InstDesc("pxor", (_x(), _xm(128)), opcode=0xEF, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # pcmpeqd xmm, xmm/m128              66 0F 76 /r
    # Asked of a register and itself it is how every bit of one is set, there
    # being no instruction that puts a constant in one of these.
    X86InstDesc("pcmpeqd", (_x(), _xm(128)), opcode=0x76, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # punpcklbw xmm, xmm/m128            66 0F 60 /r
    # The three that double the width of what is in the low half by taking every
    # value twice, which is how one value is spread over a whole register: a
    # byte becomes a pair, the pair a quadruple, and so on until the register is
    # full.
    X86InstDesc("punpcklbw", (_x(), _xm(128)), opcode=0x60, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # punpcklwd xmm, xmm/m128            66 0F 61 /r
    X86InstDesc("punpcklwd", (_x(), _xm(128)), opcode=0x61, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # punpcklqdq xmm, xmm/m128           66 0F 6C /r
    X86InstDesc("punpcklqdq", (_x(), _xm(128)), opcode=0x6C, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4, roles=_ACCUMULATE),
    # pshufd xmm, xmm/m128, imm8         66 0F 70 /r ib
    X86InstDesc("pshufd", (_x(), _xm(128), _imm(8)), opcode=0x70, map=OpMap.M0F,
                mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, imm_op=2, imm_bits=8, est_size=5,
                roles=(OperandRole.DEF, OperandRole.USE, OperandRole.USE)),
    # -- the same, thirty-two bytes at a time -----------------------------------
    # These are AVX2, which the architecture's third level includes.  They are
    # the same operations over twice as many lanes, with two differences that
    # come from the prefix rather than from the operation: the destination is
    # named separately from both sources, so nothing has to be moved into place
    # first, and the width is a field rather than a different opcode.
    #
    # The mnemonics are the ones the narrow forms have.  Which of the two a row
    # is, is said by how wide its registers are, and that is what the table
    # matches on -- so the lowering asks for "and" and gets whichever it has
    # registers for.
    #
    # vmovdqu ymm, ymm/m256              VEX.256.F3.0F 6F /r
    X86InstDesc("movdqu", (_y(), _ym()), opcode=0x6F, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, vex=VexInfo(length=256), est_size=5,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # vmovdqu m256, ymm                  VEX.256.F3.0F 7F /r
    X86InstDesc("movdqu", (_mem(256), _y()), opcode=0x7F, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=1, rm_op=0, vex=VexInfo(length=256), est_size=5,
                roles=_READS_BOTH),
    # vpmovmskb r32, ymm                 VEX.256.66.0F D7 /r
    X86InstDesc("pmovmskb", (_r(32), _y()), opcode=0xD7, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, vex=VexInfo(length=256), est_size=5,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # The bitwise three and the comparison that sets every bit.
    X86InstDesc("pand", (_y(), _y(), _ym()), opcode=0xDB, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("por", (_y(), _y(), _ym()), opcode=0xEB, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("pxor", (_y(), _y(), _ym()), opcode=0xEF, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("pcmpeqd", (_y(), _y(), _ym()), opcode=0x76, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    # The arithmetic, one per lane width.
    X86InstDesc("paddb", (_y(), _y(), _ym()), opcode=0xFC, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("paddw", (_y(), _y(), _ym()), opcode=0xFD, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("paddd", (_y(), _y(), _ym()), opcode=0xFE, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("paddq", (_y(), _y(), _ym()), opcode=0xD4, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubb", (_y(), _y(), _ym()), opcode=0xF8, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubw", (_y(), _y(), _ym()), opcode=0xF9, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubd", (_y(), _y(), _ym()), opcode=0xFA, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubq", (_y(), _y(), _ym()), opcode=0xFB, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("pmullw", (_y(), _y(), _ym()), opcode=0xD5, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("pmulld", (_y(), _y(), _ym()), opcode=0x40, map=OpMap.M0F38,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=6,
                roles=_THREE_OPERAND),
    # And the saturating forms the two narrow widths have.
    X86InstDesc("paddusb", (_y(), _y(), _ym()), opcode=0xDC, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("paddusw", (_y(), _y(), _ym()), opcode=0xDD, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubusb", (_y(), _y(), _ym()), opcode=0xD8, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubusw", (_y(), _y(), _ym()), opcode=0xD9, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("paddsb", (_y(), _y(), _ym()), opcode=0xEC, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("paddsw", (_y(), _y(), _ym()), opcode=0xED, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubsb", (_y(), _y(), _ym()), opcode=0xE8, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    X86InstDesc("psubsw", (_y(), _y(), _ym()), opcode=0xE9, map=OpMap.M0F,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=2,
                vex=VexInfo(length=256, vvvv_op=1), est_size=5,
                roles=_THREE_OPERAND),
    # vpbroadcastb/w/d/q ymm, xmm        VEX.256.66.0F38.W0 78/79/58/59 /r
    # One value in every lane, in one instruction: the value goes into the low
    # lane of a narrow register first, which is the only way to get an ordinary
    # register's value into one of these at all.
    X86InstDesc("pbroadcastb", (_y(), _x()), opcode=0x78, map=OpMap.M0F38,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                vex=VexInfo(length=256), est_size=6,
                roles=(OperandRole.DEF, OperandRole.USE)),
    X86InstDesc("pbroadcastw", (_y(), _x()), opcode=0x79, map=OpMap.M0F38,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                vex=VexInfo(length=256), est_size=6,
                roles=(OperandRole.DEF, OperandRole.USE)),
    X86InstDesc("pbroadcastd", (_y(), _x()), opcode=0x58, map=OpMap.M0F38,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                vex=VexInfo(length=256), est_size=6,
                roles=(OperandRole.DEF, OperandRole.USE)),
    X86InstDesc("pbroadcastq", (_y(), _x()), opcode=0x59, map=OpMap.M0F38,
                enc=EncKind.VEX, mandatory_prefix=0x66,
                modrm=ModRMUse.REG_RM, reg_op=0, rm_op=1,
                vex=VexInfo(length=256), est_size=6,
                roles=(OperandRole.DEF, OperandRole.USE)),
    # cvtss2sd xmm, xmm/m32              F3 0F 5A /r
    X86InstDesc("cvtss2sd", (_x(), _xm(32)), opcode=0x5A, map=OpMap.M0F,
                mandatory_prefix=0xF3, modrm=ModRMUse.REG_RM,
                reg_op=0, rm_op=1, est_size=4,
                roles=(OperandRole.DEF, OperandRole.USE)),
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
    # jno rel32                          0F 81 cd
    # What it reads is the overflow flag, which the arithmetic before it wrote:
    # for a signed operation that is the whole of the question.  It is the
    # negative form because what it jumps over is the report of the fault, which
    # is how every check here is written: going past is what does not come back.
    X86InstDesc("jno", (_rel(32),), opcode=0x81, map=OpMap.M0F,
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
    # jnp rel32                          0F 8B cd   (the parity flag again: a
    # branch taken where the two compared were ordered, which is what says
    # neither of them was a not-a-number)
    X86InstDesc("jnp", (_rel(32),), opcode=0x8B, map=OpMap.M0F,
                rel_op=0, rel_bits=32, implicit_uses=(EFLAGS,),
                flags=InstFlags.TERMINATOR, est_size=6),
    # What a call destroys is the *callee's* to say: two functions of one
    # compilation may follow different conventions, and a function that destroys
    # little is one a caller has to save little around -- neither of which the
    # table can know.  So the call carries it, per call, and what is named here
    # is only what the instruction itself writes.
    # call rel32                         E8 cd
    X86InstDesc("call", (_rel(32),), opcode=0xE8, rel_op=0, rel_bits=32,
                flags=InstFlags.CALL, est_size=5),
    # call r/m64                         FF /2
    # A call through a register, which is what a function held in a value is
    # called by.  No REX.W: in long mode the operand of a near call is already
    # sixty-four bits and saying so again is refused by the assembler.
    X86InstDesc("call", (_rm(64),), opcode=0xFF, modrm=ModRMUse.EXT_RM, ext=2,
                rm_op=0, flags=InstFlags.CALL, est_size=3),
    # ret                                C3
    X86InstDesc("ret", (), opcode=0xC3, flags=InstFlags.TERMINATOR | InstFlags.RETURN, est_size=1),
    # syscall                            0F 05
    X86InstDesc("syscall", (), opcode=0x05, map=OpMap.M0F,
                implicit_defs=(RCX, R11, EFLAGS), est_size=2),
    # cpuid                              0F A2
    # It reads the leaf in EAX and the subleaf in ECX and answers in all four,
    # which is why every one of them is written down: nothing else tells the
    # register allocator that a value in EBX does not survive it.
    X86InstDesc("cpuid", (), opcode=0xA2, map=OpMap.M0F,
                implicit_uses=(RAX, RCX),
                implicit_defs=(RAX, RBX, RCX, RDX), est_size=2),
    # ud2                                0F 0B
    X86InstDesc("ud2", (), opcode=0x0B, map=OpMap.M0F,
                flags=InstFlags.TERMINATOR | InstFlags.BARRIER, est_size=2),
)
