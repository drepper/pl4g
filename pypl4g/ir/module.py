"""The module: everything one compilation produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..source.location import INVALID_SPAN, Span
from .decisions import DecisionLog
from .function import Function, Linkage
from .types import (ArrayType, BoolType, CHAR, CharType, EnumType, FloatType,
                    IntType, MEM,
                    PtrType, ResultType, Type,
                    TypeContext)
from .value import (ArrayConst, BoolConst, CharConst, Const, EnumConst,
                    FloatConst, IntConst, ResultConst, Value)


class GlobalVar(Value):
    """A variable that exists for as long as the program does.

    It is a *value* of pointer type, not a value of the type it holds: naming
    one yields its address, and reading it is a load.  That is what keeps every
    access to memory visible in the dataflow graph rather than implied by a
    name.
    """

    __slots__ = ("name", "value_type", "linkage", "initializer", "span", "module",
                 "exported", "system_layout")

    def __init__(self, name: str, value_type: Type, ptr_type: Type,
                 initializer: Value | None = None,
                 linkage: Linkage = Linkage.INTERNAL,
                 span: Span = INVALID_SPAN, module: str = "",
                 exported: bool = False, system_layout: bool = False,
                 name_span: Span = INVALID_SPAN) -> None:
        super().__init__(ptr_type, name)
        self.name = name
        #: The type of what the variable holds, not of the variable itself.
        self.value_type = value_type
        self.linkage = linkage
        #: A variable is always given a value where it is defined.
        self.initializer = initializer
        self.span = span
        # `name_span` comes from Value, where every named thing keeps where its
        # name is written; what is set here is the definition's own extent.
        self.name_span = name_span if name_span.is_valid else span
        self.module = module
        #: Whether a file importing this module may name it, which is a
        #: different question from whether the image offers the symbol.
        self.exported = exported
        #: Whether it has to be laid out the way the system's own compilers
        #: would lay it out.  The specification leaves the compiler free
        #: otherwise, and a definition something outside the image reads gives
        #: that freedom up.
        self.system_layout = system_layout

    @property
    def mutable(self) -> bool:
        """Whether the program may change it.

        It is read from the pointer rather than stored beside it, because that
        is where the language puts it: ``mut`` qualifies the type, and the type
        of a variable in memory is a pointer.
        """
        return isinstance(self.ty, PtrType) and self.ty.mutable

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
    #: What the compiler decided about this program, as opposed to what it
    #: reported.  It travels with the module because every stage has the module
    #: and any of them may decide something.
    decisions: DecisionLog = field(default_factory=DecisionLog)
    _int_consts: dict[tuple[int, bool, int], IntConst] = field(default_factory=dict)
    _float_consts: dict[tuple[int, bytes], FloatConst] = field(default_factory=dict)
    _bool_consts: dict[bool, BoolConst] = field(default_factory=dict)
    _array_consts: dict[tuple[int, tuple[int, ...]], ArrayConst] = field(
        default_factory=dict, repr=False)
    _result_consts: dict[tuple[int, int, bool], ResultConst] = field(
        default_factory=dict)
    _enum_consts: dict[tuple[int, int], EnumConst] = field(default_factory=dict)
    _char_consts: dict[int, CharConst] = field(default_factory=dict)

    def add_function(self, func: Function, key: str | None = None) -> Function:
        """Register *func* in this module.

        The key is the name unless something says otherwise.  Two modules may
        each hold a function of one name, so what brought them in gives a key
        that tells them apart; the name itself stays what the source wrote.
        """
        self.functions[key if key is not None else func.name] = func
        return func

    def add_global(self, var: GlobalVar, key: str | None = None) -> GlobalVar:
        """Register *var* in this module, by the same rule as a function."""
        self.globals[key if key is not None else var.name] = var
        return var

    def int_const(self, ty: IntType, value: int) -> IntConst:
        """Return the interned constant *value* of type *ty*."""
        key = (ty.bits, ty.signed, value)
        found = self._int_consts.get(key)
        if found is None:
            found = IntConst(ty, value)
            self._int_consts[key] = found
        return found

    def float_const(self, ty: FloatType, value: float) -> FloatConst:
        """Return the interned constant *value* of type *ty*.

        Interned by the bits and not by the number, so that the two zeroes stay
        two constants: they compare equal and are not the same value.
        """
        import struct

        key = (ty.bits, struct.pack("<d", value))
        found = self._float_consts.get(key)
        if found is None:
            found = FloatConst(ty, value)
            self._float_consts[key] = found
        return found

    def char_const(self, value: int) -> CharConst:
        """Return the interned constant naming the code point *value*."""
        found = self._char_consts.get(value)
        if found is None:
            found = CharConst(CHAR, value)
            self._char_consts[value] = found
        return found

    def enum_const(self, ty: EnumType, index: int) -> EnumConst:
        """Return the interned constant naming one value of an enumeration."""
        key = (id(ty), index)
        found = self._enum_consts.get(key)
        if found is None:
            found = EnumConst(ty, index)
            self._enum_consts[key] = found
        return found

    def result_const(self, ty: ResultType, answer: Const,
                     failed: bool = False) -> ResultConst:
        """Return the interned result constant with this answer.

        Interned by the answer's identity, which is enough because every
        constant that can be an answer is itself interned.
        """
        key = (id(ty), id(answer), failed)
        found = self._result_consts.get(key)
        if found is None:
            found = ResultConst(ty, answer, failed)
            self._result_consts[key] = found
        return found

    def array_const(self, ty: ArrayType,
                    elements: Sequence[Const]) -> ArrayConst:
        """Return the interned array constant with these elements."""
        key = (id(ty), tuple(id(e) for e in elements))
        found = self._array_consts.get(key)
        if found is None:
            found = ArrayConst(ty, tuple(elements))
            self._array_consts[key] = found
        return found

    def bool_const(self, ty: BoolType, value: bool) -> BoolConst:
        """Return the interned boolean constant *value*."""
        found = self._bool_consts.get(value)
        if found is None:
            found = BoolConst(ty, value)
            self._bool_consts[value] = found
        return found
