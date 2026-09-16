"""The x86-64 encoder.

The differential test is the important one: every row of the table is encoded and
handed to an external disassembler, which must read back the instruction we
claimed to write.  That is what makes it safe to add rows quickly.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from pypl4g.mc.desc import InstrTable, SelectionError
from pypl4g.mc.inst import MCInst
from pypl4g.mc.operand import MCImm, MCMem, MCReg, MCSymRef, SymExpr
from pypl4g.mc.reg import VirtReg, interferes
from pypl4g.mc.symbol import MCSymbol
from pypl4g.target.x86_64.encoder import EncodingError, encode
from pypl4g.target.x86_64.opcodes import X86_INSTRS
from pypl4g.target.x86_64.regs import GPR, reg

from conftest import OBJDUMP

TABLE = InstrTable(X86_INSTRS)


def assemble(mnemonic: str, *operands: object) -> bytes:
    """Select and encode one instruction."""
    desc = TABLE.select(mnemonic, operands)  # type: ignore[arg-type]
    return encode(MCInst(desc, operands))[0]  # type: ignore[arg-type]


#: Hand-checked encodings, so that a regression in the generic emitter is
#: diagnosed without needing an external tool.
KNOWN = [
    ("31 ed", "xor", MCReg(reg("ebp")), MCReg(reg("ebp"))),
    ("31 c0", "xor", MCReg(reg("eax")), MCReg(reg("eax"))),
    ("89 c7", "mov", MCReg(reg("edi")), MCReg(reg("eax"))),
    ("b8 e7 00 00 00", "mov", MCReg(reg("eax")), MCImm(231, 32, False)),
    ("48 89 c7", "mov", MCReg(reg("rdi")), MCReg(reg("rax"))),
    ("0f 05", "syscall"),
    ("0f a2", "cpuid"),
    ("00 d8", "add", MCReg(reg("al")), MCReg(reg("bl"))),
    ("66 01 d8", "add", MCReg(reg("ax")), MCReg(reg("bx"))),
    ("28 d8", "sub", MCReg(reg("al")), MCReg(reg("bl"))),
    ("80 c3 05", "add", MCReg(reg("bl")), MCImm(5, 8)),
    ("80 eb 05", "sub", MCReg(reg("bl")), MCImm(5, 8)),
    ("66 83 c3 05", "add", MCReg(reg("bx")), MCImm(5, 8)),
    ("66 81 c3 00 01", "add", MCReg(reg("bx")), MCImm(256, 16)),
    ("66 83 eb 05", "sub", MCReg(reg("bx")), MCImm(5, 8)),
    ("66 81 eb 00 01", "sub", MCReg(reg("bx")), MCImm(256, 16)),
    ("66 29 d8", "sub", MCReg(reg("ax")), MCReg(reg("bx"))),
    ("0f 81 00 00 00 00", "jno", MCSymRef(SymExpr(MCSymbol("over")))),
    ("c3", "ret"),
    ("0f 0b", "ud2"),
    ("e8 00 00 00 00", "call", MCSymRef(SymExpr(MCSymbol("main")))),
    ("ff d3", "call", MCReg(reg("rbx"))),
]


@pytest.mark.parametrize(("expected", "mnemonic", "operands"),
                         [(k[0], k[1], k[2:]) for k in KNOWN],
                         ids=[k[1] + "-" + k[0].replace(" ", "") for k in KNOWN])
def test_known_encodings(expected: str, mnemonic: str, operands: tuple) -> None:
    """The encodings the entry point is built from are exactly these bytes."""
    assert assemble(mnemonic, *operands).hex(" ") == expected


#: One legal operand tuple per table row, plus the addressing modes that are
#: easy to get wrong.
SAMPLES = [
    ("mov eax,0xe7", "mov", MCReg(reg("eax")), MCImm(231, 32, False)),
    ("movabs rax,0x123456789", "mov", MCReg(reg("rax")), MCImm(0x123456789, 64)),
    ("mov rax,0x5", "mov", MCReg(reg("rax")), MCImm(5, 32)),
    ("mov edi,eax", "mov", MCReg(reg("edi")), MCReg(reg("eax"))),
    ("mov rdi,rax", "mov", MCReg(reg("rdi")), MCReg(reg("rax"))),
    ("mov al,bl", "mov", MCReg(reg("al")), MCReg(reg("bl"))),
    ("mov dil,sil", "mov", MCReg(reg("dil")), MCReg(reg("sil"))),
    ("mov r9d,r10d", "mov", MCReg(reg("r9d")), MCReg(reg("r10d"))),
    ("xor ebp,ebp", "xor", MCReg(reg("ebp")), MCReg(reg("ebp"))),
    ("xor rbx,rcx", "xor", MCReg(reg("rbx")), MCReg(reg("rcx"))),
    ("add esi,edx", "add", MCReg(reg("esi")), MCReg(reg("edx"))),
    ("add rsi,rdx", "add", MCReg(reg("rsi")), MCReg(reg("rdx"))),
    ("sub esi,edx", "sub", MCReg(reg("esi")), MCReg(reg("edx"))),
    ("sub rbx,rcx", "sub", MCReg(reg("rbx")), MCReg(reg("rcx"))),
    ("ret", "ret"),
    ("syscall", "syscall"),
    ("cpuid", "cpuid"),
    ("add al,bl", "add", MCReg(reg("al")), MCReg(reg("bl"))),
    ("add ax,bx", "add", MCReg(reg("ax")), MCReg(reg("bx"))),
    ("sub al,bl", "sub", MCReg(reg("al")), MCReg(reg("bl"))),
    ("add bl,0x5", "add", MCReg(reg("bl")), MCImm(5, 8)),
    ("sub bl,0x5", "sub", MCReg(reg("bl")), MCImm(5, 8)),
    ("add bx,0x5", "add", MCReg(reg("bx")), MCImm(5, 8)),
    ("add bx,0x100", "add", MCReg(reg("bx")), MCImm(256, 16)),
    ("sub bx,0x5", "sub", MCReg(reg("bx")), MCImm(5, 8)),
    ("sub bx,0x100", "sub", MCReg(reg("bx")), MCImm(256, 16)),
    ("sub ax,bx", "sub", MCReg(reg("ax")), MCReg(reg("bx"))),
    ("ud2", "ud2"),
    ("movdqu xmm0,xmm1", "movdqu", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("movdqu xmm0,XMMWORD PTR [rbx]", "movdqu", MCReg(reg("xmm0")),
     MCMem(base=reg("rbx"), size_bits=128)),
    ("movdqu XMMWORD PTR [rbx],xmm0", "movdqu",
     MCMem(base=reg("rbx"), size_bits=128), MCReg(reg("xmm0"))),
    ("movq xmm0,QWORD PTR [rbx]", "movq", MCReg(reg("xmm0")),
     MCMem(base=reg("rbx"), size_bits=64)),
    ("movq QWORD PTR [rbx],xmm0", "movq",
     MCMem(base=reg("rbx"), size_bits=64), MCReg(reg("xmm0"))),
    ("movd xmm0,DWORD PTR [rbx]", "movd", MCReg(reg("xmm0")),
     MCMem(base=reg("rbx"), size_bits=32)),
    ("movd DWORD PTR [rbx],xmm0", "movd",
     MCMem(base=reg("rbx"), size_bits=32), MCReg(reg("xmm0"))),
    ("movd xmm0,eax", "movd", MCReg(reg("xmm0")), MCReg(reg("eax"))),
    ("movd eax,xmm0", "movd", MCReg(reg("eax")), MCReg(reg("xmm0"))),
    ("movq xmm0,rax", "movq", MCReg(reg("xmm0")), MCReg(reg("rax"))),
    ("and ebx,0xffff", "and", MCReg(reg("ebx")), MCImm(0xFFFF, 32, False)),
    ("and ebx,0x5", "and", MCReg(reg("ebx")), MCImm(5, 8)),
    ("paddb xmm0,xmm1", "paddb", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("paddw xmm0,xmm1", "paddw", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("paddd xmm0,xmm1", "paddd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("paddq xmm0,xmm1", "paddq", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubb xmm0,xmm1", "psubb", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubw xmm0,xmm1", "psubw", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubd xmm0,xmm1", "psubd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubq xmm0,xmm1", "psubq", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("pmullw xmm0,xmm1", "pmullw", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("pmulld xmm0,xmm1", "pmulld", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("paddusb xmm0,xmm1", "paddusb", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("paddusw xmm0,xmm1", "paddusw", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubusb xmm0,xmm1", "psubusb", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubusw xmm0,xmm1", "psubusw", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("paddsb xmm0,xmm1", "paddsb", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("paddsw xmm0,xmm1", "paddsw", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubsb xmm0,xmm1", "psubsb", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("psubsw xmm0,xmm1", "psubsw", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("pmovmskb eax,xmm1", "pmovmskb", MCReg(reg("eax")), MCReg(reg("xmm1"))),
    ("pand xmm0,xmm1", "pand", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("por xmm0,xmm1", "por", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("pxor xmm0,xmm1", "pxor", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("pcmpeqd xmm0,xmm0", "pcmpeqd", MCReg(reg("xmm0")), MCReg(reg("xmm0"))),
    ("punpcklbw xmm0,xmm0", "punpcklbw", MCReg(reg("xmm0")), MCReg(reg("xmm0"))),
    ("punpcklwd xmm0,xmm0", "punpcklwd", MCReg(reg("xmm0")), MCReg(reg("xmm0"))),
    ("punpcklqdq xmm0,xmm0", "punpcklqdq", MCReg(reg("xmm0")), MCReg(reg("xmm0"))),
    ("pshufd xmm0,xmm1,0x0", "pshufd", MCReg(reg("xmm0")), MCReg(reg("xmm1")),
     MCImm(0, 8, False)),
    ("vmovdqu ymm0,ymm1", "movdqu", MCReg(reg("ymm0")), MCReg(reg("ymm1"))),
    ("vmovdqu ymm0,YMMWORD PTR [rbx]", "movdqu", MCReg(reg("ymm0")),
     MCMem(base=reg("rbx"), size_bits=256)),
    ("vmovdqu YMMWORD PTR [rbx],ymm0", "movdqu",
     MCMem(base=reg("rbx"), size_bits=256), MCReg(reg("ymm0"))),
    ("vpmovmskb eax,ymm1", "pmovmskb", MCReg(reg("eax")), MCReg(reg("ymm1"))),
    ("vpand ymm0,ymm1,ymm2", "pand", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpor ymm0,ymm1,ymm2", "por", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpxor ymm0,ymm1,ymm2", "pxor", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpcmpeqd ymm0,ymm1,ymm2", "pcmpeqd", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddb ymm0,ymm1,ymm2", "paddb", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddw ymm0,ymm1,ymm2", "paddw", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddd ymm0,ymm1,ymm2", "paddd", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddq ymm0,ymm1,ymm2", "paddq", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubb ymm0,ymm1,ymm2", "psubb", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubw ymm0,ymm1,ymm2", "psubw", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubd ymm0,ymm1,ymm2", "psubd", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubq ymm0,ymm1,ymm2", "psubq", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpmullw ymm0,ymm1,ymm2", "pmullw", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpmulld ymm0,ymm1,ymm2", "pmulld", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddusb ymm0,ymm1,ymm2", "paddusb", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddusw ymm0,ymm1,ymm2", "paddusw", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubusb ymm0,ymm1,ymm2", "psubusb", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubusw ymm0,ymm1,ymm2", "psubusw", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddsb ymm0,ymm1,ymm2", "paddsb", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpaddsw ymm0,ymm1,ymm2", "paddsw", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubsb ymm0,ymm1,ymm2", "psubsb", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpsubsw ymm0,ymm1,ymm2", "psubsw", MCReg(reg("ymm0")), MCReg(reg("ymm1")),
     MCReg(reg("ymm2"))),
    ("vpbroadcastb ymm0,xmm1", "pbroadcastb", MCReg(reg("ymm0")), MCReg(reg("xmm1"))),
    ("vpbroadcastw ymm0,xmm1", "pbroadcastw", MCReg(reg("ymm0")), MCReg(reg("xmm1"))),
    ("vpbroadcastd ymm0,xmm1", "pbroadcastd", MCReg(reg("ymm0")), MCReg(reg("xmm1"))),
    ("vpbroadcastq ymm0,xmm1", "pbroadcastq", MCReg(reg("ymm0")), MCReg(reg("xmm1"))),
    ("mov rax,QWORD PTR [rbx+rcx*4+0x8]", "mov", MCReg(reg("rax")),
     MCMem(base=reg("rbx"), index=reg("rcx"), scale=4, disp=8)),
    ("mov eax,DWORD PTR [rbx]", "mov", MCReg(reg("eax")), MCMem(base=reg("rbx"))),
    ("mov al,BYTE PTR [rbx]", "mov", MCReg(reg("al")), MCMem(base=reg("rbx"))),
    ("mov BYTE PTR [rbx],al", "mov", MCMem(base=reg("rbx")), MCReg(reg("al"))),
    ("mov DWORD PTR [rbx],eax", "mov", MCMem(base=reg("rbx")), MCReg(reg("eax"))),
    ("mov QWORD PTR [rsp],rbp", "mov", MCMem(base=reg("rsp")), MCReg(reg("rbp"))),
    ("mov QWORD PTR [rbp+0x0],rax", "mov", MCMem(base=reg("rbp")), MCReg(reg("rax"))),
    ("mov QWORD PTR [r13+0x0],rax", "mov", MCMem(base=reg("r13")), MCReg(reg("rax"))),
    ("mov QWORD PTR [r12],rax", "mov", MCMem(base=reg("r12")), MCReg(reg("rax"))),
    ("mov rax,QWORD PTR ds:0x1234", "mov", MCReg(reg("rax")), MCMem(disp=0x1234)),
    ("mov rax,QWORD PTR [rcx*8+0x0]", "mov", MCReg(reg("rax")),
     MCMem(index=reg("rcx"), scale=8)),
    ("mov rax,QWORD PTR [rbx+0x200]", "mov", MCReg(reg("rax")),
     MCMem(base=reg("rbx"), disp=0x200)),
    ("mov rax,QWORD PTR fs:0x10", "mov", MCReg(reg("rax")),
     MCMem(disp=0x10, seg=reg("fs"))),
    ("mov BYTE PTR [rbx],0x5", "mov", MCMem(base=reg("rbx"), size_bits=8),
     MCImm(5, 8, False)),
    ("mov WORD PTR [rbx],0x5", "mov", MCMem(base=reg("rbx"), size_bits=16),
     MCImm(5, 16, False)),
    ("mov DWORD PTR [rbx],0x5", "mov", MCMem(base=reg("rbx"), size_bits=32),
     MCImm(5, 32, False)),
    ("mov QWORD PTR [rbx],0x5", "mov", MCMem(base=reg("rbx"), size_bits=64),
     MCImm(5, 32, False)),
    ("mov WORD PTR [rbx],ax", "mov", MCMem(base=reg("rbx"), size_bits=16),
     MCReg(reg("ax"))),
    ("movzx eax,BYTE PTR [rbx]", "movzx", MCReg(reg("eax")),
     MCMem(base=reg("rbx"), size_bits=8)),
    ("movzx eax,WORD PTR [rbx]", "movzx", MCReg(reg("eax")),
     MCMem(base=reg("rbx"), size_bits=16)),
    ("movsx eax,BYTE PTR [rbx]", "movsx", MCReg(reg("eax")),
     MCMem(base=reg("rbx"), size_bits=8)),
    ("movsx eax,WORD PTR [rbx]", "movsx", MCReg(reg("eax")),
     MCMem(base=reg("rbx"), size_bits=16)),
    ("movsxd rax,DWORD PTR [rbx]", "movsxd", MCReg(reg("rax")),
     MCMem(base=reg("rbx"), size_bits=32)),
    ("mov eax,DWORD PTR [rbx]", "mov", MCReg(reg("eax")),
     MCMem(base=reg("rbx"), size_bits=32)),
    ("mov rax,QWORD PTR [rbx]", "mov", MCReg(reg("rax")),
     MCMem(base=reg("rbx"), size_bits=64)),
    ("lea rdi,[rip+0x0]", "lea", MCReg(reg("rdi")),
     MCMem(rip_relative=True, disp_sym=SymExpr(MCSymbol("msg")))),
    ("call", "call", MCSymRef(SymExpr(MCSymbol("main")))),
    ("call rbx", "call", MCReg(reg("rbx"))),
    ("sub rsp,0x10", "sub", MCReg(reg("rsp")), MCImm(16, 8)),
    ("add rsp,0x10", "add", MCReg(reg("rsp")), MCImm(16, 8)),
    ("sub rsp,0x200", "sub", MCReg(reg("rsp")), MCImm(0x200, 32)),
    ("add rsp,0x200", "add", MCReg(reg("rsp")), MCImm(0x200, 32)),
    ("and esi,edx", "and", MCReg(reg("esi")), MCReg(reg("edx"))),
    ("and rsi,rdx", "and", MCReg(reg("rsi")), MCReg(reg("rdx"))),
    ("or esi,edx", "or", MCReg(reg("esi")), MCReg(reg("edx"))),
    ("or rsi,rdx", "or", MCReg(reg("rsi")), MCReg(reg("rdx"))),
    ("not esi", "not", MCReg(reg("esi"))),
    ("not rsi", "not", MCReg(reg("rsi"))),
    ("cmp esi,edx", "cmp", MCReg(reg("esi")), MCReg(reg("edx"))),
    ("cmp rsi,rdx", "cmp", MCReg(reg("rsi")), MCReg(reg("rdx"))),
    ("cmp bl,0x5", "cmp", MCReg(reg("bl")), MCImm(5, 8, False)),
    ("cmp ebx,0x1234", "cmp", MCReg(reg("ebx")), MCImm(0x1234, 32, False)),
    ("cmp rbx,0x1234", "cmp", MCReg(reg("rbx")), MCImm(0x1234, 32, False)),
    ("test eax,eax", "test", MCReg(reg("eax")), MCReg(reg("eax"))),
    ("test rax,rax", "test", MCReg(reg("rax")), MCReg(reg("rax"))),
    ("sete al", "sete", MCReg(reg("al"))),
    ("setne al", "setne", MCReg(reg("al"))),
    ("setl al", "setl", MCReg(reg("al"))),
    ("setle al", "setle", MCReg(reg("al"))),
    ("setg al", "setg", MCReg(reg("al"))),
    ("setge al", "setge", MCReg(reg("al"))),
    ("setb al", "setb", MCReg(reg("al"))),
    ("setbe al", "setbe", MCReg(reg("al"))),
    ("seta al", "seta", MCReg(reg("al"))),
    ("setae al", "setae", MCReg(reg("al"))),
    ("imul eax,ebx", "imul", MCReg(reg("eax")), MCReg(reg("ebx"))),
    ("imul rax,rbx", "imul", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmove rax,rbx", "cmove", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovne rax,rbx", "cmovne", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovl rax,rbx", "cmovl", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovle rax,rbx", "cmovle", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovg rax,rbx", "cmovg", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovge rax,rbx", "cmovge", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovb rax,rbx", "cmovb", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovbe rax,rbx", "cmovbe", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmova rax,rbx", "cmova", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cmovae rax,rbx", "cmovae", MCReg(reg("rax")), MCReg(reg("rbx"))),
    ("cdq", "cdq"),
    ("cqo", "cqo"),
    ("idiv ebx", "idiv", MCReg(reg("ebx"))),
    ("idiv rbx", "idiv", MCReg(reg("rbx"))),
    ("div ebx", "div", MCReg(reg("ebx"))),
    ("div rbx", "div", MCReg(reg("rbx"))),
    ("shl ebx,cl", "shl", MCReg(reg("ebx")), MCReg(reg("cl"))),
    ("shl rbx,cl", "shl", MCReg(reg("rbx")), MCReg(reg("cl"))),
    ("shr ebx,cl", "shr", MCReg(reg("ebx")), MCReg(reg("cl"))),
    ("shr rbx,cl", "shr", MCReg(reg("rbx")), MCReg(reg("cl"))),
    ("sar ebx,cl", "sar", MCReg(reg("ebx")), MCReg(reg("cl"))),
    ("sar rbx,cl", "sar", MCReg(reg("rbx")), MCReg(reg("cl"))),
    ("rol ebx,cl", "rol", MCReg(reg("ebx")), MCReg(reg("cl"))),
    ("rol rbx,cl", "rol", MCReg(reg("rbx")), MCReg(reg("cl"))),
    ("ror ebx,cl", "ror", MCReg(reg("ebx")), MCReg(reg("cl"))),
    ("ror rbx,cl", "ror", MCReg(reg("rbx")), MCReg(reg("cl"))),
    ("movsx rax,bl", "movsx", MCReg(reg("rax")), MCReg(reg("bl"))),
    ("movsx rax,bx", "movsx", MCReg(reg("rax")), MCReg(reg("bx"))),
    ("movss xmm0,xmm1", "movss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("movsd xmm0,xmm1", "movsd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("addss xmm0,xmm1", "addss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("addsd xmm0,xmm1", "addsd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("subss xmm0,xmm1", "subss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("subsd xmm0,xmm1", "subsd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("mulss xmm0,xmm1", "mulss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("mulsd xmm0,xmm1", "mulsd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("divss xmm0,xmm1", "divss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("divsd xmm0,xmm1", "divsd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("roundss xmm0,xmm1,0x1", "roundss", MCReg(reg("xmm0")), MCReg(reg("xmm1")),
     MCImm(1, 8, signed=False)),
    ("roundsd xmm0,xmm1,0x1", "roundsd", MCReg(reg("xmm0")), MCReg(reg("xmm1")),
     MCImm(1, 8, signed=False)),
    ("roundsd xmm0,xmm1,0x4", "roundsd", MCReg(reg("xmm0")), MCReg(reg("xmm1")),
     MCImm(4, 8, signed=False)),
    ("maxss xmm0,xmm1", "maxss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("maxsd xmm0,xmm1", "maxsd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("minss xmm0,xmm1", "minss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("minsd xmm0,xmm1", "minsd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("andps xmm0,xmm1", "andps", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("andpd xmm0,xmm1", "andpd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("andps xmm0,XMMWORD PTR [rbx]", "andps", MCReg(reg("xmm0")),
     MCMem(base=reg("rbx"), size_bits=128)),
    ("andpd xmm0,XMMWORD PTR [rbx]", "andpd", MCReg(reg("xmm0")),
     MCMem(base=reg("rbx"), size_bits=128)),
    ("cvtss2sd xmm0,xmm1", "cvtss2sd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("ucomiss xmm0,xmm1", "ucomiss", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("ucomisd xmm0,xmm1", "ucomisd", MCReg(reg("xmm0")), MCReg(reg("xmm1"))),
    ("movss DWORD PTR [rbx],xmm0", "movss", MCMem(base=reg("rbx")),
     MCReg(reg("xmm0"))),
    ("movsd QWORD PTR [rbx],xmm0", "movsd", MCMem(base=reg("rbx")),
     MCReg(reg("xmm0"))),
    ("setp al", "setp", MCReg(reg("al"))),
    ("setnp al", "setnp", MCReg(reg("al"))),
    ("jmp", "jmp", MCSymRef(SymExpr(MCSymbol("there")))),
    ("je", "je", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jne", "jne", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jl", "jl", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jle", "jle", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jg", "jg", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jge", "jge", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jb", "jb", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jno", "jno", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jbe", "jbe", MCSymRef(SymExpr(MCSymbol("there")))),
    ("ja", "ja", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jae", "jae", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jnp", "jnp", MCSymRef(SymExpr(MCSymbol("there")))),
]


def disassemble(data: bytes) -> str:
    """Disassemble one instruction with an external tool."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        result = subprocess.run([OBJDUMP, "-D", "-b", "binary", "-m", "i386:x86-64",
                                 "-M", "intel", path],
                                capture_output=True, text=True, check=True)
    finally:
        Path(path).unlink()
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].strip():
            return " ".join(parts[2].split())
    return ""


