"""What a loop over an array costs at -O1: the work the checker writes once per
mention, worked out once.

Six things a loop over a pick, or over the places of an array, used to do in
every turn and does not any more: multiply an index by an element size of one,
compare a length with itself, read the element twice, check an index the loop's
own test already checked, saturate a step that cannot overflow, and turn a
comparison into a truth value only to test that truth value.  Each is asked of
the code the compiler writes, since the program answers the same either way.
"""

from __future__ import annotations

import re

from conftest import describe

PROGRAM = """\
@[inline(never)]
fn over(v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, k: u8) \N{RIGHTWARDS ARROW} u8:
    let s: mut u8 = 0u8
    foreach x := v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}v > k\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:
        s \N{LEFTWARDS ARROW} s \N{SQUARED PLUS} x
    s

@[inline(never)]
fn placed(v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, m: bool\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}) \N{RIGHTWARDS ARROW} u8:
    let s: mut u8 = 0u8
    foreach i: u64 \N{CURRENCY SIGN}idx = 0\N{HORIZONTAL ELLIPSIS}\N{APL FUNCTIONAL SYMBOL QUAD}unit(\N{APL FUNCTIONAL SYMBOL QUAD}drop(#v), \N{TOP LEFT CORNER}idx\N{TOP RIGHT CORNER}):
        if m\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:
            s \N{LEFTWARDS ARROW} s \N{SQUARED PLUS} v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}
    s

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup, impure]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}10u8, 20u8, 30u8, 40u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    let m: bool\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}true, false, true, false\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    if over(v, 25u8) + placed(v, m) = 110u8: 0u6 else: 1u6
"""


def _function(text: str, name: str) -> str:
    """The body of one function of the textual form."""
    found = re.search("".join((r"fn @", name, r"\(.*?\n}")), text, re.S)
    assert found is not None, text
    return found.group(0)


def _ir(compile_source) -> str:  # noqa: ANN001, ANN202
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def test_an_index_is_not_multiplied_by_one(compile_source) -> None:  # noqa: ANN001
    """The address of a byte is the place plus the index."""
    text = _ir(compile_source)
    assert not re.search(r"mul\.u64 %\d+, 1$", text, re.M), text


def test_a_length_is_not_compared_with_itself(compile_source) -> None:  # noqa: ANN001
    """`v⟦v > k⟧` checks `v` against itself, which always holds: no check is left."""
    over = _function(_ir(compile_source), "over")
    assert "assert" not in over and "icmp.eq" not in over, over


def test_the_element_is_read_once(compile_source) -> None:  # noqa: ANN001
    """The mask reads `v⟦i⟧` and so does the turn: one read serves both."""
    over = _function(_ir(compile_source), "over")
    assert len(re.findall(r"= load\.u8", over)) == 1, over


def test_an_index_the_loop_checked_is_not_checked_again(compile_source) -> None:  # noqa: ANN001
    """`i < #v` is the loop's test, so `v⟦i⟧` needs none; `m⟦i⟧` still does."""
    placed = _function(_ir(compile_source), "placed")
    assert len(re.findall(r"^\s+assert ", placed, re.M)) == 1, placed


def test_a_step_that_cannot_overflow_does_not_saturate(compile_source) -> None:  # noqa: ANN001
    """`i` is below the end, so `i + 1` fits: the plain add."""
    placed = _function(_ir(compile_source), "placed")
    assert "sat.add.u64" not in placed and "wrap.add.u64" in placed, placed


def test_a_check_branches_on_the_comparison(compile_source) -> None:  # noqa: ANN001
    """`cmp` and a jump, not the truth value made and then tested."""
    proc, output = compile_source(PROGRAM, "--emit=asm", "-O1",
                                  "--target=x86_64-linux")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    body = text[text.index("placed("):text.index("main")]
    assert not re.search(r"\bset[a-z]+\b", body), body
