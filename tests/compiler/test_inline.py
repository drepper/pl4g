"""The call graph, the order it gives, and what is put where it was called.

Two things rest on the order: the backend generates a callee before its caller,
so that a call saves only what that callee actually destroys, and the inliner
walks the same way, so that a callee it reaches is one whose size is final.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for
from pypl4g.ir.callgraph import (called_by_count, in_a_cycle, in_call_order,
                                 size_of_body)
from pypl4g.ir.function import FuncAttrs, Function
from pypl4g.ir.inst import CallInst, RetInst
from pypl4g.ir.module import Module
from pypl4g.ir.types import U8

#: A program whose shape the inliner's decisions can be read off.
SMALL = """\
fn add(a: u8, b: u8) \N{RIGHTWARDS ARROW} u8:
    a + b

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(add(1u8, 2u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
"""


def built(module: Module, name: str, callees: list[Function]) -> Function:
    """A function of *module* that calls each of *callees* once."""
    func = module.add_function(Function(
        name, module.types.func_type((), U8), FuncAttrs()))
    block = func.add_block()
    for callee in callees:
        block.append(CallInst(callee, (), U8))
    block.append(RetInst(module.int_const(U8, 0)))
    return func


# -- the graph and the order ----------------------------------------------------

def test_a_callee_comes_before_its_caller() -> None:
    """Which is the whole of what the order is for."""
    module = Module("t")
    leaf = built(module, "leaf", [])
    middle = built(module, "middle", [leaf])
    built(module, "top", [middle])
    order = [func.name for func in in_call_order(module)]
    assert order.index("leaf") < order.index("middle") < order.index("top")


def test_every_function_comes_out_once() -> None:
    """A function two others call is generated once, not twice."""
    module = Module("t")
    leaf = built(module, "leaf", [])
    built(module, "one", [leaf])
    built(module, "other", [leaf])
    order = [func.name for func in in_call_order(module)]
    assert sorted(order) == ["leaf", "one", "other"]


def test_a_cycle_still_comes_out() -> None:
    """There is no order round a ring, and every function is still in the list.

    What the ring costs is exactness: a function of it is generated before one
    of its callees, so the caller assumes the convention's whole caller-saved
    set rather than knowing.
    """
    module = Module("t")
    first = built(module, "first", [])
    second = built(module, "second", [first])
    first.blocks[0].insts.insert(0, CallInst(second, (), U8))
    order = [func.name for func in in_call_order(module)]
    assert sorted(order) == ["first", "second"]
    ring = in_a_cycle(module)
    assert {id(first), id(second)} <= ring


def test_a_function_that_calls_itself_is_in_a_cycle() -> None:
    """The one-function ring, which is what a recursion is."""
    module = Module("t")
    alone = built(module, "alone", [])
    alone.blocks[0].insts.insert(0, CallInst(alone, (), U8))
    assert id(alone) in in_a_cycle(module)


def test_how_many_call_each_one() -> None:
    """What says a function is called once, which is what makes a copy free."""
    module = Module("t")
    leaf = built(module, "leaf", [])
    built(module, "one", [leaf])
    built(module, "other", [leaf, leaf])
    assert called_by_count(module)[id(leaf)] == 3


def test_the_size_is_the_instructions() -> None:
    """The measure the decision is made on, before anything is generated."""
    module = Module("t")
    leaf = built(module, "leaf", [])
    assert size_of_body(leaf) == 1


# -- what is inlined ------------------------------------------------------------

def emitted(compile_source, source: str, *extra: str) -> str:  # noqa: ANN001
    """The IR of *source*, compiled with the given options."""
    proc, output = compile_source(source, "--emit=ir", *extra)
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def test_a_small_callee_is_put_where_it_was_called(compile_source) -> None:  # noqa: ANN001
    """And the function it was is then dropped, nothing calling it any more."""
    text = emitted(compile_source, SMALL, "-O1")
    assert "call" not in text
    assert "fn @add" not in text


def test_nothing_is_inlined_where_nothing_was_asked_for(compile_source) -> None:  # noqa: ANN001
    """At -O0 the program is what it says it is."""
    text = emitted(compile_source, SMALL)
    assert "call" in text and "fn @add" in text


#: An answer the optimizer cannot know, so that a pure function handed it is not
#: worked out while compiling -- which would leave no call to inline or keep.
SEED = """\
@[impure, inline(never)]
fn seed() \N{RIGHTWARDS ARROW} u8:
    1u8

"""


def test_the_program_may_say_never(compile_source) -> None:  # noqa: ANN001
    """`@[inline(never)]` is a thing the program said, so nothing argues."""
    text = emitted(compile_source, SEED + """\
@[inline(never)]
fn add(a: u8, b: u8) \N{RIGHTWARDS ARROW} u8:
    a + b

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(add(seed(), 2u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""", "-O1")
    assert "call" in text and "fn @add" in text


def test_the_program_may_say_always(compile_source) -> None:  # noqa: ANN001
    """And `@[inline]` puts one in place however long it is."""
    body = "\n".join("    let n{0}: u8 = a + {0}u8".format(at)
                     for at in range(1, 20))
    text = emitted(compile_source, "".join(("""\
@[inline]
fn wide(a: u8) \N{RIGHTWARDS ARROW} u8:
""", body, """
    n1

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(wide(1u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""")), "-O1")
    assert "call" not in text


def test_one_the_whole_program_calls_once_is_put_in_place(
        compile_source) -> None:  # noqa: ANN001
    """However long it is: the copy is the only one there is, and the original
    goes with the pass that drops what nothing reaches."""
    body = "\n".join("    let n{0}: u8 = a + {0}u8".format(at)
                     for at in range(1, 20))
    text = emitted(compile_source, "".join(("""\
fn wide(a: u8) \N{RIGHTWARDS ARROW} u8:
""", body, """
    n1

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(wide(1u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""")), "-O1")
    assert "call" not in text and "fn @wide" not in text


def test_one_several_call_and_nobody_says_to_stays(compile_source) -> None:  # noqa: ANN001
    """A long one called twice is two copies, which is what the budget is for."""
    body = "\n".join("    let n{0}: u8 = a + {0}u8".format(at)
                     for at in range(1, 20))
    text = emitted(compile_source, "".join((SEED, """\
fn wide(a: u8) \N{RIGHTWARDS ARROW} u8:
""", body, """
    n1

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(wide(seed()) + wide(seed()), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""")), "-O1")
    assert "call" in text and "fn @wide" in text


def test_a_function_that_calls_itself_is_left_alone(compile_source) -> None:  # noqa: ANN001
    """Inlining round a ring does not finish, so nothing of one is inlined.

    The call is not in tail position -- a tail call to itself is a loop, and a
    loop is inlined like anything else (below)."""
    text = emitted(compile_source, SEED + """\
fn down(n: u8) \N{RIGHTWARDS ARROW} u8:
    if n = 0u8: 0u8 else: n + down(n - 1u8)

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(down(seed()), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""", "-O1")
    assert "fn @down" in text


def test_a_function_calling_itself_last_is_a_loop_and_is_inlined(
        compile_source) -> None:  # noqa: ANN001
    """Calling itself in tail position makes it a loop, and no ring is left."""
    text = emitted(compile_source, """\
fn down(n: u8) \N{RIGHTWARDS ARROW} u8:
    if n = 0u8: 0u8 else: down(n - 1u8)

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(down(3u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""", "-O1")
    assert "fn @down" not in text, text


def test_what_was_inlined_is_in_the_log(compile_source, tmp_path) -> None:  # noqa: ANN001
    """A reader asking where a function went is asking the log."""
    import json

    where = tmp_path / "log.json"
    proc, _ = compile_source(SMALL, "-O1", "".join(("--report-log=", str(where))))
    assert proc.returncode == 0, describe(proc)
    rows = json.loads(where.read_text(encoding="utf-8"))["reports"]
    kinds = {(row["kind"], row["subject"]) for row in rows}
    assert ("inline", "add") in kinds
    assert ("drop-function", "add") in kinds


# -- and that the program still does what it said -------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
def test_an_inlined_program_answers_what_it_did(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The point of every one of these: the program is the same program."""
    source = tmp_path / "t.pl4g"
    source.write_text("""\
fn add(a: u8, b: u8) \N{RIGHTWARDS ARROW} u8:
    a + b

fn twice(a: u8) \N{RIGHTWARDS ARROW} u8:
    add(a, a)

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(twice(3u8) + add(1u8, 2u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""", encoding="utf-8")
    out = tmp_path / "out"
    proc = run_compiler(["-O1", "-o", str(out), "".join(("--target=", triple)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    runner = runner_for(triple)
    if runner:
        import shutil

        if not shutil.which(runner[0]):
            pytest.skip("".join((runner[0], " is not installed")))
    ran = subprocess.run([*runner, str(out)], capture_output=True, timeout=60)
    assert ran.returncode == 9
