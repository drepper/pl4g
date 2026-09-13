"""Semantic analysis and lowering to the IR.

Definitions need not be processed in order: every top-level definition is
collected first and only then is any body checked, which is what lets the whole
compilation be parallelized and what makes a forward reference legal.
"""

from dataclasses import dataclass, field
from typing import Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine, Expectation
from ..front import ast
from ..ir.builder import IRBuilder
from ..ir.inst import BinOp, CmpPred, Instruction, UnOp
from ..ir.function import (FuncAttrs, Function, InlineHint, Linkage, SpecialKind)
from ..ir.module import GlobalVar, Module
from ..ir.types import BOOL, BUILTIN_TYPES, ERROR, IntType, Type, VOID
from .modules import (ImportCycle, LoadedModule, ModuleNotFound, ModuleRegistry,
                      base_name)
from ..ir.value import UndefConst, Value
from pathlib import Path

from ..source.location import INVALID_SPAN, Span
from ..source.manager import SourceManager
from .attributes import (AttrSpec, AttrTarget, BoundAttr, SPECIAL_OF_TEST_KIND,
                         TARGET_NAMES, lookup)

#: The type every startup function must return.
#:
#: The exit status of a process is eight bits wide: what a program passes to the
#: system is truncated to that before anything can observe it.  A wider type
#: would let a program state a status that cannot arrive.
STARTUP_RETURN_TYPE_NAME = "u8"


@dataclass(slots=True)
class _Collected:
    """A top-level function definition and the attributes bound to it."""

    node: ast.FuncDef
    attrs: list[BoundAttr]
    func: Function
    expectation: Expectation | None = None


@dataclass(frozen=True, slots=True)
class _Expected:
    """One thing a construct said about a diagnostic it may raise."""

    number: int
    span: Span
    #: Whether the construct asserted that it is raised, rather than merely
    #: allowing it to be.
    asserted: bool


@dataclass(slots=True)
class _Local:
    """A name bound inside a function body, and the value it stands for.

    Assigning to one rebinds the name: a local is a value, so the new value
    simply takes the old one's place and nothing is written anywhere.
    """

    name: str
    value: Value
    span: Span
    mutable: bool = False
    #: Whether anything has read the value the name currently stands for.
    read: bool = False
    #: Where that value was given, for reporting one that nothing reads.
    value_span: Span = INVALID_SPAN
    #: A parameter arrives with a value the caller chose, so not reading it says
    #: nothing about this function.
    is_parameter: bool = False
    #: What the definition said it raises, kept in force for as long as the
    #: variable exists, since not every such diagnostic is raised while the
    #: definition itself is being read.
    expectation: Expectation | None = None
    expected_pairs: list[_Expected] = field(default_factory=list)


@dataclass(slots=True)
class _Global:
    """A variable defined at the top level, and what it says it raises.

    Whether anything reads one is a question about the whole program, since a
    variable there can be named from any function; it is therefore answered
    once every function has been checked, and what the definition said it raises
    has to survive until then.
    """

    var: GlobalVar
    span: Span
    expectation: Expectation | None = None
    expected_pairs: list[_Expected] = field(default_factory=list)


def found_name(prefix: str, base: str) -> str:
    """The name a module goes by when reached through *prefix*."""
    return ".".join((prefix, base)) if prefix else base


def _is_exported(what: object) -> bool:
    """Whether a top-level definition is one an importing file may name."""
    return bool(getattr(what, "exported", False))


#: What each operator of the source means in the representation.  The two are
#: separate because the source names an operator and the representation names an
#: operation: several spellings may come to mean one operation later, and the
#: front end is deliberately free of any knowledge of the representation.
#: The comparisons, and what each asks of two values.  A pair per operator,
#: because a signed and an unsigned ordering are different questions and the
#: type of the operands is what says which one was asked; equality is the same
#: question either way and names one predicate twice.
_COMPARISONS: Final[dict[ast.BinaryOp, tuple[CmpPred, CmpPred]]] = {
    ast.BinaryOp.EQUAL: (CmpPred.EQ, CmpPred.EQ),
    ast.BinaryOp.NOT_EQUAL: (CmpPred.NE, CmpPred.NE),
    ast.BinaryOp.LESS: (CmpPred.SLT, CmpPred.ULT),
    ast.BinaryOp.GREATER: (CmpPred.SGT, CmpPred.UGT),
    ast.BinaryOp.LESS_EQUAL: (CmpPred.SLE, CmpPred.ULE),
    ast.BinaryOp.GREATER_EQUAL: (CmpPred.SGE, CmpPred.UGE),
}

#: The logical operators that compute both sides and then combine them, and the
#: instruction each one is built from.  `⊼` and `⊽` are the first two with the
#: answer turned round afterwards, which is what the second field says.
_LOGIC_OPS: Final[dict[ast.BinaryOp, tuple[BinOp, bool]]] = {
    ast.BinaryOp.LOGIC_AND: (BinOp.AND, False),
    ast.BinaryOp.LOGIC_OR: (BinOp.OR, False),
    ast.BinaryOp.LOGIC_XOR: (BinOp.XOR, False),
    ast.BinaryOp.LOGIC_NAND: (BinOp.AND, True),
    ast.BinaryOp.LOGIC_NOR: (BinOp.OR, True),
}

#: The two that do not compute both sides, and what the left one deciding the
#: answer means: `and` is answered by a false left side, `or` by a true one.
_SHORT_CIRCUIT: Final[dict[ast.BinaryOp, bool]] = {
    ast.BinaryOp.SHORT_AND: False,
    ast.BinaryOp.SHORT_OR: True,
}

#: The comparisons that put the two values in an order.  Ordering is defined on
#: numbers; equality is defined on anything whose values can be told apart.
_ORDERINGS: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.LESS, ast.BinaryOp.GREATER,
    ast.BinaryOp.LESS_EQUAL, ast.BinaryOp.GREATER_EQUAL))

_BINARY_OPS: Final[dict[ast.BinaryOp, BinOp]] = {
    ast.BinaryOp.BIT_AND: BinOp.AND,
    ast.BinaryOp.BIT_OR: BinOp.OR,
    ast.BinaryOp.BIT_XOR: BinOp.XOR,
    ast.BinaryOp.ADD: BinOp.ADD,
    ast.BinaryOp.SUBTRACT: BinOp.SUB,
    ast.BinaryOp.MULTIPLY: BinOp.MUL,
    ast.BinaryOp.SAT_ADD: BinOp.SAT_ADD,
    ast.BinaryOp.SAT_SUB: BinOp.SAT_SUB,
    ast.BinaryOp.SAT_MUL: BinOp.SAT_MUL,
}

_UNARY_OPS: Final[dict[ast.UnaryOp, UnOp]] = {
    ast.UnaryOp.BIT_NOT: UnOp.NOT,
}


