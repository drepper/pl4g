"""`v ← f(v, x)`: a list the caller is done with is handed over, not copied.

Run tests cannot see it -- the program answers the same either way -- so what is
checked is the report log, which says which arguments went as they were, and the
code, which has no copy of the list on the way into the callee.
"""

from __future__ import annotations

import json
import re

from conftest import describe

PROGRAM = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

fn pushed(l: mut [u8], x: u8) \N{RIGHTWARDS ARROW} [u8]:
    l \N{DOUBLE PLUS}\N{LEFTWARDS ARROW} [x]
    l

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let v: mut [u8] = []
    v \N{LEFTWARDS ARROW} pushed(v, 1u8)
    let w: [u8] = pushed(v, 2u8)
    if #v + #w = 3: 0u6 else: 1u6
"""


def _compiled(compile_source, tmp_path):  # noqa: ANN001, ANN202
    log = tmp_path / "report.json"
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0",
                                  "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    reports = json.loads(log.read_text(encoding="utf-8"))["reports"]
    return output.read_text(encoding="utf-8"), reports


def test_only_the_list_nothing_reads_again_is_handed_over(compile_source, tmp_path) -> None:  # noqa: ANN001
    """The first call moves `v`; the second, after which `v` is read, copies it."""
    _, reports = _compiled(compile_source, tmp_path)
    moved = [one["where"]["line"] for one in reports
             if "is handed to 'pushed' as it is" in one["reason"]]
    assert moved == [10], reports


def test_the_callee_copies_nothing_on_the_way_in(compile_source, tmp_path) -> None:  # noqa: ANN001
    """What `pushed` takes as its own is the caller's to see to: one copy in all,
    the second call's, made in `main`."""
    text, _ = _compiled(compile_source, tmp_path)
    bodies = dict(re.findall(r"fn @(\w+)\(.*?\n(.*?)\n}", text, re.S))
    own = r"= call\.\[u8\] %\d+, %\d+$"
    assert not re.findall(own, bodies["pushed"].split("owned.copy")[0], re.M), bodies["pushed"]
    assert len(re.findall(own, bodies["main"], re.M)) == 1, bodies["main"]
