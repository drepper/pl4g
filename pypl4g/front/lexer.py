"""Lexical analysis, including the layout rules.

The lexer produces the ``INDENT``, ``DEDENT`` and ``NEWLINE`` tokens that the
layout-based syntax needs, in the manner of Python.  Inside brackets and inside
an explicit brace-enclosed block, line structure carries no meaning and those
tokens are not produced.
"""

from __future__ import annotations

import math
import re

import unicodedata

from typing import Final

from dataclasses import replace

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..source.location import Span
from ..source.manager import SourceFile
from .token import (ABOVE_NOT_ALIKE_GLYPH, BLANK_LINE, ABOVE_OR_ALIKE_GLYPH, ALIKE_GLYPH,
                    HOLE_GLYPH,
                    OPNAME_GLYPH, OPERATOR_CATEGORIES, OPERATOR_GLYPHS,
                    OPENER_CATEGORY, CLOSER_CATEGORY,
                    OPERATORS_NOT_NAMEABLE,
                    BOTTOM_GLYPH, BUILTIN_GLYPH,
                    AND_GLYPH, ARROW_GLYPH, ASCII_SUBSTITUTES, ASSIGN_GLYPH,
                    BELOW_NOT_ALIKE_GLYPH, BELOW_OR_ALIKE_GLYPH,
                    COMMENT_GLYPH, CONCAT_GLYPH, FLOAT_TYPE_NAMES,
                    CEILING_GLYPH, FLOOR_GLYPH, MAX_GLYPH, MIN_GLYPH,
                    DIVIDES_GLYPH, NOT_DIVIDES_GLYPH,
                    DEREF_GLYPH, EACH_GLYPH, ITER_GLYPH, TAKES_GLYPH, KEY_GLYPH, LAMBDA_GLYPH, LIFETIME_GLYPH,
                    UNIT_GLYPH,
                    LIFT_CLOSE_GLYPH, LIFT_OPEN_GLYPH,
                    NEAREST_GLYPH, POWER_GLYPH, ROUNDED_GLYPH,
                    SHAPE_GLYPH, SUPERSCRIPT_DIGITS,
                    GREATER_EQUAL_GLYPH,
                    INTEGER_TYPE_NAMES,
                    KEYWORDS, LESS_EQUAL_GLYPH, NAND_GLYPH, NEGATIVE_GLYPH,
                    ARRAY_CLOSE_GLYPH, ARRAY_OPEN_GLYPH, LABEL_GLYPH, SPREAD_GLYPH,
                    NOR_GLYPH, NOT_EQUAL_GLYPH, NOT_GLYPH, OR_GLYPH,
                    RANGE_GLYPH,
                    DIVIDE_GLYPH, ROTATE_LEFT_GLYPH, ROTATE_RIGHT_GLYPH,
                    SAT_ADD_GLYPH, SAT_MUL_GLYPH, SHIFT_LEFT_GLYPH,
                    SHIFT_RIGHT_GLYPH,
                    QUESTION_GLYPH, SET_CLOSE_GLYPH, SET_OPEN_GLYPH,
                    TAKE_GLYPH, NEXT_GLYPH, PREV_GLYPH,
                    TUPLE_CLOSE_GLYPH, TUPLE_OPEN_GLYPH,
                    SAT_SUB_GLYPH, TIMES_GLYPH, TokKind, Token,
                    UNALIKE_GLYPH, XOR_GLYPH)

