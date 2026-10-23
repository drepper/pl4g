"""A named function handed over as a value is called directly, not through a shim.

A call through a value passes the environment in the convention's chain register,
which a function of the language's own convention never reads, so the value can
point at the function itself.  So can one of the system's convention, whose
value is of the system's convention too, `@[cdecl] fn(u64) → u64`, and is
called as C calls it.  Only one of a convention named by an architecture's name
is called through something standing between.  The log names every piece of
code the compiler made for a value (`name-lambda`), which is what tells them
apart.
"""

from __future__ import annotations

import json

from conftest import describe

OWN = """\
let std := ⎕import("std")

fn doubled(x: u64) \N{RIGHTWARDS ARROW} u64:
    x \N{MULTIPLICATION SIGN} 2u64

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let f: fn(u64) \N{RIGHTWARDS ARROW} u64 = doubled
    if f(2u64) = 4u64: 0u6 else: 1u6
"""

SYSTEM = OWN.replace("fn doubled", "@[cdecl]\nfn doubled").replace(
    "let f: fn", "let f: @[cdecl] fn")

#: Named by the name x86-64 gives the system's convention, which the target the
#: suite compiles for by default answers to.
NAMED = OWN.replace("fn doubled", '@[abi("sysv64")]\nfn doubled')


def _made(compile_source, tmp_path, program: str) -> list[str]:  # noqa: ANN001
    log = tmp_path / "report.json"
    proc, _ = compile_source(program, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    return [one["subject"] for one in json.loads(log.read_text(encoding="utf-8"))["reports"]
            if one["kind"] == "name-lambda"]


def test_a_function_of_the_own_convention_needs_no_shim(compile_source, tmp_path) -> None:  # noqa: ANN001
    assert _made(compile_source, tmp_path, OWN) == []


def test_a_function_of_the_system_convention_needs_none(compile_source, tmp_path) -> None:  # noqa: ANN001
    assert _made(compile_source, tmp_path, SYSTEM) == []


def test_a_function_of_a_named_convention_keeps_one(compile_source, tmp_path) -> None:  # noqa: ANN001
    assert any("doubled" in one for one in _made(compile_source, tmp_path, NAMED))
