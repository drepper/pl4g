"""The lexer, including the layout rules and the glyphs."""

from pathlib import Path

import pytest

from pypl4g.diag import ids as D
from pypl4g.diag.engine import WarningControl, collecting_engine
from pypl4g.front.lexer import tokenize
from pypl4g.front.token import TokKind
from pypl4g.source.manager import SourceManager


def lex(text: str, *, all_warnings: bool = False) -> tuple[list[TokKind], list[int]]:
    """Tokenize *text*, returning the token kinds and the diagnostics raised."""
    sources = SourceManager()
    source = sources.add(Path("t.pl4g"), text)
    control = WarningControl(enabled={"ascii-substitute": True}) if all_warnings else None
    engine, collected = collecting_engine(control)
    tokens = tokenize(source, engine)
    return [t.kind for t in tokens], [d.info.number for d in collected]


def test_comment_glyph_runs_to_end_of_line() -> None:
    """The reference mark introduces a comment."""
    kinds, diags = lex("\N{REFERENCE MARK} a comment\nfn\n")
    assert diags == []
    assert TokKind.KW_FN in kinds


def test_hash_is_not_a_comment() -> None:
    """'#' is deliberately left free for a future language feature."""
    _, diags = lex("# not a comment\n")
    assert D.LANG_SYNTAX_UNEXPECTED_CHAR in diags


def test_documentation_comment_is_a_token() -> None:
    """A doubled reference mark is a documentation comment."""
    kinds, diags = lex("\N{REFERENCE MARK}\N{REFERENCE MARK} documented\nfn\n")
    assert diags == []
    assert TokKind.DOC_COMMENT in kinds


def test_arrow_glyph_and_its_substitute() -> None:
    """'->' is accepted for the arrow, and says so when asked."""
    kinds, diags = lex("\N{RIGHTWARDS ARROW}\n")
    assert kinds[0] is TokKind.ARROW and diags == []
    kinds, diags = lex("->\n")
    assert kinds[0] is TokKind.ARROW
    assert diags == [], "the substitute is accepted silently unless asked about"
    kinds, diags = lex("->\n", all_warnings=True)
    assert diags == [D.LANG_SYNTAX_ASCII_SUBSTITUTE]


def test_layout_produces_indent_and_dedent() -> None:
    """An indented block is delimited by synthesized tokens."""
    kinds, diags = lex("fn\n    fn\nfn\n")
    assert diags == []
    assert kinds.count(TokKind.INDENT) == 1
    assert kinds.count(TokKind.DEDENT) == 1


def test_tab_indentation_is_rejected() -> None:
    """A tab would make the layout depend on a width the language does not fix."""
    _, diags = lex("fn\n\tfn\n")
    assert D.LANG_SYNTAX_TAB_INDENT in diags


def test_inconsistent_indentation_is_rejected() -> None:
    """A dedent must land on a level that was opened."""
    _, diags = lex("fn\n        fn\n    fn\n")
    assert D.LANG_SYNTAX_INCONSISTENT_INDENT in diags


def test_line_structure_is_ignored_inside_brackets() -> None:
    """A bracketed list may be written across lines."""
    kinds, diags = lex("@[startup,\n  inline]\nfn\n")
    assert diags == []
    assert TokKind.INDENT not in kinds


@pytest.mark.parametrize(("text", "value"), [
    ("0", 0), ("42", 42), ("0x1f", 31), ("0b1010", 10), ("0o17", 15), ("1_000", 1000),
])
def test_integer_literals(text: str, value: int) -> None:
    """Integer literals in every base, with separators."""
    sources = SourceManager()
    source = sources.add(Path("t.pl4g"), text)
    engine, collected = collecting_engine()
    tokens = tokenize(source, engine)
    assert tokens[0].int_value == value
    assert collected == []


def test_string_escapes() -> None:
    """Strings use the escapes of modern C, including the Unicode notation."""
    sources = SourceManager()
    source = sources.add(Path("t.pl4g"), '"a\\tb\\u203bc"')
    engine, collected = collecting_engine()
    tokens = tokenize(source, engine)
    assert tokens[0].str_value == "a\tb\N{REFERENCE MARK}c"
    assert collected == []


def test_unterminated_string() -> None:
    """A string does not run past the end of its line."""
    _, diags = lex('"open\n')
    assert D.LANG_SYNTAX_UNTERMINATED_STRING in diags
