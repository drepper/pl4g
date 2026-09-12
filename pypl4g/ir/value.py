"""IR values.

An instruction *is* the value it defines, so there is no side table mapping names
to definitions and a match on an operand is directly a match on the instruction
that produced it.  Values have identity, never structural equality: two distinct
constants of the same type are two references, and treating them as equal would
break every use list.
"""

from .types import BoolType, IntType, Type


class Value:
    """Base of everything that can appear as an operand."""

    __slots__ = ("ty", "name_hint")

    def __init__(self, ty: Type, name_hint: str | None = None) -> None:
        self.ty = ty
        self.name_hint = name_hint

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


class BoolConst(Const):
    """A boolean constant."""

    __slots__ = ("value",)

    def __init__(self, ty: BoolType, value: bool) -> None:
        super().__init__(ty)
        self.value = value


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
