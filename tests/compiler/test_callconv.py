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
from pypl4g.target.registry import conventions_of

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
IDENTITY = "".join(("fn f(p: u6) ", ARROW, " u6:\n    p\n\n",
                    "@[startup, impure]\nfn main() ", ARROW, " u6:\n    f(42u6)\n"))


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
    assert body_of(listing(tmp_path, triple, IDENTITY), "f(u6)u6") == ["ret"]


@pytest.mark.parametrize("triple", compiler_targets())
def test_and_it_runs(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Which is the half that reading the dump does not say."""
    assert run_it(tmp_path, triple, IDENTITY) == 42


# -- a call is placed by the callee's convention --------------------------------

TWO_CONVENTIONS = "".join((
    "@[cdecl]\nfn theirs(a: u6, b: u6) ", ARROW, " u6:\n    a | b\n\n",
    "fn ours(a: u6, b: u6) ", ARROW, " u6:\n    a | b\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u6:\n",
    "    theirs(32u6, 8u6) | ours(2u6, 0u6)\n"))


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
    assert "\nours(u6,u6)u6:\n" in dump


# -- what a caller has to save --------------------------------------------------

#: Five values read out of memory and wanted after a call, which is more than a
#: convention leaves a caller once it has given up everything a call may
#: destroy.  The callee destroys almost nothing, so nothing has to be saved.
ACROSS_A_CALL = "".join((
    "".join("".join(("let v", str(n), ": u6 = ", str(1 << n), "u6\n"))
            for n in range(5)),
    "\nfn twice(p: u6) ", ARROW, " u6:\n    p | p\n\n",
    "@[startup, impure]\nfn main() ", ARROW, " u6:\n",
    "    let a: u6 = v0\n    let b: u6 = v1\n    let c: u6 = v2\n",
    "    let d: u6 = v3\n    let e: u6 = v4\n",
    "    let got: u6 = twice(32u6)\n",
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
    inside = dump.split("main()u6:")[1].split("\n_start")[0]
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


# -- a name the target has to know ----------------------------------------------

def refused(tmp_path, triple: str, source: str) -> str:  # noqa: ANN001
    """Compile *source* for *triple*, expect a refusal, and answer what it said."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--target=", triple)), str(path)])
    assert proc.returncode != 0, describe(proc)
    return "".join((proc.stdout, proc.stderr))


def declaring(abi: str) -> str:
    """A program whose one declaration follows the named convention."""
    return "".join(("@[abi(\"", abi, """\")]
fn elsewhere(n: u64) """, ARROW, """ u64:
    n + 1u64

@[startup, impure]
fn main() """, ARROW, """ u6:
    0u6
"""))


