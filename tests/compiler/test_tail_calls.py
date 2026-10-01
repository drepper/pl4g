"""Which calls of a function to itself became loops, and why the others did not.

The report log says it for every one -- `tail-call` and `self-call` -- and what is
checked here is that it says the right thing, and that the code agrees.
"""

from __future__ import annotations

import json

from conftest import describe

PROGRAM = """\
@[impure]
fn deferred(n: u64, c: &mut u64) \N{RIGHTWARDS ARROW} u64:
    defer c\N{POSITION INDICATOR} \N{LEFTWARDS ARROW} c\N{POSITION INDICATOR} + 1u64
    if n = 0u64: 0u64 else: deferred(n - 1u64, c)

fn checked(n: u64) \N{RIGHTWARDS ARROW} u64 post(\N{APL FUNCTIONAL SYMBOL QUAD}answer \N{GREATER-THAN OR EQUAL TO} 0u64):
    if n = 0u64: 0u64 else: checked(n - 1u64)

@[impure]
fn framed(n: u64, p: &mut u64) \N{RIGHTWARDS ARROW} u64:
    let mine: mut u64 = n
    if n = 0u64: p\N{POSITION INDICATOR} else: framed(n - 1u64, &mut mine)

fn summed(n: u64) \N{RIGHTWARDS ARROW} u64:
    if n = 0u64: 0u64 else: n + summed(n - 1u64)

fn looped(n: u64) \N{RIGHTWARDS ARROW} u64:
    if n = 0u64: 0u64 else: looped(n - 1u64)

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let c: mut u64 = 0u64
    let x: mut u64 = 9u64
    let all: u64 = deferred(3u64, &mut c) + checked(3u64) + framed(3u64, &mut x) + summed(3u64) + looped(3u64)
    if all = 7u64: 0u6 else: 1u6
"""


def _compiled(compile_source, tmp_path):  # noqa: ANN001, ANN202
    log = tmp_path / "report.json"
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0",
                                  "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    reports = json.loads(log.read_text(encoding="utf-8"))["reports"]
    return output.read_text(encoding="utf-8"), reports


def test_only_the_last_call_is_a_loop(compile_source, tmp_path) -> None:  # noqa: ANN001
    """One loop, and four calls that take stack, each said with why."""
    text, reports = _compiled(compile_source, tmp_path)
    tail = {one["subject"] for one in reports if one["kind"] == "tail-call"}
    kept = {one["subject"]: one["reason"] for one in reports
            if one["kind"] == "self-call"}
    assert tail == {"looped"}, reports
    assert set(kept) == {"deferred", "checked", "framed", "summed"}, kept
    assert "deferred statement" in kept["deferred"]
    assert "reference into the call's own storage" in kept["framed"]
    looped = text.split("fn @looped(")[1].split("\n}")[0]
    assert "call" not in looped, looped
