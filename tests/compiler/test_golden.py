"""Golden files for the textual IR and the assembler dump.

Regenerate them with ``PL4G_UPDATE_GOLDEN=1 pytest``, and read the diff: a change
here is a change in what the compiler produces.
"""

from __future__ import annotations

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


def _chunks(text: str, ir: bool) -> list[list[str]]:
    """*text* cut into the things it defines, each with the lines that say it.

    In the IR a variable is one line and a function runs from its first line to
    the brace that closes it; in an assembler dump a label begins one and the
    next label, section or blank line ends it.  Anything else -- the header, a
    section, a blank line -- is a chunk of its own.
    """
    chunks: list[list[str]] = []
    open_ = False
    for line in text.splitlines():
        if open_ and (ir or line.startswith(" ")):
            chunks[-1].append(line)
            open_ = line != "}" if ir else True
            continue
        chunks.append([line])
        open_ = (line.startswith("fn ") and line.endswith("{")) if ir else \
            (bool(line) and not line.startswith((" ", "section ")) and line.endswith(":"))
    return chunks


def _unpadded(chunk: list[str]) -> tuple[str, ...]:
    """*chunk* without the padding in it, which is where it landed and not what it is."""
    return tuple(line for line in chunk if "\N{REFERENCE MARK} align " not in line)


def _own(root: Path, produced: str, arguments: list[str], ir: bool,
         tmp_path: Path) -> str:
    """What of *produced* is the program's own.

    Every program imports `std`, and `std`'s destructor, what it reaches and the
    entry point that calls it are in every one of them, the same lines in each.
    They would bury the thing each file is about, so what `exit0` carries apart
    from its startup function is taken out here, and recorded once where the
    making of the stack is (`stack.ARCH.asm`).
    """
    output = tmp_path / "carried"
    proc = run_compiler(["-o", str(output), *arguments,
                         str(_language_test(root, "exit0"))])
    assert proc.returncode == 0, describe(proc)
    carried = {_unpadded(chunk)
               for chunk in _chunks(output.read_text(encoding="utf-8"), ir)
               if not chunk[0].startswith(("fn @main(", "main("))
               and chunk[0].strip() and not chunk[0].startswith((";", "module ", "※", "section "))}
    kept = [chunk for chunk in _chunks(produced, ir) if _unpadded(chunk) not in carried]
    # A section nothing of the program's own is in, and the blank lines left
    # where something was taken out, go with it.
    lines: list[str] = []
    for at, chunk in enumerate(kept):
        if chunk[0].startswith("section ") and \
                (at + 1 == len(kept) or kept[at + 1][0].startswith("section ")
                 or not kept[at + 1][0]):
            continue
        if not chunk[0] and (not lines or not lines[-1]):
            continue
        lines.extend(chunk)
    while lines and not lines[-1]:
        lines.pop()
    return "".join(("\n".join(lines), "\n"))


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
    _compare(GOLDEN / "".join((name, ".ir")),
             _own(root, output.read_text(encoding="utf-8"), ["--emit=ir"], True, tmp_path))


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

    At the oldest microarchitecture level on x86-64, so that what the dump shows
    is the program.  Every level above it puts the same forty-odd lines of
    `CPUID` at the entry point of every program, which would be repeated in each
    of these files and would bury the thing each file is about; what that code
    is, is checked where it is the subject.

    With no stack of its own, for the same reason: making one is another forty
    lines at the entry point of every program, the same forty in each of these
    files.  They have a golden file of their own, below.
    """
    output = tmp_path / "out.asm"
    arguments = ["--emit=asm", "-O1", "--stack-size=0", "".join(("--target=", triple))]
    if architecture_of(triple) == "x86_64":
        arguments.append("--mclevel=v1")
    proc = run_compiler(["-o", str(output), *arguments, str(_language_test(root, name))])
    assert proc.returncode == 0, describe(proc)
    golden = GOLDEN / "".join((name, ".", architecture_of(triple), ".asm"))
    _compare(golden, _own(root, output.read_text(encoding="utf-8"), arguments, False,
                          tmp_path))


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_stack_is_made_as_the_golden_dump_says(root: Path, triple: str,
                                                   tmp_path: Path) -> None:
    """And what every other dump here leaves out, recorded once.

    The making of the stack is code the compiler selects like any other, so a
    change in it is a change in code generation and has to be acknowledged
    somewhere.  Here rather than in each of the files above, since it is the
    same instructions in every program: what differs between the programs is
    what those files are about.  What `std` puts in every program is recorded
    here too, whole, for the same reason.
    """
    output = tmp_path / "out.asm"
    arguments = ["-o", str(output), "--emit=asm", "-O1",
                 "".join(("--target=", triple))]
    if architecture_of(triple) == "x86_64":
        arguments.append("--mclevel=v1")
    proc = run_compiler([*arguments, str(_language_test(root, "exit0"))])
    assert proc.returncode == 0, describe(proc)
    _compare(GOLDEN / "".join(("stack.", architecture_of(triple), ".asm")),
             output.read_text(encoding="utf-8"))
