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

Then forward, over the same answers, the *values* a name holds: a value is the
binding and the statement that gave it -- its `let`, an assignment, a turn of a
loop, `0` for a parameter -- and a value *reaches* a statement where some way
there gives the name that value last.  A reference a name keeps is kept by one
value of it, so it is out while that value reaches and the name is live: given
another value, the name keeps the new value's references and not the old one's,
which is why the old ones end there -- on that way through and no other.

The answers, by the identity of a statement, a block, or a way out:

- `live_in` and `live_out`, the bindings live as a statement begins and once it
  is done;
- `reach_in`, the values that reach a statement;
- `ends`, the values that stop counting as a statement begins, each with why:
  `"unused"` where the name is not used again on that way, `"given"` where the
  statement gives the name another value;
- `ends_after`, the values a statement gives another value to after using them
  in it, which stop counting once it is done;
- `leave_ends`, those that stop counting where a `return`, `break` or `continue`
  leaves -- every one, or what the loop's turn bound;
- `exit_ends`, those bound in a block that are still counted where it ends.

Every one of them is where the checker stops counting a borrow the value keeps,
since it asks `live_in` and `reach_in` of the statement, and so where the report
log says the borrow ended.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..front import ast
from ..front.ast import fields_of
from ..front.token import WILDCARD_NAME
from ..source.location import Span


