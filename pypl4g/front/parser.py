"""Recursive-descent parser.

The grammar is context-free and definitions need not be processed in order, so
the parser produces one syntax tree per file and leaves every question of
meaning to the semantic analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..source.location import INVALID_SPAN, Span
from . import ast
from .token import (COMMENT_GLYPH, IMPORT_NAME, LASTING_WORD,
                    TokKind, Token, WILDCARD_NAME)

#: Tokens at which error recovery stops, because a new definition can begin there.
_RECOVERY: Final[frozenset[TokKind]] = frozenset(
    (TokKind.KW_FN, TokKind.KW_LET, TokKind.KW_TYPE, TokKind.KW_UNIT,
     TokKind.AT_LBRACKET, TokKind.EOF))


@dataclass(frozen=True, slots=True)
class _Operator:
    """One row of the precedence table."""

    op: ast.BinaryOp
    #: How tightly it binds.  A larger number binds tighter, and the numbers are
    #: spaced so that a level can be added between two without renumbering.
    precedence: int
    #: Whether `a op b op c` means `a op (b op c)` rather than `(a op b) op c`.
    right_associative: bool = False
    #: Whether `a op b op c` means nothing at all and is refused.  A comparison
    #: answers with a truth value, so a second comparison would be asking about
    #: that answer -- which is almost never what was meant, and is a mistake a
    #: language can simply not have.
    non_associative: bool = False


#: What may stand between two operands, and how tightly each binds.
#:
#: Loosest first.  The **logical** operators come first because what they join
#: is whole questions: `a < b ∧ c < d` reads as it looks, which it would not if
#: they bound tighter than the comparisons.  Among themselves they take the
#: order of the bitwise three they mirror -- "and" tighter than "exclusive or"
#: tighter than "or" -- so that one set of habits serves for both.  `and` and
#: `or` sit exactly where `∧` and `∨` do, since they say the same thing and
#: differ only in what they evaluate.
#:
#: `⊼` and `⊽` are neither associative nor conventional, so they share a level of
#: their own and do not associate: `a ⊼ b ⊼ c` has two meanings and is refused
#: rather than given one of them.
#:
#: The **comparisons** come next, all six on one level as in Go and Rust.
#: Splitting equality from ordering, as C does, only decides what `a < b = c`
#: means, and that expression is refused here rather than given a meaning.
#:
#: The **bitwise** order is the one C settled on and Rust, Go and Zig kept.  The
#: comparisons bind looser than all of them, which is where C put them wrongly
#: and where every language since has put them: `a & b = c` asks about `a & b`,
#: not about `b = c`.
#:
#: The **arithmetic** binds tightest of all, multiplication tighter than
#: addition, as it does everywhere and as it does in writing.  It is tighter
#: than the bitwise operators, which is C's order too and the one place C's
#: order was not a mistake.
#: Where a range binds, which is not in the table below: a range is not a
#: binary operator, taking two ends or three.  It sits between the comparisons
#: and the bitwise operators, so that the arithmetic in `1…n-1` binds to the end
#: and the whole range is something a comparison could be asked about.
_RANGE_PRECEDENCE: Final[int] = 7

#: Where an operator the language gives no meaning binds: as tightly as
#: multiplying, and to the left.
#:
#: One level for all of them, because a program cannot declare a level and should
#: not be able to -- the glyph set is the language's and so is its table.  Tight
#: rather than loose so that a reader who does not know the glyph still knows how
#: the line groups: `a \N{CIRCLED ASTERISK OPERATOR} b + c` is `(a \N{CIRCLED ASTERISK OPERATOR} b) + c`, which is what a novel glyph
#: between two things looks like it means.  Stated against something a reader
#: knows rather than given a level of its own, and looser than `\N{SUPERSCRIPT LATIN SMALL LETTER N}`, because a
#: raised number is written flush against what it raises.
_FRESH_PRECEDENCE: Final[int] = 50

_BINARY_OPERATORS: Final[dict[TokKind, _Operator]] = {
    TokKind.LOGIC_OR: _Operator(ast.BinaryOp.LOGIC_OR, 1),
    TokKind.KW_OR: _Operator(ast.BinaryOp.SHORT_OR, 1),
    TokKind.LOGIC_XOR: _Operator(ast.BinaryOp.LOGIC_XOR, 2),
    TokKind.LOGIC_AND: _Operator(ast.BinaryOp.LOGIC_AND, 3),
    TokKind.KW_AND: _Operator(ast.BinaryOp.SHORT_AND, 3),
    TokKind.LOGIC_NAND: _Operator(ast.BinaryOp.LOGIC_NAND, 4, non_associative=True),
    TokKind.LOGIC_NOR: _Operator(ast.BinaryOp.LOGIC_NOR, 4, non_associative=True),
    TokKind.EQUALS: _Operator(ast.BinaryOp.EQUAL, 5, non_associative=True),
    TokKind.NOT_EQUAL: _Operator(ast.BinaryOp.NOT_EQUAL, 5, non_associative=True),
    TokKind.LESS: _Operator(ast.BinaryOp.LESS, 5, non_associative=True),
    TokKind.GREATER: _Operator(ast.BinaryOp.GREATER, 5, non_associative=True),
    TokKind.LESS_EQUAL: _Operator(ast.BinaryOp.LESS_EQUAL, 5, non_associative=True),
    # Tighter than the comparisons and looser than everything that computes a
    # number, so that `a ÷ b ?? c + d` takes the whole of each side, and
    # `x = a ÷ b ?? c` asks about what the division came to.  Right
    # associative, so that `a ?? b ?? c` is "a, or else b, or else c" -- which
    # is the only reading of it that is well typed.
    TokKind.OR_ELSE: _Operator(ast.BinaryOp.OR_ELSE, 6, right_associative=True),
    # Whether one number divides another answers a truth value about two
    # numbers, which is a comparison's shape -- so it binds where a comparison
    # binds and joins two and no more: `a \N{DIVIDES} b \N{DIVIDES} c` would be asking whether
    # `a` divides a truth value.
    TokKind.DIVIDES: _Operator(ast.BinaryOp.DIVIDES, 5, non_associative=True),
    TokKind.NOT_DIVIDES: _Operator(ast.BinaryOp.NOT_DIVIDES, 5,
                                   non_associative=True),
    TokKind.ALIKE: _Operator(ast.BinaryOp.ALIKE, 5, non_associative=True),
    TokKind.UNALIKE: _Operator(ast.BinaryOp.UNALIKE, 5, non_associative=True),
    TokKind.BELOW_OR_ALIKE: _Operator(ast.BinaryOp.BELOW_OR_ALIKE, 5,
                                      non_associative=True),
    TokKind.ABOVE_OR_ALIKE: _Operator(ast.BinaryOp.ABOVE_OR_ALIKE, 5,
                                      non_associative=True),
    TokKind.BELOW_NOT_ALIKE: _Operator(ast.BinaryOp.BELOW_NOT_ALIKE, 5,
                                       non_associative=True),
    TokKind.ABOVE_NOT_ALIKE: _Operator(ast.BinaryOp.ABOVE_NOT_ALIKE, 5,
                                       non_associative=True),
    TokKind.GREATER_EQUAL: _Operator(ast.BinaryOp.GREATER_EQUAL, 5,
                                     non_associative=True),
    # Joining two arrays binds looser than everything that works out what goes
    # in one and tighter than every comparison, so that `a + 1u8 \N{DOUBLE PLUS} b` joins
    # what the two sides came to and `x \N{DOUBLE PLUS} y = z` asks about the whole join.
    TokKind.CONCAT: _Operator(ast.BinaryOp.CONCAT, 8),
    # Making something of a shape binds tighter than joining two and looser than
    # everything that works out what goes in one, so `2 ⍴ a + 1u8` makes two of
    # what the addition came to and `s ⍴ v ⧺ w` joins what it made to `w`.
    TokKind.SHAPE: _Operator(ast.BinaryOp.SHAPE, 9),
    TokKind.PIPE: _Operator(ast.BinaryOp.BIT_OR, 10),
    # The larger and the smaller of two bind looser than everything that works
    # out a number and tighter than the bitwise operators, so `a + b \N{LEFT CEILING} c` takes
    # the larger of what the addition came to and `c`.
    TokKind.MAX: _Operator(ast.BinaryOp.MAX, 35),
    TokKind.MIN: _Operator(ast.BinaryOp.MIN, 35),
    TokKind.CARET: _Operator(ast.BinaryOp.BIT_XOR, 20),
    TokKind.AMPERSAND: _Operator(ast.BinaryOp.BIT_AND, 30),
    TokKind.PLUS: _Operator(ast.BinaryOp.ADD, 40),
    TokKind.MINUS: _Operator(ast.BinaryOp.SUBTRACT, 40),
    TokKind.SAT_ADD: _Operator(ast.BinaryOp.SAT_ADD, 40),
    TokKind.SAT_SUB: _Operator(ast.BinaryOp.SAT_SUB, 40),
    TokKind.TIMES: _Operator(ast.BinaryOp.MULTIPLY, 50),
    TokKind.DIVIDE: _Operator(ast.BinaryOp.DIVIDE, 50),
    TokKind.PERCENT: _Operator(ast.BinaryOp.REMAINDER, 50),
    # Moving bits sideways binds where multiplying does, which is where Go puts
    # it.  C binds it looser than *addition*, so that `a << 1 + b` shifts by
    # `1 + b`, which is a defect of the same family as binding comparison
    # tighter than the bitwise operators and is not inherited here either.
    TokKind.SHIFT_LEFT: _Operator(ast.BinaryOp.SHIFT_LEFT, 50),
    TokKind.SHIFT_RIGHT: _Operator(ast.BinaryOp.SHIFT_RIGHT, 50),
    TokKind.ROTATE_LEFT: _Operator(ast.BinaryOp.ROTATE_LEFT, 50),
    TokKind.ROTATE_RIGHT: _Operator(ast.BinaryOp.ROTATE_RIGHT, 50),
    TokKind.SAT_MUL: _Operator(ast.BinaryOp.SAT_MUL, 50),
    # Raising to a power binds tighter than multiplying, as it does on paper and
    # in every language that has it, and it is right associative for the same
    # reason: `a ⁿ b ⁿ c` is `a` raised to what `b ⁿ c` came to, which is the
    # only reading that is not a longer way of writing `a ⁿ (b × c)`.
    TokKind.POWER: _Operator(ast.BinaryOp.POWER, 60, right_associative=True),
}

#: What may stand before an operand.  These bind tighter than anything above.
_UNARY_OPERATORS: Final[dict[TokKind, ast.UnaryOp]] = {
    TokKind.TILDE: ast.UnaryOp.BIT_NOT,
    TokKind.LOGIC_NOT: ast.UnaryOp.LOGIC_NOT,
    TokKind.LENGTH: ast.UnaryOp.LENGTH,
    TokKind.SHAPE: ast.UnaryOp.SHAPE,
    TokKind.MAX: ast.UnaryOp.MAX,
    TokKind.MIN: ast.UnaryOp.MIN,
    TokKind.DIVIDES: ast.UnaryOp.DIVIDES,
    TokKind.NOT_DIVIDES: ast.UnaryOp.NOT_DIVIDES,
    TokKind.FLOOR: ast.UnaryOp.FLOOR,
    TokKind.CEILING: ast.UnaryOp.CEILING,
    TokKind.NEAREST: ast.UnaryOp.NEAREST,
    TokKind.ROUNDED: ast.UnaryOp.ROUNDED,
    TokKind.NEXT: ast.UnaryOp.NEXT,
    TokKind.PREV: ast.UnaryOp.PREV,
}


#: The kinds of token an expression may begin with, which is what says whether
#: `\N{UP TACK}` carries a value or stands on its own.  Listed rather than derived: what
#: may begin one is a property of the grammar, and a rule that asked the parser
#: to try and back out would read the whole of an expression to find out that
#: there was not one.
_STARTS_AN_EXPRESSION: Final[frozenset[TokKind]] = frozenset((
    TokKind.INT, TokKind.FLOAT, TokKind.STRING, TokKind.CHAR, TokKind.IDENT,
    TokKind.KW_TRUE, TokKind.KW_FALSE, TokKind.LPAREN, TokKind.ARRAY_OPEN,
    TokKind.LBRACKET, TokKind.TUPLE_OPEN, TokKind.SET_OPEN, TokKind.LIFT_OPEN,
    TokKind.BOTTOM, TokKind.TILDE, TokKind.LOGIC_NOT, TokKind.LENGTH,
    TokKind.SHAPE, TokKind.MAX, TokKind.MIN, TokKind.FLOOR, TokKind.CEILING,
    TokKind.NEAREST, TokKind.ROUNDED, TokKind.DIVIDES, TokKind.NOT_DIVIDES,
    TokKind.AMPERSAND, TokKind.LAMBDA,
    TokKind.TAKE, TokKind.NEXT, TokKind.PREV,
))


def _begins_an_expression(kind: TokKind) -> bool:
    """Whether an expression may begin with a token of this kind."""
    return kind in _STARTS_AN_EXPRESSION


def _writable(found: ast.TypeExpr, mutable: bool) -> ast.TypeExpr:
    """*found*, marked writable where `mut` was written before it.

    `mut` before a type says two things, which are one thing said of the two
    places a name can change: the name may be bound to something else, and --
    where what it stands for is a handle rather than a value -- what it reaches
    may be written.  A collection is a handle, so the second half lands on the
    type; for everything else this is where the mark stops.
    """
    return replace(found, mutable=True) \
        if mutable and isinstance(found, ast.CollectionTypeRef) else found


class _Bail(Exception):
    """Unwinds to the top-level recovery point after an unrecoverable error."""


class Parser:
    """Parses the tokens of one source file."""

    def __init__(self, tokens: Sequence[Token], path: str, diags: DiagEngine) -> None:
        self._tokens = tokens
        self._path = path
        self._diags = diags
        self._pos = 0
        #: Whether a block written on one line is being read.  What closes such
        #: a block is the end of the line, so a second one opened inside it
        #: would end where the first does and there would be no saying which.
        self._inline = False

    # -- token access ----------------------------------------------------------

    @property
    def _current(self) -> Token:
        """The token the parser is looking at."""
        return self._tokens[self._pos]

    def _peek(self, ahead: int = 1) -> Token:
        """The token *ahead* positions further on."""
        index = min(self._pos + ahead, len(self._tokens) - 1)
        return self._tokens[index]

    def _advance(self) -> Token:
        """Consume and return the current token."""
        token = self._tokens[self._pos]
        if token.kind is not TokKind.EOF:
            self._pos += 1
        return token

    def _check(self, kind: TokKind) -> bool:
        """Whether the current token has the given kind."""
        return self._current.kind is kind

    def _accept(self, kind: TokKind) -> Token | None:
        """Consume the current token if it has the given kind."""
        return self._advance() if self._check(kind) else None

    def _expect(self, kind: TokKind, ident: int = D.LANG_SYNTAX_UNEXPECTED_TOKEN) -> Token:
        """Consume a token of the given kind, or report and bail out."""
        token = self._accept(kind)
        if token is None:
            if ident == D.LANG_SYNTAX_UNEXPECTED_TOKEN:
                self._diags.emit(ident, self._current.span, expected=str(kind),
                                 found=self._current.describe())
            else:
                self._diags.emit(ident, self._current.span)
            raise _Bail()
        return token

    def _skip_newlines(self) -> None:
        """Skip over any number of blank lines."""
        while self._check(TokKind.NEWLINE):
            self._advance()

    def _recover(self) -> None:
        """Skip tokens until something that can start a new definition."""
        depth = 0
        while True:
            kind = self._current.kind
            if kind is TokKind.EOF:
                return
            if depth == 0 and kind in _RECOVERY:
                return
            if kind in (TokKind.LPAREN, TokKind.LBRACKET, TokKind.LBRACE,
                        TokKind.AT_LBRACKET):
                depth += 1
            elif kind in (TokKind.RPAREN, TokKind.RBRACKET, TokKind.RBRACE):
                depth = max(0, depth - 1)
            self._advance()

    # -- top level -------------------------------------------------------------

    def parse_unit(self) -> ast.SourceUnit:
        """Parse a whole source file."""
        items: list[ast.Definition] = []
        start = self._current.span
        while not self._check(TokKind.EOF):
            self._skip_newlines()
            if self._check(TokKind.EOF):
                break
            try:
                item = self._parse_item()
            except _Bail:
                self._recover()
                continue
            if item is not None:
                items.append(item)
        end = self._current.span
        return ast.SourceUnit(span=start.to(end), path=self._path, items=tuple(items))

    def _parse_item(self) -> ast.Definition | None:
        """Parse one top-level definition together with what precedes it."""
        doc, doc_lines = self._parse_doc_comments()
        attrs = self._parse_attributes()
        self._skip_newlines()
        if self._check(TokKind.KW_FN):
            return self._parse_function(attrs, doc, doc_lines)
        if self._check(TokKind.KW_LET):
            return self._parse_variable(attrs, doc, doc_lines)
        if self._check(TokKind.KW_TYPE):
            return self._parse_type_definition(attrs, doc, doc_lines)
        if self._check(TokKind.KW_ENUM):
            return self._parse_enum_definition(attrs, doc, doc_lines)
        if self._check(TokKind.KW_UNIT):
            return self._parse_unit_definition(doc, doc_lines)
        if self._check(TokKind.KW_BUNDLE):
            return self._parse_bundle(attrs, doc, doc_lines)
        if self._check(TokKind.KW_MACRO):
            return self._parse_macro(attrs, doc, doc_lines)
        self._diags.emit(D.LANG_FILESTRUCT_UNEXPECTED_TOPLEVEL, self._current.span,
                         construct=self._current.describe())
        raise _Bail()

    def _parse_variable(self, attrs: tuple[ast.Attribute, ...] = (),
                        doc: str | None = None,
                        doc_lines: tuple[Span, ...] = ()
                        ) -> ast.VarDef | ast.ModuleImport:
        """Parse ``let NAME ':' ['mut'] [TYPE] '=' VALUE``.

        The colon is always there; what varies is what follows it.  Written with
        neither a qualifier nor a type, and without a space, the two characters
        read as ``:=``, but they are the same two tokens either way.
        """
        start = self._expect(TokKind.KW_LET).span
        name_token = self._expect(TokKind.IDENT)
        # Names next to each other take a tuple apart, each name standing for
        # one of its members -- the same thing `auto[a, b]` does in C++ and with
        # nothing written to say so beyond the comma.
        more: list[tuple[str, Span]] = []
        while self._accept(TokKind.COMMA) is not None:
            written = self._expect(TokKind.IDENT)
            more.append((written.text, written.span))
        self._expect(TokKind.COLON, D.LANG_VARDEF_EXPECTED_COLON)
        # 'mut' qualifies the type, so it stands where the type does.  Either
        # part may be left out: the type is then the value's, and without 'mut'
        # the variable keeps whatever it was given.
        mutable = self._accept(TokKind.KW_MUT) is not None
        declared: ast.TypeExpr | None = None
        if self._begins_a_type():
            declared = _writable(self._parse_type_ref(), mutable)
        if self._accept(TokKind.EQUALS) is None:
            self._diags.emit(D.LANG_VARDEF_MISSING_INITIALIZER, name_token.span,
                             name=name_token.text)
            raise _Bail()
        if self._check(TokKind.IDENT) and self._current.text == IMPORT_NAME:
            # One of the compiler's names rather than a keyword, so it is
            # matched by what it says.  It is read here and not by the checker
            # because what it makes is not a value: a module is a file that was
            # read, and there is nothing for an expression to come to.
            return self._parse_import(start, name_token, mutable, declared, doc,
                                      doc_lines)
        value = self._parse_expression()
        return ast.VarDef(span=start.to(value.span), name=name_token.text,
                          name_span=name_token.span, type=declared, value=value,
                          mutable=mutable, doc=doc, attrs=attrs,
                          more=tuple(more))

    def _parse_import(self, start: Span, name_token: Token, mutable: bool,
                      declared: ast.TypeExpr | None,
                      doc: str | None,
                      doc_lines: tuple[Span, ...] = ()) -> ast.ModuleImport:
        """Parse the rest of ``let NAME ':=' \N{APL FUNCTIONAL SYMBOL QUAD}import(STRING)``.

        A module is not a value, so nothing about it may be qualified: there is
        nothing to change and nothing to give a type to.
        """
        self._advance()
        if mutable or declared is not None:
            self._diags.emit(D.LANG_IMPORT_QUALIFIED, name_token.span,
                             name=name_token.text)
        self._expect(TokKind.LPAREN)
        source = self._expect(TokKind.STRING, D.LANG_IMPORT_EXPECTED_NAME)
        end = self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN)
        assert source.str_value is not None
        return ast.ModuleImport(span=start.to(end.span), name=name_token.text,
                                name_span=name_token.span, source=source.str_value,
                                source_span=source.span, doc=doc, doc_lines=doc_lines)

    def _parse_doc_comments(self) -> tuple[str | None, tuple[Span, ...]]:
        """Collect the documentation comments preceding a definition.

        The text and where it is: one line of the answer is one line of the
        comment, and the span beside it covers that line without its marker and
        without the spaces after it -- so a column in the text is a column in the
        file, which is what lets a diagnostic about what a comment *says* point
        at the words it says it in.
        """
        lines: list[str] = []
        places: list[Span] = []
        while self._check(TokKind.DOC_COMMENT):
            token = self._advance()
            body = token.text.removeprefix(COMMENT_GLYPH * 2)
            begins = token.span.start + (len(token.text) - len(body.lstrip()))
            text = body.strip()
            lines.append(text)
            places.append(Span(begins, begins + len(text)))
            self._skip_newlines()
        return ("\n".join(lines) if lines else None, tuple(places))

    # -- attributes ------------------------------------------------------------

    def _parse_attributes(self) -> tuple[ast.Attribute, ...]:
        """Parse the ``@[...]`` list that precedes a definition or a statement.

        One list, not several.  Two lists attached to one thing say exactly what
        one list holding both would say, and having two ways to write a thing
        means reading two shapes and diffing two shapes for no gain.  A blank
        line between them does not make them two, since it changes nothing about
        what they attach to.

        The exception this rule will need is the attribute that attaches to
        nothing, which is an action rather than a description of what follows;
        one of those before a list is two lists in a row with nothing wrong
        about it.  There are none yet, and the check is written at the one place
        that would have to ask.
        """
        attrs: list[ast.Attribute] = []
        lists = 0
        while self._check(TokKind.AT_LBRACKET):
            start = self._current.span
            self._advance()
            lists += 1
            if lists > 1:
                self._diags.emit(D.LANG_ATTR_SEPARATE_LISTS, start,
                                 count=str(lists))
            while True:
                attrs.append(self._parse_attribute())
                if self._accept(TokKind.COMMA) is None:
                    break
            self._expect(TokKind.RBRACKET)
            self._skip_newlines()
        return tuple(attrs)

    def _parse_attribute(self) -> ast.Attribute:
        """Parse one attribute and its arguments.

        The parentheses are how an attribute carries arguments, so an attribute
        carrying none is written without them.  An empty pair says exactly what
        no pair says, and one meaning with two spellings is one spelling too
        many -- the same reason two attribute lists are refused above.
        """
        name_token = self._expect(TokKind.IDENT)
        args: list[ast.AttrArg] = []
        end = name_token.span
        if (opening := self._accept(TokKind.LPAREN)) is not None:
            if self._check(TokKind.RPAREN):
                self._diags.emit(D.LANG_ATTR_EMPTY_ARGUMENTS,
                                 opening.span.to(self._current.span),
                                 name=name_token.text)
            else:
                while True:
                    args.append(self._parse_attr_arg())
                    if self._accept(TokKind.COMMA) is None:
                        break
            end = self._expect(TokKind.RPAREN).span
        return ast.Attribute(span=name_token.span.to(end), name=name_token.text,
                             args=tuple(args), name_span=name_token.span)

    def _parse_attr_arg(self) -> ast.AttrArg:
        """Parse one attribute argument, positional or named."""
        if self._check(TokKind.IDENT) and self._peek().kind is TokKind.EQUALS:
            name_token = self._advance()
            self._advance()
            value = self._parse_attr_value()
            return ast.AttrArg(span=name_token.span.to(value.span), value=value,
                               name=name_token.text)
        value = self._parse_attr_value()
        return ast.AttrArg(span=value.span, value=value)

    def _parse_attr_value(self) -> ast.AttrValue:
        """Parse the value of an attribute argument."""
        token = self._current
        match token.kind:
            case TokKind.INT:
                self._advance()
                assert token.int_value is not None
                return ast.AttrInt(span=token.span, value=token.int_value)
            case TokKind.STRING:
                self._advance()
                assert token.str_value is not None
                return ast.AttrString(span=token.span, value=token.str_value)
            case TokKind.KW_TRUE | TokKind.KW_FALSE:
                self._advance()
                return ast.AttrBool(span=token.span, value=token.kind is TokKind.KW_TRUE)
            case TokKind.IDENT:
                self._advance()
                return ast.AttrName(span=token.span, value=token.text)
            case _:
                self._diags.emit(D.LANG_SYNTAX_UNEXPECTED_TOKEN, token.span,
                                 expected="an attribute argument", found=token.describe())
                raise _Bail()

    # -- functions -------------------------------------------------------------

    def _parse_function(self, attrs: tuple[ast.Attribute, ...],
                        doc: str | None,
                        doc_lines: tuple[Span, ...] = ()) -> ast.FuncDef:
        """Parse a function definition."""
        start = self._expect(TokKind.KW_FN).span
        # An operator standing where a name goes, which is how a program says
        # what one means for its own types: the glyph is the name.
        name_token = self._advance() if self._check(TokKind.OPNAME) \
            else self._expect(TokKind.IDENT)
        self._expect(TokKind.LPAREN)
        params = self._parse_params()
        self._expect(TokKind.RPAREN)
        # The arrow and what follows it say what the function answers with.
        # Leaving them off is how a function says it answers with nothing;
        # there is no name to write for that, which is what keeps the two from
        # being two ways of saying one thing.
        ret_type: ast.TypeExpr | None = None
        if self._accept(TokKind.ARROW) is not None:
            # `mut` before what a function answers with says the same of it:
            # what comes back is a collection whose entries may be written.
            writable = self._accept(TokKind.KW_MUT) is not None
            ret_type = _writable(self._parse_type_ref(), writable)
        # What the function requires of its types and demands of its values, in
        # the order written.  They stand between the header and the body because
        # that is what they are about: a caller reads them off the signature.
        clauses = self._parse_clauses()
        # A function with no body is the declaration of one defined somewhere
        # else.  What says there is none is that the *line ends* here: a body
        # begins with a colon or a brace, and anything else after the header is
        # neither a body nor the end of one -- which is the error it already
        # was and stays.
        if self._check(TokKind.NEWLINE) or self._check(TokKind.EOF):
            end = clauses[-1].span if clauses \
                else ret_type.span if ret_type is not None else name_token.span
            return ast.FuncDef(span=start.to(end), name=name_token.text,
                               name_span=name_token.span, params=params,
                               ret_type=ret_type, body=None, clauses=clauses,
                               attrs=attrs, doc=doc, doc_lines=doc_lines)
        body = self._parse_body()
        return ast.FuncDef(span=start.to(body.span), name=name_token.text,
                           name_span=name_token.span, params=params,
                           ret_type=ret_type, body=body, clauses=clauses,
                           attrs=attrs, doc=doc, doc_lines=doc_lines)

    #: The two words that begin a clause, and which of the two places in a call
    #: each speaks about.
    _CLAUSE_WORDS: Final[dict[TokKind, ast.ClauseKind]] = {
        TokKind.KW_PRE: ast.ClauseKind.PRE,
        TokKind.KW_POST: ast.ClauseKind.POST,
    }

    def _parse_clauses(self) -> tuple[ast.Clause, ...]:
        """Parse the `pre` and `post` clauses of a signature, however many.

        One expression per clause and as many clauses as are written: a function
        with several things to say says them separately, so that a failure points
        at the clause that failed rather than at a list holding it.  They are on
        the header's line, a newline there being what says a function has no
        body.
        """
        found: list[ast.Clause] = []
        while (kind := self._CLAUSE_WORDS.get(self._current.kind)) is not None:
            found.append(self._parse_clause(kind))
        return tuple(found)

    def _parse_clause(self, kind: ast.ClauseKind) -> ast.Clause:
        """Parse one `pre(EXPR)` or `post(EXPR)`, with the arrow if it has one."""
        start = self._advance().span
        self._expect(TokKind.LPAREN)
        expr, answers = self._parse_requirement()
        end = self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN)
        return ast.Clause(span=start.to(end.span), kind=kind, expr=expr,
                          answers=answers)

    def _parse_requirement(self) -> tuple[ast.Expr, ast.TypeExpr | None]:
        """Parse an expression and the type an arrow after it names.

        The arrow's right-hand side is a *type* and not an expression -- what a
        requirement answers is written the way every other type in the language
        is -- so `→ E' ?` says a result of `E'` and needs nothing new to say it.
        """
        expr = self._parse_expression()
        if self._accept(TokKind.ARROW) is None:
            return expr, None
        return expr, self._parse_type_ref()

    def _parse_bundle(self, attrs: tuple[ast.Attribute, ...],
                      doc: str | None,
                      doc_lines: tuple[Span, ...] = ()) -> ast.BundleDef:
        """Parse ``bundle NAME(T', ...)`` and the requirements it stands for."""
        start = self._expect(TokKind.KW_BUNDLE).span
        name_token = self._expect(TokKind.IDENT)
        self._expect(TokKind.LPAREN)
        names: list[str] = []
        places: list[Span] = []
        while True:
            written = self._expect(TokKind.IDENT)
            names.append(written.text)
            places.append(written.span)
            if self._accept(TokKind.COMMA) is None:
                break
        self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN)
        clauses, end = self._parse_bundle_body()
        return ast.BundleDef(span=start.to(end), name=name_token.text,
                             name_span=name_token.span, params=tuple(names),
                             param_spans=tuple(places), clauses=clauses,
                             attrs=attrs, doc=doc, doc_lines=doc_lines)

    def _parse_bundle_body(self) -> tuple[tuple[ast.Clause, ...], Span]:
        """Parse the requirements of a bundle, in either notation.

        Everything in a bundle is a requirement, so no line carries a keyword
        saying so; what separates the lines is what separates statements
        everywhere -- a line, a `;`, or braces -- which is why a bundle's body
        needs no notation of its own.
        """
        if self._check(TokKind.LBRACE):
            start = self._advance().span
            found = self._parse_bundle_run()
            end = self._current.span
            self._expect(TokKind.RBRACE)
            return tuple(found), start.to(end)
        start = self._expect(TokKind.COLON, D.LANG_FUNCDEF_EXPECTED_BLOCK).span
        if not self._check(TokKind.NEWLINE):
            found = self._parse_bundle_run()
            return tuple(found), start.to(self._current.span)
        self._expect(TokKind.NEWLINE)
        self._expect(TokKind.INDENT)
        found = []
        while not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
            self._skip_newlines()
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                break
            found.extend(self._parse_bundle_run())
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                continue
            self._expect(TokKind.NEWLINE)
        end = self._current.span
        self._accept(TokKind.DEDENT)
        return tuple(found), start.to(end)

    def _parse_bundle_run(self) -> list[ast.Clause]:
        """Parse the requirements a run of semicolons separates."""
        found = [self._parse_bundle_clause()]
        while self._accept(TokKind.SEMICOLON) is not None:
            if self._check_any(self._ENDS_A_RUN):
                break
            found.append(self._parse_bundle_clause())
        return found

    def _parse_bundle_clause(self) -> ast.Clause:
        """Parse one line of a bundle, which is a requirement with no keyword."""
        expr, answers = self._parse_requirement()
        end = answers.span if answers is not None else expr.span
        return ast.Clause(span=expr.span.to(end), kind=ast.ClauseKind.PRE,
                          expr=expr, answers=answers)

    # -- macros ----------------------------------------------------------------

    def _parse_macro(self, attrs: tuple[ast.Attribute, ...],
                     doc: str | None,
                     doc_lines: tuple[Span, ...] = ()) -> ast.MacroDef:
        """Parse ``macro NAME:`` and the rules it stands for.

        A name and a body of bare lines, which is a bundle's shape: both are a
        definition whose body holds neither statements nor a parameter list.  What
        a parameter list *would* say is that this is the other form of macro, a
        function over the program's text, and that waits on an interpreter -- so
        one written here is refused with the reason rather than read.
        """
        start = self._expect(TokKind.KW_MACRO).span
        name_token = self._expect(TokKind.IDENT)
        if self._check(TokKind.LPAREN):
            self._diags.emit(D.LANG_MACRO_IS_A_FUNCTION, name_token.span,
                             name=name_token.text)
            raise _Bail()
        rules, end = self._parse_macro_rules()
        return ast.MacroDef(span=start.to(end), name=name_token.text,
                            name_span=name_token.span, rules=rules,
                            attrs=attrs, doc=doc, doc_lines=doc_lines)

    def _parse_macro_rules(self) -> tuple[tuple[ast.Rule, ...], Span]:
        """Parse the rules of a macro, in either notation."""
        if self._check(TokKind.LBRACE):
            start = self._advance().span
            found = [self._parse_macro_rule()]
            while self._accept(TokKind.SEMICOLON) is not None:
                if self._check_any(self._ENDS_A_RUN):
                    break
                found.append(self._parse_macro_rule())
            end = self._current.span
            self._expect(TokKind.RBRACE)
            return tuple(found), start.to(end)
        start = self._expect(TokKind.COLON, D.LANG_FUNCDEF_EXPECTED_BLOCK).span
        if not self._check(TokKind.NEWLINE):
            return (self._parse_macro_rule(),), start.to(self._current.span)
        self._expect(TokKind.NEWLINE)
        self._expect(TokKind.INDENT)
        found = []
        while not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
            self._skip_newlines()
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                break
            found.append(self._parse_macro_rule())
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                continue
            self._expect(TokKind.NEWLINE)
        end = self._current.span
        self._accept(TokKind.DEDENT)
        return tuple(found), start.to(end)

    def _parse_macro_rule(self) -> ast.Rule:
        """Parse ``\N{TOP LEFT CORNER}pattern\N{TOP RIGHT CORNER} \N{RIGHTWARDS ARROW} \N{TOP LEFT CORNER}template\N{TOP RIGHT CORNER}``."""
        pattern = self._parse_quote()
        self._expect(TokKind.ARROW)
        template = self._parse_quote()
        return ast.Rule(span=pattern.span.to(template.span), pattern=pattern,
                        template=template)

    def _parse_quote(self) -> ast.Quote:
        """Parse what stands between the lifting marks where a macro reads them.

        Expressions separated by commas, or a run of statements where the contents
        are indented under the opening mark.  The marks do not hide the ends of
        lines the way the other brackets do, so the indented form is the layout
        every block in the language already has, with the mark in place of a colon.
        """
        start = self._expect(TokKind.LIFT_OPEN).span
        if self._check(TokKind.NEWLINE):
            self._expect(TokKind.NEWLINE)
            self._expect(TokKind.INDENT)
            stmts: list[ast.Stmt] = []
            while not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
                self._skip_newlines()
                if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                    break
                stmts.extend(self._parse_separated())
                if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                    continue
                if self._accept(TokKind.NEWLINE) is None \
                        and not _ends_with_a_block(stmts[-1]):
                    self._expect(TokKind.NEWLINE)
            inner = self._current.span
            self._accept(TokKind.DEDENT)
            self._skip_newlines()
            end = self._expect(TokKind.LIFT_CLOSE,
                               D.LANG_SYNTAX_EXPECTED_CLOSING_LIFT)
            return ast.Quote(span=start.to(end.span),
                             body=ast.Block(span=start.to(inner),
                                            style=ast.BlockStyle.LAYOUT,
                                            stmts=tuple(stmts)))
        pieces: list[ast.Expr] = []
        if not self._check(TokKind.LIFT_CLOSE):
            # Nothing between the marks is a macro of no arguments, which is a thing
            # to write: what it stands for does not depend on anything the caller
            # said, and the marks are still what says it is a macro.
            pieces.append(self._parse_expression())
            while self._accept(TokKind.COMMA) is not None:
                pieces.append(self._parse_expression())
        end = self._expect(TokKind.LIFT_CLOSE, D.LANG_SYNTAX_EXPECTED_CLOSING_LIFT)
        return ast.Quote(span=start.to(end.span), pieces=tuple(pieces))

    def _parse_type_ref(self) -> ast.TypeExpr:
        """Parse a type, which may be a collection written the way a value is.

        An array is written after what it holds -- `i32\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` -- and more than one
        may follow, which is an array of arrays.  The element type comes first
        because that is the order it is read in: four of these, not an array of
        four whose elements are these.
        """
        if self._check(TokKind.AMPERSAND):
            # A reference to a place someone else holds.  `mut` here says what
            # may be done to that place, which is part of the type because both
            # the one who wrote the reference and the one who reads it reach it.
            start = self._advance().span
            mutable = self._accept(TokKind.KW_MUT) is not None
            # How long what it names lives, where the type can say: as long as
            # the program, which is what a variable at the top level has and
            # nothing else does.
            lasting = self._reading(LASTING_WORD) and self._begins_a_type(1)
            if lasting:
                self._advance()
            # Or as long as whatever else in this signature carries the same
            # name, which is the other answer to that one question and stands
            # in the same place.
            lifetime: str | None = None
            if not lasting and self._accept(TokKind.LIFETIME) is not None:
                named = self._expect(TokKind.IDENT,
                                     D.LANG_SYNTAX_EXPECTED_LIFETIME_NAME)
                lifetime = named.text if named is not None else None
            pointee = self._parse_type_ref()
            return ast.RefTypeRef(span=start.to(pointee.span), pointee=pointee,
                                  mutable=mutable, lasting=lasting,
                                  lifetime=lifetime)
        if self._check(TokKind.KW_FN) or self._check(TokKind.AT_LBRACKET):
            # What a caller reads off a function type is written before it, the
            # way it is written before the function itself.
            return self._parse_function_type()
        if self._check(TokKind.SET_OPEN):
            found: ast.TypeExpr = self._parse_collection_type()
        elif self._check(TokKind.TUPLE_OPEN):
            found = self._parse_tuple_type()
        elif self._check(TokKind.LBRACKET):
            found = self._parse_list_type()
        else:
            found = self._parse_named_type()
        if self._check(TokKind.UNIT):
            # A unit belongs to the type it is written after and not to an
            # array of it, so it is read before the suffixes: `u8 \N{CURRENCY SIGN}meter\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` is
            # four lengths and not a length made of four numbers.
            written = self._parse_unit_ref()
            found = ast.UnitTypeRef(span=found.span.to(written.span), base=found,
                                    unit=written)
        while self._check(TokKind.ARRAY_OPEN):
            self._advance()
            shape = [self._parse_dimension()]
            while self._accept(TokKind.COMMA) is not None:
                shape.append(self._parse_dimension())
            end = self._expect(TokKind.ARRAY_CLOSE,
                               D.LANG_SYNTAX_EXPECTED_CLOSING_ARRAY).span
            found = ast.ArrayTypeRef(span=found.span.to(end), element=found,
                                     shape=tuple(shape))
        return found

    def _parse_unit_ref(self) -> ast.UnitRef:
        """Parse `\N{CURRENCY SIGN}` and the unit after it.

        Read left to right: the first name stands above the line, `\N{MULTIPLICATION SIGN}` puts the
        next one above it too and `\N{DIVISION SIGN}` puts it below, and a number written raised
        after one is what it is raised to.  That is how a unit is written in
        physics, and it means `\N{CURRENCY SIGN}meter\N{DIVISION SIGN}second\N{SUPERSCRIPT TWO}` reads as it looks.

        A name between quotation marks is a unit whose name is not an
        identifier, which is what lets a program count things the language has
        never heard of.
        """
        start = self._expect(TokKind.UNIT).span
        factors = [self._parse_unit_factor(1)]
        end = factors[-1].span
        while True:
            if self._accept(TokKind.TIMES) is not None:
                sign = 1
            elif self._accept(TokKind.DIVIDE) is not None:
                sign = -1
            else:
                break
            factors.append(self._parse_unit_factor(sign))
            end = factors[-1].span
        return ast.UnitRef(span=start.to(end), factors=tuple(factors))

    def _parse_unit_factor(self, sign: int) -> ast.UnitFactor:
        """Parse one base unit of a written unit, with whatever it is raised to."""
        if self._check(TokKind.STRING):
            token = self._advance()
            name, quoted = token.text, True
        else:
            token = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_UNIT)
            name, quoted = token.text, False
        exponent, end = 1, token.span
        raised = self._accept(TokKind.EXPONENT)
        if raised is not None:
            assert raised.int_value is not None
            exponent, end = raised.int_value, raised.span
        return ast.UnitFactor(span=token.span.to(end), name=name, quoted=quoted,
                              exponent=sign * exponent)

    def _parse_unit_definition(self, doc: str | None = None,
                               doc_lines: tuple[Span, ...] = ()) -> ast.UnitDef:
        """Parse `unit NAME`, `unit NAME = VALUE` or `unit \N{CURRENCY SIGN}FROM \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}TO`.

        A unit the language does not provide is introduced before it is used,
        which is what keeps a mistyped unit from quietly becoming a unit of its
        own.  Where the definition stands is how far it reaches: at the top
        level, the whole file; inside a body, that body.
        """
        start = self._expect(TokKind.KW_UNIT).span
        if self._check(TokKind.UNIT):
            what = self._parse_unit_ref()
            self._expect(TokKind.ARROW, D.LANG_SYNTAX_EXPECTED_UNIT_ARROW)
            where = self._parse_unit_ref()
            return ast.UnitDef(span=start.to(where.span), name="",
                               name_span=what.span, stands=(what, where),
                               doc=doc, doc_lines=doc_lines)
        if self._check(TokKind.STRING):
            token = self._advance()
            quoted = True
        else:
            token = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_UNIT)
            quoted = False
        if self._accept(TokKind.EQUALS) is None:
            return ast.UnitDef(span=start.to(token.span), name=token.text,
                               name_span=token.span, quoted=quoted, doc=doc, doc_lines=doc_lines)
        over, under, factors = self._parse_unit_measure()
        end = factors[-1].span if factors else token.span
        return ast.UnitDef(span=start.to(end), name=token.text,
                           name_span=token.span, quoted=quoted,
                           measured=ast.UnitRef(span=token.span.to(end),
                                                factors=tuple(factors)),
                           scale=(over, under), doc=doc, doc_lines=doc_lines)

    def _parse_unit_measure(self) -> tuple[int, int, list[ast.UnitFactor]]:
        """Parse what one unit is in terms of others: numbers and names mixed.

        `unit mph = 1609344 \N{DIVISION SIGN} 3600000 \N{MULTIPLICATION SIGN} meter \N{DIVISION SIGN} second` is one product read
        left to right, in which the numbers say how many and the names say of
        what.  Keeping them in one sequence is what lets it be written the way
        the conversion is written down anywhere else.
        """
        over, under = 1, 1
        factors: list[ast.UnitFactor] = []
        sign = 1
        while True:
            if self._check(TokKind.INT):
                token = self._advance()
                assert token.int_value is not None
                if sign > 0:
                    over *= token.int_value
                else:
                    under *= token.int_value
            else:
                factors.append(self._parse_unit_factor(sign))
            if self._accept(TokKind.TIMES) is not None:
                sign = 1
            elif self._accept(TokKind.DIVIDE) is not None:
                sign = -1
            else:
                return (over, under, factors)

    def _parse_function_type(self) -> ast.FuncTypeRef:
        """Parse `fn(TYPE, TYPE) \N{RIGHTWARDS ARROW} TYPE`: the type of a function as a value.

        The keyword a function is defined with, and then what it takes and what
        it answers.  The parameter names are not there because a type is not a
        definition: what a caller has to know is the types, and what the names
        are is the body's business.
        """
        attrs = (self._parse_attributes()
                 if self._check(TokKind.AT_LBRACKET) else ())
        start = self._expect(TokKind.KW_FN).span
        self._expect(TokKind.LPAREN)
        params: list[ast.TypeExpr] = []
        if not self._check(TokKind.RPAREN):
            params.append(self._parse_type_ref())
            while self._accept(TokKind.COMMA) is not None:
                params.append(self._parse_type_ref())
        end = self._expect(TokKind.RPAREN).span
        ret: ast.TypeExpr | None = None
        if self._accept(TokKind.ARROW) is not None:
            ret = self._parse_type_ref()
            end = ret.span
        return ast.FuncTypeRef(span=start.to(end), params=tuple(params),
                               ret=ret, attrs=attrs)

    def _parse_lambda(self, attrs: tuple[ast.Attribute, ...] = ()
                      ) -> ast.Lambda:
        """Parse `\N{GREEK SMALL LETTER LAMDA} PARM: TYPE, \N{HORIZONTAL ELLIPSIS} [CAPTURES] \N{RIGHTWARDS ARROW} TYPE` and the body after it.

        The parameter list has no parentheses round it, there being nothing
        before it for them to separate it from -- and it needs none: what ends
        it is the capture list, the arrow or the body, and none of the three can
        be part of a parameter.
        """
        start = self._expect(TokKind.LAMBDA).span
        params: list[ast.Param] = []
        while self._check(TokKind.IDENT):
            params.append(self._parse_lambda_param())
            if self._accept(TokKind.COMMA) is None:
                break
        captures: tuple[ast.Capture, ...] = ()
        brings_in: ast.CaptureAll | None = None
        if self._check(TokKind.LBRACKET):
            captures, brings_in = self._parse_captures()
        ret: ast.TypeExpr | None = None
        if self._accept(TokKind.ARROW) is not None:
            ret = self._parse_type_ref()
        body = self._parse_body()
        return ast.Lambda(span=start.to(body.span), params=tuple(params),
                          body=body, captures=captures, ret_type=ret,
                          brings_in=brings_in, attrs=attrs)

    def _parse_lambda_param(self) -> ast.Param:
        """Parse one parameter of a lambda, which is one of a function without
        a default: what a caller gives an indirect call is what its type says,
        and nothing at such a call knows what a definition wrote."""
        name = self._expect(TokKind.IDENT)
        self._expect(TokKind.COLON)
        mutable = self._accept(TokKind.KW_MUT) is not None
        written = _writable(self._parse_type_ref(), mutable)
        return ast.Param(span=name.span.to(written.span), name=name.text,
                         type=written, mutable=mutable)

    def _parse_captures(self) -> tuple[tuple[ast.Capture, ...],
                                       ast.CaptureAll | None]:
        """Parse `[a, &b]`, `[=]` or `[&]`: what a lambda brings in.

        `&` says the variable itself rather than what it held, which is C++'s
        mark for the distinction and the same `&` a reference type is written
        with -- what it says here is what it says there.

        `[=]` and `[&]` say it of every name the body reaches from outside
        itself rather than of named ones, which is what those two say in C++.
        They are told from a list of names by what follows the mark: a name
        follows `&` in a list and the closing bracket follows it here.
        """
        start = self._expect(TokKind.LBRACKET).span
        every = self._every_name()
        if every is not None:
            self._expect(TokKind.RBRACKET, D.LANG_SYNTAX_EXPECTED_CLOSING_LIST)
            return ((), every)
        found: list[ast.Capture] = []
        while not self._check(TokKind.RBRACKET):
            mark = self._accept(TokKind.AMPERSAND)
            name = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_CAPTURE)
            found.append(ast.Capture(
                span=(mark.span if mark is not None else name.span).to(name.span),
                name=name.text, by_reference=mark is not None))
            if self._accept(TokKind.COMMA) is None:
                break
        end = self._expect(TokKind.RBRACKET,
                           D.LANG_SYNTAX_EXPECTED_CLOSING_LIST).span
        if not found:
            # An empty list is a second spelling of no list at all, and the
            # language admits one spelling of one thing.
            self._diags.emit(D.LANG_SYNTAX_EMPTY_CAPTURE, start.to(end))
        return (tuple(found), None)

    def _every_name(self) -> ast.CaptureAll | None:
        """Whether the list is `[=]` or `[&]`, said without reading a name.

        `&` begins a capture of a named variable as well, so which it is, is
        what follows it: a name in a list, and the closing bracket here.
        """
        if self._check(TokKind.EQUALS):
            self._advance()
            return ast.CaptureAll.BY_VALUE
        if self._check(TokKind.AMPERSAND) and self._peek(1).kind is TokKind.RBRACKET:
            self._advance()
            return ast.CaptureAll.BY_REFERENCE
        return None

    def _parse_dimension(self) -> ast.Expr | None:
        """Parse how many there are along one dimension, or nothing for a
        dimension the type does not say the size of."""
        if self._check(TokKind.ARRAY_CLOSE) or self._check(TokKind.COMMA):
            return None
        return self._parse_expression()

    def _parse_tuple_type(self) -> ast.TupleTypeRef:
        """Parse ``\N{LEFT ANGLE BRACKET}TYPE, TYPE\N{RIGHT ANGLE BRACKET}``."""
        start = self._expect(TokKind.TUPLE_OPEN).span
        members = [self._parse_type_ref()]
        while self._accept(TokKind.COMMA) is not None:
            members.append(self._parse_type_ref())
        end = self._expect(TokKind.TUPLE_CLOSE,
                           D.LANG_SYNTAX_EXPECTED_CLOSING_TUPLE).span
        return ast.TupleTypeRef(span=start.to(end), members=tuple(members))

    def _parse_collection_type(self) -> ast.CollectionTypeRef:
        """Parse ``\N{LEFT DOUBLE PARENTHESIS}TYPE\N{RIGHT DOUBLE PARENTHESIS}`` or ``\N{LEFT DOUBLE PARENTHESIS}TYPE ':' TYPE\N{RIGHT DOUBLE PARENTHESIS}``."""
        start = self._expect(TokKind.SET_OPEN).span
        element = self._parse_type_ref()
        value: ast.TypeExpr | None = None
        if self._accept(TokKind.COLON) is not None:
            value = self._parse_type_ref()
        end = self._expect(TokKind.SET_CLOSE, D.LANG_SYNTAX_EXPECTED_CLOSING_SET).span
        return ast.CollectionTypeRef(span=start.to(end), element=element, value=value)

    def _reading(self, word: str) -> bool:
        """Whether the token here is the identifier *word*.

        Two words are read where nothing else could stand rather than taken
        outright, so that a program may still use them as names.  What makes
        that safe is the place: nothing but a type may follow `&`, and nothing
        but a body may follow a function's return type.
        """
        return self._check(TokKind.IDENT) and self._current.text == word

    def _begins_a_type(self, ahead: int = 0) -> bool:
        """Whether a type is written here rather than left out.

        Six things begin one: a name, a collection, a tuple, a list, the mark of
        a reference, and what a function type says about itself, which stands
        before the `fn`.  It is asked wherever a type may be written and may
        equally be absent, which is a variable and a binding in a loop.
        """
        kind = self._peek(ahead).kind if ahead else self._current.kind
        return kind in (TokKind.IDENT, TokKind.SET_OPEN, TokKind.TUPLE_OPEN,
                        TokKind.LBRACKET, TokKind.AMPERSAND, TokKind.KW_FN,
                        TokKind.AT_LBRACKET)

    def _parse_list_type(self) -> ast.ListTypeRef:
        """Parse ``'[' TYPE ']'``, which is written the way a value of one is."""
        start = self._expect(TokKind.LBRACKET).span
        element = self._parse_type_ref()
        end = self._expect(TokKind.RBRACKET,
                           D.LANG_SYNTAX_EXPECTED_CLOSING_LIST).span
        return ast.ListTypeRef(span=start.to(end), element=element)

    def _parse_named_type(self) -> ast.TypeRef:
        """Parse a type: a name, and whatever says what else it may be.

        A name may be reached through a module, as a function or a variable is:
        `m.Point` is the `Point` that module `m` exports.

        `TYPE?` is a result: a value of that type, or the fact that there is
        none.  `TYPE?ERROR` is one whose error carries a value of its own, and
        the name after the mark is what says so -- the mark with nothing after
        it is what says the error is only the fact of it.
        """
        name_token = self._expect(TokKind.IDENT)
        first = name_token
        module: str | None = None
        if self._check(TokKind.DOT):
            self._advance()
            module = name_token.text
            name_token = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_MEMBER)
        # The unit comes before the mark because it belongs to the answer: a
        # result of a length is a result whose answer is a length, and there is
        # nothing about a result for a unit to say.
        unit = self._parse_unit_ref() if self._check(TokKind.UNIT) else None
        last_name = unit.span if unit is not None else name_token.span
        mark = self._accept(TokKind.QUESTION)
        if mark is None:
            return ast.TypeRef(span=first.span.to(last_name),
                               name=name_token.text, module=module, unit=unit)
        error: str | None = None
        last = mark
        if self._check(TokKind.IDENT):
            last = self._advance()
            error = last.text
        return ast.TypeRef(span=first.span.to(last.span), name=name_token.text,
                           module=module, result=True, error=error, unit=unit)

    #: Which kind of definition each separator makes.
    _TYPE_SEPARATORS: Final[dict[TokKind, ast.TypeKind]] = {
        TokKind.SEMICOLON: ast.TypeKind.PRODUCT,
        TokKind.PIPE: ast.TypeKind.SUM,
    }

    def _parse_type_definition(self, attrs: tuple[ast.Attribute, ...] = (),
                               doc: str | None = None,
                               doc_lines: tuple[Span, ...] = ()) -> ast.TypeDef:
        """Parse ``type NAME '=' NAME ':' TYPE (SEP NAME ':' TYPE)*``.

        The separator is what says which kind of type it is: `;` for a product,
        which holds all of its parts, and `|` for a sum, which holds one of
        them.  One pair with no separator to go by is a product -- a record of
        one field is a useful thing and a choice between one alternative is not.

        The sequence may be written over several lines in either of the two ways
        the language already breaks a line: inside braces, where the lexer gives
        out no ends of lines at all, or indented under the definition, where the
        separator ends the line and this skips what follows it.  Neither changes
        what separates the pairs.
        """
        start = self._expect(TokKind.KW_TYPE).span
        name_token = self._expect(TokKind.IDENT)
        self._expect(TokKind.EQUALS, D.LANG_TYPEDEF_EXPECTED_EQUALS)
        braced = self._accept(TokKind.LBRACE) is not None
        indented = False
        if not braced and self._check(TokKind.NEWLINE) \
                and self._peek().kind is TokKind.INDENT:
            # An end of line with nothing indented under it is a definition
            # that said nothing after the `=`, and is reported as that rather
            # than as a missing indentation somewhere further down.
            self._advance()
            self._advance()
            indented = True
        fields: list[ast.Field] = []
        kind: ast.TypeKind | None = None
        separator: Token | None = None
        while True:
            if indented:
                self._skip_newlines()
            if not fields and self._ends_the_parts(braced, indented):
                # Nothing at all between the delimiters, which is a definition
                # that said nothing about what the type is.
                self._diags.emit(D.LANG_TYPEDEF_NO_PARTS, name_token.span)
                raise _Bail()
            fields.append(self._parse_field())
            # The separator ends the line it is on, where the definition is
            # written over several.  Looking for it before skipping any end of
            # line is what keeps "this pair was the last" decidable where it is
            # written rather than one line further on.
            found = self._TYPE_SEPARATORS.get(self._current.kind)
            if found is None:
                break
            here = self._advance()
            if kind is None:
                kind, separator = found, here
            elif found is not kind:
                assert separator is not None
                self._diags.emit(D.LANG_TYPEDEF_MIXED_SEPARATORS, here.span,
                                 found=here.describe(),
                                 first=separator.describe()).note(
                    D.LANG_TYPEDEF_FIRST_SEPARATOR, separator.span)
                raise _Bail()
        end = self._current.span
        if braced:
            end = self._expect(TokKind.RBRACE).span
        elif indented:
            self._skip_newlines()
            self._accept(TokKind.DEDENT)
        return ast.TypeDef(span=start.to(end), name=name_token.text,
                           name_span=name_token.span,
                           kind=kind if kind is not None else ast.TypeKind.PRODUCT,
                           fields=tuple(fields), attrs=attrs, doc=doc, doc_lines=doc_lines)

    def _ends_the_parts(self, braced: bool, indented: bool) -> bool:
        """Whether what comes next closes a type definition rather than opening
        a part of it."""
        if braced:
            return self._check(TokKind.RBRACE)
        if indented:
            return self._check(TokKind.DEDENT) or self._check(TokKind.EOF)
        return self._check(TokKind.NEWLINE) or self._check(TokKind.EOF)

    def _parse_enum_definition(self, attrs: tuple[ast.Attribute, ...] = (),
                               doc: str | None = None,
                               doc_lines: tuple[Span, ...] = ()) -> ast.EnumDef:
        """Parse ``enum NAME [':' TYPE]`` and the names of its values.

        The type says how much room a value takes and nothing else.  It is
        introduced by a colon, and so is the indented form of the list, which is
        why a definition that names a type and indents its values carries two of
        them: one belongs to the type and one opens the block, as a function
        that answers with something and has an indented body carries both a
        return type and a colon.
        """
        start = self._expect(TokKind.KW_ENUM).span
        name_token = self._expect(TokKind.IDENT)
        holder: ast.TypeExpr | None = None
        indented = False
        if self._accept(TokKind.COLON) is not None:
            if self._check(TokKind.IDENT):
                holder = self._parse_type_ref()
            else:
                indented = True
        braced = not indented and self._accept(TokKind.LBRACE) is not None
        if not braced and not indented:
            self._expect(TokKind.COLON, D.LANG_ENUMDEF_EXPECTED_VALUES)
            indented = True
        if indented:
            self._expect(TokKind.NEWLINE)
            self._expect(TokKind.INDENT)
        members: list[ast.EnumMember] = []
        while True:
            if indented:
                self._skip_newlines()
            if not members and self._ends_the_parts(braced, indented):
                self._diags.emit(D.LANG_ENUMDEF_NO_VALUES, name_token.span)
                raise _Bail()
            members.append(self._parse_enum_member())
            if self._accept(TokKind.SEMICOLON) is None:
                break
            if indented:
                self._skip_newlines()
        end = self._current.span
        if braced:
            end = self._expect(TokKind.RBRACE).span
        else:
            self._skip_newlines()
            self._accept(TokKind.DEDENT)
        return ast.EnumDef(span=start.to(end), name=name_token.text,
                           name_span=name_token.span, members=tuple(members),
                           holder=holder, attrs=attrs, doc=doc, doc_lines=doc_lines)

    def _parse_enum_member(self) -> ast.EnumMember:
        """Parse ``NAME`` or ``NAME '=' (NUMBER | NAME)``.

        A number says which value it is; a name says "the one that name already
        stands for", which is how two names are given one value on purpose.
        Nothing written means the compiler chooses.
        """
        written = self._expect(TokKind.IDENT)
        if self._accept(TokKind.EQUALS) is None:
            return ast.EnumMember(span=written.span, name=written.text,
                                  name_span=written.span)
        token = self._current
        if token.kind is TokKind.INT:
            self._advance()
            assert token.int_value is not None
            value: ast.IntLit | ast.NameRef = ast.IntLit(
                span=token.span, value=token.int_value, type_name=token.int_type)
        elif token.kind is TokKind.IDENT:
            self._advance()
            value = ast.NameRef(span=token.span, name=token.text)
        else:
            self._diags.emit(D.LANG_ENUMDEF_EXPECTED_VALUE, token.span,
                             found=token.describe())
            raise _Bail()
        return ast.EnumMember(span=written.span.to(token.span), name=written.text,
                              name_span=written.span, value=value)

    def _parse_field(self) -> ast.Field:
        """Parse one ``NAME ':' TYPE`` of a type definition."""
        name_token = self._expect(TokKind.IDENT)
        self._expect(TokKind.COLON, D.LANG_TYPEDEF_EXPECTED_COLON)
        # `mut` stands before the type here as it does in a definition, and a
        # field is the one place it says nothing about a name: a field is never
        # bound to something else, so what it says here is what it says of a
        # collection -- that entries may be put in it.
        mutable = self._accept(TokKind.KW_MUT) is not None
        written = _writable(self._parse_type_ref(), mutable)
        return ast.Field(span=name_token.span.to(written.span),
                         name=name_token.text, name_span=name_token.span,
                         type=written)

    def _parse_params(self) -> tuple[ast.Param, ...]:
        """Parse a parameter list, which may be empty."""
        params: list[ast.Param] = []
        seen: dict[str, Span] = {}
        if self._check(TokKind.RPAREN):
            return ()
        while True:
            name_token = self._expect(TokKind.IDENT)
            self._expect(TokKind.COLON)
            # `mut` stands where it stands in a definition, before the type,
            # and says the same thing there: the name may be bound to something
            # else later on.
            mutable = self._accept(TokKind.KW_MUT) is not None
            written = _writable(self._parse_type_ref(), mutable)
            # `\N{LEFTWARDS ARROW} VALUE` says what a caller that says nothing about this
            # parameter gets.  The glyph is the one an assignment is written
            # with, which is what this is: the name is bound to that value.
            default = None
            end = written.span
            if self._accept(TokKind.ASSIGN) is not None:
                default = self._parse_expression()
                end = default.span
            if name_token.text in seen:
                self._diags.emit(D.LANG_FUNCDEF_DUPLICATE_PARAMETER, name_token.span,
                                 name=name_token.text)
            else:
                seen[name_token.text] = name_token.span
            params.append(ast.Param(
                span=name_token.span.to(end), name=name_token.text,
                type=written, mutable=mutable, default=default))
            if self._accept(TokKind.COMMA) is None:
                break
        return tuple(params)

    def _parse_body(self) -> ast.Block:
        """Parse a function body in either notation."""
        if self._check(TokKind.COLON):
            return self._parse_layout_block()
        if self._check(TokKind.LBRACE):
            return self._parse_explicit_block()
        self._diags.emit(D.LANG_FUNCDEF_EXPECTED_BLOCK, self._current.span)
        raise _Bail()

    #: What ends a run of statements, whichever notation the block is in.  A
    #: semicolon separates two statements and never ends the last, so what
    #: follows one is always a statement -- and where one of these follows it
    #: instead, the statement between them is the empty one.
    _ENDS_A_RUN: Final[tuple[TokKind, ...]] = (
        TokKind.NEWLINE, TokKind.DEDENT, TokKind.RBRACE, TokKind.EOF)

    def _parse_separated(self) -> list[ast.Stmt]:
        """Parse the statements a run of semicolons separates.

        A semicolon is a *separator* and not a terminator: `a;` is two
        statements, the second of which is empty.  That is what makes a body
        ending in a semicolon produce no value, which is a thing worth being
        able to write and worth not writing by accident.
        """
        found: list[ast.Stmt] = [self._parse_statement()]
        while self._check(TokKind.SEMICOLON):
            semicolon = self._advance().span
            if self._check(TokKind.SEMICOLON) or self._check_any(self._ENDS_A_RUN):
                found.append(ast.EmptyStmt(span=semicolon))
                continue
            found.append(self._parse_statement())
        return found

    def _check_any(self, kinds: Sequence[TokKind]) -> bool:
        """Whether the next token is one of *kinds*."""
        return any(self._check(kind) for kind in kinds)

    def _parse_layout_block(self) -> ast.Block:
        """Parse ``: NEWLINE INDENT statements DEDENT``.

        Statements are separated by the ends of lines and, within a line, by
        semicolons.  Both notations take both separators, so that what a
        statement is does not depend on which notation it is written in.
        """
        start = self._expect(TokKind.COLON).span
        if not self._check(TokKind.NEWLINE):
            return self._parse_inline_block(start)
        self._expect(TokKind.NEWLINE)
        self._expect(TokKind.INDENT)
        stmts: list[ast.Stmt] = []
        while not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
            self._skip_newlines()
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                break
            stmts.extend(self._parse_separated())
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                continue
            if self._accept(TokKind.NEWLINE) is None \
                    and not _ends_with_a_block(stmts[-1]):
                # A statement that ends with an indented block has already taken
                # the end of its own last line, so there is none left to ask for.
                self._expect(TokKind.NEWLINE)
        end = self._current.span
        self._accept(TokKind.DEDENT)
        return ast.Block(span=start.to(end), style=ast.BlockStyle.LAYOUT, stmts=tuple(stmts))

    def _parse_inline_block(self, start: Span) -> ast.Block:
        """Parse ``: statement`` written on one line.

        It is the layout notation with the indent left out: what opens the block
        is the colon and what closes it is the end of the line -- or the `else`
        or `elif` of the same chain, neither of which can continue a statement.
        Nothing else could close it, which is why a block written this way may
        not open another (3044): the inner one would end where the outer one
        does and there would be no saying which `else` belonged to which `if`.
        """
        if self._inline:
            self._diags.emit(D.LANG_SYNTAX_INLINE_INSIDE_INLINE,
                             self._current.span)
            raise _Bail()
        outer, self._inline = self._inline, True
        try:
            stmts = self._parse_separated()
        finally:
            self._inline = outer
        return ast.Block(span=start.to(self._current.span),
                         style=ast.BlockStyle.INLINE, stmts=tuple(stmts))

    def _parse_explicit_block(self) -> ast.Block:
        """Parse ``{ statement ; ... }``.

        There are no ends of lines inside braces -- the lexer gives none out
        there -- so a semicolon is the only separator, and two statements with
        nothing between them is the layout notation written inside braces.
        """
        start = self._expect(TokKind.LBRACE).span
        stmts: list[ast.Stmt] = []
        if not self._check(TokKind.RBRACE) and not self._check(TokKind.EOF):
            stmts.extend(self._parse_separated())
            if not self._check(TokKind.RBRACE) and not self._check(TokKind.EOF):
                self._diags.emit(D.LANG_SYNTAX_MIXED_BLOCK_STYLE, self._current.span)
                raise _Bail()
        end = self._current.span
        self._accept(TokKind.RBRACE)
        return ast.Block(span=start.to(end), style=ast.BlockStyle.EXPLICIT, stmts=tuple(stmts))

    def _parse_statement(self) -> ast.Stmt:
        """Parse one statement, with whatever attributes precede it.

        In the layout notation an attribute stands on its own line, indented
        with the statement it belongs to, which is what says which statement
        that is.
        """
        attrs = self._parse_attributes() if self._check(TokKind.AT_LBRACKET) else ()
        stmt = self._parse_bare_statement()
        return replace(stmt, attrs=attrs) if attrs else stmt

    def _parse_bare_statement(self) -> ast.Stmt:
        """Parse one statement."""
        if self._check(TokKind.KW_UNIT):
            # A unit introduced inside a body reaches as far as the body does,
            # which is what every other name written in one does.
            return self._parse_unit_definition()
        if self._check(TokKind.KW_LET):
            found = self._parse_variable()
            if isinstance(found, ast.ModuleImport):
                # A module is read while the program is compiled and what it
                # holds is the program's, not one function's, so there is
                # nowhere inside a body for one to belong to.
                self._diags.emit(D.LANG_IMPORT_INSIDE_FUNCTION, found.span,
                                 name=found.name)
                raise _Bail()
            return found
        if self._check(TokKind.KW_MATCH):
            matched = self._parse_match()
            return ast.ExprStmt(span=matched.span, value=matched)
        if self._check(TokKind.KW_IF) or self._begins_an_arm(TokKind.KW_IF):
            asked = self._parse_if()
            return ast.ExprStmt(span=asked.span, value=asked)
        if self._check(TokKind.KW_COMPTIME) \
                and self._peek().kind is TokKind.KW_FOREACH:
            start = self._advance().span
            self._advance()
            walked = self._parse_iteration(start, "foreach", self._parse_label(),
                                           comptime=True)
            return ast.ExprStmt(span=walked.span, value=walked)
        if self._check(TokKind.KW_WHILE) or self._check(TokKind.KW_UNLESS):
            looped = self._parse_while()
            return ast.ExprStmt(span=looped.span, value=looped)
        if self._check(TokKind.KW_FOREACH):
            start = self._advance().span
            walked = self._parse_iteration(start, "foreach", self._parse_label())
            return ast.ExprStmt(span=walked.span, value=walked)
        if self._check(TokKind.KW_BREAK) or self._check(TokKind.KW_CONTINUE):
            return self._parse_jump()
        if self._check(TokKind.KW_RETURN):
            start = self._advance().span
            if self._check(TokKind.NEWLINE) or self._check(TokKind.SEMICOLON) \
                    or self._check(TokKind.RBRACE) or self._check(TokKind.DEDENT):
                return ast.ReturnStmt(span=start, value=None, explicit=True)
            value = self._parse_expression()
            return ast.ReturnStmt(span=start.to(value.span), value=value, explicit=True)
        value = self._parse_expression()
        if self._check(TokKind.COMMA) and isinstance(value, ast.NameRef):
            return self._parse_unpacking(value)
        if self._check(TokKind.ASSIGN):
            return self._parse_assignment(value)
        return ast.ExprStmt(span=value.span, value=value)

    def _parse_unpacking(self, first: ast.NameRef) -> ast.Stmt:
        """Parse ``NAME, NAME \N{LEFTWARDS ARROW} VALUE``, which takes a tuple apart."""
        more: list[tuple[str, Span]] = []
        while self._accept(TokKind.COMMA) is not None:
            written = self._expect(TokKind.IDENT)
            more.append((written.text, written.span))
        self._expect(TokKind.ASSIGN)
        value = self._parse_expression()
        return ast.AssignStmt(span=first.span.to(value.span), name=first.name,
                              name_span=first.span, value=value,
                              more=tuple(more))

    def _parse_while(self) -> ast.While:
        """Parse ``while COND BODY``, and ``unless COND BODY``.

        The condition stands on its own, with no parentheses around it, for the
        reason `if`'s does: what ends it is the body, which begins with a colon
        or a brace, and neither can be part of an expression.

        `unless` is the same loop with the condition read the other way round --
        the body runs *until* it holds -- and is one word rather than a `¬`
        because the conditions it is for are already the negative of what a
        reader means: a cursor asked whether a walk is over is the one there is.
        """
        until = self._check(TokKind.KW_UNLESS)
        start = self._advance().span
        label = self._parse_label()
        if self._binds_a_value():
            # `unless x := ...` would be a walk that stops where it began, so
            # there is nothing for the word to mean there.
            if until:
                self._diags.emit(D.LANG_UNLESS_BINDS_A_VALUE, start)
            return self._parse_iteration(start, "while", label)
        condition = self._parse_expression()
        body = self._parse_body()
        otherwise = self._parse_otherwise()
        return ast.While(span=start.to((otherwise or body).span),
                         condition=condition, body=body, label=label,
                         alternative=otherwise, until=until)

    def _parse_otherwise(self) -> ast.Block | None:
        """Parse a loop's `else` arm, which is where the loop ran out.

        It is found the way an `if`'s is: the keyword after the body, the
        layout notation having ended the body before it.  What it is for is
        the way through the loop that no `break` took, which is the way that
        would otherwise have no value to give.
        """
        if not self._check(TokKind.KW_ELSE):
            return None
        self._advance()
        return self._parse_body()

    def _parse_label(self) -> ast.Label | None:
        """Parse `\N{SECTION SIGN}name` where a loop may be given one.

        It stands between the keyword and what the loop runs on, which is where
        a reader looks to see which loop this is, and the glyph is what keeps it
        apart from the condition: `while name` would otherwise be a loop over a
        name that is true, and a marker is what every language that puts a label
        here has needed.
        """
        mark = self._accept(TokKind.LABEL)
        if mark is None:
            return None
        name = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_LABEL_NAME)
        return ast.Label(span=mark.span.to(name.span), name=name.text)

    def _parse_jump(self) -> ast.Stmt:
        """Parse ``break \N{SECTION SIGN}name`` or ``continue \N{SECTION SIGN}name``.

        The name is written every time.  What a jump with none would mean is
        the loop nearest to it, which is a thing that changes when a loop is
        put around it -- and putting a loop around something is what a program
        that writes this language does all day.
        """
        keyword = self._advance()
        label = self._parse_label()
        if label is None:
            self._diags.emit(D.LANG_SYNTAX_JUMP_WITHOUT_A_LABEL, keyword.span,
                             keyword=keyword.text)
            raise _Bail()
        span = keyword.span.to(label.span)
        if keyword.kind is not TokKind.KW_BREAK:
            return ast.Continue(span=span, label=label)
        handed: ast.Expr | None = None
        if not self._ends_a_statement():
            handed = self._parse_expression()
            span = span.to(handed.span)
        return ast.Break(span=span, label=label, value=handed)

    def _ends_a_statement(self) -> bool:
        """Whether what comes next ends the statement rather than continuing it.

        `break \N{SECTION SIGN}name` may be followed by what the loop comes to, and may
        equally be followed by nothing, so what is written next is the question
        of which was meant.
        """
        return self._check(TokKind.NEWLINE) or self._check(TokKind.SEMICOLON) \
            or self._check(TokKind.RBRACE) or self._check(TokKind.DEDENT) \
            or self._check(TokKind.EOF)

    def _binds_a_value(self) -> bool:
        """Whether what follows `while` is a binding rather than a condition.

        A name and a comma can begin nothing else, and a name and a colon can
        begin only one other thing -- a condition that is a bare name, with the
        colon opening the body -- which is why the line ending after it is what
        tells the two apart.
        """
        if not self._check(TokKind.IDENT):
            return False
        if self._peek().kind is TokKind.COMMA:
            return True
        return (self._peek().kind is TokKind.COLON
                and self._peek(2).kind is not TokKind.NEWLINE)

    def _parse_comptime(self) -> ast.Expr:
        """Parse what `comptime` stands before where a value is wanted."""
        if self._peek().kind is TokKind.KW_IF:
            return self._parse_if()
        start = self._advance().span
        self._expect(TokKind.KW_FOREACH)
        return self._parse_iteration(start, "foreach", self._parse_label(),
                                     comptime=True)

    def _parse_iteration(self, start: Span, keyword: str,
                         label: ast.Label | None = None,
                         comptime: bool = False) -> ast.ForEach:
        """Parse ``NAMES ':' [TYPE] '=' EXPR BODY``, which is `let`'s shape.

        The colon is always there and the type may be left out, exactly as in a
        variable: written with neither a qualifier nor a type the two characters
        read as ``:=``, and they are the same two tokens either way.  It is not
        optional because a name binds a value here as much as `let` does, and
        two spellings of one thing is what this language does not have -- and
        after `while` it could not be optional anyway, a name on its own
        followed by a colon being a condition with a body after it.
        """
        name_token = self._expect(TokKind.IDENT)
        more: list[tuple[str, Span]] = []
        while self._accept(TokKind.COMMA) is not None:
            written = self._expect(TokKind.IDENT)
            more.append((written.text, written.span))
        declared: ast.TypeExpr | None = None
        self._expect(TokKind.COLON, D.LANG_VARDEF_EXPECTED_COLON)
        if self._begins_a_type():
            declared = self._parse_type_ref()
        if self._accept(TokKind.EQUALS) is None:
            self._diags.emit(D.LANG_VARDEF_MISSING_INITIALIZER, name_token.span,
                             name=name_token.text)
            raise _Bail()
        iterable = self._parse_expression()
        body = self._parse_body()
        otherwise = self._parse_otherwise()
        return ast.ForEach(span=start.to((otherwise or body).span),
                           name=name_token.text,
                           name_span=name_token.span, type=declared,
                           iterable=iterable, body=body, more=tuple(more),
                           keyword=keyword, label=label, alternative=otherwise,
                           comptime=comptime)

    def _parse_if(self) -> ast.If:
        """Parse ``[comptime] if COND BODY`` with its `elif`s and its `else`.

        The condition stands on its own: there are no parentheses around it,
        because nothing needs them -- what ends it is the body, which begins
        with a colon or a brace, and neither can be part of an expression.

        `comptime` is written before the keyword of each arm it applies to, and
        not once for the whole `if`: which arms are settled while compiling is a
        property of each condition rather than of the chain, and a chain that
        mixes the two is a program asking one question of the compiler and
        another of itself.
        """
        start = self._current.span
        arms: list[ast.IfArm] = [self._parse_if_arm(self._takes_comptime(
            TokKind.KW_IF))]
        while self._begins_an_arm(TokKind.KW_ELIF):
            arms.append(self._parse_if_arm(self._takes_comptime(TokKind.KW_ELIF)))
        if self._check(TokKind.KW_ELSE):
            self._advance()
            body = self._parse_body()
            arms.append(ast.IfArm(span=body.span, condition=None, body=body))
            if self._begins_an_arm(TokKind.KW_ELIF) or self._check(TokKind.KW_ELSE):
                self._diags.emit(D.LANG_IF_ELSE_IS_LAST, self._current.span)
                raise _Bail()
        return ast.If(span=start.to(arms[-1].body.span), arms=tuple(arms))

    def _begins_an_arm(self, keyword: TokKind) -> bool:
        """Whether an arm beginning with *keyword* stands here, with or without
        the word that says it is settled while compiling."""
        return self._check(keyword) or (
            self._check(TokKind.KW_COMPTIME) and self._peek().kind is keyword)

    def _takes_comptime(self, keyword: TokKind) -> bool:
        """Read past an arm's keyword, saying whether `comptime` came first."""
        found = self._accept(TokKind.KW_COMPTIME) is not None
        self._expect(keyword)
        return found

    def _parse_if_arm(self, comptime: bool = False) -> ast.IfArm:
        """Parse the condition of one arm and the body it runs."""
        condition = self._parse_expression()
        body = self._parse_body()
        return ast.IfArm(span=condition.span.to(body.span), condition=condition,
                         body=body, comptime=comptime)

    def _parse_match(self) -> ast.Match:
        """Parse ``match EXPR`` and the arms that take its alternatives apart.

        The arms stand where the statements of a body would, in either of the
        two notations: indented under a colon, or inside braces.  An arm is a
        pattern and then a body written the way a function's is -- a colon and
        an indented block, or braces -- so that what a body looks like is one
        thing wherever one appears.  Inside braces the arms follow one another
        with nothing between them, each ending in the brace that closes it.
        """
        start = self._expect(TokKind.KW_MATCH).span
        subject = self._parse_expression()
        arms: list[ast.MatchArm] = []
        if self._check(TokKind.LBRACE):
            self._advance()
            while not self._check(TokKind.RBRACE) and not self._check(TokKind.EOF):
                arms.append(self._parse_arm())
            end = self._expect(TokKind.RBRACE).span
        else:
            self._expect(TokKind.COLON, D.LANG_MATCH_EXPECTED_ARMS)
            self._expect(TokKind.NEWLINE)
            self._expect(TokKind.INDENT)
            while not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
                self._skip_newlines()
                if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                    break
                arms.append(self._parse_arm())
                self._skip_newlines()
            end = self._current.span
            self._accept(TokKind.DEDENT)
        return ast.Match(span=start.to(end), subject=subject, arms=tuple(arms))

    def _parse_arm(self) -> ast.MatchArm:
        """Parse one arm: a pattern and the body it runs."""
        pattern = self._parse_pattern()
        body = self._parse_body()
        return ast.MatchArm(span=pattern.span.to(body.span), pattern=pattern,
                            body=body)

    def _parse_pattern(self) -> ast.Pattern:
        """Parse ``TYPE``, ``TYPE(NAME)``, ``\N{UP TACK}``, ``\N{UP TACK}(NAME)`` or ``_``.

        `_` takes every alternative no earlier arm took and binds nothing, so a
        name written after it would stand for a value of no one type.
        """
        if self._check(TokKind.IDENT) and self._current.text == WILDCARD_NAME:
            token = self._advance()
            if self._accept(TokKind.LPAREN) is None:
                return ast.Pattern(span=token.span, type=None, wildcard=True)
            # `_(name)` is a pattern in shape and not in meaning, so it is built
            # and reported where the rest of what an arm means is checked.
            name_token = self._expect(TokKind.IDENT)
            end = self._expect(TokKind.RPAREN,
                               D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span
            return ast.Pattern(span=token.span.to(end), type=None, wildcard=True,
                               name=name_token.text, name_span=name_token.span)
        bottom = self._accept(TokKind.BOTTOM)
        written = None if bottom is not None else self._parse_type_ref()
        start = bottom.span if bottom is not None else written.span  # pyright: ignore
        if self._accept(TokKind.LPAREN) is None:
            return ast.Pattern(span=start, type=written)
        name_token = self._expect(TokKind.IDENT)
        end = self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span
        return ast.Pattern(span=start.to(end), type=written, name=name_token.text,
                           name_span=name_token.span)

    def _parse_assignment(self, target: ast.Expr) -> ast.Stmt:
        """Parse the rest of ``TARGET ← VALUE``.

        What may stand on the left is a name, a field of a record, an entry of
        a dictionary and an element of an array, each written the way one is
        read.  Anything else is a value the program worked out, and there is
        nowhere for an assignment to put anything.
        """
        self._expect(TokKind.ASSIGN)
        value = self._parse_expression()
        if isinstance(target, ast.NameRef):
            return ast.AssignStmt(span=target.span.to(value.span), name=target.name,
                                  name_span=target.span, value=value)
        if isinstance(target, ast.Index):
            return ast.EntryAssign(span=target.span.to(value.span),
                                   base=target.base, key=target.key, value=value)
        if isinstance(target, ast.Element):
            return ast.ElementAssign(span=target.span.to(value.span),
                                     base=target.base, indices=target.indices,
                                     value=value)
        if isinstance(target, ast.Deref):
            return ast.DerefAssign(span=target.span.to(value.span),
                                   target=target.operand, value=value)
        if isinstance(target, ast.Member):
            return ast.MemberAssign(span=target.span.to(value.span),
                                    base=target.base, name=target.name,
                                    name_span=target.name_span, value=value)
        if isinstance(target, ast.Hole):
            # In a macro's template, where what it writes to is not known until the
            # hole is filled.  Which assignment it turns out to be is settled by
            # expansion, and a hole that matched a value is reported there.
            return ast.HoleAssign(span=target.span.to(value.span), target=target,
                                  value=value)
        self._diags.emit(D.LANG_ASSIGN_NOT_A_PLACE, target.span)
        raise _Bail()

    def _parse_expression(self, minimum: int = 0) -> ast.Expr:
        """Parse an expression whose operators bind at least as tightly as *minimum*.

        Precedence climbing: one function for every level, rather than one
        function per level.  Adding an operator is a row in the table above and
        nothing else, which is what keeps the grammar from growing a new layer
        each time the language gains a symbol.

        A range is the one thing here that is not in that table, because it is
        not a binary operator: it takes two ends or three, and three written
        with a binary operator would nest, which is not what `a…b…c` means.
        """
        left = self._parse_unary()
        while True:
            if self._check(TokKind.RANGE) and _RANGE_PRECEDENCE >= minimum:
                left = self._parse_range(left)
                continue
            if self._check(TokKind.OPERATOR):
                if _FRESH_PRECEDENCE < minimum:
                    return left
                token = self._advance()
                right = self._parse_expression(_FRESH_PRECEDENCE + 1)
                left = ast.Fresh(span=left.span.to(right.span),
                                 glyph=token.text, operands=(left, right))
                continue
            operator = _BINARY_OPERATORS.get(self._current.kind)
            if operator is None or operator.precedence < minimum:
                return left
            self._advance()
            # A left-associative operator will not take another of its own level
            # on the right, so the next level up is where its right side starts.
            right = self._parse_expression(
                operator.precedence + (0 if operator.right_associative else 1))
            left = ast.Binary(span=left.span.to(right.span), op=operator.op,
                              left=left, right=right)
            if operator.non_associative:
                following = _BINARY_OPERATORS.get(self._current.kind)
                if following is not None \
                        and following.precedence == operator.precedence:
                    self._diags.emit(D.LANG_SYNTAX_NOT_ASSOCIATIVE,
                                     self._current.span, first=operator.op.value,
                                     second=following.op.value)
                    raise _Bail()

    def _parse_range(self, first: ast.Expr) -> ast.Range:
        """Parse the rest of ``A…B`` or ``A…B…C``, the first end being in hand.

        The ends are parsed one level in, so that what is written on either side
        of the glyph binds to the end and not to the range: `1…n-1` reads the
        way it looks.
        """
        ends = [first]
        while self._accept(TokKind.RANGE) is not None:
            ends.append(self._parse_expression(_RANGE_PRECEDENCE + 1))
            if len(ends) > 3:
                self._diags.emit(D.LANG_SYNTAX_RANGE_TOO_MANY_ENDS,
                                 first.span.to(ends[-1].span), count=len(ends))
                raise _Bail()
        return ast.Range(span=first.span.to(ends[-1].span), start=ends[0],
                         stop=ends[1], step=ends[2] if len(ends) > 2 else None)

    def _parse_unary(self) -> ast.Expr:
        """Parse an operand, with any operators written before it."""
        if self._check(TokKind.OPERATOR):
            # A glyph the language gives no meaning, before one operand.  It binds
            # as tightly as every operator written before its operand does, which
            # is tighter than any written between two.
            token = self._advance()
            operand = self._parse_unary()
            return ast.Fresh(span=token.span.to(operand.span), glyph=token.text,
                             operands=(operand,))
        if self._check(TokKind.AMPERSAND):
            # `&` before an operand asks for a reference to the place it names;
            # `&` between two asks for their bits in common.  Which it is, is
            # decided by where it stands and by nothing else, as it is in C.
            token = self._advance()
            mutable = self._accept(TokKind.KW_MUT) is not None
            operand = self._parse_unary()
            return ast.AddressOf(span=token.span.to(operand.span),
                                 operand=operand, mutable=mutable)
        if self._check(TokKind.TAKE):
            # Three things may be taken out, and what says which is what is
            # written after it: a key of a collection, an element of a list, or
            # the element a cursor is at.  The operand is parsed the way any
            # other is and then read as whichever of the three it is.
            token = self._advance()
            operand = self._parse_unary()
            span = token.span.to(operand.span)
            if isinstance(operand, ast.Index):
                return ast.Take(span=span, base=operand.base, key=operand.key)
            if isinstance(operand, ast.Element):
                if len(operand.indices) != 1:
                    self._diags.emit(D.LANG_TAKE_NOT_AN_ENTRY, span)
                    return operand
                return ast.TakeAt(span=span, base=operand.base,
                                  index=operand.indices[0])
            return ast.TakeThrough(span=span, operand=operand)
        operator = _UNARY_OPERATORS.get(self._current.kind)
        if operator is None:
            return self._parse_primary()
        token = self._current
        self._advance()
        operand = self._parse_unary()
        return ast.Unary(span=token.span.to(operand.span), op=operator,
                         operand=operand)

    def _parse_primary(self) -> ast.Expr:
        """Parse an operand, with whatever is written after it.

        Both of these bind tighter than any operator, and to whatever stands
        immediately before them: `a.b(c)` calls `a.b`, and `f(x) + 1` adds to
        what the call answered with.

        Nothing is written after something that ends with a block of its own.
        An `if`, a `match` and a loop all end where their body ends, and in the
        layout notation that is a place with no line ending after it -- so the
        `(` beginning the next statement would otherwise be read as a call on
        what the block came to.  Nobody writes a call that way, and anybody who
        wanted one would write the brackets.
        """
        found = self._parse_atom()
        if _trailing_match(found):
            return found
        while True:
            if self._check(TokKind.DOT):
                self._advance()
                name = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_MEMBER)
                found = ast.Member(span=found.span.to(name.span), base=found,
                                   name=name.text, name_span=name.span)
                continue
            if self._check(TokKind.LPAREN):
                found = self._parse_call(found)
                continue
            if self._check(TokKind.SET_OPEN):
                self._advance()
                key = self._parse_expression()
                end = self._expect(TokKind.SET_CLOSE,
                                   D.LANG_SYNTAX_EXPECTED_CLOSING_SET).span
                found = ast.Index(span=found.span.to(end), base=found, key=key)
                continue
            if self._check(TokKind.ARRAY_OPEN):
                self._advance()
                indices = [self._parse_expression()]
                while self._accept(TokKind.COMMA) is not None:
                    indices.append(self._parse_expression())
                end = self._expect(TokKind.ARRAY_CLOSE,
                                   D.LANG_SYNTAX_EXPECTED_CLOSING_ARRAY).span
                found = ast.Element(span=found.span.to(end), base=found,
                                    indices=tuple(indices))
                continue
            if self._check(TokKind.LIFT_OPEN) and isinstance(found, ast.NameRef):
                # A macro invoked.  Only after a bare name, which is the only thing
                # a macro is ever called by -- and the marks follow nothing else
                # today, so the position is free.
                arguments = self._parse_quote()
                found = ast.Invoke(span=found.span.to(arguments.span),
                                   name=found.name, name_span=found.span,
                                   arguments=arguments)
                continue
            if self._check(TokKind.OPEN_OPERATOR):
                # A pair of brackets the language gives no meaning, written
                # around what stands inside it and after what it is applied to,
                # which is where the brackets the language has are written.  What
                # closes it is whatever closing bracket arrives: which closer
                # belongs to which opener is what a definition says, and Unicode
                # does not say it.
                opener = self._advance()
                inside = [self._parse_expression()]
                while self._accept(TokKind.COMMA) is not None:
                    inside.append(self._parse_expression())
                closer = self._expect(TokKind.CLOSE_OPERATOR)
                found = ast.Fresh(span=found.span.to(closer.span),
                                  glyph="".join((opener.text, closer.text)),
                                  operands=(found, *inside))
                continue
            if self._check(TokKind.DEREF):
                # What is at the place a reference names.  It stands after its
                # operand, so reaching further into what it answers -- an
                # element of it, a field of it -- reads left to right.
                end = self._advance().span
                found = ast.Deref(span=found.span.to(end), operand=found)
                continue
            if self._check(TokKind.EXPONENT):
                # A number written raised is a power whose exponent is
                # written down, which is what makes it answer differently from
                # the operator: here the compiler knows whether the exponent is
                # negative.  It binds where a call and an index bind, which is
                # to whatever stands immediately before it: `a²×b` squares
                # `a`, and `f(x)²` squares what the call answered with.
                raised = self._advance()
                assert raised.int_value is not None
                found = ast.Raised(span=found.span.to(raised.span), base=found,
                                   exponent=raised.int_value)
                continue
            if self._check(TokKind.QUESTION):
                mark = self._advance()
                found = ast.Try(span=found.span.to(mark.span), operand=found)
                continue
            return found

    def _parse_call(self, callee: ast.Expr) -> ast.Expr:
        """Parse the arguments written after what is being called.

        Positional, and separated by commas, as an attribute's arguments are.
        A call with none is written with the parentheses all the same: they are
        what says a call is being made, not what carries the arguments.

        An argument written after `\N{ASTERISM}` is a tuple handed over as several
        arguments rather than as one.  It stands where an argument stands, so
        arguments may be written before it and after it and more than one may
        appear; what it is not is an expression, and nowhere but here takes one.

        An argument written `.name \N{LEFTWARDS ARROW} VALUE` says which parameter it is for
        rather than leaving the place to say.  The dot is what says the name is
        a parameter's: a leading one cannot be a member access, there being
        nothing on its left.
        """
        self._expect(TokKind.LPAREN)
        args: list[ast.Expr] = []
        if not self._check(TokKind.RPAREN):
            while True:
                args.append(self._parse_named() if self._check(TokKind.DOT)
                            else self._parse_spreadable())
                if self._accept(TokKind.COMMA) is None:
                    break
        end = self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span
        return ast.Call(span=callee.span.to(end), callee=callee, args=tuple(args))

    def _parse_named(self) -> ast.Expr:
        """Parse ``'.' NAME '\N{LEFTWARDS ARROW}' VALUE``, an argument that names its parameter."""
        start = self._expect(TokKind.DOT).span
        name_token = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_PARAMETER)
        self._expect(TokKind.ASSIGN, D.LANG_SYNTAX_EXPECTED_NAMED_VALUE)
        value = self._parse_expression()
        return ast.Named(span=start.to(value.span), name=name_token.text,
                         name_span=name_token.span, value=value)

    def _parse_spreadable(self) -> ast.Expr:
        """Parse one entry of a list that admits `\N{ASTERISM}` in front of it.

        The two such lists are a call's arguments and a tuple's members.  What
        the glyph marks is not an expression -- nowhere that wants exactly one
        value accepts it -- so it is a rule of the list rather than of the
        expression grammar, and this is the one place that rule lives.
        """
        mark = self._accept(TokKind.SPREAD)
        written = self._parse_expression()
        if mark is None:
            return written
        return ast.Spread(span=mark.span.to(written.span), operand=written)

    def _parse_tuple(self) -> ast.Expr:
        """Parse ``\N{LEFT ANGLE BRACKET}a, b\N{RIGHT ANGLE BRACKET}``.

        A member written after `\N{ASTERISM}` stands for several members rather than
        one, exactly as an argument does: a list of values is what the glyph
        wants, and a tuple's members are the other list this language has.
        """
        start = self._expect(TokKind.TUPLE_OPEN).span
        members = [self._parse_spreadable()]
        while self._accept(TokKind.COMMA) is not None:
            members.append(self._parse_spreadable())
        end = self._expect(TokKind.TUPLE_CLOSE,
                           D.LANG_SYNTAX_EXPECTED_CLOSING_TUPLE).span
        return ast.TupleLit(span=start.to(end), members=tuple(members))

    def _parse_collection(self) -> ast.Expr:
        """Parse ``\N{LEFT DOUBLE PARENTHESIS}a, b\N{RIGHT DOUBLE PARENTHESIS}`` or ``\N{LEFT DOUBLE PARENTHESIS}k: v, k: v\N{RIGHT DOUBLE PARENTHESIS}``.

        Which of the two it is is decided by the first entry: a colon after it
        makes it a dictionary, and its absence a set.  One written with nothing
        in it is neither until something says which, and what says so is the
        type it is wanted as.
        """
        start = self._expect(TokKind.SET_OPEN).span
        if self._check(TokKind.SET_CLOSE):
            end = self._advance().span
            where = self._parse_arena()
            return ast.SetLit(span=start.to(where.span if where is not None else end),
                              elements=(), arena=where)
        first = self._parse_expression()
        if self._accept(TokKind.COLON) is None:
            elements = [first]
            while self._accept(TokKind.COMMA) is not None:
                elements.append(self._parse_expression())
            end = self._expect(TokKind.SET_CLOSE,
                               D.LANG_SYNTAX_EXPECTED_CLOSING_SET).span
            where = self._parse_arena()
            return ast.SetLit(
                span=start.to(where.span if where is not None else end),
                elements=tuple(elements), arena=where)
        entries = [(first, self._parse_expression())]
        while self._accept(TokKind.COMMA) is not None:
            key = self._parse_expression()
            self._expect(TokKind.COLON, D.LANG_SYNTAX_EXPECTED_ENTRY_VALUE)
            entries.append((key, self._parse_expression()))
        end = self._expect(TokKind.SET_CLOSE,
                           D.LANG_SYNTAX_EXPECTED_CLOSING_SET).span
        where = self._parse_arena()
        return ast.DictLit(span=start.to(where.span if where is not None else end),
                           entries=tuple(entries), arena=where)

    def _parse_arena(self) -> ast.NameRef | None:
        """Parse ``'in' NAME`` where one was written: which allocator to use.

        A name and not an expression.  What goes here is a place the allocator
        keeps its state in, and a place is named rather than computed -- the
        same reason the left of an assignment is a name.
        """
        if self._accept(TokKind.KW_IN) is None:
            return None
        written = self._expect(TokKind.IDENT)
        return ast.NameRef(span=written.span, name=written.text)

    def _parse_array(self) -> ast.ArrayLit:
        """Parse ``\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}a, b, c\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}``: an array written down.

        One written with nothing in it is written all the same; what it holds
        is then the type it is wanted as, and how many is none.
        """
        start = self._expect(TokKind.ARRAY_OPEN).span
        elements: list[ast.Expr] = []
        if not self._check(TokKind.ARRAY_CLOSE):
            elements.append(self._parse_expression())
            while self._accept(TokKind.COMMA) is not None:
                elements.append(self._parse_expression())
        end = self._expect(TokKind.ARRAY_CLOSE,
                           D.LANG_SYNTAX_EXPECTED_CLOSING_ARRAY).span
        return ast.ArrayLit(span=start.to(end), elements=tuple(elements))

    def _parse_list(self) -> ast.ListLit:
        """Parse ``'[' a, b, c ']'``: a list written down.

        One written with nothing in it is written all the same; what it holds is
        then the type it is wanted as, there being nothing in it to say.
        """
        start = self._expect(TokKind.LBRACKET).span
        elements: list[ast.Expr] = []
        if not self._check(TokKind.RBRACKET):
            elements.append(self._parse_expression())
            while self._accept(TokKind.COMMA) is not None:
                elements.append(self._parse_expression())
        end = self._expect(TokKind.RBRACKET,
                           D.LANG_SYNTAX_EXPECTED_CLOSING_LIST).span
        return ast.ListLit(span=start.to(end), elements=tuple(elements))

    def _parse_lift(self) -> ast.Expr:
        """Parse ``'\N{TOP LEFT CORNER}' (TYPE | EXPRESSION) '\N{TOP RIGHT CORNER}'``.

        A type is tried first and kept where it reaches the closing bracket,
        because everything a type may be is written in a way no expression is --
        except a bare name, which is both and which the checker settles by
        looking the name up.  Anything else is an expression.

        Reading it twice rather than deciding is what makes the brackets worth
        having: what is between them may be a type this parser cannot tell from
        an expression, and the one thing it does not have to do is guess.
        """
        start = self._expect(TokKind.LIFT_OPEN).span
        mark = self._pos
        written: ast.TypeRef | None = None
        if self._begins_a_type():
            written = self._parse_type_ref()
            if not self._check(TokKind.LIFT_CLOSE):
                written = None
                self._pos = mark
        value = None if written is not None else self._parse_expression()
        end = self._expect(TokKind.LIFT_CLOSE, D.LANG_SYNTAX_EXPECTED_CLOSING_LIFT)
        return ast.Lifted(span=start.to(end.span), written=written, value=value)

    def _parse_atom(self) -> ast.Expr:
        """Parse an expression with nothing binding it to what is around it."""
        token = self._current
        if token.kind is TokKind.DOLLAR:
            # A hole, which stands where an expression stands and is written only
            # inside a macro's pattern or template.  It is read here rather than by
            # a parser of its own so that a pattern is an ordinary expression with
            # holes in it, which is what makes a rule read like what it matches.
            self._advance()
            name = self._expect(TokKind.IDENT)
            return ast.Hole(span=token.span.to(name.span), name=name.text)
        if token.kind is TokKind.LAMBDA:
            # A function written where a value is wanted.  It ends with its
            # body, so nothing may follow it on the line -- which is the rule
            # everything ending in a block already follows.
            return self._parse_lambda()
        if token.kind is TokKind.AT_LBRACKET:
            # What is said about a lambda is said the way it is said about a
            # function, before the thing it describes.  Nothing else begins an
            # expression with a bracket after an at sign, so there is nothing
            # for this to be confused with.
            attrs = self._parse_attributes()
            return self._parse_lambda(attrs)
        if token.kind is TokKind.LPAREN:
            self._advance()
            inner = self._parse_expression()
            self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN)
            return inner
        if token.kind is TokKind.BOTTOM:
            # `\N{UP TACK}` and `\N{UP TACK} VALUE`, read the way `return` is read: what follows
            # is the whole of an expression where one begins there, and nothing
            # where the statement ends.
            self._advance()
            value = (self._parse_expression()
                     if _begins_an_expression(self._current.kind) else None)
            return ast.Failure(
                span=token.span if value is None else token.span.to(value.span),
                value=value)
        if token.kind is TokKind.LIFT_OPEN:
            return self._parse_lift()
        match token.kind:
            case TokKind.INT:
                self._advance()
                assert token.int_value is not None
                return ast.IntLit(span=token.span, value=token.int_value,
                                  type_name=token.int_type)
            case TokKind.FLOAT:
                self._advance()
                assert token.float_value is not None
                return ast.FloatLit(span=token.span, value=token.float_value,
                                    type_name=token.float_type)
            case TokKind.STRING:
                self._advance()
                assert token.str_value is not None
                return ast.StringLit(span=token.span, value=token.str_value)
            case TokKind.CHAR:
                self._advance()
                assert token.int_value is not None
                return ast.CharLit(span=token.span, value=token.int_value)
            case TokKind.KW_TRUE | TokKind.KW_FALSE:
                self._advance()
                return ast.BoolLit(span=token.span, value=token.kind is TokKind.KW_TRUE)
            case TokKind.TUPLE_OPEN:
                return self._parse_tuple()
            case TokKind.SET_OPEN:
                return self._parse_collection()
            case TokKind.LBRACKET:
                return self._parse_list()
            case TokKind.ARRAY_OPEN:
                return self._parse_array()
            case TokKind.SPREAD:
                # Said here rather than left to "expected an expression",
                # because what is wrong is not that the glyph is unknown but
                # that it belongs somewhere this is not.
                self._diags.emit(D.LANG_SYNTAX_SPREAD_OUTSIDE_A_CALL, token.span)
                raise _Bail()
            case TokKind.KW_MATCH:
                return self._parse_match()
            case TokKind.KW_IF:
                return self._parse_if()
            case TokKind.KW_COMPTIME:
                return self._parse_comptime()
            case TokKind.KW_WHILE | TokKind.KW_UNLESS:
                return self._parse_while()
            case TokKind.KW_FOREACH:
                start = self._advance().span
                return self._parse_iteration(start, "foreach",
                                             self._parse_label())
            case TokKind.IDENT:
                self._advance()
                return ast.NameRef(span=token.span, name=token.text)
            case _:
                self._diags.emit(D.LANG_SYNTAX_UNEXPECTED_TOKEN, token.span,
                                 expected="an expression", found=token.describe())
                raise _Bail()


