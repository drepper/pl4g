"""Where each variable's scope ends: after its last use, not at its block's end.

A name is bound by a `let`, a parameter, a `foreach`, an arm of a `match` or a
lambda's parameter, and is there for the rest of its block -- as far as what may
be *written* goes.  What the program can still *need* ends sooner: after the
last thing that uses it.  That is what a reader of the report log asks about a
variable, so each one gets a `scope-ended` entry on the line after the one its
scope ends on, which the viewer shows just below that line.

Four things carry a scope past the text of its last use:

- a loop: a use in a loop the variable was bound outside of may come round
  again, so the scope ends after the loop;
- what a `foreach` walks: it is kept for the whole loop, and so is what it was
  made of -- except the bounds of a range, which are worked out once;
- a deferred statement: it runs when its block is left, so a use in one is a
  use at the end of that block;
- a reference: a name bound to something holding a reference to the variable
  needs it for as long as that name lasts.  Which names hold which is the
  checker's to know, from the types, and it hands this the pairs.

A macro's expansion is part of the tree and carries the places of the macro's
own text, in another file: a use in it is a use in the statement that invoked
it, and what it binds and the loops it makes are its own business.

This is a walk of the tree and nothing else: it is done once per function,
however many times the function is lowered.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, fields as fields_of
from typing import Callable

from ..front import ast
from ..front.token import WILDCARD_NAME
from ..source.location import NO_LOC, Loc, Span
from ..source.manager import SourceFile, SourceManager


@dataclass(slots=True)
class _Binding:
    """A variable while its scope is walked, and how far its uses reach."""

    name: str
    #: Where it is bound, which is how the checker names it too.
    at: Span
    #: The end of the statement binding it, where it ends if nothing uses it.
    bound_end: Loc
    #: How many loops were open where it was bound.
    loops: int
    #: Where its scope ends so far, as a place in the text, and why.
    last: Loc = NO_LOC
    why: tuple[object, ...] = ()
    #: Whether that last use moves its value out: the move is then where it
    #: ended, and is logged as one.
    moved: bool = False


def end_of(node: object) -> Loc:
    """Where the text of *node* ends: the end of its last statement, where it
    ends with a block -- a block's span runs on to the next statement."""
    match node:
        case ast.Block():
            return end_of(node.stmts[-1]) if node.stmts else node.span.end
        case ast.ExprStmt() | ast.ReturnStmt() | ast.VarDef() | ast.AssignStmt():
            value = node.value
            return end_of(value) if isinstance(
                value, (ast.If, ast.Match, ast.Scoped, ast.ForEach, ast.While,
                        ast.Lambda)) else node.span.end
        case ast.Defer():
            return end_of(node.stmt)
        case ast.If():
            return end_of(node.arms[-1].body)
        case ast.Match():
            return end_of(node.arms[-1].body) if node.arms else node.span.end
        case ast.ForEach() | ast.While():
            return end_of(node.alternative if node.alternative is not None
                          else node.body)
        case ast.Scoped() | ast.Lambda():
            return end_of(node.body)
        case ast.Node():
            return node.span.end
    return NO_LOC


