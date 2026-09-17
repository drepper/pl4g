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


def test_the_architectures_agree_on_what_they_define() -> None:
    """One source compiled three ways answers the same names three times."""
    found = {a: sorted(blob_for(a).symbols) for a in ARCHITECTURES  # type: ignore[union-attr]
             if blob_for(a) is not None}
    assert len({tuple(one) for one in found.values()}) == 1, found
