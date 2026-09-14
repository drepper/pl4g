"""The AArch64 encoder.

As on the other architecture, the important test is the differential one: every
row of the table is encoded and handed to an external disassembler, which must
read back the instruction the table claimed to describe.  Here the assembler is
also available, so the templates themselves are checked against it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import ARCH_TOOLS
from pypl4g.mc.desc import InstrTable, SelectionError
from pypl4g.mc.fixup import FixupRangeError, MCFixup
from pypl4g.mc.inst import MCInst
from pypl4g.mc.operand import MCImm, MCReg, MCSymRef, SymExpr
from pypl4g.mc.reg import VirtReg, interferes
from pypl4g.mc.symbol import MCSymbol
from pypl4g.target.aarch64 import fixups
from pypl4g.target.aarch64.encoder import EncodingError, encode
from pypl4g.target.aarch64.opcodes import AARCH64_INSTRS, INSTRUCTION_SIZE, UDF_WORD
from pypl4g.target.aarch64.regs import GPR, SP, XZR, reg

TABLE = InstrTable(AARCH64_INSTRS)
TOOLS = ARCH_TOOLS["aarch64"]
OBJDUMP = TOOLS["objdump"]
ASSEMBLER = "/usr/bin/aarch64-linux-gnu-as"


def assemble(mnemonic: str, *operands: object) -> bytes:
    """Select and encode one instruction."""
    desc = TABLE.select(mnemonic, operands)  # type: ignore[arg-type]
    return encode(MCInst(desc, operands))[0]  # type: ignore[arg-type]


def word_of(data: bytes) -> int:
    """The instruction word the bytes hold."""
    return int.from_bytes(data, "little")


#: One legal operand tuple per table row, paired with the way the external
#: assembler spells it.  Every row must appear.
SAMPLES = [
    ("mov w0, #0", "movz", MCReg(reg("w0")), MCImm(0, 16, False)),
    ("mov x8, #94", "movz", MCReg(reg("x8")), MCImm(94, 16, False)),
    ("mov w0, w1", "mov", MCReg(reg("w0")), MCReg(reg("w1"))),
    ("mov x29, xzr", "mov", MCReg(reg("x29")), MCReg(reg("xzr"))),
    ("add w0, w1, w2", "add", MCReg(reg("w0")), MCReg(reg("w1")), MCReg(reg("w2"))),
    ("add x0, x1, x2", "add", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("add w0, w1, #5", "add", MCReg(reg("w0")), MCReg(reg("w1")), MCImm(5, 12, False)),
    ("add x0, x1, #5", "add", MCReg(reg("x0")), MCReg(reg("x1")), MCImm(5, 12, False)),
    ("sub w0, w1, w2", "sub", MCReg(reg("w0")), MCReg(reg("w1")), MCReg(reg("w2"))),
    ("sub x0, x1, x2", "sub", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("sub w0, w1, #5", "sub", MCReg(reg("w0")), MCReg(reg("w1")), MCImm(5, 12, False)),
    ("sub x0, x1, #5", "sub", MCReg(reg("x0")), MCReg(reg("x1")), MCImm(5, 12, False)),
    ("eor w0, w1, w2", "eor", MCReg(reg("w0")), MCReg(reg("w1")), MCReg(reg("w2"))),
    ("eor x0, x1, x2", "eor", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("cmp w1, w2", "cmp", MCReg(reg("w1")), MCReg(reg("w2"))),
    ("cmp x1, x2", "cmp", MCReg(reg("x1")), MCReg(reg("x2"))),
    ("adrp x0, .", "adrp", MCReg(reg("x0")), MCSymRef(SymExpr(MCSymbol("s")))),
    ("add x0, x1, #0", "add.lo12", MCReg(reg("x0")), MCReg(reg("x1")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("ldrb w0, [x1]", "ldrb", MCReg(reg("w0")), MCReg(reg("x1")), MCImm(0, 12, False)),
    ("ldrb w0, [x1, #3]", "ldrb", MCReg(reg("w0")), MCReg(reg("x1")),
     MCImm(3, 12, False)),
    ("ldrsb w0, [x1]", "ldrsb", MCReg(reg("w0")), MCReg(reg("x1")),
     MCImm(0, 12, False)),
    ("ldrh w0, [x1, #4]", "ldrh", MCReg(reg("w0")), MCReg(reg("x1")),
     MCImm(4, 12, False)),
    ("ldrsh w0, [x1]", "ldrsh", MCReg(reg("w0")), MCReg(reg("x1")),
     MCImm(0, 12, False)),
    ("ldr w0, [x1, #8]", "ldr", MCReg(reg("w0")), MCReg(reg("x1")),
     MCImm(8, 12, False)),
    ("ldrsw x0, [x1]", "ldrsw", MCReg(reg("x0")), MCReg(reg("x1")),
     MCImm(0, 12, False)),
    ("ldr x0, [x1, #16]", "ldr", MCReg(reg("x0")), MCReg(reg("x1")),
     MCImm(16, 12, False)),
    ("strb w0, [x1]", "strb", MCReg(reg("w0")), MCReg(reg("x1")), MCImm(0, 12, False)),
    ("strh w0, [x1, #4]", "strh", MCReg(reg("w0")), MCReg(reg("x1")),
     MCImm(4, 12, False)),
    ("str w0, [x1, #8]", "str", MCReg(reg("w0")), MCReg(reg("x1")),
     MCImm(8, 12, False)),
    ("str x0, [x1, #16]", "str", MCReg(reg("x0")), MCReg(reg("x1")),
     MCImm(16, 12, False)),
    ("and w0, w1, w2", "and", MCReg(reg("w0")), MCReg(reg("w1")), MCReg(reg("w2"))),
    ("and x0, x1, x2", "and", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("orr w0, w1, w2", "orr", MCReg(reg("w0")), MCReg(reg("w1")), MCReg(reg("w2"))),
    ("orr x0, x1, x2", "orr", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("mvn w0, w1", "mvn", MCReg(reg("w0")), MCReg(reg("w1"))),
    ("mvn x0, x1", "mvn", MCReg(reg("x0")), MCReg(reg("x1"))),
    ("cset w0, eq", "cset.eq", MCReg(reg("w0"))),
    ("cset w0, ne", "cset.ne", MCReg(reg("w0"))),
    ("cset w0, lt", "cset.lt", MCReg(reg("w0"))),
    ("cset w0, le", "cset.le", MCReg(reg("w0"))),
    ("cset w0, gt", "cset.gt", MCReg(reg("w0"))),
    ("cset w0, ge", "cset.ge", MCReg(reg("w0"))),
    ("cset w0, lo", "cset.lo", MCReg(reg("w0"))),
    ("cset w0, ls", "cset.ls", MCReg(reg("w0"))),
    ("cset w0, hi", "cset.hi", MCReg(reg("w0"))),
    ("cset w0, hs", "cset.hs", MCReg(reg("w0"))),
    ("movz x0, 1, lsl 16", "movz", MCReg(reg("x0")), MCImm(1, 16, False),
     MCImm(16, 8, False)),
    ("movk x0, 0xffff, lsl 48", "movk", MCReg(reg("x0")), MCImm(0xFFFF, 16, False),
     MCImm(48, 8, False)),
    ("movn x0, 0, lsl 0", "movn", MCReg(reg("x0")), MCImm(0, 16, False),
     MCImm(0, 8, False)),
    ("mul w0, w1, w2", "mul", MCReg(reg("w0")), MCReg(reg("w1")), MCReg(reg("w2"))),
    ("mul x0, x1, x2", "mul", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("umulh x0, x1, x2", "umulh", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("smulh x0, x1, x2", "smulh", MCReg(reg("x0")), MCReg(reg("x1")), MCReg(reg("x2"))),
    ("csel x0, x1, x2, eq", "csel.eq", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, ne", "csel.ne", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, lt", "csel.lt", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, le", "csel.le", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, gt", "csel.gt", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, ge", "csel.ge", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, lo", "csel.lo", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, ls", "csel.ls", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, hi", "csel.hi", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("csel x0, x1, x2, hs", "csel.hs", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("sxtw x0, w1", "sxtw", MCReg(reg("x0")), MCReg(reg("w1"))),
    ("sdiv w0, w1, w2", "sdiv", MCReg(reg("w0")), MCReg(reg("w1")),
     MCReg(reg("w2"))),
    ("sdiv x0, x1, x2", "sdiv", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("udiv w0, w1, w2", "udiv", MCReg(reg("w0")), MCReg(reg("w1")),
     MCReg(reg("w2"))),
    ("udiv x0, x1, x2", "udiv", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("msub w0, w1, w2, w3", "msub", MCReg(reg("w0")), MCReg(reg("w1")),
     MCReg(reg("w2")), MCReg(reg("w3"))),
    ("msub x0, x1, x2, x3", "msub", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2")), MCReg(reg("x3"))),
    ("lslv w0, w1, w2", "lslv", MCReg(reg("w0")), MCReg(reg("w1")),
     MCReg(reg("w2"))),
    ("lslv x0, x1, x2", "lslv", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("lsrv w0, w1, w2", "lsrv", MCReg(reg("w0")), MCReg(reg("w1")),
     MCReg(reg("w2"))),
    ("lsrv x0, x1, x2", "lsrv", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("asrv w0, w1, w2", "asrv", MCReg(reg("w0")), MCReg(reg("w1")),
     MCReg(reg("w2"))),
    ("asrv x0, x1, x2", "asrv", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("rorv w0, w1, w2", "rorv", MCReg(reg("w0")), MCReg(reg("w1")),
     MCReg(reg("w2"))),
    ("rorv x0, x1, x2", "rorv", MCReg(reg("x0")), MCReg(reg("x1")),
     MCReg(reg("x2"))),
    ("sxtb x0, w1", "sxtb", MCReg(reg("x0")), MCReg(reg("w1"))),
    ("sxth x0, w1", "sxth", MCReg(reg("x0")), MCReg(reg("w1"))),
    ("fmov s0, s1", "fmov", MCReg(reg("s0")), MCReg(reg("s1"))),
    ("fmov d0, d1", "fmov", MCReg(reg("d0")), MCReg(reg("d1"))),
    ("fabs s0, s1", "fabs", MCReg(reg("s0")), MCReg(reg("s1"))),
    ("fabs d0, d1", "fabs", MCReg(reg("d0")), MCReg(reg("d1"))),
    ("fcvt d0, s1", "fcvt", MCReg(reg("d0")), MCReg(reg("s1"))),
    ("fadd s0, s1, s2", "fadd", MCReg(reg("s0")), MCReg(reg("s1")),
     MCReg(reg("s2"))),
    ("fadd d0, d1, d2", "fadd", MCReg(reg("d0")), MCReg(reg("d1")),
     MCReg(reg("d2"))),
    ("fsub s0, s1, s2", "fsub", MCReg(reg("s0")), MCReg(reg("s1")),
     MCReg(reg("s2"))),
    ("fsub d0, d1, d2", "fsub", MCReg(reg("d0")), MCReg(reg("d1")),
     MCReg(reg("d2"))),
    ("fmul s0, s1, s2", "fmul", MCReg(reg("s0")), MCReg(reg("s1")),
     MCReg(reg("s2"))),
    ("fmul d0, d1, d2", "fmul", MCReg(reg("d0")), MCReg(reg("d1")),
     MCReg(reg("d2"))),
    ("fdiv s0, s1, s2", "fdiv", MCReg(reg("s0")), MCReg(reg("s1")),
     MCReg(reg("s2"))),
    ("fdiv d0, d1, d2", "fdiv", MCReg(reg("d0")), MCReg(reg("d1")),
     MCReg(reg("d2"))),
    ("fcmp s0, s1", "fcmp", MCReg(reg("s0")), MCReg(reg("s1"))),
    ("fcmp d0, d1", "fcmp", MCReg(reg("d0")), MCReg(reg("d1"))),
    ("ldr s0, [x1, 4]", "ldr", MCReg(reg("s0")), MCReg(reg("x1")),
     MCImm(4, 12, False)),
    ("ldr d0, [x1, 8]", "ldr", MCReg(reg("d0")), MCReg(reg("x1")),
     MCImm(8, 12, False)),
    ("str s0, [x1, 4]", "str", MCReg(reg("s0")), MCReg(reg("x1")),
     MCImm(4, 12, False)),
    ("str d0, [x1, 8]", "str", MCReg(reg("d0")), MCReg(reg("x1")),
     MCImm(8, 12, False)),
    ("b .", "b", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.eq .", "b.eq", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.ne .", "b.ne", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.hs .", "b.hs", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.lo .", "b.lo", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.hi .", "b.hi", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.ls .", "b.ls", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.ge .", "b.ge", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.lt .", "b.lt", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.gt .", "b.gt", MCSymRef(SymExpr(MCSymbol("s")))),
    ("b.le .", "b.le", MCSymRef(SymExpr(MCSymbol("s")))),
    ("cbnz w0, .", "cbnz", MCReg(reg("w0")), MCSymRef(SymExpr(MCSymbol("s")))),
    ("cbnz x1, .", "cbnz", MCReg(reg("x1")), MCSymRef(SymExpr(MCSymbol("s")))),
    ("cbz w0, .", "cbz", MCReg(reg("w0")), MCSymRef(SymExpr(MCSymbol("s")))),
    ("cbz x1, .", "cbz", MCReg(reg("x1")), MCSymRef(SymExpr(MCSymbol("s")))),
    ("cmp w3, #16", "cmp", MCReg(reg("w3")), MCImm(16, 12, False)),
    ("cmp x3, #16", "cmp", MCReg(reg("x3")), MCImm(16, 12, False)),
    ("bl .", "bl", MCSymRef(SymExpr(MCSymbol("s")))),
    ("ret", "ret"),
    ("svc #0", "svc", MCImm(0, 16, False)),
    ("brk #1", "brk", MCImm(1, 16, False)),
    ("udf #0", "udf", MCImm(0, 16, False)),
    ("nop", "nop"),
]

needs_binutils = pytest.mark.skipif(
    not shutil.which(ASSEMBLER) or not shutil.which(OBJDUMP),
    reason="the AArch64 cross binutils are not installed")


def assemble_externally(source: str) -> list[int]:
    """Assemble *source* with the external assembler and return its words."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / "in.s").write_text("".join(("\t.text\n", source, "\n")), encoding="utf-8")
        subprocess.run([ASSEMBLER, "-o", str(path / "in.o"), str(path / "in.s")],
                       check=True, capture_output=True)
        objcopy = OBJDUMP.replace("objdump", "objcopy")
        subprocess.run([objcopy, "-O", "binary", "--only-section=.text",
                        str(path / "in.o"), str(path / "in.bin")],
                       check=True, capture_output=True)
        data = (path / "in.bin").read_bytes()
    return [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data), 4)]


