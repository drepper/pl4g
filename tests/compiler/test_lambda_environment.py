"""Where a lambda keeps what it brought in: the frame, `⎕heap`, or the arena `in` names.

Run tests cannot see this -- the program answers the same either way -- so what is
checked is the report log, which says for every lambda where its environment went,
and the code, which takes room from the heap for exactly the lambdas that may leave.
"""

from __future__ import annotations

import json
import re

from conftest import describe

PROGRAM = """\
@[impure]
fn apply(g: fn(u8) \N{RIGHTWARDS ARROW} u8, x: u8) \N{RIGHTWARDS ARROW} u8:
    g(x)

fn adding(n: u8) \N{RIGHTWARDS ARROW} fn(u8) \N{RIGHTWARDS ARROW} u8:
    \N{GREEK SMALL LETTER LAMDA} a: u8 [n] \N{RIGHTWARDS ARROW} u8 { a + n }

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let n: u8 = 3u8
    let called: fn(u8) \N{RIGHTWARDS ARROW} u8 = \N{GREEK SMALL LETTER LAMDA} a: u8 [n] \N{RIGHTWARDS ARROW} u8 { a + n }
    let renamed: fn(u8) \N{RIGHTWARDS ARROW} u8 = \N{GREEK SMALL LETTER LAMDA} a: u8 [n] \N{RIGHTWARDS ARROW} u8 { a \N{MULTIPLICATION SIGN} n }
    let other: fn(u8) \N{RIGHTWARDS ARROW} u8 = renamed
    let pool: mut arena = \N{APL FUNCTIONAL SYMBOL QUAD}arena
    defer \N{APL FUNCTIONAL SYMBOL QUAD}empty(pool)
    let pooled: fn(u8) \N{RIGHTWARDS ARROW} u8 = \N{GREEK SMALL LETTER LAMDA} a: u8 [n] \N{RIGHTWARDS ARROW} u8 { a - n } in pool
    let plain: fn(u8) \N{RIGHTWARDS ARROW} u8 = \N{GREEK SMALL LETTER LAMDA} a: u8 \N{RIGHTWARDS ARROW} u8 { a }
    let added: fn(u8) \N{RIGHTWARDS ARROW} u8 = adding(1u8)
    let r: u8 = apply(\N{GREEK SMALL LETTER LAMDA} a: u8 [n] \N{RIGHTWARDS ARROW} u8 { a - n }, called(1u8)) + other(1u8)
    if r + pooled(4u8) + plain(0u8) + added(0u8) = 6u8: 0u6 else: 1u6
"""


def _compiled(compile_source, tmp_path):  # noqa: ANN001, ANN202
    log = tmp_path / "report.json"
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0",
                                  "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    reports = json.loads(log.read_text(encoding="utf-8"))["reports"]
    return output.read_text(encoding="utf-8"), reports


def _kept(reports: list[dict]) -> dict[int, str]:
    """Where each lambda keeps what it brought in, by the line it is written on."""
    found: dict[int, str] = {}
    for one in reports:
        said = re.match(r"'\N{APL FUNCTIONAL SYMBOL QUAD}lambda\d+' (.*)", one["reason"])
        if one["kind"] == "allocator" and said is not None:
            found[one["where"]["line"]] = said.group(1)
    return found


def test_the_report_log_says_where_each_environment_is(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Called or handed to a call: the frame.  Bound on, or answered: the heap."""
    _, reports = _compiled(compile_source, tmp_path)
    kept = _kept(reports)
    assert kept[6].startswith("keeps what it brought in in \N{APL FUNCTIONAL SYMBOL QUAD}heap"), kept
    assert kept[11].startswith("keeps what it brought in in this call's frame"), kept
    assert kept[12].startswith("keeps what it brought in in \N{APL FUNCTIONAL SYMBOL QUAD}heap"), kept
    assert kept[16].startswith("keeps what it brought in in the arena"), kept
    assert kept[17].startswith("brings nothing in"), kept
    assert kept[19].startswith("keeps what it brought in in this call's frame"), kept


def test_only_a_lambda_that_may_leave_takes_room_from_the_heap(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Two lambdas may leave, so the heap is named twice: in `adding`, and for `renamed`."""
    text, _ = _compiled(compile_source, tmp_path)
    asked = {name: len(re.findall(r"address\.[^@\n]*@__pl4g_heap$", body, re.M))
             for name, body in re.findall(r"fn @([^(]+)\((.*?)\n}", text, re.S)}
    assert asked.get("adding") == 1, asked
    assert asked.get("main") == 1, asked
