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
from ..ir.function import (FuncAttrs, Function, InlineHint, Linkage, SpecialKind)
from ..ir.module import GlobalVar, Module
from ..ir.types import BOOL, BUILTIN_TYPES, ERROR, IntType, Type, VOID
from ..ir.value import UndefConst, Value
from ..source.location import INVALID_SPAN, Span
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


class Checker:
    """Checks one program and lowers it into a module."""

    def __init__(self, module: Module, diags: DiagEngine) -> None:
        self._module = module
        self._diags = diags
        self._defined: dict[str, tuple[Span, str]] = {}
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
        #: Whether anything inside the function being checked absorbed an error.
        #: A construct that raises one cannot be compiled, so the definition it
        #: belongs to is discarded rather than half built.
        self._discard_function: bool = False
        #: What each top-level definition says it raises, and where it says so.
        self._expected_pairs: dict[str, list[_Expected]] = {}
        #: Every variable defined at the top level, in the order it was written,
        #: so that what nothing reads can be reported once the program is whole.
        self._top_level: list[_Global] = []

    # -- entry point -----------------------------------------------------------

    def run(self, units: Sequence[ast.SourceUnit]) -> Module:
        """Check every unit and lower it into the module."""
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
                    case _:
                        self._diags.internal("unknown kind of top-level definition")
        for entry in collected:
            self._lower_function(entry)
        self._check_program()
        return self._module

    # -- variables -------------------------------------------------------------

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
            initializer=initializer, linkage=linkage, span=node.span))
        # The expectation stays alive rather than being settled here: whether
        # anything reads this variable is not known until every function has
        # been checked, so an `@[expect]` written on the definition -- where a
        # reader would write it -- has to still be in force then.
        self._top_level.append(_Global(var=var, span=node.name_span,
                                       expectation=expectation,
                                       expected_pairs=list(pairs)))

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
        found_global = self._module.globals.get(ref.name)
        if found_global is not None:
            return found_global
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
            ret = self._resolve_type(node.ret_type)
            func_attrs, linkage = self._function_attrs(attrs)
            func = Function(name=node.name,
                            ty=self._module.types.func_type(params, ret),
                            attrs=func_attrs, linkage=linkage,
                            cconv="sysv" if func_attrs.abi is not None else "pl4g.v0",
                            span=node.span, source_path=path)
            self._module.add_function(func)
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
        """How widely a definition is visible.

        Nothing is visible outside the program unless it says so, which is why
        the default is the one that keeps it in.
        """
        if any(attr.name == "export" for attr in bound):
            return Linkage.EXPORTED
        return Linkage.INTERNAL

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
        self._module.functions.pop(func.name, None)
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
                if stmt.explicit and is_last:
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
            case ast.ExprStmt():
                # The value of the last statement is the function's result, which
                # is why the canonical form of the language omits the keyword.
                if is_last and func.ty.ret is not VOID:
                    value = self._lower_expr(builder, stmt.value, func.ty.ret)
                    builder.ret(value, stmt.span)
                else:
                    self._lower_expr(builder, stmt.value, None)
            case _:
                self._diags.internal("unknown statement kind in lowering")

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
        self._bind_local(node.name, self._as_declared(value, declared), node.name_span,
                         node.mutable, value_span=node.span)

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
        target = self._module.globals.get(node.name)
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
                return builder.bool_const(expr.value)
            case ast.NameRef():
                return self._lower_name(builder, expr, expected)
            case ast.StringLit():
                self._diags.emit(D.LANG_TYPE_RETURN_MISMATCH, expr.span, found="string",
                                 expected=expected.render() if expected is not None else "void")
                return UndefConst(ERROR)
            case _:
                self._diags.internal("unknown expression kind in lowering")
                return UndefConst(ERROR)

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
            if var.linkage is Linkage.EXPORTED:
                # Something outside this compilation may read it, so nothing
                # here can say that nothing does.
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



def check(module: Module, units: Sequence[ast.SourceUnit], diags: DiagEngine) -> Module:
    """Check *units* and lower them into *module*."""
    return Checker(module, diags).run(units)
