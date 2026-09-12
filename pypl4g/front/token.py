"""Tokens.

The language is meant to be generated rather than typed, so several operators are
written with Unicode glyphs.  An ASCII substitute exists only where it is a
sequence of more than one character: a single character is never a substitute,
so that it stays available for a future language feature.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..source.location import Span

#: Introduces a comment that runs to the end of the line.
COMMENT_GLYPH: Final[str] = "\N{REFERENCE MARK}"

#: Introduces a documentation comment.
DOC_COMMENT_GLYPH: Final[str] = COMMENT_GLYPH * 2

#: Separates a function's parameter list from its return type.
ARROW_GLYPH: Final[str] = "\N{RIGHTWARDS ARROW}"

#: Accepted substitute for the arrow.  Two characters, so it claims nothing.
ARROW_ASCII: Final[str] = "->"


class TokKind(StrEnum):
    """The kinds of token the lexer produces."""

    IDENT = "identifier"
    INT = "integer literal"
    STRING = "string literal"

    KW_FN = "'fn'"
    KW_RETURN = "'return'"
    KW_VAR = "'var'"
    KW_TYPE = "'type'"
    KW_TRUE = "'true'"
    KW_FALSE = "'false'"

    AT_LBRACKET = "'@['"
    LPAREN = "'('"
    RPAREN = "')'"
    LBRACKET = "'['"
    RBRACKET = "']'"
    LBRACE = "'{'"
    RBRACE = "'}'"
    COMMA = "','"
    COLON = "':'"
    SEMICOLON = "';'"
    EQUALS = "'='"
    ARROW = "'\N{RIGHTWARDS ARROW}'"

    DOC_COMMENT = "documentation comment"
    NEWLINE = "end of line"
    INDENT = "indentation"
    DEDENT = "end of indented block"
    EOF = "end of file"


KEYWORDS: Final[dict[str, TokKind]] = {
    "fn": TokKind.KW_FN,
    "return": TokKind.KW_RETURN,
    "var": TokKind.KW_VAR,
    "type": TokKind.KW_TYPE,
    "true": TokKind.KW_TRUE,
    "false": TokKind.KW_FALSE,
}


@dataclass(frozen=True, slots=True)
class Token:
    """One token, with the source text it came from."""

    kind: TokKind
    span: Span
    text: str = ""
    #: Value of an integer literal, or ``None`` for every other kind.
    int_value: int | None = None
    #: Decoded value of a string literal, or ``None`` for every other kind.
    str_value: str | None = None

    def describe(self) -> str:
        """How this token is named in a diagnostic."""
        if self.kind in (TokKind.IDENT, TokKind.INT):
            return "".join((str(self.kind), " '", self.text, "'"))
        return str(self.kind)
