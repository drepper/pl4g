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


#: A use put off to the end of the block that is not a move.
DEFERRED = """\
fn shown(n: u8) \N{RIGHTWARDS ARROW} u6:
    if n = 0u8: 1u6 else: 0u6

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let k: u8 = 2u8
    defer _ \N{LEFTWARDS ARROW} shown(k)
    0u6

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
"""


def _ended(compile_source, tmp_path,  # noqa: ANN001
           program: str = PROGRAM, after: int = 7) -> dict[str, tuple[int, str]]:
    """Where the log says each variable of `main` -- the lines past *after* --
    ends, and why."""
    log = tmp_path / "report.json"
    proc, _ = compile_source(program, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    found: dict[str, tuple[int, str]] = {}
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] == "scope-ended" and one["where"]["line"] > after:
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
    ended = _ended(compile_source, tmp_path, DEFERRED, 4)
    assert ended["k"] == (9, "last used by what is put off on line 7, "
                             "when the block is left"), ended


def test_a_variable_moved_out_of_has_no_end_of_its_own(compile_source, tmp_path) -> None:  # noqa: ANN001
    """`h` is closed by its last use, which moves it: the move is logged, and
    there is no variable left after it to end."""
    ended = _ended(compile_source, tmp_path)
    assert "h" not in ended, ended


#: A variable read in the conditions and arms of a choice, and one read only
#: by a condition whose arm leaves.
ARMS = """\
@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let c: u8 = 2u8
    let total: mut u8 = 0u8
    if c = 1u8:
        total \N{LEFTWARDS ARROW} 1u8
    elif c = 2u8:
        total \N{LEFTWARDS ARROW} 2u8
    else:
        total \N{LEFTWARDS ARROW} c
    if total = 0u8:
        return 1u6
    0u6

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
"""


def _every_end(compile_source, tmp_path, program: str  # noqa: ANN001
               ) -> dict[str, list[tuple[int, str]]]:
    """Every end the log gives each variable, as line and reason."""
    log = tmp_path / "report.json"
    proc, _ = compile_source(program, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    found: dict[str, list[tuple[int, str]]] = {}
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] == "scope-ended":
            found.setdefault(one["subject"], []).append(
                (one["where"]["line"], one["reason"]))
    return found


def test_a_variable_ends_in_every_arm(compile_source, tmp_path) -> None:  # noqa: ANN001
    """`c` is last read by the first condition on the way into the first arm, by
    the second on the way into the second, and in the third arm itself: it ends
    in each, at the start of the arm where the arm does not read it."""
    ends = _every_end(compile_source, tmp_path, ARMS)
    assert sorted(ends["c"]) == [(6, "last used on line 5"),
                                 (8, "last used on line 7"),
                                 (11, "last used on line 10")], ends


def test_an_arm_that_leaves_and_the_way_past_the_choice_each_end_it(compile_source, tmp_path) -> None:  # noqa: ANN001
    """`total` is last read by a condition: in the arm, which returns, and on
    the way past the `if` when no arm is taken."""
    ends = _every_end(compile_source, tmp_path, ARMS)
    assert sorted(ends["total"]) == [(12, "last used on line 11"),
                                     (13, "last used on line 11")], ends
