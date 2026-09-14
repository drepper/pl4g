"""Calling a function: what reaches the callee, and what survives the call.

The parts worth testing are the ones nothing else in the compiler had needed
before.  An argument has to arrive where the callee looks for it.  A value the
caller still wants has to be somewhere the call cannot destroy.  A function that
takes a register the convention says it must hand back has to hand it back.  And
on two of the three architectures the return address is in a register, which the
next call overwrites.

Each of those is a thing that, got wrong, produces a program that runs and gives
the wrong answer -- so each is compiled and run rather than read.
"""

import subprocess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for

ARROW = "\N{RIGHTWARDS ARROW}"
ASSIGN = "\N{LEFTWARDS ARROW}"
SAT_ADD = "\N{SQUARED PLUS}"
SAT_SUB = "\N{SQUARED MINUS}"


def run_it(tmp_path, triple: str, source: str) -> int:  # noqa: ANN001
    """Compile *source* for *triple*, run it, and return its status."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)), str(path)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for(triple), str(output)],
                         capture_output=True, timeout=60)
    assert ran.returncode >= 0, describe(ran)
    return ran.returncode


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_argument_arrives_where_the_callee_looks_for_it(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The caller writes the register the convention names and the callee reads
    it, and both take its width from the type, which is what makes them agree."""
    assert run_it(tmp_path, triple, "".join((
        "fn twice(n: u8) ", ARROW, " u8:\n    n ", SAT_ADD, " n\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    twice(21u8)\n"))) == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_several_arguments_keep_their_order(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Positional means the place decides, so an operation that is not
    symmetric is what shows the order arrived intact."""
    assert run_it(tmp_path, triple, "".join((
        "fn difference(a: u8, b: u8) ", ARROW, " u8:\n    a ", SAT_SUB, " b\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    difference(50u8, 8u8)\n"))) == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_value_survives_a_call(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The first answer is still wanted after the second call, so it cannot be
    in a register the call destroys.  Without the call saying which registers
    those are, this runs and gives the wrong number."""
    assert run_it(tmp_path, triple, "".join((
        "fn twice(n: u8) ", ARROW, " u8:\n    n ", SAT_ADD, " n\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    twice(20u8) ", SAT_ADD, " twice(1u8)\n"))) == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_callee_hands_back_what_it_was_lent(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The middle function holds a value across a call of its own, so it takes
    a register its caller is also using.  It has to put it back."""
    assert run_it(tmp_path, triple, "".join((
        "fn one(n: u8) ", ARROW, " u8:\n    n ", SAT_ADD, " 1u8\n\n",
        "fn two(n: u8) ", ARROW, " u8:\n    one(n) ", SAT_ADD, " one(n)\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    two(10u8) ", SAT_ADD, " two(9u8)\n"))) == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_caller_keeps_its_own_way_back(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Two of these architectures leave the return address in a register, which
    the next call overwrites.  A function three deep is what shows it: without
    saving it, the middle one returns to itself and the program never stops."""
    assert run_it(tmp_path, triple, "".join((
        "fn inner(n: u8) ", ARROW, " u8:\n    n ", SAT_ADD, " 1u8\n\n",
        "fn middle(n: u8) ", ARROW, " u8:\n    inner(n)\n\n",
        "fn outer(n: u8) ", ARROW, " u8:\n    middle(n)\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    outer(41u8)\n"))) == 42


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_call_that_answers_with_nothing_still_happens(triple: str, tmp_path) -> None:  # noqa: ANN001
    """It is the effect that was wanted, not the answer."""
    assert run_it(tmp_path, triple, "".join((
        "let counter: mut u8 = 0u8\n\n",
        "@[impure]\nfn prepare():\n    counter ", ASSIGN, " 42u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    prepare()\n    counter\n"))) == 42


# -- what it is checked against -------------------------------------------------

def refuses(tmp_path, source: str, number: int) -> None:  # noqa: ANN001
    """Compile *source* and require that it is refused with *number*."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), str(path)])
    assert proc.returncode != 0, describe(proc)
    assert "".join(("[PL4G-", str(number), "]")) in proc.stderr, describe(proc)


def test_the_arguments_have_to_match_in_number(tmp_path) -> None:  # noqa: ANN001
    """Nothing is variadic and nothing has a default."""
    refuses(tmp_path, "".join((
        "fn twice(n: u8) ", ARROW, " u8:\n    n ", SAT_ADD, " n\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    twice()\n")), 4211)


def test_the_arguments_have_to_match_in_type(tmp_path) -> None:  # noqa: ANN001
    """A parameter's type is what its argument is checked against, the same way
    a variable's declared type checks its initializer."""
    refuses(tmp_path, "".join((
        "fn twice(n: u8) ", ARROW, " u8:\n    n ", SAT_ADD, " n\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    twice(1u16)\n")), 4213)


def test_only_a_function_can_be_called(tmp_path) -> None:  # noqa: ANN001
    """A variable holds a value, which is not something to hand arguments to."""
    refuses(tmp_path, "".join((
        "let counter: u8 = 3u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    counter(1u8)\n")), 4210)


def test_a_pure_call_nothing_reads_is_not_made(compile_source) -> None:  # noqa: ANN001
    """The optimization `_ \N{LEFTWARDS ARROW} f()` exists for: the answer goes nowhere, so the call does.

    A function that only works out an answer may be called once, twice or not at
    all without anything being able to tell, which is what purity says; so where
    nothing reads what it answered, not calling it is what the program means.
    """
    proc, output = compile_source(
        "fn worked_out(n: u8) \N{RIGHTWARDS ARROW} u8:\n    n + n\n\n"
        "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u8:\n"
        "    _ \N{LEFTWARDS ARROW} worked_out(3u8)\n    0u8\n",
        "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "call" not in text, text


def test_an_impure_call_nothing_reads_is_still_made(compile_source) -> None:  # noqa: ANN001
    """What it may change is what keeps it, whoever wants its answer."""
    proc, output = compile_source(
        "let seen: mut u8 = 0u8\n\n"
        "@[impure]\nfn notes(n: u8) \N{RIGHTWARDS ARROW} u8:\n"
        "    seen \N{LEFTWARDS ARROW} n\n    n\n\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u8:\n"
        "    _ \N{LEFTWARDS ARROW} notes(3u8)\n    seen\n",
        "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "call" in text, text
