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
from typing import Callable, Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..ir.module import Module
from ..ir.function import Function
from ..source.location import INVALID_SPAN, Span
from . import ast
from ..target.allocator import ALLOC_SYMBOL, GROW_SYMBOL
from ..sema.tables import HEAP_ALLOC_SYMBOL, HEAP_FREE_SYMBOL
from .interpret import Machine, Refused, Stopped


class _Said(Exception):
    """A macro refusing with its own message, which is about the invocation."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

#: The names of what a macro may call, which the machine gives a meaning to.  They
#: are the checker's, because the checker is what writes the calls; they are repeated
#: here rather than imported, the checker importing this.
QUOTE_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}quote"
FILL_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}fill"
FILL_NUMBER_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}fillnumber"
FILL_TEXT_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}filltext"
PIECE_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}name"
REFUSE_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}refuse"
HEAD_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}head"
KIND_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}kind"
PARTS_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}parts"
PART_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}part"
ALIKE_PIECES_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}alike"

#: Which shape each piece of the program is, as the number `\N{APL FUNCTIONAL SYMBOL QUAD}kind` answers.  A
#: number rather than a name because the machine holds numbers; the language gives it
#: an enumeration to compare against, which is what a program writes.
_KINDS: Final[dict[str, int]] = {
    "IntLit": 1, "FloatLit": 2, "StringLit": 3, "CharLit": 4, "BoolLit": 5,
    "NameRef": 6, "Binary": 7, "Unary": 8, "Call": 9, "ArrayLit": 10,
    "TupleLit": 11, "Lambda": 12, "Block": 13, "Member": 14, "Element": 15,
    "Index": 16, "Fresh": 17,
}


class _Checked:
    """The macros, checked and lowered: the module and the trees they quoted."""

    __slots__ = ("module", "quoted")

    def __init__(self, module: Module, quoted: Sequence[ast.Quote]) -> None:
        self.module = module
        self.quoted = list(quoted)

#: How many rewrites one invocation may take before it is refused.  A macro that
#: writes an invocation of itself is the shape this catches, and the number is a
#: limit rather than an analysis: whether expansion ends is the halting problem, and
#: a program that wanted more than this many rounds would be one nobody could read.
ROUNDS: Final[int] = 64

#: What a renamed name carries.  The glyph is an operator, so a program cannot write
#: it in a name and cannot collide with one of these however it tries.
HYGIENE_MARK: Final[str] = "#"


def expand(units: Sequence[ast.SourceUnit], diags: DiagEngine,
           checked: Callable[[Sequence[ast.SourceUnit]], _Checked] | None = None,
           imported: Callable[[object], object] | None = None
           ) -> list[ast.SourceUnit]:
    """Expand every macro in *units*, answering the units with none left.

    The macros of every unit are collected first, so that a file may invoke one
    written below the invocation and -- since the units of one compilation share a
    namespace -- one written in another of them.

    *checked* is how the macros written as functions are made runnable: handed a unit
    holding them and the functions they may call, it answers the module the ordinary
    checker lowered them to.  It is a parameter rather than an import because the
    checker imports this: expansion comes first in the pipeline and last in the
    dependencies.
    """
    return _Expander(units, diags, checked, imported).run()


class _Expander:
    """One expansion of one compilation."""

    def __init__(self, units: Sequence[ast.SourceUnit], diags: DiagEngine,
                 checked: Callable[[Sequence[ast.SourceUnit]],
                                   _Checked] | None = None,
                 imported: Callable[[object], object] | None = None) -> None:
        self._units = units
        self._diags = diags
        self._checked = checked
        #: How a module an import names is found and read, which expansion needs
        #: because it comes before the checker resolves one.
        self._imported = imported
        #: The macros written as functions, by name, and the module they were
        #: lowered into once anything has asked for one.
        self._written: dict[str, ast.FuncDef] = {}
        self._made: _Checked | None = None
        self._machine: Machine | None = None
        #: Where the macro written as a function that is running was written, as the
        #: quotes it makes know it, and what the names they bind are called in this
        #: run of it.
        self._running: Span | None = None
        self._run_names: dict[str, str] = {}
        #: Every piece of the program a handle stands for, and the handle each tree
        #: has where it has one.  A handle is an index here and nothing else, which
        #: is what lets the machine hold one in a register.
        self._trees: list[object] = []
        self._handles: dict[int, int] = {}
        #: The holes of the quote a handle came from, in the order they were
        #: written.  Filling one makes a new tree and the rest are the *same* nodes,
        #: so a hole is found by which object it is rather than by counting again --
        #: which would renumber what is left every time one went.
        self._holes: dict[int, list[ast.Hole]] = {}
        #: The macros, by name.  Not beside the program's own names: a macro and a
        #: function are not in one namespace, so a name may be both, and then the
        #: parentheses call the function and the marks invoke the macro.
        self._macros: dict[str, ast.MacroDef] = {}
        #: And the macros of a module this compilation imports, by the name the
        #: import gave the module and the macro's own name.  A macro reached through
        #: a module is written `m.f⌜…⌝`, which is the path every other name a module
        #: exports is reached by: it is the same two-part name, not a third notation.
        self._through: dict[tuple[str, str], object] = {}
        #: What each of those modules defines at the top level, and the module a macro
        #: is being run out of just now.
        self._defines: dict[str, frozenset[str]] = {}
        self._invoking: str | None = None
        #: How many names hygiene has renamed, which is what makes each new one
        #: different from the last.
        self._renames = 0
        #: How many rewrites deep this is.  On the expander and not passed along,
        #: because what a macro wrote is walked whole and an invocation inside it is
        #: reached by the ordinary walk rather than by a call from here -- so a
        #: number handed down would start again at nothing every time.
        self._deep = 0

    def run(self) -> list[ast.SourceUnit]:
        """Collect the macros, then rewrite every unit without them."""
        # Which modules are invoked through at all, found before any is read: reading
        # one costs a parse of a file this compilation would otherwise parse once, and
        # having any macro at all costs a rebuild of every unit.  A program that
        # imports a module full of macros and invokes none of them should pay neither,
        # which almost every program does.
        wanted: set[str] = set()
        for unit in self._units:
            for item in unit.items:
                _invoked_through(item, wanted)
        for unit in self._units:
            for item in unit.items:
                if isinstance(item, ast.MacroDef):
                    self._collect(item)
                elif isinstance(item, ast.FuncDef) and item.is_macro:
                    self._collect_written(item)
                elif isinstance(item, ast.ModuleImport) \
                        and item.name in wanted:
                    self._collect_imported(item)
        if not self._macros and not self._written and not self._through:
            # A program with no macro is handed back as it is.  Everything below
            # rebuilds each node it passes, and rebuilding a whole compilation to
            # find nothing is what this test is here to avoid: almost every program
            # defines no macro and none of them should pay for the ones that do.
            return list(self._units)
        answer: list[ast.SourceUnit] = []
        for unit in self._units:
            items = tuple(self._walked(item) for item in unit.items
                          if not isinstance(item, ast.MacroDef)
                          and not _only_for_macros(item))
            answer.append(replace(unit, items=items))
        return answer

    def _collect_written(self, node: ast.FuncDef) -> None:
        """Write down a macro written as a function."""
        previous = self._macros.get(node.name) or self._written.get(node.name)
        if previous is not None:
            self._diags.emit(D.LANG_FILESTRUCT_DUPLICATE_DEFINITION,
                             node.name_span, name=node.name).note(
                D.LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous.name_span,
                name=node.name)
            return
        if node.ret_type is None or not _names_syntax(node.ret_type):
            # What replaces the invocation is a piece of the program, so that is what
            # a macro answers.  One that worked a number out puts it into a tree with
            # `$(…)`, which is what says where in the program the number goes.
            self._diags.emit(D.LANG_MACRO_ANSWERS_OTHERWISE, node.name_span,
                             name=node.name,
                             found="nothing" if node.ret_type is None
                             else "something else")
            return
        self._written[node.name] = node

    def _collect_imported(self, node: ast.ModuleImport) -> None:
        """Collect the macros of a module, under the name the import gave it.

        Expansion comes before anything is checked, and an import is resolved while
        checking -- so the module has to be found and read here, before the checker
        has looked at anything.  What is read is handed to the checker through the
        same cache it would have filled itself, so the file is parsed once.

        A macro of a module this file does not import is not reachable from here at
        all, which is the rule every other name a module holds follows.
        """
        if self._imported is None:
            return
        unit = self._imported(node)
        if unit is None:
            return
        named: set[str] = set()
        for item in unit.items:
            if isinstance(item, (ast.MacroDef, ast.FuncDef)) \
                    and getattr(item, "is_macro", True):
                self._through[(node.name, item.name)] = item
            held = getattr(item, "name", None)
            if isinstance(held, str):
                named.add(held)
        # Every name the module writes at the top level, so that a name the macro
        # *reads* can be found again from here: the macro's body means what it means
        # in its own file, and the same thing is written at the caller by reaching it
        # through the module -- which is the path every other name of a module takes.
        self._defines[node.name] = frozenset(named)

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
            # Rebuilt only where something below it changed, so a subtree holding no
            # invocation is the subtree that was there and not a copy of it.
            changed: dict[str, object] = {}
            for one in fields_of(node):
                was = getattr(node, one.name)
                now = self._walked(was)
                if now is not was:
                    changed[one.name] = now
            return replace(node, **changed) if changed else node
        if isinstance(node, tuple):
            found = tuple(self._walked(one) for one in node)
            return found if any(a is not b for a, b in zip(found, node)) else node
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

    def _found(self, node: ast.Invoke) -> object | None:
        """The macro an invocation names, by a bare name or through a module."""
        if node.through is not None:
            return self._through.get((node.through, node.name))
        return self._macros.get(node.name) or self._written.get(node.name)

    def _expanded(self, node: ast.Invoke,
                  as_statement: bool) -> ast.Expr | ast.Block:
        """What one invocation comes to, expanded as far as it goes."""
        if self._deep >= ROUNDS:
            self._diags.emit(D.LANG_MACRO_TOO_DEEP, node.span, name=node.name)
            return ast.NameRef(span=node.span, name=node.name)
        macro = self._found(node)
        if macro is None:
            self._diags.emit(D.LANG_MACRO_UNKNOWN, node.name_span, name=node.name)
            return ast.NameRef(span=node.span, name=node.name)
        if isinstance(macro, ast.FuncDef):
            return self._ran(node, as_statement, macro)
        assert isinstance(macro, ast.MacroDef)
        outer, self._invoking = self._invoking, node.through
        try:
            return self._by_rule(node, as_statement, macro)
        finally:
            self._invoking = outer

    def _by_rule(self, node: ast.Invoke, as_statement: bool,
                 macro: ast.MacroDef) -> ast.Expr | ast.Block:
        """What a rules-form macro comes to, by the first rule that matches."""
        for rule in macro.rules:
            bound: dict[str, ast.Expr] = {}
            if not self._matches(rule.pattern, node.arguments, bound):
                continue
            written = self._filled(self._template(rule.template), bound)
            if isinstance(written, ast.Block) and not as_statement:
                if not _comes_to_a_value(written):
                    self._diags.emit(D.LANG_MACRO_WRITES_STATEMENTS, node.span,
                                     name=node.name)
                    return ast.NameRef(span=node.span, name=node.name)
                written = ast.Scoped(span=node.span, body=written)
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

    # -- running a macro written as a function ---------------------------------

    def _ran(self, node: ast.Invoke, as_statement: bool,
             written: ast.FuncDef) -> ast.Expr | ast.Block:
        """What a macro written as a function comes to, by running it."""
        made = self._prepared()
        if made is None:
            return ast.NameRef(span=node.span, name=node.name)
        func = made.module.functions.get(_macro_symbol(written, made))
        if func is None:  # pragma: no cover - the checker reported why
            return ast.NameRef(span=node.span, name=node.name)
        given = _handed_over(node.arguments, written)
        if given is None:
            self._diags.emit(D.LANG_MACRO_ARGUMENT_COUNT, node.span,
                             name=node.name,
                             expected=_takes(written),
                             found=str(len(node.arguments.pieces)))
            return ast.NameRef(span=node.span, name=node.name)
        assert self._machine is not None
        outer, self._invoking = self._invoking, node.through
        try:
            answer = self._ran_named(func, given, written)
        except _Said as said:
            # The macro would not write what it was asked for and said why, which
            # is a fault in the invocation and not in the compiler -- so it carries
            # the macro's own words and points where the invocation is.
            self._invoking = outer
            self._diags.emit(D.LANG_MACRO_REFUSED, node.span, detail=said.detail)
            return ast.NameRef(span=node.span, name=node.name)
        except Stopped as stopped:
            self._invoking = outer
            self._diags.emit(D.LANG_MACRO_RAN_BADLY, node.span, name=node.name,
                             detail=stopped.detail)
            return ast.NameRef(span=node.span, name=node.name)
        except Refused as refused:
            self._invoking = outer
            self._diags.emit(D.LANG_MACRO_CANNOT_RUN, node.span, name=node.name,
                             detail=refused.detail)
            return ast.NameRef(span=node.span, name=node.name)
        if not isinstance(answer, int) or not 0 <= answer < len(self._trees):
            self._invoking = outer
            self._diags.emit(D.LANG_MACRO_ANSWERS_OTHERWISE, node.span,
                             name=node.name, found="something that is not one")
            return ast.NameRef(span=node.span, name=node.name)
        self._invoking = outer
        found = self._trees[answer]
        if isinstance(found, ast.Block) and not as_statement:
            if not _comes_to_a_value(found):
                self._diags.emit(D.LANG_MACRO_WRITES_STATEMENTS, node.span,
                                 name=node.name)
                return ast.NameRef(span=node.span, name=node.name)
            found = ast.Scoped(span=node.span, body=found)
        assert isinstance(found, (ast.Expr, ast.Block))
        self._deep += 1
        try:
            again = self._walked(found)
        finally:
            self._deep -= 1
        assert isinstance(again, (ast.Expr, ast.Block))
        return again

    def _prepared(self) -> _Checked | None:
        """The macros, checked and lowered, made on first ask.

        Once per compilation: every macro and every function they may call is in one
        module, so a macro calling another needs nothing more than the call.
        """
        if self._made is not None:
            return self._made
        if self._checked is None:  # pragma: no cover - always given in a build
            return None
        wanted: list[ast.SourceUnit] = []
        for unit in self._units:
            kept = tuple(item for item in unit.items
                         if _belongs_to_the_macros(item))
            wanted.append(replace(unit, items=kept))
        found = self._checked(wanted)
        if found is None:
            return None
        self._made = found
        self._machine = Machine({})
        self._machine.knows(self._builtins())
        return found

    def _builtins(self) -> dict[str, Callable[..., object]]:
        """What each of the compiler-provided functions a macro may call does.

        They are the only things in the machine that know what a handle stands for:
        everything else holds one as the number it is.
        """
        return {
            QUOTE_NAME: self._quoted,
            FILL_NAME: self._put_piece,
            FILL_NUMBER_NAME: self._put_number,
            FILL_TEXT_NAME: self._put_text,
            PIECE_NAME: self._spelling,
            REFUSE_NAME: self._refuses,
            HEAD_NAME: self._head,
            KIND_NAME: self._kind,
            PARTS_NAME: self._parts,
            PART_NAME: self._part,
            ALIKE_PIECES_NAME: self._pieces_alike,
            # And the allocator, which is the one callee with no body that is not
            # a question about the program: it is per-target assembly, so there is
            # nothing to run and the machine is the allocator while a macro runs.
            # That is what lets a macro join two strings -- the join itself is
            # ordinary code in the module, which the machine does run.
            ALLOC_SYMBOL: self._machine_allocates,
            GROW_SYMBOL: self._machine_allocates,
            # And the heap, which is the machine's memory as well; nothing is
            # given back, a macro running for one expansion.
            HEAP_ALLOC_SYMBOL: lambda size: self._machine_allocates(None, size),
            HEAP_FREE_SYMBOL: lambda where, size: None,
        }

    def _refuses(self, text: object) -> object:
        """What `⎕refuse` does: stop, carrying what the macro said.

        A macro that will not write what it was asked for is reporting on the
        *invocation*, so this is not the machine giving up -- it is the one thing a
        macro can say about a program, and without it a template that disagrees with
        its arguments would be reported as the compiler stopping.
        """
        raise _Said(self._read_text(text))

    def _read_text(self, held: object) -> str:
        """The text a machine value of type `str` stands for."""
        if not (isinstance(held, tuple) and len(held) == 3):
            raise Refused("text wanted")
        return self._machine.read_text(held)

    def _machine_allocates(self, arena: object, size: object) -> int:
        """Room for a macro, out of the machine's own memory."""
        return self._machine.allocate(arena, size)

    # -- handles ---------------------------------------------------------------

    def _handle(self, tree: object) -> int:
        """The handle *tree* goes by, giving it one where it has none."""
        found = self._handles.get(id(tree))
        if found is not None:
            return found
        self._trees.append(tree)
        self._handles[id(tree)] = len(self._trees) - 1
        return len(self._trees) - 1

    def _tree(self, handle: object) -> object:
        """What a handle stands for."""
        if not isinstance(handle, int) or not 0 <= handle < len(self._trees):
            raise Refused("a piece of the program that is not one")
        return self._trees[handle]

    # -- what the builtins do --------------------------------------------------

    def _ran_it(self, func: object, given: Sequence[object]) -> object:
        """Run the macro, with which module it came from already in force."""
        assert self._machine is not None
        return self._machine.call(func, [self._handle(one) for one in given])

    def _reached(self, tree: object) -> object:
        """Rewrite a name a macro of another module wrote so this file can reach it.

        **A name a macro reads means what it means where the macro was written.**  A
        macro in a module naming `Io` means that module's `Io`, and the file that
        invoked it may have no `Io` at all -- so the name is written here the way
        every other name of a module is written here: through the module.  `Io` becomes
        `std.Io`, and `Io.println` becomes `std.Io.println`, which is a path design B
        already reads.

        Only the names the module writes at the top level are touched, and only in a
        quote the macro itself wrote: what the caller handed over arrives by filling a
        hole, which happens after this and is not walked by it.  So a name of the
        caller's is never requalified, however much it looks like one of the module's.
        """
        if self._invoking is None:
            return tree
        named = self._defines.get(self._invoking)
        if not named:
            return tree
        return self._requalified(tree, self._invoking, named)

    def _requalified(self, node: object, through: str,
                     named: frozenset[str]) -> object:
        """The tree with every name the module defines reached through the module."""
        if isinstance(node, ast.NameRef) and node.name in named:
            return ast.Member(span=node.span,
                              base=ast.NameRef(span=node.span, name=through),
                              name=node.name, name_span=node.span)
        if isinstance(node, ast.Node):
            changes: dict[str, object] = {}
            for one in fields_of(node):
                held = getattr(node, one.name)
                if isinstance(held, ast.Node):
                    found = self._requalified(held, through, named)
                    if found is not held:
                        changes[one.name] = found
                elif isinstance(held, tuple) and held \
                        and all(isinstance(each, ast.Node) for each in held):
                    found_all = tuple(self._requalified(each, through, named)
                                      for each in held)
                    if any(a is not b for a, b in zip(found_all, held)):
                        changes[one.name] = found_all
            if changes:
                return replace(node, **changes)
        return node

    def _ran_named(self, func: Function, given: object,
                   written: ast.FuncDef) -> object:
        """Run a macro with the names its quotes bind chosen for this run.

        Named once for the run, so that one quote may read what another binds: a
        run of statements binding a pool and the expression naming it are two
        quotes and one variable.  A second run names them afresh, which is what
        keeps two invocations on one line from meaning one variable.
        """
        running, run_names = self._running, self._run_names
        self._running = func.span
        self._run_names = {name: self._fresh(name)
                           for name in sorted(_bound_in_quotes(written))}
        try:
            return self._ran_it(func, given)
        finally:
            self._running, self._run_names = running, run_names

    def _quoted(self, at: object) -> int:
        """`\N{APL FUNCTIONAL SYMBOL QUAD}quote(n)`: the tree the macro wrote down, holes and all."""
        assert self._made is not None
        if not isinstance(at, int) or not 0 <= at < len(self._made.quoted):
            raise Refused("a quote that was never written")
        quote = self._made.quoted[at]
        if quote.body is None and len(quote.pieces) != 1:
            raise Refused("a quote of more than one expression")
        tree = quote.body if quote.body is not None else quote.pieces[0]
        # What the quote binds is renamed, once per run of the macro that wrote it,
        # so that a temporary a macro writes is not the caller's variable of that
        # name.
        # The holes come through the rename as the same objects, which is what lets
        # the calls that fill them find them afterwards.
        tree = self._renamed(tree, quote)
        tree = self._reached(tree)
        holes: list[ast.Hole] = []
        _holes_of(tree, holes)
        found = self._handle(tree)
        self._holes[found] = holes
        return found

    def _renamed(self, tree: object, quote: ast.Quote) -> object:
        """*tree* with every name it binds renamed to one no source file can spell.

        A quote the running macro wrote takes the names of the run, readings and
        bindings alike; one a function it called wrote is renamed by itself, once
        per time it is asked for.
        """
        wanted = {name: self._fresh(name) for name in _bound_in(tree)}
        if self._running is not None and _within(quote.span, self._running):
            wanted = self._run_names
        return _with_names(tree, wanted) if wanted else tree

    def _put_piece(self, tree: object, at: object, with_: object) -> int:
        """`\N{APL FUNCTIONAL SYMBOL QUAD}fill(t, n, v)`: the tree with hole number *n* replaced by *v*."""
        return self._put_into(tree, at, self._tree(with_))

    def _put_number(self, tree: object, at: object, number: object) -> int:
        """The same, with the number a program would have written to mean it."""
        if not isinstance(number, int):
            raise Refused("a number wanted")
        written = ast.IntLit(span=INVALID_SPAN, value=abs(number),
                             type_name="i64")
        return self._put_into(tree, at, written if number >= 0 else ast.Unary(
            span=INVALID_SPAN, op=ast.UnaryOp.NEGATE, operand=written))

    def _put_text(self, tree: object, at: object, text: object) -> int:
        """The same, with the literal a program would have written to mean the text.

        What a macro taking a template apart puts back: the pieces between the holes
        are text it worked out, and a literal is how text stands in a program.
        """
        return self._put_into(tree, at, ast.StringLit(
            span=INVALID_SPAN, value=self._read_text(text)))

    def _spelling(self, handle: object) -> tuple[int, int, int]:
        """What a piece is written as, as text.

        A string literal answers what is between its quotation marks and a name
        answers itself; anything else is refused, there being no one answer for an
        expression -- `1u8 + 2u8` is not written as any one word, and a macro that
        wants to know what it is made of asks `⎕head`.

        This is what lets a macro read a template: the template is a literal, and
        reading it is asking what it says.
        """
        found = self._tree(handle)
        match found:
            case ast.StringLit():
                return self._machine.text(found.value)
            case ast.NameRef():
                return self._machine.text(found.name)
        raise Refused("".join((type(found).__name__.lower(),
                               " is written as no one word")))

    def _put_into(self, tree: object, at: object, put: object) -> int:
        """The tree with one hole replaced, and the rest still findable."""
        handle = _counted(tree)
        holes = self._holes.get(handle)
        number = _counted(at)
        if holes is None or number >= len(holes):
            raise Refused("a hole that the quote does not have")
        found = self._handle(_without_hole(self._tree(tree), holes[number], put))
        self._holes[found] = holes
        return found

    def _head(self, tree: object) -> int:
        """`\N{APL FUNCTIONAL SYMBOL QUAD}head(e)`: what the piece is made by."""
        found = _head_of(self._tree(tree))
        return self._handle(found)

    def _kind(self, tree: object) -> int:
        """`\N{APL FUNCTIONAL SYMBOL QUAD}kind(e)`: which of the shapes it is, as a number."""
        return _KINDS.get(type(self._tree(tree)).__name__, 0)

    def _parts(self, tree: object) -> int:
        """`\N{APL FUNCTIONAL SYMBOL QUAD}parts(e)`: how many pieces it applies its head to."""
        return len(_parts_of(self._tree(tree)))

    def _part(self, tree: object, at: object) -> int:
        """`\N{APL FUNCTIONAL SYMBOL QUAD}part(e, n)`: the piece it applies its head to, by which one."""
        parts = _parts_of(self._tree(tree))
        number = _counted(at)
        if number >= len(parts):
            raise Refused("a part that is not there")
        return self._handle(parts[number])

    def _pieces_alike(self, one: object, other: object) -> bool:
        """Whether two pieces are the same thing written."""
        return _written_alike(self._tree(one), self._tree(other))

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

    def _template(self, template: ast.Quote) -> ast.Quote:
        """A template with the names of its own module reached through the module."""
        found = self._reached(template)
        assert isinstance(found, ast.Quote)
        return found

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
        self._renames += 1
        return "".join((name, HYGIENE_MARK, str(self._renames)))

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


