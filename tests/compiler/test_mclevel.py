"""The microarchitecture levels, and the check a program makes of the processor.

x86-64 has meant several quite different machines over twenty-five years, and its
own documentation names four sets of features for saying which one a program was
built for.  What this compiler does with a level is let code generation use what
the level allows -- nothing does yet -- and have the program ask the processor,
at its own entry point, whether it can run at all.

The asking is done with `CPUID`, which is the instruction the architecture
provides for the question: it is answered the same way on every operating system
and it cannot be out of date about the processor the program is actually running
on, which a file the kernel writes can be.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import describe, run_compiler

SOURCE = "".join((
    "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u8:\n    42u8\n"))

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
    """They are each architecture's own, so a name from one means nothing
    to another, and an architecture that defines none has none to name."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "--target=riscv64-linux-none", "--mclevel=v2",
                         str(source)])
    assert proc.returncode != 0
    assert "PL4G-1012" in proc.stderr, proc.stderr


def test_an_architecture_without_levels_is_built_without_one(tmp_path: Path) -> None:
    """Saying nothing is not asking for anything, so it is not an error."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "--target=riscv64-linux-none", str(source)])
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
    return subprocess.run(["qemu-x86_64", "-cpu", cpu, str(path)],
                          capture_output=True, timeout=60)


@pytest.mark.qemu
@pytest.mark.parametrize(("cpu", "level"), OLD_ENOUGH)
def test_a_processor_that_cannot_run_it_is_told_so(built: dict[str, Path],
                                                   cpu: str, level: str) -> None:
    """The whole point: a clear line of text, not an illegal instruction.

    And a status rather than a signal, nothing having gone wrong inside the
    program -- it is the machine that is wrong for it.
    """
    proc = _emulated(cpu, built[level])
    assert proc.returncode == 1, describe(proc)
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
