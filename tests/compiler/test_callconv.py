"""Calling conventions: one per function, and what a caller has to save.

The specification says a convention need not match the system's and may differ
between the functions of one compilation.  Two things follow, and both are here:
a call is placed by the *callee's* convention rather than the caller's, and what
a caller has to keep out of a register across a call is what the callee turned
out to destroy rather than everything its convention allows it to.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for

ARROW = "\N{RIGHTWARDS ARROW}"


def compile_it(tmp_path, triple: str, source: str, *extra: str):  # noqa: ANN001, ANN201
    """Compile *source* for *triple* and return where the image went."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)),
                         *extra, str(path)])
    assert proc.returncode == 0, describe(proc)
    return output


def run_it(tmp_path, triple: str, source: str) -> int:  # noqa: ANN001
    """Compile *source* for *triple*, run it, and return its status."""
    output = compile_it(tmp_path, triple, source)
    ran = subprocess.run([*runner_for(triple), str(output)],
                         capture_output=True, timeout=60)
    assert ran.returncode >= 0, describe(ran)
    return ran.returncode


def listing(tmp_path, triple: str, source: str, *extra: str) -> str:  # noqa: ANN001
    """The assembler dump of *source*, for reading what was generated."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    proc = run_compiler(["--emit=asm", "-o", str(tmp_path / "out.s"),
                         "".join(("--target=", triple)), *extra, str(path)])
    assert proc.returncode == 0, describe(proc)
    return (tmp_path / "out.s").read_text(encoding="utf-8")


def body_of(dump: str, name: str) -> list[str]:
    """The mnemonics of one function of a dump, in order."""
    found: list[str] = []
    inside = False
    for line in dump.splitlines():
        if line.startswith("".join((name, ":"))):
            inside = True
            continue
        if inside:
            if line and not line.startswith(" "):
                break
            words = line.split()
            # Every line of the dump is bytes and then the instruction, and a
            # line with no bytes is an alignment note rather than an
            # instruction.
            mnemonic = next((w for w in words if not _is_byte(w)), None)
            if mnemonic is not None and mnemonic != "\N{REFERENCE MARK}":
                found.append(mnemonic)
    return found


def _is_byte(word: str) -> bool:
    """Whether a word of the dump is one of the bytes rather than the text."""
    return len(word) == 2 and all(c in "0123456789abcdef" for c in word)


# -- what the language's own convention buys ------------------------------------

#: The function the whole arrangement is for: it answers with what it was given.
IDENTITY = "".join(("fn f(p: u8) ", ARROW, " u8:\n    p\n\n",
                    "@[startup, impure]\nfn main() ", ARROW, " u8:\n    f(42u8)\n"))


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_function_that_answers_with_what_it_was_given_is_a_bare_return(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """The whole of `fn f(p) = p`.

    The convention passes the argument in the register the answer comes back
    in, so the value is already where it has to be and there is nothing to do.
    Getting there needs two things besides the convention: the hint has to be
    taken, and the register the argument arrives in has to be seen as free once
    the value has been read out of it.
    """
    assert body_of(listing(tmp_path, triple, IDENTITY), "f(u8)u8") == ["ret"]


@pytest.mark.parametrize("triple", compiler_targets())
def test_and_it_runs(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Which is the half that reading the dump does not say."""
    assert run_it(tmp_path, triple, IDENTITY) == 42


# -- a call is placed by the callee's convention --------------------------------

TWO_CONVENTIONS = "".join((
    "@[cdecl]\nfn theirs(a: u8, b: u8) ", ARROW, " u8:\n    a | b\n\n",
    "fn ours(a: u8, b: u8) ", ARROW, " u8:\n    a | b\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
    "    theirs(32u8, 8u8) | ours(2u8, 0u8)\n"))


@pytest.mark.parametrize("triple", compiler_targets())
def test_two_conventions_in_one_program(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Each call puts its arguments where the function it calls looks for them."""
    assert run_it(tmp_path, triple, TWO_CONVENTIONS) == 42


def test_a_cdecl_function_keeps_its_plain_name(tmp_path) -> None:  # noqa: ANN001
    """The point of asking for the system's convention is to be called by
    something that has never heard of this language, which cannot be expected to
    know how a name is mangled."""
    dump = listing(tmp_path, "x86_64-linux-none", TWO_CONVENTIONS)
    assert "\ntheirs:\n" in dump
    assert "\nours(u8,u8)u8:\n" in dump


# -- what a caller has to save --------------------------------------------------

#: Five values read out of memory and wanted after a call, which is more than a
#: convention leaves a caller once it has given up everything a call may
#: destroy.  The callee destroys almost nothing, so nothing has to be saved.
ACROSS_A_CALL = "".join((
    "".join("".join(("let v", str(n), ": u8 = ", str(1 << n), "u8\n"))
            for n in range(5)),
    "\nfn twice(p: u8) ", ARROW, " u8:\n    p | p\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
    "    let a: u8 = v0\n    let b: u8 = v1\n    let c: u8 = v2\n",
    "    let d: u8 = v3\n    let e: u8 = v4\n",
    "    let got: u8 = twice(32u8)\n",
    "    a | b | c | d | e | got\n"))


@pytest.mark.parametrize("triple", compiler_targets())
def test_nothing_is_saved_across_a_call_that_destroys_nothing(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """A function that destroys little is one a caller has to save little around.

    What the callee turned out to destroy is recorded when it is generated and
    read where it is called, so the five values live in registers a call is
    allowed to destroy and are never written to the frame.  Asking the
    convention instead would have the caller give up every one of those
    registers, take five it has to hand back, and save and restore all five.
    """
    dump = listing(tmp_path, triple, ACROSS_A_CALL, "-O1")
    inside = dump.split("main()u8:")[1].split("\n_start")[0]
    named = _handed_back(triple)
    assert not [n for n in named if "".join((" ", n, ",")) in inside
                or "".join((" ", n, "\n")) in inside], inside


def _handed_back(triple: str) -> set[str]:
    """Every name of every register the convention says a function hands back.

    Read off the target rather than written down here, so that a convention
    that changes does not leave this test asserting something about a register
    that no longer has to be saved.
    """
    import importlib

    which = triple.split("-")[0]
    abi = importlib.import_module("".join(("pypl4g.target.", which, ".abi")))
    regs = importlib.import_module("".join(("pypl4g.target.", which, ".regs")))
    # Only the ones the allocator may give out: a stack pointer is handed back
    # too and says nothing about whether anything had to be saved.
    kept = abi.CC_PL4G.callee_saved & frozenset(abi.CC_PL4G.allocation_order)
    return {reg.name for reg in regs.INFO.registers.values() if reg.unit in kept}


@pytest.mark.parametrize("triple", compiler_targets())
def test_and_the_answer_is_right(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Which is what says the five really did survive."""
    assert run_it(tmp_path, triple, ACROSS_A_CALL) == 63
