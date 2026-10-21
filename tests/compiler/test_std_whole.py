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


def _untyped(node: object, found: list[str], path: str) -> None:
    """Every definition under *node* that leaves its type to the compiler."""
    from dataclasses import fields, is_dataclass
    from pypl4g.front import ast

    match node:
        case ast.VarDef() if node.type is None:
            found.append("".join((path, ": let ", node.name)))
        case ast.ForEach() if node.type is None and node.name != "_" and not (
                node.comptime and isinstance(node.iterable, ast.Call)
                and isinstance(node.iterable.callee, ast.NameRef)
                and node.iterable.callee.name in ("\N{APL FUNCTIONAL SYMBOL QUAD}fields",
                                                  "\N{APL FUNCTIONAL SYMBOL QUAD}alternatives")):
            found.append("".join((path, ": foreach ", node.name)))
    if is_dataclass(node):
        for one in fields(node):
            _untyped(getattr(node, one.name), found, path)
    elif isinstance(node, (tuple, list)):
        for one in node:
            _untyped(one, found, path)


def test_std_writes_every_type() -> None:
    """The modules the installation provides say every type they use: nothing in
    them is left for the compiler to work out.

    The one exception is a `comptime foreach` over `⎕fields` or `⎕alternatives`,
    whose name is of a different type on every turn and so of none that could be
    written; the modules are system modules, so it is not logged either."""
    found: list[str] = []
    for where in system_modules():
        for path in sorted(where.glob("*.pl4g")):
            sources = SourceManager()
            engine, _ = collecting_engine(None)
            unit = parse(tokenize(sources.add(path, path.read_text(encoding="utf-8")),
                                  engine), str(path), engine)
            _untyped(unit.items, found, path.name)
    assert found == [], found
