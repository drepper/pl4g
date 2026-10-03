"""`std` is checked as a whole, whatever any one program asks of it.

A program compiles a module's functions as far as it names them, so a body no
test program reaches would never be checked by compiling programs.  This checks
every body of `std` -- and of whatever it reads -- the way the compiler did before
it learned to stop at what is named, so that a mistake in a function nothing calls
yet is found now rather than by the first program that calls it.
"""

from __future__ import annotations

from pathlib import Path

from pypl4g.diag.engine import collecting_engine
from pypl4g.front.lexer import tokenize
from pypl4g.front.parser import parse
from pypl4g.ir.module import Module
from pypl4g.sema.check import check
from pypl4g.sema.modules import ModuleRegistry, SearchPath, system_modules
from pypl4g.source.manager import SourceManager

PROGRAM = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    0u6
"""


def _checked(whole: bool) -> tuple[Module, list[str]]:
    """The module a program naming nothing of `std` comes to, and its errors."""
    sources = SourceManager()
    engine, collected = collecting_engine(None)
    unit = parse(tokenize(sources.add(Path("probe.pl4g"), PROGRAM), engine),
                 "probe.pl4g", engine)
    module = Module("probe")
    check(module, [unit], engine,
          ModuleRegistry(search=SearchPath(system=system_modules())), sources,
          whole_modules=whole)
    return module, [d.info.name for d in collected if d.info.severity == "error"]


def test_every_body_of_std_checks() -> None:
    """Every function of `std` is lowered, and none of them says anything."""
    module, errors = _checked(True)
    assert errors == [], errors
    empty = [f.name for f in module.functions.values()
             if f.name.startswith("std.") and not f.blocks
             and f.attrs.external is None]
    assert empty == [], empty


def test_a_program_compiles_only_what_it_names() -> None:
    """Naming nothing of `std`, a program lowers nothing of it that does I/O: the
    ring is drained by the entry point, which calls the runtime itself."""
    module, errors = _checked(False)
    assert errors == [], errors
    lowered = {f.name for f in module.functions.values() if f.blocks}
    assert "main" in lowered and not any("drain" in one or "finish" in one
                                         for one in lowered), lowered
    assert not any("read_lines" in one for one in lowered), lowered
    assert not any("error_name" in one for one in lowered), lowered