_SIMPLE: Final[dict[str, TokKind]] = {
    "(": TokKind.LPAREN,
    ")": TokKind.RPAREN,
    "[": TokKind.LBRACKET,
    "]": TokKind.RBRACKET,
    "{": TokKind.LBRACE,
    "}": TokKind.RBRACE,
    ",": TokKind.COMMA,
    ":": TokKind.COLON,
    ";": TokKind.SEMICOLON,
    HOLE_GLYPH: TokKind.DOLLAR,
    "=": TokKind.EQUALS,
    ARROW_GLYPH: TokKind.ARROW,
    ASSIGN_GLYPH: TokKind.ASSIGN,
    ".": TokKind.DOT,
    "&": TokKind.AMPERSAND,
    "|": TokKind.PIPE,
    "^": TokKind.CARET,
    "~": TokKind.TILDE,
    NOT_EQUAL_GLYPH: TokKind.NOT_EQUAL,
    "<": TokKind.LESS,
    ">": TokKind.GREATER,
    LESS_EQUAL_GLYPH: TokKind.LESS_EQUAL,
    GREATER_EQUAL_GLYPH: TokKind.GREATER_EQUAL,
    ALIKE_GLYPH: TokKind.ALIKE,
    UNALIKE_GLYPH: TokKind.UNALIKE,
    BELOW_OR_ALIKE_GLYPH: TokKind.BELOW_OR_ALIKE,
    ABOVE_OR_ALIKE_GLYPH: TokKind.ABOVE_OR_ALIKE,
    BELOW_NOT_ALIKE_GLYPH: TokKind.BELOW_NOT_ALIKE,
    ABOVE_NOT_ALIKE_GLYPH: TokKind.ABOVE_NOT_ALIKE,
    AND_GLYPH: TokKind.LOGIC_AND,
    OR_GLYPH: TokKind.LOGIC_OR,
    XOR_GLYPH: TokKind.LOGIC_XOR,
    NAND_GLYPH: TokKind.LOGIC_NAND,
    NOR_GLYPH: TokKind.LOGIC_NOR,
    NOT_GLYPH: TokKind.LOGIC_NOT,
    CONCAT_GLYPH: TokKind.CONCAT,
    SHAPE_GLYPH: TokKind.SHAPE,
    MAX_GLYPH: TokKind.MAX,
    MIN_GLYPH: TokKind.MIN,
    FLOOR_GLYPH: TokKind.FLOOR,
    CEILING_GLYPH: TokKind.CEILING,
    NEAREST_GLYPH: TokKind.NEAREST,
    ROUNDED_GLYPH: TokKind.ROUNDED,
    POWER_GLYPH: TokKind.POWER,
    DIVIDES_GLYPH: TokKind.DIVIDES,
    NOT_DIVIDES_GLYPH: TokKind.NOT_DIVIDES,
    LIFT_OPEN_GLYPH: TokKind.LIFT_OPEN,
    LIFT_CLOSE_GLYPH: TokKind.LIFT_CLOSE,
    DEREF_GLYPH: TokKind.DEREF,
    EACH_GLYPH: TokKind.EACH,
    ITER_GLYPH: TokKind.ITER,
    TAKES_GLYPH: TokKind.TAKES,
    UNIT_GLYPH: TokKind.UNIT,
    LAMBDA_GLYPH: TokKind.LAMBDA,
    LIFETIME_GLYPH: TokKind.LIFETIME,
    SAT_ADD_GLYPH: TokKind.SAT_ADD,
    SAT_SUB_GLYPH: TokKind.SAT_SUB,
    SAT_MUL_GLYPH: TokKind.SAT_MUL,
    "+": TokKind.PLUS,
    "-": TokKind.MINUS,
    TIMES_GLYPH: TokKind.TIMES,
    DIVIDE_GLYPH: TokKind.DIVIDE,
    "%": TokKind.PERCENT,
    "#": TokKind.LENGTH,
    SHIFT_LEFT_GLYPH: TokKind.SHIFT_LEFT,
    SHIFT_RIGHT_GLYPH: TokKind.SHIFT_RIGHT,
    ROTATE_LEFT_GLYPH: TokKind.ROTATE_LEFT,
    ROTATE_RIGHT_GLYPH: TokKind.ROTATE_RIGHT,
    TUPLE_OPEN_GLYPH: TokKind.TUPLE_OPEN,
    TUPLE_CLOSE_GLYPH: TokKind.TUPLE_CLOSE,
    TAKE_GLYPH: TokKind.TAKE,
    NEXT_GLYPH: TokKind.NEXT,
    PREV_GLYPH: TokKind.PREV,
    SET_OPEN_GLYPH: TokKind.SET_OPEN,
    SET_CLOSE_GLYPH: TokKind.SET_CLOSE,
    SPREAD_GLYPH: TokKind.SPREAD,
    LABEL_GLYPH: TokKind.LABEL,
    ARRAY_OPEN_GLYPH: TokKind.ARRAY_OPEN,
    ARRAY_CLOSE_GLYPH: TokKind.ARRAY_CLOSE,
    QUESTION_GLYPH: TokKind.QUESTION,
    BOTTOM_GLYPH: TokKind.BOTTOM,
    RANGE_GLYPH: TokKind.RANGE,
}

_OPEN: Final[frozenset[TokKind]] = frozenset(
    (TokKind.LPAREN, TokKind.LBRACKET, TokKind.LBRACE, TokKind.AT_LBRACKET,
     TokKind.SET_OPEN, TokKind.TUPLE_OPEN, TokKind.ARRAY_OPEN))
_CLOSE: Final[frozenset[TokKind]] = frozenset(
    (TokKind.RPAREN, TokKind.RBRACKET, TokKind.RBRACE, TokKind.SET_CLOSE,
     TokKind.TUPLE_CLOSE, TokKind.ARRAY_CLOSE))

_SIMPLE_ESCAPES: Final[dict[str, str]] = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    "\\": "\\", "'": "'", '"': '"', "0": "\0",
}

_DIGITS: Final[dict[str, str]] = {
    "x": "0123456789abcdefABCDEF_",
    "o": "01234567_",
    "b": "01_",
}

_RADIX: Final[dict[str, int]] = {"x": 16, "o": 8, "b": 2}


def is_operator_opener(ch: str) -> bool:
    """Whether one character may open a pair a program defines.

    Unicode's opening punctuation, `Ps`, which is every bracket there is -- less
    the ones the grammar needs for itself, and less the ones the language already
    uses as an operator of one glyph.  `\N{LEFT CEILING}` is the second kind: Unicode calls it a
    bracket and this language calls it the larger of two, so a pair beginning with
    it could be defined and never written.  The array brackets and the collection
    brackets are neither, so a program may say what they mean for a type of its
    own, exactly as it may for `+`.
    """
    return (len(ch) == 1 and ch not in OPERATORS_NOT_NAMEABLE
            and ch not in OPERATOR_GLYPHS
            and unicodedata.category(ch) == OPENER_CATEGORY)


def is_operator_closer(ch: str) -> bool:
    """Whether one character may close a pair, which is `Pe` by the same rule."""
    return (len(ch) == 1 and ch not in OPERATORS_NOT_NAMEABLE
            and ch not in OPERATOR_GLYPHS
            and unicodedata.category(ch) == CLOSER_CATEGORY)


