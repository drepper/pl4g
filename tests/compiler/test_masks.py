"""A mask from a comparison over a run of bytes, at every level a build can ask for.

`\N{APL FUNCTIONAL SYMBOL QUAD}mask(c = 34u8)` is two or three instructions where a machine has registers of
bytes and a comparison of them -- sixteen at a time on the first two x86-64
levels and on AArch64, thirty-two on the third and fourth, sixteen on RISC-V
where the build has the vector extension -- and a byte at a time where it has
not.  What a program means does not depend on which, so the language test that
holds every mask to one worked out an element at a time is run here at every
level each target takes; the language suite runs it only at the defaults.

And the instructions are looked for, since a mask that came out right a byte at
a time on a machine that has the instructions would pass every other test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import describe, run_compiler, runner_for

ROOT = Path(__file__).resolve().parent.parent.parent

#: The language tests whose answers have to be the same at every level.
PROGRAMS = ("mask-of-a-run", "mask-orders", "view-copied-into-an-array",
            "operators-over-a-run")

#: Every level of each target the suite does not already build for.
LEVELS = [("x86_64-linux-none", level) for level in ("v1", "v2", "v3")] + [
    ("riscv64-linux-none", level) for level in ("rv64gc", "rv64gcv")]

#: Each program at each level, but the orders on a RISC-V build without the
#: vector extension: there a comparison of a run is the comparison of one byte
#: the scalar tests already ask about, sixty-four times over and ten times a
#: turn, which is what the code generator takes longest over of anything here.
CASES = [(triple, level, program) for triple, level in LEVELS for program in PROGRAMS
         if not (level == "rv64gc" and program == "mask-orders")]

ARROW = "\N{RIGHTWARDS ARROW}"
QUAD = "\N{APL FUNCTIONAL SYMBOL QUAD}"
OPEN, CLOSE = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}", "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}"

#: A scanner's step: thirty-two bytes read out of a buffer, and the quotes and
#: the bytes no greater than a space among them.
SCANNING = "".join((
    "fn quotes(input: u8", OPEN, CLOSE, ", at: u64 \N{CURRENCY SIGN}idx) ", ARROW, " u32:\n",
    "    let chunk: u8", OPEN, "32", CLOSE, " = input", OPEN, "at\N{HORIZONTAL ELLIPSIS}at + 32", CLOSE, "\n",
    "    ", QUAD, "mask((chunk = 34u8) \N{LOGICAL OR} (chunk \N{LESS-THAN OR EQUAL TO} 32u8))\n",
    "\n",
    "let std := ", QUAD, "import(\"std\")\n",
    "\n",
    "@[startup]\n",
    "fn main(init: std.Init) ", ARROW, " u6:\n",
    "    let b: u8", OPEN, "64", CLOSE, " = 64 \N{APL FUNCTIONAL SYMBOL RHO} ", OPEN, "34u8, 65u8", CLOSE, "\n",
    "    if quotes(b, 1) = 2863311530u32: 0u6 else: 1u6\n"))


@pytest.mark.parametrize(("triple", "level", "program"), CASES,
                         ids=["".join((t.split("-")[0], "-", lv, "-", p)) for t, lv, p in CASES])
@pytest.mark.parametrize("opt", ("0", "1"))
def test_the_answers_are_the_same_at_every_level(triple: str, level: str,
                                                 program: str, opt: str,
                                                 tmp_path: Path) -> None:
    """Each program exits with nought wherever it is built, which is every
    mask agreeing with the one worked out an element at a time."""
    source = ROOT / "tests" / "language" / program / "".join((program, ".pl4g"))
    out = tmp_path / "out"
    proc = run_compiler(["-o", str(out), "".join(("-O", opt)),
                         "".join(("--target=", triple)),
                         "".join(("--mclevel=", level)), str(source)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for(triple), str(out)], capture_output=True,
                         timeout=120)
    assert ran.returncode == 0, describe(ran)


def dumped(triple: str, level: str | None, tmp_path: Path) -> str:
    """The assembler dump of the scanner's step."""
    path = tmp_path / "t.pl4g"
    path.write_text(SCANNING, encoding="utf-8")
    out = tmp_path / "t.asm"
    arguments = ["-o", str(out), "--emit=asm", "-O1", "".join(("--target=", triple))]
    if level is not None:
        arguments.append("".join(("--mclevel=", level)))
    proc = run_compiler([*arguments, str(path)])
    assert proc.returncode == 0, describe(proc)
    return out.read_text(encoding="utf-8")


def test_the_step_runs(tmp_path: Path) -> None:
    """The program the dumps below are of answers what it should."""
    path = tmp_path / "t.pl4g"
    path.write_text(SCANNING, encoding="utf-8")
    out = tmp_path / "out"
    proc = run_compiler(["-o", str(out), str(path)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for("x86_64-linux-none"), str(out)],
                         capture_output=True, timeout=60)
    assert ran.returncode == 0, describe(ran)


def test_sixteen_bytes_at_a_time_at_the_first_level(tmp_path: Path) -> None:
    """Two comparisons, the unsigned one through the smaller of two bytes, an
    `or` and the gathering of the top bits, each twice for thirty-two bytes."""
    text = dumped("x86_64-linux-none", "v1", tmp_path)
    for mnemonic in ("pcmpeqb", "pminub", "por", "pmovmskb"):
        assert mnemonic in text, mnemonic
    assert "ymm" not in text


def test_thirty_two_at_the_third(tmp_path: Path) -> None:
    """The same in one register of thirty-two bytes: the run read once and
    gathered once."""
    text = dumped("x86_64-linux-none", "v3", tmp_path)
    assert "ymm" in text
    assert text.count("pmovmskb") == 1, text


def test_aarch64_adds_the_weighed_lanes(tmp_path: Path) -> None:
    """No instruction gathers a bit of each byte here, so the lanes are weighed
    and added up pairwise."""
    text = dumped("aarch64-linux-none", None, tmp_path)
    for mnemonic in ("cmeq.16b", "cmhs.16b", "orr.16b", "addp.16b", "umov.h0"):
        assert mnemonic in text, mnemonic


def test_riscv_asks_the_vector_extension_where_the_build_has_it(
        tmp_path: Path) -> None:
    """A comparison into a mask register and the mask read out whole -- and,
    built for a machine without the extension, no vector instruction at all."""
    text = dumped("riscv64-linux-none", "rva23", tmp_path)
    for mnemonic in ("vle8.v", "vmseq.vv", "vmsleu.vv", "vmor.mm", "vmv.x.s"):
        assert mnemonic in text, mnemonic
    scalar = dumped("riscv64-linux-none", "rv64gc", tmp_path)
    assert "vsetivli" not in scalar and "vle8.v" not in scalar


def test_what_is_said_once_is_not_said_again(tmp_path: Path) -> None:
    """Each group of vector instructions says what it works on, and where the
    one before in its block said the same the second goes: the two halves of a
    comparison work on the same sixteen bytes."""
    text = dumped("riscv64-linux-none", "rva23", tmp_path)
    lines = [line for line in text.splitlines()
             if "vsetivli" in line or ".vv" in line or ".mm" in line]
    said = [line for line in lines if "vsetivli" in line]
    worked = [line for line in lines if "vsetivli" not in line]
    assert len(said) < len(worked), text