@needs_binutils
@pytest.mark.parametrize(("spelling", "mnemonic", "operands"),
                         [(s[0], s[1], s[2:]) for s in SAMPLES],
                         ids=[s[0].replace(" ", "_").replace(",", "") for s in SAMPLES])
def test_matches_the_external_assembler(spelling: str, mnemonic: str,
                                        operands: tuple) -> None:
    """Our word for an instruction is the word the GNU assembler produces.

    The relocated fields are zero in both, since neither has resolved a symbol.
    """
    assert word_of(assemble(mnemonic, *operands)) == assemble_externally(spelling)[0]


@needs_binutils
def test_every_table_row_has_a_sample() -> None:
    """No row goes unchecked."""
    covered = set()
    for _, mnemonic, *operands in SAMPLES:
        covered.add(id(TABLE.select(mnemonic, operands)))  # type: ignore[arg-type]
    missing = [r.mnemonic for r in TABLE.rows() if id(r) not in covered]
    assert missing == [], "".join(("rows with no sample: ", ", ".join(missing)))


def test_every_instruction_is_one_word() -> None:
    """The architecture is fixed width, and the table says so consistently."""
    for _, mnemonic, *operands in SAMPLES:
        assert len(assemble(mnemonic, *operands)) == INSTRUCTION_SIZE
    for row in TABLE.rows():
        assert row.est_size == INSTRUCTION_SIZE