def _ends_with_a_block(stmt: ast.Stmt) -> bool:
    """Whether a statement ends with a block of its own.

    Such a statement swallows the end of its own last line in the layout
    notation -- the dedent comes after that newline, not before -- so the block
    it stands in must not ask for one after it.  A loop is one outright; the
    other two are expressions, so what is asked of a statement is what it ends
    with.
    """
    return _trailing_match(getattr(stmt, "value", None))


def _trailing_match(expr: ast.Expr | None) -> bool:
    """Whether something with an indented block of its own ends an expression.

    A block written on one line is not one: what closes it is the end of that
    line, which it therefore leaves for whatever it stands in to ask for.
    """
    while True:
        if isinstance(expr, (ast.Match, ast.If, ast.While, ast.ForEach,
                             ast.Lambda)):
            return not _ends_inline(expr)
        if isinstance(expr, ast.Binary):
            expr = expr.right
            continue
        return False


def _ends_inline(expr: ast.Expr) -> bool:
    """Whether the last block of *expr* is one written on one line."""
    last = getattr(expr, "body", None)
    if isinstance(expr, ast.If) and expr.arms:
        last = expr.arms[-1].body
    if isinstance(expr, ast.Match) and expr.arms:
        last = expr.arms[-1].body
    if isinstance(expr, (ast.While, ast.ForEach)) and expr.alternative is not None:
        last = expr.alternative
    return isinstance(last, ast.Block) \
        and last.style is ast.BlockStyle.INLINE


def parse(tokens: Sequence[Token], path: str, diags: DiagEngine) -> ast.SourceUnit:
    """Parse the tokens of one source file."""
    return Parser(tokens, path, diags).parse_unit()