@pytest.mark.objdump
@pytest.mark.skipif(not shutil.which(OBJDUMP), reason="objdump is not installed")
@pytest.mark.parametrize(("expected", "mnemonic", "operands"),
                         [(s[0], s[1], s[2:]) for s in SAMPLES],
                         ids=[s[0].replace(" ", "_") for s in SAMPLES])
def test_disassembles_as_declared(expected: str, mnemonic: str,
                                  operands: tuple) -> None:
    """An external disassembler reads back the instruction we claimed to write."""
    assert disassemble(assemble(mnemonic, *operands)).startswith(expected)


@pytest.mark.objdump
@pytest.mark.skipif(not shutil.which(OBJDUMP), reason="objdump is not installed")
def test_every_table_row_is_covered() -> None:
    """Each row of the table has at least one sample that exercises it."""
    covered = set()
    for _, mnemonic, *operands in SAMPLES:
        covered.add(id(TABLE.select(mnemonic, operands)))  # type: ignore[arg-type]
    missing = [r.mnemonic for r in TABLE.rows() if id(r) not in covered]
    assert missing == [], "".join(("rows with no sample: ", ", ".join(missing)))


def test_shortest_encoding_is_chosen() -> None:
    """The assembler picks the smallest form without being told to."""
    short = assemble("mov", MCReg(reg("rax")), MCImm(5, 32))
    long = assemble("mov", MCReg(reg("rax")), MCImm(5, 64))
    assert len(short) < len(long)


