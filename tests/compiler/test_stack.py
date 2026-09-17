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
from pypl4g.target.stack import (ALT_STACK, GAP, HANDLER_SYMBOL,
                                 HINT_GRAIN)
from conftest import (architecture_of, check_conformance, compiler_targets,
                      describe, run_compiler, runner_for)

SOURCE = """\N{REFERENCE MARK} A program that exits with status 0.
@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    0
"""

STRACE = "strace"
SETARCH = "setarch"


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
def test_the_entry_point_makes_it_itself(triple: str, tmp_path: Path) -> None:
    """A few dozen instructions the compiler selected, and nothing carried.

    The making of the stack was compiled from C once and carried in the image;
    it is now emitted like any other code, which is what keeps a program that
    wanted nothing else from the packaged runtime from carrying any of it.  So
    what this looks for is the four calls in the entry point itself and no
    packaged object anywhere.
    """
    output = tmp_path / "out.asm"
    source = tmp_path / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    proc = run_compiler(["-o", str(output), "--emit=asm", "-O1",
                         "".join(("--target=", triple)), str(source)])
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    entry = text[text.index("_start:"):]
    for number in (MMAP[architecture_of(triple)], MPROTECT[architecture_of(triple)]):
        assert _mentions(entry, number), entry
    assert HANDLER_SYMBOL in text, "the handler was not emitted"
    assert ".pl4grt" not in text, "the packaged runtime was placed"


def _mentions(text: str, number: int) -> bool:
    """Whether the assembly *text* builds *number* as an immediate somewhere.

    Written as a search of the dump rather than of the bytes because what is
    being checked is that the call is made at all; which instruction builds the
    number differs between the three, and one of them writes it in hexadecimal.
    """
    return any(one in text for one in
               ("".join((", ", str(number))), "".join((",", str(number))),
                "".join((" ", str(number))), "".join((" 0x", format(number, "x")))))


#: The numbers of the two calls that make the stack, which differ between the
#: architectures and are the architecture's to say.
MMAP = {"x86_64": 9, "aarch64": 222, "riscv64": 222}
MPROTECT = {"x86_64": 10, "aarch64": 226, "riscv64": 226}


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
    stack, guard = 256 << 10, 64 << 10
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
        r"".join((r"^mmap\(0x[0-9a-f]+, ", str(stack + guard + ALT_STACK),
                  r", PROT_READ\|PROT_WRITE, ",
                  r"MAP_PRIVATE\|MAP_ANONYMOUS\|MAP_NORESERVE, -1, 0\)",
                  r" = 0x([0-9a-f]+)")),
        proc.stderr, re.MULTILINE)
    assert whole is not None, proc.stderr
    taken = re.search(
        r"".join((r"^mprotect\(0x([0-9a-f]+), ", str(guard),
                  r", PROT_NONE\) = 0")),
        proc.stderr, re.MULTILINE)
    assert taken is not None, proc.stderr
    assert int(taken.group(1), 16) == int(whole.group(1), 16), \
        "what was taken away is not the bottom of what was mapped"
    top = re.search(
        r"".join((r"^sigaltstack\(\{ss_sp=0x([0-9a-f]+), ss_flags=0, ss_size=",
                  str(ALT_STACK), r"\}")),
        proc.stderr, re.MULTILINE)
    assert top is not None, proc.stderr
    assert int(top.group(1), 16) == int(whole.group(1), 16) + guard + stack, \
        "the handler's stack is not above the program's"
    assert any(line.startswith("rt_sigaction(SIGSEGV") and "SA_ONSTACK" in line
               and "SA_SIGINFO" in line for line in lines), proc.stderr


@pytest.mark.skipif(not shutil.which(STRACE) or not shutil.which(SETARCH),
                    reason="strace or setarch is not installed")
def test_it_is_asked_for_below_the_stack_the_kernel_made(tmp_path: Path) -> None:
    """Where the program asks for its stack, and that the kernel gives it there.

    The address is a hint and not a demand, so a kernel that would rather put
    the mapping elsewhere does; what the hint is worth is that the program's
    stack stays in the part of the address space a stack lives in rather than
    in the middle of where mappings are handed out.

    Read with randomisation turned off, so that the two processes' stacks start
    at the same fixed address and the one seen from outside says where the one
    inside is.  With it on, every process is somewhere else and there is nothing
    to compare against.  Only for the machine this runs on, an emulator's
    address space being its own business.
    """
    path = _build(tmp_path, "placed",
                  triple="".join((platform.machine(), "-linux-none")))
    proc = subprocess.run([SETARCH, "-R", STRACE, "-e", "trace=execve,mmap",
                           str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, describe(proc)
    started = re.search(r"^execve\(.*, (0x[0-9a-f]+) /\*", proc.stderr,
                        re.MULTILINE)
    mapped = re.search(r"^mmap\((0x[0-9a-f]+), .*\) = (0x[0-9a-f]+)",
                       proc.stderr, re.MULTILINE)
    assert started is not None and mapped is not None, proc.stderr
    assert mapped.group(1) == mapped.group(2), \
        "".join(("the hint was not taken: ", proc.stderr))
    below = int(started.group(1), 16) - int(mapped.group(2), 16)
    assert 0 < below < GAP + (1 << 20) + ALT_STACK + HINT_GRAIN + (1 << 16), \
        "".join(("it is ", str(below), " below the stack:\n", proc.stderr))
