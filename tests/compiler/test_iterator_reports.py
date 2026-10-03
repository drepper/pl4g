"""Every decision about an iterator is in the report log.

An iterator is a walk described where it is bound, and what then becomes of it
is the compiler's choice: walked in place, built into an array where one is
asked for, handed on through a call per turn.  Each of those is said, at the
line it is about, and so is what the iterator borrows.
"""

from __future__ import annotations

import json

from conftest import describe

PROGRAM = """\
fn total(s: \N{RIGHTWARDS PAIRED ARROWS}u8) \N{RIGHTWARDS ARROW} u8:
    let n: mut u8 = 0u8
    foreach x := s:
        n \N{LEFTWARDS ARROW} n \N{SQUARED PLUS} x
    n

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let arr: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0u8, 1u8, 2u8, 3u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    let q := arr\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}arr > 1\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}
    let n: mut u8 = 0u8
    foreach x := q:
        n \N{LEFTWARDS ARROW} n + x
    let a: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = q
    if n + total(q) + a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = 12u8: 0u6 else: 1u6
"""


def _said(compile_source, tmp_path) -> dict[int, list[str]]:  # noqa: ANN001
    """What the log says about iterators, by line."""
    log = tmp_path / "report.json"
    proc, _ = compile_source(PROGRAM, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    found: dict[int, list[str]] = {}
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] == "iterator":
            found.setdefault(one["where"]["line"], []).append(one["reason"])
    return found


def test_each_decision_is_logged_where_it_is_made(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Described and borrowing where bound; walked in place; built; handed on;
    walked through a call where it is handed in."""
    said = _said(compile_source, tmp_path)
    assert any("described and not made" in one for one in said[10]), said
    assert any("borrows 'arr'" in one for one in said[10]), said
    assert any("walked here, in place" in one for one in said[12]), said
    assert any("built into an array" in one for one in said[14]), said
    assert any("handed on" in one for one in said[15]), said
    assert any("a call per turn" in one for one in said[3]), said
