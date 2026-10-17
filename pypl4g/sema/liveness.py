"""Which names are still to be used, at each statement of a function.

A name is *live* at a statement where some way on from there -- through the
statement itself and whatever follows it -- uses it.  What a name keeps is what
it keeps only while it is live: a reference a name holds is out until the name's
last use and not until its scope ends, which is what lets a second reference to
the same place be taken after the first is done with.  That is the whole of what
this is for, and why it answers by statement: the borrow rules are asked one
statement at a time, so "live at this statement" is exactly the question.

Worked backwards over the tree, which for a language without `goto` is the
control-flow graph already: a block is its statements in reverse, an `if` and a
`match` join what their arms need, a loop is worked to a fixed point -- what is
used on a later turn is live on this one -- and `break`, `continue` and `return`
leave with what is live where they go.  A deferred statement runs when its block
is left, so what it uses is live until then; what a `foreach` walks is held for
the whole loop, the bounds of a range excepted.  A name given a new value is
dead before it: what it held before is not what anything after reads.

A name is the binding it resolves to -- where it is bound, which is how the
checker names a variable too -- so a name shadowed is two names.

Three answers for each statement, by its identity:

- `live_in`, what is live as it begins;
- `live_out`, what is live once it is done;
- `ends`, the names that stop being live as this statement begins, on some way
  through that reaches it with them live -- the statement is the first one after
  their last use there.  Worked forward over the same answers, joining what the
  arms of a choice come to, so that a name used only in one arm ends where each
  other arm begins and, past an `if` with no `else`, after it.  That is where the
  checker stops counting a borrow the name keeps, since it asks `live_in` of the
  statement, and so where the report log says the borrow ended.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as fields_of

from ..front import ast
from ..front.token import WILDCARD_NAME
from ..source.location import Span


@dataclass(slots=True)
class _Loop:
    """What a `break` and a `continue` inside a loop leave with."""

    label: str | None
    after: frozenset[int]
    head: frozenset[int]


@dataclass(slots=True)
class Liveness:
    """The liveness of every binding of the functions handed to it."""

    live_in: dict[int, frozenset[int]] = field(default_factory=dict)
    live_out: dict[int, frozenset[int]] = field(default_factory=dict)
    ends: dict[int, frozenset[int]] = field(default_factory=dict)
    #: What each name written resolves to, by the name's identity.
    _refs: dict[int, int] = field(default_factory=dict)
    #: What each statement or turn binds, by the node's identity.
    _defs: dict[int, frozenset[int]] = field(default_factory=dict)
    _uses: dict[int, frozenset[int]] = field(default_factory=dict)
    _scopes: list[dict[str, int]] = field(default_factory=list)
    _loops: list[_Loop] = field(default_factory=list)
    #: What the deferred statements of each block being walked use, innermost
    #: last: live until the block is left, which a `return` does too.
    _deferred: list[frozenset[int]] = field(default_factory=list)

    # -- entry points ------------------------------------------------------------

    def function(self, node: ast.FuncDef) -> None:
        """Work out the liveness of *node*'s bindings."""
        if node.body is None or node.is_macro:
            return
        self._scopes = [{}]
        for param in node.params:
            self._bind(param.name, param.span)
        self._resolve(node.body, scoped=False)
        self._scopes = []
        self._block(node.body, frozenset())
        self._forward(node.body, frozenset())

    def lambda_(self, node: ast.Lambda) -> None:
        """And of a lambda's, whose body is a function of its own."""
        self._scopes = [{}]
        for param in node.params:
            self._bind(param.name, param.span)
        self._resolve(node.body, scoped=False)
        self._scopes = []
        self._block(node.body, frozenset())
        self._forward(node.body, frozenset())

    # -- what a name is ----------------------------------------------------------

    def _bind(self, name: str, at: Span) -> int | None:
        if name == WILDCARD_NAME or not at.is_valid:
            return None
        self._scopes[-1][name] = at.start
        return at.start

    def _find(self, name: str) -> int | None:
        for scope in reversed(self._scopes):
            found = scope.get(name)
            if found is not None:
                return found
        return None

    def _use(self, node: object, name: str) -> None:
        found = self._find(name)
        if found is not None:
            self._refs[id(node)] = found

    def _resolve(self, node: object, scoped: bool = True) -> None:
        """Walk forward, saying what each name resolves to and what each
        statement binds."""
        match node:
            case ast.Block():
                if scoped:
                    self._scopes.append({})
                for one in node.stmts:
                    self._resolve(one)
                if scoped:
                    self._scopes.pop()
            case ast.VarDef():
                self._resolve(node.value)
                self._resolve(node.type)
                if node.made_in is not None:
                    self._use(node, node.made_in[0])
                bound = [self._bind(node.name, node.name_span),
                         *(self._bind(name, span) for name, span in node.more)]
                self._defs[id(node)] = frozenset(one for one in bound if one is not None)
            case ast.AssignStmt():
                self._resolve(node.value)
                written = [self._find(name) for name in (node.name,
                                                          *(n for n, _ in node.more))]
                self._defs[id(node)] = frozenset(one for one in written if one is not None)
            case ast.ForEach():
                self._resolve(node.iterable)
                self._resolve(node.type)
                self._scopes.append({})
                bound = [self._bind(node.name, node.name_span),
                         *(self._bind(name, span) for name, span in node.more)]
                self._defs[id(node)] = frozenset(one for one in bound if one is not None)
                self._resolve(node.body, scoped=False)
                self._scopes.pop()
                self._resolve(node.alternative)
            case ast.Match():
                self._resolve(node.subject)
                for arm in node.arms:
                    self._scopes.append({})
                    self._resolve(arm.pattern.type)
                    bound = None
                    if arm.pattern.name is not None and not arm.pattern.wildcard:
                        bound = self._bind(arm.pattern.name, arm.pattern.name_span)
                    self._defs[id(arm)] = frozenset(() if bound is None else (bound,))
                    self._resolve(arm.body, scoped=False)
                    self._scopes.pop()
            case ast.Lambda():
                for capture in node.captures:
                    self._use(capture, capture.name)
                # Its body is a function of its own, worked out when it is
                # lowered; what it uses from here is what it brings in.
                self._resolve_free(node.body)
            case ast.NameRef() | ast.Capture():
                self._use(node, node.name)
            case ast.Node():
                for one in fields_of(node):
                    if one.name not in ("span", "attrs"):
                        self._resolve(getattr(node, one.name))
            case tuple() | list():
                if len(node) == 2 and isinstance(node[0], str) \
                        and isinstance(node[1], Span):
                    # An arena named where something is made in it.
                    self._use(node, node[0])
                else:
                    for one in node:
                        self._resolve(one)

    def _resolve_free(self, node: object) -> None:
        """Resolve what a lambda's body names of what is around it, binding
        nothing of the lambda's own over it -- a name it binds that is also
        around it is taken for the outside one, which only keeps it live
        longer."""
        match node:
            case ast.NameRef() | ast.Capture():
                self._use(node, node.name)
            case ast.Node():
                for one in fields_of(node):
                    if one.name not in ("span", "attrs"):
                        self._resolve_free(getattr(node, one.name))
            case tuple() | list():
                for one in node:
                    self._resolve_free(one)

    # -- what a piece of the tree uses ---------------------------------------------

    def _used(self, node: object) -> frozenset[int]:
        """Every binding named anywhere in *node*."""
        if isinstance(node, ast.Node):
            known = self._uses.get(id(node))
            if known is not None:
                return known
        found: set[int] = set()
        self._gather(node, found, deep=True)
        result = frozenset(found)
        if isinstance(node, ast.Node):
            self._uses[id(node)] = result
        return result

    def _direct(self, node: object) -> frozenset[int]:
        """What a statement names itself, leaving out the blocks inside it --
        an arm, a loop's body -- whose statements answer for themselves."""
        found: set[int] = set()
        self._gather(node, found, deep=False)
        return frozenset(found)

    def _gather(self, node: object, into: set[int], deep: bool) -> None:
        known = self._refs.get(id(node))
        if known is not None:
            into.add(known)
        match node:
            case ast.Block() if not deep:
                return
            case ast.Lambda():
                # Used where the lambda is, whatever its body does with it.
                self._gather_all(node, into)
            case ast.Node():
                for one in fields_of(node):
                    if one.name not in ("span", "attrs"):
                        self._gather(getattr(node, one.name), into, deep)
            case tuple() | list():
                for one in node:
                    self._gather(one, into, deep)

    def _gather_all(self, node: object, into: set[int]) -> None:
        known = self._refs.get(id(node))
        if known is not None:
            into.add(known)
        if isinstance(node, ast.Node):
            for one in fields_of(node):
                if one.name not in ("span", "attrs"):
                    self._gather_all(getattr(node, one.name), into)
        elif isinstance(node, (tuple, list)):
            for one in node:
                self._gather_all(one, into)

    # -- backwards -----------------------------------------------------------------

    def _block(self, block: ast.Block, out: frozenset[int]) -> frozenset[int]:
        """What is live as *block* begins, given what is live once it is left."""
        deferred = frozenset().union(*(self._used(one.stmt) for one in block.stmts
                                       if isinstance(one, ast.Defer)))
        self._deferred.append(deferred)
        try:
            live = out | deferred
            for stmt in reversed(block.stmts):
                after = live
                live = self._stmt(stmt, after)
                self.live_out[id(stmt)] = after
                self.live_in[id(stmt)] = live
            return live
        finally:
            self._deferred.pop()

    # -- forwards: where each name stops being live, way by way ------------------

    def _forward(self, block: ast.Block, state: frozenset[int]) -> frozenset[int]:
        """Walk *block* with *state*, the names live on the way into it, and
        note at each statement which of them stop being live there; answer what
        is live on the way out of it, less what it bound."""
        bound: set[int] = set()
        for stmt in block.stmts:
            live = self.live_in.get(id(stmt), frozenset())
            gone = state - live
            if gone:
                self.ends[id(stmt)] = self.ends.get(id(stmt), frozenset()) | gone
            state = state & live
            state = self._through(stmt, state)
            made = self._defs.get(id(stmt), frozenset()) if isinstance(
                stmt, ast.VarDef) else frozenset()
            bound |= made
            state = state | made
        return state - bound

    def _through(self, node: object, state: frozenset[int]) -> frozenset[int]:
        """What is live on the way out of one statement, from what was on the way
        in -- walking into the blocks inside it, joining the ways of a choice."""
        match node:
            case ast.ReturnStmt() | ast.Break() | ast.Continue():
                if isinstance(node, ast.ReturnStmt) and node.value is not None:
                    self._through(node.value, state)
                return frozenset()
            case ast.VarDef() | ast.AssignStmt() | ast.ExprStmt():
                return self._through(node.value, state)
            case ast.If():
                ways: list[frozenset[int]] = []
                for arm in node.arms:
                    ways.append(self._forward(arm.body, state))
                if node.arms[-1].condition is not None:
                    ways.append(state)
                return frozenset().union(*ways)
            case ast.Match():
                ways = [self._forward(arm.body, state) for arm in node.arms]
                return frozenset().union(*ways)
            case ast.While() | ast.ForEach():
                self._forward(node.body, state)
                if node.alternative is not None:
                    self._forward(node.alternative, state)
                # A loop may run no turns, and what is live through it is live
                # until it is done: what comes after asks.
                return state
            case ast.Scoped():
                return self._forward(node.body, state)
        return state

    def _leaving(self) -> frozenset[int]:
        """What is live where a `return` goes: what every block it leaves put
        off."""
        return frozenset().union(*self._deferred) if self._deferred else frozenset()

    def _stmt(self, stmt: ast.Stmt, out: frozenset[int]) -> frozenset[int]:
        match stmt:
            case ast.VarDef():
                kept = out - self._defs.get(id(stmt), frozenset())
                return self._expr(stmt.value, kept) | self._used(stmt.type) \
                    | frozenset(one for one in (self._refs.get(id(stmt)),)
                                if one is not None)
            case ast.AssignStmt():
                kept = out - self._defs.get(id(stmt), frozenset())
                return self._expr(stmt.value, kept)
            case ast.ReturnStmt():
                return self._expr(stmt.value, self._leaving()) \
                    if stmt.value is not None else self._leaving()
            case ast.Break():
                loop = self._loop_for(stmt.label)
                after = loop.after if loop is not None else out
                return after | self._used(stmt.value)
            case ast.Continue():
                loop = self._loop_for(stmt.label)
                return loop.head if loop is not None else out
            case ast.Defer():
                # What it uses is live from here to where the block is left, which
                # the block already said.
                return out
            case ast.ExprStmt():
                return self._expr(stmt.value, out)
        return out | self._used(stmt)

    def _loop_for(self, label: ast.Label | None) -> _Loop | None:
        if label is None:
            return self._loops[-1] if self._loops else None
        for loop in reversed(self._loops):
            if loop.label == label.name:
                return loop
        return None

    def _expr(self, expr: object, out: frozenset[int]) -> frozenset[int]:
        """What is live before *expr* is worked out, given what is live after."""
        match expr:
            case ast.If():
                live = out
                ending = expr.arms[-1].condition is not None
                following = out if ending else None
                for arm in reversed(expr.arms):
                    body = self._block(arm.body, out)
                    if arm.condition is None:
                        following = body
                        continue
                    assert following is not None
                    following = self._used(arm.condition) | body | following
                live = following if following is not None else out
                return live
            case ast.Match():
                live = frozenset()
                for arm in expr.arms:
                    live = live | (self._block(arm.body, out)
                                   - self._defs.get(id(arm), frozenset())) \
                        | self._used(arm.pattern.type)
                return self._used(expr.subject) | live
            case ast.While():
                return self._loop(expr, out, self._used(expr.condition), frozenset())
            case ast.ForEach():
                again = expr.keyword == "while"
                held = self._held(expr.iterable)
                if again:
                    return self._loop(expr, out, self._used(expr.iterable),
                                      self._defs.get(id(expr), frozenset()))
                return self._used(expr.iterable) | self._loop(
                    expr, out, held, self._defs.get(id(expr), frozenset()))
            case ast.Scoped():
                return self._block(expr.body, out)
            case None:
                return out
            case ast.Node():
                # Anything else: what it names, and whatever is inside it that
                # holds statements of its own -- an `if` written as an operand --
                # worked out so that they have answers too.
                live = out | self._used(expr)
                self._nested(expr, out)
                return live
        return out

    def _nested(self, node: object, out: frozenset[int]) -> None:
        """Give the statements inside an expression their answers."""
        match node:
            case ast.If() | ast.Match() | ast.While() | ast.ForEach() | ast.Scoped():
                self._expr(node, out)
            case ast.Lambda():
                return
            case ast.Node():
                for one in fields_of(node):
                    if one.name not in ("span", "attrs"):
                        self._nested(getattr(node, one.name), out)
            case tuple() | list():
                for one in node:
                    self._nested(one, out)

    def _held(self, iterable: object) -> frozenset[int]:
        """What a `foreach` keeps for the whole loop: what it walks, but not the
        bounds of a range, which are worked out once."""
        if isinstance(iterable, ast.Range):
            return frozenset()
        found: set[int] = set()

        def walk(node: object) -> None:
            known = self._refs.get(id(node))
            if known is not None:
                found.add(known)
            if isinstance(node, ast.Range):
                return
            if isinstance(node, ast.Node):
                for one in fields_of(node):
                    if one.name not in ("span", "attrs"):
                        walk(getattr(node, one.name))
            elif isinstance(node, (tuple, list)):
                for one in node:
                    walk(one)

        walk(iterable)
        return frozenset(found)

    def _loop(self, node: ast.While | ast.ForEach, out: frozenset[int],
              asked: frozenset[int], bound: frozenset[int]) -> frozenset[int]:
        """What is live as a loop begins: worked to a fixed point, since what a
        later turn uses is live on this one."""
        ran_out = self._block(node.alternative, out) \
            if node.alternative is not None else out
        label = node.label.name if node.label is not None else None
        live = asked | ran_out
        while True:
            self._loops.append(_Loop(label=label, after=out, head=live))
            try:
                body = self._block(node.body, live) - bound
            finally:
                self._loops.pop()
            again = asked | body | ran_out
            if again <= live:
                return live
            live = live | again
