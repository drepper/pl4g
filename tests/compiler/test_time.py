"""`std.time`: what a program carries for the clocks, and what it does not.

What the two functions answer is a language test (`std-time`, and
`std-time-in-a-shared-object` for the system call); what is here is what the
image holds.  Where the vDSO's clocks are is found by the initializers of the
module's `constinit` variables, which are kept only where something kept reads
the variables, so a program that reads no clock leaves them out -- and with them
the walk of the vDSO, the table of the auxiliary vector, its reader and the
read-only segment.  A program that reads one has all of it, the variables in
the pages made read-only before the startup function runs.  And a program
reading no clock does not even lower the walk: the module is lowered as far as
it is named, and an initializer is no root of that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler

#: What the image calls the initializer of the variable the walk fills, that
#: variable, one of the two function values, and the table of the auxiliary
#: vector.
FINDER = "__pl4g_constinit.found()void"
FOUND = "std.time.found"
SLOT = "std.time.vdso_gettime"
TABLE = "__pl4g_auxv"

#: What `constinit` variables are kept in, and the header saying so.
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
    """The initializers, the table the walk reads, and the variables they give
    values, in the pages made read-only before the startup function."""
    image = _built(READS, tmp_path, triple)
    assert image.symbol(FINDER) is not None
    assert image.symbol(TABLE) is not None
    relro = image.section(RELRO)
    assert relro is not None
    for name in (SLOT, FOUND):
        slot = image.symbol(name)
        assert slot is not None, name
        assert relro.sh_addr <= slot.value < relro.sh_addr + relro.sh_size, name
    assert [one for one in image.segments if one.p_type == PT_GNU_RELRO]


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_reading_no_clock_carries_none_of_it(triple: str,
                                                       tmp_path: Path) -> None:
    """Nothing kept reads the variables, so their initializers are not there,
    and nor is anything only they needed."""
    image = _built(SILENT, tmp_path, triple)
    for name in (FINDER, FOUND, SLOT, TABLE):
        assert image.symbol(name) is None, name
    assert image.section(RELRO) is None
    assert not [one for one in image.segments if one.p_type == PT_GNU_RELRO]


def test_a_program_reading_no_clock_lowers_none_of_it() -> None:
    """Nor is any of it checked or lowered: an initializer is lowered where its
    variable is named, as a function is, and not because the module has it --
    which is what a constructor finding the clocks used to cost every program
    importing `std`."""
    from pypl4g.diag.engine import collecting_engine
    from pypl4g.front.lexer import tokenize
    from pypl4g.front.parser import parse
    from pypl4g.ir.module import Module
    from pypl4g.sema.check import check
    from pypl4g.sema.modules import ModuleRegistry, SearchPath, system_modules
    from pypl4g.source.manager import SourceManager

    lowered: dict[str, set[str]] = {}
    for name, source in (("silent", SILENT), ("reads", READS)):
        sources = SourceManager()
        engine, collected = collecting_engine(None)
        unit = parse(tokenize(sources.add(Path("".join((name, ".pl4g"))), source),
                              engine), "".join((name, ".pl4g")), engine)
        module = Module(name)
        check(module, [unit], engine,
              ModuleRegistry(search=SearchPath(system=system_modules())), sources)
        assert not [d for d in collected if d.info.severity == "error"], name
        lowered[name] = {f.name for f in module.functions.values() if f.blocks}
    walk = {"look", "named", FINDER.removesuffix("()void")}
    assert not walk & lowered["silent"], lowered["silent"]
    assert walk <= lowered["reads"], lowered["reads"]