def test_padding_word_is_permanently_undefined() -> None:
    """Falling into padding must trap rather than drift into the next function."""
    assert UDF_WORD == 0


def test_a_load_offset_must_be_a_multiple_of_the_access_size() -> None:
    """The encoding holds the offset divided by the size, so an odd one has none."""
    assert assemble("ldrh", MCReg(reg("w0")), MCReg(reg("x1")), MCImm(4, 12, False))
    with pytest.raises(EncodingError, match="does not fit"):
        assemble("ldrh", MCReg(reg("w0")), MCReg(reg("x1")), MCImm(3, 12, False))
    with pytest.raises(EncodingError, match="does not fit"):
        assemble("ldr", MCReg(reg("x0")), MCReg(reg("x1")), MCImm(4, 12, False))


def test_immediate_out_of_range_is_refused() -> None:
    """An immediate the field cannot hold has no encoding, so none is invented."""
    with pytest.raises(SelectionError):
        assemble("movz", MCReg(reg("w0")), MCImm(0x10000, 32, False))
    with pytest.raises(SelectionError):
        assemble("add", MCReg(reg("x0")), MCReg(reg("x1")), MCImm(0x1000, 32, False))


def test_mixed_widths_are_refused() -> None:
    """An instruction operates on one width; there is no implicit conversion."""
    with pytest.raises(SelectionError):
        assemble("mov", MCReg(reg("x0")), MCReg(reg("w1")))


