"""One meaning, one spelling: how attributes are written down.

An attribute list says things about the definition that follows it.  Two ways of
writing what is in every respect the same thing help nobody: a reader learns two
shapes, a tool matches two shapes, and a program that compares two sources has to
know they are the same.  So the notation admits one of each.

Two rules follow from that, and they are the whole of this file.  Everything one
definition is told is written in one list, not several.  And an attribute that
carries no arguments is written without the parentheses that would carry them.
"""

from __future__ import annotations

import pytest

from conftest import describe, run_compiler

ARROW = "\N{RIGHTWARDS ARROW}"
ASSIGN = "\N{LEFTWARDS ARROW}"
MARK = "\N{REFERENCE MARK}"

PROGRAM = "".join(("@[startup, impure]\nfn main() ", ARROW, " u8:\n    0u8\n"))


def build(compile_source, text: str, *extra: str):  # noqa: ANN001, ANN201
    """Compile *text* followed by a program that does nothing of its own."""
    return compile_source("".join((text, PROGRAM)), *extra)[0]


def raises(proc, number: int) -> bool:  # noqa: ANN001
    """Whether the compiler reported the diagnostic *number*."""
    return "".join(("[PL4G-", str(number), "]")) in proc.stderr


# -- one list ------------------------------------------------------------------

def test_two_lists_before_one_definition_are_refused(compile_source) -> None:  # noqa: ANN001
    """`@[export] @[visible]` and `@[export, visible]` would say the same thing."""
    proc = build(compile_source, "@[export]\n@[visible]\nlet v: u8 = 7u8\n\n")
    assert proc.returncode != 0, describe(proc)
    assert raises(proc, 3208), describe(proc)


def test_a_blank_line_between_them_changes_nothing(compile_source) -> None:  # noqa: ANN001
    """It changes nothing about what they attach to, so it makes them no less two."""
    proc = build(compile_source, "@[export]\n\n\n@[visible]\nlet v: u8 = 7u8\n\n")
    assert proc.returncode != 0, describe(proc)
    assert raises(proc, 3208), describe(proc)


def test_a_comment_between_them_changes_nothing(compile_source) -> None:  # noqa: ANN001
    """Nor does anything else that is not a definition."""
    proc = build(compile_source,
                 "".join(("@[export]\n", MARK, " why\n@[visible]\nlet v: u8 = 7u8\n\n")))
    assert proc.returncode != 0, describe(proc)
    assert raises(proc, 3208), describe(proc)


def test_one_list_holding_both_is_how_it_is_written(compile_source) -> None:  # noqa: ANN001
    """The shape that is refused above has a shape that is not, and this is it."""
    proc = build(compile_source, "@[export, visible]\nlet v: u8 = 7u8\n\n")
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", proc.stderr


def test_the_rule_holds_inside_a_body_too(compile_source) -> None:  # noqa: ANN001
    """A statement is a thing attributes attach to, so it is the same question."""
    proc, _ = compile_source("".join((
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    @[ignore(5002)]\n    @[expect(4006)]\n    let a: mut u8 = 5u8\n",
        "    a ", ASSIGN, " 4u8\n")))
    assert proc.returncode != 0, describe(proc)
    assert raises(proc, 3208), describe(proc)


def test_lists_before_different_definitions_are_not_two_lists(compile_source) -> None:  # noqa: ANN001
    """The rule is about one definition, not about two lines that look alike."""
    proc = build(compile_source, "@[export]\nlet a: u8 = 7u8\n\n"
                                 "@[visible]\nlet b: u8 = 8u8\n\n")
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", proc.stderr


def test_the_report_points_at_the_list_that_should_not_be_there(compile_source) -> None:  # noqa: ANN001
    """The first list is fine; it is the second that has to go or be merged."""
    proc = build(compile_source, "@[export]\n@[visible]\nlet v: u8 = 7u8\n\n")
    line = next(l for l in proc.stderr.splitlines() if "[PL4G-3208]" in l)
    assert ":2:" in line, "".join(("the second list is on line 2: ", line))


# -- no empty parentheses ------------------------------------------------------

def test_an_attribute_with_no_arguments_takes_no_parentheses(compile_source) -> None:  # noqa: ANN001
    """`@[export()]` says exactly what `@[export]` says."""
    proc = build(compile_source, "@[export()]\nlet v: u8 = 7u8\n\n")
    assert proc.returncode != 0, describe(proc)
    assert raises(proc, 3209), describe(proc)


def test_the_rule_holds_where_the_attribute_could_take_arguments(compile_source) -> None:  # noqa: ANN001
    """`inline` has a parameter with a default, so `@[inline()]` parses and means
    `@[inline]`; that it parses is what makes it worth refusing."""
    proc = build(compile_source,
                 "".join(("@[inline()]\nfn quiet() ", ARROW, " u8:\n    1u8\n\n")))
    assert proc.returncode != 0, describe(proc)
    assert raises(proc, 3209), describe(proc)


def test_parentheses_that_carry_something_are_of_course_kept(compile_source) -> None:  # noqa: ANN001
    """The rule is about the empty pair and nothing else."""
    proc = build(compile_source, "@[align(64)]\nlet v: u8 = 7u8\n\n")
    assert proc.returncode == 0, describe(proc)


@pytest.mark.parametrize("written", ("@[export]", "@[export, visible]",
                                     "@[align(64), section(name=\".rodata\")]"))
def test_the_shapes_that_remain_all_compile(compile_source, written: str) -> None:  # noqa: ANN001
    """Nothing above narrowed the notation beyond the two things it meant to."""
    proc = build(compile_source, "".join((written, "\nlet v: u8 = 7u8\n\n")))
    assert proc.returncode == 0, describe(proc)
