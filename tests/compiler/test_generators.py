"""How a walk of a generator is compiled, and that the report log says so.

A loop that can see the generator is written with its body in place: no call is
made and no frame is taken.  A walk that cannot see it -- a `⇉T` parameter --
goes through a `⎕next` function and the generator's body split at its stops.
Both are in the log, with why a loop was not written in place where it was not,
and how large the split body's frame is.
"""

from __future__ import annotations

import json
import re

from conftest import describe

PROGRAM = """\
fn evens(n: u64) \N{RIGHTWARDS PAIRED ARROWS} u64:
    foreach i := 0u64\N{HORIZONTAL ELLIPSIS}n:
        if 2u64 \N{DIVIDES} i:
            yield i

fn total(s: \N{RIGHTWARDS PAIRED ARROWS}u64) \N{RIGHTWARDS ARROW} u64:
    let t: mut u64 = 0u64
    foreach x := s:
        t \N{LEFTWARDS ARROW} t + x
    t

@[inline(never)]
fn here(n: u64) \N{RIGHTWARDS ARROW} u64:
    let t: mut u64 = 0u64
    foreach x := evens(n):
        t \N{LEFTWARDS ARROW} t + x
    t

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup, impure]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    if here(10u64) + total(evens(10u64)) = 40u64: 0u6 else: 1u6
"""


def _compiled(compile_source, tmp_path):  # noqa: ANN001, ANN202
    log = tmp_path / "report.json"
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0",
                                  "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    return (output.read_text(encoding="utf-8"),
            json.loads(log.read_text(encoding="utf-8"))["reports"])


def _body(text: str, name: str) -> str:
    found = re.search("".join((r"fn @", re.escape(name), r"\(.*?\n}")), text, re.S)
    assert found is not None, text
    return found.group(0)


def test_a_loop_that_sees_the_generator_calls_nothing(compile_source, tmp_path) -> None:  # noqa: ANN001
    """`here` walks `evens` with the body in place: no call, no frame."""
    text, _ = _compiled(compile_source, tmp_path)
    body = _body(text, "here")
    assert " call" not in body and "frame" not in body, body


def test_a_walk_through_calls_goes_through_next(compile_source, tmp_path) -> None:  # noqa: ANN001
    """`main` hands `evens` on: a description in its frame and `⎕next.evens`;
    the generator's body is split, resuming where it stopped."""
    text, _ = _compiled(compile_source, tmp_path)
    assert "= code." in _body(text, "main"), text
    assert "fn @\N{APL FUNCTIONAL SYMBOL QUAD}next.evens(" in text, text
    split = _body(text, "\N{RIGHTWARDS PAIRED ARROWS}evens")
    assert "switch" in split and "yield" not in split, split


def test_each_decision_is_logged(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Written in, walked through calls, and the split with its frame's size."""
    _, reports = _compiled(compile_source, tmp_path)
    said = [one["reason"] for one in reports if one["kind"] == "iterator"]
    assert any("written into this loop" in one for one in said), said
    assert any("walked through calls to" in one for one in said), said
    assert any(re.search(r"split at its 1 stop: .* frame of \d+ bytes", one)
               for one in said), said
