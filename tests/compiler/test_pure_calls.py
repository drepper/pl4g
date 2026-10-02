"""A pure function's purity, used: calls reused, moved out of loops, worked out.

Run tests cannot see any of it -- the program answers the same either way -- so
what is checked is the report log, which says each, and the code, which is the
same less the calls.
"""

from __future__ import annotations

import json

from conftest import describe

PROGRAM = """\
@[inline(never)]
fn square(x: u32) \N{RIGHTWARDS ARROW} u32:
    x \N{MULTIPLICATION SIGN} x

@[impure, inline(never)]
fn seed() \N{RIGHTWARDS ARROW} u32:
    3u32

@[impure, inline(never)]
fn looped(a: u32, k: u32) \N{RIGHTWARDS ARROW} u32:
    let n: mut u32 = 0u32
    let total: mut u32 = 0u32
    while square(a) > n:
        total \N{LEFTWARDS ARROW} total + 1u32
        n \N{LEFTWARDS ARROW} n + k
    total

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let s: u32 = seed()
    let twice: u32 = square(s) + square(s)
    let folded: u32 = square(12u32)
    if twice + folded + looped(s, 1u32) = 171u32: 0u6 else: 1u6
"""


def _compiled(compile_source, tmp_path, level):  # noqa: ANN001, ANN202
    log = tmp_path / "report.json"
    proc, output = compile_source(PROGRAM, "--emit=ir", level,
                                  "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    reports = json.loads(log.read_text(encoding="utf-8"))["reports"]
    return output.read_text(encoding="utf-8"), reports


def test_each_use_is_in_the_report_log(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Reused in `main`, worked out in `main`, moved out of the loop in `looped`."""
    _, reports = _compiled(compile_source, tmp_path, "-O1")
    kinds = {(one["kind"], one["subject"]) for one in reports}
    assert ("pure-call-reused", "main") in kinds, kinds
    assert ("pure-call-folded", "main") in kinds, kinds
    assert ("pure-call-hoisted", "looped") in kinds, kinds


def test_nothing_is_done_without_optimizing(compile_source, tmp_path) -> None:  # noqa: ANN001
    """At -O0 every call is where it was written."""
    _, reports = _compiled(compile_source, tmp_path, "-O0")
    assert not [one for one in reports if one["kind"].startswith("pure-call")]
