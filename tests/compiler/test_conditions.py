"""A condition made of parts is branched on a part at a time.

The checker writes `a ∧ b` as two truth values joined with `and.bool`, or -- where
`b` waits for `a` -- as a block that answers `false` and one that works `b` out,
meeting in a block that branches on what they answered.  At `-O1` the branch is
on each part, and neither the joined value nor the meeting block is left.
"""

from __future__ import annotations

from conftest import describe, function_ir

#: Both shapes, a branch and an answer.
PROGRAM = """\
unit \N{CURRENCY SIGN}size \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}idx

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[inline(never)]
fn joined(a: u64, b: u64) \N{RIGHTWARDS ARROW} u64:
    if a < 10u64 \N{LOGICAL AND} \N{NOT SIGN}(b > 3u64):
        return 1u64
    2u64

@[inline(never)]
fn waits(bytes: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, k: u64 \N{CURRENCY SIGN}idx) \N{RIGHTWARDS ARROW} u64:
    let n: u64 \N{CURRENCY SIGN}idx = #bytes
    if k \N{GREATER-THAN OR EQUAL TO} n or bytes\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}k\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = 9u8:
        return 1u64
    2u64

@[inline(never)]
fn answered(a: u64, b: u64) \N{RIGHTWARDS ARROW} bool:
    a < 10u64 \N{LOGICAL AND} b > 3u64

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let n: u64 = \N{APL FUNCTIONAL SYMBOL QUAD}drop(#init.args)
    let b: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{APL FUNCTIONAL SYMBOL QUAD}bytes(init.args\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
    let r: u64 = joined(n, n) + waits(b, \N{APL FUNCTIONAL SYMBOL QUAD}unit(n, \N{TOP LEFT CORNER}idx\N{TOP RIGHT CORNER}))
    if answered(n, r): 0u6 else: 1u6
"""


def _emitted(compile_source, *extra: str) -> str:  # noqa: ANN001
    """The IR of the program, compiled with *extra*."""
    proc, output = compile_source(PROGRAM, "--emit=ir", *extra)
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def test_a_branch_is_on_each_part(compile_source) -> None:  # noqa: ANN001
    """Two comparisons, two branches, and nothing joining them."""
    body = function_ir(_emitted(compile_source, "-O1"), "joined")
    assert "and.bool" not in body and "xor.bool" not in body
    assert body.count("condbr") == 2 and body.count("icmp") == 2


def test_the_block_where_the_ways_met_is_gone_through(compile_source) -> None:  # noqa: ANN001
    """The arm answering `true` goes where `true` goes, and the arm reading the
    element branches on what it read."""
    body = function_ir(_emitted(compile_source, "-O1"), "waits")
    assert "bool" not in body
    assert body.count("condbr") == 2


def test_an_answer_is_still_a_truth_value(compile_source) -> None:  # noqa: ANN001
    """Nothing branches on what is answered, so it is left as it was made."""
    body = function_ir(_emitted(compile_source, "-O1"), "answered")
    assert "and.bool" in body


def test_nothing_is_done_where_nothing_was_asked_for(compile_source) -> None:  # noqa: ANN001
    """At `-O0` the condition is what the checker wrote."""
    body = function_ir(_emitted(compile_source), "joined")
    assert "and.bool" in body
