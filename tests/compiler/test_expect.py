"""Saying what a construct raises, so that it is not reported.

The attribute is `expect` rather than `allow` or `suppress`, and it behaves as
the name reads: it says the construct raises the diagnostic, and an expectation
nothing meets is itself reported.  Rust draws the same distinction between
`#[allow]` and `#[expect]`.
"""

import pytest

from conftest import describe, run_compiler
from pypl4g.diag.catalog import load_catalog
from pypl4g.diag.engine import collecting_engine
from pypl4g.diag import ids as D

ARROW = "\N{RIGHTWARDS ARROW}"
ASSIGN = "\N{LEFTWARDS ARROW}"

#: A value given and replaced before anything reads it.
WASTEFUL = "".join((
    "@[startup]\nfn main() ", ARROW, " u8:\n",
    "    let a: mut u8 = 5u8\n    a ", ASSIGN, " 4u8\n"))


def test_the_engine_absorbs_what_is_expected() -> None:
    """An expectation takes the diagnostic instead of it being reported."""
    engine, reported = collecting_engine()
    scope = engine.expect(frozenset({D.LANG_VARDEF_VALUE_UNUSED}))
    engine.emit(D.LANG_VARDEF_VALUE_UNUSED, name="a")
    engine.emit(D.LANG_FUNCDEF_RETURN_REDUNDANT)
    engine.release(scope)
    assert [d.info.number for d in reported] == [D.LANG_FUNCDEF_RETURN_REDUNDANT]
    assert scope.raised == {D.LANG_VARDEF_VALUE_UNUSED}
    assert scope.unmet == []


def test_an_absorbed_error_is_remembered() -> None:
    """A construct that raises an error cannot be compiled, absorbed or not."""
    engine, reported = collecting_engine()
    scope = engine.expect(frozenset({D.LANG_TYPE_UNKNOWN}))
    engine.emit(D.LANG_TYPE_UNKNOWN, name="nosuch")
    engine.release(scope)
    assert reported == []
    assert scope.saw_error
    assert not engine.failed, "an absorbed diagnostic does not fail the compilation"


def test_an_expectation_nothing_meets_is_reported() -> None:
    """A stale one hides nothing and says something untrue."""
    engine, _ = collecting_engine()
    scope = engine.expect(frozenset({D.LANG_VARDEF_VALUE_UNUSED}))
    engine.release(scope)
    assert scope.unmet == [D.LANG_VARDEF_VALUE_UNUSED]


def test_the_innermost_expectation_takes_it() -> None:
    """Expectations nest, and the one closest to the construct answers first."""
    engine, reported = collecting_engine()
    outer = engine.expect(frozenset({D.LANG_VARDEF_VALUE_UNUSED}))
    inner = engine.expect(frozenset({D.LANG_VARDEF_VALUE_UNUSED}))
    engine.emit(D.LANG_VARDEF_VALUE_UNUSED, name="a")
    engine.release(inner)
    engine.release(outer)
    assert reported == []
    assert inner.raised and not outer.raised


def test_a_value_nothing_reads_is_reported(compile_source) -> None:  # noqa: ANN001
    """The program the attribute exists for."""
    proc, _ = compile_source(WASTEFUL)
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4006]" in proc.stderr, proc.stderr
    assert "never read" in proc.stderr


def test_saying_so_keeps_it_quiet(compile_source) -> None:  # noqa: ANN001
    """The same program, with the attribute on the definition."""
    proc, _ = compile_source(WASTEFUL.replace("    let a:", "    @[expect(4006)]\n    let a:"))
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", proc.stderr


def test_a_parameter_is_not_reported(compile_source) -> None:  # noqa: ANN001
    """A parameter arrives with a value the caller chose; not reading it says
    nothing about this function."""
    proc, _ = compile_source("".join((
        "fn ignore(x: u8) ", ARROW, " u8:\n    1u8\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n    1u8\n")))
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4006]" not in proc.stderr, proc.stderr


def test_an_expected_error_discards_the_function(compile_source) -> None:  # noqa: ANN001
    """There is nothing to generate code from, and half of one would be worse."""
    proc, _ = compile_source("".join((
        "@[startup]\nfn main() ", ARROW, " u8:\n    1u8\n\n",
        "@[expect(4201)]\nfn helper() ", ARROW, " nosuch:\n    1u8\n")))
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", proc.stderr


def test_an_expected_error_discards_the_variable(compile_source) -> None:  # noqa: ANN001
    """A variable whose definition made no sense is not put in the image."""
    proc, output = compile_source("".join((
        "@[expect(2006)]\nlet big: u8 = 300u8\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n    1u8\n")), "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    assert "big" not in output.read_text(encoding="utf-8")


def test_discarding_the_startup_function_is_then_reported(compile_source) -> None:  # noqa: ANN001
    """What follows from discarding a definition is not itself hidden."""
    proc, _ = compile_source("".join((
        "@[startup, expect(4201)]\nfn main() ", ARROW, " nosuch:\n    1u8\n")))
    assert proc.returncode != 0
    assert "[PL4G-4401]" in proc.stderr, proc.stderr


def test_the_number_must_be_one_that_exists(compile_source) -> None:  # noqa: ANN001
    """A number nothing can raise could only ever be a mistake."""
    proc, _ = compile_source(WASTEFUL.replace("    let a:", "    @[expect(1234)]\n    let a:"))
    assert proc.returncode != 0
    assert "[PL4G-3207]" in proc.stderr, proc.stderr


def test_it_may_be_given_more_than_once(compile_source) -> None:  # noqa: ANN001
    """One expectation names one diagnostic, and a construct may raise several."""
    proc, _ = compile_source("".join((
        "@[startup]\nfn main() ", ARROW, " u8:\n",
        "    @[expect(4006)]\n    @[expect(4006)]\n    let a: mut u8 = 5u8\n",
        "    a ", ASSIGN, " 4u8\n")))
    assert "[PL4G-3202]" not in proc.stderr, "repeating it was refused"


def test_it_is_not_an_attribute_for_a_type() -> None:
    """Every attribute declares what it may be attached to, and this is no
    exception: it says what a construct raises, and a type raises nothing."""
    from pypl4g.sema.attributes import AttrTarget, lookup

    spec = lookup("expect")
    assert spec is not None
    assert AttrTarget.STATEMENT in spec.targets
    assert AttrTarget.FUNCTION in spec.targets
    assert AttrTarget.VARIABLE in spec.targets
    assert AttrTarget.TYPE not in spec.targets
    assert spec.repeatable


@pytest.mark.parametrize("number", [4006, 3206, 3207])
def test_the_new_diagnostics_are_in_the_catalog(number: int) -> None:
    """They are part of the contract between implementations like every other."""
    entry = load_catalog().by_number[number]
    assert entry.cause and entry.spec.requirement
