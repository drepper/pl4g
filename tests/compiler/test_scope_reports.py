"""Every variable's scope ends after its last use, and the report log says so.

The entry is on the line after the one the scope ends on, so the viewer shows it
just below that line.  A loop, what a `foreach` walks, a deferred statement and a
name holding a reference each carry a scope past the text of its last use.
"""

from __future__ import annotations

import json

from conftest import describe

PROGRAM = """\
@[closed_by(close)]
type Handle = n : u8

fn Handle.close(h: Handle):
    let n: u8 = h.n

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let a: u8 = 1u8
    let r: &u8 = &a
    let unused: u8 = 3u8
    let total: mut u8 = 0u8
    let n: u8 = 4u8
    let step: u8 = 1u8
    foreach i: u8 = 0u8\N{HORIZONTAL ELLIPSIS}n:
        total \N{LEFTWARDS ARROW} total + i + step
    let b: u8 = r\N{POSITION INDICATOR}
    let h: Handle = Handle(.n \N{LEFTWARDS ARROW} b)
    defer Handle.close(h)
    if b + total = 0u8: 1u6 else: 0u6

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
"""


def _ended(compile_source, tmp_path) -> dict[str, tuple[int, str]]:  # noqa: ANN001
    """Where the log says each variable of `main` ends, and why."""
    log = tmp_path / "report.json"
    proc, _ = compile_source(PROGRAM, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    found: dict[str, tuple[int, str]] = {}
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] == "scope-ended" and one["where"]["line"] > 7:
            assert one["subject"] not in found, one
            found[one["subject"]] = (one["where"]["line"], one["reason"])
    return found


def test_a_scope_ends_on_the_line_after_its_last_use(compile_source, tmp_path) -> None:  # noqa: ANN001
    ended = _ended(compile_source, tmp_path)
    assert ended["b"] == (21, "last used on line 20"), ended
    assert ended["total"] == (21, "last used on line 20"), ended
    assert ended["i"] == (17, "last used on line 16"), ended


def test_a_name_nothing_uses_ends_where_it_is_bound(compile_source, tmp_path) -> None:  # noqa: ANN001
    ended = _ended(compile_source, tmp_path)
    assert ended["unused"] == (12, "never used"), ended
    assert ended["init"] == (9, "never used"), ended


def test_a_loop_carries_what_it_uses_to_its_end(compile_source, tmp_path) -> None:  # noqa: ANN001
    """`step` is read on every turn; `n`, a bound of the range, once."""
    ended = _ended(compile_source, tmp_path)
    assert ended["step"][0] == 17 and "in the loop from line 15" in ended["step"][1], ended
    assert ended["n"] == (16, "last used on line 15"), ended


def test_a_reference_keeps_what_it_names(compile_source, tmp_path) -> None:  # noqa: ANN001
    ended = _ended(compile_source, tmp_path)
    assert ended["r"] == (18, "last used on line 17"), ended
    assert ended["a"] == (18, "'r' holds a reference to it, and its scope ends here"), ended


def test_a_deferred_use_is_at_the_end_of_the_block(compile_source, tmp_path) -> None:  # noqa: ANN001
    ended = _ended(compile_source, tmp_path)
    assert ended["h"] == (21, "last used by what is put off on line 19, "
                              "when the block is left"), ended
