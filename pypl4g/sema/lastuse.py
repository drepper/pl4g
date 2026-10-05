"""Where each variable's scope ends: after its last use, not at its block's end.

A name is bound by a `let`, a parameter, a `foreach`, an arm of a `match` or a
lambda's parameter, and is there for the rest of its block -- as far as what may
be *written* goes.  What the program can still *need* ends sooner: after the
last thing that uses it.  That is what a reader of the report log asks about a
variable, so each one gets a `scope-ended` entry on the line after the one its
scope ends on, which the viewer shows just below that line.

The last use is the last on each way through the function, so a variable used in
the arms of an `if` ends in each of them: the walk keeps, for every variable, one
end for each way that has come this far, takes each arm from where the
conditions before it leave things, and puts the arms' ends together after it.
An arm that leaves -- a `return`, a `break`, a `continue` -- ends there what it
used, and adds nothing to what follows.

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

A last use that moves the value out has no entry: the move is logged, and
after it there is no variable left to end.

A macro's expansion is part of the tree and carries the places of the macro's
own text, in another file: a use in it is a use in the statement that invoked
it, and what it binds and the loops it makes are its own business.

This is a walk of the tree and nothing else: it is done once per function,
however many times the function is lowered.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field, fields as fields_of
from typing import Callable

from ..front import ast
from ..front.token import WILDCARD_NAME
from ..source.location import NO_LOC, Loc, Span
from ..source.manager import SourceFile, SourceManager


@dataclass(frozen=True, slots=True)
class _End:
    """Where a variable's scope ends on one way through, and why there."""

    at: Loc
    why: tuple[object, ...]
    #: Whether the use there moves the value out, which the move says.
    moved: bool = False
    #: Where the entry goes, where that is not the line after *at*: the first
    #: statement of an arm it was carried into.
    place: Loc = NO_LOC


@dataclass(slots=True)
class _Binding:
    """A variable while its scope is walked, and how far its uses reach."""

    name: str
    #: Where it is bound, which is how the checker names it too.
    at: Span
    #: One end for each way through that has come this far.
    ends: list[_End] = field(default_factory=list)
    #: The ends on the ways that left early, which nothing after changes.
    final: list[_End] = field(default_factory=list)
    #: The end of the block a deferred use holds it to, if one does: a use
    #: before that is not its last.
    held_to: Loc = NO_LOC
    #: How many uses there have been, to tell whether a loop used it.
    uses: int = 0
    #: The use furthest on in the text, which a loop's end names.
    latest: Span | None = None


#: For every variable there is, its ends and what holds it, as a branch found
#: them -- keyed by the binding's identity.
type _State = dict[int, tuple[_Binding, list[_End], Loc]]


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