@pytest.mark.parametrize("triple", compiler_targets())
def test_every_target_knows_the_two_that_are_not_a_targets_to_know(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """`pl4g` is the language's own and `cdecl` is "whatever the system calls C".

    Neither is a name a target has to be asked about, which is what lets the
    standard library write one without the front end importing a target.
    """
    for name in ("pl4g", "cdecl"):
        compile_it(tmp_path, triple, declaring(name))


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_name_this_target_does_not_know_is_refused(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """Rather than quietly meaning the language's own under an unmangled name."""
    said = refused(tmp_path, triple, declaring("stdcall"))
    assert "PL4G-3210" in said and "stdcall" in said


@pytest.mark.parametrize("triple", compiler_targets())
def test_and_what_it_does_know_is_what_it_offered(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """Each of the names the target's own table holds compiles, and the refusal
    of one it does not hold names every one of them, so a reader is told what to
    write instead."""
    known = conventions_of(triple)
    assert known
    for name in known:
        compile_it(tmp_path, triple, declaring(name))
    said = refused(tmp_path, triple, declaring("stdcall"))
    assert all("".join(("'", name, "'")) in said for name in known)


def test_a_convention_of_another_target_is_still_a_name_this_one_refuses(
        tmp_path) -> None:  # noqa: ANN001
    """Which is the case the check is really for: the name is a real convention,
    and the target compiled for is one it says nothing to."""
    for triple, elsewhere in (("x86_64-linux-none", "aapcs64"),
                              ("aarch64-linux-none", "sysv64"),
                              ("riscv64-linux-none", "sysv64")):
        said = refused(tmp_path, triple, declaring(elsewhere))
        assert "PL4G-3210" in said and triple in said


def test_a_type_is_laid_out_and_not_called(tmp_path) -> None:  # noqa: ANN001
    """So a convention written on one is a name with nothing to say."""
    said = refused(tmp_path, compiler_targets()[0], """\
@[abi("cdecl")]
type Ring = state : u64 ; fd : u64

@[startup, impure]
fn main() """ + ARROW + """ u6:
    0u6
""")
    assert "PL4G-3211" in said


# -- where what a call destroys comes from --------------------------------------

def _call_rows(triple: str):  # noqa: ANN202
    """Every row of the target's instruction table that makes a call."""
    import importlib

    from pypl4g.mc.desc import InstFlags

    which = triple.split("-")[0]
    opcodes = importlib.import_module("".join(("pypl4g.target.", which, ".opcodes")))
    table = next(value for name, value in vars(opcodes).items()
                 if name.endswith("_INSTRS"))
    return [row for row in table if InstFlags.CALL in row.flags]


def _convention(triple: str):  # noqa: ANN202
    """The target's own convention, and the registers it names."""
    import importlib

    which = triple.split("-")[0]
    return importlib.import_module("".join(("pypl4g.target.", which, ".abi"))).CC_PL4G


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_table_names_no_register_a_call_destroys(triple: str) -> None:
    """Which registers a call destroys is the convention's to say, so a table
    that named them would be saying something it cannot know: two functions of
    one compilation may follow different conventions.

    What a call row may still name is what the *instruction* writes -- the link
    register, on the two architectures that have one -- and that is never a
    register the allocator hands out, which is what this asserts.
    """
    rows = _call_rows(triple)
    assert rows, "".join((triple, ": no call in the instruction table"))
    handed_out = set(_convention(triple).allocation_order)
    for row in rows:
        named = {reg.unit for reg in row.implicit_defs}
        assert not named & handed_out, \
            "".join((triple, ": ", row.mnemonic, " names ",
                     ", ".join(r.name for r in row.implicit_defs)))


@pytest.mark.parametrize("triple", compiler_targets())
def test_what_a_call_destroys_is_asked_of_the_convention(triple: str) -> None:
    """A call to something this compilation has not worked out destroys what the
    convention allows -- read off the convention that was handed in, not off a
    table."""
    import importlib

    from dataclasses import replace

    from pypl4g.target.callconv import destroyed_by

    which = triple.split("-")[0]
    regs = importlib.import_module("".join(("pypl4g.target.", which, ".regs")))
    cconv = _convention(triple)
    answered = {reg.unit for reg in destroyed_by(None, cconv, regs.INFO, None)}
    assert answered == set(cconv.caller_saved)
    # And a convention naming less is answered with less, which is what says
    # this is the convention's answer and not the instruction's.
    fewer = frozenset(list(cconv.caller_saved)[:2])
    answered = {reg.unit for reg in
                destroyed_by(None, replace(cconv, caller_saved=fewer),
                             regs.INFO, None)}
    assert answered == set(fewer)


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_callee_that_has_been_generated_is_asked_instead(triple: str) -> None:
    """The convention says what a function is *allowed* to destroy; one that has
    been generated says what it did, which is less."""
    import importlib

    from pypl4g.ir.function import FuncAttrs, Function
    from pypl4g.ir.mangle import symbol_name
    from pypl4g.ir.module import Module
    from pypl4g.ir.types import U8
    from pypl4g.target.callconv import destroyed_by

    which = triple.split("-")[0]
    regs = importlib.import_module("".join(("pypl4g.target.", which, ".regs")))
    cconv = _convention(triple)
    module = Module("t", triple=triple)
    callee = Function("quiet", module.types.func_type((), U8), FuncAttrs())
    one = next(iter(cconv.caller_saved))
    answered = destroyed_by(callee, cconv, regs.INFO,
                            {symbol_name(callee): frozenset((one,))})
    assert [reg.unit for reg in answered] == [one]
