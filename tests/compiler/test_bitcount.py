"""Counting bits, and the microarchitecture level being a description.

`\N{APL FUNCTIONAL SYMBOL QUAD}ones`, `\N{APL FUNCTIONAL SYMBOL QUAD}lead` and `\N{APL FUNCTIONAL SYMBOL QUAD}trail` are questions the language asks that two of
the three architectures answer with an instruction only at some levels.  So
these check two things at once: that the answers are right, and that they are
the same answers however the machine arrived at them.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for

ARROW = "\N{RIGHTWARDS ARROW}"
QUAD = "\N{APL FUNCTIONAL SYMBOL QUAD}"
LIFT, DROP = "\N{TOP LEFT CORNER}", "\N{TOP RIGHT CORNER}"

#: (what is counted, its type, the value, the answer).  The values are the ones
#: where the answer changes: nothing set, everything set, one bit at each end,
#: and a value whose type is narrower than the register it is held in.
CASES = [
    ("ones", "u64", "0u64", 0), ("ones", "u64", "1u64", 1),
    ("ones", "u64", "18446744073709551615u64", 64),
    ("ones", "u64", "1229782938247303441u64", 16),
    ("ones", "u8", "0u8", 0), ("ones", "u8", "255u8", 8),
    ("ones", "u8", "200u8", 3),
    ("ones", "i8", "\N{SUPERSCRIPT MINUS}1i8", 8),
    ("ones", "i8", "\N{SUPERSCRIPT MINUS}128i8", 1),
    ("ones", "i64", "\N{SUPERSCRIPT MINUS}1i64", 64),
    ("lead", "u64", "0u64", 64), ("lead", "u64", "1u64", 63),
    ("lead", "u64", "18446744073709551615u64", 0),
    ("lead", "u64", "9223372036854775808u64", 0),
    ("lead", "u8", "0u8", 8), ("lead", "u8", "1u8", 7),
    ("lead", "u8", "255u8", 0), ("lead", "u8", "128u8", 0),
    ("lead", "u32", "65536u32", 15),
    ("lead", "i8", "\N{SUPERSCRIPT MINUS}1i8", 0),
    ("lead", "i8", "1i8", 7),
    ("trail", "u64", "0u64", 64), ("trail", "u64", "1u64", 0),
    ("trail", "u64", "9223372036854775808u64", 63),
    ("trail", "u64", "18446744073709551615u64", 0),
    ("trail", "u8", "0u8", 8), ("trail", "u8", "128u8", 7),
    ("trail", "u32", "65536u32", 16), ("trail", "u32", "0u32", 32),
    ("trail", "i8", "\N{SUPERSCRIPT MINUS}128i8", 7),
    ("trail", "i8", "\N{SUPERSCRIPT MINUS}1i8", 0),
    ("trail", "i64", "\N{SUPERSCRIPT MINUS}9223372036854775808i64", 63),
]

#: The program that asks one of them.  The value is read out of a variable, so
#: that the answer comes from the machine rather than from the front end, and
#: what the program says is whether the answer is the one written down -- a
#: count of sixty-four being one an exit status cannot carry.
ASKING = """\
let value: {1} = {2}

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) {0} u6:
    if {3}{4}(value) ≠ {5}u8:
        1u6
    else:
        0u6
