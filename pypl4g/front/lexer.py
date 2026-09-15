"""Lexical analysis, including the layout rules.

The lexer produces the ``INDENT``, ``DEDENT`` and ``NEWLINE`` tokens that the
layout-based syntax needs, in the manner of Python.  Inside brackets and inside
an explicit brace-enclosed block, line structure carries no meaning and those
tokens are not produced.
"""

from __future__ import annotations

from typing import Final

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..source.location import Span
from ..source.manager import SourceFile
from .token import (ABOVE_NOT_ALIKE_GLYPH, ABOVE_OR_ALIKE_GLYPH, ALIKE_GLYPH,
                    BOTTOM_GLYPH, BUILTIN_GLYPH,
                    AND_GLYPH, ARROW_GLYPH, ASCII_SUBSTITUTES, ASSIGN_GLYPH,
                    BELOW_NOT_ALIKE_GLYPH, BELOW_OR_ALIKE_GLYPH,
                    COMMENT_GLYPH, CONCAT_GLYPH, FLOAT_TYPE_NAMES,
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
    SAT_ADD_GLYPH: TokKind.SAT_ADD,
    SAT_SUB_GLYPH: TokKind.SAT_SUB,
    SAT_MUL_GLYPH: TokKind.SAT_MUL,
    "+": TokKind.PLUS,
    "-": TokKind.MINUS,
    TIMES_GLYPH: TokKind.TIMES,
    DIVIDE_GLYPH: TokKind.DIVIDE,
    "%": TokKind.PERCENT,
    SHIFT_LEFT_GLYPH: TokKind.SHIFT_LEFT,
    SHIFT_RIGHT_GLYPH: TokKind.SHIFT_RIGHT,
    ROTATE_LEFT_GLYPH: TokKind.ROTATE_LEFT,
    ROTATE_RIGHT_GLYPH: TokKind.ROTATE_RIGHT,
    TUPLE_OPEN_GLYPH: TokKind.TUPLE_OPEN,
    TUPLE_CLOSE_GLYPH: TokKind.TUPLE_CLOSE,
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


def _is_ident_start(ch: str) -> bool:
    """Whether *ch* may begin an identifier.

    The quad begins one too, and only a name the compiler provides: what makes
    such a name impossible to collide with is that a program cannot write the
    glyph, which the semantic analysis is where it is refused, so that the
    message is about the name and not about the character.
    """
    return ch.isalpha() or ch == "_" or ch == BUILTIN_GLYPH


def _is_ident_continue(ch: str) -> bool:
    """Whether *ch* may continue an identifier."""
    return ch.isalnum() or ch == "_"


class Lexer:
    """Turns the text of one source file into a list of tokens."""

    def __init__(self, source: SourceFile, diags: DiagEngine) -> None:
        self._source = source
        self._text = source.text
        self._base = source.base
        self._diags = diags
        self._pos = 0
        self._tokens: list[Token] = []
        self._indents: list[int] = [0]
        self._bracket_depth = 0
        self._at_line_start = True

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
                if not self._handle_line_start():
                    continue
            if not self._lex_one():
                break
        return self._finish()

    def _finish(self) -> list[Token]:
        """Close any open block and append the end-of-file token."""
        end = len(self._text)
        if self._bracket_depth == 0 and self._tokens and self._tokens[-1].kind not in (
                TokKind.NEWLINE, TokKind.DEDENT):
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
            # comment is a token, so it goes through the ordinary path.
            self._skip_to_end_of_line()
            return False
        self._at_line_start = False
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

    def _skip_to_end_of_line(self) -> None:
        """Advance past the rest of the line, including its newline."""
        index = self._text.find("\n", self._pos)
        self._pos = len(self._text) if index < 0 else index + 1
        self._at_line_start = True

    def _lex_one(self) -> bool:
        """Lex one token.  Returns ``False`` at the end of the file."""
        while True:
            ch = self._peek()
            if ch == "":
                return False
            if ch == " " or ch == "\r":
                self._pos += 1
                continue
            if ch == "\t":
                self._pos += 1
                continue
            if ch == "\n":
                self._pos += 1
                if self._bracket_depth == 0:
                    self._at_line_start = True
                    if self._tokens and self._tokens[-1].kind is not TokKind.NEWLINE:
                        self._tokens.append(Token(TokKind.NEWLINE,
                                                  self._span(self._pos - 1, self._pos)))
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
        for ascii_form, glyph in ASCII_SUBSTITUTES.items():
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
        if ch.isdigit():
            self._lex_number(start)
            return True
        if ch == NEGATIVE_GLYPH:
            self._lex_number(start)
            return True
        if ch == '"':
            self._lex_string(start)
            return True
        if ch == "'":
            self._lex_character(start)
            return True
        self._pos += 1
        self._diags.emit(D.LANG_SYNTAX_UNEXPECTED_CHAR, self._span(start, self._pos),
                         char="".join(("'", ch, "' (U+", format(ord(ch), "04X"), ")")))
        return True

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

    def _lex_identifier(self, start: int) -> None:
        """Lex an identifier or a keyword.

        The first character was only looked at, so it is stepped over here: what
        may begin a name and what may go on with it are two questions, and the
        quad that begins one the compiler provides answers only the first.
        """
        self._pos += 1
        while _is_ident_continue(self._peek()):
            self._pos += 1
        text = self._text[start:self._pos]
        self._emit(KEYWORDS.get(text, TokKind.IDENT), start)

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
            if not self._peek().isdigit():
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
        return self._peek(offset).isdigit()

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
            while self._peek().isdigit() or self._peek() == "_":
                self._pos += 1
        body = self._text[digits_start:self._pos].replace("_", "")
        suffix = self._lex_literal_suffix(floating=True)
        text = "".join(("0x", body)) if radix == 16 else body
        try:
            value = float.fromhex(text) if radix == 16 else float(text)
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
        self._emit(TokKind.CHAR, start, int_value=ord(found[0]))

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
            while len(digits) < want and self._peek() in "0123456789abcdefABCDEF":
                digits += self._peek()
                self._pos += 1
            if len(digits) == want:
                return chr(int(digits, 16))
            self._diags.emit(D.LANG_SYNTAX_BAD_ESCAPE, self._span(start, self._pos), char=ch)
            return ""
        self._pos += 1 if ch != "" else 0
        self._diags.emit(D.LANG_SYNTAX_BAD_ESCAPE, self._span(start, self._pos), char=ch)
        return ""


def tokenize(source: SourceFile, diags: DiagEngine) -> list[Token]:
    """Tokenize *source*."""
    return Lexer(source, diags).run()