#: A value a name holds: the binding, and the identity of the statement that gave
#: it -- `0` for a parameter, which the call gave.
type Value = tuple[int, int]


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
    reach_in: dict[int, frozenset[Value]] = field(default_factory=dict)
    ends: dict[int, dict[Value, str]] = field(default_factory=dict)
    ends_after: dict[int, frozenset[Value]] = field(default_factory=dict)
    leave_ends: dict[int, frozenset[Value]] = field(default_factory=dict)
    exit_ends: dict[int, frozenset[Value]] = field(default_factory=dict)
    #: Whether the forward walk writes down what it finds: a loop is walked to
    #: a fixed point first, and only the last walk is the one that counts.
    _noting: bool = True
    #: The bindings each loop being walked forward binds, innermost last, which
    #: a `break` or a `continue` leaves.
    _turns: list[set[int]] = field(default_factory=list)
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
        given = frozenset((param.span.start, 0) for param in node.params
                          if param.span.is_valid)
        self._forward(node.body, given, frozenset())

    def lambda_(self, node: ast.Lambda) -> None:
        """And of a lambda's, whose body is a function of its own."""
        self._scopes = [{}]
        for param in node.params:
            self._bind(param.name, param.span)
        self._resolve(node.body, scoped=False)
        self._scopes = []
        self._block(node.body, frozenset())
        given = frozenset((param.span.start, 0) for param in node.params
                          if param.span.is_valid)
        self._forward(node.body, given, frozenset())

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

    # -- forwards: the values a name holds, and where each stops counting --------

    def _forward(self, block: ast.Block, reach: frozenset[Value],
                 active: frozenset[Value]) -> tuple[frozenset[Value], frozenset[Value]]:
        """Walk *block* forward from the values reaching it and those counting
        -- reaching, and their name live -- and answer both on the way out, less
        what the block bound, which is gone with its scope."""
        bound: set[int] = set()
        for stmt in block.stmts:
            reach, active = self._enter(stmt, reach, active)
            reach, active = self._through(stmt, reach, active)
            if isinstance(stmt, ast.VarDef):
                bound |= self._defs.get(id(stmt), frozenset())
        if self._noting and bound:
            kept = frozenset(one for one in active if one[0] in bound)
            if kept:
                self.exit_ends[id(block)] = self.exit_ends.get(
                    id(block), frozenset()) | kept
        return (frozenset(one for one in reach if one[0] not in bound),
                frozenset(one for one in active if one[0] not in bound))

    def _enter(self, stmt: ast.Stmt, reach: frozenset[Value],
               active: frozenset[Value]) -> tuple[frozenset[Value], frozenset[Value]]:
        """What counts as *stmt* begins: what reaches it whose name is live."""
        live = self.live_in.get(id(stmt), frozenset())
        now = frozenset(one for one in reach if one[0] in live)
        if self._noting:
            self.reach_in[id(stmt)] = self.reach_in.get(id(stmt), frozenset()) | reach
            gone = active - now
            if gone:
                given = self._defs.get(id(stmt), frozenset()) \
                    if isinstance(stmt, (ast.VarDef, ast.AssignStmt)) else frozenset()
                found = self.ends.setdefault(id(stmt), {})
                for one in gone:
                    found.setdefault(one, "given" if one[0] in given else "unused")
        return reach, now

    def _gives(self, stmt: ast.Stmt, reach: frozenset[Value],
               active: frozenset[Value]) -> tuple[frozenset[Value], frozenset[Value]]:
        """The values once *stmt* has given its names new ones: the old ones no
        longer reach, and one still counting -- used in the statement itself, as
        `moving ← f(moving)` does -- stops counting once it is done."""
        written = self._defs.get(id(stmt), frozenset())
        if not written:
            return reach, active
        old = frozenset(one for one in active if one[0] in written)
        if old and self._noting and isinstance(stmt, ast.AssignStmt):
            self.ends_after[id(stmt)] = self.ends_after.get(id(stmt), frozenset()) | old
        made = frozenset((one, id(stmt)) for one in written)
        return (frozenset(one for one in reach if one[0] not in written) | made,
                frozenset(one for one in active if one[0] not in written) | made)

    def _through(self, node: object, reach: frozenset[Value],
                 active: frozenset[Value]) -> tuple[frozenset[Value], frozenset[Value]]:
        """The values on the way out of one statement, from those on the way in --
        walking into the blocks inside it, joining the ways of a choice."""
        match node:
            case ast.ReturnStmt():
                if node.value is not None:
                    self._through(node.value, reach, active)
                if self._noting and active:
                    self.leave_ends[id(node)] = active
                return frozenset(), frozenset()
            case ast.Break() | ast.Continue():
                if self._noting and self._turns:
                    left = frozenset(one for one in active if one[0] in self._turns[-1])
                    if left:
                        self.leave_ends[id(node)] = left
                return frozenset(), frozenset()
            case ast.VarDef() | ast.AssignStmt():
                reach, active = self._through(node.value, reach, active)
                return self._gives(node, reach, active)
            case ast.ExprStmt():
                return self._through(node.value, reach, active)
            case ast.If():
                ways: list[tuple[frozenset[Value], frozenset[Value]]] = [
                    self._forward(arm.body, reach, active) for arm in node.arms]
                if node.arms[-1].condition is not None:
                    ways.append((reach, active))
                return (frozenset().union(*(one for one, _ in ways)),
                        frozenset().union(*(one for _, one in ways)))
            case ast.Match():
                ways = []
                for arm in node.arms:
                    turn = frozenset((one, id(arm))
                                     for one in self._defs.get(id(arm), frozenset()))
                    out, counting = self._forward(arm.body, reach | turn, active | turn)
                    ways.append((out - turn, counting - turn))
                return (frozenset().union(*(one for one, _ in ways)),
                        frozenset().union(*(one for _, one in ways)))
            case ast.While() | ast.ForEach():
                return self._loop_forward(node, reach, active)
            case ast.Scoped():
                return self._forward(node.body, reach, active)
        return reach, active

    def _loop_forward(self, node: ast.While | ast.ForEach, reach: frozenset[Value],
                      active: frozenset[Value]
                      ) -> tuple[frozenset[Value], frozenset[Value]]:
        """A loop forward: to a fixed point first, since what a turn gives a name
        reaches the next turn, and then once more writing down what it finds."""
        turn_names = set(self._defs.get(id(node), frozenset())) \
            if isinstance(node, ast.ForEach) else set()
        turn = frozenset((one, id(node)) for one in turn_names)
        head_reach, head_active = reach, active
        noting, self._noting = self._noting, False
        try:
            while True:
                self._turns.append(turn_names)
                try:
                    out, counting = self._forward(node.body, head_reach | turn,
                                                  head_active | turn)
                finally:
                    self._turns.pop()
                more = (head_reach | (out - turn), head_active | (counting - turn))
                if more == (head_reach, head_active):
                    break
                head_reach, head_active = more
        finally:
            self._noting = noting
        self._turns.append(turn_names)
        try:
            self._forward(node.body, head_reach | turn, head_active | turn)
        finally:
            self._turns.pop()
        if node.alternative is not None:
            self._forward(node.alternative, head_reach, head_active)
        return head_reach, head_active

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
