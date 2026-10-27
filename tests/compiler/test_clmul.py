"""The product without carries, and the level being a description of it.

`\N{APL FUNCTIONAL SYMBOL QUAD}clmul` is one instruction on two of the three architectures where the program
was built for it, and a sequence of ordinary multiplications everywhere else.
So these check two things at once: that the instruction is used exactly where
the program was built for it, and that the answers are the same however the
machine arrived at them -- the language test checks thousands of products
against the schoolbook method, and here it is built for every level.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import ROOT, describe, run_compiler, runner_for

#: The language test, which exits with nought where every product was right.
PRODUCTS = ROOT / "tests" / "language" / "carryless-multiply" / "carryless-multiply.pl4g"

X86 = "x86_64-linux-none"
RISCV = "riscv64-linux-none"

#: A program with one product of two values the compiler cannot see.
ONE = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let high, low := \N{APL FUNCTIONAL SYMBOL QUAD}clmul(given(6u64), given(7u64))
    if high = 0u64 \N{LOGICAL AND} low = 18u64:
        0u6
    else:
        1u6

@[opaque]
fn given(n: u64) \N{RIGHTWARDS ARROW} u64:
    n
"""


def ran(triple: str, tmp_path: Path, *extra: str) -> int:
    """Build the language test for *triple*, run it, and answer its status."""
    out = tmp_path / "out"
    proc = run_compiler(["-o", str(out), "-O1", "".join(("--target=", triple)),
                         *extra, str(PRODUCTS)])
    assert proc.returncode == 0, describe(proc)
    done = subprocess.run([*runner_for(triple), str(out)], capture_output=True,
                          timeout=120)
    assert done.returncode >= 0, describe(done)
    return done.returncode


def dumped(triple: str, tmp_path: Path, *extra: str) -> str:
    """The assembler dump of the program with one product, for *triple*."""
    path = tmp_path / "t.pl4g"
    path.write_text(ONE, encoding="utf-8")
    out = tmp_path / "t.asm"
    proc = run_compiler(["-o", str(out), "--emit=asm", "-O1",
                         "".join(("--target=", triple)), *extra, str(path)])
    assert proc.returncode == 0, describe(proc)
    return out.read_text(encoding="utf-8")


@pytest.mark.parametrize("level", ("v1", "v2", "v3", "v4"))
def test_the_same_at_every_level(level: str, tmp_path: Path) -> None:
    """The two older levels multiply around the carries and the two newer ones
    have the instruction, and every product is the one the schoolbook gives."""
    assert ran(X86, tmp_path, "".join(("--mclevel=", level))) == 0


@pytest.mark.parametrize("isa", ("rv64gc", "rv64gc_zbc", "rv64gc_zbkc", "rva23"))
def test_the_same_with_zbc_and_without_it(isa: str, tmp_path: Path) -> None:
    """RISC-V has the two instructions in Zbc and again in Zbkc, and the
    application profile requires neither."""
    assert ran(RISCV, tmp_path, "".join(("--mclevel=", isa))) == 0


def test_the_instruction_is_used_where_the_level_has_it(tmp_path: Path) -> None:
    """`pclmulqdq` is in no level the architecture's table lists, and every
    processor of the third level has it, which is the level this compiler has
    ask for it."""
    assert "pclmulqdq" not in dumped(X86, tmp_path, "--mclevel=v2")
    assert "pclmulqdq" in dumped(X86, tmp_path, "--mclevel=v3")
    assert "pclmulqdq" in dumped(X86, tmp_path)


@pytest.mark.parametrize(("isa", "has"), (("rv64gc", False), ("rva23", False),
                                          ("rv64gc_zbc", True),
                                          ("rv64gc_zbkc", True)))
def test_riscv_has_it_with_either_extension(isa: str, has: bool,
                                            tmp_path: Path) -> None:
    """Both halves an instruction each where the program is built for one of
    the two extensions, and no `clmul` at all where it is built for neither."""
    text = dumped(RISCV, tmp_path, "".join(("--mclevel=", isa)))
    assert (" clmulh " in text and " clmul " in text) == has
    assert (" clmul" in text) == has


def test_without_it_there_is_no_branch(tmp_path: Path) -> None:
    """What stands in for the instruction is straight-line code: a loop would
    answer in a time that depends on the factors, which is what the arithmetic
    of a cipher must not do."""
    body = dumped(X86, tmp_path, "--mclevel=v2").split("main(ptr<Init>)u6:")[1]
    body = body.split("\n_start")[0]
    assert body.count(" imul ") == 48
    within = body.split(" imul ")
    between = " ".join(within[1:-1])
    assert not any(one in between for one in (" jmp ", " je ", " jne "))


def test_the_third_level_asks_the_processor_for_it(tmp_path: Path) -> None:
    """The bit that says the processor has it is among those the program's own
    entry point asks `CPUID` for, from the third level up."""
    from pypl4g.target.x86_64 import levels

    def asked(level: str) -> int:
        """The bits of the first leaf's `ECX` *level* wants."""
        return next(one.bits for one in levels.requirements(level)
                    if one.leaf == 1 and one.register == "ecx")

    assert asked("v2") & 0x2 == 0
    assert asked("v3") & 0x2 == 0x2
    assert asked("v4") & 0x2 == 0x2