def only_for_macros(item: object) -> bool:
    """Whether a top-level definition belongs to the macros and not to the program.

    Named without the underscore because the checker asks it too: what a *module*
    wrote for the macros has to be taken out of the module, exactly as the expander
    takes what this file wrote out of this file.
    """
    return isinstance(item, ast.MacroDef) or _only_for_macros(item)


def _only_for_macros(item: object) -> bool:
    """Whether a definition belongs to the macros and not to the program.

    A macro, plainly.  And a `comptime` function whose signature names a piece of the
    program, because nothing at run time holds one: such a function is one the macros
    may call and the program may not, where one that mentions no piece is installed
    on both sides and is the same function either way.
    """
    if not isinstance(item, ast.FuncDef):
        return False
    return item.is_macro or (item.at_compile_time and _about_a_piece(item))


def _about_a_piece(node: ast.FuncDef) -> bool:
    """Whether a signature names a piece of the program anywhere."""
    written = [one.type for one in node.params]
    if node.ret_type is not None:
        written.append(node.ret_type)
    return any(_names_syntax(one) for one in written)


def _names_syntax(node: object) -> bool:
    """Whether a type as written names a piece of the program."""
    if isinstance(node, ast.TypeRef) and node.module is None \
            and node.name == "syntax":
        return True
    if isinstance(node, ast.Node):
        return any(_names_syntax(getattr(node, one.name))
                   for one in fields_of(node))
    if isinstance(node, (list, tuple)):
        return any(_names_syntax(one) for one in node)
    return False