class Checker:
    """Checks one program and lowers it into a module."""

    def __init__(self, module: Module, diags: DiagEngine,
                 registry: "ModuleRegistry | None" = None,
                 path: "Path | None" = None, prefix: str = "",
                 sources: "SourceManager | None" = None,
                 top_level: "list[_Global] | None" = None) -> None:
        self._module = module
        self._diags = diags
        self._defined: dict[str, tuple[Span, str]] = {}
        #: What this file's top-level names stand for.  It is the file's own,
        #: not the compilation's: two files may each define a `counter`, and
        #: neither can see the other's unless it imports it.
        self._top: dict[str, object] = {}
        #: The definitions this file owns, which carry its module's name once
        #: that name is settled.
        self._owned: list[object] = []
        #: Where this file is, which is what an import written in it is relative
        #: to, and what tells its definitions from another file's.
        self._path: Path = path if path is not None else Path("")
        #: Where modules are found and what has been read, shared by every file
        #: of one compilation.
        self._registry: ModuleRegistry = (registry if registry is not None
                                          else ModuleRegistry())
        #: The name of the module this file is, which goes in front of the name
        #: of anything it imports.
        self._prefix: str = prefix
        #: Where the text of a module read from here comes from.
        self._sources: SourceManager = sources if sources is not None else SourceManager()
        #: Where each definition's name is written, for pointing at it in a note.
        self._name_spans: dict[str, Span] = {}
        #: The names bound inside the function being checked, innermost last.
        self._scopes: list[dict[str, _Local]] = []
        #: The variable being given a value, while one is being checked.  It is
        #: what lets a type that does not match say which variable it is about
        #: rather than borrow the wording of a return.
        self._initializing: str | None = None
        #: The variable being assigned to, while one is being checked.
        self._assigning: str | None = None
        #: The operator whose operands are being checked, if any.  It is what
        #: lets a type that does not match say which operator it is about rather
        #: than borrow the wording of a return.
        self._operand_of: str | None = None
        #: Whether anything inside the function being checked absorbed an error.
        #: A construct that raises one cannot be compiled, so the definition it
        #: belongs to is discarded rather than half built.
        self._discard_function: bool = False
        #: Which argument of which function is being lowered, so that a type
        #: that does not match is reported as what it is.
        self._handing_over: tuple[str, int] | None = None
        #: What each top-level definition says it raises, and where it says so.
        self._expected_pairs: dict[str, list[_Expected]] = {}
        #: Every variable defined at the top level of any file, in the order it
        #: was written, so that what nothing reads can be reported once every
        #: file has been read.  It is shared with the checkers of the modules
        #: this one imports, since the question is about the whole program.
        self._top_level: list[_Global] = (top_level if top_level is not None
                                          else [])

    # -- entry point -----------------------------------------------------------

    def run(self, units: Sequence[ast.SourceUnit],
            whole_program: bool = True) -> Module:
        """Check every unit and lower it into the module.

        A module read on the way is checked by a checker of its own, which does
        not ask the questions that are about the whole program: whether there is
        a startup function and whether anything reads a variable cannot be
        answered until every file has been read, and a module read first would
        answer both wrongly.
        """
        collected: list[_Collected] = []
        for unit in units:
            self._module.source_paths.append(unit.path)
            for item in unit.items:
                match item:
                    case ast.FuncDef():
                        gathered = self._collect_function(item, unit.path)
                        if gathered is not None:
                            collected.append(gathered)
                    case ast.VarDef():
                        self._collect_global(item)
                    case ast.ModuleImport():
                        self._collect_import(item)
                    case _:
                        self._diags.internal("unknown kind of top-level definition")
        for entry in collected:
            self._lower_function(entry)
        if whole_program:
            self._check_program()
        return self._module

    # -- variables -------------------------------------------------------------

    def _key(self, name: str) -> str:
        """What a definition of this file is filed under.

        Two files may each define a name, and the module holds both, so the key
        says which file it came from.  The name itself stays what the source
        wrote; only what it is filed under differs.
        """
        if not self._path.name:
            return name
        return "".join((str(self._path), "\0", name))

    def _declare(self, name: str, span: Span, path: str = "") -> bool:
        """Record a top-level name, reporting one that is already taken."""
        previous = self._defined.get(name)
        if previous is not None:
            self._diags.emit(D.LANG_FILESTRUCT_DUPLICATE_DEFINITION, span,
                             name=name).note(
                D.LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous[0], name=name)
            return False
        self._defined[name] = (span, path)
        self._name_spans[name] = span
        return True

    def _collect_global(self, node: ast.VarDef) -> None:
        """Register one variable defined at the top level.

        A variable whose type or value could not be worked out is registered
        anyway, with a stand-in type.  The error has been reported once; every
        later mention of the name would otherwise report it again as undefined,
        which says nothing the first message did not.
        """
        if not self._declare(node.name, node.name_span):
            return
        attrs = self._bind_attributes(node.attrs, AttrTarget.VARIABLE)
        linkage = self._linkage_of(attrs)
        pairs = self._expected_numbers(attrs)
        expectation = self._begin_expecting(pairs)
        try:
            ty = self._variable_type(node)
            initializer = self._constant_value(node, ty) if ty is not None else None
        finally:
            self._end_expecting(expectation)
        if expectation is not None and expectation.saw_error:
            # A variable whose definition could not be made sense of has no
            # value to put in the image, so it is not put there.  What it said
            # it raises is settled now, since there will be no later chance.
            self._settle_expecting(expectation, pairs)
            return
        if ty is None:
            ty = ERROR
        var = self._module.add_global(GlobalVar(
            name=node.name, value_type=ty,
            ptr_type=self._module.types.ptr_type(ty, mutable=node.mutable),
            initializer=initializer, linkage=linkage, span=node.span,
            exported=self._is_export(attrs)),
            key=self._key(node.name))
        self._top[node.name] = var
        self._owned.append(var)
        # The expectation stays alive rather than being settled here: whether
        # anything reads this variable is not known until every function has
        # been checked, so an `@[expect]` written on the definition -- where a
        # reader would write it -- has to still be in force then.
        self._top_level.append(_Global(var=var, span=node.name_span,
                                       expectation=expectation,
                                       expected_pairs=list(pairs)))

    # -- modules ---------------------------------------------------------------

    def _collect_import(self, node: ast.ModuleImport) -> None:
        """Bring a module into this file under the name it was given here.

        The file is found, read and checked now rather than later: what it holds
        has to be known before anything in this file that names it is checked,
        and reading it is the only way to know.
        """
        if not self._declare(node.name, node.name_span):
            return
        try:
            path = self._registry.resolve(node.source, self._path)
        except ModuleNotFound as exc:
            self._diags.emit(D.LANG_IMPORT_NOT_FOUND, node.source_span,
                             name=exc.name,
                             looked=str(len(exc.looked)))
            return
        ring = self._registry.cycle_through(path)
        if ring is not None:
            self._diags.emit(D.LANG_IMPORT_CYCLE, node.source_span,
                             name=node.source,
                             chain=" -> ".join(p.name for p in ring))
            return
        found = self._registry.known(path)
        if found is None:
            found = self._read_module(path, node)
            if found is None:
                return
        # Every route to a module is a name it could go by; which one it ends up
        # with is settled once every route is known.
        found.add_candidate(self._prefix, base_name(path))
        self._top[node.name] = found

    def _read_module(self, path: Path, node: ast.ModuleImport) -> "LoadedModule | None":
        """Read and check the file at *path*, returning what it holds."""
        try:
            loaded = self._registry.begin(path)
        except ImportCycle as exc:
            self._diags.emit(D.LANG_IMPORT_CYCLE, node.source_span,
                             name=node.source,
                             chain=" -> ".join(p.name for p in exc.chain))
            return None
        try:
            unit = self._read_unit(path, node)
            if unit is None:
                return None
            prefix = found_name(self._prefix, base_name(path))
            inner = Checker(self._module, self._diags, self._registry, path, prefix,
                            self._sources, self._top_level)
            inner.run([unit], whole_program=False)
            loaded.exports = {name: what for name, what in inner._top.items()
                              if _is_exported(what)}
            loaded.owned = inner._owned
        finally:
            self._registry.finish(path)
        return loaded

    def _read_unit(self, path: Path, node: ast.ModuleImport) -> "ast.SourceUnit | None":
        """Read and parse one module file."""
        from ..front.lexer import tokenize
        from ..front.parser import parse

        try:
            source = self._sources.read(path)
        except Exception as exc:  # noqa: BLE001 - reported, not handled
            self._diags.emit(D.LANG_IMPORT_UNREADABLE, node.source_span,
                             name=node.source, reason=str(exc))
            return None
        return parse(tokenize(source, self._diags), path.as_posix(), self._diags)

    def _lower_member(self, builder: IRBuilder, expr: ast.Member,
                      expected: Type | None) -> Value:
        """Lower something named through the module it belongs to."""
        base = expr.base
        if not isinstance(base, ast.NameRef):
            self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, expr.span,
                             name="an expression")
            return UndefConst(ERROR)
        held = self._top.get(base.name)
        if not isinstance(held, LoadedModule):
            self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, base.span, name=base.name)
            return UndefConst(ERROR)
        found = held.exports.get(expr.name)
        if found is None:
            self._diags.emit(D.LANG_IMPORT_NOT_EXPORTED, expr.name_span,
                             name=expr.name, module=base.name)
            return UndefConst(ERROR)
        if isinstance(found, GlobalVar):
            return builder.load(found, expr.span)
        # A function is not a value, so naming one outside a call is naming
        # something there is nothing to do with.
        self._diags.emit(D.LANG_CALL_NOT_A_FUNCTION, expr.span, name=expr.name)
        return UndefConst(ERROR)

    def _variable_type(self, node: ast.VarDef) -> Type | None:
        """The type of a variable: the one declared, or the one its value has."""
        if node.type is not None:
            return self._resolve_type(node.type)
        derived = self._type_of(node.value)
        if derived is None:
            return None
        return derived

    def _type_of(self, expr: ast.Expr) -> Type | None:
        """The type an expression has on its own, without a context to take one from."""
        match expr:
            case ast.IntLit():
                if expr.type_name is None:
                    # A literal with no suffix and no context is an untyped
                    # value, which the specification describes and this compiler
                    # does not have yet.
                    self._diags.emit(
                        D.IMPL_UNIMPLEMENTED_FEATURE, expr.span,
                        feature=("an integer literal with neither a type suffix nor a "
                                 "context that gives it a type"))
                    return None
                found = BUILTIN_TYPES.get(expr.type_name)
                return found
            case ast.BoolLit():
                return BOOL
            case ast.NameRef():
                resolved = self._lookup(expr)
                return None if resolved is None else self._value_type_of(resolved)
            case _:
                self._diags.emit(
                    D.IMPL_UNIMPLEMENTED_FEATURE, expr.span,
                    feature="deriving the type of a variable from this kind of value")
                return None

    def _value_type_of(self, value: Value) -> Type:
        """The type naming *value* yields: what a global holds, not its address."""
        if isinstance(value, GlobalVar):
            return value.value_type
        return value.ty

    def _constant_value(self, node: ast.VarDef, ty: Type) -> Value | None:
        """The value a top-level variable is given, which must be a constant."""
        if ty is ERROR:
            # The type was already reported; saying anything about the value it
            # was given would be a second message about the same mistake.
            return None
        match node.value:
            case ast.IntLit():
                if not isinstance(ty, IntType):
                    # A suffix names the literal's type outright; without one it
                    # is a number of no particular width, and saying "integer"
                    # is as much as can honestly be said about it.
                    named = BUILTIN_TYPES.get(node.value.type_name or "")
                    return self._wrong_initializer(
                        node, ty, named.render() if named is not None else "integer")
                if not self._literal_matches(node, ty):
                    return None
                if not ty.holds(node.value.value):
                    self._diags.emit(D.LANG_SYNTAX_INTEGER_RANGE, node.value.span,
                                     literal=str(node.value.value), type=ty.render())
                    return None
                return self._module.int_const(ty, node.value.value)
            case ast.BoolLit():
                if ty is not BOOL:
                    return self._wrong_initializer(node, ty, BOOL.render())
                return self._module.bool_const(BOOL, node.value.value)
            case _:
                self._diags.emit(
                    D.IMPL_UNIMPLEMENTED_FEATURE, node.value.span,
                    feature="a top-level variable whose value is not a literal")
                return None

    def _wrong_initializer(self, node: ast.VarDef, ty: Type, found: str) -> None:
        """Report a literal of a kind the declared type cannot hold.

        A literal the type has no use for is a mismatch, the same one a variable
        inside a function reports.  Falling through to "not implemented" would
        say the compiler is unfinished where the program is simply wrong.
        """
        self._diags.emit(D.LANG_TYPE_INITIALIZER_MISMATCH, node.value.span,
                         name=node.name, expected=ty.render(), found=found)
        return None

    def _literal_matches(self, node: ast.VarDef, ty: Type) -> bool:
        """Check a suffixed literal against the type the variable was declared."""
        literal = node.value
        if not isinstance(literal, ast.IntLit) or literal.type_name is None:
            return True
        named = BUILTIN_TYPES.get(literal.type_name)
        if named is not None and named is not ty:
            self._diags.emit(D.LANG_TYPE_INITIALIZER_MISMATCH, literal.span,
                             name=node.name, expected=ty.render(), found=named.render())
            return False
        return True

    # -- expectations ----------------------------------------------------------

    def _expected_numbers(self, attrs: Sequence[BoundAttr]) -> list[_Expected]:
        """What a construct says about the diagnostics it raises.

        ``expect`` asserts that one is raised; ``ignore`` only keeps it quiet.
        Both suppress it, and the difference shows where nothing meets them.
        """
        found: list[_Expected] = []
        for attr in attrs:
            if attr.name not in ("expect", "ignore"):
                continue
            number = attr.as_int("number")
            if number not in self._diags.catalog.by_number:
                self._diags.emit(D.LANG_ATTR_EXPECT_UNKNOWN_NUMBER, attr.node.span,
                                 number=number)
                continue
            found.append(_Expected(number=number, span=attr.node.span,
                                   asserted=attr.name == "expect"))
        return found

    def _begin_expecting(self, pairs: Sequence["_Expected"]) -> Expectation | None:
        """Put what a construct said about its diagnostics in force."""
        if not pairs:
            return None
        return self._diags.expect(
            frozenset(item.number for item in pairs),
            frozenset(item.number for item in pairs if item.asserted))

    def _end_expecting(self, expectation: Expectation | None) -> None:
        """Take them out of force, without yet deciding whether they were met."""
        if expectation is not None:
            self._diags.release(expectation)

    def _settle_expecting(self, expectation: Expectation | None,
                          pairs: Sequence["_Expected"]) -> bool:
        """Report the assertions nothing met, and say whether what was absorbed
        prevents the construct from being compiled."""
        if expectation is None:
            return False
        for number in expectation.unmet:
            where = next(item.span for item in pairs if item.number == number)
            self._diags.emit(D.LANG_ATTR_EXPECT_NOT_RAISED, where, number=number)
        return expectation.saw_error

    # -- scopes ----------------------------------------------------------------

    def _push_scope(self) -> None:
        """Enter a nested scope."""
        self._scopes.append({})

    def _pop_scope(self) -> None:
        """Leave the innermost scope, reporting values nothing read."""
        for local in self._scopes.pop().values():
            self._report_unused(local)
            self._settle_local(local)

    def _report_unused(self, local: _Local) -> None:
        """Report a value nothing read before it went out of reach.

        What the definition said it raises is put back in force first, so that
        an expectation written where a reader would write it -- on the
        definition -- covers a diagnostic only discovered later.
        """
        if local.read or local.is_parameter or local.value.ty is ERROR:
            return
        if local.expectation is not None:
            self._diags.resume(local.expectation)
        try:
            self._diags.emit(D.LANG_VARDEF_VALUE_UNUSED, local.value_span,
                             name=local.name)
        finally:
            self._end_expecting(local.expectation)

    def _settle_local(self, local: _Local) -> None:
        """Decide whether what a definition said it raises was met."""
        if self._settle_expecting(local.expectation, local.expected_pairs):
            self._discard_function = True
        local.expectation = None

    def _bind_local(self, name: str, value: Value, span: Span,
                    mutable: bool = False, value_span: Span = INVALID_SPAN,
                    is_parameter: bool = False) -> None:
        """Bind a name in the innermost scope, reporting one already bound there."""
        scope = self._scopes[-1]
        previous = scope.get(name)
        if previous is not None:
            self._diags.emit(D.LANG_FILESTRUCT_DUPLICATE_DEFINITION, span,
                             name=name).note(
                D.LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous.span, name=name)
            return
        scope[name] = _Local(name=name, value=value, span=span, mutable=mutable,
                             value_span=value_span if value_span.is_valid else span,
                             is_parameter=is_parameter)

    def _find_local(self, name: str) -> _Local | None:
        """The innermost binding of *name*, if there is one."""
        for scope in reversed(self._scopes):
            found = scope.get(name)
            if found is not None:
                return found
        return None

    def _lookup(self, ref: ast.NameRef) -> Value | None:
        """Resolve a name: the innermost binding first, then the top level."""
        found = self._find_local(ref.name)
        if found is not None:
            found.read = True
            return found.value
        found_global = self._top.get(ref.name)
        if isinstance(found_global, GlobalVar):
            return found_global
        if isinstance(found_global, LoadedModule):
            self._diags.emit(D.LANG_IMPORT_MODULE_AS_VALUE, ref.span, name=ref.name)
            return None
        self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, ref.span, name=ref.name)
        return None

    # -- collection ------------------------------------------------------------

    def _collect_function(self, node: ast.FuncDef, path: str) -> _Collected | None:
        """Register one function definition without looking at its body."""
        if not self._declare(node.name, node.name_span, path):
            return None
        attrs = self._bind_attributes(node.attrs, AttrTarget.FUNCTION)
        # What a definition says it raises holds while its signature is checked
        # here and again while its body is checked in the second pass, so the
        # same expectation is put back in force there.
        pairs = self._expected_numbers(attrs)
        self._expected_pairs[node.name] = pairs
        expectation = self._begin_expecting(pairs)
        try:
            params = tuple(self._resolve_type(p.type) for p in node.params)
            ret = self._return_type(node.ret_type)
            func_attrs, linkage = self._function_attrs(attrs)
            func = Function(name=node.name,
                            ty=self._module.types.func_type(params, ret),
                            attrs=func_attrs, linkage=linkage,
                            exported=self._is_export(attrs),
                            cconv="sysv" if func_attrs.abi is not None else "pl4g.v0",
                            span=node.span, source_path=path)
            self._module.add_function(func, key=self._key(node.name))
            self._top[node.name] = func
            self._owned.append(func)
            self._register_special(func, node)
        finally:
            if expectation is not None:
                self._diags.release(expectation)
        return _Collected(node=node, attrs=attrs, func=func, expectation=expectation)

    def _register_special(self, func: Function, node: ast.FuncDef) -> None:
        """Record the function in the module's caches and check its signature."""
        match func.attrs.special:
            case SpecialKind.STARTUP:
                if self._module.startup is not None:
                    previous = self._module.startup
                    self._diags.emit(D.LANG_FUNCDEF_SPECIAL_MULTIPLE_STARTUP,
                                     node.name_span).note(
                        D.LANG_FUNCDEF_SPECIAL_PREVIOUS_STARTUP,
                        self._name_spans.get(previous.name, previous.span),
                        name=previous.name)
                    return
                self._check_startup_signature(func, node)
                self._module.startup = func
            case SpecialKind.CONSTRUCTOR:
                self._check_ctor_signature(func, node, "constructor")
                self._module.ctors.append(func)
            case SpecialKind.DESTRUCTOR:
                self._check_ctor_signature(func, node, "destructor")
                self._module.dtors.append(func)
            case SpecialKind.TEST_ALWAYS | SpecialKind.TEST_BUILD | SpecialKind.TEST_SUITE:
                self._module.tests.append(func)
            case _:
                pass

    def _check_startup_signature(self, func: Function, node: ast.FuncDef) -> None:
        """Check that the startup function takes nothing and returns the status."""
        expected = BUILTIN_TYPES[STARTUP_RETURN_TYPE_NAME]
        if func.ty.ret is ERROR or ERROR in func.ty.params:
            return
        problem: str | None = None
        if func.ty.params:
            problem = "takes parameters"
        elif func.ty.ret != expected:
            problem = "".join(("returns '", func.ty.ret.render(), "'"))
        if problem is not None:
            self._diags.emit(D.LANG_FUNCDEF_SPECIAL_BAD_SIGNATURE, node.name_span,
                             name=func.name, type=STARTUP_RETURN_TYPE_NAME,
                             problem=problem)

    def _check_ctor_signature(self, func: Function, node: ast.FuncDef, kind: str) -> None:
        """Check that a constructor or destructor takes nothing and returns void."""
        if func.ty.ret is ERROR or ERROR in func.ty.params:
            return
        problem: str | None = None
        if func.ty.params:
            problem = "it takes parameters"
        elif func.ty.ret is not VOID:
            problem = "".join(("it returns '", func.ty.ret.render(), "'"))
        if problem is not None:
            self._diags.emit(D.LANG_FUNCDEF_SPECIAL_BAD_CTOR_SIGNATURE, node.name_span,
                             kind=kind, name=func.name, problem=problem)

    # -- attributes ------------------------------------------------------------

    def _bind_attributes(self, nodes: Sequence[ast.Attribute],
                         target: AttrTarget) -> list[BoundAttr]:
        """Check a list of attributes against their declarations."""
        bound: list[BoundAttr] = []
        seen: dict[str, ast.Attribute] = {}
        groups: dict[str, str] = {}
        for node in nodes:
            spec = lookup(node.name)
            if spec is None:
                self._diags.emit(D.LANG_ATTR_UNKNOWN, node.name_span, name=node.name)
                continue
            if node.name in seen and not spec.repeatable:
                self._diags.emit(D.LANG_ATTR_DUPLICATE, node.name_span, name=node.name)
                continue
            seen[node.name] = node
            if target not in spec.targets:
                self._diags.emit(D.LANG_ATTR_NOT_APPLICABLE, node.name_span,
                                 name=node.name, target=TARGET_NAMES[target])
                continue
            if spec.group is not None:
                other = groups.get(spec.group)
                if other is not None:
                    self._diags.emit(D.LANG_ATTR_CONFLICT, node.name_span,
                                     name=node.name, other=other)
                    continue
                groups[spec.group] = node.name
            values = self._bind_arguments(spec, node)
            if values is not None:
                bound.append(BoundAttr(spec=spec, node=node, values=values))
        return bound

    def _bind_arguments(self, spec: AttrSpec,
                        node: ast.Attribute) -> dict[str, object] | None:
        """Match an attribute's arguments to its parameters."""
        values: dict[str, object] = {p.name: p.default for p in spec.params}
        supplied: set[str] = set()
        index = 0
        seen_named = False
        for arg in node.args:
            if arg.name is None:
                if seen_named:
                    self._diags.emit(D.LANG_ATTR_BAD_ARGUMENTS, arg.span, name=spec.name,
                                     problem="has a positional argument after a named one")
                    return None
                if index >= len(spec.params):
                    self._diags.emit(
                        D.LANG_ATTR_BAD_ARGUMENTS, arg.span, name=spec.name,
                        problem="".join(("takes at most ", str(len(spec.params)),
                                         " arguments; its signature is ", spec.signature())))
                    return None
                param = spec.params[index]
                index += 1
            else:
                seen_named = True
                match = next((p for p in spec.params if p.name == arg.name), None)
                if match is None:
                    self._diags.emit(
                        D.LANG_ATTR_BAD_ARGUMENTS, arg.span, name=spec.name,
                        problem="".join(("has no parameter named '", arg.name,
                                         "'; its signature is ", spec.signature())))
                    return None
                param = match
            if param.name in supplied:
                self._diags.emit(
                    D.LANG_ATTR_BAD_ARGUMENTS, arg.span, name=spec.name,
                    problem="".join(("got two values for '", param.name, "'")))
                return None
            supplied.add(param.name)
            value = self._attr_value(spec, param.name, param.kind, param.choices, arg)
            if value is None:
                return None
            values[param.name] = value
        for param in spec.params:
            if param.required and param.name not in supplied:
                self._diags.emit(
                    D.LANG_ATTR_BAD_ARGUMENTS, node.span, name=spec.name,
                    problem="".join(("requires '", param.name, "'; its signature is ",
                                     spec.signature())))
                return None
        return values

    def _attr_value(self, spec: AttrSpec, param_name: str, kind: str,
                    choices: tuple[str, ...] | None, arg: ast.AttrArg) -> object | None:
        """Check one attribute argument against its declared kind."""
        node = arg.value
        actual: object | None = None
        match node:
            case ast.AttrInt() if kind == "integer":
                actual = node.value
            case ast.AttrString() if kind == "string":
                actual = node.value
            case ast.AttrBool() if kind == "boolean":
                actual = node.value
            case ast.AttrName() if kind == "name":
                actual = node.value
            case _:
                self._diags.emit(
                    D.LANG_ATTR_BAD_ARGUMENTS, arg.span, name=spec.name,
                    problem="".join(("expects ", kind, " for '", param_name, "'")))
                return None
        if choices is not None and actual not in choices:
            self._diags.emit(
                D.LANG_ATTR_BAD_ARGUMENTS, arg.span, name=spec.name,
                problem="".join(("expects one of ", ", ".join(choices), " for '",
                                 param_name, "'")))
            return None
        return actual

    def _linkage_of(self, bound: Sequence[BoundAttr]) -> Linkage:
        """Whether the image offers this definition's symbol.

        Nothing is offered unless it says so, which is why the default is the
        one that keeps it in.  Whether a file importing this module may name it
        is a different question with a different attribute; a definition may be
        offered to the outside without its module letting it in, and a module
        may let something in that no binary ever names.
        """
        if any(attr.name == "visible" for attr in bound):
            return Linkage.VISIBLE
        return Linkage.INTERNAL

    def _is_export(self, bound: Sequence[BoundAttr]) -> bool:
        """Whether a file importing this module may name the definition."""
        return any(attr.name == "export" for attr in bound)

    def _function_attrs(self, bound: Sequence[BoundAttr]) -> tuple[FuncAttrs, Linkage]:
        """Turn checked attributes into the form the IR carries."""
        special: SpecialKind | None = None
        priority: int | None = None
        inline = InlineHint.DEFAULT
        abi: str | None = None
        linkage = self._linkage_of(bound)
        extra: dict[str, int | str | bool] = {}
        for attr in bound:
            match attr.name:
                case "startup":
                    special = SpecialKind.STARTUP
                case "constructor":
                    special = SpecialKind.CONSTRUCTOR
                    priority = attr.as_int("priority")
                case "destructor":
                    special = SpecialKind.DESTRUCTOR
                    priority = attr.as_int("priority")
                case "test":
                    special = SPECIAL_OF_TEST_KIND[attr.as_str("kind")]
                case "inline":
                    inline = (InlineHint.NEVER if attr.as_str("mode") == "never"
                              else InlineHint.ALWAYS)
                case "abi":
                    abi = attr.as_str("name")
                    extra["variadic"] = attr.as_bool("variadic")
                case "align":
                    extra["align"] = attr.as_int("bytes")
                case "section":
                    extra["section"] = attr.as_str("name")
                case _:
                    pass
        return FuncAttrs(special=special, priority=priority, inline=inline, abi=abi,
                         extra=extra), linkage

    # -- types -----------------------------------------------------------------

    def _resolve_type(self, ref: ast.TypeRef) -> Type:
        """Resolve a type name, reporting an unknown one.

        A name that resolves to nothing stands in as the type that matches
        anything, so that the one mistake is reported once rather than again
        wherever the type would have been checked.
        """
        found = BUILTIN_TYPES.get(ref.name)
        if found is None:
            self._diags.emit(D.LANG_TYPE_UNKNOWN, ref.span, name=ref.name)
            return ERROR
        return found

    # -- bodies ----------------------------------------------------------------

    def _lower_function(self, entry: _Collected) -> None:
        """Check and lower one function body.

        A function that raises an error it said it would is discarded: there is
        nothing to generate code from, and half of one would be worse than none.
        """
        node, func = entry.node, entry.func
        if node.body is None:
            return
        previous = self._discard_function
        self._discard_function = False
        if entry.expectation is not None:
            self._diags.resume(entry.expectation)
        try:
            self._lower_body(entry)
        finally:
            pairs = self._expected_pairs.get(node.name, [])
            self._end_expecting(entry.expectation)
            if self._settle_expecting(entry.expectation, pairs):
                self._discard_function = True
            if self._discard_function:
                self._discard(func)
            self._discard_function = previous

    def _discard(self, func: Function) -> None:
        """Take a function out of the module, and out of every cache of it."""
        self._module.functions.pop(self._key(func.name), None)
        self._top.pop(func.name, None)
        if self._module.startup is func:
            self._module.startup = None
        for cache in (self._module.ctors, self._module.dtors, self._module.tests):
            if func in cache:
                cache.remove(func)

    def _lower_body(self, entry: _Collected) -> None:
        """Check and lower the statements of one function."""
        node, func = entry.node, entry.func
        block = func.add_block()
        builder = IRBuilder(self._module, func)
        self._push_scope()
        for index, param in enumerate(node.params):
            value = block.add_param(func.ty.params[index], param.name)
            self._bind_local(param.name, value, node.params[index].span,
                             is_parameter=True)
        assert node.body is not None
        self._lower_block(builder, node.body, func)
        self._pop_scope()
        if not builder.is_terminated:
            if func.ty.ret is VOID:
                builder.ret()
            else:
                self._diags.emit(D.LANG_FUNCDEF_RETURN_MISSING, node.name_span,
                                 name=func.name, type=func.ty.ret.render())
                builder.unreachable()

    def _lower_block(self, builder: IRBuilder, block: ast.Block, func: Function) -> None:
        """Lower the statements of one block."""
        count = len(block.stmts)
        for index, stmt in enumerate(block.stmts):
            is_last = index == count - 1
            if builder.is_terminated:
                self._diags.emit(D.LANG_FUNCDEF_RETURN_UNREACHABLE, stmt.span)
                return
            self._lower_attributed_stmt(builder, stmt, func, is_last)

    def _lower_attributed_stmt(self, builder: IRBuilder, stmt: ast.Stmt,
                               func: Function, is_last: bool) -> None:
        """Lower one statement with whatever it says it raises in force.

        A definition hands its expectation to the variable it defines, because
        what a definition raises is not all raised while it is being read: that
        nothing ever reads the value it gives is only known once the variable is
        gone.  Every other statement settles its expectation where it ends.
        """
        pairs = self._expected_numbers(
            self._bind_attributes(stmt.attrs, AttrTarget.STATEMENT))
        expectation = self._begin_expecting(pairs)
        carried = False
        try:
            self._lower_stmt(builder, stmt, func, is_last)
            if isinstance(stmt, ast.VarDef) and expectation is not None:
                local = self._find_local(stmt.name)
                if local is not None:
                    local.expectation = expectation
                    local.expected_pairs = list(pairs)
                    carried = True
        finally:
            self._end_expecting(expectation)
            if not carried and self._settle_expecting(expectation, pairs):
                self._discard_function = True

    def _lower_stmt(self, builder: IRBuilder, stmt: ast.Stmt, func: Function,
                    is_last: bool) -> None:
        """Lower one statement."""
        match stmt:
            case ast.ReturnStmt():
                # Only where the statement is one the function could have
                # wanted.  A value returned from a function that answers with
                # nothing has something else wrong with it, and saying the
                # keyword could have been left off would be advice that makes
                # it worse.
                agrees = (stmt.value is None) == (func.ty.ret is VOID)
                if stmt.explicit and is_last and agrees:
                    self._diags.emit(D.LANG_FUNCDEF_RETURN_REDUNDANT, stmt.span)
                self._lower_return(builder, stmt, func)
            case ast.VarDef():
                self._lower_local(builder, stmt)
            case ast.AssignStmt():
                # An assignment stands for the variable it changed, so it can be
                # a function's result the way any other last statement can.
                wants_value = is_last and func.ty.ret is not VOID
                result = self._lower_assignment(builder, stmt, wants_value)
                if wants_value and result is not None:
                    # The mismatch here is between what the statement produced
                    # and what the function returns, which is what a return
                    # mismatch says; the assignment itself was already checked.
                    if result.ty is not func.ty.ret:
                        self._report_mismatch(stmt.span, result.ty, func.ty.ret)
                    builder.ret(result, stmt.span)
            case ast.EmptyStmt():
                # Nothing to lower.  What it does is be a statement, so that a
                # body ending in a semicolon ends in one that produces no value.
                pass
            case ast.ExprStmt():
                # The value of the last statement is the function's result, which
                # is why the canonical form of the language omits the keyword.
                if is_last and func.ty.ret is not VOID:
                    value = self._lower_expr(builder, stmt.value, func.ty.ret)
                    builder.ret(value, stmt.span)
                else:
                    self._check_value_is_used(stmt.value)
                    self._lower_expr(builder, stmt.value, None)
            case _:
                self._diags.internal("unknown statement kind in lowering")

    def _check_value_is_used(self, expr: ast.Expr) -> None:
        """Report a statement that is an expression whose value goes nowhere.

        Every expression the language has computes a value and does nothing
        else, so a statement that is nothing but an expression does nothing
        unless something takes the value.  Something takes it in exactly one
        place -- the last statement of a body is the body's result -- and that
        statement does not reach here.  Everywhere else the line is a mistake
        or a leftover, and in a language emitted by a generator a leftover is a
        defect in the generator, which is why this is an error.

        It applies to the whole of an expression and not to any part of it: a
        bare `1u8` or a bare name is as much a statement that does nothing as a
        comparison is, and all of them are written the same way for a reader.
        `@[ignore(5005)]` on the statement says the line is meant.

        A call will be the first expression this cannot say that about, since a
        call does whatever the callee does whether or not anyone wants its
        result.  There is no way to write one yet, and when there is, this asks
        the expression whether it has an effect instead of knowing that none
        has.
        """
        if isinstance(expr, ast.Call):
            # The exception this rule was written to leave room for.  A call
            # does whatever the function does, whether or not anyone wants what
            # it answers with.
            return
        found = self._diags.emit(D.LANG_STMT_VALUE_DISCARDED, expr.span)
        if isinstance(expr, ast.Binary) and expr.op is ast.BinaryOp.EQUAL:
            found.note(D.LANG_STMT_ASSIGNMENT_IS_AN_ARROW, expr.span)

    def _lower_local(self, builder: IRBuilder, node: ast.VarDef) -> None:
        """Lower a variable defined inside a function body.

        A local is a value, not a place: the name is bound to whatever the
        initializer produced.  Nothing is reserved in memory, because nothing
        can take its address yet -- and where the language later lets a name be
        assigned, a block parameter is what carries the new value across a
        branch, which is why the representation has them.
        """
        self._bind_attributes(node.attrs, AttrTarget.VARIABLE)
        declared = (self._resolve_type(node.type) if node.type is not None
                    else self._variable_type(node))
        if declared is None or not self._literal_matches(node, declared):
            # The error is reported; binding the name anyway keeps every later
            # mention of it from reporting the same thing again as undefined.
            self._bind_local(node.name, UndefConst(ERROR), node.name_span, node.mutable,
                             value_span=node.span)
            return
        self._initializing = node.name
        try:
            value = self._lower_expr(builder, node.value, declared)
        finally:
            self._initializing = None
        bound = self._as_declared(value, declared)
        self._name_value(bound, node.name)
        self._bind_local(node.name, bound, node.name_span, node.mutable,
                         value_span=node.span)

    def _name_value(self, value: Value, name: str) -> None:
        """Record which local a computed value belongs to.

        Only an instruction is named, and only if it has no name already.  A
        constant is interned and shared with every other use of the same number,
        so writing a name on one would put that name on all of them; and where
        two locals stand for one value, the first name is the one that stays.

        The name is a hint and nothing reads it to decide anything.  It is what
        lets the textual form be read against the source it came from, and what
        lets a pass that removes a value say which local went with it.
        """
        if isinstance(value, Instruction) and value.name_hint is None:
            value.name_hint = name

    def _lower_assignment(self, builder: IRBuilder, node: ast.AssignStmt,
                          wants_value: bool = False) -> Value | None:
        """Lower an assignment to a variable that already exists.

        A local is a value, so assigning to one rebinds the name and nothing is
        written.  A variable at the top level is an address, so assigning to one
        is a store, which the memory token then orders after.

        The result is the variable the assignment named, read back.  For a local
        that is the value just bound; for one in memory it is a load, which the
        token orders after the store -- so what comes back is what was written,
        by the same rule that governs any other read.  The load is emitted only
        where the result is wanted, since reading a place nothing looks at would
        be an instruction the program never asked for.
        """
        local = self._find_local(node.name)
        if local is not None:
            if not self._check_mutable(node, local.mutable, local.span):
                return None
            value = self._checked_value(builder, node, local.value.ty)
            # The value the name stood for is gone; if nothing read it, giving
            # it cannot have affected what the program does.
            self._report_unused(local)
            local.value = value
            local.value_span = node.span
            local.read = wants_value
            return value
        target = self._top.get(node.name)
        if not isinstance(target, GlobalVar):
            target = None
        if target is None:
            self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, node.name_span,
                             name=node.name)
            return None
        if not self._check_mutable(node, target.mutable, target.span):
            return None
        value = self._checked_value(builder, node, target.value_type)
        builder.store(target, value, node.span)
        return builder.load(target, node.span) if wants_value else None

    def _check_mutable(self, node: ast.AssignStmt, mutable: bool, where: Span) -> bool:
        """Report an assignment to something whose definition did not allow it."""
        if mutable:
            return True
        self._diags.emit(D.LANG_VARDEF_NOT_MUTABLE, node.name_span,
                         name=node.name).note(
            D.LANG_VARDEF_DEFINED_HERE, where, name=node.name)
        return False

    def _checked_value(self, builder: IRBuilder, node: ast.AssignStmt,
                       expected: Type) -> Value:
        """Lower the value of an assignment, checking it against the variable."""
        self._assigning = node.name
        try:
            value = self._lower_expr(builder, node.value, expected)
        finally:
            self._assigning = None
        return self._as_declared(value, expected)

    def _return_type(self, ref: ast.TypeRef | None) -> Type:
        """What a function answers with, from what its definition wrote.

        Nothing written means nothing answered with.  Writing `void` out is
        refused: it would be a second spelling of what the absence already says,
        and `void` is not a type any value can have, so naming it as one says
        less than leaving it out.
        """
        if ref is None:
            return VOID
        if ref.name == "void":
            self._diags.emit(D.LANG_TYPE_NOTHING_IS_NOT_WRITTEN, ref.span)
            return VOID
        return self._resolve_type(ref)

    def _lower_return(self, builder: IRBuilder, stmt: ast.ReturnStmt,
                      func: Function) -> None:
        """Lower a return statement."""
        if stmt.value is None:
            if func.ty.ret is not VOID:
                self._diags.emit(D.LANG_FUNCDEF_RETURN_MISSING, stmt.span,
                                 name=func.name, type=func.ty.ret.render())
            builder.ret(None, stmt.span)
            return
        if func.ty.ret is VOID:
            if isinstance(stmt.value, ast.Call):
                # `return f()` where both answer with nothing is the call and
                # then a return carrying nothing.  No value is named anywhere in
                # it, which is why it is an abbreviation and not an exception to
                # the rule that such a call has nothing to use.
                answer = self._lower_expr(builder, stmt.value, None)
                if answer.ty is VOID or answer.ty is ERROR:
                    builder.ret(None, stmt.span)
                    return
            self._diags.emit(D.LANG_FUNCDEF_RETURN_VALUE_IN_VOID, stmt.span, name=func.name)
            builder.ret(None, stmt.span)
            return
        builder.ret(self._lower_expr(builder, stmt.value, func.ty.ret), stmt.span)

    def _lower_expr(self, builder: IRBuilder, expr: ast.Expr,
                    expected: Type | None) -> Value:
        """Lower an expression, checking it against the expected type."""
        match expr:
            case ast.IntLit():
                ty = self._literal_type(expr, expected)
                if ty is None:
                    return UndefConst(ERROR)
                if not ty.holds(expr.value):
                    self._diags.emit(D.LANG_SYNTAX_INTEGER_RANGE, expr.span,
                                     literal=str(expr.value), type=ty.render())
                    return builder.int_const(ty, 0)
                return builder.int_const(ty, expr.value)
            case ast.BoolLit():
                if expected is not None and expected is not BOOL:
                    self._report_mismatch(expr.span, BOOL, expected)
                    # The mistake is reported; carrying on with a truth value
                    # would have whatever reads it report the same thing again.
                    return UndefConst(ERROR)
                return builder.bool_const(expr.value)
            case ast.Call():
                return self._lower_call(builder, expr, expected)
            case ast.NameRef():
                return self._lower_name(builder, expr, expected)
            case ast.Binary() if expr.op in _COMPARISONS:
                return self._lower_comparison(builder, expr, expected)
            case ast.Binary() if expr.op in _LOGIC_OPS:
                return self._lower_logic(builder, expr, expected)
            case ast.Binary() if expr.op in _SHORT_CIRCUIT:
                return self._lower_short_circuit(builder, expr, expected)
            case ast.Binary():
                return self._lower_binary(builder, expr, expected)
            case ast.Unary():
                return self._lower_unary(builder, expr, expected)
            case ast.Member():
                return self._lower_member(builder, expr, expected)
            case ast.StringLit():
                self._diags.emit(D.LANG_TYPE_RETURN_MISMATCH, expr.span, found="string",
                                 expected=expected.render() if expected is not None else "void")
                return UndefConst(ERROR)
            case _:
                self._diags.internal("unknown expression kind in lowering")
                return UndefConst(ERROR)

    def _lower_comparison(self, builder: IRBuilder, expr: ast.Binary,
                          expected: Type | None) -> Value:
        """Lower a comparison, whose answer is a truth value.

        The result type and the operand type are two different things here,
        which is what makes this its own path.  What is wanted of the whole
        expression is a truth value and says nothing about what is being
        compared, so the operands take their type from each other -- which is
        what lets `count = 1u8` and `1u8 = count` mean the same thing, the same
        way the bitwise operators do it.
        """
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        context = self._hint_of(expr.left) or self._hint_of(expr.right)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._comparable(expr.left.span, expr.op, self._value_type_of(left))
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
        found = self._comparable(expr.right.span, expr.op, self._value_type_of(right))
        if ty is ERROR or found is ERROR:
            return UndefConst(ERROR)
        if found is not ty:
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, expr.right.span,
                             operator=expr.op.value, expected=ty.render(),
                             found=found.render())
            return UndefConst(ERROR)
        signed, unsigned = _COMPARISONS[expr.op]
        # A truth value is one or zero, so where it is ordered at all it is
        # ordered as an unsigned number; nothing else here is.
        return builder.compare(
            signed if isinstance(ty, IntType) and ty.signed else unsigned,
            left, right, expr.span)

    def _lower_logic(self, builder: IRBuilder, expr: ast.Binary,
                     expected: Type | None) -> Value:
        """Lower a logical operator that computes both of its operands.

        A truth value is one or zero, so "both are true" is the bits of the two
        anded together and "at least one is true" is them ored -- the same
        instruction the bitwise operators use, asked of a value that has only
        two of its bits' worth of meaning.  `⊼` and `⊽` are those two with the
        answer turned round, and turning a truth value round is an exclusive or
        with one rather than a complement: complementing one gives every bit but
        the lowest as well.
        """
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._boolean(builder, expr.left, expr.op)
            right = self._boolean(builder, expr.right, expr.op)
        finally:
            self._operand_of = outer
        if left is None or right is None:
            return UndefConst(ERROR)
        operation, inverted = _LOGIC_OPS[expr.op]
        found = builder.binary(operation, left, right, expr.span)
        return self._negate(builder, found, expr.span) if inverted else found

    def _lower_short_circuit(self, builder: IRBuilder, expr: ast.Binary,
                             expected: Type | None) -> Value:
        """Lower `and` or `or`, which do not compute the right side unless the
        left one leaves the answer open.

        This is the first and so far the only place the front end makes a
        branch.  The shape is chosen so that only the *unconditional* branches
        carry an argument: the conditional one goes to the block that computes
        the right side or to a block that does nothing but hand the answer over.
        A conditional branch whose two edges carried different arguments would
        need the edge split before the argument could be moved into place, and
        nothing here asks for that.

            entry:      condbr left → rest, decided
            decided:    br joined(left decides it)
            rest:       br joined(right)
            joined(p):  p
        """
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._boolean(builder, expr.left, expr.op)
        finally:
            self._operand_of = outer
        if left is None:
            return UndefConst(ERROR)
        decides = _SHORT_CIRCUIT[expr.op]
        rest = builder.new_block("rest")
        decided = builder.new_block("decided")
        joined = builder.new_block("joined")
        answer = joined.add_param(BOOL, "answer")
        # The left side being what decides sends control straight to the answer.
        builder.condbr(left, decided if decides else rest,
                       rest if decides else decided, span=expr.span)
        builder.position_at(decided)
        builder.br(joined, (builder.bool_const(decides),), expr.span)
        builder.position_at(rest)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            right = self._boolean(builder, expr.right, expr.op)
        finally:
            self._operand_of = outer
        if right is None:
            # The left side was fine and the right was not, so the blocks are
            # already there; giving the join something of the right type keeps
            # the representation well formed while the error is reported.
            right = UndefConst(BOOL)
        builder.br(joined, (right,), expr.span)
        builder.position_at(joined)
        return answer

    def _boolean(self, builder: IRBuilder, expr: ast.Expr,
                 op: ast.BinaryOp | ast.UnaryOp) -> Value | None:
        """Lower *expr* where only a truth value will do, or report why not.

        Nothing else counts as one.  C's rule that any number other than zero is
        true is what `if (x = 0)` comes from, and where the question really is
        whether a number is zero, `≠` asks it.
        """
        value = self._lower_expr(builder, expr, BOOL)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if ty is not BOOL:
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_BOOLEAN, expr.span,
                             operator=op.value, found=ty.render())
            return None
        return value

    def _negate(self, builder: IRBuilder, value: Value, span: Span) -> Value:
        """The truth value that is true exactly where *value* is not.

        An exclusive or with one, not a complement: a truth value is one or
        zero, and complementing it would set every other bit of the register as
        well.
        """
        return builder.binary(BinOp.XOR, value, builder.bool_const(True), span)

    def _comparable(self, span: Span, op: ast.BinaryOp, ty: Type) -> Type:
        """*ty* itself where it may stand on one side of *op*, and ERROR else.

        Ordering asks which of two values comes first, which numbers answer and
        nothing else here does.  Equality asks whether two values are the one
        value, which truth values answer as well.
        """
        if ty is ERROR:
            return ERROR
        if isinstance(ty, IntType):
            return ty
        if ty is BOOL and op not in _ORDERINGS:
            return ty
        if op in _ORDERINGS:
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, span,
                             operator=op.value, found=ty.render())
        else:
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_COMPARABLE, span,
                             operator=op.value, found=ty.render())
        return ERROR

    def _lower_binary(self, builder: IRBuilder, expr: ast.Binary,
                      expected: Type | None) -> Value:
        """Lower an operator written between two operands.

        Both sides have the same type and the result has it too, so whichever
        side says what that type is says it for the whole expression.  That is
        what lets a literal without a suffix stand on either side of one that
        has a type, which a rule that only looked leftwards would not allow.
        """
        context = expected if expected is not None else self._hint_of(expr)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._value_type_of(left)
            if ty is not ERROR and not isinstance(ty, IntType):
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.left.span,
                                 operator=expr.op.value, found=ty.render())
                ty = ERROR
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
        found = self._value_type_of(right)
        if ty is ERROR or found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, IntType):
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.right.span,
                             operator=expr.op.value, found=found.render())
            return UndefConst(ERROR)
        if found is not ty:
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, expr.right.span,
                             operator=expr.op.value, expected=ty.render(),
                             found=found.render())
            return UndefConst(ERROR)
        if expr.op in (ast.BinaryOp.DIVIDE, ast.BinaryOp.REMAINDER):
            # One operator, two instructions: dividing signed numbers and
            # dividing unsigned ones are different questions, and the type of
            # what is divided is what says which was asked.
            signed = isinstance(ty, IntType) and ty.signed
            wanted = ((BinOp.SDIV, BinOp.UDIV) if expr.op is ast.BinaryOp.DIVIDE
                      else (BinOp.SREM, BinOp.UREM))
            return builder.binary(wanted[0] if signed else wanted[1],
                                  left, right, expr.span)
        return builder.binary(_BINARY_OPS[expr.op], left, right, expr.span)

    def _lower_unary(self, builder: IRBuilder, expr: ast.Unary,
                     expected: Type | None) -> Value:
        """Lower an operator written before its operand."""
        if expr.op is ast.UnaryOp.LOGIC_NOT:
            return self._lower_not(builder, expr, expected)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            operand = self._lower_expr(builder, expr.operand, expected)
        finally:
            self._operand_of = outer
        ty = self._value_type_of(operand)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, IntType):
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.operand.span,
                             operator=expr.op.value, found=ty.render())
            return UndefConst(ERROR)
        return builder.unary(_UNARY_OPS[expr.op], operand, expr.span)

    def _lower_not(self, builder: IRBuilder, expr: ast.Unary,
                   expected: Type | None) -> Value:
        """Lower `¬`, which answers the opposite of what its operand says."""
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            operand = self._boolean(builder, expr.operand, expr.op)
        finally:
            self._operand_of = outer
        if operand is None:
            return UndefConst(ERROR)
        return self._negate(builder, operand, expr.span)

    def _hint_of(self, expr: ast.Expr) -> Type | None:
        """The type an expression says it has, without lowering it.

        Only what can be read off the syntax: a literal that names its type, a
        name already bound, or either side of an operator.  It is a hint and not
        an answer -- the answer comes from lowering -- but it is what lets both
        `count & 1` and `1 & count` mean the same thing.
        """
        match expr:
            case ast.IntLit() if expr.type_name is not None:
                return BUILTIN_TYPES.get(expr.type_name)
            case ast.BoolLit():
                return BOOL
            case ast.NameRef():
                return self._type_of_name(expr.name)
            case ast.Binary() if (expr.op in _COMPARISONS or expr.op in _LOGIC_OPS
                                  or expr.op in _SHORT_CIRCUIT):
                # What the operator answers with, not what it was given: the
                # answer is what whatever reads the expression will get.
                return BOOL
            case ast.Unary() if expr.op is ast.UnaryOp.LOGIC_NOT:
                return BOOL
            case ast.Binary():
                return self._hint_of(expr.left) or self._hint_of(expr.right)
            case ast.Unary():
                return self._hint_of(expr.operand)
            case _:
                return None

    def _type_of_name(self, name: str) -> Type | None:
        """The type a name has, without reporting one that is not bound.

        Looking a name up for a hint must neither report it missing nor record
        it as read: the lowering that follows does both, and doing them twice
        would report twice and would call a name read that only a hint looked at.
        """
        local = self._find_local(name)
        if local is not None:
            return self._value_type_of(local.value)
        found = self._top.get(name)
        return (self._value_type_of(found) if isinstance(found, GlobalVar)
                else None)

    def _literal_type(self, expr: ast.IntLit, expected: Type | None) -> IntType | None:
        """The type an integer literal has, from its suffix or from the context.

        A suffix says what the literal is; a context says what is wanted.  Where
        both are present they must agree, and where neither is the literal is an
        untyped value, which this compiler does not have yet.
        """
        named = BUILTIN_TYPES.get(expr.type_name) if expr.type_name is not None else None
        if named is not None and expected is not None and named is not expected:
            self._report_mismatch(expr.span, named, expected)
            return None
        chosen = named if named is not None else expected
        if chosen is None:
            self._diags.emit(
                D.IMPL_UNIMPLEMENTED_FEATURE, expr.span,
                feature=("an integer literal with neither a type suffix nor a context "
                         "that gives it a type"))
            return None
        if not isinstance(chosen, IntType):
            self._report_mismatch(expr.span, BUILTIN_TYPES["i32"], chosen)
            return None
        return chosen

    def _lower_call(self, builder: IRBuilder, expr: ast.Call,
                    expected: Type | None) -> Value:
        """Lower a call, checking it against what the function takes and gives.

        A function is not a value, so what is called is resolved here rather
        than lowered as an expression: there is nothing for a name that stands
        for a function to become.
        """
        func = self._callee(expr.callee)
        if func is None:
            return UndefConst(ERROR)
        wanted = func.ty.params
        if len(expr.args) != len(wanted):
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=func.name, expected=len(wanted),
                             found=len(expr.args))
            return UndefConst(ERROR)
        args: list[Value] = []
        for position, (written, ty) in enumerate(zip(expr.args, wanted), start=1):
            outer, self._handing_over = self._handing_over, (func.name, position)
            try:
                args.append(self._lower_expr(builder, written, ty))
            finally:
                self._handing_over = outer
        if any(value.ty is ERROR for value in args):
            return UndefConst(ERROR)
        answer = builder.call(func, args, func.ty.ret, expr.span)
        if func.ty.ret is VOID and expected is not None:
            # Somewhere wants a value and there is none.  The two places a call
            # like this may stand are a statement of its own and after `return`
            # in a function that also answers with nothing, and neither of them
            # asks for one.
            self._diags.emit(D.LANG_CALL_HAS_NO_VALUE, expr.span, name=func.name)
            return UndefConst(ERROR)
        if expected is not None and answer.ty is not expected:
            self._report_mismatch(expr.span, answer.ty, expected)
            return UndefConst(ERROR)
        return answer

    def _callee(self, expr: ast.Expr) -> Function | None:
        """The function a call names, or nothing where it does not name one."""
        match expr:
            case ast.NameRef():
                found = self._top.get(expr.name)
                if isinstance(found, Function):
                    return found
                if found is None and self._find_local(expr.name) is None:
                    self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, expr.span,
                                     name=expr.name)
                    return None
                self._diags.emit(D.LANG_CALL_NOT_A_FUNCTION, expr.span,
                                 name=expr.name)
                return None
            case ast.Member():
                return self._callee_of_module(expr)
            case _:
                self._diags.emit(D.LANG_CALL_NOT_A_FUNCTION, expr.span, name="this")
                return None

    def _callee_of_module(self, expr: ast.Member) -> Function | None:
        """The function another module exports under this name."""
        base = expr.base
        if not isinstance(base, ast.NameRef):
            self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, expr.span,
                             name="an expression")
            return None
        held = self._top.get(base.name)
        if not isinstance(held, LoadedModule):
            self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, base.span, name=base.name)
            return None
        found = held.exports.get(expr.name)
        if found is None:
            self._diags.emit(D.LANG_IMPORT_NOT_EXPORTED, expr.name_span,
                             name=expr.name, module=base.name)
            return None
        if not isinstance(found, Function):
            self._diags.emit(D.LANG_CALL_NOT_A_FUNCTION, expr.span, name=expr.name)
            return None
        return found

    def _lower_name(self, builder: IRBuilder, ref: ast.NameRef,
                    expected: Type | None) -> Value:
        """Lower a reference to a name.

        A local stands for the value it was bound to.  A global stands for its
        address, so reading one is a load -- which is what keeps every access to
        memory visible in the graph instead of hidden behind a name.
        """
        resolved = self._lookup(ref)
        if resolved is None:
            return UndefConst(ERROR)
        if isinstance(resolved, GlobalVar) and resolved.value_type is ERROR:
            return UndefConst(ERROR)
        if isinstance(resolved, GlobalVar):
            resolved = builder.load(resolved, ref.span)
        if expected is not None and resolved.ty != expected:
            self._report_mismatch(ref.span, resolved.ty, expected)
        return resolved

    def _as_declared(self, value: Value, expected: Type) -> Value:
        """Keep a value whose type matches; stand in for one whose type does not.

        The mismatch has been reported.  Carrying the wrong value on would make
        every later use of the name report the same thing again.
        """
        if value.ty is expected or value.ty is ERROR:
            return value
        return UndefConst(ERROR)

    def _report_mismatch(self, span: Span, found: Type, expected: Type) -> None:
        """Report a type that does not match what the context requires.

        A type that could not be worked out matches anything: the mistake behind
        it has been reported once already, and saying so again at every place the
        value reaches would add nothing.
        """
        if found is ERROR or expected is ERROR:
            return
        if self._operand_of is not None:
            # Two different mistakes: an operand of a kind the operator has no
            # meaning for, and two operands of one kind whose widths differ.
            if not isinstance(found, IntType):
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, span,
                                 operator=self._operand_of, found=found.render())
            else:
                self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, span,
                                 operator=self._operand_of,
                                 expected=expected.render(), found=found.render())
            return
        if self._handing_over is not None:
            name, position = self._handing_over
            self._diags.emit(D.LANG_CALL_ARGUMENT_MISMATCH, span, position=position,
                             name=name, expected=expected.render(),
                             found=found.render())
            return
        if self._assigning is not None:
            self._diags.emit(D.LANG_TYPE_ASSIGNMENT_MISMATCH, span,
                             name=self._assigning, expected=expected.render(),
                             found=found.render())
            return
        if self._initializing is not None:
            self._diags.emit(D.LANG_TYPE_INITIALIZER_MISMATCH, span,
                             name=self._initializing, expected=expected.render(),
                             found=found.render())
            return
        self._diags.emit(D.LANG_TYPE_RETURN_MISMATCH, span, found=found.render(),
                         expected=expected.render())

    # -- program level ---------------------------------------------------------

    def _check_program(self) -> None:
        """Check the properties the whole program must have."""
        if self._module.startup is None:
            self._diags.emit(D.LANG_FUNCDEF_SPECIAL_NO_STARTUP)
        self._report_unread_variables()

    def _report_unread_variables(self) -> None:
        """Report a variable at the top level that is written and never read.

        This is the rule that catches an unread value inside a function, asked
        of a variable the whole program can name -- which is why it is asked
        here, where every function has been checked and the answer cannot change
        any more.
        """
        reads, writes = self._variable_uses()
        for entry in self._top_level:
            var = entry.var
            if var.linkage is Linkage.VISIBLE or var.exported:
                # Something this compilation cannot see may read it -- through
                # the image's symbol table, or by importing the module -- so
                # nothing here can say that nothing does.
                self._settle_global(entry)
                continue
            count = writes.get(id(var), 0)
            if count and id(var) not in reads and var.value_type is not ERROR:
                if entry.expectation is not None:
                    self._diags.resume(entry.expectation)
                try:
                    self._diags.emit(D.LANG_VARDEF_TOPLEVEL_UNREAD, entry.span,
                                     name=var.name)
                finally:
                    self._end_expecting(entry.expectation)
            self._settle_global(entry)

    def _settle_global(self, entry: _Global) -> None:
        """Report what a variable's definition asserted and nothing raised.

        Unlike a function or a local, there is nothing left to discard by now:
        the variable is already in the module, and an error absorbed while its
        definition was read kept it out at that point.
        """
        self._settle_expecting(entry.expectation, entry.expected_pairs)
        entry.expectation = None

    def _variable_uses(self) -> tuple[set[int], dict[int, int]]:
        """Which variables the program reads, and how often it writes each.

        Each instruction says which places it names, so this does not have to
        know the shapes; a shape that can name a variable in some new way says
        so there and this keeps working.
        """
        reads: set[int] = set()
        writes: dict[int, int] = {}
        for func in self._module.functions.values():
            for block in func.blocks:
                for inst in block.insts:
                    for place in inst.reads():
                        if isinstance(place, GlobalVar):
                            reads.add(id(place))
                    for place in inst.writes():
                        if isinstance(place, GlobalVar):
                            writes[id(place)] = writes.get(id(place), 0) + 1
        return reads, writes



def check(module: Module, units: Sequence[ast.SourceUnit], diags: DiagEngine,
          registry: ModuleRegistry | None = None,
          sources: SourceManager | None = None) -> Module:
    """Check *units* and lower them into *module*.

    The units are the files named on the command line, which share one
    namespace and are the program's own module; anything they import is read
    from here, and every module's name is settled once all of them are read.
    """
    found = registry if registry is not None else ModuleRegistry()
    path = Path(units[0].path) if units else None
    result = Checker(module, diags, found, path, "", sources).run(units)
    found.settle_names()
    return result