def is_operator_name(written: str) -> bool:
    """Whether what stands between the accents names an operator.

    One glyph, or a pair: an opening bracket and a closing one written together,
    which is what a pair is called.  Nothing checks that the two are each other's
    mirror -- Unicode does not say which closer belongs to which opener, and a
    program that pairs them oddly has written something odd rather than something
    ambiguous, since what closes a use is what the definition says closes it.
    """
    if len(written) == 1:
        return is_operator_glyph(written)
    return (len(written) == 2 and is_operator_opener(written[0])
            and is_operator_closer(written[1]))


def is_operator_glyph(ch: str) -> bool:
    """Whether one character may be an operator.

    Unicode decides most of it: a glyph in the symbol categories is one.  What
    the categories leave out is the ASCII punctuation the grammar needs for
    itself, which is the point of asking them rather than writing a list.

    A glyph the language already uses as an operator qualifies whatever Unicode
    calls it, because not all of them are symbols there: `-` is a dash, `^` a
    modifier symbol, `\N{LEFT CEILING}` and `\N{LEFT FLOOR}` are brackets.  That clause is what makes every
    builtin operator writable in the notation, which is what the specification
    needs in order to define them with it.
    """
    if len(ch) != 1 or ch in OPERATORS_NOT_NAMEABLE:
        return False
    return (unicodedata.category(ch) in OPERATOR_CATEGORIES
            or ch in OPERATOR_GLYPHS)


def _is_ident_start(ch: str) -> bool:
    """Whether *ch* may begin an identifier.

    The quad begins one too, and only a name the compiler provides: what makes
    such a name impossible to collide with is that a program cannot write the
    glyph, which the semantic analysis is where it is refused, so that the
    message is about the name and not about the character.
    """
    return ch.isalpha() or ch == "_" or ch == BUILTIN_GLYPH


def _is_ident_continue(ch: str) -> bool:
    """Whether *ch* may continue an identifier.

    A digit written raised does not, although it is a digit as far as the
    character tables are concerned: a raised number after a name is what that
    name is raised to the power of, and a name that could swallow it would make
    `a²` a name rather than a square.  The same goes for the letter written
    raised, which is the operator itself.
    """
    return ((ch.isalnum() or ch == "_" or ch == "'")
            and ch not in SUPERSCRIPT_DIGITS and ch != POWER_GLYPH)


def is_a_name(text: str) -> bool:
    """Whether *text* is one name a program may write, as the lexer would read it:
    not a keyword, and not a name the compiler provides."""
    return text != "" and _is_ident_start(text[0]) and text[0] != BUILTIN_GLYPH \
        and all(_is_ident_continue(ch) for ch in text[1:]) and text not in KEYWORDS


