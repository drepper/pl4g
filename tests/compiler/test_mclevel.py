"""What a program is built for, and the check it makes of the processor.

Two architectures answer the question differently and the option is the same
option.  x86-64 names four sets of features and asking for one is asking for the
whole of it; RISC-V has no such list, its base being small and everything else
an extension, so what is asked for is a string naming them or a profile naming a
published set of them.  AArch64 has nothing to ask for at all.

x86-64 has meant several quite different machines over twenty-five years, and its
own documentation names the four sets for saying which one a program was built
for.  What this compiler does with a level is let code generation use what
the level allows -- nothing does yet -- and have the program ask the processor,
at its own entry point, whether it can run at all.

The asking is done with `CPUID`, which is the instruction the architecture
provides for the question: it is answered the same way on every operating system
and it cannot be out of date about the processor the program is actually running
on, which a file the kernel writes can be.

And with `XGETBV` beside it from the third level up, because the processor is
only half of the question.  `OSXSAVE` says the processor *lets* the operating
system enable the wide registers; whether it has is in `XCR0`, and a kernel
built or booted with them off would let a program pass its own check and then
fault on the first instruction that used one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pypl4g.target import statuses
from conftest import describe, limited, run_compiler, runner_for

SOURCE = "".join((
    "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    42u6\n"))

X86 = "x86_64-linux-none"

#: Each level's binary, built once.
@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One binary per level, and one for the level left unstated."""
    directory = tmp_path_factory.mktemp("mclevel")
    source = directory / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    found: dict[str, Path] = {}
    for level in ("v1", "v2", "v3", "v4", None):
        output = directory / (level or "default")
        arguments = ["-o", str(output), "".join(("--target=", X86))]
        if level is not None:
            arguments.append("".join(("--mclevel=", level)))
        proc = run_compiler([*arguments, str(source)])
        assert proc.returncode == 0, describe(proc)
        found[level or "default"] = output
    return found


def _asm(tmp_path: Path, level: str) -> str:
    """The assembly the compiler emits for a program at *level*."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "out.asm"
    proc = run_compiler(["-o", str(output), "--emit=asm",
                         "".join(("--target=", X86)),
                         "".join(("--mclevel=", level)), str(source)])
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def test_the_oldest_level_asks_nothing(tmp_path: Path) -> None:
    """Every x86-64 processor has v1 by being one, so there is nothing to ask."""
    assert "cpuid" not in _asm(tmp_path, "v1")


@pytest.mark.parametrize(("level", "leaves"), [("v2", 4), ("v3", 5), ("v4", 5)])
def test_a_level_asks_once_per_leaf(tmp_path: Path, level: str,
                                    leaves: int) -> None:
    """One `CPUID` per leaf asked, counting the two that ask which leaves exist.

    v4 wants no leaf v3 does not, only more bits of one of them, which is why it
    asks no more questions than v3 while requiring more of the answers.
    """
    assert _asm(tmp_path, level).count("cpuid") == leaves


@pytest.mark.parametrize("level", ("v1", "v2"))
def test_a_level_with_no_wide_registers_asks_the_system_nothing(
        tmp_path: Path, level: str) -> None:
    """There is nothing for a system to have turned off below the third level:
    what those levels add is instructions, not registers a context switch has to
    save."""
    assert "xgetbv" not in _asm(tmp_path, level)


@pytest.mark.parametrize("level", ("v3", "v4"))
def test_a_level_with_them_asks_the_system_too(tmp_path: Path,
                                               level: str) -> None:
    """`CPUID` says the processor lets the system enable the wide registers;
    whether it did is a further question and `XGETBV` is the only way to ask it.

    A program that asked only the processor would pass its own check and then
    fault on the first instruction that used one -- which is the failure this
    check exists to turn into a sentence.
    """
    assert "xgetbv" in _asm(tmp_path, level)
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    image = tmp_path / "out"
    proc = run_compiler(["-o", str(image), "".join(("--target=", X86)),
                         "".join(("--mclevel=", level)), str(source)])
    assert proc.returncode == 0, describe(proc)
    # The sentence a system that has turned them off is told, which is not the
    # sentence a processor that cannot run the program at all is told.
    assert b"has not enabled the registers it uses" in image.read_bytes()
    assert b"this processor does not have it" in image.read_bytes()


def test_what_the_system_is_asked_for_grows_with_the_level(
        tmp_path: Path) -> None:
    """The third level wants the halves of a vector register saved and the
    fourth wants the mask registers and the sixteen further ones as well."""
    from pypl4g.target.x86_64 import levels

    assert levels.state("v2") == 0
    assert levels.state("v3") == 0x6
    assert levels.state("v4") == 0xE6
    assert levels.state_names("v4")[:2] == ("SSE", "AVX")


def test_a_level_asks_for_more_than_the_one_below(tmp_path: Path) -> None:
    """Each level contains the one before it, so each asks for at least as much."""
    masks = {level: sorted(_masks(_asm(tmp_path, level)))
             for level in ("v2", "v3", "v4")}
    assert masks["v2"] != masks["v3"] != masks["v4"], masks


def _masks(text: str) -> set[int]:
    """The numbers the emitted checks compare against."""
    found: set[int] = set()
    for line in text.splitlines():
        if " and " in line or " cmp " in line:
            word = line.rsplit(",", 1)[-1].strip()
            if word.isdigit():
                found.add(int(word))
    return found


def test_the_level_left_unstated_is_the_newest(built: dict[str, Path]) -> None:
    """A program that will not run says so at once; one built for the oldest
    machine quietly leaves everything on the table."""
    assert built["default"].read_bytes() == built["v4"].read_bytes()


def test_a_level_that_is_not_one_is_refused(tmp_path: Path) -> None:
    """The names are the architecture's own, so there is a list of them."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), "--target=" + X86,
                         "--mclevel=v5", str(source)])
    assert proc.returncode != 0
    assert "PL4G-1011" in proc.stderr, proc.stderr


