"""A string kept two words wide where its values join, its allocator held by the compiler.

Run tests cannot see this -- the program answers the same either way -- so what is
checked is the code and the report log: which block parameters lost the third word,
and that the log says so for each, and why for the one that kept it.
"""

from __future__ import annotations

import json

from conftest import describe

PROGRAM = """\
fn pick(c: bool, a: &mut arena, n: u64) \N{RIGHTWARDS ARROW} u64:
    let s: str = if c: "a" \N{DOUBLE PLUS} "b" else: "c" \N{DOUBLE PLUS} "d"
    let t: mut str = "x" \N{DOUBLE PLUS} "y"
    let i: mut u64 = 0u64
    while i < n:
        t \N{LEFTWARDS ARROW} t \N{DOUBLE PLUS} "z"
        i \N{LEFTWARDS ARROW} i + 1u64
    let u: str = if c: "e" \N{DOUBLE PLUS} "f" else: "g" \N{DOUBLE PLUS} "h" in a
    let w: str = if c: "i" \N{DOUBLE PLUS} "j" in a else: "k" \N{DOUBLE PLUS} "l" in a
    \N{APL FUNCTIONAL SYMBOL QUAD}drop(#s + #t + #u + #w)

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let a: mut arena = \N{APL FUNCTIONAL SYMBOL QUAD}arena
    defer \N{APL FUNCTIONAL SYMBOL QUAD}empty(a)
    if pick(true, &mut a, 3u64) = 11u64: 0u6 else: 1u6
"""

THIN = "\N{LEFT ANGLE BRACKET}ptr<mut u8>, u64\N{RIGHT ANGLE BRACKET}"


def _compiled(compile_source, tmp_path):  # noqa: ANN001, ANN202
    log = tmp_path / "report.json"
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0",
                                  "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    reports = json.loads(log.read_text(encoding="utf-8"))["reports"]
    return output.read_text(encoding="utf-8"), reports


def test_joins_of_one_allocator_are_two_words(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Both ways the heap's, round a loop the heap's, both ways the arena's."""
    text, _ = _compiled(compile_source, tmp_path)
    pick = text.split("fn @pick(")[1].split("\n}")[0]
    heads = [line for line in pick.splitlines() if line and not line[0].isspace()]
    thin = [line for line in heads if THIN in line]
    fat = [line for line in heads if ": str)" in line]
    assert len(thin) == 3, heads
    assert len(fat) == 1, heads


def test_the_log_says_each(compile_source, tmp_path) -> None:  # noqa: ANN001
    """One entry per parameter, naming the local it is bound to."""
    _, reports = _compiled(compile_source, tmp_path)
    lean = sorted(one["reason"].split("'")[1] for one in reports
                  if one["kind"] == "lean-value")
    fat = [one["reason"] for one in reports if one["kind"] == "fat-value"]
    assert lean == ["s", "t", "w"], reports
    assert len(fat) == 1 and fat[0].startswith("'u'"), fat
