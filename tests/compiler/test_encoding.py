"""The x86-64 encoder.

The differential test is the important one: every row of the table is encoded and
handed to an external disassembler, which must read back the instruction we
claimed to write.  That is what makes it safe to add rows quickly.
"""

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
    ("c3", "ret"),
    ("0f 0b", "ud2"),
    ("e8 00 00 00 00", "call", MCSymRef(SymExpr(MCSymbol("main")))),
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
    ("ud2", "ud2"),
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
    ("jmp", "jmp", MCSymRef(SymExpr(MCSymbol("there")))),
    ("je", "je", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jne", "jne", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jl", "jl", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jle", "jle", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jg", "jg", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jge", "jge", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jb", "jb", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jbe", "jbe", MCSymRef(SymExpr(MCSymbol("there")))),
    ("ja", "ja", MCSymRef(SymExpr(MCSymbol("there")))),
    ("jae", "jae", MCSymRef(SymExpr(MCSymbol("there")))),
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
