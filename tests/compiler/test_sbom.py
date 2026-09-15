"""What the image says it was built from.

Every image carries `.sbom` and `.sbomstr`, and the checks here are of the two
things that make them worth carrying: that a reader can find and parse them
from the file alone, and that the hashes are about what a program *means*
rather than about how it was typed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pytest

import elfcheck
from conftest import ELFLINT, compiler_targets, describe, run_compiler
from pypl4g.sbom import ROW_SIZE, Tag

LAYOUT = """@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let n: u8 = twice(2u8)
    if n = 4u8:
        0
    else:
        1

fn twice(of: u8) \N{RIGHTWARDS ARROW} u8:
    of + of
"""

#: The same program written the other way round in every respect the hashes are
#: meant to see through: braces instead of indentation, semicolons instead of
#: ends of lines, another number of spaces, a comment, and a literal written in
#: another base.
BRACED = """@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6 { let n: u8 = twice(0x2u8) ;
      \N{REFERENCE MARK} a remark, which says nothing about the program
      if n = 4u8 { 0 } else { 1 } }
fn twice(of: u8) \N{RIGHTWARDS ARROW} u8 { of + of }
"""

#: And one that differs in what it does, by one character.
CHANGED = LAYOUT.replace("of + of", "of + 1u8")


@dataclass(frozen=True, slots=True)
class Row:
    """One row of the table, with its strings already looked up."""

    digest: str
    tag: int
    name: str


def _string_at(blob: bytes, offset: int) -> str:
    """The string beginning at *offset* of a string table."""
    return blob[offset:blob.index(b"\0", offset)].decode("utf-8")


def _rows_of(path: Path) -> list[Row]:
    """The table of one image, read the way anything else would read it.

    Through the section header and its link, using nothing the writer knows:
    a bill of materials only a compiler can read is not one.
    """
    data = path.read_bytes()
    image = elfcheck.parse(data)
    table = next(s for s in image.sections if s.name == ".sbom")
    strings = image.sections[table.sh_link]
    assert strings.name == ".sbomstr", "the link must name where the strings are"
    assert table.sh_entsize == ROW_SIZE
    assert table.sh_size % ROW_SIZE == 0
    blob = data[strings.sh_offset:strings.sh_offset + strings.sh_size]
    made = []
    for at in range(table.sh_size // ROW_SIZE):
        digest, tag, name = struct.unpack_from("<III", data,
                                               table.sh_offset + at * ROW_SIZE)
        made.append(Row(_string_at(blob, digest), tag, _string_at(blob, name)))
    return made


def _built(tmp_path: Path, triple: str, source: str, name: str = "t") -> Path:
    """Compile *source* for *triple* and answer with the image."""
    written = tmp_path / "".join((name, ".pl4g"))
    written.write_text(source, encoding="utf-8")
    out = tmp_path / name
    proc = run_compiler([str(written), "".join(("--target=", triple)),
                         "-o", str(out)])
    assert proc.returncode == 0, describe(proc)
    return out


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_table_is_there_and_says_what_went_in(triple: str, tmp_path: Path) -> None:
    """Every kind of row, in the order the format puts them."""
    rows = _rows_of(_built(tmp_path, triple, LAYOUT))
    assert [r.tag for r in rows] == [Tag.COMPILER, Tag.SOURCE, Tag.SOURCES,
                                     Tag.FUNCTION, Tag.FUNCTION]
    assert rows[0].name.startswith("pypl4g ")
    assert rows[1].name.endswith("t.pl4g")
    assert [r.name for r in rows if r.tag == Tag.FUNCTION] == ["main", "twice"]
    assert all(len(r.digest) == 64 for r in rows), "SHA-256, written out in full"
    assert all(set(r.digest) <= set("0123456789abcdef") for r in rows)


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_sections_are_loaded_and_read_only(triple: str, tmp_path: Path) -> None:
    """Loaded so a program can read its own, read-only so nothing rewrites it.

    The strings are loaded with the table because the table holds offsets into
    them: one without the other is a table a running program cannot read.
    """
    image = elfcheck.parse(_built(tmp_path, triple, LAYOUT).read_bytes())
    for name, kind in ((".sbom", elfcheck.SHT_PROGBITS),
                       (".sbomstr", elfcheck.SHT_STRTAB)):
        section = next(s for s in image.sections if s.name == name)
        assert section.sh_type == kind
        assert section.sh_flags == elfcheck.SHF_ALLOC, \
            "".join((name, " must be loaded, and neither written nor executed"))
        assert section.sh_addr != 0


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_image_still_passes_elflint(triple: str, tmp_path: Path) -> None:
    """A loaded string table is unusual enough to be worth asking about."""
    import shutil
    import subprocess

    if shutil.which(ELFLINT) is None:
        pytest.skip("".join((ELFLINT, " is not installed")))
    path = _built(tmp_path, triple, LAYOUT)
    proc = subprocess.run([ELFLINT, "--strict", str(path)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, describe(proc)


def test_layout_and_braces_hash_alike(tmp_path: Path) -> None:
    """The point of hashing tokens rather than text.

    Two files that mean the same thing have the same hashes, whether the blocks
    were written with indentation or with braces, whatever the spacing, whether
    a comment stands in one of them, and whatever base a number was written in.
    """
    triple = compiler_targets()[0]
    one = {r.name: r.digest for r in _rows_of(_built(tmp_path, triple, LAYOUT, "a"))
           if r.tag == Tag.FUNCTION}
    other = {r.name: r.digest for r in _rows_of(_built(tmp_path, triple, BRACED, "b"))
             if r.tag == Tag.FUNCTION}
    assert one == other


def test_a_changed_definition_changes_its_own_hash(tmp_path: Path) -> None:
    """And leaves the others alone, which is what makes a row worth having.

    The hash of the whole moves too, because the whole is what changed.
    """
    triple = compiler_targets()[0]
    before = _rows_of(_built(tmp_path, triple, LAYOUT, "a"))
    after = _rows_of(_built(tmp_path, triple, CHANGED, "b"))
    named = {(r.tag, r.name): r.digest for r in before}
    later = {(r.tag, r.name): r.digest for r in after}
    assert named[(Tag.FUNCTION, "main")] == later[(Tag.FUNCTION, "main")]
    assert named[(Tag.FUNCTION, "twice")] != later[(Tag.FUNCTION, "twice")]
    assert named[(Tag.COMPILER, before[0].name)] == later[(Tag.COMPILER, before[0].name)]
    assert [r.digest for r in before if r.tag == Tag.SOURCES] \
        != [r.digest for r in after if r.tag == Tag.SOURCES]


def test_the_hash_of_the_whole_is_the_hash_of_the_parts(tmp_path: Path) -> None:
    """Said without reading the sources again, which is what it is for."""
    from pypl4g.sbom import digest_of_digests

    triple = compiler_targets()[0]
    rows = _rows_of(_built(tmp_path, triple, LAYOUT))
    sources = [r.digest for r in rows if r.tag == Tag.SOURCE]
    whole = next(r.digest for r in rows if r.tag == Tag.SOURCES)
    assert whole == digest_of_digests(sources)