class Lexer:
    """Turns the text of one source file into a list of tokens."""

    def __init__(self, source: SourceFile, diags: DiagEngine,
                 lazy: bool = False, start: int = 0, end: int | None = None) -> None:
        self._source = source
        self._text = source.text if end is None else source.text[:end]
        self._base = source.base
        self._diags = diags
        self._pos = start
        self._tokens: list[Token] = []
        self._indents: list[int] = [0]
        self._bracket_depth = 0
        self._at_line_start = True
        #: Whether an indented function body is left as text (`BODY`) rather
        #: than lexed, and whether the definition being read is a function's.
        self._lazy = lazy
        self._top_is_a_function = False

    # -- helpers ---------------------------------------------------------------

    def _span(self, start: int, end: int) -> Span:
        """A span from two offsets within this file."""
        return Span(self._base + start, self._base + end)

    def _peek(self, ahead: int = 0) -> str:
        """The character *ahead* positions from the cursor, or the empty string."""
        index = self._pos + ahead
        return self._text[index] if index < len(self._text) else ""

    def _emit(self, kind: TokKind, start: int, **kwargs: object) -> None:
        """Append a token spanning from *start* to the cursor."""
        text = self._text[start:self._pos]
        self._tokens.append(Token(kind, self._span(start, self._pos), text, **kwargs))  # type: ignore[arg-type]

    # -- main loop -------------------------------------------------------------

    def run(self) -> list[Token]:
        """Tokenize the whole file."""
        while self._pos < len(self._text):
            if self._at_line_start and self._bracket_depth == 0:
                if self._lazy and self._a_body_begins() and self._set_aside():
                    continue
                if not self._handle_line_start():
                    continue
            if not self._lex_one():
                break
        return self._finish()

    def _finish(self) -> list[Token]:
        """Close any open block and append the end-of-file token."""
        end = len(self._text)
        if self._bracket_depth == 0 and self._tokens and self._tokens[-1].kind not in (
                TokKind.NEWLINE, TokKind.DEDENT, TokKind.BODY):
            self._tokens.append(Token(TokKind.NEWLINE, self._span(end, end)))
        while len(self._indents) > 1:
            self._indents.pop()
            self._tokens.append(Token(TokKind.DEDENT, self._span(end, end)))
        self._tokens.append(Token(TokKind.EOF, self._span(end, end)))
        return self._tokens

    def _handle_line_start(self) -> bool:
        """Measure the indentation of a line and emit INDENT or DEDENT.

        Returns ``False`` when the line turned out to be blank or a comment, in
        which case the caller restarts the loop.
        """
        start = self._pos
        width = 0
        while True:
            ch = self._peek()
            if ch == " ":
                width += 1
                self._pos += 1
            elif ch == "\t":
                self._diags.emit(D.LANG_SYNTAX_TAB_INDENT, self._span(self._pos, self._pos + 1))
                self._pos += 1
            else:
                break
        ch = self._peek()
        is_comment = self._text.startswith(COMMENT_GLYPH, self._pos)
        is_doc = self._text.startswith(COMMENT_GLYPH * 2, self._pos)
        if ch == "" or ch == "\n" or (is_comment and not is_doc):
            # A blank line and a plain comment carry no layout; a documentation
            # comment is a token, so it goes through the ordinary path.  A blank
            # line is still noted on the end of line before it, which is the one
            # place it means anything: after an attribute list at the top level.
            if ch == "\n" and not is_comment and self._tokens \
                    and self._tokens[-1].kind is TokKind.NEWLINE \
                    and not self._tokens[-1].text:
                self._tokens[-1] = replace(self._tokens[-1], text=BLANK_LINE)
            self._skip_to_end_of_line()
            return False
        self._at_line_start = False
        if width == 0:
            # A definition begins at the margin, and whether it is a function's
            # is what its line says.
            self._top_is_a_function = False
        if width > self._indents[-1] and self._tokens \
                and self._tokens[-1].kind is TokKind.NEWLINE \
                and self._continues_a_header():
            # A function's clauses, and the colon or brace its body begins with,
            # may stand on lines of their own below its first: such a line goes
            # on with the one before it, and opens nothing.
            self._tokens.pop()
            return True
        if width > self._indents[-1]:
            self._indents.append(width)
            self._tokens.append(Token(TokKind.INDENT, self._span(start, self._pos)))
        elif width < self._indents[-1]:
            while len(self._indents) > 1 and width < self._indents[-1]:
                self._indents.pop()
                self._tokens.append(Token(TokKind.DEDENT, self._span(start, self._pos)))
            if width != self._indents[-1]:
                self._diags.emit(D.LANG_SYNTAX_INCONSISTENT_INDENT,
                                 self._span(start, self._pos))
                self._indents.append(width)
        return True

    def _a_body_begins(self) -> bool:
        """Whether the line starting here is the first of a top-level function's
        indented body: the line before ended a function's header with a colon."""
        tokens = self._tokens
        return (self._top_is_a_function and len(self._indents) == 1
                and len(tokens) >= 2 and tokens[-1].kind is TokKind.NEWLINE
                and tokens[-2].kind is TokKind.COLON
                and self._text.startswith(" ", self._pos))

    def _set_aside(self) -> bool:
        """Leave the body that begins here as text, as one `BODY` token.

        Where it ends is found with no token made: at the first line at the left
        margin that is neither blank nor a comment -- comments at the margin just
        before it being the next definition's -- which is where the lexer would
        close the body.  The brackets are counted, with text and characters and
        comments taken out first; a body that does not come out even, which
        would mean a line at the margin inside a pair, is lexed here as it
        always was.

        The line that ends it is found by one search and not a line at a time,
        and the comments before it by walking back over the few lines between;
        and the brackets are counted once, over the whole body: what is taken
        out first never reaches past the end of a line, so a sum over the lines
        is the count over their text.  A module of two and a half thousand lines
        was otherwise read a line at a time in Python, every time it was
        imported.
        """
        text = self._text
        start = self._pos
        found = _AT_THE_MARGIN.search(text, start)
        end = at = len(text) if found is None else found.start()
        while at > start:
            # The line before *at*: its end, where its newline is, and its start.
            last = at - 1 if text[at - 1] == "\n" else at
            newline = text.rfind("\n", start, last)
            begin = start if newline < 0 else newline + 1
            line = text[begin:last]
            content = line.lstrip(" \t")
            if content and not content.startswith(COMMENT_GLYPH):
                break
            if content and len(content) == len(line):
                end = begin
            at = begin
        if end <= start or _depth_change(text[start:end]) != 0:
            return False
        self._tokens.append(Token(TokKind.BODY, self._span(start, end)))
        self._pos = end
        self._at_line_start = True
        return True

    def _continues_a_header(self) -> bool:
        """Whether the line starting here goes on with a function's first line.

        `pre(` and `post(` begin nothing else, and neither does a colon or a
        brace standing first on an indented line -- each is what a definition
        writes before its body.  The tree-sitter scanner asks the same.
        """
        rest = self._text[self._pos:self._pos + 6]
        for word in ("pre", "post"):
            if rest.startswith(word):
                after = rest[len(word):].lstrip(" ")
                if after.startswith("("):
                    return True
        return rest[:1] in (":", "{")

    def _skip_to_end_of_line(self) -> None:
        """Advance past the rest of the line, including its newline."""
        index = self._text.find("\n", self._pos)
        self._pos = len(self._text) if index < 0 else index + 1
        self._at_line_start = True

    def _lex_one(self) -> bool:
        """Lex one token.  Returns ``False`` at the end of the file."""
        text = self._text
        while True:
            if self._pos >= len(text):
                return False
            ch = text[self._pos]
            if ch == " " or ch == "\r" or ch == "\t":
                # A run of them at once, which is what stands between most
                # tokens: one search rather than a turn of this loop for each.
                self._pos = _BLANKS.match(text, self._pos).end()
                continue
            if ch == "\n":
                self._pos += 1
                if self._bracket_depth == 0:
                    self._at_line_start = True
                    if self._tokens and self._tokens[-1].kind is not TokKind.NEWLINE:
                        self._tokens.append(Token(TokKind.NEWLINE,
                                                  self._span(self._pos - 1, self._pos)))
                    elif self._tokens and self._blank_line_ended():
                        # Lines with nothing on them are one end of a line, and the
                        # one says that there was one: an attribute list before it
                        # is attached to nothing that follows.
                        self._tokens[-1] = replace(self._tokens[-1], text=BLANK_LINE)
                    return True
                continue
            if self._text.startswith(COMMENT_GLYPH, self._pos):
                self._lex_comment()
                if self._bracket_depth == 0:
                    return True
                continue
            break

        start = self._pos
        ch = self._peek()
        if ch == "@" and self._peek(1) == "[":
            self._pos += 2
            self._bracket_depth += 1
            self._emit(TokKind.AT_LBRACKET, start)
            return True
        if ch == "?" and self._peek(1) == "?":
            # Before the single character below, for the same reason '<=' comes
            # before '<': the longer reading is the one that was meant.
            self._pos += 2
            self._emit(TokKind.OR_ELSE, start)
            return True
        for ascii_form, glyph in (ASCII_SUBSTITUTES.items()
                                  if ch in _ASCII_FIRST else ()):
            # Before the single characters below, because '<=' begins with one
            # of them and the longer reading is the one that was meant.
            if not self._text.startswith(ascii_form, self._pos):
                continue
            self._pos += len(ascii_form)
            self._diags.emit(D.LANG_SYNTAX_ASCII_SUBSTITUTE, self._span(start, self._pos),
                             ascii=ascii_form, glyph=glyph)
            self._emit(_SIMPLE[glyph], start)
            return True
        kind = _SIMPLE.get(ch)
        if kind is not None:
            self._pos += 1
            if kind in _OPEN:
                self._bracket_depth += 1
            elif kind in _CLOSE:
                self._bracket_depth = max(0, self._bracket_depth - 1)
            self._emit(kind, start)
            return True
        if _is_ident_start(ch):
            self._lex_identifier(start)
            return True
        if ch in SUPERSCRIPT_DIGITS:
            self._lex_exponent(start)
            return True
        if _is_digit(ch):
            self._lex_number(start)
            return True
        if ch == NEGATIVE_GLYPH:
            # A raised minus begins a negative number where digits follow it and
            # a negative exponent where raised digits do.  Which it is, is the
            # next character and nothing else: the two are written alike and
            # stand in different places.
            if self._peek(1) in SUPERSCRIPT_DIGITS:
                self._lex_exponent(start)
            else:
                self._lex_number(start)
            return True
        if ch == OPNAME_GLYPH:
            self._lex_operator_name(start)
            return True
        if ch == '"':
            self._lex_string(start)
            return True
        if ch == "'":
            self._lex_character(start)
            return True
        if is_operator_glyph(ch) or is_operator_opener(ch) \
                or is_operator_closer(ch):
            # A glyph the language gives no meaning, which is an operator a
            # program may have defined.  It is a token here and a question for the
            # checker: whether anything defined it is not something the lexer can
            # know, and a glyph that turns out to be nobody's is reported there
            # with the one thing worth saying about it.
            #
            # A bracket counts its depth as the grammar's own do, so that a line
            # may be broken inside a pair a program defined exactly as it may
            # inside one the language has.
            kind = TokKind.OPERATOR
            if is_operator_opener(ch):
                kind = TokKind.OPEN_OPERATOR
                self._bracket_depth += 1
            elif is_operator_closer(ch):
                kind = TokKind.CLOSE_OPERATOR
                self._bracket_depth = max(0, self._bracket_depth - 1)
            self._pos += 1
            self._tokens.append(Token(kind, self._span(start, self._pos),
                                      text=ch))
            return True
        self._pos += 1
        self._diags.emit(D.LANG_SYNTAX_UNEXPECTED_CHAR, self._span(start, self._pos),
                         char="".join(("'", ch, "' (U+", format(ord(ch), "04X"), ")")))
        return True

    def _lex_operator_name(self, start: int) -> None:
        """Lex `` `+` ``: an operator standing where a name goes.

        One glyph between two grave accents.  The accent is not an operator and
        cannot begin one, so what closes the name is the next accent and the name
        cannot run past the end of its line -- which is what makes an unclosed one
        a mistake reported here rather than a file read to its end looking for a
        partner.
        """
        self._pos += 1
        end = self._text.find(OPNAME_GLYPH, self._pos)
        line = self._text.find("\n", self._pos)
        if end < 0 or (0 <= line < end):
            stop = len(self._text) if line < 0 else line
            self._pos = stop
            self._diags.emit(D.LANG_OPNAME_UNTERMINATED,
                             self._span(start, self._pos))
            return
        written = self._text[self._pos:end]
        self._pos = end + 1
        if len(written) not in (1, 2):
            self._diags.emit(D.LANG_OPNAME_NOT_ONE_GLYPH,
                             self._span(start, self._pos), count=str(len(written)))
            return
        if not is_operator_name(written):
            self._diags.emit(D.LANG_OPNAME_NOT_AN_OPERATOR,
                             self._span(start, self._pos), glyph=written)
            return
        self._tokens.append(Token(TokKind.OPNAME, self._span(start, self._pos),
                                 text=written))

    def _lex_comment(self) -> None:
        """Lex a comment.  Documentation comments become tokens; others do not."""
        start = self._pos
        is_doc = self._text.startswith(COMMENT_GLYPH * 2, self._pos)
        index = self._text.find("\n", self._pos)
        end = len(self._text) if index < 0 else index
        if is_doc:
            self._pos = end
            self._emit(TokKind.DOC_COMMENT, start)
        self._pos = len(self._text) if index < 0 else index + 1
        if self._bracket_depth == 0:
            self._at_line_start = True
            if self._tokens and self._tokens[-1].kind is not TokKind.NEWLINE:
                self._tokens.append(Token(TokKind.NEWLINE, self._span(end, self._pos)))

    def _blank_line_ended(self) -> bool:
        """Whether the line the end of line just read ended has nothing on it."""
        before = self._text.rfind("\n", 0, self._pos - 1)
        return self._text[before + 1:self._pos - 1].strip(" \t\r") == ""

    def _lex_identifier(self, start: int) -> None:
        """Lex an identifier or a keyword.

        The first character was only looked at, so it is stepped over here: what
        may begin a name and what may go on with it are two questions, and the
        quad that begins one the compiler provides answers only the first.
        """
        # A name the compiler provides may carry a key: `⎕sc@write` is one name
        # and the compiler looks the whole of it up.  Only such a name may, so
        # the mark stays what it is everywhere else and `a@b` is still two
        # things with nothing between them.  What may go on with a name is
        # matched as one run, by a pattern saying what `_is_ident_continue`
        # does: a word character -- what `isalnum` is, and the underscore --
        # or the prime, and not a raised numeral or the raised letter.
        provided = self._text[start] == BUILTIN_GLYPH
        self._pos = (_PROVIDED_GOES_ON if provided else _NAME_GOES_ON).match(
            self._text, start + 1).end()
        text = self._text[start:self._pos]
        kind = KEYWORDS.get(text, TokKind.IDENT)
        if kind is TokKind.KW_FN and len(self._indents) == 1 \
                and self._bracket_depth == 0:
            self._top_is_a_function = True
        self._emit(kind, start)

    def _lex_exponent(self, start: int) -> None:
        """Lex a number written raised, which is an exponent.

        There is no radix and no type suffix, and neither is an omission: an
        exponent is a count of how many times something is multiplied by
        itself, so it is a number of no particular width, and a raised `0x` is
        not a thing anybody writes.  A raised minus makes it negative, which is
        read here and refused where it is checked -- what is wrong with it is
        what it would mean and not how it is written.
        """
        negative = self._peek() == NEGATIVE_GLYPH
        if negative:
            self._pos += 1
        value = 0
        while self._peek() in SUPERSCRIPT_DIGITS:
            value = value * 10 + SUPERSCRIPT_DIGITS[self._peek()]
            self._pos += 1
        self._emit(TokKind.EXPONENT, start,
                   int_value=-value if negative else value)

    def _lex_number(self, start: int) -> None:
        """Lex an integer literal, in decimal or with a base prefix.

        A literal may name its type with a suffix: ``3u8`` is a ``u8``.  The
        suffix is the type's own name, so there is nothing to look up, and it
        cannot be mistaken for the digits: no name of an integer type begins
        with one, and none of the letters a hexadecimal literal uses starts one
        either.

        A leading superscript minus makes the literal negative.  It is read here
        rather than as an operator because that is what it is: part of how the
        number is written, with nothing allowed between it and the digits.
        """
        negative = self._peek() == NEGATIVE_GLYPH
        if negative:
            self._pos += 1
            if not _is_digit(self._peek()):
                self._diags.emit(D.LANG_SYNTAX_LONELY_NEGATIVE,
                                 self._span(start, self._pos))
                self._emit(TokKind.INT, start, int_value=0, int_type=None)
                return
        radix = 10
        digits = "0123456789_"
        if self._peek() == "0" and self._peek(1).lower() in _DIGITS:
            marker = self._peek(1).lower()
            radix = _RADIX[marker]
            digits = _DIGITS[marker]
            self._pos += 2
        digits_start = self._pos
        while self._peek() in digits and self._peek() != "":
            self._pos += 1
        if self._begins_a_fraction(radix):
            self._lex_fraction(start, digits_start, radix, negative)
            return
        body = self._text[digits_start:self._pos]
        suffix = self._lex_literal_suffix(either=True)
        text = self._text[start:self._pos]
        try:
            value = int(body.replace("_", ""), radix)
        except ValueError:
            self._diags.emit(D.LANG_SYNTAX_UNEXPECTED_CHAR, self._span(start, self._pos),
                             char="".join(("'", text, "'")))
            value = 0
        if suffix in FLOAT_TYPE_NAMES:
            # Whole digits and a floating-point suffix: `3f64` is the number
            # three as a float, which is worth being able to write without
            # having to write a point that says nothing.
            self._emit(TokKind.FLOAT, start,
                       float_value=float(-value if negative else value),
                       float_type=suffix)
            return
        self._emit(TokKind.INT, start, int_value=-value if negative else value,
                   int_type=suffix)

    def _begins_a_fraction(self, radix: int) -> bool:
        """Whether what follows the digits makes this a floating-point literal.

        A point with a digit after it, or an exponent -- which for a decimal
        literal is `e` and for a hexadecimal one `p`, as in C.  The digit after
        the point matters: `1.x` is a member of something and not a number, and
        a language that reads it as a number has to guess.
        """
        if self._peek() == "." and self._digit_follows(1, radix):
            return True
        marker = "p" if radix == 16 else "e"
        if self._peek().lower() != marker:
            return False
        offset = 2 if self._peek(1) in "+-" else 1
        return _is_digit(self._peek(offset))

    def _digit_follows(self, offset: int, radix: int) -> bool:
        """Whether the character at *offset* is a digit of this base."""
        found = self._peek(offset)
        return found != "" and found in _DIGITS.get(
            "x" if radix == 16 else "", "0123456789_")

    def _lex_fraction(self, start: int, digits_start: int, radix: int,
                      negative: bool) -> None:
        """Lex the rest of a floating-point literal, decimal or hexadecimal.

        Both forms are the C ones, and the value is read by Python's own reader
        of them, which is the same reader with the same rounding -- so a literal
        means what it says rather than what a hand-written parser made of it.
        A hexadecimal literal is exact by construction, which is why C has the
        form and why the specification asks for it.
        """
        if self._peek() == ".":
            self._pos += 1
            while self._peek() in _DIGITS.get("x" if radix == 16 else "",
                                              "0123456789_") and self._peek() != "":
                self._pos += 1
        marker = "p" if radix == 16 else "e"
        if self._peek().lower() == marker:
            self._pos += 1
            if self._peek() in "+-":
                self._pos += 1
            while _is_digit(self._peek()) or self._peek() == "_":
                self._pos += 1
        body = self._text[digits_start:self._pos].replace("_", "")
        suffix = self._lex_literal_suffix(floating=True)
        text = "".join(("0x", body)) if radix == 16 else body
        try:
            value = float.fromhex(text) if radix == 16 else float(text)
        except OverflowError:
            # Too large for a double, which is what a decimal one too large
            # comes to as well: the literal stands for an infinity.
            value = math.inf
        except ValueError:
            self._diags.emit(D.LANG_SYNTAX_UNEXPECTED_CHAR,
                             self._span(start, self._pos),
                             char="".join(("'", self._text[start:self._pos], "'")))
            value = 0.0
        self._emit(TokKind.FLOAT, start, float_value=-value if negative else value,
                   float_type=suffix)

    def _lex_literal_suffix(self, floating: bool = False,
                            either: bool = False) -> str | None:
        """Read the type a literal names, if it names one."""
        if not _is_ident_start(self._peek()):
            return None
        start = self._pos
        while _is_ident_continue(self._peek()):
            self._pos += 1
        suffix = self._text[start:self._pos]
        wanted = (INTEGER_TYPE_NAMES | FLOAT_TYPE_NAMES if either
                  else FLOAT_TYPE_NAMES if floating else INTEGER_TYPE_NAMES)
        if suffix not in wanted:
            self._diags.emit(D.LANG_SYNTAX_BAD_LITERAL_SUFFIX,
                             self._span(start, self._pos), suffix=suffix)
            return None
        return suffix

    def _lex_string(self, start: int) -> None:
        """Lex a string literal with C-style escapes."""
        self._pos += 1
        parts: list[str] = []
        while True:
            ch = self._peek()
            if ch == "" or ch == "\n":
                self._diags.emit(D.LANG_SYNTAX_UNTERMINATED_STRING,
                                 self._span(start, self._pos))
                break
            if ch == '"':
                self._pos += 1
                break
            if ch == "\\":
                parts.append(self._lex_escape())
                continue
            parts.append(ch)
            self._pos += 1
        self._emit(TokKind.STRING, start, str_value="".join(parts))

    def _lex_character(self, start: int) -> None:
        """Lex a character literal, which is one code point between apostrophes.

        The same escapes a string takes, for the same reason: what may be
        written down is one thing however it is quoted.  A code point is one
        character to this compiler because Python strings are sequences of code
        points, which is what makes "exactly one" a thing to count rather than a
        thing to decode.
        """
        self._pos += 1
        found: list[str] = []
        closed = False
        while True:
            ch = self._peek()
            if ch == "" or ch == "\n":
                break
            if ch == "'":
                self._pos += 1
                closed = True
                break
            if ch == "\\":
                found.append(self._lex_escape())
                continue
            found.append(ch)
            self._pos += 1
        if not closed:
            self._diags.emit(D.LANG_SYNTAX_UNTERMINATED_CHARACTER,
                             self._span(start, self._pos))
            self._emit(TokKind.CHAR, start, int_value=0)
            return
        if len(found) != 1:
            self._diags.emit(D.LANG_SYNTAX_CHARACTER_IS_ONE,
                             self._span(start, self._pos), found=len(found))
            self._emit(TokKind.CHAR, start, int_value=0)
            return
        # An escape that denotes nothing was reported where it was written, and
        # stands for nought.
        self._emit(TokKind.CHAR, start, int_value=ord(found[0]) if found[0] else 0)

    def _lex_escape(self) -> str:
        """Lex one escape sequence and return the character it denotes."""
        start = self._pos
        self._pos += 1
        ch = self._peek()
        simple = _SIMPLE_ESCAPES.get(ch)
        if simple is not None:
            self._pos += 1
            return simple
        if ch == "u" or ch == "U":
            want = 4 if ch == "u" else 8
            self._pos += 1
            digits = ""
            while len(digits) < want and _is_hex_digit(self._peek()):
                digits += self._peek()
                self._pos += 1
            # A surrogate is a number and not a code point, so an escape naming
            # one names nothing, as one beyond the last does: a string holding
            # it would be no UTF-8 at all.
            if len(digits) == want and int(digits, 16) <= _MAX_CODE_POINT \
                    and not 0xD800 <= int(digits, 16) <= 0xDFFF:
                return chr(int(digits, 16))
            self._diags.emit(D.LANG_SYNTAX_BAD_ESCAPE, self._span(start, self._pos), char=ch)
            return ""
        self._pos += 1 if ch != "" else 0
        self._diags.emit(D.LANG_SYNTAX_BAD_ESCAPE, self._span(start, self._pos), char=ch)
        return ""


