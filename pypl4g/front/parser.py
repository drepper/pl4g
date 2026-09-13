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


#: What may stand between two operands, and how tightly each binds.  The order
#: is the one C settled on and Rust, Go and Zig kept: bitwise "and" binds
#: tighter than "exclusive or", which binds tighter than "or".
_BINARY_OPERATORS: Final[dict[TokKind, _Operator]] = {
    TokKind.PIPE: _Operator(ast.BinaryOp.BIT_OR, 10),
    TokKind.CARET: _Operator(ast.BinaryOp.BIT_XOR, 20),
    TokKind.AMPERSAND: _Operator(ast.BinaryOp.BIT_AND, 30),
}

#: What may stand before an operand.  These bind tighter than anything above.
_UNARY_OPERATORS: Final[dict[TokKind, ast.UnaryOp]] = {
    TokKind.TILDE: ast.UnaryOp.BIT_NOT,
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
            type_token = self._advance()
            declared = ast.TypeRef(span=type_token.span, name=type_token.text)
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
        """Parse any number of ``@[...]`` attribute lists."""
        attrs: list[ast.Attribute] = []
        while self._check(TokKind.AT_LBRACKET):
            self._advance()
            while True:
                attrs.append(self._parse_attribute())
                if self._accept(TokKind.COMMA) is None:
                    break
            self._expect(TokKind.RBRACKET)
            self._skip_newlines()
        return tuple(attrs)

    def _parse_attribute(self) -> ast.Attribute:
        """Parse one attribute and its arguments."""
        name_token = self._expect(TokKind.IDENT)
        args: list[ast.AttrArg] = []
        end = name_token.span
        if self._accept(TokKind.LPAREN) is not None:
            if not self._check(TokKind.RPAREN):
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
        self._expect(TokKind.ARROW, D.LANG_FUNCDEF_EXPECTED_ARROW)
        ret_token = self._expect(TokKind.IDENT)
        ret_type = ast.TypeRef(span=ret_token.span, name=ret_token.text)
        body = self._parse_body()
        return ast.FuncDef(span=start.to(body.span), name=name_token.text,
                           name_span=name_token.span, params=params, ret_type=ret_type,
                           body=body, attrs=attrs, doc=doc)

    def _parse_params(self) -> tuple[ast.Param, ...]:
        """Parse a parameter list, which may be empty."""
        params: list[ast.Param] = []
        seen: dict[str, Span] = {}
        if self._check(TokKind.RPAREN):
            return ()
        while True:
            name_token = self._expect(TokKind.IDENT)
            self._expect(TokKind.COLON)
            type_token = self._expect(TokKind.IDENT)
            if name_token.text in seen:
                self._diags.emit(D.LANG_FUNCDEF_DUPLICATE_PARAMETER, name_token.span,
                                 name=name_token.text)
            else:
                seen[name_token.text] = name_token.span
            params.append(ast.Param(
                span=name_token.span.to(type_token.span), name=name_token.text,
                type=ast.TypeRef(span=type_token.span, name=type_token.text)))
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

    def _parse_layout_block(self) -> ast.Block:
        """Parse ``: NEWLINE INDENT statements DEDENT``."""
        start = self._expect(TokKind.COLON).span
        self._expect(TokKind.NEWLINE)
        self._expect(TokKind.INDENT)
        stmts: list[ast.Stmt] = []
        while not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
            self._skip_newlines()
            if self._check(TokKind.DEDENT) or self._check(TokKind.EOF):
                break
            stmts.append(self._parse_statement())
            if not self._check(TokKind.DEDENT) and not self._check(TokKind.EOF):
                self._expect(TokKind.NEWLINE)
        end = self._current.span
        self._accept(TokKind.DEDENT)
        return ast.Block(span=start.to(end), style=ast.BlockStyle.LAYOUT, stmts=tuple(stmts))

    def _parse_explicit_block(self) -> ast.Block:
        """Parse ``{ statement ; ... }``."""
        start = self._expect(TokKind.LBRACE).span
        stmts: list[ast.Stmt] = []
        while not self._check(TokKind.RBRACE) and not self._check(TokKind.EOF):
            stmts.append(self._parse_statement())
            if self._accept(TokKind.SEMICOLON) is not None:
                continue
            if self._check(TokKind.RBRACE) or self._check(TokKind.EOF):
                break
            # Two statements in a row without a separator means the block was
            # written in the layout notation inside explicit braces.
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
        if self._check(TokKind.IDENT) and self._peek().kind in (TokKind.ASSIGN,
                                                               TokKind.EQUALS):
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
        """Parse ``NAME ← VALUE``.

        A name followed by '=' is caught here rather than left to the expression
        grammar, so that the habit every other language teaches is answered with
        the rule instead of with a token nobody expected.
        """
        name_token = self._advance()
        if self._check(TokKind.EQUALS):
            self._diags.emit(D.LANG_ASSIGN_EXPECTED_ARROW, self._current.span)
            raise _Bail()
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
        """Parse an operand, with whatever is written after it."""
        found = self._parse_atom()
        while self._check(TokKind.DOT):
            self._advance()
            name = self._expect(TokKind.IDENT, D.LANG_SYNTAX_EXPECTED_MEMBER)
            found = ast.Member(span=found.span.to(name.span), base=found,
                               name=name.text, name_span=name.span)
        return found

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
