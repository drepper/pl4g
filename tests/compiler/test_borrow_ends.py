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
        (24, "r", "other"),
        (25, "n", "r"),
    ], _ended(compile_source, tmp_path)
