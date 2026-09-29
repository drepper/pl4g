"""Walking the stack: what a fault says about how the program got there.

Every case here is compiled and run, and what is checked is the bytes the
program wrote to standard error -- the one thing a stack walk is for being what
a person reads.  The programs are written so that nothing folds the fault away:
the value that overflows comes out of a variable, which the compiler does not
know.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for

ARROW = "\N{RIGHTWARDS ARROW}"
TIMES = "\N{MULTIPLICATION SIGN}"

#: A fault four frames down, with nothing inlined: every function is called
#: from exactly one place, which is what would otherwise make the inliner put
#: each of them where it was called and leave one frame.
DEEP = """\
let factor: u64 = 4294967296u64

@[inline(never)]
fn inner(a: u64) {0} u64:
    a {1} factor

@[inline(never)]
fn middle(a: u64) {0} u64:
    inner(a) + 1u64

@[inline(never)]
fn outer(a: u64) {0} u64:
    middle(a) + 2u64

@[startup, impure]
fn main() {0} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(outer(factor), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""".format(ARROW, TIMES)

#: The same fault with nothing above it, which is what most programs are.
SHALLOW = """\
let factor: u64 = 4294967296u64

@[startup, impure]
fn main() {0} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(factor {1} factor, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""".format(ARROW, TIMES)

#: A program that cannot fault at all, which should carry none of this.
QUIET = """\
@[startup]
fn main() {0} u6:
    0u6
""".format(ARROW)


def ran(source: str, triple: str, tmp_path, *extra: str):  # noqa: ANN001, ANN201
    """Compile *source* for *triple*, run it, and answer with what it said."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    out = tmp_path / "out"
    proc = run_compiler(["-o", str(out), "".join(("--target=", triple)), *extra,
                         str(path)])
    assert proc.returncode == 0, describe(proc)
    return subprocess.run([*runner_for(triple), str(out)], capture_output=True,
                          text=True, timeout=60)


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_fault_says_who_called(triple: str, tmp_path) -> None:  # noqa: ANN001
    """One line per frame, outwards, each naming the function it stands for.

    The innermost is not among them: the message says where the fault was
    already, and saying it twice would be saying it twice.
    """
    said = ran(DEEP, triple, tmp_path).stderr
    assert "multiplication that does not fit in 'inner'" in said, said
    assert said.splitlines()[1:] == ["  called from 'middle'",
                                     "  called from 'outer'",
                                     "  called from 'main'"], said


@pytest.mark.parametrize("triple", compiler_targets())
def test_it_stops_where_the_program_was_started(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The entry point is in no function of the program, so the walk that
    reaches it finds no row and stops -- rather than following whatever the
    system left on the stack below."""
    said = ran(SHALLOW, triple, tmp_path).stderr
    assert said.splitlines()[1:] == [], said
    assert "in 'main'" in said


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_program_still_stops_the_way_it_did(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Walking is something said on the way out and not a change to the way
    out: the status is the one the kind of fault has, as before."""
    from pypl4g.target import statuses

    assert ran(DEEP, triple, tmp_path).returncode == statuses.OVERFLOW


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_cannot_fault_carries_none_of_it(triple: str,
                                                        tmp_path) -> None:  # noqa: ANN001
    """The table and the walk are emitted where the helper a fault leaves
    through is, which is where something can fault -- so a program that cannot
    pays for neither."""
    path = tmp_path / "t.pl4g"
    path.write_text(QUIET, encoding="utf-8")
    out = tmp_path / "t.asm"
    proc = run_compiler(["-o", str(out), "--emit=asm",
                         "".join(("--target=", triple)), str(path)])
    assert proc.returncode == 0, describe(proc)
    text = out.read_text(encoding="utf-8")
    assert "__pl4g_backtrace" not in text, text
    assert "__pl4g_frames" not in text, text


@pytest.mark.parametrize("triple", compiler_targets())
def test_what_was_inlined_is_not_a_frame(triple: str, tmp_path) -> None:  # noqa: ANN001
    """A function put where it was called is not on the stack, and the walk
    says what is on the stack rather than what the program wrote.

    The same four functions without the attribute that holds the inliner off:
    every one of them is called once, so every one is copied into its caller and
    what is left is `main`.
    """
    said = ran(DEEP.replace("@[inline(never)]\n", ""), triple, tmp_path,
               "-O1").stderr
    assert "in 'main'" in said, said
    assert said.splitlines()[1:] == [], said
