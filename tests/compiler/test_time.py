"""`std.time`: what a program carries for the clocks, and what it does not.

What the two functions answer is a language test (`std-time`, and
`std-time-in-a-shared-object` for the system call); what is here is what the
image holds.  The module's constructor finds the vDSO's clocks and only fills in
two variables, so a program that reads no clock leaves it out with them -- and
with them the table of the auxiliary vector, its reader and the read-only
segment.  A program that reads one has all of it, the two variables in the
pages made read-only once the constructors have run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler

#: What the image calls the constructor, one of the two variables, and the
#: table of the auxiliary vector.
FINDER = "std.time.find()void"
SLOT = "std.time.vdso_gettime"
TABLE = "__pl4g_auxv"

#: What `@[relro]` variables are kept in, and the header saying so.
RELRO = ".pl4g.relro"
PT_GNU_RELRO = 0x6474E552

READS = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup, impure]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    match std.time.clock_gettime(std.time.Clock.monotonic):
        std.time.Timespec(t):
            if t.tv_nsec \N{GREATER-THAN OR EQUAL TO} 0i64: 0u6 else: 1u6
        \N{UP TACK}(_):
            2u6
"""

SILENT = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    0u6
"""


def _built(source: str, where: Path, triple: str) -> elfcheck.Image:
    """Compile *source* for *triple* and answer the image it became."""
    written = where / "one.pl4g"
    written.write_text(source, encoding="utf-8")
    out = where / "one"
    proc = run_compiler(["-o", str(out), "".join(("--target=", triple)), str(written)])
    assert proc.returncode == 0, describe(proc)
    return elfcheck.parse(out.read_bytes())


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_reading_a_clock_finds_the_vdso_first(triple: str,
                                                        tmp_path: Path) -> None:
    """The constructor, the table it reads, and the variables it fills, in the
    pages made read-only after it."""
    image = _built(READS, tmp_path, triple)
    assert image.symbol(FINDER) is not None
    assert image.symbol(TABLE) is not None
    slot = image.symbol(SLOT)
    relro = image.section(RELRO)
    assert slot is not None and relro is not None
    assert relro.sh_addr <= slot.value < relro.sh_addr + relro.sh_size
    assert [one for one in image.segments if one.p_type == PT_GNU_RELRO]


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_reading_no_clock_carries_none_of_it(triple: str,
                                                       tmp_path: Path) -> None:
    """Running the constructor would change nothing it can see, so it is not
    there, and nor is anything only it needed."""
    image = _built(SILENT, tmp_path, triple)
    for name in (FINDER, SLOT, TABLE):
        assert image.symbol(name) is None, name
    assert image.section(RELRO) is None
    assert not [one for one in image.segments if one.p_type == PT_GNU_RELRO]
