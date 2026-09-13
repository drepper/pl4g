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

#: Assigns a value to a variable.  It has no ASCII substitute: the only
#: candidate, '<-', cannot be told apart from a comparison against a negated
#: value without depending on the spaces around it, which is a distinction this
#: language does not make.
ASSIGN_GLYPH: Final[str] = "\N{LEFTWARDS ARROW}"

#: Marks a literal as negative.  It is part of the literal, not an operator, so
#: nothing may stand between it and the digits.  A separate glyph is what lets
#: subtraction keep '-' without either meaning having to be worked out from the
#: spaces around it, which is the distinction APL draws with its own high minus.
NEGATIVE_GLYPH: Final[str] = "\N{SUPERSCRIPT MINUS}"

#: Compares two values.  The three that have a glyph are written with one; the
#: other three are the characters everyone already writes them with.  There is
#: no separate operator for assignment to be confused with, since assignment is
#: written with an arrow, so '=' asks a question and nothing else.
NOT_EQUAL_GLYPH: Final[str] = "\N{NOT EQUAL TO}"
LESS_EQUAL_GLYPH: Final[str] = "\N{LESS-THAN OR EQUAL TO}"
GREATER_EQUAL_GLYPH: Final[str] = "\N{GREATER-THAN OR EQUAL TO}"

#: The logical operators, which work on truth values and on nothing else.  Each
#: is a glyph, and none has an ASCII substitute: the candidates would be `&&`,
#: `||` and `!`, and spelling two of them with the characters the *bitwise*
#: operators use is the one confusion this language is built to avoid.  `and`
#: and `or` are words rather than glyphs because they differ from `\N{LOGICAL AND}` and `\N{LOGICAL OR}`
#: in when they evaluate their right operand, which is a thing a reader has to
#: be told rather than shown.
AND_GLYPH: Final[str] = "\N{LOGICAL AND}"
OR_GLYPH: Final[str] = "\N{LOGICAL OR}"
XOR_GLYPH: Final[str] = "\N{CIRCLED PLUS}"
NAND_GLYPH: Final[str] = "\N{NAND}"
NOR_GLYPH: Final[str] = "\N{NOR}"
NOT_GLYPH: Final[str] = "\N{NOT SIGN}"

#: Accepted substitute for the arrow.  Two characters, so it claims nothing.
ARROW_ASCII: Final[str] = "->"

#: Every ASCII substitute, and the glyph it stands for.  Each is at least two
#: characters, by the rule at the top of this module, and each is tried before
#: the single characters are, since '<=' begins with one of them.
ASCII_SUBSTITUTES: Final[dict[str, str]] = {
    ARROW_ASCII: ARROW_GLYPH,
    "<=": LESS_EQUAL_GLYPH,
    ">=": GREATER_EQUAL_GLYPH,
}


class TokKind(StrEnum):
    """The kinds of token the lexer produces."""

    IDENT = "identifier"
    INT = "integer literal"
    STRING = "string literal"

    KW_FN = "'fn'"
    KW_RETURN = "'return'"
    KW_LET = "'let'"
    KW_MUT = "'mut'"
    KW_TYPE = "'type'"
    KW_TRUE = "'true'"
    KW_FALSE = "'false'"
    KW_IMPORT = "'import'"
    KW_AND = "'and'"
    KW_OR = "'or'"

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
    ASSIGN = "'\N{LEFTWARDS ARROW}'"

    DOT = "'.'"
    AMPERSAND = "'&'"
    PIPE = "'|'"
    CARET = "'^'"
    TILDE = "'~'"

    NOT_EQUAL = "'\N{NOT EQUAL TO}'"
    LESS = "'<'"
    GREATER = "'>'"
    LESS_EQUAL = "'\N{LESS-THAN OR EQUAL TO}'"
    GREATER_EQUAL = "'\N{GREATER-THAN OR EQUAL TO}'"

    LOGIC_AND = "'\N{LOGICAL AND}'"
    LOGIC_OR = "'\N{LOGICAL OR}'"
    LOGIC_XOR = "'\N{CIRCLED PLUS}'"
    LOGIC_NAND = "'\N{NAND}'"
    LOGIC_NOR = "'\N{NOR}'"
    LOGIC_NOT = "'\N{NOT SIGN}'"

    DOC_COMMENT = "documentation comment"
    NEWLINE = "end of line"
    INDENT = "indentation"
    DEDENT = "end of indented block"
    EOF = "end of file"


#: The types an integer literal may name with its suffix.  Taken from the type
#: table rather than listed again, so that the two cannot disagree.
INTEGER_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    "".join((prefix, str(bits)))
    for prefix in ("i", "u") for bits in (8, 16, 32, 64))


KEYWORDS: Final[dict[str, TokKind]] = {
    "fn": TokKind.KW_FN,
    "return": TokKind.KW_RETURN,
    "let": TokKind.KW_LET,
    "mut": TokKind.KW_MUT,
    "type": TokKind.KW_TYPE,
    "true": TokKind.KW_TRUE,
    "false": TokKind.KW_FALSE,
    "import": TokKind.KW_IMPORT,
    "and": TokKind.KW_AND,
    "or": TokKind.KW_OR,
}


@dataclass(frozen=True, slots=True)
class Token:
    """One token, with the source text it came from."""

    kind: TokKind
    span: Span
    text: str = ""
    #: Value of an integer literal, or ``None`` for every other kind.
    int_value: int | None = None
    #: The type an integer literal named with its suffix, if it named one.
    int_type: str | None = None
    #: Decoded value of a string literal, or ``None`` for every other kind.
    str_value: str | None = None

    def describe(self) -> str:
        """How this token is named in a diagnostic."""
        if self.kind in (TokKind.IDENT, TokKind.INT):
            return "".join((str(self.kind), " '", self.text, "'"))
        return str(self.kind)
