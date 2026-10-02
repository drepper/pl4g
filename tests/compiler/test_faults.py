"""What a program does when an answer will not fit.

Every arithmetic operation but the saturating three stops the program where its
answer would not fit the type.  These check the three things a person relies on
when that happens: that it *does* stop, that what it says is true and says where
to look, and that a program with nothing that can fault carries none of the
machinery.
"""

from __future__ import annotations

import subprocess

import pytest

from pypl4g.target import statuses
from conftest import (architecture_of, compiler_targets, describe, run_compiler,
                      runner_for)

ARROW = "\N{RIGHTWARDS ARROW}"
TIMES = "\N{MULTIPLICATION SIGN}"

#: What a program stopped for an answer that will not fit exits with.  A status
#: and not a signal, so that a caller can tell it apart both from a program that
#: chose to fail and from one that really did die of a signal -- and its own
#: number rather than a general one, so that a caller can tell *which* of the
#: things the runtime stops a program for happened.
STOPPED = statuses.OVERFLOW

OVERFLOWS = "".join((
    "let fifty: u6 = 50u6\nlet forty: u6 = 40u6\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u6:\n",
    "    fifty + forty\n"))

FITS = "".join((
    "let twenty: u6 = 20u6\nlet three: u6 = 3u6\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u6:\n",
    "    twenty ", TIMES, " three\n"))


def compile_and_run(tmp_path, triple: str, source: str):  # noqa: ANN001, ANN201
    """Compile *source* for *triple* and run it, however that is done."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)), str(path)])
    assert proc.returncode == 0, describe(proc)
    runner = runner_for(triple)
    return subprocess.run([*runner, str(output)], capture_output=True, timeout=60)


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_answer_that_does_not_fit_stops_the_program(triple: str, tmp_path) -> None:  # noqa: ANN001
    """And stops it the same way on every target, which is what lets a program
    mean one thing wherever it is compiled."""
    proc = compile_and_run(tmp_path, triple, OVERFLOWS)
    assert proc.returncode == STOPPED, describe(proc)


@pytest.mark.parametrize("triple", compiler_targets())
def test_it_says_what_happened_and_where(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The message is built whole at compile time, so what runs at the moment of
    the fault is a write and a trap -- nothing that could itself fail."""
    proc = compile_and_run(tmp_path, triple, OVERFLOWS)
    said = proc.stderr.decode("utf-8", "replace")
    assert "addition that does not fit" in said, said
    assert "in 'main'" in said, said
    # The place, in the form the compiler's own diagnostics use.
    assert "t.pl4g:6:5:" in said, said


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_answer_that_fits_says_nothing(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The check is a branch that is not taken, and nothing else."""
    proc = compile_and_run(tmp_path, triple, FITS)
    assert proc.returncode == 60, describe(proc)
    assert proc.stderr == b"", proc.stderr


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_cannot_fault_carries_nothing(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The helper and the messages are emitted only where something asked for
    them, so a program with no arithmetic in it is the size it always was."""
    path = tmp_path / "t.pl4g"
    path.write_text("".join(("@[startup, impure]\nfn main() ", ARROW, " u6:\n    0u6\n")),
                    encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)), str(path)])
    assert proc.returncode == 0, describe(proc)
    assert b"__pl4g_abort" not in output.read_bytes()


@pytest.mark.parametrize("triple", compiler_targets())
def test_each_fault_names_its_own_place(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Two operations that can fault carry two messages, each saying where it
    is -- which is the whole reason the message is built at compile time rather
    than assembled from parts at the moment of the fault."""
    path = tmp_path / "t.pl4g"
    path.write_text("".join((
        "let a: u6 = 50u6\nlet b: u6 = 40u6\n\n",
        "@[visible]\nlet first: mut u6 = 0u6\n",
        "@[visible]\nlet second: mut u6 = 0u6\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u6:\n",
        "    first \N{LEFTWARDS ARROW} a + b\n",
        "    second \N{LEFTWARDS ARROW} a + b\n    0u6\n")), encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)), str(path)])
    assert proc.returncode == 0, describe(proc)
    image = output.read_bytes()
    assert b"t.pl4g:11:13" in image, "the first fault does not say where it is"
    assert b"t.pl4g:12:14" in image, "the second fault does not say where it is"


@pytest.mark.parametrize("triple", compiler_targets())
def test_one_wording_is_kept_once(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Two faults whose messages read the same share one string, which matters
    for a language that will have a check on every arithmetic operation."""
    from pypl4g.target.faults import Messages

    messages = Messages()
    first = messages.symbol("the same words\n")
    second = messages.symbol("the same words\n")
    assert first == second
    assert len(messages.by_text) == 1
    del triple, tmp_path


del architecture_of


# -- one number per kind of stop ------------------------------------------------

#: A program per kind, each written so that nothing settles it while compiling.
#: What is checked is the status, the message being checked where the message is
#: the subject.
KINDS = {
    statuses.OVERFLOW: """\
let fifty: u6 = 50u6
let forty: u6 = 40u6

@[startup]
fn main() → u6:
    fifty + forty
""",
    statuses.NOT_A_CODE_POINT: """\
let past: u32 = 1114112u32

@[startup]
fn main() → u6:
    ⎕narrow(⎕ord(⎕chr(past)), ⌜u6⌝) ?? 1u6
""",
    statuses.OUT_OF_RANGE: """\
@[startup]
fn main() → u6:
    let row: u6⟦3⟧ = ⟦1u6, 2u6, 3u6⟧ in ⎕static
    let at: u6 ¤idx = 7
    row⟦at⟧
""",
}


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize("status", sorted(KINDS),
                         ids=[str(s) for s in sorted(KINDS)])
def test_each_kind_of_stop_has_its_own_number(triple: str, status: int,
                                              tmp_path) -> None:  # noqa: ANN001
    """A message is for a person and a status is for a program: a caller that
    wants to act on what happened is reading the number."""
    assert compile_and_run(tmp_path, triple, KINDS[status]).returncode == status


def test_none_of_them_is_a_number_sysexits_took() -> None:
    """`<sysexits.h>` has named 64 through 78 since 4.0BSD and a great deal of
    software reads them.  A runtime that stopped a program with one of those
    would be saying something it does not mean to everything that knows the
    convention."""
    every = [statuses.GENERAL, statuses.OVERFLOW, statuses.NO_ANSWER,
             statuses.OUT_OF_RANGE, statuses.NOT_A_CODE_POINT,
             statuses.WALK_ENDED, statuses.OUT_OF_MEMORY,
             statuses.STACK_OVERFLOW, statuses.WRONG_PROCESSOR,
             statuses.TESTS_FAILED]
    assert min(every) > statuses.SYSEXITS_LAST
    assert max(every) <= statuses.LAST
    assert len(set(every)) == len(every), "two kinds share a number"


def test_and_none_of_them_is_one_a_program_can_answer_with() -> None:
    """The startup function answers a `u6`, so the two ranges cannot meet: a
    caller reading a reserved status knows the program did not choose it."""
    assert statuses.FIRST > 63
