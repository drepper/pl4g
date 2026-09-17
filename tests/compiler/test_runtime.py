"""The I/O runtime that is packaged with the compiler.

It is C, compiled ahead of time for every architecture and extracted into a
generated module per architecture.  Two things have to hold: that what is
committed is what the C compiles to, and that everything in it is something the
compiler knows what to do with.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from pypl4g.runtime import blob_for, names
from pypl4g.target.registry import architecture_of, canonical_triples

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "bin" / "pl4g-runtime"
COMPILER = "clang"

ARCHITECTURES = sorted({architecture_of(t) for t in canonical_triples()})


@pytest.mark.skipif(not shutil.which(COMPILER),
                    reason="".join((COMPILER, " is not installed")))
def test_what_is_committed_is_what_the_c_compiles_to() -> None:
    """A module older than the source it was made from fails here.

    The whole point of packaging the extracted data rather than the object is
    that building a program needs no C compiler; the cost is that what is
    committed can fall behind the C, and this is what stops it.
    """
    proc = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, "".join((
        "run bin/pl4g-runtime to bring the packaged runtime up to date\n",
        proc.stdout, proc.stderr))


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_there_is_a_runtime_for_every_architecture(architecture: str) -> None:
    """One the compiler generates for is one a program may do I/O on."""
    assert blob_for(architecture) is not None


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_every_name_it_promises_is_defined(architecture: str) -> None:
    """What the language may reach is what the C defines, and all of it."""
    blob = blob_for(architecture)
    assert blob is not None
    for one in names():
        assert blob.holds(one), "".join((architecture, " has no ", one))


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_every_name_lands_inside_a_piece(architecture: str) -> None:
    """A name outside the bytes it is said to be in would be an address the
    image had no code at."""
    blob = blob_for(architecture)
    assert blob is not None
    for one, (piece, offset) in blob.symbols.items():
        assert 0 <= piece < len(blob.pieces), one
        assert 0 <= offset < len(blob.pieces[piece].contents), one


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_every_patch_names_a_fixup_the_target_has(architecture: str) -> None:
    """The extractor maps a relocation onto a kind the target already applies,
    so a patch it could not name is a patch that stopped the extraction."""
    blob = blob_for(architecture)
    assert blob is not None
    by_name = import_module(
        "".join(("pypl4g.target.", architecture, ".fixups"))).BY_NAME
    for one in blob.patches:
        assert one.kind in by_name, "".join((architecture, ": ", one.kind))
        assert 0 <= one.piece < len(blob.pieces)
        assert 0 <= one.target < len(blob.pieces)
        kind = by_name[one.kind]
        held = blob.pieces[one.piece].contents
        assert one.offset + kind.size <= len(held)


def std_ring() -> object:
    """The record `modules/std.pl4g` hands the runtime, as the compiler sees it.

    Checked as a program, with a second file beside it naming the type: the
    files given on the command line share one namespace, so a function there may
    take the record and its parameter is then the type itself.  Reading the
    module the compiler actually uses is the point -- a copy of the declaration
    written here would be a third statement of one thing.
    """
    from pathlib import Path as _Path
    from pypl4g.diag.engine import collecting_engine
    from pypl4g.front.lexer import tokenize
    from pypl4g.front.parser import parse
    from pypl4g.ir.module import Module
    from pypl4g.sema.check import check
    from pypl4g.sema.modules import ModuleRegistry
    from pypl4g.source.manager import SourceManager

    sources = SourceManager()
    std = ROOT / "modules" / "std.pl4g"
    units = []
    engine, collected = collecting_engine(None)
    for path, text in ((std, std.read_text(encoding="utf-8")),
                       (_Path("probe.pl4g"),
                        "fn shape(r: &Ring) \N{RIGHTWARDS ARROW} u64:\n    r\N{POSITION INDICATOR}.state\n")):
        units.append(parse(tokenize(sources.add(path, text), engine),
                           str(path), engine))
    module = Module("std")
    check(module, units, engine, ModuleRegistry(), sources)
    # Everything but "this is not a program": it is a module and a file naming
    # one type in it, and neither was ever going to start anything.
    bad = [d.info.name for d in collected if d.info.severity == "error"
           and d.info.name != "LANG_FUNCDEF_SPECIAL_NO_STARTUP"]
    assert bad == [], bad
    probe = next(f for f in module.functions.values() if f.name == "shape")
    held = probe.ty.params[0]
    return held.pointee


def test_the_shared_record_is_laid_out_as_the_runtime_reads_it() -> None:
    """Two declarations in two languages, and nothing but this makes them one.

    A field added on one side and not the other is a program reading the wrong
    word, which nothing else would catch: the call would be made, the addresses
    would be right, and every number after the missing field would be somewhere
    else.
    """
    from pypl4g.ir.layout import DataLayout, align_of, offsets_of, size_of
    from pypl4g.runtime import ring_fields, shape_of

    ring = std_ring()
    assert [name for name, _ in ring.fields] == list(ring_fields())
    layout = DataLayout(pointer_size=8)
    places: list[int] = []
    for offset, (_, held) in zip(offsets_of(ring, layout), ring.fields):
        places.extend((offset, size_of(held, layout)))
    wanted = (size_of(ring, layout), align_of(ring, layout), *places)
    for architecture in ARCHITECTURES:
        blob = blob_for(architecture)
        assert blob is not None
        assert shape_of(blob) == wanted, architecture


def test_the_architectures_agree_on_what_they_define() -> None:
    """One source compiled three ways answers the same names three times."""
    found = {a: sorted(blob_for(a).symbols) for a in ARCHITECTURES  # type: ignore[union-attr]
             if blob_for(a) is not None}
    assert len({tuple(one) for one in found.values()}) == 1, found