"""


def source(which: str, ty: str, value: str, expected: int) -> str:
    """A program that exits with nought where *which* answers *expected*."""
    return ASKING.format(ARROW, ty, value, QUAD, which, expected)


def ran(text: str, triple: str, tmp_path, *extra: str) -> int:  # noqa: ANN001
    """Compile *text* for *triple*, run it, and answer with its status."""
    path = tmp_path / "t.pl4g"
    path.write_text(text, encoding="utf-8")
    out = tmp_path / "out"
    proc = run_compiler(["-o", str(out), "".join(("--target=", triple)), *extra,
                         str(path)])
    assert proc.returncode == 0, describe(proc)
    ran_it = subprocess.run([*runner_for(triple), str(out)], capture_output=True,
                            timeout=60)
    assert ran_it.returncode >= 0, describe(ran_it)
    return ran_it.returncode


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize(("which", "ty", "value", "expected"), CASES,
                         ids=["".join((c[0], ".", c[1], ".", str(c[3])))
                              for c in CASES])
def test_the_count_is_right(triple: str, which: str, ty: str, value: str,
                            expected: int, tmp_path) -> None:  # noqa: ANN001
    """Every case, compiled and run, on every target."""
    assert ran(source(which, ty, value, expected), triple, tmp_path) == 0


@pytest.mark.parametrize("level", ("v1", "v2", "v3"))
@pytest.mark.parametrize(("which", "ty", "value", "expected"), CASES,
                         ids=["".join((c[0], ".", c[1], ".", str(c[3])))
                              for c in CASES])
def test_and_the_same_at_every_level(level: str, which: str, ty: str,
                                     value: str, expected: int,
                                     tmp_path) -> None:  # noqa: ANN001
    """Which is what makes the level a promise about speed and not about
    answers: the first has neither instruction and counts the bits itself, the
    second has one and the third has both."""
    assert ran(source(which, ty, value, expected), "x86_64-linux-none", tmp_path,
               "".join(("--mclevel=", level))) == 0


def dumped(text: str, tmp_path, *extra: str) -> str:  # noqa: ANN001
    """The assembler dump of *text*, compiled for x86-64."""
    path = tmp_path / "t.pl4g"
    path.write_text(text, encoding="utf-8")
    out = tmp_path / "t.asm"
    proc = run_compiler(["-o", str(out), "--emit=asm", "-O1",
                         "--target=x86_64-linux-none", *extra, str(path)])
    assert proc.returncode == 0, describe(proc)
    return out.read_text(encoding="utf-8")


def test_the_instruction_is_used_where_the_level_has_it(tmp_path) -> None:  # noqa: ANN001
    """The second level adds counting the bits set and the third adds counting
    the zeroes above the highest one, so a program asking both gets one
    instruction at the second and two at the third."""
    ones = source("ones", "u64", "7u64", 3)
    lead = source("lead", "u64", "7u64", 61)
    assert "popcnt" not in dumped(ones, tmp_path, "--mclevel=v1")
    assert "popcnt" in dumped(ones, tmp_path, "--mclevel=v2")
    assert "lzcnt" not in dumped(lead, tmp_path, "--mclevel=v2")
    assert "lzcnt" in dumped(lead, tmp_path, "--mclevel=v3")


def test_the_count_from_the_bottom_is_an_instruction_at_every_level(
        tmp_path) -> None:  # noqa: ANN001
    """`tzcnt` comes with the third level, and below it `bsf` finds the same bit
    with the answer for nought left out -- which a conditional move puts back,
    so there is no branch at any level."""
    trail = source("trail", "u64", "8u64", 3)
    assert "tzcnt" in dumped(trail, tmp_path, "--mclevel=v3")
    older = dumped(trail, tmp_path, "--mclevel=v1")
    assert "tzcnt" not in older and "bsf" in older and "cmove" in older


def riscv_dumped(text: str, tmp_path, *extra: str) -> str:  # noqa: ANN001
    """The assembler dump of *text*, compiled for RISC-V."""
    path = tmp_path / "t.pl4g"
    path.write_text(text, encoding="utf-8")
    out = tmp_path / "t.asm"
    proc = run_compiler(["-o", str(out), "--emit=asm", "-O1",
                         "--target=riscv64-linux-none", *extra, str(path)])
    assert proc.returncode == 0, describe(proc)
    return out.read_text(encoding="utf-8")


@pytest.mark.parametrize(("which", "mnemonic"), (("ones", "cpop"),
                                                 ("lead", "clz"),
                                                 ("trail", "ctz")))
def test_riscv_counts_with_zbb_and_without_it(which: str, mnemonic: str,
                                              tmp_path) -> None:  # noqa: ANN001
    """The three counts are Zbb's on RISC-V, which the application profile the
    compiler builds for has; a program built for the base without it counts
    with the sequences, and answers the same."""
    text = source(which, "u64", "8u64", {"ones": 1, "lead": 60, "trail": 3}[which])
    assert "".join((" ", mnemonic, " ")) in riscv_dumped(text, tmp_path)
    assert "".join((" ", mnemonic, " ")) not in riscv_dumped(
        text, tmp_path, "--mclevel=rv64gc")


@pytest.mark.parametrize(("which", "ty", "value", "expected"), CASES,
                         ids=["".join((c[0], ".", c[1], ".", str(c[3])))
                              for c in CASES])
def test_and_the_same_without_zbb(which: str, ty: str, value: str,
                                  expected: int, tmp_path) -> None:  # noqa: ANN001
    """The sequences RISC-V counts with where it is built for the base."""
    assert ran(source(which, ty, value, expected), "riscv64-linux-none",
               tmp_path, "--mclevel=rv64gc") == 0


def test_the_level_without_it_counts_the_bits_itself(tmp_path) -> None:  # noqa: ANN001
    """And what it emits instead has no branch in it: a loop would answer in a
    time that depends on the value, which is the wrong shape for something a
    program may do in a tight place."""
    def branches(level: str) -> int:
        """How many instructions of `main` are branches at *level*."""
        body = dumped(source("ones", "u64", "7u64", 3), tmp_path,
                      "".join(("--mclevel=", level))).split("main(ptr<Init>)u6:")[1]
        body = body.split("\n_start")[0]
        return sum(body.count(one) for one in (" jmp ", " je ", " jne "))

    def instructions(level: str) -> int:
        """And how many instructions it is in all."""
        body = dumped(source("ones", "u64", "7u64", 3), tmp_path,
                      "".join(("--mclevel=", level))).split("main(ptr<Init>)u6:")[1]
        return body.split("\n_start")[0].count("\n    ")

    assert instructions("v1") > instructions("v2") + 8
    assert branches("v1") == branches("v2")


#: A shift by a value the program computed, which is three instructions and a
#: fixed register at the first two levels and one instruction at the third.
SHIFTING = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

let value: u64 = 1229782938247303441u64
let distance: u64 = 4u64

@[startup]
fn main(init: std.Init) {0} u6:
    if {1}ones(value {2} distance) ≠ 15u8:
        1u6
    else:
        0u6
""".format(ARROW, QUAD, "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}")