def _belongs_to_the_macros(item: object) -> bool:
    """Whether a definition goes into the unit the macros are checked in.

    The macros written as functions, the `comptime` functions they may call, and the
    definitions a signature may name -- a type, an enumeration, a unit.  Not the
    program's own functions: a macro runs before they are installed, which is what
    `comptime` moves a function to the other side of.
    """
    if isinstance(item, ast.FuncDef):
        return item.at_compile_time
    # And the imports, so that a macro of a module this file imports is checked and
    # lowered with the rest: the module is read again here, by a checker that keeps
    # what the macros own instead of taking it out.
    return isinstance(item, (ast.TypeDef, ast.EnumDef, ast.UnitDef,
                             ast.ModuleImport))


def _macro_symbol(node: ast.FuncDef, made: _Checked) -> str:
    """The name the macro's function goes by in the module it was lowered into."""
    for name, func in made.module.functions.items():
        if func.name == node.name:
            return name
    return node.name


def _counted(at: object) -> int:
    """A hole's number, as the machine handed it over."""
    if not isinstance(at, int) or at < 0:
        raise Refused("a hole that is not one")
    return at


def _with_names(node: object, wanted: dict[str, str]) -> object:
    """*node* with the names in *wanted* replaced by what they map to.

    A hole comes through as the same object, which is what lets the calls that fill
    the holes find them after the renaming.
    """
    if isinstance(node, ast.Hole):
        return node
    if isinstance(node, ast.NameRef) and node.name in wanted:
        return replace(node, name=wanted[node.name])
    if isinstance(node, ast.VarDef) and node.name in wanted:
        node = replace(node, name=wanted[node.name])
    if isinstance(node, ast.AssignStmt) and node.name in wanted:
        node = replace(node, name=wanted[node.name])
    if isinstance(node, ast.Node):
        return replace(node, **{one.name: _with_names(getattr(node, one.name),
                                                     wanted)
                                for one in fields_of(node)})
    if isinstance(node, tuple):
        return tuple(_with_names(one, wanted) for one in node)
    return node