def leaves(block: ast.Block) -> bool:
    """Whether every way through *block* leaves it before its end: by a
    `return`, a `break` or a `continue`, or by an `if` or a `match` every arm
    of which does."""
    if not block.stmts:
        return False
    match block.stmts[-1]:
        case ast.ReturnStmt() | ast.Break() | ast.Continue():
            return True
        case ast.ExprStmt(value=ast.If() as chosen):
            return chosen.arms[-1].condition is None \
                and all(leaves(arm.body) for arm in chosen.arms)
        case ast.ExprStmt(value=ast.Match() as chosen):
            return bool(chosen.arms) and all(leaves(arm.body)
                                             for arm in chosen.arms)
    return False


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
        #: Where the statement being walked ends: a use is a use until then.
        self._stmt_end: Loc = NO_LOC
        #: Where a use in what a `foreach` walks counts as being: the loop's end.
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
                self._if(node)
            case ast.Match():
                self._match(node)
            case ast.While():
                self._while(node)
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

    def _fields(self, node: ast.Node) -> None:
        """Walk everything *node* holds, it being nothing that binds a name."""
        for one in fields_of(node):
            if one.name not in ("span", "attrs"):
                self._expr(getattr(node, one.name))

    # -- the ways through ------------------------------------------------------

    def _state(self) -> _State:
        return {id(one): (one, list(one.ends), one.held_to)
                for one in self._live.values()}

    def _restore(self, state: _State) -> None:
        for one, ends, held_to in state.values():
            one.ends, one.held_to = list(ends), held_to

    def _arm_done(self, body: ast.Block, entry: _State, start: _State,
                  ways: list[tuple[_State, _State, Loc]], anchor: Loc) -> None:
        """An arm has been walked from *entry*: what it comes to is one of the
        ways on, or -- where it leaves -- what it used ends in it, and so does
        what the choice's own conditions were the last to use.  What was used
        last before the choice is not said again in an arm that leaves: every
        variable there is would be."""
        if not leaves(body):
            ways.append((entry, self._state(), anchor))
            return
        for key, (one, ends, _) in entry.items():
            before = start[key][1] if key in start else []
            for end in one.ends:
                if end in ends:
                    if end in before or not self._below(anchor, end):
                        continue
                    end = _End(anchor, end.why, end.moved, anchor)
                if end not in one.final:
                    one.final.append(end)

    def _below(self, anchor: Loc, end: _End) -> bool:
        """Whether an arm starting at *anchor* starts below the line *end* is
        on: an arm on that same line has nowhere of its own to say it."""
        if anchor == NO_LOC:
            return False
        here = self._sources.position(anchor)
        there = self._sources.position(max(end.at - 1, 0))
        return here is not None and there is not None and here.line > there.line

    def _anchor(self, body: ast.Block) -> Loc:
        """Where an end carried into an arm goes: its first statement."""
        return body.stmts[0].span.start \
            if body.stmts and self._ours(body.stmts[0].span) else NO_LOC

    def _join(self, start: _State, ways: list[tuple[_State, _State, Loc]]) -> None:
        """Every way on from the arms of one choice, put together.

        An end carried into an arm unchanged is put at the arm's start where it
        is not the same on every way, or where a condition of this choice made
        it: an end shown in one arm is read as the end in that arm, and a reader
        of each of the others has to be told where the variable ends in it.
        """
        for key, (one, before, _) in start.items():
            if not ways:
                one.ends = []
                continue
            outcomes = [outcome[key][1] for _, outcome, _ in ways]
            differ = any(theirs != outcomes[0] for theirs in outcomes)
            ends: list[_End] = []
            held_to = NO_LOC
            for entry, outcome, anchor in ways:
                _, theirs, held = outcome[key]
                held_to = max(held_to, held)
                for end in theirs:
                    if self._below(anchor, end) and end in entry[key][1] \
                            and (differ or end not in before):
                        end = _End(anchor, ("unused-here",) if end.why == ("never",)
                                   else end.why, end.moved, anchor)
                    if end not in ends:
                        ends.append(end)
            one.ends, one.held_to = ends, held_to

    def _if(self, node: ast.If) -> None:
        start = self._state()
        ways: list[tuple[_State, _State, Loc]] = []
        for arm in node.arms:
            # What a condition reads, it reads on the way to its own arm and
            # to every arm after it.
            if arm.condition is not None:
                self._header(arm.condition)
            entry = self._state()
            self._block(arm.body)
            self._arm_done(arm.body, entry, start, ways, self._anchor(arm.body))
            self._restore(entry)
        if node.arms[-1].condition is not None:
            # No arm taken: a way on of its own, which goes on after the `if`.
            after = line_after(self._sources, end_of(node), node.span.start).start \
                if self._ours(node.span) else NO_LOC
            ways.append((self._state(), self._state(), after))
        self._join(start, ways)

    def _match(self, node: ast.Match) -> None:
        start = self._state()
        self._header(node.subject)
        entry = self._state()
        ways: list[tuple[_State, _State, Loc]] = []
        for arm in node.arms:
            self._restore(entry)
            self._scopes.append([])
            pattern = arm.pattern
            if pattern.type is not None:
                self._expr(pattern.type)
            if pattern.name is not None and not pattern.wildcard:
                self._bind(pattern.name, pattern.name_span, self._stmt_end)
            self._block(arm.body, scoped=False)
            self._pop()
            self._arm_done(arm.body, entry, start, ways, self._anchor(arm.body))
        self._restore(entry)
        self._join(start, ways)

    def _uses(self) -> dict[int, int]:
        return {id(one): one.uses for one in self._live.values()}

    def _looped(self, node: ast.ForEach | ast.While, before: dict[int, int]) -> None:
        """A loop is over: what was bound outside it and used in it may be used
        on any turn, so it ends after the loop -- on every way on."""
        if not self._ours(node.span):
            return
        begins, ends = node.span.start, end_of(node.body)
        for one in self._live.values():
            if before.get(id(one), one.uses) != one.uses and one.held_to <= ends:
                one.ends = [_End(ends, ("loop", begins, one.latest))]

    def _while(self, node: ast.While) -> None:
        before = self._uses()
        self._header(node.condition)
        self._block(node.body)
        self._looped(node, before)
        if node.alternative is not None:
            self._block(node.alternative)

    def _for_each(self, node: ast.ForEach) -> None:
        """What a `foreach` walks is worked out once, before the loop, and kept
        for all of it; what a `while` binds is worked out on every turn."""
        again = node.keyword == "while"
        before = self._uses()
        if self._ours(node.iterable.span):
            self._stmt_end = end_of(node.iterable)
        outer = self._held_until
        if not again and self._ours(node.span):
            self._held_until = end_of(node.body)
        self._expr(node.iterable)
        self._held_until = outer
        if node.type is not None:
            self._expr(node.type)
        if not again:
            before = self._uses()
        self._scopes.append([])
        self._bind(node.name, node.name_span, self._stmt_end)
        for name, span in node.more:
            self._bind(name, span, self._stmt_end)
        self._block(node.body, scoped=False)
        self._pop()
        self._looped(node, before)
        if node.alternative is not None:
            self._block(node.alternative)

    # -- the bindings ----------------------------------------------------------

    def _bind(self, name: str, at: Span, bound_end: Loc) -> None:
        if name == WILDCARD_NAME or not self._ours(at):
            return
        binding = _Binding(name=name, at=at, ends=[_End(bound_end, ("never",))])
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
        binding.uses += 1
        if binding.latest is None or line.start > binding.latest.start:
            binding.latest = line
        moved = span.start in self._moves
        if self._deferred is not None:
            at = self._deferred[0]
            binding.ends = [_End(at, ("deferred", self._deferred[1]), moved)]
            binding.held_to = max(binding.held_to, at)
            return
        if self._held_until != NO_LOC:
            # Kept to the loop's end, whatever else in what is walked uses it.
            at, why = self._held_until, ("walked", line)
            if binding.held_to >= at:
                return
            binding.ends = [_End(at, why, moved)]
            binding.held_to = at
            return
        else:
            at, why = self._stmt_end, ("used", line)
        if binding.held_to >= at:
            # A deferred use holds it to the end of a block this is in.
            return
        binding.ends = [_End(at, why, moved)]

    def _pop(self) -> None:
        """Leave a scope: what its names hold references to lasts as long as
        they do, and each name's scope is reported."""
        for binding in reversed(self._scopes.pop()):
            del self._live[binding.at.start]
            ends = binding.ends + [one for one in binding.final
                                   if one not in binding.ends]
            furthest = max((one.at for one in ends), default=NO_LOC)
            for lent in self._lends.get(binding.at.start, ()):
                lender = self._live.get(lent)
                if lender is not None and furthest > max(
                        (one.at for one in lender.ends), default=NO_LOC):
                    lender.ends = [_End(one.at, ("lent", binding.name))
                                   for one in ends]
            told: set[Loc] = set()
            for end in sorted(ends, key=lambda one: one.at):
                # A last use that moves the value out: the move is logged, and
                # after it there is no variable left to end.
                if end.moved:
                    continue
                where = Span(end.place, end.place) if end.place != NO_LOC \
                    else line_after(self._sources, end.at)
                if where.start in told:
                    continue
                told.add(where.start)
                self._report(binding.name, self._reason(end), where)

    def _reason(self, end: _End) -> str:
        match end.why:
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
            case ("unused-here",):
                return "not used on the way through here"
            case _:
                return "never used"

    def _line(self, loc: Loc) -> str:
        found = self._sources.position(loc)
        return str(found.line) if found is not None else "?"


def line_after(sources: SourceManager, end: Loc, column_of: Loc = NO_LOC) -> Span:
    """The first character that is not a blank of the line after the one the
    character before *end* is on -- or where that line begins, where it has
    none, or the end of the file, where there is no such line.  Given
    *column_of*, the column that is in its own line instead, as far as the line
    reaches: what is said about a statement stands at the statement's column."""
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
        if column_of != NO_LOC and sources.file_of(column_of) is source:
            offset = column_of - source.base
            column = offset - source.line_starts[bisect_right(source.line_starts,
                                                              offset) - 1]
            begins = source.line_starts[index]
            ends = source.text.find("\n", begins)
            at = begins + min(column, (len(source.text) if ends < 0 else ends) - begins)
    return Span(source.base + at, source.base + at)
