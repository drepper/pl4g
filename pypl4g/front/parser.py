"""Recursive-descent parser.

The grammar is context-free and definitions need not be processed in order, so
the parser produces one syntax tree per file and leaves every question of
meaning to the semantic analysis.
"""

from dataclasses import dataclass, replace
from typing import Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..source.location import Span
from . import ast
from .token import COMMENT_GLYPH, TokKind, Token

#: Tokens at which error recovery stops, because a new definition can begin there.
_RECOVERY: Final[frozenset[TokKind]] = frozenset(
    (TokKind.KW_FN, TokKind.KW_LET, TokKind.KW_TYPE, TokKind.AT_LBRACKET, TokKind.EOF))


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
    TokKind.PIPE: _Operator(ast.BinaryOp.BIT_OR, 10),
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
}

#: What may stand before an operand.  These bind tighter than anything above.
_UNARY_OPERATORS: Final[dict[TokKind, ast.UnaryOp]] = {
    TokKind.TILDE: ast.UnaryOp.BIT_NOT,
    TokKind.LOGIC_NOT: ast.UnaryOp.LOGIC_NOT,
}


class _Bail(Exception):
    """Unwinds to the top-level recovery point after an unrecoverable error."""


class Parser:
    """Parses the tokens of one source file."""

    def __init__(self, tokens: Sequence[Token], path: str, diags: DiagEngine) -> None:
        self._tokens = tokens
        self._path = path
        self._diags = diags
        self._pos = 0

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
        doc = self._parse_doc_comments()
        attrs = self._parse_attributes()
        self._skip_newlines()
        if self._check(TokKind.KW_FN):
            return self._parse_function(attrs, doc)
        if self._check(TokKind.KW_LET):
            return self._parse_variable(attrs, doc)
        self._diags.emit(D.LANG_FILESTRUCT_UNEXPECTED_TOPLEVEL, self._current.span,
                         construct=self._current.describe())
        raise _Bail()

    def _parse_variable(self, attrs: tuple[ast.Attribute, ...] = (),
                        doc: str | None = None) -> "ast.VarDef | ast.ModuleImport":
        """Parse ``let NAME ':' ['mut'] [TYPE] '=' VALUE``.

        The colon is always there; what varies is what follows it.  Written with
        neither a qualifier nor a type, and without a space, the two characters
        read as ``:=``, but they are the same two tokens either way.
        """
        start = self._expect(TokKind.KW_LET).span
        name_token = self._expect(TokKind.IDENT)
        self._expect(TokKind.COLON, D.LANG_VARDEF_EXPECTED_COLON)
        # 'mut' qualifies the type, so it stands where the type does.  Either
        # part may be left out: the type is then the value's, and without 'mut'
        # the variable keeps whatever it was given.
        mutable = self._accept(TokKind.KW_MUT) is not None
        declared: ast.TypeRef | None = None
        if self._check(TokKind.IDENT):
            declared = self._parse_type_ref()
        if self._accept(TokKind.EQUALS) is None:
            self._diags.emit(D.LANG_VARDEF_MISSING_INITIALIZER, name_token.span,
                             name=name_token.text)
            raise _Bail()
        if self._check(TokKind.KW_IMPORT):
            return self._parse_import(start, name_token, mutable, declared, doc)
        value = self._parse_expression()
        return ast.VarDef(span=start.to(value.span), name=name_token.text,
                          name_span=name_token.span, type=declared, value=value,
                          mutable=mutable, doc=doc, attrs=attrs)

    def _parse_import(self, start: Span, name_token: Token, mutable: bool,
                      declared: "ast.TypeRef | None",
                      doc: str | None) -> ast.ModuleImport:
        """Parse the rest of ``let NAME ':=' import(STRING)``.

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
                                source_span=source.span, doc=doc)

    def _parse_doc_comments(self) -> str | None:
        """Collect the documentation comments preceding a definition."""
        lines: list[str] = []
        while self._check(TokKind.DOC_COMMENT):
            lines.append(self._advance().text.removeprefix(COMMENT_GLYPH * 2).strip())
            self._skip_newlines()
        return "\n".join(lines) if lines else None

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
                        doc: str | None) -> ast.FuncDef:
        """Parse a function definition."""
        start = self._expect(TokKind.KW_FN).span
        name_token = self._expect(TokKind.IDENT)
        self._expect(TokKind.LPAREN)
        params = self._parse_params()
        self._expect(TokKind.RPAREN)
        # The arrow and what follows it say what the function answers with.
        # Leaving them off is how a function says it answers with nothing;
        # there is no name to write for that, which is what keeps the two from
        # being two ways of saying one thing.
        ret_type: ast.TypeRef | None = None
        if self._accept(TokKind.ARROW) is not None:
            ret_type = self._parse_type_ref()
        body = self._parse_body()
        return ast.FuncDef(span=start.to(body.span), name=name_token.text,
                           name_span=name_token.span, params=params, ret_type=ret_type,
                           body=body, attrs=attrs, doc=doc)

    def _parse_type_ref(self) -> ast.TypeRef:
        """Parse a type: a name, and whatever says what else it may be.

        `TYPE?` is a result: a value of that type, or the fact that there is
        none.  `TYPE?ERROR` is one whose error carries a value of its own, and
        the name after the mark is what says so -- the mark with nothing after
        it is what says the error is only the fact of it.
        """
        name_token = self._expect(TokKind.IDENT)
        mark = self._accept(TokKind.QUESTION)
        if mark is None:
            return ast.TypeRef(span=name_token.span, name=name_token.text)
        error: str | None = None
        last = mark
        if self._check(TokKind.IDENT):
            last = self._advance()
            error = last.text
        return ast.TypeRef(span=name_token.span.to(last.span),
                           name=name_token.text, result=True, error=error)

    def _parse_params(self) -> tuple[ast.Param, ...]:
        """Parse a parameter list, which may be empty."""
        params: list[ast.Param] = []
        seen: dict[str, Span] = {}
        if self._check(TokKind.RPAREN):
            return ()
        while True:
            name_token = self._expect(TokKind.IDENT)
            self._expect(TokKind.COLON)
            written = self._parse_type_ref()
            if name_token.text in seen:
                self._diags.emit(D.LANG_FUNCDEF_DUPLICATE_PARAMETER, name_token.span,
                                 name=name_token.text)
            else:
                seen[name_token.text] = name_token.span
            params.append(ast.Param(
                span=name_token.span.to(written.span), name=name_token.text,
                type=written))
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
        self._expect(TokKind.NEWLINE)
        self._expect(TokKind.INDENT)
        stmts: list[ast.Stmt] = []
        while not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
            self._skip_newlines()
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                break
            stmts.extend(self._parse_separated())
            if not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
                self._expect(TokKind.NEWLINE)
        end = self._current.span
        self._accept(TokKind.DEDENT)
        return ast.Block(span=start.to(end), style=ast.BlockStyle.LAYOUT, stmts=tuple(stmts))

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
        if self._check(TokKind.IDENT) and self._peek().kind is TokKind.ASSIGN:
            return self._parse_assignment()
        if self._check(TokKind.KW_RETURN):
            start = self._advance().span
            if self._check(TokKind.NEWLINE) or self._check(TokKind.SEMICOLON) \
                    or self._check(TokKind.RBRACE) or self._check(TokKind.DEDENT):
                return ast.ReturnStmt(span=start, value=None, explicit=True)
            value = self._parse_expression()
            return ast.ReturnStmt(span=start.to(value.span), value=value, explicit=True)
        value = self._parse_expression()
        return ast.ExprStmt(span=value.span, value=value)

    def _parse_assignment(self) -> ast.Stmt:
        """Parse ``NAME ← VALUE``."""
        name_token = self._advance()
        self._expect(TokKind.ASSIGN)
        value = self._parse_expression()
        return ast.AssignStmt(span=name_token.span.to(value.span), name=name_token.text,
                              name_span=name_token.span, value=value)

    def _parse_expression(self, minimum: int = 0) -> ast.Expr:
        """Parse an expression whose operators bind at least as tightly as *minimum*.

        Precedence climbing: one function for every level, rather than one
        function per level.  Adding an operator is a row in the table above and
        nothing else, which is what keeps the grammar from growing a new layer
        each time the language gains a symbol.
        """
        left = self._parse_unary()
        while True:
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

    def _parse_unary(self) -> ast.Expr:
        """Parse an operand, with any operators written before it."""
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
        """
        found = self._parse_atom()
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
        """
        self._expect(TokKind.LPAREN)
        args: list[ast.Expr] = []
        if not self._check(TokKind.RPAREN):
            while True:
                args.append(self._parse_expression())
                if self._accept(TokKind.COMMA) is None:
                    break
        end = self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span
        return ast.Call(span=callee.span.to(end), callee=callee, args=tuple(args))

    def _parse_atom(self) -> ast.Expr:
        """Parse an expression with nothing binding it to what is around it."""
        token = self._current
        if token.kind is TokKind.LPAREN:
            self._advance()
            inner = self._parse_expression()
            self._expect(TokKind.RPAREN, D.LANG_SYNTAX_EXPECTED_CLOSING_PAREN)
            return inner
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
            case TokKind.KW_TRUE | TokKind.KW_FALSE:
                self._advance()
                return ast.BoolLit(span=token.span, value=token.kind is TokKind.KW_TRUE)
            case TokKind.IDENT:
                self._advance()
                return ast.NameRef(span=token.span, name=token.text)
            case _:
                self._diags.emit(D.LANG_SYNTAX_UNEXPECTED_TOKEN, token.span,
                                 expected="an expression", found=token.describe())
                raise _Bail()


def parse(tokens: Sequence[Token], path: str, diags: DiagEngine) -> ast.SourceUnit:
    """Parse the tokens of one source file."""
    return Parser(tokens, path, diags).parse_unit()
