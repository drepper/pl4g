"""The syntax tree.

Nodes record the span of source they came from, so that every later stage can
report against the text the user wrote.
"""

from dataclasses import dataclass, field
from enum import Enum, StrEnum

from ..source.location import INVALID_SPAN, Span


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
    #: The module the name was reached through, where it was written `m.Name`.
    module: str | None = None
    #: Whether what was written is a result type: `TYPE?` or `TYPE?ERROR`.
    result: bool = False
    #: The name of the error type, where one was written.  Nothing means the
    #: error carries no value beyond the fact that there is one.
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionTypeRef(Node):
    """`\N{LEFT DOUBLE PARENTHESIS}T\N{RIGHT DOUBLE PARENTHESIS}`, a set, or `\N{LEFT DOUBLE PARENTHESIS}K: V\N{RIGHT DOUBLE PARENTHESIS}`, a dictionary.

    Written the way a value of one is, so that a type and a value of it look
    alike -- which is what a parameter list and a call already do.
    """

    element: "TypeExpr"
    #: What a key stands for, where the type is a dictionary.
    value: "TypeExpr | None" = None


type TypeExpr = TypeRef | CollectionTypeRef


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


class BinaryOp(StrEnum):
    """An operator written between its two operands.

    These are the operators as the *source* has them.  What each one means is
    the semantic analysis's business; this says only what was written, which is
    why the names are the ones the specification uses rather than the ones the
    representation does.
    """

    BIT_AND = "&"
    BIT_OR = "|"
    BIT_XOR = "^"

    EQUAL = "="
    NOT_EQUAL = "\N{NOT EQUAL TO}"
    LESS = "<"
    GREATER = ">"
    LESS_EQUAL = "\N{LESS-THAN OR EQUAL TO}"
    GREATER_EQUAL = "\N{GREATER-THAN OR EQUAL TO}"

    OR_ELSE = "??"

    ALIKE = "\N{APPROXIMATELY EQUAL TO}"
    UNALIKE = "\N{NEITHER APPROXIMATELY NOR ACTUALLY EQUAL TO}"
    BELOW_OR_ALIKE = "\N{LESS-THAN OR APPROXIMATE}"
    ABOVE_OR_ALIKE = "\N{GREATER-THAN OR APPROXIMATE}"
    BELOW_NOT_ALIKE = "\N{LESS-THAN AND NOT APPROXIMATE}"
    ABOVE_NOT_ALIKE = "\N{GREATER-THAN AND NOT APPROXIMATE}"

    LOGIC_AND = "\N{LOGICAL AND}"
    LOGIC_OR = "\N{LOGICAL OR}"
    LOGIC_XOR = "\N{CIRCLED PLUS}"
    LOGIC_NAND = "\N{NAND}"
    LOGIC_NOR = "\N{NOR}"
    SHORT_AND = "and"
    SHORT_OR = "or"

    ADD = "+"
    SUBTRACT = "-"
    MULTIPLY = "\N{MULTIPLICATION SIGN}"
    DIVIDE = "\N{DIVISION SIGN}"
    REMAINDER = "%"
    SHIFT_LEFT = "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}"
    SHIFT_RIGHT = "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}"
    ROTATE_LEFT = "\N{ANTICLOCKWISE OPEN CIRCLE ARROW}"
    ROTATE_RIGHT = "\N{CLOCKWISE OPEN CIRCLE ARROW}"

    SAT_ADD = "\N{SQUARED PLUS}"
    SAT_SUB = "\N{SQUARED MINUS}"
    SAT_MUL = "\N{SQUARED TIMES}"


class UnaryOp(StrEnum):
    """An operator written before its operand."""

    BIT_NOT = "~"
    LOGIC_NOT = "\N{NOT SIGN}"


@dataclass(frozen=True, slots=True)
class FloatLit(Expr):
    """A floating-point literal, with the type its suffix named."""

    value: float
    type_name: str | None = None


