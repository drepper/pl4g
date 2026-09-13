"""The RISC-V encoder.

As on the other architectures, the important test is the differential one: every
row of the table is encoded and compared with what the GNU assembler produces
for the same instruction.  The jump relocation gets its own tests, because the
architecture scatters its bits across the word in four pieces and reassembling
them is the only thing here that could plausibly be wrong.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import ARCH_TOOLS
from pypl4g.mc.desc import InstrTable, SelectionError
from pypl4g.mc.fixup import FixupRangeError, MCFixup
from pypl4g.mc.fixedwidth import EncodingError
from pypl4g.mc.inst import MCInst
from pypl4g.mc.operand import MCImm, MCReg, MCSymRef, SymExpr
from pypl4g.mc.reg import VirtReg
from pypl4g.mc.symbol import MCSymbol
from pypl4g.target.riscv64 import fixups
from pypl4g.target.riscv64.encoder import encode
from pypl4g.target.riscv64.opcodes import (INSTRUCTION_SIZE, RISCV_INSTRS,
                                           UNDEFINED_WORD)
from pypl4g.target.riscv64.regs import GPR, reg

TABLE = InstrTable(RISCV_INSTRS)
OBJDUMP = ARCH_TOOLS["riscv64"]["objdump"]
ASSEMBLER = "/usr/bin/riscv64-linux-gnu-as"

#: What the compiler emits for.  The base integer set plus the multiply and
#: divide extension, which every Linux-capable RISC-V implementation has and
#: which the standard sixty-four bit Linux ABI requires; multiplying without it
#: would mean calling a routine, and there is nothing to call yet.  The
#: compressed encoding is deliberately not included: nothing emits it, and the
#: header flag that would announce it stays clear.
MARCH = "rv64im"


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
    ("li a0, 0", "li", MCReg(reg("a0")), MCImm(0, 12)),
    ("li a7, 94", "li", MCReg(reg("a7")), MCImm(94, 12)),
    ("li a0, -1", "li", MCReg(reg("a0")), MCImm(-1, 12)),
    ("mv a0, a1", "mv", MCReg(reg("a0")), MCReg(reg("a1"))),
    ("addi a0, a1, 5", "addi", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(5, 12)),
    ("addiw a0, a1, 5", "addiw", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(5, 12)),
    ("add a0, a1, a2", "add", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("addw a0, a1, a2", "addw", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("sub a0, a1, a2", "sub", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("subw a0, a1, a2", "subw", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("xor a0, a1, a2", "xor", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("lui a0, 0", "lui", MCReg(reg("a0")), MCImm(0, 20, False)),
    ("auipc a0, 0", "auipc", MCReg(reg("a0")), MCImm(0, 20, False)),
    ("auipc a0, 0", "auipc.hi20", MCReg(reg("a0")), MCSymRef(SymExpr(MCSymbol("s")))),
    ("addi a0, a0, 0", "addi.lo12", MCReg(reg("a0")), MCReg(reg("a0")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("lbu a0, 0(a1)", "lbu", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(0, 12)),
    ("lb a0, 0(a1)", "lb", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(0, 12)),
    ("lhu a0, 0(a1)", "lhu", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(0, 12)),
    ("lh a0, 0(a1)", "lh", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(0, 12)),
    ("lwu a0, 0(a1)", "lwu", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(0, 12)),
    ("lw a0, 0(a1)", "lw", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(0, 12)),
    ("ld a0, 8(a1)", "ld", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(8, 12)),
    ("sb a0, 0(a1)", "sb", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(0, 12)),
    ("sh a0, 4(a1)", "sh", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(4, 12)),
    ("sw a0, 8(a1)", "sw", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(8, 12)),
    ("sd a0, -8(a1)", "sd", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(-8, 12)),
    ("and a0, a1, a2", "and", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("or a0, a1, a2", "or", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("not a0, a1", "not", MCReg(reg("a0")), MCReg(reg("a1"))),
    ("slt a0, a1, a2", "slt", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("sltu a0, a1, a2", "sltu", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("sltiu a0, a1, 1", "sltiu", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(1, 12)),
    ("xori a0, a1, 1", "xori", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(1, 12)),
    ("slli a0, a1, 32", "slli", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(32, 6)),
    ("mul a0, a1, a2", "mul", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("mulh a0, a1, a2", "mulh", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("mulhu a0, a1, a2", "mulhu", MCReg(reg("a0")), MCReg(reg("a1")), MCReg(reg("a2"))),
    ("j .", "j", MCSymRef(SymExpr(MCSymbol("s")))),
    ("beq a0, a1, .", "beq", MCReg(reg("a0")), MCReg(reg("a1")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("bne a0, a1, .", "bne", MCReg(reg("a0")), MCReg(reg("a1")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("blt a0, a1, .", "blt", MCReg(reg("a0")), MCReg(reg("a1")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("bge a0, a1, .", "bge", MCReg(reg("a0")), MCReg(reg("a1")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("bltu a0, a1, .", "bltu", MCReg(reg("a0")), MCReg(reg("a1")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("bgeu a0, a1, .", "bgeu", MCReg(reg("a0")), MCReg(reg("a1")),
     MCSymRef(SymExpr(MCSymbol("s")))),
    ("jal ra, .", "jal", MCSymRef(SymExpr(MCSymbol("s")))),
    ("ret", "ret"),
    ("ecall", "ecall"),
    ("ebreak", "ebreak"),
    ("unimp", "unimp"),
    ("nop", "nop"),
]

needs_binutils = pytest.mark.skipif(
    not shutil.which(ASSEMBLER) or not shutil.which(OBJDUMP),
    reason="the RISC-V cross binutils are not installed")


def assemble_externally(source: str) -> list[int]:
    """Assemble *source* with the external assembler and return its words."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        # The compressed extension would give some of these a shorter form; it
        # is switched off here for the same reason the backend does not use it.
        (path / "in.s").write_text("".join(("\t.text\n\t.option norvc\n", source, "\n")),
                                   encoding="utf-8")
        subprocess.run([ASSEMBLER, "-march=" + MARCH, "-o", str(path / "in.o"),
                        str(path / "in.s")], check=True, capture_output=True)
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
    """Our word for an instruction is the word the GNU assembler produces."""
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
    """The base set is fixed width, and the table says so consistently."""
    for _, mnemonic, *operands in SAMPLES:
        assert len(assemble(mnemonic, *operands)) == INSTRUCTION_SIZE
    for row in TABLE.rows():
        assert row.est_size == INSTRUCTION_SIZE


