"""The syntax tree.

Nodes record the span of source they came from, so that every later stage can
report against the text the user wrote.
"""

from dataclasses import dataclass, field
from enum import Enum

from ..source.location import Span


class BlockStyle(Enum):
    """Which of the two notations a block is written in."""

    LAYOUT = "layout"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class Node:
    """Base of every syntax tree node."""

    span: Span


# -- attributes ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AttrValue(Node):
    """A value given as an attribute argument."""


@dataclass(frozen=True, slots=True)
class AttrInt(AttrValue):
    """An integer attribute argument."""

    value: int


@dataclass(frozen=True, slots=True)
class AttrString(AttrValue):
    """A string attribute argument."""

    value: str


@dataclass(frozen=True, slots=True)
class AttrBool(AttrValue):
    """A boolean attribute argument."""

    value: bool


@dataclass(frozen=True, slots=True)
class AttrName(AttrValue):
    """A bare name used as an attribute argument, such as a variant selector."""

    value: str


@dataclass(frozen=True, slots=True)
class AttrArg(Node):
    """One argument of an attribute; ``name`` is set for a named argument."""

    value: AttrValue
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Attribute(Node):
    """One attribute, with its arguments."""

    name: str
    args: tuple[AttrArg, ...] = ()
    #: Span of the name alone, for pointing at it in a diagnostic.
    name_span: Span = field(default_factory=lambda: Span(-1, -1))


# -- types and expressions -----------------------------------------------------

@dataclass(frozen=True, slots=True)
class TypeRef(Node):
    """A reference to a type by name."""

    name: str


@dataclass(frozen=True, slots=True)
class Expr(Node):
    """Base of every expression."""


@dataclass(frozen=True, slots=True)
class IntLit(Expr):
    """An integer literal.

    ``type_name`` is the type the literal named with its suffix, if it named
    one.  Without it the literal takes its type from the context it appears in.
    """

    value: int
    type_name: str | None = None


@dataclass(frozen=True, slots=True)
class BoolLit(Expr):
    """A boolean literal."""

    value: bool


@dataclass(frozen=True, slots=True)
class StringLit(Expr):
    """A string literal, already decoded."""

    value: str


@dataclass(frozen=True, slots=True)
class NameRef(Expr):
    """A reference to something by name."""

    name: str


# -- statements ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Stmt(Node):
    """Base of every statement."""


@dataclass(frozen=True, slots=True)
class ReturnStmt(Stmt):
    """A ``return``, or the bare final expression that stands for one."""

    value: Expr | None
    #: Whether the ``return`` keyword was actually written.
    explicit: bool


@dataclass(frozen=True, slots=True)
class ExprStmt(Stmt):
    """An expression evaluated for its effect."""

    value: Expr


@dataclass(frozen=True, slots=True)
class VarDef(Stmt):
    """A variable definition, at the top level or inside a block.

    The colon is always written.  ``type`` is what follows it, and is ``None``
    where the type is to be taken from the value instead.  There is no form
    without a value, so ``value`` is never ``None`` in a tree that parsed.
    """

    name: str
    name_span: Span
    type: TypeRef | None
    value: Expr
    #: Whether the definition said the variable may be changed.
    mutable: bool = False
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None


@dataclass(frozen=True, slots=True)
class AssignStmt(Stmt):
    """An assignment to a variable that already exists."""

    name: str
    name_span: Span
    value: Expr


@dataclass(frozen=True, slots=True)
class Block(Node):
    """A sequence of statements in one of the two notations."""

    style: BlockStyle
    stmts: tuple[Stmt, ...] = ()


# -- definitions ---------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Param(Node):
    """One parameter of a function."""

    name: str
    type: TypeRef


@dataclass(frozen=True, slots=True)
class FuncDef(Node):
    """A function definition."""

    name: str
    name_span: Span
    params: tuple[Param, ...]
    ret_type: TypeRef
    body: Block | None
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None


type Definition = FuncDef | VarDef


@dataclass(frozen=True, slots=True)
class SourceUnit(Node):
    """Everything the parser found in one source file."""

    path: str
    items: tuple[Definition, ...] = ()