#: The largest code point there is: an escape naming one beyond it names nothing.
_MAX_CODE_POINT: Final[int] = 0x10FFFF


def _is_digit(ch: str) -> bool:
    """Whether *ch* is a digit a number is written with: the ten of ASCII and no
    other script's, which `str.isdigit` would also take."""
    return len(ch) == 1 and "0" <= ch <= "9"


def _is_hex_digit(ch: str) -> bool:
    """Whether *ch* is a digit of a hexadecimal escape.  Nothing, at the end of
    the text, is none -- which `in` over a string would say it is."""
    return len(ch) == 1 and ch in "0123456789abcdefABCDEF"


def tokenize(source: SourceFile, diags: DiagEngine,
             lazy: bool = False) -> list[Token]:
    """Tokenize *source*; where *lazy* says so, leave each top-level function's
    indented body as one `BODY` token for `tokenize_body` to lex later."""
    return Lexer(source, diags, lazy=lazy).run()


def tokenize_body(source: SourceFile, span: Span, diags: DiagEngine) -> list[Token]:
    """The tokens of a body `tokenize` left as text: from its first line to its
    end, indentation and all, as the lexer would have made them there."""
    start = span.start - source.base
    return Lexer(source, diags, start=start, end=span.end - source.base).run()


#: What `_depth_change` takes out of its lines before counting: text, a character
#: written out (not the mark after a type parameter), and a comment.  Written
#: raw, so that what the pattern says of a backslash is the pattern's: an escape
#: in text takes the character after it, and a quote escaped does not end it.
#: What stands before a character's quote is looked at once the quote is taken,
#: so that every way the pattern can begin is one character -- which the engine
#: looks for before it tries anything, rather than trying each way everywhere.
_NOT_COUNTED: Final = re.compile(
    "|".join((r'"(?:[^"\\\n]|\\.)*"',
              r"'(?<!\w')(?<!'')(?:\\[^'\n]*|[^'\\\n])'",
              "".join((COMMENT_GLYPH, ".*$")))), re.MULTILINE)
