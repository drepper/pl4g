"""Expanding macros, which happens between parsing and every check.

A macro is written where a function is called and is not a function call: what
stands between its marks is handed over as it is written and not as what it
evaluates to.  That is the whole of what a macro is for -- a function receives a
number and a macro receives the multiplication that would have produced it.

**Expansion runs after parsing and before any check.**  What the checker, the type
rules and the code generator see is a program with no macro left in it, so a macro
cannot produce a program that would not otherwise be legal, and every diagnostic
the language gives is given about what the macro wrote.

It cannot run earlier.  A macro is handed the parse tree of its arguments and there
is none before parsing -- which is why the C preprocessor works on characters, and
why it has to: C's grammar is not context-free, and `a * b;` needs to know whether
`a` is a type.  This grammar is context-free and an invocation is marked, so the
text around a macro can be read without knowing what the macro is.

**An invocation is expanded before what is written inside it**, so a macro is handed
its argument as the caller wrote it, including any invocation nested in it.  What
comes out is expanded in turn, so a macro may write another one; a macro that writes
something reaching itself again is stopped after `ROUNDS` rewrites.

**Every piece keeps the position it was written at.**  That falls out of substituting
trees rather than text: a tree that came from the caller carries the caller's spans
and one that came from the template carries the macro's, because those are the spans
the parser gave them.  An error in an expanded program therefore names the place the
offending text was actually written, which is either the invocation or the macro, and
those are the only two answers that can be right.

**Hygiene.**  A name a template binds is renamed to something no source file can
spell: the name with a `#` and a number after it.  `#` is an operator glyph, so no
identifier can hold one and the renamed name collides with nothing a program writes.
Without it a macro that needs a temporary would shadow a caller's variable of the
same name, and the standard example -- a swap through a temporary called `t`, invoked
on a caller's `t` -- would quietly do nothing.
"""

from __future__ import annotations

from dataclasses import fields as fields_of, replace
from typing import Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..source.location import Span
from . import ast

#: How many rewrites one invocation may take before it is refused.  A macro that
#: writes an invocation of itself is the shape this catches, and the number is a
#: limit rather than an analysis: whether expansion ends is the halting problem, and
#: a program that wanted more than this many rounds would be one nobody could read.
ROUNDS: Final[int] = 64

#: What a renamed name carries.  The glyph is an operator, so a program cannot write
#: it in a name and cannot collide with one of these however it tries.
HYGIENE_MARK: Final[str] = "#"


def expand(units: Sequence[ast.SourceUnit],
           diags: DiagEngine) -> list[ast.SourceUnit]:
    """Expand every macro in *units*, answering the units with none left.

    The macros of every unit are collected first, so that a file may invoke one
    written below the invocation and -- since the units of one compilation share a
    namespace -- one written in another of them.
    """
    return _Expander(units, diags).run()


