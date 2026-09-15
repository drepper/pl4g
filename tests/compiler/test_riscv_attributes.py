"""What a RISC-V image says it was built for.

Two things are checked and they are checked differently.  The **bytes** of the
section are checked against the format, which is small enough to write out; the
**string** in it is checked against what the GNU assembler puts in the same
place for the same request, which is the only way to be sure of a normalized
form whose rules are spread over a specification, a profile document and forty
years of extension names.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import ARCH_TOOLS, describe, run_compiler
from pypl4g.target.riscv64 import attributes, isa

TRIPLE = "riscv64-linux-none"
ASSEMBLER = "/usr/bin/riscv64-linux-gnu-as"
READELF = ARCH_TOOLS["riscv64"]["readelf"]

SOURCE = "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u8:\n    0u8\n"


def test_the_section_is_what_the_format_says() -> None:
    """A byte for the format, a sub-section for the vendor, a sub-sub-section
    for the scope, and the tag inside it -- with every length counting itself,
    which is what lets a reader step over what it does not know."""
    built = attributes.build("rv64i2p1")
    assert built == b"".join((
        b"A",                                  # the one format there is
        (25).to_bytes(4, "little"),            # this sub-section, counting this
        b"riscv\x00",                          # whose attributes these are
        b"\x01",                               # the scope: the whole file
        (15).to_bytes(4, "little"),            # this sub-sub-section, likewise
        b"\x05",                               # Tag_RISCV_arch
        b"rv64i2p1\x00"))
    # And the lengths are the lengths: the whole thing is the format byte plus
    # what the sub-section says it is.
    assert len(built) == 1 + 25


def test_a_longer_string_only_makes_it_longer() -> None:
    """Nothing about the shape depends on what the string says."""
    short = attributes.build("rv64i2p1")
    longer = attributes.build(isa.parse("rva23").normalized())
    # The format byte and the vendor are where they were; only what says how
    # long things are, and the string itself, have moved.
    assert longer[0:1] == short[0:1] == b"A"
    assert longer[5:11] == short[5:11] == b"riscv\x00"
    assert len(longer) - len(short) == \
        len(isa.parse("rva23").normalized()) - len("rv64i2p1")


@pytest.mark.skipif(shutil.which(READELF) is None, reason="no readelf")
def test_an_image_says_what_it_was_built_for(tmp_path: Path) -> None:
    """It is in the image, it is of the architecture's own section type, and it
    takes no room when the program runs."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    built = tmp_path / "out"
    proc = run_compiler(["-o", str(built), "".join(("--target=", TRIPLE)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    shown = subprocess.run([READELF, "-A", str(built)], capture_output=True,
                           text=True, check=True).stdout
    assert 'Tag_RISCV_arch: "' in shown, shown
    assert isa.parse(isa.DEFAULT).normalized() in shown, shown
    headers = subprocess.run([READELF, "-S", str(built)], capture_output=True,
                             text=True, check=True).stdout
    assert ".riscv.attributes" in headers, headers
    # Nothing maps it: it is for whatever reads the file.
    line = next(l for l in headers.splitlines() if ".riscv.attributes" in l)
    assert "RISCV_ATTRIBUTE" in line, line
    assert "0000000000000000" in line, line


@pytest.mark.skipif(shutil.which(READELF) is None, reason="no readelf")
def test_what_it_says_is_what_it_was_asked_for(tmp_path: Path) -> None:
    """The string is the ISA that was asked for and not the default."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    built = tmp_path / "out"
    proc = run_compiler(["-o", str(built), "".join(("--target=", TRIPLE)),
                         "--mclevel=rv64imafd", str(source)])
    assert proc.returncode == 0, describe(proc)
    shown = subprocess.run([READELF, "-A", str(built)], capture_output=True,
                           text=True, check=True).stdout
    assert isa.parse("rv64imafd").normalized() in shown, shown
    assert "zfa" not in shown, shown


#: Strings whose normalized form is compared with the assembler's.  They cover
#: the base on its own, the abbreviations, every implication the table states,
#: and both widths -- since the compiler reads strings for machines it cannot
#: generate for, and refusing one is a different thing from misreading it.
CORPUS = [
    "rv64i", "rv64im", "rv64ia", "rv64imc", "rv64g", "rv64gc", "rv64imafd",
    "rv64iq", "rv64gh", "rv64ib", "rv64gcv",
    "rv64gc_zfa", "rv64gc_zba_zbb_zbs", "rv64gc_zvbb", "rv64gc_zfhmin",
    "rv64gc_zvfhmin", "rv64gc_zvfh", "rv64gc_zcb_zcmop", "rv64gc_zkt_zbc",
    "rv64gc_zicbom_zicboz", "rv64gc_zawrs_za64rs", "rv64im_zba",
    "rv64i_zicsr", "rv64i_zicntr_zihpm", "rv64imafdc_zicond_zimop",
    "rv32i", "rv32imc", "rv32gc", "rv32gcv",
    # The profile, which is the whole point of having profiles: forty-odd
    # extensions under one name, every one of which has to come out right.
    "rva23u64",
]


@pytest.mark.skipif(shutil.which(ASSEMBLER) is None, reason="no assembler")
@pytest.mark.parametrize("written", CORPUS)
def test_the_normalized_form_is_the_one_everyone_writes(tmp_path: Path,
                                                        written: str) -> None:
    """The differential test, as for the instruction encodings.

    A normalized ISA string has to be the *same* string another toolchain
    writes, or the thing it exists for -- a reader comparing two images -- does
    not work.  So it is compared with what the GNU assembler puts in the same
    attribute for the same request.

    `rva23s64` is not here.  The profile document makes Zifencei mandatory for
    it and this assembler's table leaves it out, so the two disagree by one
    extension; the document is what this follows, and the half they disagree
    about is the privileged half, which no program this compiler builds can
    use.
    """
    empty = tmp_path / "t.s"
    empty.write_text(".text\n", encoding="utf-8")
    proc = subprocess.run([ASSEMBLER, "".join(("-march=", written)),
                           "-o", str(tmp_path / "t.o"), str(empty)],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        pytest.skip("".join(("this assembler does not know ", written)))
    shown = subprocess.run([READELF, "-A", str(tmp_path / "t.o")],
                           capture_output=True, text=True, check=True).stdout
    found = re.search(r'Tag_RISCV_arch: "([^"]+)"', shown)
    assert found is not None, shown
    assert isa.parse(written).normalized() == found.group(1)


@pytest.mark.parametrize("written", ["rv32i", "rv32gc", "rva23u32"])
def test_a_narrower_base_is_not_this_target(tmp_path: Path,
                                            written: str) -> None:
    """A machine whose addresses are half as wide is a different target and not
    a different level of this one.  The comparison is against what the target
    says its addresses are, so the same line refuses `rv64` on a thirty-two bit
    target of the same family when there is one."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--target=", TRIPLE)),
                         "".join(("--mclevel=", written)), str(source)])
    assert proc.returncode != 0
    assert "PL4G-1011" in proc.stderr, proc.stderr


def test_the_reduced_base_is_not_this_target(tmp_path: Path) -> None:
    """`rv64e` has sixteen registers and a calling convention of its own, and
    this compiler's register file and convention are the full ones."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--target=", TRIPLE)), "--mclevel=rv64e",
                         str(source)])
    assert proc.returncode != 0
    assert "PL4G-1011" in proc.stderr, proc.stderr
    assert "sixteen registers" in proc.stderr, proc.stderr
