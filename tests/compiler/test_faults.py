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

#: What a program the runtime stopped exits with: the general one of the range
#: reserved for a stop it reports.  A status and not a signal, so that a caller
#: can tell it apart both from a program that chose to fail and from one that
#: really did die of a signal.
STOPPED = statuses.GENERAL

OVERFLOWS = "".join((
    "let two_hundred: u8 = 200u8\nlet one_hundred: u8 = 100u8\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
    "    two_hundred + one_hundred\n"))

FITS = "".join((
    "let twenty: u8 = 20u8\nlet three: u8 = 3u8\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
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
    path.write_text("".join(("@[startup, impure]\nfn main() ", ARROW, " u8:\n    0u8\n")),
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
        "let a: u8 = 200u8\nlet b: u8 = 100u8\n\n",
        "@[visible]\nlet first: mut u8 = 0u8\n",
        "@[visible]\nlet second: mut u8 = 0u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    first \N{LEFTWARDS ARROW} a + b\n",
        "    second \N{LEFTWARDS ARROW} a + b\n    0u8\n")), encoding="utf-8")
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
