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
fn main() \N{RIGHTWARDS ARROW} u6:
    let a: Handle = made(1u8)
    let b: Handle = a
    Handle.close(b)
    0u6
"""


def _said(compile_source, tmp_path) -> dict[str, dict[int, list[str]]]:  # noqa: ANN001
    """What the log says about moves and lifetimes, by kind and line."""
    log = tmp_path / "report.json"
    proc, _ = compile_source(PROGRAM, "".join(("--report-log=", str(log))))
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