def _without_hole(tree: object, hole: ast.Hole, put: object) -> object:
    """*tree* with that one hole replaced by *put*.

    Which hole it is, is which object it is: filling one makes a new tree and leaves
    the others the same nodes, so counting again would renumber what is left.
    """
    return _fill_counting(tree, hole, put, [0])


def _fill_counting(node: object, at: ast.Hole, put: object,
                   seen: list[int]) -> object:
    """The walk that fills one hole."""
    if isinstance(node, ast.Hole):
        return put if node is at else node
    if isinstance(node, ast.HoleAssign):
        target = _fill_counting(node.target, at, put, seen)
        value = _fill_counting(node.value, at, put, seen)
        if isinstance(target, ast.Expr) and not isinstance(target, ast.Hole):
            found = _assignment(target, value, node.span)
            if found is not None:
                return found
        assert isinstance(target, ast.Expr) and isinstance(value, ast.Expr)
        return replace(node, target=target, value=value)
    if isinstance(node, ast.Node):
        return replace(node, **{one.name: _fill_counting(getattr(node, one.name),
                                                        at, put, seen)
                                for one in fields_of(node)})
    if isinstance(node, tuple):
        return tuple(_fill_counting(one, at, put, seen) for one in node)
    return node


def _head_of(node: object) -> object:
    """What a piece of the program is made by.

    Every piece answers, so there is never a question of whether there is one: an
    expression that applies something answers what it applies, and one that applies
    nothing answers the most particular name the language has for what it is.
    """
    if isinstance(node, ast.Binary):
        return ast.NameRef(span=node.span, name=node.op.value)
    if isinstance(node, ast.Unary):
        return ast.NameRef(span=node.span, name=node.op.value)
    if isinstance(node, ast.Fresh):
        return ast.NameRef(span=node.span, name=node.glyph)
    if isinstance(node, ast.Call):
        return node.callee
    if isinstance(node, ast.IntLit):
        return ast.NameRef(span=node.span, name=node.type_name or "int")
    # A literal of any other kind is made by its type, as a number is: what a
    # macro asks it is whether a piece is text, a character, a truth value.
    if isinstance(node, ast.StringLit):
        return ast.NameRef(span=node.span, name="str")
    if isinstance(node, ast.CharLit):
        return ast.NameRef(span=node.span, name="char")
    if isinstance(node, ast.BoolLit):
        return ast.NameRef(span=node.span, name="bool")
    if isinstance(node, ast.FloatLit):
        return ast.NameRef(span=node.span,
                           name=getattr(node, "type_name", None) or "float")
    if isinstance(node, ast.NameRef):
        return node
    return ast.NameRef(span=getattr(node, "span", INVALID_SPAN),
                       name=type(node).__name__)


