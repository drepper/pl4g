"""How a loop walks a value of a type with an `each`.

`foreach x := v` over such a value is the loop over `T.each(v)`, so where the loop
can see the generator its body is written into the loop, no coroutine made -- over a
name, a field of a record and an element of an array alike.  Handed to a `⇉T`
parameter, the callee cannot see what it walks, and it is walked through calls.
And two generators' frames are two types, however alike the rest of their
descriptions are.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_compiler
from pypl4g.ir.types import CoroutineFrame

PROGRAM = """let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
type Walk = at : u8 ; last : u8
type Pair = left : Walk ; right : Walk

fn Walk.each(w: Walk) \N{RIGHTWARDS PAIRED ARROWS} u8:
    foreach i := w.at\N{HORIZONTAL ELLIPSIS}w.last:
        yield i

fn total(s: \N{RIGHTWARDS PAIRED ARROWS}u8) \N{RIGHTWARDS ARROW} u8:
    let t: mut u8 = 0u8
    foreach x := s:
        t \N{LEFTWARDS ARROW} t + x
    t

fn made(n: u8) \N{RIGHTWARDS ARROW} Walk:
    Walk(.at \N{LEFTWARDS ARROW} 0u8, .last \N{LEFTWARDS ARROW} n)

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let p: Pair = Pair(.left \N{LEFTWARDS ARROW} Walk(.at \N{LEFTWARDS ARROW} 0u8, .last \N{LEFTWARDS ARROW} 3u8), .right \N{LEFTWARDS ARROW} Walk(.at \N{LEFTWARDS ARROW} 1u8, .last \N{LEFTWARDS ARROW} 3u8))
    let ws: Walk\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}2\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}p.left, p.right\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}
    let n: mut u8 = 0u8
    foreach x := p.left:
        n \N{LEFTWARDS ARROW} n + x
    foreach x := ws\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:
        n \N{LEFTWARDS ARROW} n + x
    foreach x := made(3u8):
        n \N{LEFTWARDS ARROW} n + x
    if n = 9u8 \N{LOGICAL AND} total(p.right) = 3u8 \N{LOGICAL AND} total(made(4u8)) = 6u8: 0u6 else: 1u6
"""


def _iterator_reports(tmp_path: Path) -> dict[int, list[str]]:
    """What the report log says about each walk, by the line it is on."""
    source = tmp_path / "t.pl4g"
    source.write_text(PROGRAM, encoding="utf-8")
    log = tmp_path / "reports.json"
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--report-log=", str(log))), str(source)])
    assert proc.returncode == 0, proc.stderr
    found: dict[int, list[str]] = {}
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] == "iterator" and one["where"]["file"] == str(source):
            found.setdefault(one["where"]["line"], []).append(one["reason"])
    return found


def test_a_loop_has_each_written_into_it(tmp_path: Path) -> None:
    """Over a field, an element and a call's answer, as over a name."""
    found = _iterator_reports(tmp_path)
    for line in (23, 25, 27):
        assert any("is written into this loop" in one for one in found[line]), found


def test_a_parameter_walks_each_through_calls(tmp_path: Path) -> None:
    """The callee cannot see what it walks, so the walk is a call per turn --
    reported under the generator's own name."""
    found = _iterator_reports(tmp_path)
    assert any(one.startswith("'each' is walked through calls")
               for one in found[29]), found


def test_two_frames_are_two_types() -> None:
    """Told apart by which generator each is, not by what they hold."""
    one, other = CoroutineFrame("a"), CoroutineFrame("b")
    assert one != other
    assert one == one
    assert len({one, other}) == 2
