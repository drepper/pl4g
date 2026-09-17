"""The stack a program makes for itself, and the guard below it.

A program does not run on the stack the kernel gave it.  Before anything else,
its entry point asks the runtime for one of the size it was built with, with a
guard below that which may not be touched at all, and moves the stack pointer
onto it.  Running off the bottom is then a fault in the guard, which a handler
recognizes and reports as a status of its own rather than as a signal.

Why not the kernel's stack: its size is the loader's business and a `ulimit`
away from being something else, its guard is one page, and what happens when it
is exhausted is a SIGSEGV indistinguishable from following a bad address.  A
generator emitting programs wants to say how much stack one needs and to be told
apart when it needs more, and neither is something to leave to the environment
the program is started in.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import elfcheck
from pypl4g.target import statuses
from conftest import (architecture_of, check_conformance, compiler_targets,
                      describe, run_compiler, runner_for)

SOURCE = """\N{REFERENCE MARK} A program that exits with status 0.
@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    0
"""

#: The name the runtime knows the call by, which every entry point makes.
MAKES_STACK = "pl4g_stack"

STRACE = "strace"


def _build(tmp_path: Path, name: str, *options: str,
           triple: str | None = None) -> Path:
    """Compile the program above with *options* and answer the image."""
    source = tmp_path / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / name
    arguments = ["-o", str(output), "-O1", *options]
    if triple is not None:
        arguments.append("".join(("--target=", triple)))
    proc = run_compiler([*arguments, str(source)])
    assert proc.returncode == 0, describe(proc)
    return output


def _stack_segment(path: Path) -> elfcheck.Segment:
    """The header that says what stack the program wants."""
    image = elfcheck.parse(path.read_bytes())
    return next(s for s in image.segments if s.p_type == elfcheck.PT_GNU_STACK)


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_header_says_how_large_a_stack_the_program_wants(
        triple: str, tmp_path: Path) -> None:
    """PT_GNU_STACK carries the size, which is what that field is for.

    The program maps its own stack and does not depend on the one the loader
    supplies, so this is not what makes it work.  It is filled in because a
    reader of the image -- a loader, or somebody asking what a program needs --
    finds the number in the place the format keeps it, and because a program
    whose own mapping failed is then left on a stack of the size it asked for.
    """
    path = _build(tmp_path, "sized", "--stack-size=2M", triple=triple)
    segment = _stack_segment(path)
    assert segment.p_memsz == 2 << 20
    assert segment.p_flags == elfcheck.PF_R | elfcheck.PF_W
    check_conformance(path)


@pytest.mark.parametrize(("written", "bytes_"), [
    ("4096", 4096), ("64k", 1 << 16), ("64K", 1 << 16),
    ("1m", 1 << 20), ("1G", 1 << 30), ("1048576", 1 << 20)])
def test_a_size_may_be_written_with_a_letter(written: str, bytes_: int,
                                             tmp_path: Path) -> None:
    """K, M and G, and powers of 1024 rather than of 1000.

    What is being asked for is room in memory, which is counted in pages and
    never in thousands; a stack of 1000000 bytes would be an odd thing to ask
    for and an odd thing to mean by 1M.
    """
    assert _stack_segment(
        _build(tmp_path, written, "".join(("--stack-size=", written)))
    ).p_memsz == bytes_


@pytest.mark.parametrize("option", ["--stack-size", "--guard-size"])
@pytest.mark.parametrize("given", ["big", "1MB", "-1", "1.5M", ""])
def test_a_size_that_is_not_one_is_refused(option: str, given: str,
                                           tmp_path: Path) -> None:
    """Silently taking a default instead would build the wrong program."""
    source = tmp_path / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join((option, "=", given)), str(source)])
    assert proc.returncode != 0, describe(proc)
    assert "[PL4G-1016]" in proc.stderr, describe(proc)
    assert option in proc.stderr


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_entry_point_asks_the_runtime_for_it(triple: str,
                                                 tmp_path: Path) -> None:
    """One call, before anything of the program runs, and the runtime carried.

    The call is written at the entry point rather than being something the
    program reaches, so what pulls the runtime into the image is the entry
    point asking for it.  Nothing else here does: the program is a `return 0`.
    """
    output = tmp_path / "out.asm"
    source = tmp_path / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(output), "--emit=asm", "-O1",
                         "".join(("--target=", triple)), str(source)])
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    entry = text[text.index("_start:"):]
    called = re.search(r"^\s+\S.*?(?:call|bl|jal) (\S+)", entry, re.MULTILINE)
    assert called is not None and called.group(1) == MAKES_STACK, entry[:600]
    assert "".join((MAKES_STACK, ":")) in text, "the runtime was not placed"


@pytest.mark.parametrize("triple", compiler_targets())
def test_asking_for_none_leaves_the_program_where_it_started(
        triple: str, tmp_path: Path) -> None:
    """--stack-size=0 is the way out, and it costs nothing at all.

    A program that says it wants no stack of its own stays on the one the kernel
    supplied, makes no call, and carries none of the runtime -- which is what
    makes it the smallest image the compiler can produce, and is why the tests
    that are about the size of an image build this way.
    """
    path = _build(tmp_path, "bare", "--stack-size=0", triple=triple)
    assert _stack_segment(path).p_memsz == 0
    image = elfcheck.parse(path.read_bytes())
    assert [s.name for s in image.sections if s.name.startswith(".pl4grt")] == []
    runner = runner_for(triple)
    if runner and not shutil.which(runner[0]):
        pytest.skip("".join((runner[0], " is not installed")))
    proc = subprocess.run([*runner, str(path)], capture_output=True, timeout=60)
    assert proc.returncode == 0, describe(proc)


@pytest.mark.skipif(not shutil.which(STRACE), reason="strace is not installed")
def test_the_sizes_asked_for_are_the_ones_mapped(tmp_path: Path) -> None:
    """What the program actually does, watched from outside it.

    Everything above reads the image; this reads the system calls.  One mapping
    of the guard and the stack together that may not be touched, then the stack
    part of it made readable and writable -- so the guard is what is left, and
    it is unreachable because nothing ever made it otherwise.  Then a stack for
    the handler and the handler itself, because a fault in the guard has to be
    caught somewhere there is room to catch it.

    Only for the machine this runs on: an emulator's own mappings are its
    business and would be in the way.
    """
    stack, guard = 256 << 10, 8 << 10
    path = _build(tmp_path, "watched",
                  "".join(("--stack-size=", str(stack))),
                  "".join(("--guard-size=", str(guard))),
                  triple="".join((platform.machine(), "-linux-none")))
    proc = subprocess.run(
        [STRACE, "-e", "trace=mmap,mprotect,sigaltstack,rt_sigaction", str(path)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, describe(proc)
    lines = proc.stderr.splitlines()
    whole = re.search(
        r"".join((r"^mmap\(NULL, ", str(stack + guard),
                  r", PROT_NONE,.*\) = 0x([0-9a-f]+)")),
        proc.stderr, re.MULTILINE)
    assert whole is not None, proc.stderr
    opened = re.search(
        r"".join((r"^mprotect\(0x([0-9a-f]+), ", str(stack),
                  r", PROT_READ\|PROT_WRITE\) = 0")),
        proc.stderr, re.MULTILINE)
    assert opened is not None, proc.stderr
    assert int(opened.group(1), 16) == int(whole.group(1), 16) + guard, \
        "the part made writable is not the part above the guard"
    assert any(line.startswith("sigaltstack(") for line in lines), proc.stderr
    assert any(line.startswith("rt_sigaction(SIGSEGV") and "SA_ONSTACK" in line
               and "SA_SIGINFO" in line for line in lines), proc.stderr


def test_the_status_is_the_one_the_runtime_reserves() -> None:
    """The number is in two languages and has to be the same number.

    The handler is compiled from C and carried by the compiler; everything else
    that names a reserved status is Python.  Neither can see the other, so the
    one place they meet is checked here.
    """
    source = (Path(__file__).resolve().parents[2] / "runtime" / "io.c") \
        .read_text(encoding="utf-8")
    found = re.search(r"#define\s+PL4G_STACK_OVERFLOW\s+(\d+)", source)
    assert found is not None, "runtime/io.c does not define the status"
    assert int(found.group(1)) == statuses.STACK_OVERFLOW