def test_padding_word_is_not_a_valid_instruction() -> None:
    """Falling into padding must trap rather than drift into the next function."""
    assert UNDEFINED_WORD == 0


def test_a_register_has_two_names_and_one_identity() -> None:
    """The number and the role name the same register, not two equal ones."""
    assert reg("a0") is reg("x10")
    assert reg("s0") is reg("fp")
    assert assemble("mv", MCReg(reg("a0")), MCReg(reg("a1"))) == \
        assemble("mv", MCReg(reg("x10")), MCReg(reg("x11")))


def test_immediate_out_of_range_is_refused() -> None:
    """The twelve-bit immediate is signed, and its limits are enforced."""
    assert assemble("li", MCReg(reg("a0")), MCImm(2047, 12))
    assert assemble("li", MCReg(reg("a0")), MCImm(-2048, 12))
    with pytest.raises(SelectionError):
        assemble("li", MCReg(reg("a0")), MCImm(2048, 32))
    with pytest.raises(SelectionError):
        assemble("li", MCReg(reg("a0")), MCImm(-2049, 32))


def test_negative_immediates_are_stored_in_twos_complement() -> None:
    """A negative immediate occupies the field as the hardware reads it."""
    word = word_of(assemble("addi", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(-1, 12)))
    assert (word >> 20) & 0xFFF == 0xFFF


def test_virtual_register_is_refused() -> None:
    """Refusing one here is the contract the register allocator must satisfy."""
    operands = (MCReg(VirtReg(3, GPR, 64)), MCReg(reg("a1")))
    with pytest.raises(EncodingError, match="virtual"):
        encode(MCInst(TABLE.select("mv", operands), operands))


#: Offsets and the word the GNU assembler produces for 'jal ra, offset'.  The
#: architecture keeps the offset in four pieces, so these were taken from the
#: assembler rather than worked out by hand.
JAL_CASES = [(0x100, 0x100000EF), (-4, 0xFFDFF0EF), (-0xA04, 0xDFCFF0EF),
             (0, 0x000000EF)]


@pytest.mark.parametrize(("offset", "expected"), JAL_CASES)
def test_jump_offset_is_reassembled_correctly(offset: int, expected: int) -> None:
    """The four pieces of a jump offset go back together as the hardware wants."""
    data = bytearray(assemble("jal", MCSymRef(SymExpr(MCSymbol("s")))))
    fixup = MCFixup(offset=0, kind=fixups.JAL, target=SymExpr(MCSymbol("s")))
    fixups.apply_fixup(data, 0, fixup, offset)
    assert word_of(bytes(data)) == expected