class LastUses:
    """Walk one function and report where each of its variables' scopes ends.

    *lends* maps where a name is bound to where the names it holds a reference
    to are bound; *moves* holds where a name is read as a value it is moved out
    of; *report* takes the variable's name, the reason and the place the entry
    goes.
    """

    def __init__(self, sources: SourceManager, lends: dict[Loc, set[Loc]],
                 moves: set[Loc], report: Callable[[str, str, Span], None]) -> None:
        self._sources = sources
        self._lends = lends
        self._moves = moves
        self._report = report
        self._file: SourceFile | None = None
        self._scopes: list[list[_Binding]] = []
        self._live: dict[Loc, _Binding] = {}
        #: The loops open where the walk is, outermost first, as where each
        #: begins and ends -- `None` for one a macro made.
        self._loops: list[tuple[Loc, Loc] | None] = []
        #: Where the statement being walked ends: a use is a use until then.
        self._stmt_end: Loc = NO_LOC
        #: Where a use counts as being where the walk is not in the function's
        #: own text, or is in what a `foreach` walks: the end of the statement
        #: or of the loop.
        self._held_until: Loc = NO_LOC
        #: The end of the block a deferred statement being walked runs at, and
        #: the statement.
        self._deferred: tuple[Loc, Span] | None = None
        self._blocks: list[Loc] = []

    def _ours(self, span: Span) -> bool:
        """Whether *span* is in the function's own text, and not a macro's."""
        return (span.is_valid and self._file is not None
                and self._file.base <= span.start <= self._file.end)

    # -- the walk --------------------------------------------------------------

    def function(self, node: ast.FuncDef) -> None:
        """Walk *node*: its parameters, its conditions and its body."""
        if node.body is None or node.is_macro or not node.span.is_valid:
            return
        self._file = self._sources.file_of(node.span.start)
        self._scopes.append([])
        for param in node.params:
            if param.default is not None:
                self._stmt_end = param.span.end
                self._expr(param.default)
            self._bind(param.name, param.span, param.span.end)
        for clause in node.clauses:
            self._stmt_end = clause.span.end
            self._expr(clause.expr)
        self._block(node.body, scoped=False)
        self._pop()

    def _block(self, block: ast.Block, scoped: bool = True) -> None:
        if scoped:
            self._scopes.append([])
        self._blocks.append(end_of(block) if self._ours(block.span)
                            else (self._blocks[-1] if self._blocks
                                  else self._stmt_end))
        for stmt in block.stmts:
            self._stmt(stmt)
        self._blocks.pop()
        if scoped:
            self._pop()

    def _stmt(self, stmt: ast.Stmt) -> None:
        if self._ours(stmt.span):
            self._stmt_end = end_of(stmt) \
                if not isinstance(stmt, (ast.ExprStmt, ast.ReturnStmt))\
                or not isinstance(stmt.value, (ast.If, ast.Match, ast.ForEach,
                                               ast.While, ast.Scoped)) \
                else stmt.span.start
        match stmt:
            case ast.VarDef():
                self._expr(stmt.value)
                if stmt.type is not None:
                    self._expr(stmt.type)
                if stmt.made_in is not None:
                    self._use(*stmt.made_in)
                if self._ours(stmt.span):
                    self._stmt_end = end_of(stmt)
                self._bind(stmt.name, stmt.name_span, self._stmt_end)
                for name, span in stmt.more:
                    self._bind(name, span, self._stmt_end)
            case ast.AssignStmt():
                self._expr(stmt.value)
                if self._ours(stmt.span):
                    self._stmt_end = end_of(stmt)
                self._use(stmt.name, stmt.name_span)
                for name, span in stmt.more:
                    self._use(name, span)
            case ast.Defer():
                outer = self._deferred
                if self._ours(stmt.span):
                    self._deferred = (self._blocks[-1], stmt.span)
                self._stmt(stmt.stmt)
                self._deferred = outer
            case _:
                self._fields(stmt)

    def _expr(self, node: object) -> None:
        match node:
            case ast.NameRef():
                self._use(node.name, node.span)
            case ast.Capture():
                self._use(node.name, node.span)
            case ast.Member():
                self._expr(node.base)
            case ast.Named():
                self._expr(node.value)
            case ast.Block():
                self._block(node)
            case ast.Stmt():
                self._stmt(node)
            case ast.If():
                for arm in node.arms:
                    if arm.condition is not None:
                        self._header(arm.condition)
                    self._block(arm.body)
            case ast.Match():
                self._header(node.subject)
                for arm in node.arms:
                    self._scopes.append([])
                    pattern = arm.pattern
                    if pattern.type is not None:
                        self._expr(pattern.type)
                    if pattern.name is not None and not pattern.wildcard:
                        self._bind(pattern.name, pattern.name_span,
                                   self._stmt_end)
                    self._block(arm.body, scoped=False)
                    self._pop()
            case ast.While():
                self._loops.append(self._loop(node))
                self._header(node.condition)
                self._block(node.body)
                self._loops.pop()
                if node.alternative is not None:
                    self._block(node.alternative)
            case ast.ForEach():
                self._for_each(node)
            case ast.Range() if self._held_until != NO_LOC:
                # The bounds of a range are worked out once, before the loop:
                # the loop keeps the range, and not what it was made of.
                outer, self._held_until = self._held_until, NO_LOC
                self._fields(node)
                self._held_until = outer
            case ast.Lambda():
                for capture in node.captures:
                    self._use(capture.name, capture.span)
                for where in (node.made_in, node.kept_in):
                    if where is not None:
                        self._use(*where)
                self._scopes.append([])
                for param in node.params:
                    self._bind(param.name, param.span, param.span.end)
                self._block(node.body, scoped=False)
                self._pop()
            case ast.Label() | ast.Attribute():
                pass
            case ast.Node():
                self._fields(node)
            case tuple():
                if len(node) == 2 and isinstance(node[0], str) \
                        and isinstance(node[1], Span):
                    # An arena named where something is made in it.
                    self._use(node[0], node[1])
                else:
                    for one in node:
                        self._expr(one)

    def _header(self, expr: ast.Expr) -> None:
        """Walk what an `if`, a `match` or a `while` asks about: a statement of
        its own, as far as where a use in it ends goes."""
        if self._ours(expr.span):
            self._stmt_end = end_of(expr)
        self._expr(expr)

    def _loop(self, node: ast.ForEach | ast.While) -> tuple[Loc, Loc] | None:
        return (node.span.start, end_of(node.body)) \
            if self._ours(node.span) else None

    def _for_each(self, node: ast.ForEach) -> None:
        """What a `foreach` walks is worked out once, before the loop, and kept
        for all of it; what a `while` binds is worked out on every turn."""
        loop = self._loop(node)
        again = node.keyword == "while"
        if again:
            self._loops.append(loop)
        if self._ours(node.iterable.span):
            self._stmt_end = end_of(node.iterable)
        outer = self._held_until
        if not again and loop is not None:
            self._held_until = loop[1]
        self._expr(node.iterable)
        self._held_until = outer
        if node.type is not None:
            self._expr(node.type)
        if not again:
            self._loops.append(loop)
        self._scopes.append([])
        self._bind(node.name, node.name_span, self._stmt_end)
        for name, span in node.more:
            self._bind(name, span, self._stmt_end)
        self._block(node.body, scoped=False)
        self._pop()
        self._loops.pop()
        if node.alternative is not None:
            self._block(node.alternative)

    def _fields(self, node: ast.Node) -> None:
        """Walk everything *node* holds, it being nothing that binds a name."""
        for one in fields_of(node):
            if one.name not in ("span", "attrs"):
                self._expr(getattr(node, one.name))

    # -- the bindings ----------------------------------------------------------

    def _bind(self, name: str, at: Span, bound_end: Loc) -> None:
        if name == WILDCARD_NAME or not self._ours(at):
            return
        binding = _Binding(name=name, at=at, bound_end=bound_end,
                           loops=len(self._loops))
        self._scopes[-1].append(binding)
        self._live[at.start] = binding

    def _find(self, name: str) -> _Binding | None:
        for scope in reversed(self._scopes):
            for binding in reversed(scope):
                if binding.name == name:
                    return binding
        return None

    def _use(self, name: str, span: Span) -> None:
        binding = self._find(name)
        if binding is None:
            return
        line = span if self._ours(span) else Span(self._stmt_end - 1,
                                                  self._stmt_end - 1)
        why: tuple[object, ...]
        if self._deferred is not None:
            at, why = self._deferred[0], ("deferred", self._deferred[1])
        elif self._held_until != NO_LOC:
            at, why = self._held_until, ("walked", line)
        else:
            at, why = self._stmt_end, ("used", line)
        for loop in self._loops[binding.loops:]:
            if loop is not None:
                if loop[1] >= at:
                    at, why = loop[1], ("loop", loop[0], line)
                break
        if at >= binding.last:
            binding.last, binding.why = at, why
            binding.moved = span.start in self._moves

    def _pop(self) -> None:
        """Leave a scope: what its names hold references to lasts as long as
        they do, and each name's scope is reported."""
        for binding in reversed(self._scopes.pop()):
            del self._live[binding.at.start]
            ends = binding.last if binding.last != NO_LOC else binding.bound_end
            for lent in self._lends.get(binding.at.start, ()):
                lender = self._live.get(lent)
                if lender is not None and ends > lender.last:
                    lender.last, lender.why = ends, ("lent", binding.name)
                    lender.moved = False
            if binding.moved:
                # Moved out by its last use: the move is logged, and after it
                # there is no variable left to end.
                continue
            self._report(binding.name, self._reason(binding),
                         line_after(self._sources, ends))

    def _reason(self, binding: _Binding) -> str:
        match binding.why:
            case ("used", Span() as span):
                return "".join(("last used on line ", self._line(span.start)))
            case ("walked", Span() as span):
                return "".join(("used on line ", self._line(span.start),
                                " in what the loop walks, which it keeps to its end"))
            case ("loop", int() as begins, Span() as span):
                return "".join(("last used on line ", self._line(span.start),
                                ", in the loop from line ", self._line(begins),
                                ", which may come round to it again"))
            case ("deferred", Span() as written):
                return "".join(("last used by what is put off on line ",
                                self._line(written.start),
                                ", when the block is left"))
            case ("lent", str() as holder):
                return "".join(("'", holder, "' holds a reference to it, "
                                "and its scope ends here"))
            case _:
                return "never used"

    def _line(self, loc: Loc) -> str:
        found = self._sources.position(loc)
        return str(found.line) if found is not None else "?"


def line_after(sources: SourceManager, end: Loc) -> Span:
    """The first character that is not a blank of the line after the one the
    character before *end* is on -- or where that line begins, where it has
    none, or the end of the file, where there is no such line."""
    source = sources.file_of(max(end - 1, 0))
    if source is None:
        return Span(end, end)
    offset = max(end - 1 - source.base, 0)
    index = bisect_right(source.line_starts, offset)
    if index >= len(source.line_starts):
        at = len(source.text)
    else:
        at = source.line_starts[index]
        while at < len(source.text) and source.text[at] in " \t":
            at += 1
        if at < len(source.text) and source.text[at] == "\n":
            at = source.line_starts[index]
    return Span(source.base + at, source.base + at)
