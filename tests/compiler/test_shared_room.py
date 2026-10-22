"""Which bindings of a list copy it and which hand its room over.

A run test sees a copy that was not made, as a list of the caller's changed;
it cannot see a copy that was made needlessly.  So what is asked here is the
code: a name done with hands its room on where that room is its own, and is
copied where it is a parameter's or shares one.
"""

from __future__ import annotations

from conftest import describe, function_ir

PROGRAM = """\
fn first(l: [u8]) \N{RIGHTWARDS ARROW} u8:
    let seen: mut u8 = 0u8
    foreach v := l:
        seen \N{LEFTWARDS ARROW} v
    seen

@[opaque]
fn shared(x: [u8]) \N{RIGHTWARDS ARROW} u8:
    let y: [u8] = x
    let out: mut [u8] = y
    out\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} \N{LEFTWARDS ARROW} 9u8
    first(out)

@[opaque]
fn made() \N{RIGHTWARDS ARROW} u8:
    let y: [u8] = [1u8, 2u8] in \N{APL FUNCTIONAL SYMBOL QUAD}heap
    let out: mut [u8] = y
    out\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} \N{LEFTWARDS ARROW} 9u8
    first(out)

@[opaque]
fn handed_on() \N{RIGHTWARDS ARROW} u8:
    let y: mut [u8] = [1u8, 2u8] in \N{APL FUNCTIONAL SYMBOL QUAD}heap
    y\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} \N{LEFTWARDS ARROW} 3u8
    let out: mut [u8] = y
    out\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} \N{LEFTWARDS ARROW} 9u8
    first(out)

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let a: [u8] = [1u8] in \N{APL FUNCTIONAL SYMBOL QUAD}heap
    if shared(a) = 9u8 \N{LOGICAL AND} made() = 2u8 \N{LOGICAL AND} handed_on() = 3u8: 0u6 else: 1u6
"""


def _copies(compile_source, name: str) -> int:  # noqa: ANN001
    """How many copies of a list the body of *name* makes, unoptimized: the
    calls answering a list, nothing else in these bodies answering one."""
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    body = function_ir(output.read_text(encoding="utf-8"), name)
    return body.count("call.[u8]")


def test_a_name_bound_to_a_parameter_is_copied(compile_source) -> None:  # noqa: ANN001
    """`y` shares the parameter's room, so `out` is given a copy of it."""
    assert _copies(compile_source, "shared") == 1


def test_a_list_made_here_is_handed_over(compile_source) -> None:  # noqa: ANN001
    """`y` is room made here and done with, so `out` takes it as it is."""
    assert _copies(compile_source, "made") == 0


def test_a_variable_of_its_own_is_handed_over(compile_source) -> None:  # noqa: ANN001
    """A `mut` variable is always given room of its own, so it hands it on."""
    assert _copies(compile_source, "handed_on") == 0
