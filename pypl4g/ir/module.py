"""The module: everything one compilation produces."""

from dataclasses import dataclass, field

from ..source.location import INVALID_SPAN, Span
from .function import Function, Linkage
from .types import BoolType, IntType, MEM, Type, TypeContext
from .value import BoolConst, IntConst, Value


class GlobalVar(Value):
    """A variable that exists for as long as the program does.

    It is a *value* of pointer type, not a value of the type it holds: naming
    one yields its address, and reading it is a load.  That is what keeps every
    access to memory visible in the dataflow graph rather than implied by a
    name.
    """

    __slots__ = ("name", "value_type", "linkage", "initializer", "span", "module")

    def __init__(self, name: str, value_type: Type, ptr_type: Type,
                 initializer: Value | None = None,
                 linkage: Linkage = Linkage.INTERNAL,
                 span: Span = INVALID_SPAN, module: str = "") -> None:
        super().__init__(ptr_type, name)
        self.name = name
        #: The type of what the variable holds, not of the variable itself.
        self.value_type = value_type
        self.linkage = linkage
        #: A variable is always given a value where it is defined.
        self.initializer = initializer
        self.span = span
        self.module = module

    def __repr__(self) -> str:
        return "".join(("GlobalVar(@", self.name, ")"))


@dataclass(slots=True, eq=False)
class Module:
    """The whole program.

    There is no equivalent of an object file: compilation always covers the whole
    program, so a module is the unit of code generation as well as of analysis.
    """

    name: str
    triple: str = "x86_64-linux-none"
    types: TypeContext = field(default_factory=TypeContext)
    functions: dict[str, Function] = field(default_factory=dict)
    globals: dict[str, GlobalVar] = field(default_factory=dict)
    #: Caches filled by the semantic analysis and re-checked by the verifier.
    #: The backend reads only these and never scans attributes itself.
    startup: Function | None = None
    ctors: list[Function] = field(default_factory=list)
    dtors: list[Function] = field(default_factory=list)
    tests: list[Function] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)
    _int_consts: dict[tuple[int, bool, int], IntConst] = field(default_factory=dict)
    _bool_consts: dict[bool, BoolConst] = field(default_factory=dict)

    def add_function(self, func: Function) -> Function:
        """Register *func* in this module."""
        self.functions[func.name] = func
        return func

    def add_global(self, var: GlobalVar) -> GlobalVar:
        """Register *var* in this module."""
        self.globals[var.name] = var
        return var

    def int_const(self, ty: IntType, value: int) -> IntConst:
        """Return the interned constant *value* of type *ty*."""
        key = (ty.bits, ty.signed, value)
        found = self._int_consts.get(key)
        if found is None:
            found = IntConst(ty, value)
            self._int_consts[key] = found
        return found

    def bool_const(self, ty: BoolType, value: bool) -> BoolConst:
        """Return the interned boolean constant *value*."""
        found = self._bool_consts.get(value)
        if found is None:
            found = BoolConst(ty, value)
            self._bool_consts[value] = found
        return found
