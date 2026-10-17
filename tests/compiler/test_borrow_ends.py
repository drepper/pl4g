"""Where a borrow ends is in the report log, at the point the checker assumed.

A reference a name keeps is out until the name's last use on each way through.
The checker stops counting it at the first statement where the name is no longer
live, and the log's `borrow-ended` entry stands there: above that statement.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import describe

PROGRAM = (Path(__file__).resolve().parent.parent / "language" / "borrow-ends-at-last-use"
           / "borrow-ends-at-last-use.pl4g")


def _ended(compile_source, tmp_path) -> list[tuple[int, str, str]]:  # noqa: ANN001
    """Every `borrow-ended` entry, as line, the place lent and who held it."""
    text = PROGRAM.read_text(encoding="utf-8")
    log = tmp_path / "report.json"
    proc, _ = compile_source(text, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    first = text.splitlines().index("let std := ⎕import(\"std\")")
    found = []
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] == "borrow-ended":
            holder = one["reason"].split("'")[1]
            found.append((one["where"]["line"] - first, one["subject"], holder))
    return sorted(found)


def test_every_borrow_ends_where_its_keeper_is_last_used(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Counted from the line `std` is imported on, as the program in
    `borrow-ends-at-last-use` is written."""
    assert _ended(compile_source, tmp_path) == [
        (8, "n", "a"),     # after `a⌖ ← 2u8`, the line `b` is made on
        (10, "n", "b"),
        (15, "n", "c"),    # where the `else` begins, which never uses `c`
        (16, "n", "c"),    # and after the `if`, on the way through `then`
        (20, "n", "d"),    # after the loop, not inside it
        (24, "n", "other"),  # what `other` reaches through `r`, kept by it too
        (24, "r", "other"),
        (25, "n", "r"),
    ], _ended(compile_source, tmp_path)


def _said(compile_source, tmp_path, name: str) -> list[tuple[int, str, str]]:  # noqa: ANN001
    """Every `borrow-ended` entry of the language test *name*: the line counted from
    where `std` is imported, the place lent, and why it ended."""
    path = Path(__file__).resolve().parent.parent / "language" / name / "".join((name, ".pl4g"))
    text = path.read_text(encoding="utf-8")
    log = tmp_path / "report.json"
    proc, _ = compile_source(text, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    first = text.splitlines().index("let std := ⎕import(\"std\")")
    return sorted((one["where"]["line"] - first, one["subject"],
                   one["reason"].split(": ", 1)[1])
                  for one in json.loads(log.read_text(encoding="utf-8"))["reports"]
                  if one["kind"] == "borrow-ended")


def test_a_name_given_another_value_releases_what_it_kept(compile_source, tmp_path) -> None:  # noqa: ANN001
    said = _said(compile_source, tmp_path, "reference-released-when-rebound")
    lines = [(line, place) for line, place, _ in said]
    assert lines == [(9, "n"), (13, "m"), (18, "m"), (18, "n"), (23, "m"), (25, "m")], said
    assert said[0][2].startswith("'moving' is given another value here"), said
    assert said[1][2].startswith("'moving' is given another value here"), said
    assert said[2][2].startswith("'moving' is not used again"), said
    assert said[4][2].startswith("'inner' is not used again"), said   # where `continue` goes
    assert said[5][2].startswith("the scope of 'inner' ends here"), said


def test_every_cause_is_said(compile_source, tmp_path) -> None:  # noqa: ANN001
    said = _said(compile_source, tmp_path, "borrow-ended-causes")
    why = {(line, place): reason.split(",")[0] for line, place, reason in said}
    assert why[(11, "k")].startswith("the statement above gives 'r' another value made "
                                     "from the one it held"), said
    assert why[(14, "k")] == "'r' is not used again on this way through", said
    assert why[(16, "n")] == "the function returns here", said
    assert why[(17, "k")] == "'r' is not used again on this way through", said
    assert why[(18, "n")] == "the scope of 'held' ends here", said
