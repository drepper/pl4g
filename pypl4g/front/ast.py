"""The syntax tree.

Nodes record the span of source they came from, so that every later stage can
report against the text the user wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum

from ..source.location import INVALID_SPAN, Span


class BlockStyle(Enum):
    """Which of the two notations a block is written in."""

    LAYOUT = "layout"
    EXPLICIT = "explicit"
    #: Written on one line after the colon, which the end of the line closes.
    #: It is the layout notation with the indent left out, and it is told apart
    #: from it because it does not swallow the end of its own line.
    INLINE = "inline"


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
    #: What a value of it counts, written between the name and the mark that
    #: makes it a result.  It belongs to the answer and not to the result, and
    #: is written where it belongs: `u8 \N{CURRENCY SIGN}meter?E` is a result whose answer is
    #: a length.
    unit: UnitRef | None = None


@dataclass(frozen=True, slots=True)
class TupleTypeRef(Node):
    """`\N{LEFT ANGLE BRACKET}T, T\N{RIGHT ANGLE BRACKET}`: several values travelling as one."""

    members: tuple[TypeExpr, ...]


@dataclass(frozen=True, slots=True)
class CollectionTypeRef(Node):
    """`\N{LEFT DOUBLE PARENTHESIS}T\N{RIGHT DOUBLE PARENTHESIS}`, a set, or `\N{LEFT DOUBLE PARENTHESIS}K: V\N{RIGHT DOUBLE PARENTHESIS}`, a dictionary.

    Written the way a value of one is, so that a type and a value of it look
    alike -- which is what a parameter list and a call already do.
    """

    element: TypeExpr
    #: What a key stands for, where the type is a dictionary.
    value: TypeExpr | None = None
    #: Whether entries may be put in through a value of this type, which is
    #: what `mut` before it says.  It is part of the type, as a reference's
    #: `mut` is and for the reason a reference's is: a collection is a handle,
    #: so what may be done to the table is what everything holding one knows.
    mutable: bool = False


@dataclass(frozen=True, slots=True)
class ListTypeRef(Node):
    """`[T]`: however many of them there turn out to be.

    Written the way a value of one is, so that a type and a value of it look
    alike -- which is what a collection type already does.
    """

    element: TypeExpr


@dataclass(frozen=True, slots=True)
class ArrayTypeRef(Node):
    """`T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}N\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`, several values of one type, or `T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`, as many as there turn out to be.

    `shape` is one entry per dimension, in the order they were written; an entry
    is nothing where nothing was written for it, which says the type does not
    carry how many there are along that dimension.  How many entries there are
    is the rank, so `T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}3,4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` is a table and `T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET},\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` a table of no stated shape.
    """

    element: TypeExpr
    shape: tuple[Expr | None, ...] = (None,)


@dataclass(frozen=True, slots=True)
class RefTypeRef(Node):
    """`&T` and `&mut T`: a name for a place someone else holds.

    What `mut` says here is what may be done to the place, which is part of the
    type because the one who wrote the reference and the one who reads it both
    reach that place.  That is where it differs from the `mut` a variable or a
    parameter carries, which says only that the *name* may be bound to
    something else and is no part of any type.
    """

    pointee: TypeExpr
    #: Whether the place may be written through this reference.
    mutable: bool = False
    #: Whether what it names lives as long as the program, which is what
    #: `static` says and what a variable at the top level has.
    lasting: bool = False
    #: The name of the lifetime this was written with, where it was written
    #: with one: `&mut ⧖x u32` says as long as whatever else in this signature
    #: carries `⧖x`.  It stands in the slot `static` would, the two being the
    #: same question answered two ways, so at most one of them is ever set.
    lifetime: str | None = None


@dataclass(frozen=True, slots=True)
class UnitFactor(Node):
    """One base unit in a written unit, and what it is raised to."""

    name: str
    #: Whether the name was written between quotation marks, which is how a
    #: unit whose name is not an identifier is written.
    quoted: bool = False
    #: What it is raised to, negative where it stands below the line.
    exponent: int = 1


@dataclass(frozen=True, slots=True)
class UnitRef(Node):
    """`\N{CURRENCY SIGN}meter\N{DIVISION SIGN}second\N{SUPERSCRIPT TWO}`: what a number counts.

    Read left to right: `\N{MULTIPLICATION SIGN}` puts the next one above the line and `\N{DIVISION SIGN}` below it,
    and a raised number after one is what it is raised to.  What comes of it is
    a product of powers, so two units written differently are one unit where
    they come to the same thing.
    """

    factors: tuple[UnitFactor, ...]


@dataclass(frozen=True, slots=True)
class UnitTypeRef(Node):
    """A type with a unit written after it: `u64 \N{CURRENCY SIGN}meter`."""

    base: TypeExpr
    unit: UnitRef


type TypeExpr = (TypeRef | CollectionTypeRef | TupleTypeRef | ArrayTypeRef
                 | ListTypeRef | RefTypeRef | UnitTypeRef | FuncTypeRef)


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
class CharLit(Expr):
    """A character literal: the one code point it was written with."""

    value: int


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

    CONCAT = "\N{DOUBLE PLUS}"
    #: Making something of the shape on the left out of the values on the right.
    SHAPE = "\N{APL FUNCTIONAL SYMBOL RHO}"
    #: The larger and the smaller of two, element by element.
    MAX = "\N{LEFT CEILING}"
    MIN = "\N{LEFT FLOOR}"
    #: Raising something to a power.  A number written raised is this with the
    #: number on the right, which is why there is one operator and not two.
    POWER = "\N{SUPERSCRIPT LATIN SMALL LETTER N}"
    #: Whether the left divides the right with nothing left over.
    DIVIDES = "\N{DIVIDES}"
    NOT_DIVIDES = "\N{DOES NOT DIVIDE}"


class UnaryOp(StrEnum):
    """An operator written before its operand."""

    BIT_NOT = "~"
    LOGIC_NOT = "\N{NOT SIGN}"
    #: How many: of the characters of a string, of the outermost dimension of an
    #: array, of the members of a tuple, of what a set or a dictionary holds.
    LENGTH = "#"
    #: The shape of an array: how many along each of its dimensions.
    SHAPE = "\N{APL FUNCTIONAL SYMBOL RHO}"
    #: Where a walk over a list goes next, and where it came from.  A cursor is
    #: what these are written before, and a walk that stepped off either end of
    #: the list stops the program rather than pointing anywhere.
    NEXT = "\N{UPWARDS WHITE ARROW}"
    PREV = "\N{DOWNWARDS WHITE ARROW}"
    #: The largest and the smallest of what something holds.
    MAX = "\N{LEFT CEILING}"
    MIN = "\N{LEFT FLOOR}"
    #: Whether two divides it, which is whether it is even.  It is the operator
    #: above with two on the left, and is written this way because that is how
    #: the question is asked.
    DIVIDES = "\N{DIVIDES}"
    NOT_DIVIDES = "\N{DOES NOT DIVIDE}"
    #: The four roundings of a floating-point number to a whole one.  The last
    #: of them asks the processor what it is doing just now, so a function that
    #: writes it depends on something outside itself.
    FLOOR = "\N{DOWNWARDS ARROW}"
    CEILING = "\N{UPWARDS ARROW}"
    NEAREST = "\N{UP DOWN ARROW}"
    ROUNDED = "\N{UP DOWN DOUBLE ARROW}"


@dataclass(frozen=True, slots=True)
class FloatLit(Expr):
    """A floating-point literal, with the type its suffix named."""

    value: float
    type_name: str | None = None


@dataclass(frozen=True, slots=True)
class Spread(Expr):
    """`\N{ASTERISM}t` among a call's arguments: the members of a tuple, one argument each.

    It is an expression node only so that it can stand where an argument does;
    it is no expression on its own and nowhere else accepts one.
    """

    operand: Expr


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
class Hole(Expr):
    """`$a` in a macro's pattern or template.

    In a pattern it matches anything and remembers what it matched; in a template
    it is filled with what the pattern's hole of that name matched.  It is an
    expression node because that is where one stands, and it never survives
    expansion: a program the checker sees holds none.

    In the body of a macro written as a function it means the third thing, which is
    the same thing said of a value rather than of a match: `$a` puts what `a` holds
    into the tree, and `$(expr)` does it for something more than a name.
    """

    name: str
    #: What goes in, where it is more than a name.  Nothing in a pattern, where a
    #: hole is a name and matches.
    value: Expr | None = None


@dataclass(frozen=True, slots=True)
class Quote(Node):
    """What stands between the lifting marks where a macro is what reads them.

    One or more expressions separated by commas, or -- written with its contents
    indented under the opening mark -- a run of statements.  Which of the two it is
    decides what a rule may be written for and what an invocation may stand where:
    a macro whose template holds statements writes a line and not a value.
    """

    #: The expressions, where it holds expressions.
    pieces: tuple[Expr, ...] = ()
    #: The statements, where it holds those instead.
    body: Block | None = None


@dataclass(frozen=True, slots=True)
class Allocated(Expr):
    """`EXPR in NAME`: which arena what the expression takes room from comes out of.

    The same `in` a collection literal is written with, said of an expression that
    allocates: a join of two strings, or an operator whose definition takes an arena.
    What follows it is a name and not an expression, for the reason a collection's is
    -- it is a place the allocator keeps its state in, and a place is named rather
    than computed.
    """

    value: Expr
    arena: Expr


@dataclass(frozen=True, slots=True)
class Invoke(Expr):
    """`f\N{TOP LEFT CORNER}a, b\N{TOP RIGHT CORNER}`: a macro invoked, which is not a call.

    What stands between the marks is handed over as it is written and not as what
    it evaluates to, which is the whole of what a macro is for -- and the marks are
    the language's own for exactly that, so the invocation is a name applied to a
    quote rather than a notation of its own.
    """

    name: str
    name_span: Span
    arguments: Quote
    #: The module it is reached through, where the name is a path: `std` for
    #: `std.format⌜…⌝`.  Nothing for a macro this file wrote.
    through: str | None = None


@dataclass(frozen=True, slots=True)
class Rule(Node):
    """One line of a macro: what the arguments have to look like, and what the
    invocation is replaced by."""

    pattern: Quote
    template: Quote


@dataclass(frozen=True, slots=True)
class MacroDef(Node):
    """`macro NAME:` and the rules it stands for.

    Rules are tried in order and the first that matches decides, so a catch-all is
    written last.  Nothing of a macro reaches the checker: expansion runs before it
    and what it wrote is what is checked.
    """

    name: str
    name_span: Span
    rules: tuple[Rule, ...]
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None
    doc_lines: tuple[Span, ...] = ()


@dataclass(frozen=True, slots=True)
class Fresh(Expr):
    """An operator the language gives no meaning, applied to one operand or two.

    A glyph a program defined for itself.  It is a node of its own rather than a
    `Binary` with an unusual operator because the two have nothing in common past
    the shape: everything the language does with an operator of its own -- the
    unit it works out, the array it walks over, the width it demands -- is about
    an operator it knows, and there is none of it to do here.  What this means is
    the definition and nothing else.
    """

    #: The operator's name: one glyph, or an opening and a closing bracket
    #: written together, which is what a pair is called.
    glyph: str
    #: What it was applied to.  One for an operator written before its operand,
    #: two for one written between them, and for a bracket pair the value the
    #: brackets follow and then whatever was written inside them -- so a pair may
    #: have as many as it likes, which is what makes `t⟦i, j⟧` sayable.
    operands: tuple[Expr, ...]


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
class Named(Expr):
    """`.name \N{LEFTWARDS ARROW} VALUE`: an argument that says which parameter it is for.

    The dot is what says the name is a parameter's and not a variable's -- a
    leading one cannot be a member access, there being nothing on its left -- and
    it is the spelling C, Odin and Zig give the same idea in a structure's
    initializer.  What follows the arrow is the argument, written the way a
    value is bound to a name everywhere else.
    """

    name: str
    name_span: Span
    value: Expr


@dataclass(frozen=True, slots=True)
class Failure(Expr):
    """`\N{UP TACK}` and `\N{UP TACK} VALUE`: a result that has no answer, written out.

    The other way one is made is by an operation that has no answer for what it
    was given -- a division by zero, a divisor that turns out to be zero -- and
    until now that was the only way.  What it is a failure *of* is not written
    here: it is what stands where the failure stands, exactly as a value of the
    answer type written there is the successful result of the same type.
    """

    #: What the error carries, where the result's error carries something.
    value: Expr | None = None


@dataclass(frozen=True, slots=True)
class Lifted(Expr):
    """`\N{TOP LEFT CORNER}x\N{TOP RIGHT CORNER}`: what is written, lifted out of the program and into the compiler.

    What it lifts is a *type* where the brackets hold one and an *expression*
    where they hold one of those, and which it is, is a question about what the
    names mean rather than about how they are written -- so the parser keeps
    whichever reading it could build and the checker settles it.

    The brackets are what keeps the grammar context-free.  A type's name and a
    value's name are both identifiers, and a type written out in full --
    `u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`, `\N{LEFT DOUBLE PARENTHESIS}u8: u16\N{RIGHT DOUBLE PARENTHESIS}` -- is not an expression at all, so without
    something saying "a type follows" the parser would have to know what the
    names turned out to mean before it could read them.
    """

    #: The type where the brackets hold one, and nothing where they hold an
    #: expression.  A bare name is read as a type here and may turn out to be a
    #: value, which is the one thing the checker has to settle.
    written: TypeRef | None
    #: The expression where the brackets hold one.
    value: Expr | None = None


@dataclass(frozen=True, slots=True)
class Raised(Expr):
    """`EXPR\N{SUPERSCRIPT TWO}`: raised to a power written as a raised number.

    It is not `\N{SUPERSCRIPT LATIN SMALL LETTER N}` with a literal on the right, and the difference is what it
    answers with.  The exponent here is written down, so whether it is negative
    is known while compiling: a power of a non-negative number is a value of
    what was raised, and one of a negative number is a division and answers a
    result.  Written with the operator the exponent may be anything, so the
    answer is a result whatever it turns out to be.
    """

    base: Expr
    exponent: int


@dataclass(frozen=True, slots=True)
class TupleLit(Expr):
    """`\N{LEFT ANGLE BRACKET}a, b\N{RIGHT ANGLE BRACKET}`: several values written as one."""

    members: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class SetLit(Expr):
    """`\N{LEFT DOUBLE PARENTHESIS}a, b, c\N{RIGHT DOUBLE PARENTHESIS}`: a set written down.

    `arena` is the name written after `in`, where one was: which allocator the
    table comes out of.  Nothing there means the one the compiler provides.
    """

    elements: tuple[Expr, ...]
    arena: Expr | None = None


@dataclass(frozen=True, slots=True)
class DictLit(Expr):
    """`\N{LEFT DOUBLE PARENTHESIS}k: v, k: v\N{RIGHT DOUBLE PARENTHESIS}`: a dictionary written down.

    `arena` is the name written after `in`, where one was: which allocator the
    table comes out of.  Nothing there means the one the compiler provides.
    """

    entries: tuple[tuple[Expr, Expr], ...]
    arena: Expr | None = None


@dataclass(frozen=True, slots=True)
class ArrayLit(Expr):
    """`\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}a, b, c\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`: an array written down."""

    elements: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class ListLit(Expr):
    """`[a, b, c]`: a list written down."""

    elements: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Element(Expr):
    """`a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`: one element of an array, or `a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{HORIZONTAL ELLIPSIS}j\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`, a run of them.

    There is one index per dimension, separated by commas, in the order the
    shape was written in.  Which of the two things it is, is what stands between
    the brackets: a range there asks for a run and anything else for one
    element.
    """

    base: Expr
    indices: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Index(Expr):
    """`c\N{LEFT DOUBLE PARENTHESIS}k\N{RIGHT DOUBLE PARENTHESIS}`: whether a set holds a key, or what a dictionary has for one."""

    base: Expr
    key: Expr


@dataclass(frozen=True, slots=True)
class Take(Expr):
    """`\N{DAGGER}c\N{LEFT DOUBLE PARENTHESIS}k\N{RIGHT DOUBLE PARENTHESIS}`: take a key out of a collection, and answer what was there.

    Written before the lookup it undoes, and answering exactly what that lookup
    answers: what the key stood for, where the collection is a dictionary, and
    whether it was there at all, where it is a set.  So taking a key out and
    reading it are one question asked once, and a program that wants only the
    one may ignore the other -- nothing is reported for a value this leaves
    unread, which is what makes it a statement as well as an expression.
    """

    base: Expr
    key: Expr


@dataclass(frozen=True, slots=True)
class TakeAt(Expr):
    """`\N{DAGGER}l\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`: take one element out of a list, and answer what it was.

    What follows it is where the list is, so the shorter list goes back there:
    a list is where its elements are and how many there are, and one element
    fewer is a different pair of words.  An index past the end stops the
    program, which is what an index past the end of an array does.
    """

    base: Expr
    index: Expr


@dataclass(frozen=True, slots=True)
class TakeThrough(Expr):
    """`\N{DAGGER}it`: take out the element a cursor is at, and answer the cursor at the
    next one.

    Which is the same cursor: what followed has moved down into the place the
    element left, so the walk goes on from where it was.  A cursor past the last
    element is what a walk that is over looks like, and this may answer one.
    """

    operand: Expr


@dataclass(frozen=True, slots=True)
class Unary(Expr):
    """An operator applied to one operand."""

    op: UnaryOp
    operand: Expr


@dataclass(frozen=True, slots=True)
class AddressOf(Expr):
    """`&x` and `&mut x`: a reference to the place *x* names.

    What follows it is a place and not a value -- a name, an element of an
    array -- because a value has no address to give.  `mut` says the place may
    be written through what this answers with, which the place itself has to
    allow.
    """

    operand: Expr
    #: Whether what is asked for may be written through.
    mutable: bool = False


@dataclass(frozen=True, slots=True)
class Deref(Expr):
    """`r⌖`: what is at the place a reference names.

    It stands after its operand so that reaching further into what it answers
    reads left to right: `rows⌖⟦2⟧` is an element of what `rows` names, and
    needs no brackets to say so.
    """

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
    type: TypeExpr | None
    value: Expr
    #: Whether the type said the variable may be changed.
    mutable: bool = False
    doc: str | None = None
    #: Where each line of that comment is, so that something reading the
    #: comment apart -- a `\param` that names nothing, say -- can point at the
    #: line it is on.  Written with the text, by the one place that collects it.
    doc_lines: tuple[Span, ...] = ()
    #: The names after the first, where the definition takes a tuple apart.
    more: tuple[tuple[str, Span], ...] = ()


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
    type: TypeExpr | None
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

    condition: Expr | None
    body: Block
    #: Whether the condition is settled while compiling rather than while the
    #: program runs, which is what `comptime` before the keyword says.  Such an
    #: arm puts no test in the program at all: one of the arms is what the `if`
    #: turned out to be, and the others are not lowered.
    comptime: bool = False


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
    #: The names after the first, where the assignment takes a tuple apart.
    more: tuple[tuple[str, Span], ...] = ()


@dataclass(frozen=True, slots=True)
class DerefAssign(Stmt):
    """`r⌖ ← v`: the place a reference names, written.

    Assigning to the name itself binds the name to another place, which is what
    assigning to a name does everywhere; this writes what is at the place, which
    is the only thing the mark after it ever means.
    """

    target: Expr
    value: Expr


@dataclass(frozen=True, slots=True)
class ElementAssign(Stmt):
    """`a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} \N{LEFTWARDS ARROW} v`: what an array holds at one place, changed."""

    base: Expr
    indices: tuple[Expr, ...]
    value: Expr


@dataclass(frozen=True, slots=True)
class HoleAssign(Stmt):
    """`$a \N{LEFTWARDS ARROW} v` in a macro's template: an assignment to whatever the hole
    matched.

    A node of its own because an assignment says on its face what kind of place it
    writes -- a name, a field, an entry, an element -- and a hole is not known to be
    any of them until it is filled.  Expansion turns one of these into whichever
    assignment the filled target is, which is where a hole that matched something
    nothing can be assigned to is reported.
    """

    target: Expr
    value: Expr


@dataclass(frozen=True, slots=True)
class MemberAssign(Stmt):
    """`p.x ← v`: one field of a record, changed.

    Written the way the field is read, which is what every place in this
    language is: a name on the left says where, and the same words on the right
    say what is there.
    """

    base: Expr
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
class UnitDef(Stmt):
    """`unit NAME`, `unit NAME = VALUE` and `unit \N{CURRENCY SIGN}FROM \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}TO`.

    The first introduces a unit that is measured in nothing but itself.  The
    second says what one of them is in terms of others, so that a unit and the
    units it is built from are known to measure the same thing.  The third says
    a value written in one unit may stand where another is wanted, which is a
    statement about what the program means and not about arithmetic.
    """

    #: The name being introduced, and nothing for the third form, which
    #: introduces none.
    name: str
    name_span: Span
    quoted: bool = False
    #: What one of these is measured in, where the definition says.
    measured: UnitRef | None = None
    #: How many of those it is, as a fraction written over.
    scale: tuple[int, int] | None = None
    #: The two units of `unit \N{CURRENCY SIGN}FROM \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}TO`: what may stand, and where.
    stands: tuple[UnitRef, UnitRef] | None = None
    doc: str | None = None
    #: Where each line of that comment is, so that something reading the
    #: comment apart -- a `\param` that names nothing, say -- can point at the
    #: line it is on.  Written with the text, by the one place that collects it.
    doc_lines: tuple[Span, ...] = ()


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
    #: Where each line of that comment is, so that something reading the
    #: comment apart -- a `\param` that names nothing, say -- can point at the
    #: line it is on.  Written with the text, by the one place that collects it.
    doc_lines: tuple[Span, ...] = ()


@dataclass(frozen=True, slots=True)
class Range(Expr):
    """`A…B`, or `A…B…C`: the numbers from one end towards the other.

    Two ends or three, and never more: what a fourth would mean is nothing, and
    a range of ranges is not a thing this has.  `step` is nothing where only two
    were written, which means one.
    """

    start: Expr
    stop: Expr
    step: Expr | None = None


@dataclass(frozen=True, slots=True)
class ForEach(Expr):
    """``foreach NAMES [: TYPE] = EXPR BODY``: a turn for each value there is.

    `while` written with a binding instead of a condition is the same thing,
    and reaches here; which keyword was written is kept for what a message has
    to say and for nothing else.

    An expression, because a `break` may hand a value over -- and a statement
    wherever nothing wants the value, which is what an `if` already is.
    """

    name: str
    name_span: Span
    type: TypeExpr | None
    iterable: Expr
    body: Block
    #: The names after the first, where each value is taken apart.
    more: tuple[tuple[str, Span], ...] = ()
    #: Which of the two spellings was written.
    keyword: str = "foreach"
    #: What `break` and `continue` call this loop, where it was given a name.
    label: Label | None = None
    #: What runs where the loop ran out rather than being left by a `break`.
    alternative: Block | None = None
    #: Whether the turns are taken while compiling rather than while the program
    #: runs, which is what `comptime` before the keyword says.  Such a loop is
    #: written out, one body per turn -- which is what lets each turn's value be
    #: of a different type, a tuple's members being of the types they were
    #: written with.
    comptime: bool = False


@dataclass(frozen=True, slots=True)
class While(Expr):
    """``while COND BODY``: the body runs again for as long as the condition holds.

    An expression, as `if` and `match` are, because a `break` may hand a value
    over.  What the way through that runs the body no times at all produces is
    the `else` arm's value where there is one, and a failure where there is
    none -- which is why a loop with no `else` answers with a result.
    """

    condition: Expr
    body: Block
    #: What `break` and `continue` call this loop, where it was given a name.
    label: Label | None = None
    #: What runs where the condition stopped holding rather than a `break`.
    alternative: Block | None = None
    #: Whether it was written `unless`, which runs the body *until* the
    #: condition holds.  The same loop with the condition read the other way
    #: round: what it is for is the conditions that are already the negative of
    #: what a reader means, of which a cursor being at the end is one.
    until: bool = False


@dataclass(frozen=True, slots=True)
class Label(Node):
    """`§name`: what a loop is called, so that a jump can say which one it means.

    It is a node of its own rather than a bare string because it is written in
    two places -- on the loop and on the jump -- and both want to be pointed at
    by a message.
    """

    name: str


@dataclass(frozen=True, slots=True)
class Break(Stmt):
    """`break §name [VALUE]`: leave the loop of that name, which we must be in.

    The value is what the loop comes to where it is left this way.  Every
    `break` naming one loop hands over a value of one type, or none of them
    does and the loop comes to nothing.
    """

    label: Label
    value: Expr | None = None


@dataclass(frozen=True, slots=True)
class Continue(Stmt):
    """`continue §name`: begin the next turn of the loop of that name.

    What "the next turn" is, is the loop's business: for a `while` it is the
    condition again, and for a `foreach` it is the step and then the condition,
    which is exactly what reaching the end of the body would have done.
    """

    label: Label


@dataclass(frozen=True, slots=True)
class Block(Node):
    """A sequence of statements in one of the two notations."""

    style: BlockStyle
    stmts: tuple[Stmt, ...] = ()


# -- definitions ---------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Param(Node):
    """One parameter of a function.

    `mutable` says the body may bind the name to something else.  It is no part
    of the type: what the caller hands over is a value, and what the body does
    with its own name for it is the body's business.
    """

    name: str
    type: TypeExpr
    mutable: bool = False
    #: What a caller that says nothing about it gets.  Nothing where the
    #: parameter has none, in which case every caller says something.
    default: Expr | None = None
    #: Whether it stands for *all* the arguments from here on rather than one, which
    #: `⁂` before the name says.  Only a macro has one, and only as its last: what it
    #: is handed is the rest of the pieces as one piece, and how many that is, is a
    #: question the compiler answers.
    several: bool = False


@dataclass(frozen=True, slots=True)
class Capture(Node):
    """One name a lambda brings in from around it.

    Written plain, what the lambda gets is the value the name held where the
    lambda was written; written after `&`, it is the variable itself, so a
    change to it afterwards is one the lambda sees.  That is C++'s distinction
    and C++'s mark for it, and it is the same `&` a reference type is written
    with -- what it says here is what it says there.
    """

    name: str
    by_reference: bool = False


class CaptureAll(StrEnum):
    """A capture list that names no names and says what to do with all of them.

    `[=]` brings in what each name held and `[&]` brings in the names
    themselves, which are the two things a list of names says one at a time.
    Which names those are, is which ones the body reaches from outside itself.
    """

    BY_VALUE = "="
    BY_REFERENCE = "&"


@dataclass(frozen=True, slots=True)
class Lambda(Expr):
    """`\N{GREEK SMALL LETTER LAMDA} PARM: TYPE, \N{HORIZONTAL ELLIPSIS} [CAPTURES] \N{RIGHTWARDS ARROW} TYPE` and a body: a function written
    where a value is wanted.

    The parameter list has no parentheses round it, there being nothing before
    it for them to separate it from.  What ends it is the capture list, the
    arrow, or the body -- and none of those can be part of a parameter.
    """

    params: tuple[Param, ...]
    body: Block
    #: The names it brings in from around it.  Empty where none was written,
    #: which says it reads nothing outside itself.
    captures: tuple[Capture, ...] = ()
    #: What it answers with, or nothing where it answers with nothing -- which
    #: is written by leaving the arrow off, as a function definition does.
    ret_type: TypeExpr | None = None
    #: What `[=]` or `[&]` said, where one of them was written instead of a
    #: list of names.  The names are then worked out from the body.
    brings_in: CaptureAll | None = None
    #: What was written before the `λ`.  Only what a caller reads off the
    #: type can be said here, since that is all a value handed from one name to
    #: another still carries.
    attrs: tuple[Attribute, ...] = ()


@dataclass(frozen=True, slots=True)
class FuncTypeRef(Node):
    """`fn(u8, u8) \N{RIGHTWARDS ARROW} u8`: the type of a function written as a value.

    The parameter names are not there because a type is not a definition: what
    a caller has to know is what it takes and what it answers with, and what
    the names are is the body's business.
    """

    params: tuple[TypeExpr, ...]
    ret: TypeExpr | None = None
    #: What was written before the `fn`, which is the same thing written before
    #: the `λ` of a lambda this type can hold.
    attrs: tuple[Attribute, ...] = ()


class ClauseKind(StrEnum):
    """Which of the two places in a call a clause speaks about."""

    PRE = "pre"
    POST = "post"


@dataclass(frozen=True, slots=True)
class Clause(Node):
    """One `pre(…)` or `post(…)` of a signature, or one line of a bundle.

    What it holds is one expression, and what the expression is *over* decides
    what the clause means: over values it is a condition, evaluated and checked;
    over types it is a requirement, which asks only that it can be written.  The
    arrow says what a requirement answers and may not be written on a condition.
    """

    kind: ClauseKind
    expr: Expr
    answers: TypeExpr | None = None


@dataclass(frozen=True, slots=True)
class BundleDef(Node):
    """`bundle NAME(T', …)`: a name for a set of requirements.

    The lines of the body carry no keyword, everything in a bundle being a
    requirement, so they are held as clauses whose kind is `pre` and whose spans
    are the lines themselves.
    """

    name: str
    name_span: Span
    params: tuple[str, ...]
    param_spans: tuple[Span, ...]
    clauses: tuple[Clause, ...]
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None
    doc_lines: tuple[Span, ...] = ()


@dataclass(frozen=True, slots=True)
class FuncDef(Node):
    """A function definition."""

    name: str
    name_span: Span
    params: tuple[Param, ...]
    #: What the function answers with, or nothing where it answers with
    #: nothing -- which is written by leaving the arrow off altogether.
    ret_type: TypeExpr | None
    body: Block | None
    #: The `pre` and `post` clauses, in the order written.
    clauses: tuple[Clause, ...] = ()
    #: Whether `comptime` stands before the `fn`, which says *when* the function
    #: exists and nothing about what it computes: one marked so is installed before
    #: expansion, for the macros to call, and again in the ordinary way for the
    #: program -- unless what it takes or answers is a piece of the program, which
    #: nothing at run time may hold.
    at_compile_time: bool = False
    #: Whether it is a macro rather than a function: invoked with the lifting marks,
    #: handed the pieces of the program written between them, and answering the piece
    #: that replaces the invocation.  A macro is a `comptime` function that also
    #: rewrites, so this says which of the two a definition is and `at_compile_time`
    #: says when it exists -- which is the same answer for both.
    is_macro: bool = False
    #: The type the definition is named inside, where its name is a path: `held`
    #: for `fn Walk.next`, and nothing for an ordinary definition.  The name
    #: itself is the part after the dot, so everything that reads a name reads the
    #: same thing whichever this is.
    held: str | None = None
    #: And where that part of the name is, for a message about the type rather
    #: than about the function.
    held_span: Span | None = None
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None
    #: Where each line of that comment is, so that something reading the
    #: comment apart -- a `\param` that names nothing, say -- can point at the
    #: line it is on.  Written with the text, by the one place that collects it.
    doc_lines: tuple[Span, ...] = ()


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
    type: TypeExpr


@dataclass(frozen=True, slots=True)
class TypeDef(Node):
    """`type NAME = NAME : TYPE (';' | '|') ...`"""

    name: str
    name_span: Span
    kind: TypeKind
    fields: tuple[Field, ...]
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None
    #: Where each line of that comment is, so that something reading the
    #: comment apart -- a `\param` that names nothing, say -- can point at the
    #: line it is on.  Written with the text, by the one place that collects it.
    doc_lines: tuple[Span, ...] = ()


@dataclass(frozen=True, slots=True)
class EnumMember(Node):
    """One value of an enumeration: its name, and the number it is stored as.

    Nothing written means the compiler chooses, by the rule the specification
    gives; a number says which one outright; a name says "the one that name
    already stands for", which is how two names are given one value on purpose.
    """

    name: str
    name_span: Span
    value: IntLit | NameRef | None = None


@dataclass(frozen=True, slots=True)
class EnumDef(Node):
    """`enum NAME [: TYPE]` and the names of its values."""

    name: str
    name_span: Span
    members: tuple[EnumMember, ...]
    #: What a value of it occupies, where the definition said.  Nothing means
    #: the compiler chooses, and what it chooses is written in the
    #: specification.
    holder: TypeExpr | None = None
    attrs: tuple[Attribute, ...] = ()
    doc: str | None = None
    #: Where each line of that comment is, so that something reading the
    #: comment apart -- a `\param` that names nothing, say -- can point at the
    #: line it is on.  Written with the text, by the one place that collects it.
    doc_lines: tuple[Span, ...] = ()


type Definition = (FuncDef | VarDef | ModuleImport | TypeDef | EnumDef
                   | UnitDef | BundleDef | MacroDef)


@dataclass(frozen=True, slots=True)
class SourceUnit(Node):
    """Everything the parser found in one source file."""

    path: str
    items: tuple[Definition, ...] = ()