@dataclass(frozen=True, slots=True)
class Call(Expr):
    """A function called with the arguments written after its name.

    Arguments are positional.  Whether they may be named as well is the one
    part of the question about calls still open, and nothing here forecloses it.
    """

    callee: Expr
    args: tuple[Expr, ...] = ()


@dataclass(frozen=True, slots=True)
class Member(Expr):
    """Something named through the thing it belongs to: `modname.NAME`."""

    base: Expr
    name: str
    name_span: Span


@dataclass(frozen=True, slots=True)
class Binary(Expr):
    """An operator applied to two operands."""

    op: BinaryOp
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Try(Expr):
    """`EXPR?`: the answer, or the whole function leaving with the error."""

    operand: Expr


@dataclass(frozen=True, slots=True)
class SetLit(Expr):
    """`\N{LEFT DOUBLE PARENTHESIS}a, b, c\N{RIGHT DOUBLE PARENTHESIS}`: a set written down."""

    elements: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class DictLit(Expr):
    """`\N{LEFT DOUBLE PARENTHESIS}k: v, k: v\N{RIGHT DOUBLE PARENTHESIS}`: a dictionary written down."""

    entries: tuple[tuple[Expr, Expr], ...]


@dataclass(frozen=True, slots=True)
class Index(Expr):
    """`c\N{LEFT DOUBLE PARENTHESIS}k\N{RIGHT DOUBLE PARENTHESIS}`: whether a set holds a key, or what a dictionary has for one."""

    base: Expr
    key: Expr


@dataclass(frozen=True, slots=True)
class Unary(Expr):
    """An operator applied to one operand."""

    op: UnaryOp
    operand: Expr


# -- statements ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Stmt(Node):
    """Base of every statement.

    Attributes are declared here rather than on each kind of statement, and are
    keyword-only so that a statement's own fields keep their places.
    """

    attrs: tuple[Attribute, ...] = field(default=(), kw_only=True)


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
class EmptyStmt(Stmt):
    """A statement that does nothing, which is what stands between or after two
    semicolons.

    It is a statement and not an absence of one, and that is the whole of why it
    exists: the last statement of a body is the body's result, so a body ending
    in a semicolon ends in something that produces no value, and a function that
    was declared to answer with one has not answered.
    """


@dataclass(frozen=True, slots=True)
class VarDef(Stmt):
    """A variable definition, at the top level or inside a block.

    The colon is always written.  ``type`` is what follows it, and is ``None``
    where the type is to be taken from the value instead.  There is no form
    without a value, so ``value`` is never ``None`` in a tree that parsed.
    """

    name: str
    name_span: Span
    type: "TypeExpr | None"
    value: Expr
    #: Whether the type said the variable may be changed.
    mutable: bool = False
    doc: str | None = None


@dataclass(frozen=True, slots=True)
class Pattern(Node):
    """What one arm of a `match` matches.

    `TYPE(NAME)` takes the alternative whose type is `TYPE` and binds its value
    to `NAME`; `TYPE` alone takes it and binds nothing, which is what an
    alternative carrying nothing is written with.  `\N{UP TACK}` in place of the type is the
    error arm of a result, whose two alternatives may name one type and so
    cannot both be said by naming one.
    """

    #: The type this arm takes -- or, where what is taken apart is an
    #: enumeration, the name of one of its values.  Nothing where the arm is
    #: the error one or the wildcard.
    type: "TypeExpr | None"
    name: str | None = None
    name_span: Span = INVALID_SPAN
    #: Whether this arm takes every alternative no earlier arm took.
    wildcard: bool = False


@dataclass(frozen=True, slots=True)
class MatchArm(Node):
    """One arm: what it matches and what it does."""

    pattern: Pattern
    body: Block


@dataclass(frozen=True, slots=True)
class IfArm(Node):
    """One arm of an `if`: what has to hold for it, and what it does.

    The condition is nothing for the arm written `else`, which is the one that
    holds when none of the others did.
    """

    condition: "Expr | None"
    body: Block