def test_an_architecture_without_levels_says_so(tmp_path: Path) -> None:
    """They are each architecture's own, so an architecture that has nothing to
    ask for says so rather than quietly taking any name."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "--target=aarch64-linux-none", "--mclevel=v2",
                         str(source)])
    assert proc.returncode != 0
    assert "PL4G-1012" in proc.stderr, proc.stderr


def test_a_name_from_one_architecture_means_nothing_to_another(
        tmp_path: Path) -> None:
    """RISC-V has something to ask for and `v2` is not one of them, so what
    comes back says what one of them looks like."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "--target=riscv64-linux-none", "--mclevel=v2",
                         str(source)])
    assert proc.returncode != 0
    assert "PL4G-1011" in proc.stderr, proc.stderr
    assert "rv32" in proc.stderr and "rva23" in proc.stderr, proc.stderr


def test_an_architecture_without_levels_is_built_without_one(tmp_path: Path) -> None:
    """Saying nothing is not asking for anything, so it is not an error."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "--target=aarch64-linux-none", str(source)])
    assert proc.returncode == 0, describe(proc)


RISCV = "riscv64-linux-none"


@pytest.mark.parametrize("level", ["rva23", "rva23u64", "rva23s64", "rv64gc",
                                   "rv64g", "rv64imafd", "RV64GC_Zba_Zbb",
                                   "rv64gc_zfa", "rv64i_m_a_f_d"])
def test_risc_v_takes_an_isa_string_or_a_profile(tmp_path: Path,
                                                 level: str) -> None:
    """Every spelling the naming convention allows for what this program needs
    builds it."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), "--target=" + RISCV,
                         "".join(("--mclevel=", level)), str(source)])
    assert proc.returncode == 0, describe(proc)


FLOATS = "".join((
    "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u6:\n",
    "    let x: f64 = 1.5f64\n",
    "    if x \N{APPROXIMATELY EQUAL TO} 1.5f64:\n        0u6\n",
    "    else:\n        1u6\n"))


def test_floating_point_needs_the_extension_that_has_it(tmp_path: Path) -> None:
    """A base without the floating-point extensions cannot run floating-point
    instructions, and there is no software convention here to fall back on."""
    source = tmp_path / "t.pl4g"
    source.write_text(FLOATS, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), "--target=" + RISCV,
                         "--mclevel=rv64imc", str(source)])
    assert proc.returncode != 0
    assert "PL4G-8503" in proc.stderr, proc.stderr


