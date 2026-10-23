"""`⎕auxv`: what a program carries for it, and what it does not.

What the name answers is a language test; what is here is the bargain it
strikes with the image -- a program that never asks the vector anything carries
no table and no reader, a question that never has an answer makes neither, and
a shared object has the table and no reader, having no start to call one from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler

#: What the table and the function that fills it are called in the image.
TABLE = "__pl4g_auxv"
READER = "__pl4g_auxv_make"

#: A program asking the vector, one asking nothing, and one asking only what it
#: never holds.
ASKS = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    if (\N{APL FUNCTIONAL SYMBOL QUAD}auxv(6) ?? 0u64) \N{GREATER-THAN OR EQUAL TO} 4096u64: 0u6 else: 1u6
"""

SILENT = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    0u6
"""

NEVER = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    @[ignore(4381)]
    let none: u64 = \N{APL FUNCTIONAL SYMBOL QUAD}auxv(99) ?? 0u64
    if none = 0u64: 0u6 else: 1u6
"""

#: And a shared object asking it.
SHARED = """\
@[visible, cdecl]
fn page() \N{RIGHTWARDS ARROW} u64:
    \N{APL FUNCTIONAL SYMBOL QUAD}auxv(6) ?? 0u64
"""


def _built(source: str, where: Path, triple: str, *extra: str) -> elfcheck.Image:
    """Compile *source* for *triple* and answer the image it became."""
    written = where / "one.pl4g"
    written.write_text(source, encoding="utf-8")
    out = where / "one"
    proc = run_compiler(["-o", str(out), "".join(("--target=", triple)), *extra,
                         str(written)])
    assert proc.returncode == 0, describe(proc)
    return elfcheck.parse(out.read_bytes())


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_asks_carries_both(triple: str, tmp_path: Path) -> None:
    """The table and the generated reader are both in the image."""
    image = _built(ASKS, tmp_path, triple)
    assert image.symbol(TABLE) is not None
    assert image.symbol(READER) is not None


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_does_not_carries_neither(triple: str,
                                                 tmp_path: Path) -> None:
    """Made on first ask, so a program that never asks has neither."""
    image = _built(SILENT, tmp_path, triple)
    assert image.symbol(TABLE) is None
    assert image.symbol(READER) is None


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_question_without_an_answer_asks_nothing(triple: str,
                                                   tmp_path: Path) -> None:
    """A type the table never holds is answered while compiling."""
    image = _built(NEVER, tmp_path, triple)
    assert image.symbol(TABLE) is None
    assert image.symbol(READER) is None


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_shared_object_has_no_reader(triple: str, tmp_path: Path) -> None:
    """The table, which stays empty, and nothing to fill it."""
    image = _built(SHARED, tmp_path, triple, "-shared")
    assert image.symbol(TABLE) is not None
    assert image.symbol(READER) is None