class _Expander:
    """One expansion of one compilation."""

    def __init__(self, units: Sequence[ast.SourceUnit],
                 diags: DiagEngine) -> None:
        self._units = units
        self._diags = diags
        #: The macros, by name.  Not beside the program's own names: a macro and a
        #: function are not in one namespace, so a name may be both, and then the
        #: parentheses call the function and the marks invoke the macro.
        self._macros: dict[str, ast.MacroDef] = {}
        #: How many names hygiene has renamed, which is what makes each new one
        #: different from the last.
        self._renamed = 0
        #: How many rewrites deep this is.  On the expander and not passed along,
        #: because what a macro wrote is walked whole and an invocation inside it is
        #: reached by the ordinary walk rather than by a call from here -- so a
        #: number handed down would start again at nothing every time.
        self._deep = 0

    def run(self) -> list[ast.SourceUnit]:
        """Collect the macros, then rewrite every unit without them."""
        for unit in self._units:
            for item in unit.items:
                if isinstance(item, ast.MacroDef):
                    self._collect(item)
        answer: list[ast.SourceUnit] = []
        for unit in self._units:
            items = tuple(self._walked(item) for item in unit.items
                          if not isinstance(item, ast.MacroDef))
            answer.append(replace(unit, items=items))
        return answer

    def _collect(self, node: ast.MacroDef) -> None:
        """Write a macro down, reporting a second of one name."""
        previous = self._macros.get(node.name)
        if previous is not None:
            self._diags.emit(D.LANG_FILESTRUCT_DUPLICATE_DEFINITION,
                             node.name_span, name=node.name).note(
                D.LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous.name_span,
                name=node.name)
            return
        for rule in node.rules:
            self._check_rule(node, rule)
        self._macros[node.name] = node

    def _check_rule(self, node: ast.MacroDef, rule: ast.Rule) -> None:
        """Report what is wrong with a rule whatever it is invoked with.

        A template naming a hole the pattern does not have can never be filled, so
        it is reported where it is written rather than at whatever invocation first
        reaches it -- which is the one thing about a rule that does not wait for a
        caller.
        """
        have = set(_holes_in(rule.pattern))
        for name in _holes_in(rule.template):
            if name not in have:
                self._diags.emit(D.LANG_MACRO_HOLE_UNKNOWN, rule.template.span,
                                 name=name, macro=node.name)

    # -- rewriting -------------------------------------------------------------

    def _walked(self, node: object) -> object:
        """*node* with every invocation below it replaced by what it expands to."""
        if isinstance(node, ast.Invoke):
            return self._expanded(node, as_statement=False)
        if isinstance(node, ast.ExprStmt) and isinstance(node.value, ast.Invoke):
            # A line of its own, which is where a macro that writes statements may
            # stand.  What it writes replaces the line.
            found = self._expanded(node.value, as_statement=True)
            if isinstance(found, ast.Block):
                return found
            assert isinstance(found, ast.Expr)
            return replace(node, value=found)
        if isinstance(node, ast.Hole):
            self._diags.emit(D.LANG_MACRO_HOLE_OUTSIDE, node.span)
            return node
        if isinstance(node, ast.Block):
            return replace(node, stmts=tuple(self._statements(node.stmts)))
        if isinstance(node, ast.Node):
            return replace(node, **{one.name: self._walked(getattr(node, one.name))
                                    for one in fields_of(node)})
        if isinstance(node, tuple):
            return tuple(self._walked(one) for one in node)
        return node

    def _statements(self, stmts: Sequence[ast.Stmt]) -> list[ast.Stmt]:
        """Every statement rewritten, with a block a macro wrote spliced in.

        A macro that writes statements writes several, and what it wrote stands
        where the line stood -- so the statements go into the run around them
        rather than into a block of their own, which would be a scope the macro did
        not ask for and would hide what it bound from the lines after it.
        """
        found: list[ast.Stmt] = []
        for one in stmts:
            written = self._walked(one)
            if isinstance(written, ast.Block):
                found.extend(written.stmts)
            else:
                assert isinstance(written, ast.Stmt)
                found.append(written)
        return found

    def _expanded(self, node: ast.Invoke,
                  as_statement: bool) -> ast.Expr | ast.Block:
        """What one invocation comes to, expanded as far as it goes."""
        if self._deep >= ROUNDS:
            self._diags.emit(D.LANG_MACRO_TOO_DEEP, node.span, name=node.name)
            return ast.NameRef(span=node.span, name=node.name)
        macro = self._macros.get(node.name)
        if macro is None:
            self._diags.emit(D.LANG_MACRO_UNKNOWN, node.name_span, name=node.name)
            return ast.NameRef(span=node.span, name=node.name)
        for rule in macro.rules:
            bound: dict[str, ast.Expr] = {}
            if not self._matches(rule.pattern, node.arguments, bound):
                continue
            written = self._filled(rule.template, bound)
            if isinstance(written, ast.Block) and not as_statement:
                self._diags.emit(D.LANG_MACRO_WRITES_STATEMENTS, node.span,
                                 name=node.name)
                return ast.NameRef(span=node.span, name=node.name)
            # What came out is expanded in turn, so a macro may write another.
            self._deep += 1
            try:
                again = self._walked(written)
            finally:
                self._deep -= 1
            assert isinstance(again, (ast.Expr, ast.Block))
            return again
        self._diags.emit(D.LANG_MACRO_NO_RULE, node.span, name=node.name)
        return ast.NameRef(span=node.span, name=node.name)

    # -- matching --------------------------------------------------------------

    def _matches(self, pattern: ast.Quote, given: ast.Quote,
                 bound: dict[str, ast.Expr]) -> bool:
        """Whether the arguments look like the pattern, remembering the holes.

        A pattern holds one expression per argument, so a rule for two arguments
        does not match an invocation with one: how many were written is part of the
        shape and not something a rule may be silent about.
        """
        if pattern.body is not None or given.body is not None:
            # Nothing is written between the marks of an invocation but
            # expressions, and a pattern is read the same way; a statement on
            # either side is a rule that can never match.
            return False
        if len(pattern.pieces) != len(given.pieces):
            return False
        return all(self._alike(one, other, bound)
                   for one, other in zip(pattern.pieces, given.pieces))

    def _alike(self, pattern: object, given: object,
               bound: dict[str, ast.Expr]) -> bool:
        """Whether one piece of a pattern matches one piece of what was written.

        Structural: a name, a literal or an operator matches only itself, and a
        hole matches anything and remembers what it matched.  A hole written twice
        in one pattern matches only where the two are written alike, which is how a
        rule says its arguments agree.
        """
        if isinstance(pattern, ast.Hole):
            earlier = bound.get(pattern.name)
            if earlier is not None:
                return _written_alike(earlier, given)
            assert isinstance(given, ast.Expr)
            bound[pattern.name] = given
            return True
        return _written_alike(pattern, given)

    # -- filling ---------------------------------------------------------------

    def _filled(self, template: ast.Quote,
                bound: dict[str, ast.Expr]) -> ast.Expr | ast.Block:
        """The template with its holes filled and what it binds renamed."""
        renamed = {name: self._fresh(name)
                   for name in _bound_in(template)}
        if template.body is not None:
            found = self._put(template.body, bound, renamed)
            assert isinstance(found, ast.Block)
            return found
        if len(template.pieces) != 1:
            # A template holds one expression, since an invocation stands where one
            # value does.  A pattern may hold any number, that being how many
            # arguments it matches.
            self._diags.emit(D.LANG_MACRO_TEMPLATE_NOT_ONE, template.span,
                             count=str(len(template.pieces)))
            return ast.NameRef(span=template.span, name="")
        found = self._put(template.pieces[0], bound, renamed)
        assert isinstance(found, ast.Expr)
        return found

    def _fresh(self, name: str) -> str:
        """A name like *name* that no source file can spell."""
        self._renamed += 1
        return "".join((name, HYGIENE_MARK, str(self._renamed)))

    def _put(self, node: object, bound: dict[str, ast.Expr],
             renamed: dict[str, str]) -> object:
        """*node* with holes filled and the names the template binds renamed.

        A hole is replaced by the tree it matched, which carries the caller's spans;
        everything else keeps the template's.  That is the whole of how a diagnostic
        about an expanded program points at the right file.
        """
        if isinstance(node, ast.Hole):
            found = bound.get(node.name)
            # A hole the pattern does not have was reported where the rule is
            # written; here it stands for itself so that one mistake is one message.
            return node if found is None else found
        if isinstance(node, ast.NameRef) and node.name in renamed:
            return replace(node, name=renamed[node.name])
        if isinstance(node, ast.VarDef) and node.name in renamed:
            node = replace(node, name=renamed[node.name])
        if isinstance(node, ast.AssignStmt) and node.name in renamed:
            node = replace(node, name=renamed[node.name])
        if isinstance(node, ast.HoleAssign):
            # Now that the target is filled, which assignment this is can be said.
            # It is the parser's own list of what may be written on the left, asked
            # of a tree rather than of what was written.
            filled = self._put(node.target, bound, renamed)
            assert isinstance(filled, ast.Expr)
            found = _assignment(filled, self._put(node.value, bound, renamed),
                                node.span)
            if found is None:
                # The statement goes, so that the hole in it is not then reported as
                # a hole standing where no macro reads one: one mistake, one message.
                self._diags.emit(D.LANG_MACRO_HOLE_NOT_A_PLACE, node.target.span)
                return ast.EmptyStmt(span=node.span)
            return found
        if isinstance(node, ast.Node):
            return replace(node, **{one.name: self._put(getattr(node, one.name),
                                                        bound, renamed)
                                    for one in fields_of(node)})
        if isinstance(node, tuple):
            return tuple(self._put(one, bound, renamed) for one in node)
        return node


