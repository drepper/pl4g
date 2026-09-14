"""The examples build, for every target the compiler supports.

The examples are what someone reads first, so a change that breaks them should
break the testsuite rather than be found by hand.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import describe, run_compiler

pytestmark = pytest.mark.skipif(not shutil.which("make"), reason="make is not installed")


#: Every test here builds in the same directory, so they run on one worker:
#: one of them running `make clean` while another looks for what it built is a
#: failure with nothing wrong in it.
pytestmark = pytest.mark.xdist_group("examples")


@pytest.fixture(scope="module")
def examples(root: Path) -> Path:
    """The examples directory."""
    directory = root / "examples"
    if not directory.is_dir():
        pytest.skip("there are no examples")
    return directory


def run_make(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke make in *directory*."""
    return subprocess.run(["make", "--no-print-directory", *args], cwd=directory,
                          capture_output=True, text=True, timeout=300)


def triples() -> list[str]:
    """The targets the compiler reports."""
    proc = run_compiler(["--print-targets"])
    assert proc.returncode == 0, describe(proc)
    return proc.stdout.split()


def test_every_example_has_a_makefile_and_a_source(examples: Path) -> None:
    """An example is a directory holding a Makefile and the program it builds."""
    directories = [d for d in sorted(examples.iterdir())
                   if d.is_dir() and not d.name.startswith(".")]
    assert directories, "there are no example directories"
    for directory in directories:
        assert (directory / "Makefile").is_file(), directory.name
        assert list(directory.glob("*.pl4g")), directory.name


def test_building_the_examples_produces_one_binary_per_target(examples: Path) -> None:
    """Every example builds for every target, and each binary really runs."""
    assert run_make(examples, "clean").returncode == 0
    built = run_make(examples)
    assert built.returncode == 0, describe(built)
    for directory in sorted(p for p in examples.iterdir() if p.is_dir()):
        name = directory.name
        if name.startswith("."):
            continue
        for triple in triples():
            binary = directory / "build" / triple / name
            assert binary.is_file(), "".join(("missing ", str(binary), "\n",
                                              describe(built)))
            assert binary.stat().st_mode & 0o111, "the binary is not executable"


def test_the_run_target_reports_a_status_for_every_target(examples: Path) -> None:
    """'make run' runs each binary, through an emulator where it has to."""
    proc = run_make(examples, "run")
    assert proc.returncode == 0, describe(proc)
    for triple in triples():
        assert triple in proc.stdout, describe(proc)
    assert "exit 0" in proc.stdout, describe(proc)


def test_clean_removes_every_generated_file(examples: Path) -> None:
    """Nothing generated is left behind, so the ignore rule stays sufficient."""
    assert run_make(examples).returncode == 0
    assert run_make(examples, "clean").returncode == 0
    leftovers = [p for p in examples.rglob("*") if p.is_dir() and p.name == "build"]
    assert leftovers == []


def test_generated_binaries_are_ignored_by_git(root: Path, examples: Path) -> None:
    """The ignore rule covers the build output without naming any architecture."""
    assert run_make(examples).returncode == 0
    proc = subprocess.run(["git", "status", "--porcelain", "--ignored=no",
                           str(examples)],
                          cwd=root, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "build/" not in proc.stdout, proc.stdout
    run_make(examples, "clean")