#: A run of what separates tokens on a line, skipped at once.
_BLANKS: Final = re.compile("[ \\r\\t]+")
#: What the ASCII spellings of glyphs begin with: only a token beginning with one
#: of these can be one of them.
_ASCII_FIRST: Final = frozenset(form[0] for form in ASCII_SUBSTITUTES)
#: What goes on with a name: `_is_ident_continue` as a pattern, and with the key
#: mark as well for a name the compiler provides.
_GOES_ON: Final = "".join(("[^\\W", "".join(SUPERSCRIPT_DIGITS), POWER_GLYPH, "]|'"))
_NAME_GOES_ON: Final = re.compile("".join(("(?:", _GOES_ON, ")*")))
_PROVIDED_GOES_ON: Final = re.compile("".join(("(?:", _GOES_ON, "|", re.escape(KEY_GLYPH),
                                               ")*")))
#: The first character of a line at the left margin that is neither blank nor a
#: comment, which is where a body set aside ends.
_AT_THE_MARGIN: Final = re.compile("".join(("^[^ \\t\\n", COMMENT_GLYPH, "]")),
                                   re.MULTILINE)
_GLYPHS: Final = re.compile("[^\\x00-\\x7f]")
_ASCII_OPEN: Final = "([{"
_ASCII_CLOSE: Final = ")]}"


def _depth_change(lines: str) -> int:
    """How much deeper in brackets some lines of a body leave the next one."""
    counted = _NOT_COUNTED.sub("", lines)
    change = sum(counted.count(one) for one in _ASCII_OPEN) \
        - sum(counted.count(one) for one in _ASCII_CLOSE)
    for ch in _GLYPHS.findall(counted):
        # In the order the lexer asks: a glyph of the language is what it is,
        # and only one the language does not have may be a pair's.
        kind = _SIMPLE.get(ch)
        if kind is not None:
            change += (kind in _OPEN) - (kind in _CLOSE)
        elif is_operator_opener(ch):
            change += 1
        elif is_operator_closer(ch):
            change -= 1
    return change
