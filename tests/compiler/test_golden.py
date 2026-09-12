"""Golden files for the textual IR and the assembler dump.

Regenerate them with ``PL4G_UPDATE_GOLDEN=1 pytest``, and read the diff: a change
here is a change in what the compiler produces.
"""

import os
from pathlib import Path

import pytest

from conftest import architecture_of, compiler_targets, describe, run_compiler
from pypl4g.ir.printer import render_module
from pypl4g.ir.reader import read_module

GOLDEN = Path(__file__).resolve().parent / "data" / "golden"
CASES = ["exit0", "exit-42", "explicit-block", "local-variable",
         "global-variable", "assign-global",
         "assign-is-the-result"]


def _language_test(root: Path, name: str) -> Path:
    """The source file of the language test called *name*."""
    return root / "tests" / "language" / name / "".join((name, ".pl4g"))


def _compare(path: Path, produced: str) -> None:
    """Compare against the golden file, or write it when asked to."""
    if os.environ.get("PL4G_UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(produced, encoding="utf-8")
        return
    assert path.exists(), "".join((
        "missing golden file ", path.name, "; run PL4G_UPDATE_GOLDEN=1 pytest"))
    assert produced == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CASES)
def test_ir_matches_golden(root: Path, name: str, tmp_path: Path) -> None:
    """The IR the frontend produces is what the golden file records."""
    output = tmp_path / "out.ir"
    proc = run_compiler(["-o", str(output), "--emit=ir", str(_language_test(root, name))])
    assert proc.returncode == 0, describe(proc)
    _compare(GOLDEN / "".join((name, ".ir")), output.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", CASES)
def test_ir_round_trip_is_a_fixed_point(name: str) -> None:
    """Reading a golden file and printing it again gives the same text.

    This is the practical meaning of the textual form being round-trippable.
    """
    path = GOLDEN / "".join((name, ".ir"))
    if not path.exists():
        pytest.skip("golden file has not been generated yet")
    text = path.read_text(encoding="utf-8")
    assert render_module(read_module(text)) == text


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize("name", CASES)
def test_asm_matches_golden(root: Path, name: str, triple: str,
                            tmp_path: Path) -> None:
    """Instruction selection produces what the golden dump records.

    There is one dump per architecture, so a change in either backend has to be
    acknowledged rather than slipping through.
    """
    output = tmp_path / "out.asm"
    proc = run_compiler(["-o", str(output), "--emit=asm", "-O1",
                         "".join(("--target=", triple)),
                         str(_language_test(root, name))])
    assert proc.returncode == 0, describe(proc)
    golden = GOLDEN / "".join((name, ".", architecture_of(triple), ".asm"))
    _compare(golden, output.read_text(encoding="utf-8"))
