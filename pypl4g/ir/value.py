"""IR values.

An instruction *is* the value it defines, so there is no side table mapping names
to definitions and a match on an operand is directly a match on the instruction
that produced it.  Values have identity, never structural equality: two distinct
constants of the same type are two references, and treating them as equal would
break every use list.
"""

from __future__ import annotations

from ..source.location import INVALID_SPAN, Span
from .types import (ArrayType, BoolType, EnumType, FloatType, IntType,
                    ResultType, Type)


class Value:
    """Base of everything that can appear as an operand."""

    __slots__ = ("ty", "name_hint", "name_span")

    def __init__(self, ty: Type, name_hint: str | None = None) -> None:
        self.ty = ty
        self.name_hint = name_hint
        #: Where that name is written, so that a pass removing the value can
        #: point at the name rather than at whatever produced it -- which is
        #: somewhere else on the line, and sometimes on another line entirely.
        self.name_span: Span = INVALID_SPAN

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


class Const(Value):
    """Base of every compile-time constant."""

    __slots__ = ()


class IntConst(Const):
    """An integer constant, already reduced into the range of its type."""

    __slots__ = ("value",)

    def __init__(self, ty: IntType, value: int) -> None:
        super().__init__(ty)
        self.value = value


class FloatConst(Const):
    """A floating-point constant.

    Kept as the number and not as the bits, so that what the source wrote and
    what the image holds are one rounding apart and not two.
    """

    __slots__ = ("value",)

    def __init__(self, ty: FloatType, value: float) -> None:
        super().__init__(ty)
        self.value = value


class BoolConst(Const):
    """A boolean constant."""

    __slots__ = ("value",)

    def __init__(self, ty: BoolType, value: bool) -> None:
        super().__init__(ty)
        self.value = value


class EnumConst(Const):
    """One of the named values of an enumeration.

    Kept as which one it is and not as the number it is stored as: the number is
    the representation's business, and a pass that chose a different one should
    not have to find every constant that was written down.
    """

    __slots__ = ("index",)

    def __init__(self, ty: EnumType, index: int) -> None:
        super().__init__(ty)
        self.index = index

    @property
    def member(self) -> str:
        """The name this value was written with."""
        assert isinstance(self.ty, EnumType)
        return self.ty.members[self.index]

    @property
    def number(self) -> int:
        """The number it is stored as."""
        assert isinstance(self.ty, EnumType)
        return self.ty.values[self.index]


class ArrayConst(Const):
    """An array every element of which is known while compiling.

    What a variable of an array type starts out holding, which is the one place
    an array is a constant: everywhere else it is a place, and a place is
    something the program has rather than something it knows.
    """

    __slots__ = ("elements",)

    def __init__(self, ty: ArrayType, elements: tuple[Const, ...]) -> None:
        super().__init__(ty)
        self.elements = elements


class ResultConst(Const):
    """A result whose answer, or whose absence of one, is known while compiling.

    Both halves are here for the same reason the instruction that makes one
    carries both: a result is an answer beside a truth value saying whether
    there is one, and an error that carries nothing still leaves the answer half
    a value -- one nothing may read.
    """

    __slots__ = ("answer", "failed")

    def __init__(self, ty: ResultType, answer: Const, failed: bool) -> None:
        super().__init__(ty)
        self.answer = answer
        self.failed = failed


class UndefConst(Const):
    """A value that is not defined.

    Reserved.  The language admits no undefined behaviour, so this can only ever
    appear where a pass has proved the value is never observed.
    """

    __slots__ = ()


class Argument(Value):
    """Reserved: a parameter of a function that is not an entry-block parameter."""

    __slots__ = ("index",)

    def __init__(self, ty: Type, index: int, name_hint: str | None = None) -> None:
        super().__init__(ty, name_hint)
        self.index = index


class BlockParam(Value):
    """A parameter of a basic block.

    Block parameters take the place of phi instructions: every branch carries the
    arguments for its destination, which removes the rule that phis come first,
    the interaction with critical edges, and the question of where the register
    allocator inserts its parallel copies.
    """

    __slots__ = ("block", "index")

    def __init__(self, ty: Type, block: object, index: int,
                 name_hint: str | None = None) -> None:
        super().__init__(ty, name_hint)
        self.block = block
        self.index = index
