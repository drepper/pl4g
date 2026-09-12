"""Semantic analysis and lowering to the IR.

Definitions need not be processed in order: every top-level definition is
collected first and only then is any body checked, which is what lets the whole
compilation be parallelized and what makes a forward reference legal.
"""

from dataclasses import dataclass
from typing import Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..front import ast
from ..ir.builder import IRBuilder
from ..ir.function import (FuncAttrs, Function, InlineHint, Linkage, SpecialKind)
from ..ir.module import Module
from ..ir.types import BOOL, BUILTIN_TYPES, IntType, Type, VOID
from ..ir.value import Value
from ..source.location import Span
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
    """A top-level definition and the attributes bound to it."""

    node: ast.FuncDef
    attrs: list[BoundAttr]
    func: Function


class Checker:
    """Checks one program and lowers it into a module."""

    def __init__(self, module: Module, diags: DiagEngine) -> None:
        self._module = module
        self._diags = diags
        self._defined: dict[str, tuple[Span, str]] = {}
        #: Where each function's name is written, for pointing at it in a note.
        self._name_spans: dict[str, Span] = {}

    # -- entry point -----------------------------------------------------------

    def run(self, units: Sequence[ast.SourceUnit]) -> Module:
        """Check every unit and lower it into the module."""
        collected: list[_Collected] = []
        for unit in units:
            self._module.source_paths.append(unit.path)
            for item in unit.items:
                gathered = self._collect_function(item, unit.path)
                if gathered is not None:
                    collected.append(gathered)
        for entry in collected:
            self._lower_function(entry)
        self._check_program()
        return self._module

    # -- collection ------------------------------------------------------------

    def _collect_function(self, node: ast.FuncDef, path: str) -> _Collected | None:
        """Register one function definition without looking at its body."""
        previous = self._defined.get(node.name)
        if previous is not None:
            self._diags.emit(D.LANG_FILESTRUCT_DUPLICATE_DEFINITION, node.name_span,
                             name=node.name).note(
                D.LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous[0], name=node.name)
            return None
        self._defined[node.name] = (node.name_span, path)
        self._name_spans[node.name] = node.name_span
        attrs = self._bind_attributes(node.attrs, AttrTarget.FUNCTION)
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
        return _Collected(node=node, attrs=attrs, func=func)

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
            if node.name in seen:
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

    def _function_attrs(self, bound: Sequence[BoundAttr]) -> tuple[FuncAttrs, Linkage]:
        """Turn checked attributes into the form the IR carries."""
        special: SpecialKind | None = None
        priority: int | None = None
        inline = InlineHint.DEFAULT
        abi: str | None = None
        linkage = Linkage.INTERNAL
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
                case "export":
                    linkage = Linkage.EXPORTED
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
        """Resolve a type name, reporting an unknown one."""
        found = BUILTIN_TYPES.get(ref.name)
        if found is None:
            self._diags.emit(D.LANG_TYPE_UNKNOWN, ref.span, name=ref.name)
            return VOID
        return found

    # -- bodies ----------------------------------------------------------------

    def _lower_function(self, entry: _Collected) -> None:
        """Check and lower one function body."""
        node, func = entry.node, entry.func
        if node.body is None:
            return
        block = func.add_block()
        for index, param in enumerate(node.params):
            block.add_param(func.ty.params[index], param.name)
        builder = IRBuilder(self._module, func)
        self._lower_block(builder, node.body, func)
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
            self._lower_stmt(builder, stmt, func, is_last)

    def _lower_stmt(self, builder: IRBuilder, stmt: ast.Stmt, func: Function,
                    is_last: bool) -> None:
        """Lower one statement."""
        match stmt:
            case ast.ReturnStmt():
                if stmt.explicit and is_last:
                    self._diags.emit(D.LANG_FUNCDEF_RETURN_REDUNDANT, stmt.span)
                self._lower_return(builder, stmt, func)
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
                ty = expected if isinstance(expected, IntType) else BUILTIN_TYPES["i32"]
                assert isinstance(ty, IntType)
                if not ty.holds(expr.value):
                    self._diags.emit(D.LANG_SYNTAX_INTEGER_RANGE, expr.span,
                                     literal=str(expr.value), type=ty.render())
                    return builder.int_const(ty, 0)
                if expected is not None and expected is not ty:
                    self._report_mismatch(expr.span, ty, expected)
                return builder.int_const(ty, expr.value)
            case ast.BoolLit():
                if expected is not None and expected is not BOOL:
                    self._report_mismatch(expr.span, BOOL, expected)
                return builder.bool_const(expr.value)
            case ast.NameRef():
                self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, expr.span, name=expr.name)
                return builder.int_const(BUILTIN_TYPES["i32"], 0)  # type: ignore[arg-type]
            case ast.StringLit():
                self._diags.emit(D.LANG_TYPE_RETURN_MISMATCH, expr.span, found="string",
                                 expected=expected.render() if expected is not None else "void")
                return builder.int_const(BUILTIN_TYPES["i32"], 0)  # type: ignore[arg-type]
            case _:
                self._diags.internal("unknown expression kind in lowering")
                return builder.int_const(BUILTIN_TYPES["i32"], 0)  # type: ignore[arg-type]

    def _report_mismatch(self, span: Span, found: Type, expected: Type) -> None:
        """Report a type that does not match what the context requires."""
        self._diags.emit(D.LANG_TYPE_RETURN_MISMATCH, span, found=found.render(),
                         expected=expected.render())

    # -- program level ---------------------------------------------------------

    def _check_program(self) -> None:
        """Check the properties the whole program must have."""
        if self._module.startup is None:
            self._diags.emit(D.LANG_FUNCDEF_SPECIAL_NO_STARTUP)


def check(module: Module, units: Sequence[ast.SourceUnit], diags: DiagEngine) -> Module:
    """Check *units* and lower them into *module*."""
    return Checker(module, diags).run(units)
