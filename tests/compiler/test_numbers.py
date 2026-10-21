"""What the crossings between whole and floating-point numbers, and the wide
multiplication, come to on each architecture.

The language tests say the answers are right.  These say they cost what a
hand-written program would pay for them: the instruction each machine has for
the question, once.
"""

from __future__ import annotations

import pytest

from conftest import describe, run_compiler

QUAD = "\N{APL FUNCTIONAL SYMBOL QUAD}"

#: Each question in a function of its own, which the dump is cut down to.
PROGRAM = """\
let std := ⎕import("std")

@[startup]
fn main(init: std.Init) → u6:
    let h, l := wide(3u64, 4u64)
    let f: f64 = float(given(3i64))
    let b: u64 = bits(1.0f64)
    let x: f64 = from_bits(4u64)
    if h = l ∨ b = 0u64 ∨ f < x:
        1u6
    else:
        0u6

@[opaque]
fn given(n: i64) → i64:
    n

@[opaque]
fn wide(a: u64, b: u64) → 〈u64, u64〉:
    ⎕wide_mul(a, b)

@[opaque]
fn float(n: i64) → f64:
    ⎕float(n, ⌜f64⌝)

@[opaque]
fn bits(x: f64) → u64:
    ⎕bits(x)

@[opaque]
fn from_bits(n: u64) → f64:
    ⎕from_bits(n, ⌜f64⌝)
"""

#: The instructions each architecture answers each with, and how many of each.
#: A mnemonic alone stands for every form of it; one followed by the first
#: letters of its operands stands for that form, which is what tells the move
#: between the two kinds of register from a move within one.
EXPECTED = {
    "x86_64": {"wide": {"mul": 1, "imul": 0}, "float": {"cvtsi2sd": 1},
               "bits": {"movq": 1}, "from_bits": {"movq": 1}},
    "aarch64": {"wide": {"umulh": 1, "mul": 1}, "float": {"scvtf": 1},
                "bits": {"fmov x d": 1}, "from_bits": {"fmov d x": 1}},
    "riscv64": {"wide": {"mulhu": 1, "mul": 1}, "float": {"fcvt.d.l": 1},
                "bits": {"fmv.x.d": 1}, "from_bits": {"fmv.d.x": 1}},
}


def mnemonics(arch: str, tmp_path) -> dict[str, list[str]]:  # noqa: ANN001
    """The mnemonics of each function of the program, compiled for *arch*."""
    path = tmp_path / "t.pl4g"
    path.write_text(PROGRAM, encoding="utf-8")
    out = tmp_path / "t.asm"
    proc = run_compiler(["-o", str(out), "--emit=asm", "-O1",
                         "".join(("--target=", arch, "-linux-none")), str(path)])
    assert proc.returncode == 0, describe(proc)
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in out.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(" ") and line.endswith(":"):
            current = found.setdefault(line.split("(")[0], [])
            continue
        if current is None or "\N{REFERENCE MARK}" in line.split("  ")[-1][:2]:
            continue
        # The bytes, and then the instruction: its mnemonic, and the first
        # letter of each of its operands.
        written = [one for one in line.split("  ") if one.strip()]
        if written:
            mnemonic, _, rest = written[-1].strip().partition(" ")
            kinds = [one.strip()[:1] for one in rest.split(",") if one.strip()]
            current.append(" ".join((mnemonic, *kinds)))
    return found


def counted(found: list[str], wanted: str) -> int:
    """How many of *found* are the instruction *wanted* names."""
    if " " in wanted:
        return sum(1 for one in found if one == wanted)
    return sum(1 for one in found if one.split(" ")[0] == wanted)


@pytest.mark.parametrize("arch", sorted(EXPECTED))
def test_each_is_the_instruction_the_machine_has(arch: str, tmp_path) -> None:  # noqa: ANN001
    """One instruction where the machine has one, and two where the upper half
    of a product is an instruction of its own: x86-64's one-operand `mul`
    answers both halves, so there is no second multiplication there."""
    found = mnemonics(arch, tmp_path)
    for which, wanted in EXPECTED[arch].items():
        for mnemonic, count in wanted.items():
            assert counted(found[which], mnemonic) == count, (which, found[which])
