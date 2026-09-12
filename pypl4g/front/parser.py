"""Recursive-descent parser.

The grammar is context-free and definitions need not be processed in order, so
the parser produces one syntax tree per file and leaves every question of
meaning to the semantic analysis.
"""

from typing import Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..source.location import Span
from . import ast
from .token import COMMENT_GLYPH, TokKind, Token

#: Tokens at which error recovery stops, because a new definition can begin there.
_RECOVERY: Final[frozenset[TokKind]] = frozenset(
    (TokKind.KW_FN, TokKind.KW_VAR, TokKind.KW_TYPE, TokKind.AT_LBRACKET, TokKind.EOF))


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
        items: list[ast.FuncDef] = []
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

    def _parse_item(self) -> ast.FuncDef | None:
        """Parse one top-level definition together with what precedes it."""
        doc = self._parse_doc_comments()
        attrs = self._parse_attributes()
        self._skip_newlines()
        if self._check(TokKind.KW_FN):
            return self._parse_function(attrs, doc)
        self._diags.emit(D.LANG_FILESTRUCT_UNEXPECTED_TOPLEVEL, self._current.span,
                         construct=self._current.describe())
        raise _Bail()

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
        """Parse one statement."""
        if self._check(TokKind.KW_RETURN):
            start = self._advance().span
            if self._check(TokKind.NEWLINE) or self._check(TokKind.SEMICOLON) \
                    or self._check(TokKind.RBRACE) or self._check(TokKind.DEDENT):
                return ast.ReturnStmt(span=start, value=None, explicit=True)
            value = self._parse_expression()
            return ast.ReturnStmt(span=start.to(value.span), value=value, explicit=True)
        value = self._parse_expression()
        return ast.ExprStmt(span=value.span, value=value)

    def _parse_expression(self) -> ast.Expr:
        """Parse an expression.

        The expression grammar grows with the language; for now it is a literal
        or a name.
        """
        token = self._current
        match token.kind:
            case TokKind.INT:
                self._advance()
                assert token.int_value is not None
                return ast.IntLit(span=token.span, value=token.int_value)
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