@dataclass(frozen=True, slots=True)
class If(Expr):
    """`if`, its `elif`s and its `else`.

    An expression, as `match` is: where a value is wanted of it every arm
    produces one, and there has to be an `else`, since an `if` with none has a
    way through that produces nothing.
    """

    arms: tuple[IfArm, ...]


@dataclass(frozen=True, slots=True)
class Match(Expr):
    """`match EXPR` and the arms that take its alternatives apart.

    An expression: where a value is wanted of it, every arm produces one and
    they are of one type.  Written as a statement of its own it produces none,
    and the arms are runs of statements like any other body.
    """

    subject: Expr
    arms: tuple[MatchArm, ...]


@dataclass(frozen=True, slots=True)
class AssignStmt(Stmt):
    """An assignment to a variable that already exists."""

    name: str
    name_span: Span
    value: Expr


@dataclass(frozen=True, slots=True)
class EntryAssign(Stmt):
    """`d\N{LEFT DOUBLE PARENTHESIS}k\N{RIGHT DOUBLE PARENTHESIS} \N{LEFTWARDS ARROW} v`: what a dictionary has for a key, changed."""

    base: Expr
    key: Expr
    value: Expr


@dataclass(frozen=True, slots=True)
class ModuleImport(Node):
    """A module brought into a file, and the name it is known by there.

    Written as a definition -- `let name := import("somename")` -- because that
    is what it is: a name bound to something.  It is a node of its own rather
    than a variable whose value happens to be a module, because a module is not
    a value: nothing can be computed from it and nothing of it survives into the
    program but the definitions it holds.
    """

    name: str
    name_span: Span
    #: The name as the source wrote it, before anything is looked for.
    source: str
    source_span: Span
    doc: str | None = None


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
    type: "TypeExpr"


@dataclass(frozen=True, slots=True)
class FuncDef(Node):
    """A function definition."""

    name: str
    name_span: Span
    params: tuple[Param, ...]
    #: What the function answers with, or nothing where it answers with
    #: nothing -- which is written by leaving the arrow off altogether.
    ret_type: "TypeExpr | None"
    body: Block | None
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None


class TypeKind(StrEnum):
    """Which kind of type a definition defines, which is what its separator says.

    A product holds every one of its parts at once and a sum holds exactly one
    of them, so the two are written with the two characters that already mean
    "and also" and "or else" everywhere else in the language.
    """

    PRODUCT = ";"
    SUM = "|"


@dataclass(frozen=True, slots=True)
class Field(Node):
    """One `NAME : TYPE` of a type definition: a field, or a variant."""

    name: str
    name_span: Span
    type: "TypeExpr"


@dataclass(frozen=True, slots=True)
class TypeDef(Node):
    """`type NAME = NAME : TYPE (';' | '|') ...`"""

    name: str
    name_span: Span
    kind: TypeKind
    fields: tuple[Field, ...]
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None


@dataclass(frozen=True, slots=True)
class EnumMember(Node):
    """One value of an enumeration: its name, and the number it is stored as.

    Nothing written means the compiler chooses, by the rule the specification
    gives; a number says which one outright; a name says "the one that name
    already stands for", which is how two names are given one value on purpose.
    """

    name: str
    name_span: Span
    value: "IntLit | NameRef | None" = None


@dataclass(frozen=True, slots=True)
class EnumDef(Node):
    """`enum NAME [: TYPE]` and the names of its values."""

    name: str
    name_span: Span
    members: tuple[EnumMember, ...]
    #: What a value of it occupies, where the definition said.  Nothing means
    #: the compiler chooses, and what it chooses is written in the
    #: specification.
    holder: "TypeExpr | None" = None
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None


type Definition = FuncDef | VarDef | ModuleImport | TypeDef | EnumDef


@dataclass(frozen=True, slots=True)
class SourceUnit(Node):
    """Everything the parser found in one source file."""

    path: str
    items: tuple[Definition, ...] = ()