def test_jump_relocation_leaves_the_rest_of_the_word_alone() -> None:
    """The offset shares its word with the opcode and the link register."""
    data = bytearray(assemble("jal", MCSymRef(SymExpr(MCSymbol("s")))))
    fixup = MCFixup(offset=0, kind=fixups.JAL, target=SymExpr(MCSymbol("s")))
    fixups.apply_fixup(data, 0, fixup, 0x100)
    assert word_of(bytes(data)) & 0xFFF == 0x0EF


def test_jump_offset_out_of_range_is_refused() -> None:
    """A jump that cannot reach is reported, not silently truncated."""
    data = bytearray(assemble("jal", MCSymRef(SymExpr(MCSymbol("s")))))
    fixup = MCFixup(offset=0, kind=fixups.JAL, target=SymExpr(MCSymbol("s")))
    with pytest.raises(FixupRangeError):
        fixups.apply_fixup(data, 0, fixup, 1 << 21)
    with pytest.raises(FixupRangeError):
        fixups.apply_fixup(data, 0, fixup, 1)


def test_the_address_pair_matches_the_linker() -> None:
    """Two instructions compute an address; both words are the linker's own.

    They come from 'auipc a0, %pcrel_hi(target)' followed by
    'addi a0, a0, %pcrel_lo(...)' twelve bytes before the target's page, linked
    with relaxation switched off so the pair survives.
    """
    from pypl4g.mc.fixup import fixup_value
    from pypl4g.mc.operand import ConstExpr

    pc, target = 0x400FFC, 0x411008
    symbol = MCSymRef(SymExpr(MCSymbol("target")))

    high = bytearray(assemble("auipc.hi20", MCReg(reg("a0")), symbol))
    fixup = MCFixup(offset=0, kind=fixups.PCREL_HI20, target=ConstExpr(target))
    fixups.apply_fixup(high, 0, fixup, fixup_value(fixup, pc))
    assert word_of(bytes(high)) == 0x00010517

    low = bytearray(assemble("addi.lo12", MCReg(reg("a0")), MCReg(reg("a0")), symbol))
    paired = MCFixup(offset=0, kind=fixups.PCREL_LO12_I, target=ConstExpr(target),
                     base_adjust=-fixups.PCREL_PAIR_DISTANCE)
    fixups.apply_fixup(low, 0, paired, fixup_value(paired, pc + 4))
    assert word_of(bytes(low)) == 0x00C50513


def test_the_second_half_of_the_pair_measures_from_the_first() -> None:
    """Its own address is four bytes on, which would give a different answer."""
    from pypl4g.mc.fixup import fixup_value
    from pypl4g.mc.operand import ConstExpr

    paired = MCFixup(offset=0, kind=fixups.PCREL_LO12_I, target=ConstExpr(0x1000),
                     base_adjust=-fixups.PCREL_PAIR_DISTANCE)
    alone = MCFixup(offset=0, kind=fixups.PCREL_LO12_I, target=ConstExpr(0x1000))
    assert fixup_value(paired, 0x104) == fixup_value(alone, 0x100)


def test_a_store_offset_is_split_across_two_runs_of_bits() -> None:
    """The architecture puts the low five bits of the offset where a destination
    register would sit, and the rest at the top.  Both halves are one value, so
    the range is the value's: 2047 and -2048 encode, 2048 does not."""
    assert assemble("sb", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(2047, 12))
    assert assemble("sb", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(-2048, 12))
    with pytest.raises(SelectionError):
        assemble("sb", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(2048, 32))
    word = word_of(assemble("sd", MCReg(reg("a0")), MCReg(reg("a1")), MCImm(-8, 12)))
    low, high = (word >> 7) & 0x1F, (word >> 25) & 0x7F
    assert ((high << 5) | low) - (1 << 12) == -8


def test_branch_offset_is_reassembled_correctly() -> None:
    """The conditional branch keeps its offset in four pieces of its own."""
    data = bytearray(4)
    fixup = MCFixup(offset=0, kind=fixups.BRANCH, target=SymExpr(MCSymbol("s")))
    fixups.apply_fixup(data, 0, fixup, -4)
    word = word_of(bytes(data))
    recovered = (((word >> 31) & 1) << 12 | ((word >> 7) & 1) << 11
                 | ((word >> 25) & 0x3F) << 5 | ((word >> 8) & 0xF) << 1)
    assert recovered - (1 << 13) == -4