def _assignment(target: ast.Expr, value: object,
                span: Span) -> ast.Stmt | None:
    """The assignment that writes *value* to *target*, or nothing where none does.

    The same list the parser has of what may stand on the left of an arrow, asked
    of a tree: a hole is filled with whatever the caller wrote, so what kind of
    place it is, is known here and not before.
    """
    assert isinstance(value, ast.Expr)
    if isinstance(target, ast.NameRef):
        return ast.AssignStmt(span=span, name=target.name,
                              name_span=target.span, value=value)
    if isinstance(target, ast.Index):
        return ast.EntryAssign(span=span, base=target.base, key=target.key,
                               value=value)
    if isinstance(target, ast.Element):
        return ast.ElementAssign(span=span, base=target.base,
                                 indices=target.indices, value=value)
    if isinstance(target, ast.Deref):
        return ast.DerefAssign(span=span, target=target.operand, value=value)
    if isinstance(target, ast.Member):
        return ast.MemberAssign(span=span, base=target.base, name=target.name,
                                name_span=target.name_span, value=value)
    return None


# -- walks over a tree ---------------------------------------------------------


def _holes_in(node: object, found: list[str] | None = None) -> list[str]:
    """Every hole written below *node*, in the order they are written."""
    into = [] if found is None else found
    if isinstance(node, ast.Hole) and node.name not in into:
        into.append(node.name)
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            _holes_in(getattr(node, one.name), into)
    elif isinstance(node, tuple):
        for one in node:
            _holes_in(one, into)
    return into


