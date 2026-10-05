"""Every move and every end of a lifetime is in the report log.

A value of a type marked `@[closed_by(f)]` is moved and never copied, so where
it went and where the variable that held it stopped holding anything are the
compiler's to say -- at the line each happened on.
"""

from __future__ import annotations

import json

from conftest import describe

PROGRAM = """\
@[closed_by(close)]
type Handle = n : u8

fn Handle.close(h: Handle):
    let n: u8 = h.n

fn made(n: u8) \N{RIGHTWARDS ARROW} Handle:
    Handle(.n \N{LEFTWARDS ARROW} n)

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let a: Handle = made(1u8)
    let b: Handle = a
    Handle.close(b)
    0u6

let std := ⎕import("std")
"""

#: Closed by a deferred call, read after it, and with two ways out of the block.
DEFERRED = """\
@[closed_by(close)]
type Handle = n : u8

fn Handle.close(h: Handle):
    let n: u8 = h.n

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let h: Handle = Handle(.n \N{LEFTWARDS ARROW} 1u8)
    defer Handle.close(h)
    if #init.args > 5:
        return 1u6
    let n: u8 = h.n
    if n = 0u8: 2u6 else: 0u6

let std := ⎕import("std")
"""


def _said(compile_source, tmp_path,  # noqa: ANN001
          program: str = PROGRAM) -> dict[str, dict[int, list[str]]]:
    """What the log says about moves and lifetimes, by kind and line."""
    log = tmp_path / "report.json"
    proc, _ = compile_source(program, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    found: dict[str, dict[int, list[str]]] = {"move": {}, "lifetime-ended": {}}
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] in found:
            found[one["kind"]].setdefault(one["where"]["line"], []).append(
                "".join((one["subject"], ": ", one["reason"])))
    return found


def test_moves_and_ends_are_logged_where_they_happen(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Bound to another name, then handed to the function that closes it; and
    the closing function's parameter ends with it."""
    said = _said(compile_source, tmp_path)
    assert any(one.startswith("a: ") for one in said["move"][13]), said
    assert any(one.startswith("a: ") for one in said["lifetime-ended"][13]), said
    assert any("handed to 'Handle.close'" in one for one in said["move"][14]), said
    assert any(one.startswith("b: ") for one in said["lifetime-ended"][14]), said
    assert any("closed: this is 'Handle.close'" in one
               for one in said["lifetime-ended"][4]), said


def test_a_deferred_move_is_made_when_the_block_is_left(compile_source, tmp_path) -> None:  # noqa: ANN001
    """The move is logged once, on the line after the block, where it is made --
    not where it is written, the variable being read on the lines after that."""
    said = _said(compile_source, tmp_path, DEFERRED)
    assert 10 not in said["move"] and 10 not in said["lifetime-ended"], said
    assert said["move"][15] == ["h: handed to 'Handle.close' as its argument 1, "
                                "by what is put off on line 10"], said
    ended = said["lifetime-ended"][15]
    assert len(ended) == 1 and ended[0].startswith("h: moved"), said