def _takes(written: ast.FuncDef) -> str:
    """How many arguments a macro takes, said in words a message can use."""
    fixed = len(written.params) - 1 if _has_several(written) else len(written.params)
    if not _has_several(written):
        return str(fixed)
    return "".join((str(fixed), " or more"))


def _has_several(written: ast.FuncDef) -> bool:
    """Whether the last parameter stands for all the arguments from there on."""
    return bool(written.params) and written.params[-1].several


def _handed_over(arguments: ast.Quote,
                 written: ast.FuncDef) -> tuple[object, ...] | None:
    """What the macro is handed, or nothing where the count does not fit.

    A parameter written with `⁂` is handed the rest of the arguments as one piece --
    a quote, which is what several pieces standing where one does already is -- so
    `⎕parts` of it is how many there are and `⎕part` is each.  That is a variadic
    call without the language gaining a variadic function: how many there are is a
    question about a piece and the compiler answers it.
    """
    if arguments.body is not None:
        return None
    pieces = arguments.pieces
    if not _has_several(written):
        return None if len(pieces) != len(written.params) else tuple(pieces)
    fixed = len(written.params) - 1
    if len(pieces) < fixed:
        return None
    rest = pieces[fixed:]
    return (*pieces[:fixed],
            ast.Quote(span=arguments.span, pieces=tuple(rest)))