def _bound_in(node: object, found: set[str] | None = None) -> set[str]:
    """Every name a template binds, which is what hygiene renames.

    A variable a template defines, and nothing else: a parameter belongs to a
    function the template wrote, whose names are the function's own business, and a
    name the template *reads* is either a hole or something its own file can see.
    """
    into = set() if found is None else found
    if isinstance(node, ast.VarDef):
        into.add(node.name)
        into.update(name for name, _ in node.more)
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            _bound_in(getattr(node, one.name), into)
    elif isinstance(node, tuple):
        for one in node:
            _bound_in(one, into)
    return into


def _written_alike(one: object, other: object) -> bool:
    """Whether two pieces of a program are the same thing written.

    The trees are compared and the spans are not: two pieces are alike where the
    same thing is written in both, wherever each was written.
    """
    if type(one) is not type(other):
        return False
    if isinstance(one, ast.Node):
        return all(_written_alike(getattr(one, f.name), getattr(other, f.name))
                   for f in fields_of(one) if f.name != "span"
                   and not f.name.endswith("_span"))
    if isinstance(one, tuple):
        assert isinstance(other, tuple)
        return len(one) == len(other) and all(
            _written_alike(a, b) for a, b in zip(one, other))
    return one == other


def _spans_of(node: object) -> Span | None:  # pragma: no cover - for debugging
    """The span of a node, for whoever is looking at one in a debugger."""
    return getattr(node, "span", None)