def test_high_byte_register_with_rex_is_refused() -> None:
    """No instruction carrying a REX prefix may name ah, ch, dh or bh.

    'spl' is only nameable at all with a REX prefix, so pairing the two asks for
    an encoding that cannot exist.
    """
    with pytest.raises(EncodingError, match="REX"):
        assemble("mov", MCReg(reg("spl")), MCReg(reg("ah")))


def test_low_byte_registers_force_an_empty_rex() -> None:
    """spl, bpl, sil and dil are nameable only with a REX prefix present."""
    assert assemble("mov", MCReg(reg("dil")), MCReg(reg("sil"))).hex(" ") == "40 88 f7"
    assert assemble("mov", MCReg(reg("al")), MCReg(reg("bl"))).hex(" ") == "88 d8"


def test_stack_pointer_as_index_is_refused() -> None:
    """That encoding means 'no index', so it cannot name rsp."""
    with pytest.raises(EncodingError, match="index"):
        assemble("mov", MCReg(reg("rax")),
                 MCMem(base=reg("rbx"), index=reg("rsp")))


def test_extended_registers_are_refused_for_now() -> None:
    """The extended registers exist in the model but need prefixes we do not emit."""
    with pytest.raises(EncodingError, match="extended"):
        assemble("mov", MCReg(reg("r16d")), MCReg(reg("eax")))


def test_virtual_register_is_refused() -> None:
    """Refusing one here is the contract the register allocator must satisfy."""
    operands = (MCReg(VirtReg(3, GPR, 32)), MCReg(reg("eax")))
    with pytest.raises(EncodingError, match="virtual"):
        encode(MCInst(TABLE.select("mov", operands), operands))


def test_no_encoding_reports_the_operands() -> None:
    """A shape the table does not accept says so, naming what was asked for."""
    with pytest.raises(SelectionError, match="mov"):
        assemble("mov", MCReg(reg("eax")), MCReg(reg("rax")))


def test_register_views_share_storage() -> None:
    """Two registers interfere when they are views of overlapping bytes."""
    assert interferes(reg("rax"), reg("eax"))
    assert interferes(reg("eax"), reg("ah"))
    assert not interferes(reg("rax"), reg("rbx"))
    assert interferes(reg("zmm0"), reg("xmm0"))
    assert not interferes(reg("xmm0"), reg("xmm1"))