def _invoked_through(node: object, into: set[str]) -> None:
    """Every module a macro is invoked through below *node*.

    A walk that builds nothing: what it is for is deciding whether anything has to be
    read or rebuilt at all, so it has to be cheaper than the thing it is avoiding.
    """
    if isinstance(node, ast.Invoke) and node.through is not None:
        into.add(node.through)
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            held = getattr(node, one.name)
            if isinstance(held, ast.Node):
                _invoked_through(held, into)
            elif isinstance(held, tuple):
                for each in held:
                    if isinstance(each, ast.Node):
                        _invoked_through(each, into)


def _parts_of(node: object) -> tuple[object, ...]:
    """What a piece applies its head to, which is empty where it applies nothing."""
    if isinstance(node, ast.Binary):
        return (node.left, node.right)
    if isinstance(node, ast.Unary):
        return (node.operand,)
    if isinstance(node, ast.Fresh):
        return tuple(node.operands)
    if isinstance(node, ast.Call):
        return tuple(node.args)
    if isinstance(node, ast.Quote):
        # Several pieces standing where one does, which is what a macro's last
        # parameter is handed where `⁂` is written before its name: the rest of the
        # arguments, as the one thing a quote already is.
        return tuple(node.pieces)
    return ()


def _holes_of(node: object, into: list[ast.Hole]) -> None:
    """Every hole below *node*, in the order they are written.

    The same walk the checker makes when it writes the calls that fill them, which is
    what makes the numbers agree: a hole is a place and the place is its order.
    """
    if isinstance(node, ast.Hole):
        into.append(node)
        return
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            _holes_of(getattr(node, one.name), into)
    elif isinstance(node, (list, tuple)):
        for one in node:
            _holes_of(one, into)


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


def _comes_to_a_value(block: ast.Block) -> bool:
    """Whether a run of statements ends in an expression, which is what it comes to."""
    return bool(block.stmts) and isinstance(block.stmts[-1], ast.ExprStmt)


def _bound_in_quotes(node: object, found: set[str] | None = None) -> set[str]:
    """Every name a quote written below *node* binds."""
    into = set() if found is None else found
    if isinstance(node, ast.Quote):
        _bound_in(node, into)
    elif isinstance(node, ast.Node):
        for one in fields_of(node):
            _bound_in_quotes(getattr(node, one.name), into)
    elif isinstance(node, tuple):
        for one in node:
            _bound_in_quotes(one, into)
    return into


def _within(inner: Span, outer: Span) -> bool:
    """Whether *inner* is written inside *outer*."""
    return inner.is_valid and outer.start <= inner.start and inner.end <= outer.end


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