#: Rounding written four ways, whose answers are the same whichever of the two
#: lowerings the backend chose.
ROUNDS = "".join((
    "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n",
    "    let x: f64 = \N{SUPERSCRIPT MINUS}2.5f64\n",
    "    let y: f32 = 2.5f32\n",
    "    if \N{DOWNWARDS ARROW}x \N{APPROXIMATELY EQUAL TO} \N{SUPERSCRIPT MINUS}3.0f64 ",
    "\N{LOGICAL AND} \N{UPWARDS ARROW}x \N{APPROXIMATELY EQUAL TO} \N{SUPERSCRIPT MINUS}2.0f64 ",
    "\N{LOGICAL AND} \N{UP DOWN ARROW}x \N{APPROXIMATELY EQUAL TO} \N{SUPERSCRIPT MINUS}2.0f64 ",
    "\N{LOGICAL AND} \N{UP DOWN DOUBLE ARROW}y \N{APPROXIMATELY EQUAL TO} 2.0f32:\n",
    "        0u6\n    else:\n        1u6\n"))


@pytest.mark.parametrize("level", ["rva23", "rv64gc"])
def test_rounding_answers_alike_with_and_without_the_instruction(
        tmp_path: Path, level: str) -> None:
    """Zfa has an instruction that rounds where the number stands and the base
    has none, so the backend has two lowerings -- and what they answer has to be
    the same answer.  The profile has Zfa; `rv64gc` is the same machine without
    it, and is the only way the round trip through an integer is reached now
    that the default profile has the instruction."""
    source = tmp_path / "t.pl4g"
    source.write_text(ROUNDS, encoding="utf-8")
    built = tmp_path / "out"
    proc = run_compiler(["-o", str(built), "--target=" + RISCV,
                         "".join(("--mclevel=", level)), str(source)])
    assert proc.returncode == 0, describe(proc)
    runner = runner_for(RISCV)
    if runner and shutil.which(runner[0]) is None:
        pytest.skip("no emulator for this target")
    assert subprocess.run([*runner, str(built)], check=False).returncode == 0


def test_a_program_with_no_floats_needs_no_floating_point(tmp_path: Path) -> None:
    """What is refused is using it, not building for a base without it."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), "--target=" + RISCV,
                         "--mclevel=rv64imc", str(source)])
    assert proc.returncode == 0, describe(proc)


#: What each of these processors is old enough not to have.  `qemu` emulates
#: them by name, which is the only way to run a program on a processor one does
#: not own -- and the only way to see the refusal actually happen.
OLD_ENOUGH = [("qemu64", "v2"), ("qemu64", "v3"), ("Nehalem", "v3"),
              ("Haswell", "v4")]

NEW_ENOUGH = [("qemu64", "v1"), ("Nehalem", "v1"), ("Nehalem", "v2"),
              ("Haswell", "v2"), ("Haswell", "v3")]


def _emulated(cpu: str, path: Path) -> subprocess.CompletedProcess[bytes]:
    """Run *path* on an emulated processor of the named kind."""
    if not shutil.which("qemu-x86_64"):
        pytest.skip("qemu-x86_64 is not installed")
    return subprocess.run([*limited(), "qemu-x86_64", "-cpu", cpu, str(path)],
                          capture_output=True, timeout=60)


@pytest.mark.qemu
@pytest.mark.parametrize(("cpu", "level"), OLD_ENOUGH)
def test_a_processor_that_cannot_run_it_is_told_so(built: dict[str, Path],
                                                   cpu: str, level: str) -> None:
    """The whole point: a clear line of text, not an illegal instruction.

    And a status out of the range the runtime reserves, with a number of its own
    within it: this is the one stop that happens before the program has run at
    all, and what to do about it -- build for an older level, or find a newer
    machine -- is a different thing to do.
    """
    proc = _emulated(cpu, built[level])
    assert proc.returncode == statuses.WRONG_PROCESSOR, describe(proc)
    assert b"built for x86-64-" in proc.stderr, proc.stderr
    assert level.encode("ascii") in proc.stderr, proc.stderr
    assert proc.stdout == b""


@pytest.mark.qemu
@pytest.mark.parametrize(("cpu", "level"), NEW_ENOUGH)
def test_a_processor_that_can_run_it_does(built: dict[str, Path],
                                          cpu: str, level: str) -> None:
    """The other half: the check lets through what it should.

    What is asked of the error output is that the program said nothing on it,
    not that it is empty: the emulator writes its own warnings there about
    features it does not implement, which is its business and not the
    program's.
    """
    proc = _emulated(cpu, built[level])
    assert proc.returncode == 42, describe(proc)
    assert b"built for x86-64-" not in proc.stderr, proc.stderr