def test_virtual_register_is_refused() -> None:
    """Refusing one here is the contract the register allocator must satisfy."""
    operands = (MCReg(VirtReg(3, GPR, 64)), MCReg(reg("x1")))
    with pytest.raises(EncodingError, match="virtual"):
        encode(MCInst(TABLE.select("mov", operands), operands))


def test_zero_register_and_stack_pointer_do_not_interfere() -> None:
    """They share a number but not a meaning, and the model keeps them apart."""
    assert XZR.enc == SP.enc
    assert not interferes(XZR, SP)
    assert interferes(reg("x0"), reg("w0"))


def test_branch_offset_is_stored_in_units_of_four_bytes() -> None:
    """A branch drops the two bits every instruction address has as zero."""
    data = bytearray(assemble("bl", MCSymRef(SymExpr(MCSymbol("s")))))
    fixup = MCFixup(offset=0, kind=fixups.BRANCH26, target=SymExpr(MCSymbol("s")))
    fixups.apply_fixup(data, 0, fixup, -24)
    assert word_of(bytes(data)) == 0x97FFFFFA


def test_branch_offset_out_of_range_is_refused() -> None:
    """A branch that cannot reach is reported, not silently truncated."""
    data = bytearray(assemble("bl", MCSymRef(SymExpr(MCSymbol("s")))))
    fixup = MCFixup(offset=0, kind=fixups.BRANCH26, target=SymExpr(MCSymbol("s")))
    with pytest.raises(FixupRangeError):
        fixups.apply_fixup(data, 0, fixup, 1 << 28)
    with pytest.raises(FixupRangeError, match="does not fit"):
        fixups.apply_fixup(data, 0, fixup, 2)


