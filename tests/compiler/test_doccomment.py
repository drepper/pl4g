"""Reading a documentation comment into its parts.

The comment itself is prose and stays prose; what this is about is the part of
one written the way Doxygen writes it.  Two things read the answer: the compiler,
which checks that a `\\param` names a parameter, and the language server, which
shows it.  So the tests are about what the parts are, and the tests about what is
done with them are where those two are.
"""

from __future__ import annotations

import pytest

from pypl4g.front.doccomment import COMMANDS, parse


def test_prose_is_the_summary() -> None:
    """A comment with no commands in it is what it says, and nothing else."""
    found = parse("What this is for.\nAnd a second line about it.")
    assert found.summary == "What this is for.\nAnd a second line about it."
    assert found.parts == ()
    assert found.brief == "What this is for."


def test_either_sigil_begins_a_command() -> None:
    """Doxygen takes both and so does this; a comment moved here keeps working."""
    for sigil in ("\\", "@"):
        found = parse("".join(("A thing.\n", sigil, "param n how many")))
        assert [one.command for one in found.parts] == ["param"]
        assert found.parts[0].subject == "n"
        assert found.parts[0].text == "how many"
        # And what it quotes back is what was written, not the other spelling.
        assert found.parts[0].written == "".join((sigil, "param"))


def test_a_command_runs_until_the_next_one() -> None:
    """What follows a command on later lines belongs to it."""
    found = parse("\n".join(("Prose.",
                             "\\return how many bytes went,",
                             "    and nothing more to say",
                             "\\note a remark")))
    answers = found.first("return")
    assert answers is not None
    assert answers.text == "how many bytes went,\nand nothing more to say"
    assert found.first("note").text == "a remark"


def test_the_direction_doxygen_writes_is_read_and_dropped() -> None:
    """`\\param[in] n` is `\\param n` here: this language says that in the type."""
    for written in ("\\param[in] n how many", "\\param[out] n how many",
                    "\\param[in,out] n how many", "\\param [in, out] n how many"):
        found = parse("".join(("A thing.\n", written)))
        assert [(one.subject, one.text) for one in found.params()] == \
            [("n", "how many")], written


def test_a_brief_with_no_prose_is_the_summary() -> None:
    """And is not shown twice: it is the summary, so it is not also a part."""
    found = parse("\\brief what this is for\n\\param n how many")
    assert found.summary == "what this is for"
    assert [one.command for one in found.parts] == ["param"]


def test_a_brief_under_prose_stays_a_part() -> None:
    """Two summaries is a thing to show, not a thing to choose between."""
    found = parse("What this is for.\n\\brief said again")
    assert found.summary == "What this is for."
    assert [one.command for one in found.parts] == ["brief"]


def test_a_word_that_is_not_a_command_is_marked_and_kept() -> None:
    """Kept, because the compiler says so once and a reader still sees the text."""
    found = parse("A thing.\n\\bogus what is this")
    assert len(found.parts) == 1
    one = found.parts[0]
    assert not one.known and one.command == ""
    assert one.word == "bogus" and one.text == "what is this"


@pytest.mark.parametrize(("written", "means"), sorted(COMMANDS.items()))
def test_every_spelling_means_one_thing(written: str, means: str) -> None:
    """The several words Doxygen has for one thing are one thing here."""
    found = parse("".join(("A thing.\n\\", written, " something")))
    assert len(found.parts) == 1 or means == "brief"
    if means != "brief":
        assert found.parts[0].command == means


def test_where_a_command_is_written() -> None:
    """The line and the column, which is what places a diagnostic about one.

    Counted in the comment's own text, whose lines are the lines of the comment
    and whose columns are columns of the file: the parser keeps the span of each
    line without its marker, so the two meet without anything being measured
    twice.
    """
    found = parse("\n".join(("A thing.", "", "\\param n how many",
                             "  \\return one")))
    first, second = found.parts
    assert (first.line, first.column) == (2, 0)
    assert (second.line, second.column) == (3, 2)