def test_a_shift_takes_its_count_in_any_register_at_the_third_level(
        tmp_path) -> None:  # noqa: ANN001
    """One instruction where the older form is three, and no register held away
    from every other value while the shift waits for it."""
    assert "shlx" not in dumped(SHIFTING, tmp_path, "--mclevel=v2")
    assert "shlx" in dumped(SHIFTING, tmp_path, "--mclevel=v3")


@pytest.mark.parametrize("level", ("v1", "v2", "v3"))
def test_and_answers_the_same_whichever_it_is(level: str, tmp_path) -> None:  # noqa: ANN001
    """Sixteen bits set, moved up four places, are fifteen: the highest of them
    went off the end, which is what a shift does with what it loses."""
    assert ran(SHIFTING, "x86_64-linux-none", tmp_path,
               "".join(("--mclevel=", level))) == 0


def test_counting_the_bits_of_something_that_is_not_a_number_is_refused(
        tmp_path) -> None:  # noqa: ANN001
    """Both count the bits of a whole number and of nothing else."""
    path = tmp_path / "t.pl4g"
    path.write_text("""\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

let value: f64 = 1.5f64

@[startup]
fn main(init: std.Init) {0} u6:
    {1}narrow({1}ones(value), {2}u6{3}) ?? 1u6
""".format(ARROW, QUAD, LIFT, DROP), encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "--target=x86_64-linux-none", str(path)])
    assert proc.returncode != 0
    assert "PL4G-4803" in "".join((proc.stdout, proc.stderr))