def test_the_page_relocation_takes_the_difference_of_pages() -> None:
    """An address a page away is a page away even if the two are bytes apart.

    Both words here are what the GNU linker produces for 'adrp x0, target'
    twelve bytes before a target that lies in the next page but one.
    """
    from pypl4g.mc.fixup import fixup_value
    from pypl4g.mc.operand import ConstExpr

    fixup = MCFixup(offset=0, kind=fixups.ADR_PAGE21, target=ConstExpr(0x411008))
    assert fixup_value(fixup, 0x400FFC) == 17 << 12, "the pages, not the addresses"

    data = bytearray(assemble("adrp", MCReg(reg("x0")),
                              MCSymRef(SymExpr(MCSymbol("target")))))
    fixups.apply_fixup(data, 0, fixup, fixup_value(fixup, 0x400FFC))
    assert word_of(bytes(data)) == 0xB0000080


def test_relocations_are_inserted_not_overwritten() -> None:
    """A relocated field shares its word with the opcode and the registers.

    The page difference is not one run of bits: the instruction keeps its low
    two bits apart from the other nineteen.  The expected word here is the one
    the GNU linker produces for 'adrp x3, there' with three pages between them.
    """
    data = bytearray(assemble("adrp", MCReg(reg("x3")),
                              MCSymRef(SymExpr(MCSymbol("s")))))
    fixup = MCFixup(offset=0, kind=fixups.ADR_PAGE21, target=SymExpr(MCSymbol("s")))
    fixups.apply_fixup(data, 0, fixup, 0x3000)
    word = word_of(bytes(data))
    assert word == 0xF0000003
    assert word & 0x1F == 3, "the destination register was overwritten"
    assert (word >> 24) & 0x9F == 0x90, "the opcode was overwritten"
    low, high = (word >> 29) & 3, (word >> 5) & 0x7FFFF
    assert (high << 2) | low == 3, "the page difference was not stored"
