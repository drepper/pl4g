"""Semantic analysis and lowering to the IR.

Definitions need not be processed in order: every top-level definition is
collected first and only then is any body checked, which is what lets the whole
compilation be parallelized and what makes a forward reference legal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine, Expectation
from ..front import ast
from ..front.token import (BOTTOM_GLYPH, BUILTIN_GLYPH, EMPTY_ARENA_NAME,
                          WRAP_NAME,
                          HEAP_NAME, TOLERANCE_DEFAULT,
                          TOLERANCE_NAME, WILDCARD_NAME)
from ..ir.builder import IRBuilder
from ..ir.layout import DataLayout, stride_of
from ..ir.inst import (BinaryInst, BinOp, CastInst, CastKind, CmpPred,
                       ExtractInst, FrameInst, Instruction, UnOp)
from . import tables
from ..ir.function import (DEFAULT_CCONV, SYSTEM_CCONV, BasicBlock, FuncAttrs,
                           Function,
                           InlineHint,
                           Linkage, SpecialKind)
from ..ir.module import GlobalVar, Module
from ..ir.types import (ARENA, ArrayType, BOOL, BoolType, BUILTIN_TYPES,
                        DictType,
                        ERROR, EnumType,
                        U64,
                        F64,
                        FloatType, IntType, MEM, ProductType, ResultType,
                        SetType, SumType, TupleType, Type, VecType, VOID)
from .modules import (ImportCycle, LoadedModule, ModuleNotFound, ModuleRegistry,
                      base_name)
from ..ir.value import (Const, EnumConst, FloatConst, IntConst, UndefConst,
                        Value)
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
class _ArmPlan:
    """One arm to run: its body, the block it runs in, and what it binds.

    A `match` over a result binds the answer; nothing else binds anything.  The
    block is nothing where the arm is the whole of what runs, which is what an
    arm taking every alternative comes to.
    """

    body: ast.Block
    block: BasicBlock | None = None
    binds: tuple[str, Span, Span, Value, Type] | None = None


@dataclass(slots=True)
class _NamedType:
    """A type a program defined, and what it turned out to be.

    What it is made of is worked out on first ask rather than where it is
    written, so that one definition may name another written below it.  The
    `resolving` flag is what catches a type that reaches itself: a value of such
    a type would have to hold a value of it, and there is no way to write the
    indirection that makes that finite.
    """

    name: str
    node: ast.TypeDef | ast.EnumDef
    origin: str
    exported: bool = False
    ty: Type | None = None
    resolving: bool = False


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


def _assigned_in(block: ast.Block) -> list[str]:
    """Every name assigned anywhere in *block*, in the order they appear.

    A definition is not an assignment: it binds a new name, which the block it
    is in owns and nothing outside it can be standing for.  What is looked
    through is everything that holds statements -- a nested loop, the arms of
    an `if` or a `match` -- because a name assigned there is assigned by this
    loop just the same.
    """
    found: list[str] = []
    _collect_assigned(block, found)
    return found


def _collect_assigned(block: ast.Block, into: list[str]) -> None:
    """Add to *into* every name *block* assigns, looking through nested bodies."""
    for stmt in block.stmts:
        match stmt:
            case ast.AssignStmt():
                into.append(stmt.name)
                into.extend(name for name, _ in stmt.more)
            case ast.ExprStmt(value=(ast.While() | ast.ForEach()) as looped):
                _collect_assigned(looped.body, into)
                if looped.alternative is not None:
                    _collect_assigned(looped.alternative, into)
            case ast.ExprStmt(value=ast.If() as asked):
                for arm in asked.arms:
                    _collect_assigned(arm.body, into)
            case ast.ExprStmt(value=ast.Match() as matched):
                for arm in matched.arms:
                    _collect_assigned(arm.body, into)
            case _:
                pass


def _lets_go_of(found: ArrayType, wanted: ArrayType) -> bool:
    """Whether an array of *found* stands where one of *wanted* is asked for.

    A dimension the wanted type does not state takes whatever the one it is
    given has; a dimension it does state has to be the one it states, and a
    dimension neither states is already the same question.  So what may be let
    go of is a length, never a length for a different length -- a table of three
    columns is not a table of four however little either says about its rows.
    """
    if found.element is not wanted.element or found.rank != wanted.rank:
        return False
    return all(theirs is None or theirs == ours
               for ours, theirs in zip(found.shape, wanted.shape))


def _spread_out(at: int, shape: tuple[int, ...]) -> tuple[int, ...]:
    """Which element of each dimension the *at*-th of the run is.

    Row-major, so the last dimension moves fastest -- the order an array written
    down is filled in, and the order its elements lie in.
    """
    found: list[int] = []
    left = at
    for along in reversed(shape):
        found.append(left % along)
        left //= along
    return tuple(reversed(found))


def _says_its_type(expr: ast.Expr) -> bool:
    """Whether an expression says on its own what type it has.

    A literal does, where it carries a suffix.  Anything else has to be lowered
    before the question can be answered, which is what a variable with no type
    written now does.
    """
    match expr:
        case ast.IntLit() | ast.FloatLit():
            return expr.type_name is not None
        case ast.BoolLit() | ast.StringLit():
            return True
        case _:
            return False


def _can_be_a_key(ty: Type) -> bool:
    """Whether a value of *ty* may be a key of a set or a dictionary.

    A key is hashed and then compared, so a type that can be one is a type `=`
    is defined on and answers exactly: the integers, truth values and
    enumerations.  Floating point is left out on purpose -- two values that
    stand for one number may be two values, a not-a-number is equal to nothing
    including itself, and the two zeroes are equal and hash differently, so
    every one of the three properties a key wants fails.
    """
    from ..ir.types import BoolType

    return isinstance(ty, (IntType, BoolType, EnumType))


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

#: Moving bits sideways, with the signed reading first: only the right shift
#: differs between the two, bringing in copies of the sign rather than zeroes.
_SHIFTS: Final[dict[ast.BinaryOp, tuple[BinOp, BinOp]]] = {
    ast.BinaryOp.SHIFT_LEFT: (BinOp.SHL, BinOp.SHL),
    ast.BinaryOp.SHIFT_RIGHT: (BinOp.ASHR, BinOp.LSHR),
    ast.BinaryOp.ROTATE_LEFT: (BinOp.ROTL, BinOp.ROTL),
    ast.BinaryOp.ROTATE_RIGHT: (BinOp.ROTR, BinOp.ROTR),
}

#: The comparisons that put the two values in an order.  Ordering is defined on
#: numbers; equality is defined on anything whose values can be told apart.
_ORDERINGS: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.LESS, ast.BinaryOp.GREATER,
    ast.BinaryOp.LESS_EQUAL, ast.BinaryOp.GREATER_EQUAL))

#: The approximate comparisons, and how each is asked.  Every one of them is a
#: subtraction, a comparison of what came of it against the tolerance, and for
#: two of them the magnitude in between: the first field says which way round
#: the subtraction goes, the second whether the magnitude is taken, and the
#: third which question is asked of the tolerance.
#:
#: `a ≅ b` is |a-b| ≤ t, and `a ≇ b` is the same question answered the other
#: way.  `a ⪅ b` is "below it or alike", which is a-b ≤ t with no magnitude: a
#: that is far below b answers a large negative difference, which is below the
#: tolerance as it should be.  `a ⪉ b` is "below it and not alike", which is that
#: question turned round with the operands exchanged.
_APPROXIMATE: Final[dict[ast.BinaryOp, tuple[bool, bool, CmpPred]]] = {
    ast.BinaryOp.ALIKE: (False, True, CmpPred.SLE),
    ast.BinaryOp.UNALIKE: (False, True, CmpPred.SGT),
    ast.BinaryOp.BELOW_OR_ALIKE: (False, False, CmpPred.SLE),
    ast.BinaryOp.ABOVE_OR_ALIKE: (True, False, CmpPred.SLE),
    ast.BinaryOp.BELOW_NOT_ALIKE: (True, False, CmpPred.SGT),
    ast.BinaryOp.ABOVE_NOT_ALIKE: (False, False, CmpPred.SGT),
}

#: The two comparisons that ask whether two values are the one value.  On a
#: floating-point value that is a question worth warning about.
_EXACT_ON_FLOATS: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.EQUAL, ast.BinaryOp.NOT_EQUAL))

#: The operators that can be asked of a whole run of elements at once.  Every
#: one of them is the same question of each element and of none of the others,
#: so asking it of all of them together answers what asking it of each would --
#: which is what a machine with registers holding several values does in one
#: instruction.
#:
#: Dividing is not among them, and neither is taking a remainder: those two are
#: the operations with no answer for some pairs, so what they answer is a result
#: rather than a number, and a result of a run would have to say which element
#: had none.  Neither is either of the two that do not work out both sides:
#: which side is worked out is what they are about, and over a run there is no
#: such thing as which side.
_ALL_AT_ONCE: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.BIT_AND, ast.BinaryOp.BIT_OR, ast.BinaryOp.BIT_XOR,
    ast.BinaryOp.ADD, ast.BinaryOp.SUBTRACT, ast.BinaryOp.MULTIPLY,
    ast.BinaryOp.SAT_ADD, ast.BinaryOp.SAT_SUB, ast.BinaryOp.SAT_MUL,
    ast.BinaryOp.EQUAL, ast.BinaryOp.NOT_EQUAL,
    ast.BinaryOp.LESS, ast.BinaryOp.GREATER,
    ast.BinaryOp.LESS_EQUAL, ast.BinaryOp.GREATER_EQUAL,
    ast.BinaryOp.LOGIC_AND, ast.BinaryOp.LOGIC_OR, ast.BinaryOp.LOGIC_XOR,
    ast.BinaryOp.LOGIC_NAND, ast.BinaryOp.LOGIC_NOR,
    ast.BinaryOp.SHIFT_LEFT, ast.BinaryOp.SHIFT_RIGHT,
    ast.BinaryOp.ROTATE_LEFT, ast.BinaryOp.ROTATE_RIGHT))

#: The same, written before their operand.
_ALL_AT_ONCE_UNARY: Final[frozenset[ast.UnaryOp]] = frozenset((
    ast.UnaryOp.LOGIC_NOT, ast.UnaryOp.BIT_NOT))

#: What each operator means where it is written inside `⎕wrap`.  Every one of them
#: answers with the low bits of what it came to, whatever it came to -- so the
#: ones that would have stopped the program do not, and the ones that would have
#: stopped at the end of the type do not either.
#:
#: The saturating three are here for the second of those reasons, which is the
#: part worth saying out loud: `⨁` inside a wrap is an addition that wraps and not
#: one that saturates.  A wrap says what every operator inside it means, and an
#: operator that meant something else would make that untrue of the one place a
#: reader most needs it to hold.
#:
#: Dividing and taking a remainder are not here.  Neither of them can go past
#: the end of a type by arithmetic -- what they have is a pair with no answer at
#: all, a divisor of zero -- so there is nothing about them for a wrap to say.
_WRAPPED: Final[dict[BinOp, BinOp]] = {
    BinOp.ADD: BinOp.WRAP_ADD, BinOp.SUB: BinOp.WRAP_SUB,
    BinOp.MUL: BinOp.WRAP_MUL,
    BinOp.SAT_ADD: BinOp.WRAP_ADD, BinOp.SAT_SUB: BinOp.WRAP_SUB,
    BinOp.SAT_MUL: BinOp.WRAP_MUL,
    BinOp.SHL: BinOp.WRAP_SHL, BinOp.ASHR: BinOp.WRAP_ASHR,
    BinOp.LSHR: BinOp.WRAP_LSHR,
    BinOp.ROTL: BinOp.WRAP_ROTL, BinOp.ROTR: BinOp.WRAP_ROTR,
}

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

#: The operators a flag enumeration answers, whose values are meant to be
#: combined.  The bitwise three and nothing else: arithmetic on one would be
#: asking what the *number* is, which is the one thing the type does not say.
_ON_FLAGS: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.BIT_AND, ast.BinaryOp.BIT_OR, ast.BinaryOp.BIT_XOR))

#: What a set answers: everything in both, in either, in one and not the other,
#: and in the first and not the second.  The same four Python gives a set, and
#: written with the same characters.
_ON_SETS: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.BIT_AND, ast.BinaryOp.BIT_OR, ast.BinaryOp.BIT_XOR,
    ast.BinaryOp.SUBTRACT))

#: The operators that ask a question about a number rather than about a pattern
#: of bits, and so are defined on a floating-point value as well as on an
#: integer.  Everything left out is left out on purpose: the bitwise operators
#: and the shifts are questions about bits, which a floating-point type says the
#: value is not; the saturating ones are the ends of a range of whole numbers;
#: and what is left of a division has no one meaning for a value that is not.
_ON_FLOATS: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.ADD, ast.BinaryOp.SUBTRACT, ast.BinaryOp.MULTIPLY,
    ast.BinaryOp.DIVIDE))

_UNARY_OPS: Final[dict[ast.UnaryOp, UnOp]] = {
    ast.UnaryOp.BIT_NOT: UnOp.NOT,
}


#: What the tolerance is called in the image.  The name a program writes it by
#: is not a name an assembler or a debugger would take, so the two differ; the
#: one in the image says whose it is.
#: What memory looks like.  Every target this compiler has is an eight-byte
#: pointer, and the one thing the front end needs a layout for is how far apart
#: two elements of an array are.
_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8)

TOLERANCE_SYMBOL: Final[str] = "__pl4g_tolerance"

#: And the one the arena the compiler provides carries.
HEAP_SYMBOL: Final[str] = "__pl4g_heap"


def _tolerance(module: Module) -> GlobalVar:
    """The variable the approximate comparisons read, made once per module.

    It is a variable and not a constant of the compiler because a program may
    want another tolerance -- the right one depends on how far the numbers being
    compared have travelled, which is the program's business and not the
    language's.  APL's `⎕CT` is the same arrangement and the same default.

    Every file of a compilation gets the same one: a program has one tolerance,
    however many files it is written in.  A program that reads it nowhere drops
    it along with everything else nothing reaches.
    """
    found = module.globals.get(TOLERANCE_NAME)
    if isinstance(found, GlobalVar):
        return found
    return module.add_global(GlobalVar(
        name=TOLERANCE_SYMBOL, value_type=F64,
        ptr_type=module.types.ptr_type(F64, mutable=True),
        initializer=module.float_const(F64, TOLERANCE_DEFAULT),
        linkage=Linkage.INTERNAL), key=TOLERANCE_NAME)


def _heap(module: Module) -> GlobalVar:
    """The arena the compiler provides, made once per module.

    It starts as three zero words, which is an arena that has asked the system
    for nothing yet: the first allocation out of it is what asks.  A program
    that allocates nowhere carries neither it nor the allocator.
    """
    found = module.globals.get(HEAP_NAME)
    if isinstance(found, GlobalVar):
        return found
    return module.add_global(GlobalVar(
        name=HEAP_SYMBOL, value_type=ARENA,
        ptr_type=module.types.ptr_type(ARENA, mutable=True),
        initializer=None, linkage=Linkage.INTERNAL), key=HEAP_NAME)


@dataclass(frozen=True, slots=True)
class _Ready(ast.Expr):
    """A value already worked out, standing where an expression would.

    No parser makes one.  It exists so that an operator walked over an array can
    be lowered for one element by lowering the operator again with the element
    in place of what was written -- which means every check and every choice the
    operator makes is made once per element by the code that already makes it,
    rather than by a second copy of that code written for the walk.
    """

    value: Value


@dataclass(slots=True)
class _Loop:
    """A loop being lowered that has a name, and the two places a jump goes.

    *header* is where a turn begins, so `continue` branches there with whatever
    the next turn is to start from; *after* is where the loop ends, so `break`
    branches there.  Both take the names the loop carries, which is why a jump
    hands over what those names stand for where it stands rather than leaving
    them to be read from the header.

    *step* is what the header's own state has to become for there to be a next
    turn, which is the iterator's business and is nothing for a `while`.
    """

    label: str
    span: Span
    header: BasicBlock
    after: BasicBlock
    carried: list[_Local]
    state: tuple[Value, ...]
    step: Callable[[IRBuilder, tuple[Value, ...]], tuple[Value, ...]] | None
    #: Whether something wants what the loop comes to, which is what decides
    #: whether a `break` may hand a value over and whether it must.
    answers: bool = False
    #: Whether a handed-over value is made into a result before it travels,
    #: which it is where the loop has no `else` arm: the way through that ran
    #: the body out has nothing to hand over, and that is the failure.
    wraps: bool = False
    #: What type a `break` hands over, where one says: what was asked of the
    #: loop before anything was lowered, or what the first `break` turned out
    #: to hand over.  Every later one has to agree with it.
    handing: Type | None = None
    #: Where the first `break` that handed something over was written, for
    #: pointing at it beside one that did not.
    handed_at: Span | None = None
    #: Where the first `break` that handed nothing over was written.
    bare_at: Span | None = None
    #: Whether anything named the label, for reporting one nothing did.
    named: bool = False


@dataclass(frozen=True, slots=True)
class _Entries:
    """What a collection's entries came to, and what was wanted of them.

    They are lowered where they are written and put in the table afterwards, so
    that each is worked out once and in the order it stands in.
    """

    keys: list[Value]
    values: list[Value]
    wanted_key: Type | None
    wanted_value: Type | None


@dataclass(frozen=True, slots=True)
class _Iteration:
    """What a loop needs of the thing it takes its values from.

    The three are the one thing an iterator is, taken apart into the places a
    loop asks them: whether there is another turn, which it asks where it tests;
    what this turn gives, which it asks in the body; and what the next turn
    starts from, which it asks at the branch backwards.  A `next` answering a
    result is those three said as one value, and it is what a user-written
    iterator will answer with -- the loop built here is the loop either kind
    wants.
    """

    #: What a turn is bound to.
    element: Type
    #: What the loop carries from turn to turn, as it starts out.
    start: tuple[Value, ...]
    more: Callable[[IRBuilder, tuple[Value, ...]], Value]
    take: Callable[[IRBuilder, tuple[Value, ...]], Value]
    step: Callable[[IRBuilder, tuple[Value, ...]], tuple[Value, ...]]


class Checker:
    """Checks one program and lowers it into a module."""

    def __init__(self, module: Module, diags: DiagEngine,
                 registry: ModuleRegistry | None = None,
                 path: Path | None = None, prefix: str = "",
                 sources: SourceManager | None = None,
                 top_level: list[_Global] | None = None) -> None:
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
        #: Every type this file defines, in the order they were written, so
        #: that one nothing names is still worked out and still reported on.
        self._named_types: list[_NamedType] = []
        #: The locals whose value a `match` being lowered may carry past its
        #: arms, and about which the unread-value rule therefore says nothing
        #: while the arms are being checked.
        self._carried: set[int] = set()
        #: Whether an array stands where one of its elements is wanted, which
        #: is so while the arguments of a call to a function marked `listable`
        #: are lowered and nowhere else.
        self._listing: bool = False
        #: Whether what is being lowered stands inside `⎕wrap`, where every
        #: operator answers with the low bits of what it came to rather than
        #: stopping the program or stopping at the end of the type.  It is
        #: lexical: it says where the operator was written and nothing about the
        #: body of anything called from there.
        self._wrapping: bool = False
        #: Whether the function being lowered said it may change things that
        #: outlive the call.  Where it did not, the places that would make such
        #: a change report one instead.
        self._impure: bool = False
        #: Whether what is being lowered is what a loop comes to, which is
        #: what a `break` hands over and what an `else` arm gives.  A mismatch
        #: there is about the loop rather than about whatever the loop stands
        #: in, so it is said as one.
        self._leaving: bool = False
        #: The labelled loops this statement is inside, innermost last, which
        #: is what `break` and `continue` look their label up in.
        self._loops: list[_Loop] = []
        #: What the function now being lowered answers with, which is what `?`
        #: has to agree with: it leaves the function carrying an error, so the
        #: function must be one that can carry it.
        self._answering: Type | None = None
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
        # Three passes over the definitions, because each needs what the one
        # before it settled.  The imports come first, since a type may be one
        # another module defines; then every type name, so that a definition may
        # name one written below it; then what the types are made of; and only
        # then the functions and the variables, whose signatures name types.
        for unit in units:
            self._module.source_paths.append(unit.path)
            for item in unit.items:
                if isinstance(item, ast.ModuleImport):
                    self._collect_import(item)
        for unit in units:
            for item in unit.items:
                if isinstance(item, (ast.TypeDef, ast.EnumDef)):
                    self._collect_type(item, unit.path)
        for defined in self._named_types:
            self._resolved(defined)
        for unit in units:
            for item in unit.items:
                match item:
                    case ast.FuncDef():
                        gathered = self._collect_function(item, unit.path)
                        if gathered is not None:
                            collected.append(gathered)
                    case ast.VarDef():
                        self._collect_global(item)
                    case ast.ModuleImport() | ast.TypeDef() | ast.EnumDef():
                        pass
                    case _:
                        self._diags.internal("unknown kind of top-level definition")
        for entry in collected:
            self._lower_function(entry)
        if whole_program:
            self._check_program()
        return self._module

    # -- variables -------------------------------------------------------------

    def _provided(self, name: str) -> object | None:
        """What a name the compiler provides stands for, made on first ask.

        On first ask rather than always, so that a program that never names one
        carries nothing for it and its decision log says nothing about dropping
        it.  There is one such name so far.
        """
        found = self._top.get(name)
        if found is None and name == TOLERANCE_NAME:
            found = _tolerance(self._module)
            self._top[name] = found
        if found is None and name == HEAP_NAME:
            found = _heap(self._module)
            self._top[name] = found
        return found

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
        if name.startswith(BUILTIN_GLYPH):
            # Not "already taken": the whole shape of name is the compiler's,
            # so this is not a clash with one definition but a rule about the
            # glyph, and saying so is what tells a reader what to do about it.
            self._diags.emit(D.LANG_NAME_IS_THE_COMPILERS, span, name=name)
            return False
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
        if node.name == WILDCARD_NAME:
            self._diags.emit(D.LANG_WILDCARD_IS_NOT_DEFINED, node.span)
            return
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
        if isinstance(ty, ArrayType) and not ty.fixed:
            self._diags.emit(D.LANG_ARRAY_NO_LENGTH_AT_TOP, node.span,
                             found=ty.render())
            ty = ERROR
        if ty is None:
            ty = ERROR
        var = self._module.add_global(GlobalVar(
            name=node.name, value_type=ty,
            ptr_type=self._module.types.ptr_type(ty, mutable=node.mutable),
            initializer=initializer, linkage=linkage, span=node.span,
            name_span=node.name_span,
            exported=self._is_export(attrs),
            system_layout=any(a.name == "cdecl" for a in attrs)),
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

    def _read_module(self, path: Path, node: ast.ModuleImport) -> LoadedModule | None:
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

    def _read_unit(self, path: Path, node: ast.ModuleImport) -> ast.SourceUnit | None:
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
        """Lower something named through what it belongs to.

        A module, or an enumeration: `Colour.red` is the value of `Colour`
        called `red`.  The two are one syntax with two meanings decided by what
        the base names, which is what Go, Rust and Zig all do -- and an
        enumeration's values are written with the type in front of them so that
        two enumerations may each have a `red`.
        """
        base = expr.base
        if not isinstance(base, ast.NameRef):
            self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, expr.span,
                             name="an expression")
            return UndefConst(ERROR)
        held = self._top.get(base.name)
        if isinstance(held, _NamedType):
            return self._enum_value(held, expr)
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

    def _enum_value(self, defined: _NamedType, expr: ast.Member) -> Value:
        """The value of an enumeration written as `TYPE.NAME`."""
        ty = self._resolved(defined)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, EnumType):
            self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, expr.name_span,
                             name=expr.name, type=ty.render())
            return UndefConst(ERROR)
        index = ty.index_of(expr.name)
        if index is None:
            self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, expr.name_span,
                             name=expr.name, type=ty.render())
            return UndefConst(ERROR)
        return self._module.enum_const(ty, index)

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
            case ast.FloatLit():
                return BUILTIN_TYPES.get(expr.type_name) if expr.type_name else None
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
        if ty is ARENA:
            # An arena of one's own.  There is exactly one thing to write here,
            # and it stands for three zero words -- an arena that has asked the
            # system for nothing yet.  It is not a constant of a type the way
            # every other initializer is: an arena is a place and never a value,
            # so what is written says what the place starts out holding.
            if not (isinstance(node.value, ast.NameRef)
                    and node.value.name == EMPTY_ARENA_NAME):
                self._diags.emit(D.LANG_ARENA_STARTS_EMPTY, node.value.span,
                                 name=EMPTY_ARENA_NAME)
            return None
        if ty is ERROR:
            # The type was already reported; saying anything about the value it
            # was given would be a second message about the same mistake.
            return None
        if isinstance(ty, ResultType):
            # The only constant of a result type a program can write is the
            # successful one, since a value of the answer type written where a
            # result is wanted *is* the successful result and there is no other
            # way to write one.  So the initializer is checked against the
            # answer type and wrapped.
            answer = self._constant_value(node, ty.ok)
            if answer is None or not isinstance(answer, Const):
                return None
            return self._module.result_const(ty, answer)
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
            case ast.FloatLit():
                if not isinstance(ty, FloatType):
                    return self._wrong_initializer(node, ty, F64.render())
                named = (BUILTIN_TYPES.get(node.value.type_name)
                         if node.value.type_name else None)
                if named is not None and named is not ty:
                    return self._wrong_initializer(node, ty, named.render())
                return self._module.float_const(ty, node.value.value)
            case ast.ArrayLit() if isinstance(ty, ArrayType):
                # Every element has to be one the compiler knows, which is what
                # a variable at the top level is: bytes in the image and not
                # something a program works out.  They are gathered into one run
                # however many dimensions the shape has, since that is what they
                # are in the image.
                held = self._constant_elements(node, ty, node.value, ty.shape)
                return (None if held is None
                        else self._module.array_const(ty, held))
            case ast.Member():
                # A value of an enumeration is written `TYPE.NAME` and is known
                # while compiling, so it is a constant like any literal.
                found = self._enum_value_of(node.value)
                if found is None:
                    return None
                if found.ty is not ty:
                    return self._wrong_initializer(node, ty, found.ty.render())
                return found
            case _:
                self._diags.emit(
                    D.IMPL_UNIMPLEMENTED_FEATURE, node.value.span,
                    feature="a top-level variable whose value is not a literal")
                return None

    def _constant_elements(self, node: ast.VarDef, ty: ArrayType,
                           written: ast.ArrayLit,
                           shape: tuple[int | None, ...]
                           ) -> list[Const] | None:
        """Every element of an array written down, in the order they are laid out."""
        if len(written.elements) != shape[0]:
            self._diags.emit(D.LANG_ARRAY_WRONG_LENGTH, written.span,
                             given=len(written.elements), wanted=shape[0])
            return None
        held: list[Const] = []
        for one in written.elements:
            if len(shape) == 1:
                found = self._constant_value(replace(node, value=one), ty.element)
                if not isinstance(found, Const):
                    return None
                held.append(found)
                continue
            if not isinstance(one, ast.ArrayLit):
                self._diags.emit(D.LANG_ARRAY_NOT_WRITTEN_DEEP_ENOUGH,
                                 one.span)
                return None
            deeper = self._constant_elements(node, ty, one, shape[1:])
            if deeper is None:
                return None
            held.extend(deeper)
        return held

    def _enum_value_of(self, expr: ast.Member) -> EnumConst | None:
        """The value of an enumeration a `TYPE.NAME` names, outside a body."""
        base = expr.base
        held = self._top.get(base.name) if isinstance(base, ast.NameRef) else None
        if not isinstance(held, _NamedType):
            self._diags.emit(
                D.IMPL_UNIMPLEMENTED_FEATURE, expr.span,
                feature="a top-level variable whose value is not a literal")
            return None
        found = self._enum_value(held, expr)
        return found if isinstance(found, EnumConst) else None

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

    def _begin_expecting(self, pairs: Sequence[_Expected]) -> Expectation | None:
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
                          pairs: Sequence[_Expected]) -> bool:
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
        """Resolve a name: the innermost binding first, then the top level.

        `_` is the one name that resolves to nothing on purpose: it is where a
        value goes to be dropped, so there is nothing there to read back and
        saying it is undefined would be answering a different question.
        """
        if ref.name == WILDCARD_NAME:
            self._diags.emit(D.LANG_WILDCARD_IS_NOT_READ, ref.span)
            return None
        found = self._find_local(ref.name)
        if found is not None:
            found.read = True
            return found.value
        found_global = self._provided(ref.name)
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
                            cconv=(func_attrs.abi if func_attrs.abi is not None
                                   else DEFAULT_CCONV),
                            span=node.span, name_span=node.name_span,
                            source_path=path)
            if func_attrs.listable and not params:
                self._diags.emit(D.LANG_LISTABLE_TAKES_NOTHING, node.name_span,
                                 name=node.name)
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
        can_ignore = False
        impure = False
        listable = False
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
                case "listable":
                    listable = True
                case "impure":
                    impure = True
                case "can_ignore":
                    can_ignore = True
                case "cdecl":
                    # The one every program that means to be called from
                    # elsewhere writes.  It says "the one this system uses"
                    # without the program having to know what this architecture
                    # calls it, which is what each backend answers for.
                    abi = SYSTEM_CCONV
                    extra["variadic"] = attr.as_bool("variadic")
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
                         can_ignore=can_ignore, impure=impure,
                         listable=listable, extra=extra), linkage

    # -- types -----------------------------------------------------------------

    # -- types the program defines ---------------------------------------------

    def _collect_type(self, node: ast.TypeDef | ast.EnumDef, path: str) -> None:
        """Register one type definition without working out what it is made of.

        The name is registered in the same namespace as everything else at the
        top level, so a type and a function cannot share one: a name in this
        language stands for one thing, and which kind of thing it is is not
        something a reader should have to work out from where it is written.
        """
        if not self._declare(node.name, node.name_span, path):
            return
        attrs = self._bind_attributes(node.attrs, AttrTarget.TYPE)
        defined = _NamedType(name=node.name, node=node, origin=path,
                             exported=self._is_export(attrs))
        self._top[node.name] = defined
        self._named_types.append(defined)

    def _resolved(self, defined: _NamedType) -> Type:
        """What a defined type is made of, worked out on first ask."""
        if defined.ty is not None:
            return defined.ty
        if defined.resolving:
            self._diags.emit(D.LANG_TYPEDEF_CONTAINS_ITSELF,
                             defined.node.name_span, name=defined.name)
            defined.ty = ERROR
            return ERROR
        defined.resolving = True
        try:
            defined.ty = (self._values_of(defined)
                          if isinstance(defined.node, ast.EnumDef)
                          else self._parts_of(defined))
        finally:
            defined.resolving = False
        return defined.ty

    def _values_of(self, defined: _NamedType) -> Type:
        """The enumeration a definition makes, reporting what is wrong with it.

        A value may say which number it is stored as, or take the name of an
        earlier one and be that; what is written down nowhere the compiler
        chooses, by the rule in the specification -- one past the last for an
        ordinary enumeration, and the next power of two for one marked
        `@[flag]`, whose values are meant to be combined.
        """
        node = defined.node
        assert isinstance(node, ast.EnumDef)
        flag = any(attr.name == "flag"
                   for attr in self._bind_attributes(node.attrs, AttrTarget.TYPE))
        names: list[str] = []
        numbers: list[int] = []
        where: dict[str, Span] = {}
        taken: dict[int, str] = {}
        spoiled = False
        for member in node.members:
            if member.name in where:
                self._diags.emit(D.LANG_ENUMDEF_DUPLICATE_VALUE, member.name_span,
                                 name=member.name)
                spoiled = True
                continue
            where[member.name] = member.name_span
            number = self._number_for(member, names, numbers, taken, flag)
            if number is None:
                spoiled = True
                continue
            names.append(member.name)
            numbers.append(number)
        if spoiled or not names:
            return ERROR
        holder = self._holder_for(node, numbers)
        if holder is None:
            return ERROR
        return EnumType(tuple(names), tuple(numbers), holder, flag=flag,
                        name=defined.name, origin=defined.origin)

    def _number_for(self, member: ast.EnumMember, names: Sequence[str],
                    numbers: Sequence[int], taken: dict[int, str],
                    flag: bool) -> int | None:
        """The number one value of an enumeration is stored as.

        A number written down must be one no other value was given, since two
        values a program cannot tell apart would be two names for one thing
        written as though they were two things.  Taking another value's *name*
        says exactly that they are one thing, and is allowed for it.
        """
        match member.value:
            case ast.NameRef():
                try:
                    return numbers[list(names).index(member.value.name)]
                except ValueError:
                    self._diags.emit(D.LANG_ENUMDEF_UNKNOWN_EARLIER_VALUE,
                                     member.value.span, name=member.value.name)
                    return None
            case ast.IntLit():
                number = member.value.value
            case _:
                number = self._next_number(numbers, flag)
        held = taken.get(number)
        if held is not None:
            self._diags.emit(D.LANG_ENUMDEF_REPEATED_NUMBER, member.name_span,
                             number=str(number), name=held)
            return None
        taken[number] = member.name
        return number

    def _next_number(self, numbers: Sequence[int], flag: bool) -> int:
        """The number a value that says none is given.

        One past the last for an ordinary enumeration, starting at zero.  For
        one whose values are meant to be combined, the smallest power of two
        above every number already used, starting at one -- so that each value
        is a bit of its own however the ones before it were written.
        """
        if not flag:
            return numbers[-1] + 1 if numbers else 0
        highest = max(numbers, default=0)
        bit = 1
        while bit <= highest:
            bit <<= 1
        return bit

    def _holder_for(self, node: ast.EnumDef, numbers: Sequence[int]) -> IntType | None:
        """What holds the values: what the definition named, or the smallest
        unsigned type that holds every one of them."""
        if node.holder is None:
            return self._module.types.holder_for(max(numbers) + 1)
        named = self._resolve_type(node.holder)
        if named is ERROR:
            return None
        if not isinstance(named, IntType):
            self._diags.emit(D.LANG_ENUMDEF_HOLDER_NOT_INTEGER, node.holder.span,
                             type=named.render())
            return None
        outside = next((n for n in numbers if not named.holds(n)), None)
        if outside is not None:
            self._diags.emit(D.LANG_ENUMDEF_HOLDER_TOO_NARROW, node.holder.span,
                             type=named.render(), value=str(outside))
            return None
        return named

    def _parts_of(self, defined: _NamedType) -> Type:
        """The type a definition's parts make, reporting what is wrong with them."""
        node = defined.node
        assert isinstance(node, ast.TypeDef)
        parts: list[tuple[str, Type]] = []
        seen: dict[str, Span] = {}
        spoiled = False
        for field in node.fields:
            if field.name in seen:
                self._diags.emit(D.LANG_TYPEDEF_DUPLICATE_PART, field.name_span,
                                 name=field.name)
                spoiled = True
                continue
            seen[field.name] = field.name_span
            ty = self._resolve_type(field.type)
            if ty is VOID and node.kind is ast.TypeKind.PRODUCT:
                # A field that carries nothing leaves the product meaning what
                # it would have meant without it.  A *variant* of this type may
                # be `void`, and is how an enumeration is written.
                self._diags.emit(D.LANG_TYPEDEF_FIELD_OF_NOTHING, field.name_span,
                                 name=field.name)
                spoiled = True
                continue
            if ty is ERROR:
                spoiled = True
                continue
            if node.kind is ast.TypeKind.SUM:
                # An arm of a `match` names the type of the alternative it
                # takes, so two alternatives of one type would be two no arm
                # could choose between.  A product has no such rule: it holds
                # all of its fields and reaches each by name.
                twice = next((n for n, seen_ty in parts if seen_ty is ty), None)
                if twice is not None:
                    self._diags.emit(D.LANG_TYPEDEF_REPEATED_TYPE, field.name_span,
                                     type=ty.render())
                    spoiled = True
                    continue
            parts.append((field.name, ty))
        if not parts:
            # Every part was wrong, and each was reported where it was written.
            # A definition with no parts at all never reaches here: the parser
            # refuses one, there being nothing to read.
            return ERROR
        if spoiled:
            return ERROR
        made = tuple(parts)
        if node.kind is ast.TypeKind.SUM:
            return SumType(made, name=defined.name, origin=defined.origin)
        return ProductType(made, name=defined.name, origin=defined.origin)

    def _defined_type(self, ref: ast.TypeRef) -> Type | None:
        """The type a name stands for, where a definition gave it one."""
        if ref.module is not None:
            held = self._top.get(ref.module)
            if not isinstance(held, LoadedModule):
                self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, ref.span,
                                 name=ref.module)
                return ERROR
            found = held.exports.get(ref.name)
            if found is None:
                self._diags.emit(D.LANG_IMPORT_NOT_EXPORTED, ref.span,
                                 name=ref.name, module=ref.module)
                return ERROR
            if isinstance(found, _NamedType):
                # Already worked out: the module was checked whole before this
                # file was allowed to name anything in it.
                return found.ty if found.ty is not None else ERROR
            self._diags.emit(D.LANG_TYPE_UNKNOWN, ref.span, name=ref.name)
            return ERROR
        held = self._top.get(ref.name)
        return self._resolved(held) if isinstance(held, _NamedType) else None

    def _resolve_type(self, ref: ast.TypeExpr) -> Type:
        """Resolve a type written down, collection or name."""
        if isinstance(ref, ast.CollectionTypeRef):
            return self._collection_type(ref)
        if isinstance(ref, ast.ArrayTypeRef):
            return self._array_type(ref)
        if isinstance(ref, ast.TupleTypeRef):
            members = [self._resolve_type(m) for m in ref.members]
            if any(m is ERROR for m in members):
                return ERROR
            return self._module.types.tuple_type(members)
        return self._named_type(ref)

    def _array_type(self, ref: ast.ArrayTypeRef) -> Type:
        """Resolve `T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}N\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` or `T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`.

        How many elements there are is part of the type, so it is a number the
        compiler can read and not an expression the program works out: a type
        that depended on a value would be a different language.
        """
        element = self._resolve_type(ref.element)
        if element is ERROR:
            return ERROR
        if element is VOID:
            self._diags.emit(D.LANG_ARRAY_ELEMENT_IS_NOTHING, ref.span)
            return ERROR
        shape: list[int | None] = []
        for written in ref.shape:
            if written is None:
                shape.append(None)
                continue
            if not isinstance(written, ast.IntLit) or written.value < 0:
                self._diags.emit(D.LANG_ARRAY_LENGTH_NOT_A_NUMBER, written.span)
                return ERROR
            shape.append(written.value)
        # A dimension the type states and one it does not may stand side by
        # side.  What makes that worth having is what selecting rows of a table
        # produces: however many rows were picked, each still as wide as the
        # table was, which is `i32⟦,3⟧` and nothing else.  A value of such a type
        # carries a count for every dimension, the stated ones included, so that
        # what it is, is one thing however much of its shape the type says.
        return self._module.types.array_type(element, shape)

    def _collection_type(self, ref: ast.CollectionTypeRef) -> Type:
        """Resolve `⸨T⸩` or `⸨K: V⸩`, checking that the key can be one."""
        element = self._resolve_type(ref.element)
        if element is not ERROR and not _can_be_a_key(element):
            self._diags.emit(D.LANG_COLLECTION_KEY_NOT_HASHABLE, ref.element.span,
                             found=element.render())
            element = ERROR
        if ref.value is None:
            return (self._module.types.set_type(element) if element is not ERROR
                    else ERROR)
        value = self._resolve_type(ref.value)
        if element is ERROR or value is ERROR:
            return ERROR
        if value is VOID:
            self._diags.emit(D.LANG_COLLECTION_VALUE_IS_NOTHING, ref.value.span)
            return ERROR
        return self._module.types.dict_type(element, value)

    def _named_type(self, ref: ast.TypeRef) -> Type:
        """Resolve a type name, reporting an unknown one.

        A name that resolves to nothing stands in as the type that matches
        anything, so that the one mistake is reported once rather than again
        wherever the type would have been checked.
        """
        found = BUILTIN_TYPES.get(ref.name) if ref.module is None else None
        if found is None:
            found = self._defined_type(ref)
        if found is None:
            self._diags.emit(D.LANG_TYPE_UNKNOWN, ref.span, name=ref.name)
            return ERROR
        if found is ERROR:
            return ERROR
        if not ref.result:
            return found
        if ref.error is not None:
            # Nothing in the language makes one, so a program that wrote the
            # type could not put a value in it.  Refused as a thing the compiler
            # lacks rather than as a thing the language does not have.
            self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, ref.span,
                             feature="a result whose error carries a value")
            return ERROR
        if found is VOID:
            self._diags.emit(D.LANG_TYPE_RESULT_OF_NOTHING, ref.span)
            return ERROR
        return self._module.types.result_type(found)

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
        outer_answer, self._answering = self._answering, func.ty.ret
        outer_impure, self._impure = self._impure, func.attrs.impure
        self._push_scope()
        for index, param in enumerate(node.params):
            value = block.add_param(func.ty.params[index], param.name)
            self._bind_local(param.name, value, node.params[index].span,
                             param.mutable, is_parameter=True)
        assert node.body is not None
        self._lower_block(builder, node.body, func)
        self._pop_scope()
        self._answering = outer_answer
        self._impure = outer_impure
        if not builder.is_terminated:
            if func.ty.ret is VOID:
                builder.ret()
            else:
                self._diags.emit(D.LANG_FUNCDEF_RETURN_MISSING, node.name_span,
                                 name=func.name, type=func.ty.ret.render())
                builder.unreachable()

    def _lower_block(self, builder: IRBuilder, block: ast.Block, func: Function,
                     as_result: bool = True, wanted: Type | None = None,
                     produces: bool = False) -> Value | None:
        """Lower the statements of one block, and hand back what it comes to.

        `as_result` says whether the last statement of this block is the
        function's result.  It is for a function's body and is not for the body
        of an arm of a `match`.  `produces` says the block is an arm a value is
        wanted of, in which case its last statement has to have one.
        """
        count = len(block.stmts)
        answer: Value | None = None
        for index, stmt in enumerate(block.stmts):
            is_last = index == count - 1
            if builder.is_terminated:
                self._diags.emit(D.LANG_STMT_UNREACHABLE, stmt.span)
                return None
            if is_last and produces:
                answer = self._lower_yielding(builder, stmt, func, wanted)
                continue
            self._lower_attributed_stmt(builder, stmt, func,
                                        as_result and is_last)
        if produces and answer is None and not builder.is_terminated:
            self._diags.emit(D.LANG_MATCH_ARM_HAS_NO_VALUE, block.span)
        return answer

    def _lower_yielding(self, builder: IRBuilder, stmt: ast.Stmt, func: Function,
                        wanted: Type | None) -> Value | None:
        """Lower the last statement of an arm a value is wanted of.

        The statements that have a value are the same ones that may be the last
        of a function's body: an expression, and an assignment, which stands for
        the variable it changed.  A `return` leaves the function, so the arm
        reaches no join and owes no value.
        """
        pairs = self._expected_numbers(
            self._bind_attributes(stmt.attrs, AttrTarget.STATEMENT))
        expectation = self._begin_expecting(pairs)
        try:
            match stmt:
                case ast.ExprStmt():
                    return self._lower_into(builder, stmt.value,
                                            wanted if wanted is not None else ERROR,
                                            stmt.span) \
                        if wanted is not None \
                        else self._lower_expr(builder, stmt.value, None)
                case ast.AssignStmt():
                    found = self._lower_assignment(builder, stmt, True)
                    if found is not None and wanted is not None \
                            and found.ty is not wanted and found.ty is not ERROR:
                        self._report_mismatch(stmt.span, found.ty, wanted)
                        return UndefConst(wanted)
                    return found
                case ast.ReturnStmt():
                    self._lower_return(builder, stmt, func)
                    return None
                case _:
                    self._diags.emit(D.LANG_MATCH_ARM_HAS_NO_VALUE, stmt.span)
                    return None
        finally:
            self._end_expecting(expectation)
            if self._settle_expecting(expectation, pairs):
                self._discard_function = True

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
            case ast.EntryAssign():
                self._lower_entry_assign(builder, stmt)
            case ast.ElementAssign():
                self._lower_element_assign(builder, stmt)
            case ast.Break():
                self._lower_break(builder, stmt)
            case ast.Continue():
                self._lower_continue(builder, stmt)
            case ast.EmptyStmt():
                # Nothing to lower.  What it does is be a statement, so that a
                # body ending in a semicolon ends in one that produces no value.
                pass
            case ast.ExprStmt():
                # The value of the last statement is the function's result, which
                # is why the canonical form of the language omits the keyword.
                if is_last and func.ty.ret is not VOID:
                    value = self._lower_into(builder, stmt.value, func.ty.ret,
                                             stmt.span)
                    builder.ret(value, stmt.span)
                elif isinstance(stmt.value, ast.If):
                    # An `if` written as a statement of its own produces no
                    # value, and needs no `else` for that reason.
                    self._lower_if(builder, stmt.value, func, None, False)
                elif isinstance(stmt.value, ast.While):
                    # A loop written as a statement of its own produces no
                    # value either, so no `break` in it need hand one over.
                    self._lower_while(builder, stmt.value, func, None, False)
                elif isinstance(stmt.value, ast.ForEach):
                    self._lower_foreach(builder, stmt.value, func, None, False)
                elif isinstance(stmt.value, ast.Match):
                    # A `match` written as a statement of its own produces no
                    # value, and its arms are runs of statements like any other
                    # body.  It is the one expression that is worth writing for
                    # what it does rather than for what it comes to.
                    self._lower_match(builder, stmt.value, func, None, False)
                else:
                    self._check_value_is_used(stmt.value)
                    self._lower_expr(builder, stmt.value, None)
            case _:
                self._diags.internal("unknown statement kind in lowering")

    # -- tuples -----------------------------------------------------------------

    def _lower_tuple(self, builder: IRBuilder, expr: ast.TupleLit,
                     expected: Type | None) -> Value:
        """Lower `〈a, b〉`: several values made into one.

        A member written after `⁂` stands for several members rather than
        one, which is what joining two tuples is written with and is the same
        expansion a call's arguments get, done by the same code.  What it
        expands into is members like any other, so a tuple made this way is a
        tuple made the other way and nothing downstream knows the difference.
        """
        aim = self._aiming_at(expected)
        wanted = aim.members if isinstance(aim, TupleType) else None
        values = self._one_by_one(builder, expr.members, wanted)
        if values is None:
            return UndefConst(ERROR)
        if not values:
            # Everything written was spread, and all of it was empty.  A tuple
            # of no members is not a type this language has.
            self._diags.emit(D.LANG_SPREAD_NOTHING_LEFT, expr.span)
            return UndefConst(ERROR)
        types = [self._value_type_of(value) for value in values]
        if any(ty is ERROR for ty in types):
            return UndefConst(ERROR)
        ty = self._module.types.tuple_type(types)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        return builder.make_tuple(values, ty, expr.span)

    def _taken_apart(self, value: Value, names: Sequence[tuple[str, Span]],
                     span: Span) -> list[Type] | None:
        """The type each name of a destructuring stands for, or nothing where
        the value cannot be taken apart that way."""
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if not isinstance(ty, TupleType):
            self._diags.emit(D.LANG_TUPLE_NOT_A_TUPLE, span, found=ty.render())
            return None
        if len(ty.members) != len(names):
            self._diags.emit(D.LANG_TUPLE_WRONG_COUNT, span,
                             wanted=str(len(names)), found=ty.render(),
                             count=str(len(ty.members)))
            return None
        return list(ty.members)

    # -- sets and dictionaries --------------------------------------------------

    # -- arrays ------------------------------------------------------------

    def _lower_array(self, builder: IRBuilder, expr: ast.ArrayLit,
                     expected: Type | None) -> Value:
        """Lower `\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}a, b, c\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`: several values of one type, written down.

        The elements go into room this function holds for as long as it runs,
        and what the expression comes to is where that room is -- which is what
        a value of an array type is.  A frame and not an arena: an array whose
        type says its shape lasts exactly as long as the name does, and an arena
        that never frees would leak one for every turn of a loop.

        An array of more than one dimension is written a dimension deep: the
        outer list is the first dimension and each of its entries is the array
        of the dimensions left.  What the elements *are* is one run either way,
        so the writing goes straight into the run at the place the shape puts
        it and nothing is copied afterwards.
        """
        ty, ready = self._array_written(builder, expr, expected)
        if ty is None:
            return UndefConst(ERROR)
        if ready is not None and len(ready) != ty.count:
            # The writing is not the same width all the way along, so what was
            # lowered does not line up with the run.  `_fill` says which list
            # is the wrong length, which is what a reader needs to hear.
            ready = None
        place = builder.frame(ty, expr.span)
        if not self._fill(builder, expr, ty, place, 0, ty.shape, ready):
            return UndefConst(ERROR)
        return builder.cast(CastKind.BITCAST, place, ty, expr.span)

    def _array_written(self, builder: IRBuilder, expr: ast.ArrayLit,
                       expected: Type | None
                       ) -> tuple[ArrayType | None, list[Value] | None]:
        """What type an array written down has, and what its elements came to.

        The shape comes from what it is wanted as where that says, and from how
        deep and how wide the writing is where it does not.  A shape written on
        both sides has to agree: an array of a different length is an array of a
        different type, and nothing is padded out or dropped.

        The elements come back only where they had to be lowered to answer the
        question, which is where nothing says what type they have.  Everywhere
        else the type says it and they are lowered once, by `_fill`, into the
        type it says -- which is what lets a literal with no suffix stand as an
        element.
        """
        aim = self._aiming_at(expected)
        wanted = aim if isinstance(aim, ArrayType) else None
        if wanted is not None and wanted.fixed:
            if len(expr.elements) != wanted.shape[0]:
                self._diags.emit(D.LANG_ARRAY_WRONG_LENGTH, expr.span,
                                 given=len(expr.elements), wanted=wanted.shape[0])
                return None, None
            return wanted, None
        shape, inner = self._shape_written(expr)
        if wanted is not None:
            # An array of no stated length is wanted, which happens where one is
            # handed to a parameter or given to a name of such a type.  What the
            # writing makes is the array with the length in it, which the caller
            # is the one to let go of; the elements are its element type, and
            # asking them instead would ask before there is anywhere to put the
            # answer.  A rank that does not match is left to the caller too,
            # since what it has to say is that the two types are not the same.
            return self._module.types.array_type(wanted.element, shape), None
        # Nothing says what these are, so they say it themselves.  They are
        # lowered here rather than twice: `_fill` writes them into the run in
        # the order `_shape_written` gave them, which is the order they lie in.
        values: list[Value] = []
        element = self._one_type(builder, inner, None, values)
        if element is ERROR:
            return None, None
        return self._module.types.array_type(element, shape), values

    def _shape_written(self, expr: ast.ArrayLit
                       ) -> tuple[tuple[int | None, ...], tuple[ast.Expr, ...]]:
        """How deep and how wide an array written down is, and what is innermost.

        The first entry of each list is what says how deep the writing goes;
        a list whose entries are not all the same depth is reported where the
        elements are checked against one another, which is where every other
        disagreement between them is reported.
        """
        shape: list[int | None] = [len(expr.elements)]
        inner: tuple[ast.Expr, ...] = expr.elements
        while inner and all(isinstance(e, ast.ArrayLit) for e in inner):
            first = inner[0]
            assert isinstance(first, ast.ArrayLit)
            shape.append(len(first.elements))
            spread: list[ast.Expr] = []
            for written in inner:
                assert isinstance(written, ast.ArrayLit)
                spread.extend(written.elements)
            inner = tuple(spread)
        return tuple(shape), inner

    def _fill(self, builder: IRBuilder, expr: ast.ArrayLit, ty: ArrayType,
              place: Value, at: int, shape: tuple[int | None, ...],
              ready: Sequence[Value] | None = None) -> bool:
        """Write what was written down into the run of elements, in order.

        *at* is how many elements are already behind it, so that a dimension
        deeper simply carries on where the one above it left off -- which is
        what row-major order is.  It is therefore also where the element is in
        *ready*, where the elements were lowered before this was reached; there
        is one such place, and what put them there laid them out this way.
        """
        if len(expr.elements) != shape[0]:
            self._diags.emit(D.LANG_ARRAY_WRONG_LENGTH, expr.span,
                             given=len(expr.elements), wanted=shape[0])
            return False
        if len(shape) == 1:
            for index, written in enumerate(expr.elements):
                value = ready[at + index] if ready is not None \
                    else self._lower_into(builder, written, ty.element,
                                          written.span)
                if self._value_type_of(value) is ERROR:
                    return False
                builder.store(
                    self._element_place(builder, place, ty.element,
                                        builder.int_const(U64, at + index),
                                        expr.span), value, expr.span)
            return True
        step = 1
        for along in shape[1:]:
            assert along is not None
            step *= along
        for index, written in enumerate(expr.elements):
            if not isinstance(written, ast.ArrayLit):
                self._diags.emit(D.LANG_ARRAY_NOT_WRITTEN_DEEP_ENOUGH,
                                 written.span)
                return False
            if not self._fill(builder, written, ty, place, at + index * step,
                              shape[1:], ready):
                return False
        return True

    def _on_its_own(self, builder: IRBuilder, written: ast.Expr) -> Value:
        """Lower an index, which belongs to no operator and no assignment.

        It stands between brackets and says which element is wanted, and is no
        part of whatever is being done with that element; lowering it with the
        surrounding context still in force would have a mistake in it reported
        as a mistake about the operator around it.

        A literal with no suffix is a count and is read as one, which is the
        only place an index takes a type from something other than itself.  An
        index of any integer type is accepted, so nothing else is expected of
        it: which type a program counts in is the program's business.
        """
        outer = (self._operand_of, self._initializing, self._assigning,
                 self._handing_over)
        (self._operand_of, self._initializing, self._assigning,
         self._handing_over) = None, None, None, None
        try:
            bare = isinstance(written, ast.IntLit) and written.type_name is None
            return self._lower_expr(builder, written, U64 if bare else None)
        finally:
            (self._operand_of, self._initializing, self._assigning,
             self._handing_over) = outer

    def _element_place(self, builder: IRBuilder, base: Value, element: Type,
                       index: Value, span: Span) -> Value:
        """Where the element at *index* is, given where the first one is."""
        start = builder.cast(CastKind.BITCAST, base,
                             self._module.types.ptr_type(element, mutable=True),
                             span)
        stride = stride_of(element, _LAYOUT)
        return builder.binary(
            BinOp.ADD, start,
            builder.binary(BinOp.WRAP_MUL, index,
                           builder.int_const(U64, stride), span), span)

    def _shape_of(self, builder: IRBuilder, base: Value, ty: ArrayType,
                  span: Span) -> tuple[Value, list[Value]]:
        """Where the elements of an array are, and how many along each dimension.

        One question asked of both kinds: a type that says its shape carries it
        nowhere, and one that does not carries it beside the place.
        """
        pointer = self._module.types.ptr_type(ty.element, mutable=True)
        if ty.fixed:
            return (builder.cast(CastKind.BITCAST, base, pointer, span),
                    [builder.int_const(U64, along) for along in ty.shape])
        return (builder.extract(base, 0, pointer, span),
                [builder.extract(base, at + 1, U64, span)
                 for at in range(ty.rank)])

    def _checked_index(self, builder: IRBuilder, written: ast.Expr,
                       along: int | None, length: Value,
                       span: Span) -> Value | None:
        """Lower one index and see to it that it is one the array has.

        Where both the index and the dimension are written down the answer is
        known while compiling and a program that could only fail is refused.
        Where either is not, the check is one comparison and a branch that does
        not come back, which is the same shape an addition that does not fit has.
        """
        index = self._on_its_own(builder, written)
        found = self._value_type_of(index)
        if found is ERROR:
            return None
        if not isinstance(found, IntType):
            self._diags.emit(D.LANG_ARRAY_INDEX_NOT_A_NUMBER, written.span,
                             found=found.render())
            return None
        if isinstance(written, ast.IntLit) and along is not None:
            if not 0 <= written.value < along:
                self._diags.emit(D.LANG_ARRAY_INDEX_OUTSIDE, written.span,
                                 index=str(written.value), length=along)
                return None
            return builder.int_const(U64, written.value)
        wide = self._as_count(builder, index, found, span)
        builder.check(builder.compare(CmpPred.ULT, wide, length, span),
                      "an index outside its array", span)
        return wide

    def _as_count(self, builder: IRBuilder, value: Value, found: IntType,
                  span: Span) -> Value:
        """An index as the word every count is compared and multiplied as."""
        if found is U64:
            return value
        return builder.cast(CastKind.SEXT if found.signed else CastKind.ZEXT,
                            value, U64, span)

    def _offset_of(self, builder: IRBuilder, written: Sequence[ast.Expr],
                   ty: ArrayType, lengths: Sequence[Value],
                   span: Span) -> Value | None:
        """How far into the run of elements one place is, counted in elements.

        Row-major: each index is added on after what is already there has been
        multiplied by the dimension it is stepping through.  For a vector that
        is the index itself, which is what it should be.

        Fewer indices than there are dimensions names a *row* -- everything the
        dimensions left over reach -- and the place it begins is that many rows
        in, so what the indices come to is multiplied by how many elements a row
        holds.
        """
        if len(written) > ty.rank:
            self._diags.emit(D.LANG_ARRAY_WRONG_RANK, span, given=len(written),
                             wanted=ty.rank)
            return None
        offset: Value | None = None
        for at, one in enumerate(written):
            index = self._checked_index(builder, one, ty.shape[at],
                                        lengths[at], span)
            if index is None:
                return None
            offset = index if offset is None else builder.binary(
                BinOp.WRAP_ADD,
                builder.binary(BinOp.WRAP_MUL, offset, lengths[at], span),
                index, span)
        assert offset is not None
        return self._by_row(builder, offset, lengths[len(written):], span)

    def _by_row(self, builder: IRBuilder, offset: Value,
                left: Sequence[Value], span: Span) -> Value:
        """A count of rows turned into a count of elements."""
        for along in left:
            offset = builder.binary(BinOp.WRAP_MUL, offset, along, span)
        return offset

    def _row_type(self, ty: ArrayType, taken: int) -> ArrayType:
        """The type of what is left of an array once *taken* indices are given."""
        return self._module.types.array_type(ty.element, ty.shape[taken:])

    def _row_at(self, builder: IRBuilder, start: Value, ty: ArrayType,
                lengths: Sequence[Value], offset: Value, taken: int,
                span: Span) -> Value:
        """The row an array has at a place, where not every index was given.

        Row-major is what makes this cheap: a row is a run of elements, so
        naming one is arithmetic on the place and no copy at all.  Where the
        array it came from says its shape the row says its own; where it does
        not, the row carries the counts that are left over.
        """
        place = self._element_place(builder, start, ty.element, offset, span)
        row = self._row_type(ty, taken)
        if row.fixed:
            return builder.cast(CastKind.BITCAST, place, row, span)
        return builder.make_tuple((place, *lengths[taken:]), row, span)

    def _lower_element(self, builder: IRBuilder, expr: ast.Element,
                       expected: Type | None) -> Value:
        """Lower `a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`, one element, or `a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{HORIZONTAL ELLIPSIS}j\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`, a run of them.

        A tuple is looked in the same way, and for the same reason the brackets
        are the array's: what is being asked for is a place among several, and
        the language should not have two shapes for one question.  What differs
        is what the index may be, and that follows from what a tuple is rather
        than from any choice made here -- see `_lower_tuple_member`.
        """
        base = self._lower_expr(builder, expr.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return UndefConst(ERROR)
        if isinstance(ty, TupleType):
            return self._lower_tuple_member(builder, expr, base, ty, expected)
        if not isinstance(ty, ArrayType):
            self._diags.emit(D.LANG_ARRAY_NOT_AN_ARRAY, expr.base.span,
                             found=ty.render())
            return UndefConst(ERROR)
        if any(isinstance(one, ast.Range) for one in expr.indices):
            return self._lower_slice(builder, expr, base, ty, expected)
        mask = self._mask_written(builder, expr)
        if mask is not None:
            return self._lower_picked(builder, expr, base, ty, mask, expected)
        start, lengths = self._shape_of(builder, base, ty, expr.span)
        offset = self._offset_of(builder, expr.indices, ty, lengths, expr.span)
        if offset is None:
            return UndefConst(ERROR)
        if len(expr.indices) < ty.rank:
            # Fewer indices than dimensions names a row rather than an element.
            row = self._row_at(builder, start, ty, lengths, offset,
                               len(expr.indices), expr.span)
            found_ty = self._value_type_of(row)
            if not self._accepts(expected, found_ty):
                self._report_mismatch(expr.span, found_ty, expected)
            return row
        value = builder.load(
            self._element_place(builder, start, ty.element, offset, expr.span),
            expr.span)
        if not self._accepts(expected, ty.element):
            self._report_mismatch(expr.span, ty.element, expected)
        return value

    def _mask_written(self, builder: IRBuilder,
                      expr: ast.Element) -> Value | None:
        """The mask an array is being picked with, or nothing where it is not.

        One index and an array of truth values is what says so, and nothing else
        is: an array of numbers indexes and an array of truth values picks, and
        which of the two was meant is never a question about how it was written.
        Lowered here rather than where an index is, since what is wanted of it is
        not a number.
        """
        if len(expr.indices) != 1:
            return None
        written = expr.indices[0]
        if isinstance(written, ast.Range):
            return None
        mark = len(self._diags.entries) if hasattr(self._diags, "entries") else None
        value = self._on_its_own(builder, written)
        ty = self._value_type_of(value)
        if isinstance(ty, ArrayType) and ty.element is BOOL:
            return value
        # Not a mask, so it was an index; what it is, is asked again where an
        # index is asked, which is where the message about it belongs.
        del mark
        return None

    def _lower_picked(self, builder: IRBuilder, expr: ast.Element, base: Value,
                      ty: ArrayType, mask: Value,
                      expected: Type | None) -> Value:
        """Lower `a⟦m⟧` where *m* is a mask: the things it picked, in order.

        The mask has one truth value for each thing it could pick, so its shape
        is the array's leading dimensions -- as many of them as it has.  One
        dimension over a table picks rows and two picks elements, and what is
        picked keeps whatever dimensions the mask said nothing about: rows of a
        table of three columns are still three columns wide.

        **How many were picked is not known while compiling**, so the answer's
        first dimension is one the type does not state.  What *is* known is that
        it cannot be more than there were, so the room is the whole array's and
        is taken once, here, as this call's own -- no allocation, and nothing
        that outlives the call.

        **Nothing branches.**  Each thing is copied to where the count has got
        to and the count is then advanced by the mask, which is one where it
        picked and zero where it did not; a thing that was not picked is written
        where the next one will be written over it.  Writing into room of this
        call's own is what makes that sound, and it is why the loop is the same
        loop whatever the mask turns out to hold.
        """
        held = self._value_type_of(mask)
        assert isinstance(held, ArrayType)
        if not ty.fixed:
            self._diags.emit(D.LANG_MASK_NEEDS_A_STATED_SHAPE, expr.base.span,
                             found=ty.render())
            return UndefConst(ERROR)
        if not held.fixed or held.rank > ty.rank \
                or held.shape[:held.rank] != ty.shape[:held.rank]:
            self._diags.emit(D.LANG_MASK_WRONG_SHAPE, expr.indices[0].span,
                             found=held.render(), wanted=ty.render())
            return UndefConst(ERROR)
        kept = ty.shape[held.rank:]
        answer = self._module.types.array_type(ty.element, (None, *kept))
        row = 1
        for along in kept:
            assert along is not None
            row *= along
        picks = 1
        for along in ty.shape[:held.rank]:
            assert along is not None
            picks *= along
        start, _ = self._shape_of(builder, base, ty, expr.span)
        marks, _ = self._shape_of(builder, mask, held, expr.span)
        place = builder.frame(ty, expr.span)
        count: Value = builder.int_const(U64, 0)
        for at in range(picks):
            where = builder.binary(BinOp.WRAP_MUL, count,
                                   builder.int_const(U64, row), expr.span)
            for inside in range(row):
                taken = builder.load(
                    self._element_place(builder, start, ty.element,
                                        builder.int_const(U64, at * row + inside),
                                        expr.span), expr.span)
                builder.store(
                    self._element_place(
                        builder, place, ty.element,
                        builder.binary(BinOp.WRAP_ADD, where,
                                       builder.int_const(U64, inside), expr.span),
                        expr.span),
                    taken, expr.span)
            picked = builder.load(
                self._element_place(builder, marks, BOOL,
                                    builder.int_const(U64, at), expr.span),
                expr.span)
            count = builder.binary(
                BinOp.WRAP_ADD, count,
                builder.cast(CastKind.ZEXT, picked, U64, expr.span), expr.span)
        made = builder.make_tuple(
            (builder.cast(CastKind.BITCAST, place,
                          self._module.types.ptr_type(ty.element, mutable=True),
                          expr.span),
             count, *(builder.int_const(U64, along) for along in kept)),
            answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _assign_picked(self, builder: IRBuilder, stmt: ast.ElementAssign,
                       base: Value, ty: ArrayType, mask: Value) -> None:
        """Lower `a⟦m⟧ ← v`: write *v* where the mask picked, and nowhere else.

        The mask's shape is the array's leading dimensions, as it is for
        picking, and one value goes to every element the mask picked -- which is
        what makes a mask of a table's rows write whole rows.

        **Nothing branches.**  What is written to each element is the old value
        where the mask did not pick it and the new one where it did, chosen with
        the mask spread across the whole width of the element: all ones where it
        picked and all zeros where it did not, so that `(old & ~m) | (v & m)` is
        one or the other and no instruction depends on which.  Every element is
        written either way, which for a place this program already owns is a
        write of what was already there.
        """
        held = self._value_type_of(mask)
        assert isinstance(held, ArrayType)
        if not ty.fixed:
            self._diags.emit(D.LANG_MASK_NEEDS_A_STATED_SHAPE, stmt.base.span,
                             found=ty.render())
            return
        if not held.fixed or held.rank > ty.rank \
                or held.shape[:held.rank] != ty.shape[:held.rank]:
            self._diags.emit(D.LANG_MASK_WRONG_SHAPE, stmt.indices[0].span,
                             found=held.render(), wanted=ty.render())
            return
        if not isinstance(ty.element, (IntType, BoolType)):
            self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, stmt.span,
                             feature="".join((
                                 "writing through a mask into an array of '",
                                 ty.element.render(), "'")))
            return
        start, lengths = self._shape_of(builder, base, ty, stmt.span)
        if not self._made_here(start):
            self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, stmt.span)
        marks, _ = self._shape_of(builder, mask, held, stmt.span)
        value = self._lower_into(builder, stmt.value, ty.element, stmt.span)
        if self._value_type_of(value) is ERROR:
            return
        row = 1
        for along in ty.shape[held.rank:]:
            assert along is not None
            row *= along
        picks = 1
        for along in ty.shape[:held.rank]:
            assert along is not None
            picks *= along
        for at in range(picks):
            picked = builder.load(
                self._element_place(builder, marks, BOOL,
                                    builder.int_const(U64, at), stmt.span),
                stmt.span)
            # All ones where it picked and all zeros where it did not, in the
            # element's own width: nought less what the truth value is.
            spread = builder.binary(
                BinOp.WRAP_SUB, self._zero_of(builder, ty.element, stmt.span),
                builder.cast(CastKind.ZEXT, picked, ty.element, stmt.span),
                stmt.span)
            chosen = builder.binary(BinOp.AND, value, spread, stmt.span)
            for inside in range(row):
                where = self._element_place(
                    builder, start, ty.element,
                    builder.int_const(U64, at * row + inside), stmt.span)
                old = builder.load(where, stmt.span)
                builder.store(where, builder.binary(
                    BinOp.OR,
                    builder.binary(BinOp.AND, old,
                                   builder.unary(UnOp.NOT, spread, stmt.span),
                                   stmt.span),
                    chosen, stmt.span), stmt.span)

    def _zero_of(self, builder: IRBuilder, ty: Type, span: Span) -> Value:
        """Nought of a type, which for a truth value is the false one."""
        if isinstance(ty, BoolType):
            return builder.bool_const(False)
        return builder.int_const(ty, 0)

    def _lower_tuple_member(self, builder: IRBuilder, expr: ast.Element,
                            base: Value, ty: TupleType,
                            expected: Type | None) -> Value:
        """Lower `t\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`: one member of a tuple, named by where it stands.

        **The index has to be known while compiling** (4460).  That is not a
        restriction chosen here but what a tuple is: its members are of whatever
        types they were written with, so which one is wanted decides what type
        the whole expression has, and a type this language settles while the
        program runs is a type it does not have.  `_constant_number` says what
        counts as known.

        **There is one index** (4463), for the same reason an array of one
        dimension takes one: a tuple is a run of members and not a shape.

        Nothing is read from memory.  A tuple is its members in registers, so
        naming one is saying which register to go on using.
        """
        if len(expr.indices) != 1:
            self._diags.emit(D.LANG_TUPLE_ONE_INDEX, expr.span,
                             given=len(expr.indices))
            return UndefConst(ERROR)
        at = self._constant_number(expr.indices[0])
        if at is None:
            self._diags.emit(D.LANG_TUPLE_INDEX_NOT_CONSTANT,
                             expr.indices[0].span)
            return UndefConst(ERROR)
        if not 0 <= at < len(ty.members):
            self._diags.emit(D.LANG_TUPLE_INDEX_OUTSIDE, expr.indices[0].span,
                             index=str(at), count=len(ty.members))
            return UndefConst(ERROR)
        member = ty.members[at]
        if not self._accepts(expected, member):
            self._report_mismatch(expr.span, member, expected)
        return builder.extract(base, at, member, expr.span)

    def _constant_number(self, expr: ast.Expr) -> int | None:
        """The whole number an expression stands for while compiling, or nothing.

        A literal is one, with or without a suffix.  A name is one where it was
        bound at the top level to something that cannot change and whose value is
        a whole number: such a name *is* that number, and a program that troubled
        to give it one should not have to write the number again wherever the
        compiler has to know it.

        A name bound inside a function is not one, even where nothing assigns to
        it.  What it stands for is the value an expression produced, and whether
        that expression could have been worked out while compiling is a question
        about the expression rather than about the name.

        Nothing computed is one yet.  `1 + 1` is a constant to a reader and not
        to this compiler: folding happens after the front end, by which time
        every type has been settled -- and settling a type is what this is for.
        The to-do list says what asking earlier would need.
        """
        if isinstance(expr, ast.IntLit):
            return expr.value
        if not isinstance(expr, ast.NameRef):
            return None
        if self._find_local(expr.name) is not None:
            return None
        found = self._top.get(expr.name)
        if not isinstance(found, GlobalVar) or found.mutable:
            return None
        return (found.initializer.value
                if isinstance(found.initializer, IntConst) else None)

    def _lower_slice(self, builder: IRBuilder, expr: ast.Element, base: Value,
                     ty: ArrayType, expected: Type | None) -> Value:
        """Lower `a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{HORIZONTAL ELLIPSIS}j\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}`: the elements from one place up to another.

        What comes out is an array whose type does not say its shape, which is
        where the elements are and how many there are -- and the elements are
        the ones it was taken from, not a copy of them.

        Only a vector is sliced.  A run taken out of a table is not a run of
        elements at all: the second row of a three-by-four is four elements
        together, and the second *column* is four elements a row apart, which
        is a stride and not something a place and a count can say.
        """
        span = expr.span
        if ty.rank != 1:
            self._diags.emit(D.LANG_ARRAY_SLICE_OF_A_TABLE, span,
                             found=ty.render())
            return UndefConst(ERROR)
        written = expr.indices[0]
        assert isinstance(written, ast.Range)
        if written.step is not None:
            self._diags.emit(D.LANG_ARRAY_SLICE_HAS_A_STEP, written.step.span)
            return UndefConst(ERROR)
        start, lengths = self._shape_of(builder, base, ty, span)
        first = self._checked_index(builder, written.start, ty.shape[0],
                                    lengths[0], span)
        if first is None:
            return UndefConst(ERROR)
        # The end may be the length itself, which is one past the last element
        # and so not an index; it is checked against the length and not below it.
        last = self._on_its_own(builder, written.stop)
        reaching = self._value_type_of(last)
        if reaching is ERROR:
            return UndefConst(ERROR)
        if not isinstance(reaching, IntType):
            self._diags.emit(D.LANG_ARRAY_INDEX_NOT_A_NUMBER, written.stop.span,
                             found=reaching.render())
            return UndefConst(ERROR)
        end = self._as_count(builder, last, reaching, span)
        builder.check(builder.compare(CmpPred.ULE, end, lengths[0], span),
                      "a slice reaching past the end of its array", span)
        builder.check(builder.compare(CmpPred.ULE, first, end, span),
                      "a slice that ends before it begins", span)
        answer = self._module.types.array_type(ty.element, (None,))
        made = builder.make_tuple(
            (self._element_place(builder, start, ty.element, first, span),
             builder.binary(BinOp.WRAP_SUB, end, first, span)), answer, span)
        if not self._accepts(expected, answer):
            self._report_mismatch(span, answer, expected)
        return made

    def _lower_element_assign(self, builder: IRBuilder,
                              stmt: ast.ElementAssign) -> None:
        """Lower `a\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} \N{LEFTWARDS ARROW} v`, which puts a value at one place."""
        base = self._lower_expr(builder, stmt.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return
        if isinstance(ty, TupleType):
            # A tuple is a value and not a place: its members are registers, not
            # room in memory.  Assigning to one would mean binding the name to a
            # tuple made of the others and the new value, which is what writing
            # that out does.
            self._diags.emit(D.LANG_TUPLE_ELEMENT_NOT_A_PLACE, stmt.span)
            return
        if not isinstance(ty, ArrayType):
            self._diags.emit(D.LANG_ARRAY_NOT_AN_ARRAY, stmt.base.span,
                             found=ty.render())
            return
        mask = self._mask_written(builder, stmt)
        if mask is not None:
            self._assign_picked(builder, stmt, base, ty, mask)
            return
        if len(stmt.indices) != ty.rank:
            # Every index, because what is assigned is one element.  Assigning a
            # whole row would be copying one array into another, which nothing
            # in the language does yet and which is not what `←` means anywhere
            # else: it binds a name or writes one place.
            self._diags.emit(D.LANG_ARRAY_WRONG_RANK, stmt.span,
                             given=len(stmt.indices), wanted=ty.rank)
            return
        start, lengths = self._shape_of(builder, base, ty, stmt.span)
        if not self._made_here(start):
            self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, stmt.span)
        offset = self._offset_of(builder, stmt.indices, ty, lengths, stmt.span)
        if offset is None:
            return
        value = self._lower_into(builder, stmt.value, ty.element, stmt.span)
        if self._value_type_of(value) is ERROR:
            return
        builder.store(
            self._element_place(builder, start, ty.element, offset, stmt.span),
            value, stmt.span)

    def _lower_collection(self, builder: IRBuilder,
                          expr: ast.SetLit | ast.DictLit,
                          expected: Type | None) -> Value:
        """Check a set or a dictionary written down.

        Nothing is built: a collection is a table somewhere in memory, and there
        is nowhere yet for one to be -- the compiler has no allocator and the
        language no way to write the loop a lookup walks.  What is checked is
        everything about the types, so that a program that will work when there
        is one is known to be right now.
        """
        empty = (isinstance(expr, ast.SetLit) and not expr.elements) or \
            (isinstance(expr, ast.DictLit) and not expr.entries)
        # Nothing written is nothing to work out, and a table with nothing in
        # it is still a table.
        ready = _Entries(keys=[], values=[], wanted_key=None, wanted_value=None)
        aim = self._aiming_at(expected)
        if empty:
            if not isinstance(aim, (SetType, DictType)):
                self._diags.emit(D.LANG_COLLECTION_EMPTY_UNKNOWN, expr.span)
                return UndefConst(ERROR)
            ty: Type = aim
        else:
            written = (tuple((one, None) for one in expr.elements)
                       if isinstance(expr, ast.SetLit) else expr.entries)
            ready = self._entries_written(builder, written, expected)
            keys = self._same_type(ready.keys, [k.span for k, _ in written],
                                   ready.wanted_key)
            if keys is ERROR:
                return UndefConst(ERROR)
            if not _can_be_a_key(keys):
                self._diags.emit(D.LANG_COLLECTION_KEY_NOT_HASHABLE,
                                 written[0][0].span, found=keys.render())
                return UndefConst(ERROR)
            if isinstance(expr, ast.SetLit):
                ty = self._module.types.set_type(keys)
            else:
                held = self._same_type(
                    ready.values, [v.span for _, v in written if v is not None],
                    ready.wanted_value)
                if held is ERROR:
                    return UndefConst(ERROR)
                ty = self._module.types.dict_type(keys, held)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        if isinstance(ty, DictType) and not _can_be_a_key(ty.value):
            # The same restriction the key has, and for a duller reason: an
            # entry is words, and what goes in one has to fit in one.  The
            # to-do list says what a value of any type would need.
            self._diags.emit(D.LANG_COLLECTION_VALUE_TOO_LARGE, expr.span,
                             found=ty.value.render())
            return UndefConst(ERROR)
        # Making one takes room out of an arena, and an arena outlives the
        # call: the next call gets what this one left of it.
        self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, expr.span)
        return self._build_collection(builder, expr, ty,
                                      self._arena_named(expr.arena), ready)

    def _arena_named(self, written: ast.NameRef | None) -> GlobalVar | None:
        """Which allocator a collection was told to come out of.

        Nothing where none was named, which is what says to use the one the
        compiler provides.  A name that is not an arena is reported here rather
        than where the table is made, because what is wrong with it is what it
        is and not what it is being used for.
        """
        if written is None:
            return None
        found = self._provided(written.name)
        if not (isinstance(found, GlobalVar) and found.value_type is ARENA):
            self._diags.emit(D.LANG_NOT_AN_ARENA, written.span,
                             name=written.name)
            return None
        return found

    def _build_collection(self, builder: IRBuilder,
                          expr: ast.SetLit | ast.DictLit,
                          ty: SetType | DictType,
                          arena: GlobalVar | None,
                          ready: _Entries) -> Value:
        """Make the table a collection is, and put what was written down in it.

        The entries are put in one at a time through the same call an assignment
        uses, so a collection written with a key twice holds it once -- which is
        what a set is, and what Python answers for a dictionary written that way.

        What goes in are the values the entries already came to.  They were
        worked out where they were written, in the order they were written, and
        working them out again here would be running them twice.
        """
        table = self._new_table(builder, ty, expr.span, arena)
        held = iter(ready.values)
        for at, key in enumerate(ready.keys):
            place = self._put_key(builder, table, key, expr.span)
            if isinstance(ty, DictType):
                builder.store(self._value_place(builder, place, ty.value),
                              next(held), expr.span)
        return table

    def _new_table(self, builder: IRBuilder, ty: SetType | DictType,
                   span: Span, arena: GlobalVar | None = None,
                   comes_from: Value | None = None) -> Value:
        """Make an empty table of the shape *ty* calls for.

        Out of the arena the program named, or out of the one another table came
        from, or -- where it said nothing -- out of the one the compiler
        provides.  Which of the three it was is settled here so that everything
        that makes a table asks the same question once.
        """
        tables.ensure_runtime(self._module)
        if comes_from is not None:
            place = tables.arena_of(builder, comes_from)
        else:
            found = arena if arena is not None else self._provided(HEAP_NAME)
            assert isinstance(found, GlobalVar)
            place = builder.address(found, span)
        stride = tables.SET_STRIDE if isinstance(ty, SetType) else tables.DICT_STRIDE
        made = builder.call(self._module.functions[tables.NEW_SYMBOL],
                            (place, builder.int_const(U64, stride)),
                            tables.table_type(self._module), span)
        return builder.cast(CastKind.BITCAST, made, ty, span)

    def _as_word(self, builder: IRBuilder, key: Value, span: Span) -> Value:
        """A key as the word a table holds it as.

        Every key is a whole number as far as a register is concerned -- a truth
        value and a value of an enumeration are both one -- and a table holds
        one word, so what a key is stored and compared as is that word.  The
        widening says which, so that two keys that are the same number are the
        same word however narrow their type is.
        """
        ty = self._value_type_of(key)
        if ty is U64:
            return key
        return builder.cast(CastKind.ZEXT, key, U64, span)

    def _put_key(self, builder: IRBuilder, table: Value, key: Value,
                 span: Span) -> Value:
        """The entry for *key*, made if the table did not have it."""
        tables.ensure_runtime(self._module)
        return builder.call(
            self._module.functions[tables.PUT_SYMBOL],
            (builder.cast(CastKind.BITCAST, table,
                          tables.table_type(self._module), span),
             self._as_word(builder, key, span)),
            tables.table_type(self._module), span)

    def _find_key(self, builder: IRBuilder, table: Value, key: Value,
                  span: Span) -> Value:
        """The entry the key is in, or the one it would go in."""
        tables.ensure_runtime(self._module)
        return builder.call(
            self._module.functions[tables.SLOT_SYMBOL],
            (builder.cast(CastKind.BITCAST, table,
                          tables.table_type(self._module), span),
             self._as_word(builder, key, span)),
            tables.table_type(self._module), span)

    def _is_live(self, builder: IRBuilder, place: Value, span: Span) -> Value:
        """Whether an entry holds a key rather than standing empty."""
        return builder.compare(CmpPred.EQ, builder.load(place, span),
                               builder.int_const(U64, tables.LIVE), span)

    def _value_place(self, builder: IRBuilder, place: Value, ty: Type) -> Value:
        """Where in an entry the value belonging to its key is."""
        word = builder.binary(BinOp.ADD, place,
                              builder.int_const(U64, tables.VALUE_AT))
        return builder.cast(CastKind.BITCAST, word,
                            self._module.types.ptr_type(ty, mutable=True))

    def _one_type(self, builder: IRBuilder, written: Sequence[ast.Expr],
                  wanted: Type | None,
                  into: list[Value] | None = None) -> Type:
        """Lower each of *written* and give back the one type they share.

        *into* collects what they came to, for a caller that has to lower them
        to learn the type and would otherwise lower them a second time to use
        them.  What is collected is every one of them, including any whose type
        did not agree, so that the places line up with what was written.
        """
        values = [self._lower_expr(builder, entry, None)
                  for entry in self._nothing_expected(written)]
        if into is not None:
            into.extend(values)
        return self._same_type(values, [entry.span for entry in written], wanted)

    def _nothing_expected(self, written: Sequence[ast.Expr]
                          ) -> Sequence[ast.Expr]:
        """Hand *written* back with the surrounding context put aside.

        Nothing is expected of an entry: what it is, is what the collection is
        made of, and a mismatch between two of them is about the collection
        rather than about wherever it is being given to.
        """
        outer = self._initializing, self._assigning
        self._initializing, self._assigning = None, None
        try:
            yield from written
        finally:
            self._initializing, self._assigning = outer

    def _same_type(self, values: Sequence[Value], spans: Sequence[Span],
                   wanted: Type | None) -> Type:
        """The one type several values share, reporting one that does not.

        Asked of values rather than of what was written, because the thing that
        wanted to know has already lowered them: an entry is worked out once,
        where it stands.
        """
        found: Type | None = wanted
        spoiled = False
        for value, span in zip(values, spans):
            ty = self._value_type_of(value)
            if ty is ERROR:
                spoiled = True
                continue
            if found is None:
                found = ty
            elif ty is not found:
                self._diags.emit(D.LANG_COLLECTION_MIXED_ENTRIES, span,
                                 found=ty.render(), expected=found.render())
                spoiled = True
        return ERROR if spoiled or found is None else found

    def _entries_written(self, builder: IRBuilder,
                         written: Sequence[tuple[ast.Expr, ast.Expr | None]],
                         expected: Type | None) -> _Entries:
        """Lower a collection's entries, once each and in the order written.

        A dictionary is written key, value, key, value, and that is the order
        they are worked out in: the two were lowered in two passes, all the keys
        and then all the values, which put them in an order nobody wrote.

        Nothing is expected of an entry even where the collection's type is
        written down.  What an entry is, is what the collection is made of, and
        a disagreement between one entry and the type is the same complaint as a
        disagreement between two entries -- so the wanted type is carried out as
        what `_same_type` starts from rather than as what each entry is lowered
        into, and one message says it either way.
        """
        aim = self._aiming_at(expected)
        wanted_key = aim.element if isinstance(aim, SetType) else \
            aim.key if isinstance(aim, DictType) else None
        wanted_value = aim.value if isinstance(aim, DictType) else None
        keys: list[Value] = []
        values: list[Value] = []
        outer = self._initializing, self._assigning
        self._initializing, self._assigning = None, None
        try:
            for key, value in written:
                keys.append(self._lower_expr(builder, key, None))
                if value is not None:
                    values.append(self._lower_expr(builder, value, None))
        finally:
            self._initializing, self._assigning = outer
        return _Entries(keys=keys, values=values, wanted_key=wanted_key,
                        wanted_value=wanted_value)

    def _lower_index(self, builder: IRBuilder, expr: ast.Index,
                     expected: Type | None) -> Value:
        """Check `c⸨k⸩`: whether a set holds a key, or what a dictionary has.

        A dictionary answers with a *result*: the value where there is one, and
        the fact that there is none where there is not.  That is what makes a
        key that is not there impossible to read past by accident, and what lets
        `d⸨k⸩ ?? 0u8` say "or this instead" with nothing new to learn.
        """
        base = self._lower_expr(builder, expr.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, (SetType, DictType)):
            self._diags.emit(D.LANG_INDEX_NOT_A_COLLECTION, expr.base.span,
                             found=ty.render())
            return UndefConst(ERROR)
        key_ty = ty.element if isinstance(ty, SetType) else ty.key
        key = self._lower_expr(builder, expr.key, key_ty)
        if self._value_type_of(key) is ERROR:
            return UndefConst(ERROR)
        answer: Type = (BOOL if isinstance(ty, SetType)
                        else self._module.types.result_type(ty.value))
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        place = self._find_key(builder, base, key, expr.span)
        if isinstance(ty, SetType):
            return self._is_live(builder, place, expr.span)
        # The value is read whether the entry holds a key or not: an entry of
        # the table is a place either way, and what a table that has not been
        # written holds is zero.  Reading it unasked is what keeps this to one
        # branch fewer than it looks like it needs -- the truth value beside the
        # answer is what says whether the answer means anything, and that is
        # asked of the entry's own first word rather than worked out from the
        # other question, which would be a truth value turned round.
        missing = builder.compare(CmpPred.NE, builder.load(place, expr.span),
                                  builder.int_const(U64, tables.LIVE), expr.span)
        return builder.wrap(
            builder.load(self._value_place(builder, place, ty.value), expr.span),
            missing, answer, expr.span)

    def _lower_entry_assign(self, builder: IRBuilder, stmt: ast.EntryAssign) -> None:
        """Check `d⸨k⸩ ← v`, which puts a value under a key."""
        base = self._lower_expr(builder, stmt.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return
        if not isinstance(ty, DictType):
            self._diags.emit(D.LANG_ENTRY_ASSIGN_NOT_A_DICT, stmt.base.span,
                             found=ty.render())
            return
        key = self._lower_expr(builder, stmt.key, ty.key)
        value = self._lower_into(builder, stmt.value, ty.value, stmt.span)
        if self._value_type_of(key) is ERROR or self._value_type_of(value) is ERROR:
            return
        self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, stmt.span)
        place = self._put_key(builder, base, key, stmt.span)
        builder.store(self._value_place(builder, place, ty.value), value,
                      stmt.span)

    # -- if --------------------------------------------------------------------

    def _lower_if(self, builder: IRBuilder, stmt: ast.If, func: Function,
                  wanted: Type | None, produces: bool) -> Value:
        """Check and lower an `if`, its `elif`s and its `else`.

        A chain of conditional branches, each condition asked in the block the
        one before it falls through to -- which is what makes an `elif` an
        elif and not a second `if`: it is only asked where the earlier ones did
        not hold.

            entry:   condbr c1 → then1, ask2
            ask2:    condbr c2 → then2, otherwise
            then1:   its body, then the join
            then2:   the same
            otherwise: the `else` body, or the join itself where there is none
            joined(p): whatever the arms left behind

        Where a value is wanted there has to be an `else`: an `if` without one
        has a way through that runs no arm, and that way would owe a value it
        has nowhere to get.
        """
        last = stmt.arms[-1]
        has_else = last.condition is None
        if produces and not has_else:
            self._diags.emit(D.LANG_IF_NEEDS_AN_ELSE, stmt.span)
            return UndefConst(ERROR)
        asked = [arm for arm in stmt.arms if arm.condition is not None]
        blocks = [builder.new_block("then") for _ in asked]
        otherwise = builder.new_block("otherwise") if has_else else None
        spoiled = False
        for index, arm in enumerate(asked):
            assert arm.condition is not None
            # Lowered with nothing expected of it, so that a condition of the
            # wrong type is reported once, as a condition, rather than by
            # whatever wording the place it stands in would have used.
            condition = self._lower_expr(builder, arm.condition, None)
            found = self._value_type_of(condition)
            if found is not BOOL and found is not ERROR:
                self._diags.emit(D.LANG_IF_CONDITION_NOT_BOOLEAN,
                                 arm.condition.span, found=found.render())
                spoiled = True
                condition = UndefConst(BOOL)
            elif found is ERROR:
                spoiled = True
                condition = UndefConst(BOOL)
            following = (builder.new_block("elif") if index + 1 < len(asked)
                         else otherwise)
            if following is None:
                # Nothing left to ask and no `else`: what the last condition
                # falls through to is the place the arms join, which the arms
                # themselves are what makes.
                following = builder.new_block("otherwise")
                otherwise = following
            builder.condbr(condition, blocks[index], following, span=arm.span)
            builder.position_at(following)
        if spoiled:
            return UndefConst(ERROR)
        plan = [_ArmPlan(body=arm.body, block=block)
                for arm, block in zip(asked, blocks)]
        if has_else:
            assert otherwise is not None
            plan.append(_ArmPlan(body=last.body, block=otherwise))
            return self._run_arms(builder, stmt, func, plan, wanted, produces)
        return self._run_arms(builder, stmt, func, plan, wanted, produces,
                              otherwise=otherwise)

    # -- loops -----------------------------------------------------------------

    def _lower_while(self, builder: IRBuilder, stmt: ast.While, func: Function,
                     expected: Type | None, produces: bool) -> Value:
        """Check and lower a `while`, which is a branch backwards.

            before:  br loop(v₁ … vₙ, mem)
            loop(p₁ … pₙ, mem):  the condition; condbr → body, done
            body:    its statements, then br loop(v₁′ … vₙ′, mem′)
            done:    what follows, reading the loop's parameters

        The names a turn may change are the loop's parameters, so that the next
        turn reads what the last one left and what follows the loop reads the
        same.  Which names those are is asked of the body before anything is
        lowered, because the parameters have to exist before the condition --
        which may itself read one -- is lowered against them.  That is the one
        thing a loop cannot do the way `if` and `match` do it, which look at
        what the arms turned out to change afterwards.

        Nothing travels on the conditional branch, so where the block after the
        loop has to be handed anything the way out of the test hands it from a
        block of its own.

        What the loop comes to, where anything wants it, is what a `break`
        handed over -- and, down the way that ran the body out, what the `else`
        arm came to or a failure where there is no `else` arm.
        """
        if builder.block is None:
            return UndefConst(ERROR)
        label = self._label_of(stmt.label)
        handing = self._wanted_of_a_loop(stmt, expected, produces)
        carried = self._loop_locals(stmt.body, stmt.alternative)
        header = builder.new_block("loop")
        body = builder.new_block("body")
        # A loop with a name is a loop something may leave, and where the
        # exit takes what a jump hands over it has to take it before there is
        # a jump to lower.  Which statements below hold one is a question about
        # every place a statement can be written, and the answer to it is worth
        # less than the jump it would save: a loop nothing leaves has a name
        # nothing names, and that is reported rather than optimized.
        leaves = label is not None or stmt.alternative is not None or produces
        leave = builder.new_block("leave") if leaves else None
        after = builder.new_block("done")
        builder.br(header,
                   (*(local.value for local in carried), builder.memory()),
                   stmt.span)
        builder.position_at(header)
        params = [header.add_param(self._value_type_of(local.value), local.name)
                  for local in carried]
        token = header.add_param(MEM, "mem")
        ways, exit_token = self._exit_params(after, carried, params) \
            if leaves else (None, token)
        for local, param in zip(carried, params):
            local.value = param
            local.value_span = stmt.span
            local.read = False
        builder.set_memory(token)
        # Lowered with nothing expected of it, so that a condition of the wrong
        # type is reported once, as a condition.
        condition = self._lower_expr(builder, stmt.condition, None)
        found = self._value_type_of(condition)
        if found is not BOOL:
            if found is not ERROR:
                self._diags.emit(D.LANG_LOOP_CONDITION_NOT_BOOLEAN,
                                 stmt.condition.span, found=found.render())
            condition = UndefConst(BOOL)
        builder.condbr(condition, body, leave if leave is not None else after,
                       span=stmt.span)
        ran_out = builder.memory()
        outer_carried = self._carried
        # A name the loop carries is read by the next turn, so replacing the
        # value it stands for is not throwing that value away.
        self._carried = outer_carried | {id(local) for local in carried}
        builder.position_at(body)
        self._push_scope()
        one = self._begin_loop(label, header, after, carried, (), None,
                               produces, produces and stmt.alternative is None,
                               handing)
        self._lower_block(builder, stmt.body, func, as_result=False)
        self._end_loop(label)
        self._pop_scope()
        self._carried = outer_carried
        if not builder.is_terminated and builder.block is not None:
            builder.br(header,
                       (*(local.value for local in carried), builder.memory()),
                       stmt.span)
            # The branch backwards reads every value it hands over, which is
            # what keeps a counter a loop counts down from being reported as a
            # value nothing reads: the next turn is what reads it, and this is
            # the reading.
            for local in carried:
                local.read = True
        return self._loop_answer(builder, stmt, func, one, leave, after,
                                 carried, params, token, exit_token, ran_out,
                                 ways, expected, produces)

    def _lower_foreach(self, builder: IRBuilder, stmt: ast.ForEach,
                       func: Function, expected: Type | None,
                       produces: bool) -> Value:
        """Check and lower a `foreach`, and `while` written with a binding.

        An **iterator** is a value with a `next` answering the next element or a
        failure, and the failure is what ends the loop.  The result type is how
        that is said, and it does not surface: the names are bound to what
        there was, and a loop over something with nothing in it runs no turns.

        Four things are iterators, and none of them is called: each one's `next`
        is lowered where it is asked, which for a range is a comparison and an
        addition, for an array a comparison and a read, and for a table a walk
        that steps past the places holding nothing.  What they have in common is
        the shape below, so the loop is one loop:

            before:  br loop(s₁ … sₖ, v₁ … vₙ, mem)
            loop(s₁ … sₖ, p₁ … pₙ, mem):  condbr there is another → body, done
            body:    the names stand for this one; the statements; br loop(…)
            done:    what follows

        `s₁ … sₖ` is whatever the thing being iterated has to carry from one turn to
        the next: a counter for a range and an array, a place in the entries for
        a table.  Everything that does not change from turn to turn -- where an
        array's elements are, how many there are -- is worked out once before
        the loop and read from where it was left.
        """
        if builder.block is None:
            return UndefConst(ERROR)
        found = self._iteration_over(builder, stmt)
        if found is None:
            # Bound to nothing that means anything, so that a later mention of
            # the name reports nothing of its own.  There is no loop to jump
            # out of either, so the label is not put up: a jump naming it would
            # be reported, and what it would be reported for is this.
            self._push_scope()
            self._bind_local(stmt.name, UndefConst(ERROR), stmt.name_span)
            self._lower_block(builder, stmt.body, func, as_result=False)
            self._pop_scope()
            return UndefConst(ERROR)
        label = self._label_of(stmt.label)
        handing = self._wanted_of_a_loop(stmt, expected, produces)
        carried = self._loop_locals(stmt.body, stmt.alternative)
        header = builder.new_block("loop")
        body = builder.new_block("body")
        # A loop with a name is a loop something may leave, and where the
        # exit takes what a jump hands over it has to take it before there is
        # a jump to lower.  Which statements below hold one is a question about
        # every place a statement can be written, and the answer to it is worth
        # less than the jump it would save: a loop nothing leaves has a name
        # nothing names, and that is reported rather than optimized.
        leaves = label is not None or stmt.alternative is not None or produces
        leave = builder.new_block("leave") if leaves else None
        after = builder.new_block("done")
        builder.br(header,
                   (*found.start, *(local.value for local in carried),
                    builder.memory()), stmt.span)
        builder.position_at(header)
        state = tuple(header.add_param(self._value_type_of(one), stmt.name)
                      for one in found.start)
        params = [header.add_param(self._value_type_of(local.value), local.name)
                  for local in carried]
        token = header.add_param(MEM, "mem")
        ways, exit_token = self._exit_params(after, carried, params) \
            if leaves else (None, token)
        for local, param in zip(carried, params):
            local.value = param
            local.value_span = stmt.span
            local.read = False
        builder.set_memory(token)
        builder.condbr(found.more(builder, state), body,
                       leave if leave is not None else after, span=stmt.span)
        ran_out = builder.memory()
        outer_carried = self._carried
        self._carried = outer_carried | {id(local) for local in carried}
        builder.position_at(body)
        self._push_scope()
        self._bind_turn(builder, stmt, found.take(builder, state))
        one = self._begin_loop(label, header, after, carried, state, found.step,
                               produces, produces and stmt.alternative is None,
                               handing)
        self._lower_block(builder, stmt.body, func, as_result=False)
        self._end_loop(label)
        self._pop_scope()
        self._carried = outer_carried
        if not builder.is_terminated and builder.block is not None:
            builder.br(header,
                       (*found.step(builder, state),
                        *(local.value for local in carried),
                        builder.memory()), stmt.span)
            for local in carried:
                local.read = True
        return self._loop_answer(builder, stmt, func, one, leave, after,
                                 carried, params, token, exit_token, ran_out,
                                 ways, expected, produces)

    # -- naming a loop, and leaving or repeating it ----------------------------

    def _label_of(self, label: ast.Label | None) -> ast.Label | None:
        """The name a loop may take, which is the one written unless it is taken.

        A loop inside one of the same name would hide it, leaving nothing that
        could name the outer one from inside the inner; two that are not one
        inside the other share nothing and may share a name.

        A name that is taken is dropped rather than kept, and the loop is
        lowered without one.  The program is refused either way, and lowering
        the body is what keeps the rest of what is wrong with it from being
        hidden behind this.
        """
        if label is None:
            return None
        if any(one.label == label.name for one in self._loops):
            self._diags.emit(D.LANG_LOOP_LABEL_REUSED, label.span,
                             name=label.name)
            return None
        return label

    def _begin_loop(self, label: ast.Label | None, header: BasicBlock,
                    after: BasicBlock, carried: list[_Local],
                    state: tuple[Value, ...],
                    step: (Callable[[IRBuilder, tuple[Value, ...]],
                                    tuple[Value, ...]] | None),
                    answers: bool, wraps: bool, handing: Type | None
                    ) -> _Loop | None:
        """Put a loop's name up for the length of its body."""
        if label is None:
            return None
        one = _Loop(label=label.name, span=label.span, header=header,
                    after=after, carried=carried, state=state, step=step,
                    answers=answers, wraps=wraps, handing=handing)
        self._loops.append(one)
        return one

    def _end_loop(self, label: ast.Label | None) -> _Loop | None:
        """Take it down again, and report a name nothing named."""
        if label is None:
            return None
        one = self._loops.pop()
        if not one.named:
            self._diags.emit(D.LANG_LOOP_LABEL_UNUSED, one.span, name=one.label)
        return one

    def _exit_params(self, after: BasicBlock, carried: list[_Local],
                     params: list[Value]) -> tuple[list[Value], Value]:
        """Give the block after a loop the names every way out of it hands over.

        Where nothing leaves the loop early the test is the only way out, so
        what follows reads the loop's own parameters and the block needs none of
        its own -- which is what it had before there was a `break`.  Where
        something does, the values differ by which way was taken, so they are
        handed over and the block takes them.

        What the loop comes to is not among them.  Whether there is such a thing
        is not known until the body has been read, so that parameter is added
        afterwards and stands last; the branches that hand it over are written
        before it exists, which is allowed because a branch records what it
        hands over and a block records what it takes, and the two are matched
        when the function is done.
        """
        ways = [after.add_param(self._value_type_of(param), local.name)
                for local, param in zip(carried, params)]
        return ways, after.add_param(MEM, "mem")

    def _find_loop(self, label: ast.Label) -> _Loop | None:
        """The loop a jump names, or nothing where it names none."""
        for one in reversed(self._loops):
            if one.label == label.name:
                one.named = True
                return one
        self._diags.emit(D.LANG_LOOP_LABEL_UNKNOWN, label.span, name=label.name)
        return None

    def _lower_break(self, builder: IRBuilder, stmt: ast.Break) -> None:
        """Lower `break §name [VALUE]`: go to where the loop of that name ends.

        What it hands over are the names the loop carries, as they stand here:
        the block after the loop is reached two ways now, and what it reads has
        to be right down both of them.  Where the loop is an expression it hands
        over one thing more, which is what the loop comes to.
        """
        found = self._find_loop(stmt.label)
        if found is None or builder.block is None:
            return
        handed = self._handed_over(builder, stmt, found)
        carried = tuple(local.value for local in found.carried)
        builder.br(found.after,
                   (*carried, builder.memory()) if handed is None
                   else (*carried, builder.memory(), handed), stmt.span)
        for local in found.carried:
            local.read = True

    def _handed_over(self, builder: IRBuilder, stmt: ast.Break,
                     found: _Loop) -> Value | None:
        """What a `break` hands the loop, or nothing where it hands it nothing.

        Every `break` naming one loop agrees with every other about what it
        hands over: that a value is handed at all, and what type it is.  The
        first one to say either is what the rest are held to, unless the loop
        itself already said -- which it does wherever what the loop is being
        used as is known before its body is read.

        Where the loop has no `else` arm the value becomes a result here, since
        the way that runs the body out is the way that has none.
        """
        if stmt.value is None:
            found.bare_at = found.bare_at or stmt.span
            if found.answers:
                self._diags.emit(D.LANG_LOOP_BREAK_HANDS_NOTHING, stmt.span)
            return None
        if not found.answers:
            # The loop stands where nothing reads what it comes to, so there is
            # nowhere for this to go.
            self._diags.emit(D.LANG_LOOP_VALUE_UNUSED, stmt.value.span)
            return None
        found.handed_at = found.handed_at or stmt.span
        outer = self._as_the_loops_value()
        try:
            value = self._lower_into(builder, stmt.value, found.handing,
                                     stmt.value.span) \
                if found.handing is not None \
                else self._lower_expr(builder, stmt.value, None)
            ty = self._value_type_of(value)
            if ty is ERROR:
                return None
            if found.handing is None:
                found.handing = ty
            elif ty is not found.handing:
                self._report_mismatch(stmt.value.span, ty, found.handing)
                return None
        finally:
            self._as_it_was(outer)
        if not found.wraps:
            return value
        return builder.wrap(value, builder.bool_const(False),
                            self._module.types.result_type(found.handing),
                            stmt.span)

    def _lower_continue(self, builder: IRBuilder, stmt: ast.Continue) -> None:
        """Lower `continue §name`: begin the next turn of the loop of that name.

        Which is exactly what reaching the end of the body does, so it is
        lowered the same way -- the iterator's step where there is one, then the
        names the loop carries, then the memory.  A `while` has no step: its
        next turn is its condition again.
        """
        found = self._find_loop(stmt.label)
        if found is None or builder.block is None:
            return
        moved = found.step(builder, found.state) if found.step is not None \
            else found.state
        builder.br(found.header,
                   (*moved, *(local.value for local in found.carried),
                    builder.memory()), stmt.span)
        for local in found.carried:
            local.read = True

    def _as_the_loops_value(self) -> tuple:
        """Say that what is lowered next is what the loop comes to.

        A mismatch there is about the loop and not about whatever the loop
        stands in, so every other context is put aside for as long as it lasts:
        without that, a `break` in a definition's initializer would report a
        type that does not match as a mistake about the definition.
        """
        outer = (self._operand_of, self._initializing, self._assigning,
                 self._handing_over, self._leaving)
        (self._operand_of, self._initializing, self._assigning,
         self._handing_over, self._leaving) = None, None, None, None, True
        return outer

    def _as_it_was(self, outer: tuple) -> None:
        """Put back what `_as_the_loops_value` set aside."""
        (self._operand_of, self._initializing, self._assigning,
         self._handing_over, self._leaving) = outer

    def _wanted_of_a_loop(self, stmt: ast.While | ast.ForEach,
                          expected: Type | None, produces: bool
                          ) -> Type | None:
        """What a `break` hands over, where the loop's own type already says.

        A loop with an `else` arm comes to what the two ways agree on; one
        without comes to a result whose failure is the way that ran the body
        out.  Either way what a `break` hands over is an answer and never a
        result of its own, so what is wanted of it is what `_aiming_at` says --
        and where the loop has no `else` arm, the result is made at the `break`
        rather than around the loop.

        Nothing where nothing says -- the loop standing where no type is wanted
        of it -- and the first `break` settles it then.
        """
        return self._aiming_at(expected) if produces else None

    def _loop_answer(self, builder: IRBuilder, stmt: ast.While | ast.ForEach,
                     func: Function, one: _Loop | None,
                     leave: BasicBlock | None, after: BasicBlock,
                     carried: list[_Local], params: list[Value],
                     token: Value, exit_token: Value, ran_out: Value,
                     ways: list[Value] | None, expected: Type | None,
                     produces: bool) -> Value:
        """Fill the way out of the test, and answer what the loop comes to.

        The way out of the test is the way that ran the body out: it hands over
        the loop's own parameters, and the `else` arm is what runs down it.
        Where the loop is an expression it hands over one thing more -- what the
        `else` arm came to, or the failure that says the loop was never left by
        a `break`.

        The block after the loop takes what it is handed, so the parameter that
        carries the value is added here, once it is known there is one.  It
        stands last, after the memory, because that is the order the branches
        that were written before it hand things over in.
        """
        handing = one.handing if one is not None else None
        otherwise: Value | None = None
        if leave is not None:
            builder.position_at(leave)
            builder.set_memory(ran_out)
            for local, param in zip(carried, params):
                local.value = param
                local.value_span = stmt.span
            if stmt.alternative is not None:
                self._push_scope()
                outer = self._as_the_loops_value()
                try:
                    otherwise = self._lower_block(
                        builder, stmt.alternative, func, as_result=False,
                        wanted=handing, produces=produces)
                finally:
                    self._as_it_was(outer)
                self._pop_scope()
                if otherwise is not None:
                    found = self._value_type_of(otherwise)
                    if found is ERROR:
                        otherwise = None
                    elif handing is None:
                        handing = found
                    elif found is not handing:
                        self._report_mismatch(stmt.alternative.span, found,
                                              handing)
                        otherwise = None
        if produces and handing is None:
            # Nothing hands anything over and no `else` arm gives anything, so
            # there is no value here to be the loop's.
            self._diags.emit(D.LANG_LOOP_COMES_TO_NOTHING, stmt.span)
            self._settle_after(builder, carried, ways, params, after,
                               exit_token, token, stmt, leave, None)
            return UndefConst(ERROR)
        answer: Type | None = None
        if produces:
            assert handing is not None
            answer = handing if stmt.alternative is not None \
                else self._module.types.result_type(handing)
        given: Value | None = None
        if answer is not None:
            given = otherwise if stmt.alternative is not None else builder.wrap(
                UndefConst(handing), builder.bool_const(True), answer, stmt.span)
            if given is None:
                given = UndefConst(answer)
        self._settle_after(builder, carried, ways, params, after, exit_token,
                           token, stmt, leave, given)
        if answer is None:
            return UndefConst(VOID)
        value = after.add_param(answer, "answer")
        if not self._accepts(expected, answer):
            self._report_mismatch(stmt.span, answer, expected)
            return UndefConst(ERROR)
        return value

    def _settle_after(self, builder: IRBuilder, carried: list[_Local],
                      ways: list[Value] | None, params: list[Value],
                      after: BasicBlock, exit_token: Value, token: Value,
                      stmt: ast.While | ast.ForEach,
                      leave: BasicBlock | None, given: Value | None) -> None:
        """Branch out of the test and stand in the block the loop ends at."""
        if leave is not None:
            handed = tuple(local.value for local in carried)
            builder.br(after,
                       (*handed, builder.memory()) if given is None
                       else (*handed, builder.memory(), given), stmt.span)
        builder.position_at(after)
        for local, param in zip(carried, ways if ways is not None else params):
            local.value = param
            local.value_span = stmt.span
        builder.set_memory(exit_token if ways is not None else token)

    def _bind_turn(self, builder: IRBuilder, stmt: ast.ForEach,
                   value: Value) -> None:
        """Bind what a turn gave to the names the loop was written with."""
        if stmt.more:
            self._name_value(value, stmt.name, stmt.name_span)
            self._bind_apart(builder, stmt, value)
            return
        if stmt.name == WILDCARD_NAME:
            # The name that is not a name, as it is in a `match` arm: the loop
            # runs a turn for each value there is and the value itself is not
            # wanted.  Nothing is bound, so nothing is reported as unread.
            return
        self._name_value(value, stmt.name, stmt.name_span)
        self._bind_local(stmt.name, value, stmt.name_span,
                         value_span=stmt.iterable.span)

    # -- what a loop can take its values from ----------------------------------

    def _iteration_over(self, builder: IRBuilder,
                        stmt: ast.ForEach) -> _Iteration | None:
        """What the loop's expression turns out to be, as a thing to walk.

        A range is written where it is used and has no type of its own, so it is
        recognised before anything is lowered; everything else is a value, and
        what it is, is its type's business.
        """
        if isinstance(stmt.iterable, ast.Range):
            return self._over_a_range(builder, stmt)
        declared = self._resolve_type(stmt.type) if stmt.type is not None else None
        value = self._lower_expr(builder, stmt.iterable, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        found: _Iteration | None
        if isinstance(ty, ArrayType):
            found = self._over_an_array(builder, value, ty, stmt.span)
        elif isinstance(ty, (SetType, DictType)):
            found = self._over_a_table(builder, value, ty, stmt.span)
        else:
            self._diags.emit(D.LANG_LOOP_NOT_AN_ITERATOR, stmt.iterable.span,
                             found=ty.render())
            return None
        if declared is not None and declared is not found.element:
            self._report_mismatch(stmt.iterable.span, found.element, declared)
            return None
        return found

    def _over_a_range(self, builder: IRBuilder,
                      stmt: ast.ForEach) -> _Iteration | None:
        """Walk the whole numbers a range stands for."""
        found = self._range_of(builder, stmt)
        if found is None:
            return None
        first, last, step, rising, element = found
        signed = element.signed
        going = ((CmpPred.SLT if signed else CmpPred.ULT) if rising
                 else (CmpPred.SGT if signed else CmpPred.UGT))
        # The step saturates rather than checking, so that a range whose last
        # turn would step past the end of its own type ends instead of faulting:
        # what a turn past the end would be is not a value, and the comparison
        # is what says there is no turn.
        moving = BinOp.SAT_ADD if rising else BinOp.SAT_SUB
        return _Iteration(
            element=element, start=(first,),
            more=lambda b, s: b.compare(going, s[0], last, stmt.span),
            take=lambda b, s: s[0],
            step=lambda b, s: (b.binary(moving, s[0], step, stmt.span),))

    def _over_an_array(self, builder: IRBuilder, value: Value, ty: ArrayType,
                       span: Span) -> _Iteration:
        """Walk an array along its outermost dimension.

        A turn gives an element where the array has one dimension and a row
        where it has more, which row-major makes a run of elements and so
        arithmetic on the place rather than a copy.  Where the elements are and
        how many there are do not change from turn to turn, so they are worked
        out once here and read from where they were left.
        """
        start, lengths = self._shape_of(builder, value, ty, span)
        element = ty.element if ty.rank == 1 else self._row_type(ty, 1)

        def take(b: IRBuilder, s: tuple[Value, ...]) -> Value:
            offset = self._by_row(b, s[0], lengths[1:], span)
            if ty.rank == 1:
                return b.load(
                    self._element_place(b, start, ty.element, offset, span), span)
            return self._row_at(b, start, ty, lengths, offset, 1, span)

        return _Iteration(
            element=element, start=(builder.int_const(U64, 0),),
            more=lambda b, s: b.compare(CmpPred.ULT, s[0], lengths[0], span),
            take=take,
            step=lambda b, s: (b.binary(BinOp.WRAP_ADD, s[0],
                                        b.int_const(U64, 1), span),))

    def _over_a_table(self, builder: IRBuilder, value: Value,
                      ty: SetType | DictType, span: Span) -> _Iteration:
        """Walk the keys a set holds, or the pairs a dictionary holds.

        The places a table's entries are in are not all holding keys, so the
        walk steps past the ones that are not; finding the next one is a
        function of the runtime rather than a second loop written here, which
        keeps the shape of this one the shape every other iterator has.

        A dictionary gives a key and what it stands for, together as a tuple.
        Two names take that apart, which is what several names next to each
        other already do everywhere a tuple is bound -- so `foreach k, v = d:`
        needs nothing of its own beyond the tuple.
        """
        tables.ensure_runtime(self._module)
        table = builder.cast(CastKind.BITCAST, value,
                             tables.table_type(self._module), span)
        finder = self._module.functions[tables.NEXT_SYMBOL]
        pointer = tables.table_type(self._module)
        capacity = builder.binary(
            BinOp.WRAP_ADD,
            builder.load(builder.binary(BinOp.ADD, table,
                                        builder.int_const(U64, tables.MASK_FIELD),
                                        span), span),
            builder.int_const(U64, 1), span)
        key_ty = ty.element if isinstance(ty, SetType) else ty.key
        element: Type = key_ty if isinstance(ty, SetType) else \
            self._module.types.tuple_type((ty.key, ty.value))

        def read(b: IRBuilder, place: Value, offset: int, held: Type) -> Value:
            """One word of an entry, read as what it holds."""
            return b.load(b.cast(
                CastKind.BITCAST,
                b.binary(BinOp.ADD, place, b.int_const(U64, offset), span),
                self._module.types.ptr_type(held, mutable=True), span), span)

        def take(b: IRBuilder, s: tuple[Value, ...]) -> Value:
            place = tables.entry_at(b, table, s[0])
            key = read(b, place, tables.KEY_AT, key_ty)
            if isinstance(ty, SetType):
                return key
            return b.make_tuple(
                (key, read(b, place, tables.VALUE_AT, ty.value)), element, span)

        return _Iteration(
            element=element,
            start=(builder.call(finder, (table, builder.int_const(U64, 0)),
                                U64, span),),
            more=lambda b, s: b.compare(CmpPred.ULT, s[0], capacity, span),
            take=take,
            step=lambda b, s: (b.call(
                finder,
                (table, b.binary(BinOp.WRAP_ADD, s[0], b.int_const(U64, 1), span)),
                U64, span),))

    def _range_of(self, builder: IRBuilder, stmt: ast.ForEach
                  ) -> tuple[Value, Value, Value, bool, IntType] | None:
        """What a loop's expression gives out: where it starts, where it stops,
        how far it moves, which way it runs, and of what type.

        Nothing but a range is one of these yet, and anything else is reported
        as not being something to take values from rather than as not being a
        range -- what the loop wants is an iterator, and a range is merely the
        only thing that is one.
        """
        written = stmt.iterable
        if not isinstance(written, ast.Range):
            value = self._lower_expr(builder, written, None)
            found = self._value_type_of(value)
            if found is not ERROR:
                self._diags.emit(D.LANG_LOOP_NOT_AN_ITERATOR, written.span,
                                 found=found.render())
            return None
        declared = self._resolve_type(stmt.type) if stmt.type is not None else None
        first = (self._lower_into(builder, written.start, declared,
                                  written.start.span) if declared is not None
                 else self._lower_expr(builder, written.start, None))
        element = self._value_type_of(first)
        if element is ERROR:
            return None
        if not isinstance(element, IntType):
            self._diags.emit(D.LANG_RANGE_NOT_AN_INTEGER, written.start.span,
                             found=element.render())
            return None
        last = self._lower_expr(builder, written.stop, element)
        other = self._value_type_of(last)
        if other is ERROR:
            return None
        if other != element:
            self._diags.emit(D.LANG_RANGE_ENDS_DIFFER, written.span,
                             first=element.render(), second=other.render())
            return None
        distance, rising = self._range_step(written, element)
        if distance is None:
            return None
        return first, last, builder.int_const(element, distance), rising, element

    def _range_step(self, written: ast.Range,
                    element: IntType) -> tuple[int | None, bool]:
        """How far a range moves each turn, and whether it counts up.

        It is written down rather than computed.  Which way the range runs
        follows from its sign, and that decides which comparison ends the loop;
        a step the compiler cannot read would need both comparisons and a choice
        between them on every turn, for a generality nothing has asked for.  The
        sign is read here and the distance is what is left, so a range that
        counts down over an unsigned type is written the way one that counts up
        is.
        """
        if written.step is None:
            return 1, True
        if not isinstance(written.step, ast.IntLit):
            self._diags.emit(D.LANG_RANGE_STEP_NOT_WRITTEN_DOWN,
                             written.step.span)
            return None, True
        given = written.step.value
        if given == 0:
            self._diags.emit(D.LANG_RANGE_STEP_IS_ZERO, written.step.span)
            return None, True
        distance = abs(given)
        if not element.holds(distance):
            self._diags.emit(D.LANG_SYNTAX_INTEGER_RANGE, written.step.span,
                             literal=str(distance), type=element.render())
            return None, True
        return distance, given > 0

    def _loop_locals(self, body: ast.Block,
                     alternative: ast.Block | None = None) -> list[_Local]:
        """The names in scope that a turn of the loop may change.

        Asked of the syntax rather than of what the lowering turns out to do,
        because the answer is wanted before the body is lowered.  Over-counting
        would cost a parameter the allocator then coalesces away; under-counting
        would be wrong, so what is collected is every assignment anywhere in the
        body, including inside a nested loop or the arms of an `if`.  A
        definition binds a new name and is not one of these.

        The `else` arm counts too, though it runs once and outside the loop:
        what follows the loop is reached both through it and through a `break`,
        so a name it changes is a name the two ways disagree about, which is the
        same reason the body's are counted.
        """
        found: dict[int, _Local] = {}
        names = list(_assigned_in(body))
        if alternative is not None:
            names.extend(_assigned_in(alternative))
        for name in names:
            local = self._find_local(name)
            if local is not None:
                found.setdefault(id(local), local)
        return list(found.values())

    # -- match -----------------------------------------------------------------

    def _lower_match(self, builder: IRBuilder, stmt: ast.Match, func: Function,
                     wanted: Type | None, produces: bool) -> Value:
        """Check and lower a `match`, which takes a value's alternatives apart.

        `produces` says whether a value is wanted of it.  Where one is, every
        arm ends in a statement that has one and they are all of one type, and
        the block the arms join at carries it out; where none is, an arm is a
        run of statements like any other body.
        """
        subject = self._lower_expr(builder, stmt.subject, None)
        ty = self._value_type_of(subject)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, (ResultType, SumType, EnumType)):
            self._diags.emit(D.LANG_MATCH_NOT_A_CHOICE, stmt.subject.span,
                             found=ty.render())
            return UndefConst(ERROR)
        taken = self._matched_arms(stmt, ty)
        if taken is None:
            return UndefConst(ERROR)
        if isinstance(ty, SumType):
            # Everything about the arms has been checked; what is missing is a
            # value of a sum to take apart, which nothing in the language makes
            # -- and with it the way one is held, which is not a register.
            self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, stmt.span,
                             feature="a match on a sum")
            return UndefConst(ERROR)
        if isinstance(ty, EnumType):
            return self._lower_match_on_enum(builder, stmt, func, subject, ty,
                                             taken, wanted, produces)
        return self._lower_match_on_result(builder, stmt, func, subject, ty,
                                           taken, wanted, produces)

    def _alternatives(self, ty: Type) -> list[tuple[str, Type | None, bool]]:
        """What a value of *ty* may be: a name to report it by, what the
        alternative carries, and whether it is a result's error."""
        if isinstance(ty, ResultType):
            return [(ty.ok.render(), ty.ok, False), (BOTTOM_GLYPH, ty.err, True)]
        if isinstance(ty, EnumType):
            # One alternative per *number*, not per name: two names given one
            # number are one value, and nothing at run time can tell an arm
            # naming the first from an arm naming the second.
            seen: dict[int, str] = {}
            for member, number in zip(ty.members, ty.values):
                seen.setdefault(number, member)
            return [(member, None, False) for member in seen.values()]
        assert isinstance(ty, SumType)
        return [(variant.render(), variant, False) for _, variant in ty.variants]

    def _matched_arms(self, stmt: ast.Match,
                      ty: Type) -> list[tuple[ast.MatchArm, frozenset[int]]] | None:
        """Which alternatives each arm takes, or nothing where the arms are wrong.

        Every alternative must be taken and none twice.  `_` takes every one no
        earlier arm took, so an arm after it -- or one written where nothing is
        left -- can never run, and is reported rather than left standing.
        """
        alternatives = self._alternatives(ty)
        found: list[tuple[ast.MatchArm, frozenset[int]]] = []
        settled: set[int] = set()
        spoiled = False
        for arm in stmt.arms:
            if arm.pattern.wildcard:
                if arm.pattern.name is not None:
                    self._diags.emit(D.LANG_MATCH_WILDCARD_TAKES_NO_NAME,
                                     arm.pattern.span)
                    spoiled = True
                    continue
                left = frozenset(set(range(len(alternatives))) - settled)
                if not left:
                    self._diags.emit(D.LANG_MATCH_ARM_NEVER_RUNS, arm.pattern.span)
                    spoiled = True
                    continue
                settled |= left
                found.append((arm, left))
                continue
            index = self._alternative_of(arm.pattern, ty, alternatives)
            if index is None:
                spoiled = True
                continue
            if index in settled:
                self._diags.emit(D.LANG_MATCH_REPEATED_ARM, arm.pattern.span)
                spoiled = True
                continue
            settled.add(index)
            carried = alternatives[index][1]
            if arm.pattern.name is not None and (carried is None or carried is VOID):
                self._diags.emit(D.LANG_MATCH_BINDS_NOTHING, arm.pattern.span,
                                 name=arm.pattern.name)
                spoiled = True
                continue
            found.append((arm, frozenset((index,))))
        for index, (name, _, _) in enumerate(alternatives):
            if index not in settled:
                self._diags.emit(D.LANG_MATCH_NOT_EXHAUSTIVE, stmt.span, missing=name)
                spoiled = True
        if isinstance(ty, EnumType) and ty.flag \
                and not any(arm.pattern.wildcard for arm, _ in found):
            # Its values combine, so a value of it may be one no single name
            # stands for: naming every name does not account for every value.
            self._diags.emit(D.LANG_MATCH_FLAG_NEEDS_A_REST, stmt.span,
                             type=ty.render())
            spoiled = True
        return None if spoiled else found

    def _alternative_of(self, pattern: ast.Pattern, ty: Type,
                        alternatives: Sequence[tuple[str, Type | None, bool]]
                        ) -> int | None:
        """Which alternative a pattern takes, reporting one that takes none."""
        if pattern.type is None:
            if not isinstance(ty, ResultType):
                self._diags.emit(D.LANG_MATCH_BOTTOM_NEEDS_A_RESULT, pattern.span,
                                 found=ty.render())
                return None
            return next(i for i, (_, _, bottom) in enumerate(alternatives) if bottom)
        if isinstance(ty, EnumType):
            # The alternatives of an enumeration are its values, so an arm of one
            # names a value and not a type.
            written = pattern.type
            if not isinstance(written, ast.TypeRef):
                self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, pattern.span,
                                 name="that", type=ty.render())
                return None
            index = ty.index_of(written.name)
            if index is None or written.module is not None or written.result:
                self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, pattern.span,
                                 name=written.name, type=ty.render())
                return None
            # Which alternative, which is which *number*: two names given one
            # number name one alternative between them.
            wanted = ty.values[index]
            return list(dict.fromkeys(ty.values)).index(wanted)
        named = self._resolve_type(pattern.type)
        if named is ERROR:
            return None
        for index, (_, carried, bottom) in enumerate(alternatives):
            if not bottom and carried is named:
                return index
        self._diags.emit(D.LANG_MATCH_UNKNOWN_ALTERNATIVE, pattern.span,
                         type=named.render(), found=ty.render())
        return None

    def _lower_match_on_result(self, builder: IRBuilder, stmt: ast.Match,
                               func: Function, subject: Value, ty: ResultType,
                               taken: list[tuple[ast.MatchArm, frozenset[int]]],
                               wanted: Type | None, produces: bool) -> Value:
        """Lower a `match` over a result, which has two alternatives.

            entry:     condbr failed \N{RIGHTWARDS ARROW} error, answer
            answer:    the arm that names the answer type
            error:     the arm written `\N{UP TACK}`
            matched(p): whatever a name assigned in an arm now stands for

        One arm may take both, where it is the wildcard: then there is nothing
        to ask and nothing to branch on, and the arm is the whole of it.
        """
        if len(taken) == 1:
            return self._run_arms(builder, stmt, func,
                                  [_ArmPlan(body=taken[0][0].body)],
                                  wanted, produces)
        holders = {index: arm for arm, indices in taken for index in indices}
        answered = builder.new_block("answer")
        failed = builder.new_block("error")
        builder.condbr(builder.failed(subject, stmt.span), failed, answered,
                       span=stmt.span)
        first, second = holders[0], holders[1]
        binds = None if first.pattern.name is None else (
            first.pattern.name, first.pattern.name_span, first.pattern.span,
            subject, ty.ok)
        return self._run_arms(
            builder, stmt, func,
            [_ArmPlan(body=first.body, block=answered, binds=binds),
             _ArmPlan(body=second.body, block=failed)], wanted, produces)

    def _lower_match_on_enum(self, builder: IRBuilder, stmt: ast.Match,
                             func: Function, subject: Value, ty: EnumType,
                             taken: list[tuple[ast.MatchArm, frozenset[int]]],
                             wanted: Type | None, produces: bool) -> Value:
        """Lower a `match` over an enumeration.

        A chain of comparisons, one per value an arm names, with the last arm --
        or the wildcard, where there is one -- reached by falling off the end of
        the chain.  A jump table would be the other way and wants the relocation
        work that position-independent code needs anyway, which the to-do list
        carries.
        """
        fallback = next((arm for arm, _ in taken if arm.pattern.wildcard),
                        taken[-1][0])
        blocks = {id(arm): builder.new_block("case") for arm, _ in taken}
        for arm, indices in taken:
            if arm is fallback:
                continue
            for index in sorted(indices):
                following = builder.new_block("otherwise")
                asked = builder.compare(
                    CmpPred.EQ, subject,
                    self._module.enum_const(ty, ty.values.index(
                        list(dict.fromkeys(ty.values))[index])),
                    arm.pattern.span)
                builder.condbr(asked, blocks[id(arm)], following,
                               span=arm.pattern.span)
                builder.position_at(following)
        builder.br(blocks[id(fallback)], (), stmt.span)
        return self._run_arms(
            builder, stmt, func,
            [_ArmPlan(body=arm.body, block=blocks[id(arm)]) for arm, _ in taken],
            wanted, produces)

    def _run_arms(self, builder: IRBuilder, stmt: ast.Match | ast.If,
                  func: Function,
                  plan: Sequence[_ArmPlan], wanted: Type | None = None,
                  produces: bool = False,
                  otherwise: BasicBlock | None = None) -> Value:
        """Lower each arm into its block and join what the arms leave behind.

        A name bound outside the match and assigned inside one arm stands for
        one value per arm afterwards, so the block the arms join at takes it as
        a parameter and each arm hands its own over.  The memory token is
        merged the same way and for the same reason: two arms that both touch
        memory arrive with two tokens.
        """
        joined = builder.new_block("matched")
        before = [(local, local.value, local.value_span)
                  for scope in self._scopes for local in scope.values()]
        outer_carried = self._carried
        self._carried = outer_carried | {id(local) for local, _, _ in before}
        before_memory = builder.memory()
        outcomes: list[tuple[BasicBlock, dict[int, Value], Value, Value | None]] = []
        changed: dict[int, _Local] = {}
        touched = False
        answer: Type | None = wanted
        for arm in plan:
            if arm.block is not None:
                builder.position_at(arm.block)
            builder.set_memory(before_memory)
            self._push_scope()
            if arm.binds is not None:
                name, name_span, where_span, value, answer_ty = arm.binds
                bound = builder.unwrap(value, answer_ty, where_span)
                self._name_value(bound, name, name_span)
                self._bind_local(name, bound, name_span, value_span=where_span)
            given = self._lower_block(builder, arm.body, func, as_result=False,
                                      wanted=answer, produces=produces)
            if produces and given is not None and answer is None:
                # Nothing said what the arms answer with, so the first one that
                # does say.  The rest are checked against it.
                found = self._value_type_of(given)
                answer = found if found is not ERROR else None
            moved = {id(local): local.value for local, held, _ in before
                     if local.value is not held}
            for local, _, _ in before:
                if id(local) in moved:
                    changed[id(local)] = local
            self._pop_scope()
            if not builder.is_terminated and builder.block is not None:
                outcomes.append((builder.block, moved, builder.memory(), given))
                touched = touched or builder.memory() is not before_memory
            for local, held, where in before:
                local.value, local.value_span = held, where
        if otherwise is not None:
            # A way through that runs no arm at all, which is what an `if` with
            # no `else` has.  Nothing changed along it and nothing was written.
            outcomes.append((otherwise, {}, before_memory, None))
        self._carried = outer_carried
        if not outcomes:
            # Every arm left the function, so nothing arrives at the join and a
            # block with no way in is a block that should not be there.
            func.blocks.remove(joined)
            builder.set_memory(before_memory)
            return UndefConst(answer if answer is not None else ERROR)
        merged = list(changed.values())
        params = [joined.add_param(self._value_type_of(local.value), local.name)
                  for local in merged]
        memory = joined.add_param(MEM, "mem") if touched else None
        produced = (joined.add_param(answer, "answer")
                    if produces and answer is not None else None)
        held_before = {id(local): value for local, value, _ in before}
        for block, moved, token, given in outcomes:
            builder.position_at(block)
            args = [moved.get(id(local), held_before[id(local)]) for local in merged]
            if memory is not None:
                args.append(token)
            if produced is not None:
                args.append(given if given is not None
                            else UndefConst(produced.ty))
            builder.br(joined, tuple(args), stmt.span)
        builder.position_at(joined)
        for local, param in zip(merged, params):
            local.value = param
            # The value it now stands for was given by the match, so that is
            # where a report about nothing reading it should point.
            local.value_span = stmt.span
            local.read = False
        if memory is not None:
            builder.set_memory(memory)
        return produced if produced is not None else UndefConst(VOID)

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

        A call is the one expression this cannot say that about, since a call
        does whatever the callee does whether or not anyone wants its result.
        What is asked of one is a different question, and `_answer_is_taken`
        asks it.
        """
        if isinstance(expr, ast.Call):
            self._answer_is_taken(expr)
            return
        found = self._diags.emit(D.LANG_STMT_VALUE_DISCARDED, expr.span)
        if isinstance(expr, ast.Binary) and expr.op is ast.BinaryOp.EQUAL:
            found.note(D.LANG_STMT_ASSIGNMENT_IS_AN_ARROW, expr.span)

    def _answer_is_taken(self, expr: ast.Call) -> None:
        """Report a call, standing as a statement, whose answer goes nowhere.

        A function that answers with something is a function whose answer is
        the point of calling it, so by default the answer has to be taken.  The
        two ways of saying that it need not be are one line and one word: `_` to
        drop this answer, and `@[can_ignore]` on the function where that is true
        of every call to it.

        A call that answers with nothing is a call made for what it does, which
        is what standing as a statement already says.
        """
        func = self._callee_named(expr.callee)
        if func is None or func.ty.ret is VOID or func.attrs.can_ignore:
            return
        self._diags.emit(D.LANG_CALL_ANSWER_DROPPED, expr.span, name=func.name)

    def _callee_named(self, expr: ast.Expr) -> Function | None:
        """The function a call names, asked of the syntax and reporting nothing.

        Whether the callee is a function at all is the call's own business and
        is reported where the call is lowered; this is asked before that, so it
        answers nothing rather than saying anything.
        """
        if isinstance(expr, ast.NameRef):
            found = self._top.get(expr.name)
            return found if isinstance(found, Function) else None
        return None

    def _dropped(self, builder: IRBuilder, node: ast.AssignStmt,
                 wants_value: bool) -> Value | None:
        """Lower `_ \N{LEFTWARDS ARROW} v`: work the value out and deliberately drop it.

        `_` is not a variable and is not defined anywhere: it is where a value
        goes when the program means to work it out and not use it, which is what
        a call made for what it does rather than for what it answers needs to be
        able to say.  So there is no mutability to check, nothing to rebind, and
        nothing for a later line to read.

        What is written must still produce something.  Dropping nothing is not a
        thing to say, and the call that answers with nothing is already the
        statement it should be.
        """
        value = self._lower_expr(builder, node.value, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if ty is VOID:
            name = self._callee_named(node.value.callee).name \
                if isinstance(node.value, ast.Call) \
                and self._callee_named(node.value.callee) is not None \
                else "this"
            self._diags.emit(D.LANG_WILDCARD_TAKES_A_VALUE, node.span, name=name)
            return None
        if wants_value:
            # The last statement of a body is the body's result, and what this
            # statement leaves behind is nothing anybody may read.
            self._diags.emit(D.LANG_WILDCARD_IS_NOT_READ, node.span)
            return None
        return None

    def _lower_local(self, builder: IRBuilder, node: ast.VarDef) -> None:
        """Lower a variable defined inside a function body.

        A local is a value, not a place: the name is bound to whatever the
        initializer produced.  Nothing is reserved in memory, because nothing
        can take its address yet -- and where the language later lets a name be
        assigned, a block parameter is what carries the new value across a
        branch, which is why the representation has them.
        """
        self._bind_attributes(node.attrs, AttrTarget.VARIABLE)
        if node.name == WILDCARD_NAME:
            # It is where a value goes to be dropped, everywhere, without being
            # defined anywhere; a definition would make it a variable of that
            # scope instead, which is a second meaning for one spelling.
            self._diags.emit(D.LANG_WILDCARD_IS_NOT_DEFINED, node.span)
            return
        if node.type is None and not _says_its_type(node.value):
            # Nothing written and nothing to read off the value: it is lowered
            # first and what it turned out to be is what the name stands for.
            # That is what lets `let a, b := f()` work, a call being the usual
            # thing to take a tuple from.
            self._lower_derived(builder, node)
            return
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
            value = self._lower_into(builder, node.value, declared, node.span)
        finally:
            self._initializing = None
        if node.more:
            self._bind_apart(builder, node, value)
            return
        bound = self._as_declared(value, declared)
        self._name_value(bound, node.name, node.name_span)
        self._bind_local(node.name, bound, node.name_span, node.mutable,
                         value_span=node.span)

    def _lower_derived(self, builder: IRBuilder, node: ast.VarDef) -> None:
        """Lower a variable whose type is whatever its value turns out to be."""
        self._initializing = node.name
        try:
            value = self._lower_expr(builder, node.value, None)
        finally:
            self._initializing = None
        if node.more:
            self._bind_apart(builder, node, value)
            return
        self._name_value(value, node.name, node.name_span)
        self._bind_local(node.name, value, node.name_span, node.mutable,
                         value_span=node.span)

    def _bind_apart(self, builder: IRBuilder,
                    node: ast.VarDef | ast.ForEach, value: Value) -> None:
        """Bind each name of a definition, or of a loop, that takes a tuple apart.

        A loop's names are never `mut`: what they stand for is what the turn
        gave, and the next turn gives another.
        """
        mutable = getattr(node, "mutable", False)
        names = [(node.name, node.name_span), *node.more]
        members = self._taken_apart(value, names, node.span)
        for index, (name, where) in enumerate(names):
            if members is None:
                self._bind_local(name, UndefConst(ERROR), where, mutable,
                                 value_span=node.span)
                continue
            part = builder.extract(value, index, members[index], node.span)
            self._name_value(part, name, where)
            self._bind_local(name, part, where, mutable, value_span=node.span)

    def _name_value(self, value: Value, name: str,
                    where: Span = INVALID_SPAN) -> None:
        """Record which local a computed value belongs to.

        Only an instruction is named, and only if it has no name already.  A
        constant is interned and shared with every other use of the same number,
        so writing a name on one would put that name on all of them; and where
        two locals stand for one value, the first name is the one that stays.

        The name is a hint and nothing reads it to decide anything.  It is what
        lets the textual form be read against the source it came from, and what
        lets a pass that removes a value say which local went with it -- and
        *where*, which is why the name's own span travels with it.  Pointing at
        the instruction instead would point at the initializer, which is
        somewhere else on the line and sometimes on another line entirely.
        """
        if isinstance(value, Instruction) and value.name_hint is None:
            value.name_hint = name
            value.name_span = where

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
        if node.more:
            self._assign_apart(builder, node)
            return None
        if node.name == WILDCARD_NAME:
            return self._dropped(builder, node, wants_value)
        local = self._find_local(node.name)
        if local is not None:
            if not self._check_mutable(node, local.mutable, local.span):
                return None
            value = self._checked_value(builder, node, local.value.ty)
            # The value the name stood for is gone; if nothing read it, giving
            # it cannot have affected what the program does.
            if id(local) not in self._carried:
                # Inside an arm of a `match` the question cannot be answered
                # here: whether the value this one replaces is read depends on
                # the other arms and on what follows them, so a straight-line
                # answer would be a guess.
                self._report_unused(local)
            local.value = value
            local.value_span = node.span
            local.read = wants_value
            # What the name stands for is no longer what the caller gave, so
            # what happens to it from here is this function's business: a value
            # it assigned and nothing read is a value it need not have worked
            # out, which is exactly what the rule is about.
            local.is_parameter = False
            return value
        target = self._provided(node.name)
        if not isinstance(target, GlobalVar):
            target = None
        if target is None:
            self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, node.name_span,
                             name=node.name)
            return None
        if not self._check_mutable(node, target.mutable, target.span):
            return None
        self._an_effect(D.LANG_PURE_CHANGES_A_VARIABLE, node.span, name=node.name)
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

    def _assign_apart(self, builder: IRBuilder, node: ast.AssignStmt) -> None:
        """Lower an assignment that takes a tuple apart.

        Each name is assigned as it would be on its own, so a name that is not
        a variable, or one nothing may change, is reported where it is written.
        """
        value = self._lower_expr(builder, node.value, None)
        names = [(node.name, node.name_span), *node.more]
        members = self._taken_apart(value, names, node.span)
        if members is None:
            return
        for index, (name, where) in enumerate(names):
            part = builder.extract(value, index, members[index], node.span)
            one = ast.AssignStmt(span=where, name=name, name_span=where,
                                 value=node.value)
            self._store_into(builder, one, part)

    def _store_into(self, builder: IRBuilder, node: ast.AssignStmt,
                    value: Value) -> None:
        """Put an already lowered value into the variable *node* names."""
        local = self._find_local(node.name)
        if local is not None:
            if not self._check_mutable(node, local.mutable, local.span):
                return
            if self._value_type_of(local.value) is not self._value_type_of(value):
                self._report_mismatch(node.span, self._value_type_of(value),
                                      self._value_type_of(local.value))
                return
            if id(local) not in self._carried:
                self._report_unused(local)
            local.value = value
            local.value_span = node.span
            local.read = False
            return
        target = self._provided(node.name)
        if not isinstance(target, GlobalVar):
            self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, node.name_span,
                             name=node.name)
            return
        if not self._check_mutable(node, target.mutable, target.span):
            return
        if self._value_type_of(value) is not target.value_type:
            self._report_mismatch(node.span, self._value_type_of(value),
                                  target.value_type)
            return
        self._an_effect(D.LANG_PURE_CHANGES_A_VARIABLE, node.span, name=node.name)
        builder.store(target, value, node.span)

    def _checked_value(self, builder: IRBuilder, node: ast.AssignStmt,
                       expected: Type) -> Value:
        """Lower the value of an assignment, checking it against the variable."""
        self._assigning = node.name
        try:
            value = self._lower_into(builder, node.value, expected, node.span)
        finally:
            self._assigning = None
        return self._as_declared(value, expected)

    def _return_type(self, ref: ast.TypeExpr | None) -> Type:
        """What a function answers with, from what its definition wrote.

        Nothing written means nothing answered with.  Writing `void` out is
        refused: it would be a second spelling of what the absence already says,
        and `void` is not a type any value can have, so naming it as one says
        less than leaving it out.
        """
        if ref is None:
            return VOID
        if isinstance(ref, ast.TypeRef) and ref.name == "void" and not ref.result:
            self._diags.emit(D.LANG_TYPE_NOTHING_IS_NOT_WRITTEN, ref.span)
            return VOID
        found = self._resolve_type(ref)
        if isinstance(found, ArrayType):
            # What a value of an array type is, is where its elements are, and
            # the elements of an array a function made are in that function's
            # own room -- gone by the time the caller reads them.  A slice of
            # something that outlives the call would be safe, and there is no
            # way yet to say that one does.
            self._diags.emit(D.LANG_ARRAY_ANSWERED_WITH, ref.span,
                             found=found.render())
            return ERROR
        return found

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
        builder.ret(self._lower_into(builder, stmt.value, func.ty.ret, stmt.span),
                    stmt.span)

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
            case ast.FloatLit():
                ty = self._float_literal_type(expr, expected)
                if ty is None:
                    return UndefConst(ERROR)
                return builder.float_const(ty, expr.value)
            case ast.BoolLit():
                if not self._accepts(expected, BOOL):
                    self._report_mismatch(expr.span, BOOL, expected)
                    # The mistake is reported; carrying on with a truth value
                    # would have whatever reads it report the same thing again.
                    return UndefConst(ERROR)
                return builder.bool_const(expr.value)
            case ast.Call():
                return self._lower_call(builder, expr, expected)
            case ast.NameRef():
                return self._lower_name(builder, expr, expected)
            case ast.TupleLit():
                return self._lower_tuple(builder, expr, expected)
            case ast.ArrayLit():
                return self._lower_array(builder, expr, expected)
            case ast.Element():
                return self._lower_element(builder, expr, expected)
            case ast.Range():
                # A range is a source of values for a loop and not a value.
                # Giving it a name would make it one, with a type and a place in
                # memory, and nothing yet needs that.
                self._diags.emit(D.LANG_RANGE_OUTSIDE_A_LOOP, expr.span)
                return UndefConst(ERROR)
            case ast.SetLit() | ast.DictLit():
                return self._lower_collection(builder, expr, expected)
            case ast.Index():
                return self._lower_index(builder, expr, expected)
            case ast.If():
                return self._lower_if(builder, expr, builder.function, expected,
                                      True)
            case ast.While():
                return self._lower_while(builder, expr, builder.function,
                                         expected, True)
            case ast.ForEach():
                return self._lower_foreach(builder, expr, builder.function,
                                           expected, True)
            case ast.Match():
                return self._lower_match(builder, expr, builder.function,
                                         expected, True)
            case _Ready():
                return expr.value
            case ast.Try():
                return self._lower_try(builder, expr, expected)
            case ast.Binary() if expr.op is ast.BinaryOp.OR_ELSE:
                return self._lower_or_else(builder, expr, expected)
            case ast.Binary() if expr.op in _APPROXIMATE:
                return self._lower_approximate(builder, expr, expected)
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
        # What is wanted must be a truth value, or an array of them where the
        # operands turn out to be arrays; which of the two it is, is not known
        # until they are lowered, so what is asked here is only that the answer
        # could be either.
        if expected is not None and self._scalar_of(expected) is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        context = self._hint_of(expr.left) or self._hint_of(expr.right)
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._comparable(expr.left.span, expr.op,
                                  self._scalar_of(self._value_type_of(left)))
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
            self._listing = was_listing
        if ty is not ERROR:
            walked = self._walk_operands(builder, expr,
                                         (("left", left), ("right", right)),
                                         expected)
            if walked is not None:
                return walked
        found = self._comparable(expr.right.span, expr.op,
                                 self._scalar_of(self._value_type_of(right)))
        if ty is ERROR or found is ERROR:
            return UndefConst(ERROR)
        if found is not ty:
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, expr.right.span,
                             operator=expr.op.value, expected=ty.render(),
                             found=found.render())
            return UndefConst(ERROR)
        if isinstance(ty, FloatType) and expr.op in _EXACT_ON_FLOATS:
            # Two floating-point values computed different ways are rarely the
            # one value, so asking whether they are is nearly always the wrong
            # question -- but not always, which is why this is a warning and why
            # the approximate comparisons are written differently rather than
            # this one quietly becoming approximate.
            self._diags.emit(D.LANG_TYPE_EXACT_FLOAT_COMPARISON, expr.span,
                             operator=expr.op.value, type=ty.render())
        signed, unsigned = _COMPARISONS[expr.op]
        # A truth value is one or zero, so where it is ordered at all it is
        # ordered as an unsigned number; a floating-point value is ordered the
        # way a signed number is, and nothing else here is ordered at all.
        signed_reading = isinstance(ty, FloatType) or (isinstance(ty, IntType)
                                                       and ty.signed)
        if not self._accepts(expected, BOOL):
            # Nothing was walked, so the answer is one truth value after all.
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        return builder.compare(signed if signed_reading else unsigned,
                               left, right, expr.span)

    def _lower_approximate(self, builder: IRBuilder, expr: ast.Binary,
                           expected: Type | None) -> Value:
        """Lower a comparison that allows for the errors floating point makes.

        Each of the six is the difference between the two values measured
        against the tolerance, which is a variable the program can set rather
        than a number built into the compiler.  So the answer is a subtraction,
        a read of that variable and one ordinary comparison -- and for the two
        that ask about likeness in either direction, the magnitude in between.

        The difference is computed in the type that was compared and then
        widened to the tolerance's own, which every narrower format fits in
        exactly.  Doing it that way round rather than widening both operands
        first is one instruction instead of two and gives the same answer.
        """
        # What is wanted must be a truth value, or an array of them where the
        # operands turn out to be arrays; which of the two it is, is not known
        # until they are lowered, so what is asked here is only that the answer
        # could be either.
        if expected is not None and self._scalar_of(expected) is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        context = self._hint_of(expr.left) or self._hint_of(expr.right)
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._scalar_of(self._value_type_of(left))
            assert ty is not None
            if ty is not ERROR and not isinstance(ty, FloatType):
                self._diags.emit(D.LANG_TYPE_APPROXIMATE_NEEDS_A_FLOAT,
                                 expr.left.span, operator=expr.op.value,
                                 found=ty.render())
                ty = ERROR
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
            self._listing = was_listing
        if ty is not ERROR:
            walked = self._walk_operands(builder, expr,
                                         (("left", left), ("right", right)),
                                         expected)
            if walked is not None:
                return walked
        found = self._scalar_of(self._value_type_of(right))
        assert found is not None
        if ty is ERROR or found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, FloatType):
            self._diags.emit(D.LANG_TYPE_APPROXIMATE_NEEDS_A_FLOAT, expr.right.span,
                             operator=expr.op.value, found=found.render())
            return UndefConst(ERROR)
        if found is not ty:
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, expr.right.span,
                             operator=expr.op.value, expected=ty.render(),
                             found=found.render())
            return UndefConst(ERROR)
        exchanged, magnitude, pred = _APPROXIMATE[expr.op]
        first, second = (right, left) if exchanged else (left, right)
        difference = builder.binary(BinOp.SUB, first, second, expr.span)
        if magnitude:
            difference = builder.unary(UnOp.FABS, difference, expr.span)
        if ty is not F64:
            difference = builder.cast(CastKind.FEXT, difference, F64, expr.span)
        tolerance = builder.load(self._tolerance_variable(), expr.span)
        return builder.compare(pred, difference, tolerance, expr.span)

    def _tolerance_variable(self) -> GlobalVar:
        """The variable the approximate comparisons measure against."""
        found = self._provided(TOLERANCE_NAME)
        assert isinstance(found, GlobalVar)
        return found

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
        if expected is not None and self._scalar_of(expected) is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            left = self._boolean(builder, expr.left, expr.op, walked=True)
            right = self._boolean(builder, expr.right, expr.op, walked=True)
        finally:
            self._operand_of = outer
            self._listing = was_listing
        if left is None or right is None:
            return UndefConst(ERROR)
        walked = self._walk_operands(builder, expr,
                                     (("left", left), ("right", right)), expected)
        if walked is not None:
            return walked
        if not self._accepts(expected, BOOL):
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        operation, inverted = _LOGIC_OPS[expr.op]
        found = builder.binary(operation, left, right, expr.span)
        return self._negate(builder, found, expr.span) if inverted else found

    def _lower_try(self, builder: IRBuilder, expr: ast.Try,
                   expected: Type | None) -> Value:
        """Lower `EXPR?`: the answer, or the function leaving with the error.

        The shape is the one the short-circuiting operators use, with the
        difference that the branch nothing comes back from leaves the function
        rather than joining:

            entry:    condbr failed → leaving, answered
            leaving:  ret the error
            answered: unwrap

        The function must itself answer with a result, since it is that result
        the error leaves in.  Its *answer* type need not be the same as this
        one's -- what travels is the error, and so far an error carries nothing,
        so nothing about the two answer types has to agree.
        """
        value = self._lower_expr(builder, expr.operand, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, ResultType):
            self._diags.emit(D.LANG_TYPE_NOT_A_RESULT, expr.operand.span,
                             operator="?", found=ty.render())
            return UndefConst(ERROR)
        answering = self._answering
        if not isinstance(answering, ResultType) or answering.err != ty.err:
            self._diags.emit(D.LANG_TYPE_TRY_NEEDS_A_RESULT, expr.span,
                             found=(answering.render() if answering is not None
                                    else VOID.render()))
            return UndefConst(ERROR)
        if not self._accepts(expected, ty.ok):
            self._report_mismatch(expr.span, ty.ok, expected)
            return UndefConst(ERROR)
        leaving = builder.new_block("leaving")
        answered = builder.new_block("answered")
        builder.condbr(builder.failed(value, expr.span), leaving, answered,
                       span=expr.span)
        builder.position_at(leaving)
        # An error carries nothing, so what is handed back is an answer nothing
        # may read beside the truth value that forbids reading it.
        builder.ret(builder.wrap(UndefConst(answering.ok),
                                 builder.bool_const(True), answering, expr.span),
                    expr.span)
        builder.position_at(answered)
        return builder.unwrap(value, ty.ok, expr.span)

    def _lower_or_else(self, builder: IRBuilder, expr: ast.Binary,
                       expected: Type | None) -> Value:
        """Lower `EXPR ?? DEFAULT`: the answer, or the value written instead.

        The default is only computed where there is no answer, which is the
        same rule `and` and `or` follow and for the same reason: a program that
        wrote a default meant it as what to do instead, not as something to do
        anyway.

            entry:    condbr failed → instead, answered
            instead:  br joined(the default)
            answered: br joined(the answer)
            joined(p): p
        """
        value = self._lower_expr(builder, expr.left, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, ResultType):
            self._diags.emit(D.LANG_TYPE_NOT_A_RESULT, expr.left.span,
                             operator=expr.op.value, found=ty.render())
            return UndefConst(ERROR)
        if not self._accepts(expected, ty.ok):
            self._report_mismatch(expr.span, ty.ok, expected)
            return UndefConst(ERROR)
        instead = builder.new_block("instead")
        answered = builder.new_block("answered")
        joined = builder.new_block("joined")
        answer = joined.add_param(ty.ok, "answer")
        builder.condbr(builder.failed(value, expr.span), instead, answered,
                       span=expr.span)
        builder.position_at(instead)
        default = self._lower_expr(builder, expr.right, ty.ok)
        found = self._value_type_of(default)
        if found is not ty.ok and found is not ERROR:
            self._report_mismatch(expr.right.span, found, ty.ok)
            default = UndefConst(ty.ok)
        elif found is ERROR:
            default = UndefConst(ty.ok)
        builder.br(joined, (default,), expr.span)
        builder.position_at(answered)
        builder.br(joined, (builder.unwrap(value, ty.ok, expr.span),), expr.span)
        builder.position_at(joined)
        return answer

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
        if not self._accepts(expected, BOOL):
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
                 op: ast.BinaryOp | ast.UnaryOp,
                 walked: bool = False) -> Value | None:
        """Lower *expr* where only a truth value will do, or report why not.

        Nothing else counts as one.  C's rule that any number other than zero is
        true is what `if (x = 0)` comes from, and where the question really is
        whether a number is zero, `≠` asks it.

        *walked* says the operator takes an array of them as well, which the
        ones that work out both sides do and the ones that work out one side
        only do not: what `and` would even mean over an array is a question with
        no answer, since which side is worked out is what it is about.
        """
        value = self._lower_expr(builder, expr, BOOL)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if (self._scalar_of(ty) if walked else ty) is not BOOL:
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
        one: Value = builder.bool_const(True)
        if isinstance(value.ty, VecType):
            one = builder.splat(one, value.ty, span)
        return builder.binary(BinOp.XOR, value, one, span)

    #: What each operator a set answers comes to, as walks of the two tables:
    #: which table is walked, which is asked about, and what is wanted of the
    #: answer.  Every one of them makes a table of its own rather than changing
    #: either operand, which is what an operator does everywhere else here.
    _SET_WALKS: Final[dict[ast.BinaryOp, tuple[tuple[bool, int], ...]]] = {
        ast.BinaryOp.BIT_OR: ((True, tables.WANT_EITHER),
                              (False, tables.WANT_EITHER)),
        ast.BinaryOp.BIT_AND: ((True, tables.WANT_PRESENT),),
        ast.BinaryOp.BIT_XOR: ((True, tables.WANT_ABSENT),
                               (False, tables.WANT_ABSENT)),
        ast.BinaryOp.SUBTRACT: ((True, tables.WANT_ABSENT),),
    }

    def _lower_set_operation(self, builder: IRBuilder, op: ast.BinaryOp,
                             ty: SetType, left: Value, right: Value,
                             span: Span) -> Value:
        """What one of the four operators a set answers comes to.

        A table of its own, filled by walking one or both operands.  Neither
        operand is changed: an operator answers with a value everywhere else in
        the language, and a set is no different for being a place in memory.
        """
        tables.ensure_runtime(self._module)
        table_ptr = tables.table_type(self._module)
        as_table = {
            True: builder.cast(CastKind.BITCAST, left, table_ptr, span),
            False: builder.cast(CastKind.BITCAST, right, table_ptr, span),
        }
        # Out of the same arena the left operand came from, which is what keeps
        # an answer where its operands are: a collection made in one arena and
        # combined with another's would otherwise land wherever the compiler
        # happened to put it.
        out = self._new_table(builder, ty, span, comes_from=as_table[True])
        select = self._module.functions[tables.SELECT_SYMBOL]
        into = builder.cast(CastKind.BITCAST, out, table_ptr, span)
        for first, want in self._SET_WALKS[op]:
            builder.call(select, (into, as_table[first], as_table[not first],
                                  builder.int_const(U64, want)), VOID, span)
        return out

    def _comparable(self, span: Span, op: ast.BinaryOp, ty: Type) -> Type:
        """*ty* itself where it may stand on one side of *op*, and ERROR else.

        Ordering asks which of two values comes first, which numbers answer and
        nothing else here does.  Equality asks whether two values are the one
        value, which truth values answer as well.
        """
        if ty is ERROR:
            return ERROR
        if isinstance(ty, (IntType, FloatType)):
            return ty
        if isinstance(ty, (SetType, DictType)) and op not in _ORDERINGS:
            # Two collections are one collection or they are not.  Whether one
            # is part of another is a question Python answers with `<=`; here
            # ordering is about which comes first, and neither does.
            return ty
        if isinstance(ty, EnumType) and op not in _ORDERINGS:
            # Two values of an enumeration are one value or they are not.
            # Which comes first is not a question it answers: the order is the
            # order the definition wrote them in, and the language promises
            # nothing about that.
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
        # An operator is defined on values and not on arrays, so what is wanted
        # of each side is what an array of them would be an array of -- which
        # lets a number stand beside an array and take its element's type.
        context = self._scalar_of(self._aiming_at(expected)) \
            if expected is not None else self._hint_of(expr)
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._scalar_of(self._value_type_of(left))
            assert ty is not None
            if ty is not ERROR and not self._operand_type_stands(expr.op, ty):
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.left.span,
                                 operator=expr.op.value, found=ty.render())
                ty = ERROR
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
            self._listing = was_listing
        if ty is not ERROR:
            walked = self._walk_operands(builder, expr,
                                         (("left", left), ("right", right)),
                                         expected)
            if walked is not None:
                return walked
        found = self._scalar_of(self._value_type_of(right))
        assert found is not None
        if ty is ERROR or found is ERROR:
            return UndefConst(ERROR)
        if not self._operand_type_stands(expr.op, found):
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.right.span,
                             operator=expr.op.value, found=found.render())
            return UndefConst(ERROR)
        if found is not ty:
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, expr.right.span,
                             operator=expr.op.value, expected=ty.render(),
                             found=found.render())
            return UndefConst(ERROR)
        if self._answer_is_already_known(expr, ty, left, right):
            return UndefConst(ERROR)
        if isinstance(ty, SetType):
            return self._lower_set_operation(builder, expr.op, ty, left, right,
                                             expr.span)
        if expr.op in _SHIFTS:
            if expr.op in (ast.BinaryOp.ROTATE_LEFT, ast.BinaryOp.ROTATE_RIGHT) \
                    and isinstance(ty, IntType) and ty.signed:
                # Turning the bits of a signed number round has no meaning as a
                # number, and this language's types say what a value is.
                self._diags.emit(D.LANG_TYPE_ROTATE_IS_UNSIGNED, expr.span,
                                 found=ty.render())
                return UndefConst(ERROR)
            signed = isinstance(ty, IntType) and ty.signed
            return builder.binary(
                self._wrapped(_SHIFTS[expr.op][0 if signed else 1]),
                left, right, expr.span)
        if expr.op in (ast.BinaryOp.DIVIDE, ast.BinaryOp.REMAINDER):
            # These are the operations that have no answer for some pairs of
            # operands, so what they answer with is a result: the number where
            # there is one, and the fact that there is none where there is not.
            answer = self._module.types.result_type(ty)
            if isinstance(ty, FloatType):
                # A third question again, and not either of the two below: the
                # answer is not truncated towards anything, and the only divisor
                # it has no answer for is zero.
                return builder.binary(BinOp.FDIV, left, right, expr.span, answer)
            # One operator, two instructions: dividing signed numbers and
            # dividing unsigned ones are different questions, and the type of
            # what is divided is what says which was asked.
            signed = isinstance(ty, IntType) and ty.signed
            wanted = ((BinOp.SDIV, BinOp.UDIV) if expr.op is ast.BinaryOp.DIVIDE
                      else (BinOp.SREM, BinOp.UREM))
            return builder.binary(wanted[0] if signed else wanted[1],
                                  left, right, expr.span, answer)
        return builder.binary(self._wrapped(_BINARY_OPS[expr.op]), left, right,
                              expr.span)

    #: What each operator that can fault does, where both sides are known.  The
    #: saturating ones are not here: theirs is the answer nearest the end of the
    #: type, which always fits and is never a mistake.
    _ARITHMETIC: Final[dict[ast.BinaryOp, Callable[[int, int], int]]] = {
        ast.BinaryOp.ADD: lambda a, b: a + b,
        ast.BinaryOp.SUBTRACT: lambda a, b: a - b,
        ast.BinaryOp.MULTIPLY: lambda a, b: a * b,
    }

    def _operand_type_stands(self, op: ast.BinaryOp, ty: Type) -> bool:
        """Whether a value of *ty* may stand on one side of *op*."""
        if isinstance(ty, IntType):
            return True
        if isinstance(ty, SetType):
            # The four Python gives a set, written with the same characters:
            # what is in both, in either, in one and not the other, and in the
            # first and not the second.
            return op in _ON_SETS
        if isinstance(ty, EnumType):
            # Only where the definition said the values are meant to be
            # combined.  On an ordinary enumeration a bitwise operator would be
            # asking about bits the type says nothing about.
            return ty.flag and op in _ON_FLAGS
        return isinstance(ty, FloatType) and op in _ON_FLOATS

    def _answer_is_already_known(self, expr: ast.Binary, ty: Type, left: Value,
                                 right: Value) -> bool:
        """Report an operation that can be seen to fault, and say whether it was.

        A program that must stop whenever it is started is one that need not be
        built, and the answer is here to be worked out: both sides are written
        down.  This is the checker and not the folder, because the folder is an
        optimization and a program means the same thing whether or not one runs.
        """
        if self._wrapping:
            # Inside a wrap there is no answer that does not fit: what the
            # operation comes to is its low bits, which is a number of the type
            # whatever the arithmetic came to.
            return False
        if isinstance(left, FloatConst) and isinstance(right, FloatConst):
            return self._float_answer_is_already_known(expr, left, right)
        if not isinstance(left, IntConst) or not isinstance(right, IntConst):
            return False
        if expr.op in (ast.BinaryOp.DIVIDE, ast.BinaryOp.REMAINDER):
            # A division answers with a result, so one that cannot answer is
            # still well formed and its value is the error.  It is reported
            # anyway, as a warning: both operands are written down, so the
            # error is the only thing this program will ever get out of it.
            if right.value == 0:
                self._diags.emit(D.LANG_TYPE_DIVISION_BY_ZERO, expr.span)
            elif isinstance(ty, IntType) and ty.signed \
                    and left.value == ty.low and right.value == -1:
                # The one division that overflows, and the one pair that does it.
                self._diags.emit(D.LANG_TYPE_DIVISION_DOES_NOT_FIT, expr.span,
                                 value=str(-ty.low), type=ty.render())
            return False
        working = self._ARITHMETIC.get(expr.op)
        if working is None or not isinstance(ty, IntType):
            return False
        answer = working(left.value, right.value)
        if ty.holds(answer):
            return False
        self._diags.emit(D.LANG_TYPE_ANSWER_DOES_NOT_FIT, expr.span,
                         value=str(answer), type=ty.render())
        return True

    #: What each of the four does to two numbers.  Python's own arithmetic on
    #: a `float` is the hardware's double precision, so an answer worked out
    #: here is the answer the program would compute -- for `f64`.  For `f32` it
    #: is the answer rounded once instead of twice, which can differ in the last
    #: place, so only the question asked of it is used: whether it is finite.
    _FLOAT_ARITHMETIC: Final[dict[ast.BinaryOp, Callable[[float, float], float]]] = {
        ast.BinaryOp.ADD: lambda a, b: a + b,
        ast.BinaryOp.SUBTRACT: lambda a, b: a - b,
        ast.BinaryOp.MULTIPLY: lambda a, b: a * b,
    }

    def _float_answer_is_already_known(self, expr: ast.Binary, left: FloatConst,
                                       right: FloatConst) -> bool:
        """Report a floating-point operation that can be seen to fault.

        The same rule the integers get, for the same reason: a program that must
        stop whenever it is started need not be built.  What stops it here is an
        answer that is an infinity or is not a number, which is what the check
        after every floating-point operation asks about.
        """
        if expr.op is ast.BinaryOp.DIVIDE:
            if right.value == 0.0:
                # Well formed, and the answer is the error; saying so is a
                # warning for the same reason it is for a whole number.
                self._diags.emit(D.LANG_TYPE_DIVISION_BY_ZERO, expr.span)
                return False
            answer = left.value / right.value
        else:
            working = self._FLOAT_ARITHMETIC.get(expr.op)
            if working is None:
                return False
            answer = working(left.value, right.value)
        if math.isfinite(answer):
            return False
        self._diags.emit(
            D.LANG_TYPE_ANSWER_DOES_NOT_FIT, expr.span,
            value="not a number" if math.isnan(answer) else "an infinity",
            type=left.ty.render())
        return True

    def _lower_unary(self, builder: IRBuilder, expr: ast.Unary,
                     expected: Type | None) -> Value:
        """Lower an operator written before its operand."""
        if expr.op is ast.UnaryOp.LOGIC_NOT:
            return self._lower_not(builder, expr, expected)
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            operand = self._lower_expr(builder, expr.operand,
                                       self._scalar_of(expected))
        finally:
            self._operand_of = outer
            self._listing = was_listing
        walked = self._walk_operands(builder, expr, (("operand", operand),),
                                     expected)
        if walked is not None:
            return walked
        # What the operator is defined on is never a run of values, so the
        # question is asked of what a run of them is a run of -- which is the
        # value's own type where it is not one.
        ty = self._scalar_of(self._value_type_of(operand))
        if ty is ERROR or ty is None:
            return UndefConst(ERROR)
        flagged = isinstance(ty, EnumType) and ty.flag
        if not isinstance(ty, IntType) and not flagged:
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.operand.span,
                             operator=expr.op.value, found=ty.render())
            return UndefConst(ERROR)
        return builder.unary(_UNARY_OPS[expr.op], operand, expr.span)

    def _lower_not(self, builder: IRBuilder, expr: ast.Unary,
                   expected: Type | None) -> Value:
        """Lower `¬`, which answers the opposite of what its operand says."""
        if expected is not None and self._scalar_of(expected) is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            operand = self._boolean(builder, expr.operand, expr.op, walked=True)
        finally:
            self._operand_of = outer
            self._listing = was_listing
        if operand is None:
            return UndefConst(ERROR)
        walked = self._walk_operands(builder, expr, (("operand", operand),),
                                     expected)
        if walked is not None:
            return walked
        if not self._accepts(expected, BOOL):
            self._report_mismatch(expr.span, BOOL, expected)
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
            case ast.Binary() if expr.op is ast.BinaryOp.OR_ELSE:
                # What `??` answers with is the answer inside the result, which
                # is the left side's type with the mark taken off.
                found = self._hint_of(expr.left)
                return found.ok if isinstance(found, ResultType) else found
            case ast.Try():
                found = self._hint_of(expr.operand)
                return found.ok if isinstance(found, ResultType) else found
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
        found = self._provided(name)
        return (self._value_type_of(found) if isinstance(found, GlobalVar)
                else None)

    def _float_literal_type(self, expr: ast.FloatLit,
                            expected: Type | None) -> FloatType | None:
        """The type a floating-point literal has, from its suffix or its place."""
        expected = self._aiming_at(expected)
        named = BUILTIN_TYPES.get(expr.type_name) if expr.type_name is not None else None
        if named is not None and expected is not None and named is not expected:
            self._report_mismatch(expr.span, named, expected)
            return None
        found = named if named is not None else expected
        if found is None:
            self._diags.emit(
                D.IMPL_UNIMPLEMENTED_FEATURE, expr.span,
                feature=("a floating-point literal with neither a type suffix nor a "
                         "context that gives it a type"))
            return None
        if not isinstance(found, FloatType):
            self._report_mismatch(expr.span, named or F64, found)
            return None
        return found

    def _literal_type(self, expr: ast.IntLit, expected: Type | None) -> IntType | None:
        """The type an integer literal has, from its suffix or from the context.

        A suffix says what the literal is; a context says what is wanted.  Where
        both are present they must agree, and where neither is the literal is an
        untyped value, which this compiler does not have yet.
        """
        expected = self._aiming_at(expected)
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

    def _lower_wrap(self, builder: IRBuilder, expr: ast.Call,
                    expected: Type | None) -> Value:
        """Lower `⎕wrap(EXPR)`: the expression, with its operators wrapping.

        It looks like a call and is not one.  Nothing is called, nothing is
        passed, and what it answers with is what the expression inside answers
        with -- so what is wanted of the whole is what is wanted of the
        expression, and an unsuffixed literal inside takes its type from outside
        exactly as it would have without the wrap.

        What it changes is what the operators *written inside it* mean, which is
        why it is lexical and why it stops at a call: the body of a function
        called from inside was written somewhere else and says for itself what
        its operators mean.
        """
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_WRAP_TAKES_ONE_EXPRESSION, expr.span,
                             found=len(expr.args))
            return UndefConst(ERROR)
        outer, self._wrapping = self._wrapping, True
        try:
            return self._lower_expr(builder, expr.args[0], expected)
        finally:
            self._wrapping = outer

    def _wrapped(self, op: BinOp) -> BinOp:
        """The operation to emit for *op* where it stands, wrapping or not."""
        return _WRAPPED.get(op, op) if self._wrapping else op

    def _lower_call(self, builder: IRBuilder, expr: ast.Call,
                    expected: Type | None) -> Value:
        """Lower a call, checking it against what the function takes and gives.

        A function is not a value, so what is called is resolved here rather
        than lowered as an expression: there is nothing for a name that stands
        for a function to become.
        """
        if isinstance(expr.callee, ast.NameRef) and expr.callee.name == WRAP_NAME:
            return self._lower_wrap(builder, expr, expected)
        func = self._callee(expr.callee)
        if func is None:
            return UndefConst(ERROR)
        wanted = func.ty.params
        # Left to right, one argument at a time, a tuple handed over as several
        # becoming them where it stands.  Which parameter an argument goes to is
        # therefore known by the time it is lowered, which is what an unsuffixed
        # literal needs, and nothing is worked out before something written to
        # its left.
        outer_listing, self._listing = self._listing, func.attrs.listable
        try:
            args = self._one_by_one(builder, expr.args, wanted, func.name)
        finally:
            self._listing = outer_listing
        if args is None:
            return UndefConst(ERROR)
        if len(args) != len(wanted):
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=func.name, expected=len(wanted),
                             found=len(args))
            return UndefConst(ERROR)
        if any(value.ty is ERROR for value in args):
            return UndefConst(ERROR)
        if func.attrs.listable \
                and any(value.ty is not ty for value, ty in zip(args, wanted)):
            return self._walked(builder, func, args, expr, expected)
        if func.attrs.impure:
            self._an_effect(D.LANG_PURE_CALLS_IMPURE, expr.span, name=func.name)
        answer = builder.call(func, args, func.ty.ret, expr.span)
        if func.ty.ret is VOID and expected is not None:
            # Somewhere wants a value and there is none.  The two places a call
            # like this may stand are a statement of its own and after `return`
            # in a function that also answers with nothing, and neither of them
            # asks for one.
            self._diags.emit(D.LANG_CALL_HAS_NO_VALUE, expr.span, name=func.name)
            return UndefConst(ERROR)
        if not self._accepts(expected, answer.ty):
            self._report_mismatch(expr.span, answer.ty, expected)
            return UndefConst(ERROR)
        return answer

    def _scalar_of(self, ty: Type | None) -> Type | None:
        """What an array is an array of, however many dimensions deep.

        What an operator is defined on is never an array, so this is what is
        wanted of an operand that may turn out to be one -- and of a literal
        standing beside it, which is why it is asked of the context and not only
        of the value.
        """
        while isinstance(ty, (ArrayType, VecType)):
            ty = ty.element
        return ty

    def _walk_operands(self, builder: IRBuilder, expr: ast.Expr,
                       given: Sequence[tuple[str, Value]],
                       expected: Type | None) -> Value | None:
        """Where an operand is an array, apply the operator element by element.

        Nothing where none of them is one, which is every operator on every
        ordinary value and is the path this must not slow down.

        What it does where one of them is: the operands that are arrays are
        walked in step and the ones that are not are used at every turn, exactly
        as a `listable` function's arguments are -- one rule, asked of an
        operator whose operands are never arrays instead of a parameter whose
        type says what it takes.  The operator is then lowered again for each
        element with the elements standing where the operands were written, so
        that every check it makes is made for each, by the code that makes it.
        """
        values = [value for _, value in given]
        if not any(isinstance(value.ty, ArrayType) for value in values):
            return None
        wanted = [self._scalar_of(value.ty) for value in values]
        if any(ty is None or ty is ERROR for ty in wanted):
            return UndefConst(ERROR)
        shape = self._shape_walked(values, wanted, expr.span)
        if shape is None:
            return UndefConst(ERROR)
        names = [name for name, _ in given]
        if self._all_at_once(expr, wanted, shape):
            return self._run_at_a_time(builder, expr, names, values, wanted,
                                       shape, expected)
        total = 1
        for along in shape:
            total *= along
        made: list[Value] = []
        for at in range(total):
            one = self._element_answer(builder, expr, names, values, wanted,
                                       _spread_out(at, shape))
            if self._value_type_of(one) is ERROR:
                return UndefConst(ERROR)
            made.append(one)
        element = self._value_type_of(made[0])
        answer = self._module.types.array_type(element, shape)
        place = builder.frame(answer, expr.span)
        for at, one in enumerate(made):
            builder.store(
                self._element_place(builder, place, element,
                                    builder.int_const(U64, at), expr.span),
                one, expr.span)
        held = builder.cast(CastKind.BITCAST, place, answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return held

    def _all_at_once(self, expr: ast.Expr, wanted: Sequence[Type],
                     shape: tuple[int, ...]) -> bool:
        """Whether the innermost run can be asked the operator all at once.

        Decided before anything is lowered, because it has to be: the answer
        settles which instructions are written, and a path taken halfway and
        abandoned would leave the ones already written behind.  Everything it
        rests on is known without lowering -- which operator was written, what
        the operands turned out to be of, and how long the run is.

        Three things have to hold.  The operator has to be one that asks the
        same question of each element and of nothing else.  Every operand has to
        have come to the same type, since two that did not are a mistake the
        element-by-element path reports and this one would build nonsense out
        of.  And the run has to be longer than one, a run of one being an
        element with extra words around it.
        """
        if shape[-1] < 2 or not all(ty is wanted[0] for ty in wanted):
            return False
        # A truth value and a whole number are what a lane holds.  A floating
        # point number is not yet: what says one went past the end of its type
        # is not a comparison but a question about the number itself, and asking
        # that of a lane apiece is its own piece of work.
        if not isinstance(wanted[0], (IntType, BoolType)):
            return False
        match expr:
            case ast.Binary():
                return expr.op in _ALL_AT_ONCE
            case ast.Unary():
                return expr.op in _ALL_AT_ONCE_UNARY
            case _:
                return False

    def _run_at_a_time(self, builder: IRBuilder, expr: ast.Expr,
                       names: Sequence[str], values: Sequence[Value],
                       wanted: Sequence[Type], shape: tuple[int, ...],
                       expected: Type | None) -> Value:
        """Apply the operator to the innermost run of elements all at once.

        The last dimension is the one whose elements are next to each other, so
        it is the one a machine can read as a single value and work on as one.
        Every dimension outside it is walked as it always was, and what happens
        at the bottom of that walk is one operation rather than as many as the
        run is long.

        The operator is lowered again with the runs standing where the operands
        were written, exactly as the element-by-element path lowers it with the
        elements -- so every check it makes is still made by the code that makes
        it, and asking a run rather than an element changes only how many values
        the answer covers.
        """
        lanes = shape[-1]
        outer = shape[:-1]
        total = 1
        for along in outer:
            total *= along
        made: list[Value] = []
        for at in range(total):
            taken: dict[str, object] = {}
            for name, value, want in zip(names, values, wanted):
                taken[name] = _Ready(
                    span=expr.span,
                    value=self._run_or_one(builder, value, want,
                                           _spread_out(at, outer), lanes,
                                           expr.span))
            made.append(self._lower_expr(builder, replace(expr, **taken), None))
        held = self._value_type_of(made[0])
        assert isinstance(held, VecType)
        answer = self._module.types.array_type(held.element, shape)
        place = builder.frame(answer, expr.span)
        for at, one in enumerate(made):
            builder.store(
                self._run_place(builder, place, held, at * lanes, expr.span),
                one, expr.span)
        found = builder.cast(CastKind.BITCAST, place, answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return found

    def _run_or_one(self, builder: IRBuilder, value: Value, want: Type,
                    index: tuple[int, ...], lanes: int, span: Span) -> Value:
        """One operand of a run-at-a-time operation, as a value of *lanes* lanes.

        An operand being walked comes down to a run of neighbours, which is read
        as one value: the elements of an array are laid out one after another
        with nothing between them, so the bytes a run occupies are the bytes a
        value of that many lanes occupies, and reading them as one is reading
        exactly the elements.

        An operand not being walked is one value used at every turn, so it goes
        in every lane.
        """
        picked = value
        for along in index:
            ty = self._value_type_of(picked)
            if ty is want or not isinstance(ty, ArrayType):
                break
            picked = self._one_of(builder, picked, want, along, span)
        held = self._module.types.vec_type(want, lanes)
        ty = self._value_type_of(picked)
        if ty is want or not isinstance(ty, ArrayType):
            return builder.splat(picked, held, span)
        start, _ = self._shape_of(builder, picked, ty, span)
        return builder.load(
            builder.cast(CastKind.BITCAST, start,
                         self._module.types.ptr_type(held, mutable=True), span),
            span)

    def _run_place(self, builder: IRBuilder, base: Value, held: VecType,
                   at: int, span: Span) -> Value:
        """Where the run that starts at the *at*-th element is, read as one value."""
        return builder.cast(
            CastKind.BITCAST,
            self._element_place(builder, base, held.element,
                                builder.int_const(U64, at), span),
            self._module.types.ptr_type(held, mutable=True), span)

    def _shape_walked(self, values: Sequence[Value], wanted: Sequence[Type],
                      span: Span) -> tuple[int, ...] | None:
        """How many along each dimension the operands are walked."""
        found: list[int] = []
        seen = [value.ty for value in values]
        while True:
            walked = [at for at, (ty, want) in enumerate(zip(seen, wanted))
                      if ty is not want]
            if not walked:
                return tuple(found)
            along: int | None = None
            for at in walked:
                ty = seen[at]
                if not (isinstance(ty, ArrayType) and ty.fixed):
                    self._diags.emit(D.LANG_LISTABLE_CANNOT_WALK, span,
                                     found=ty.render(), wanted=wanted[at].render())
                    return None
                if along is None:
                    along = ty.shape[0]
                elif along != ty.shape[0]:
                    self._diags.emit(D.LANG_LISTABLE_SHAPES_DIFFER, span,
                                     found=ty.shape[0], wanted=along)
                    return None
                seen[at] = self._one_less(ty)
            assert along is not None
            found.append(along)

    def _element_answer(self, builder: IRBuilder, expr: ast.Expr,
                        names: Sequence[str], values: Sequence[Value],
                        wanted: Sequence[Type], index: tuple[int, ...]) -> Value:
        """Lower the operator once, for the element the index names."""
        taken: dict[str, object] = {}
        for name, value, want in zip(names, values, wanted):
            picked = value
            for along in index:
                ty = self._value_type_of(picked)
                if ty is want or not isinstance(ty, ArrayType):
                    break
                picked = self._one_of(builder, picked, want, along, expr.span)
            taken[name] = _Ready(span=expr.span, value=picked)
        return self._lower_expr(builder, replace(expr, **taken), None)

    def _walked(self, builder: IRBuilder, func: Function,
                args: list[Value], expr: ast.Call,
                expected: Type | None) -> Value:
        """Call *func* once for each element of what was handed it as an array.

        An array handed where one of its elements is wanted is walked: the
        function is called for each, and what the call comes to is an array of
        the same shape holding the answers.  An argument that is not an array is
        handed to every one of those calls unchanged, which is what makes
        `scaled(v, 2u8)` mean what it looks like.

        Arguments walked together are walked in step, so they agree about how
        many there are along each dimension they are walked along -- and only
        along those: an array of two rows walked against a plain number says
        nothing about the number.

        The walk goes one dimension at a time and stops for each argument where
        what is left is what its parameter takes, so an array of more dimensions
        than the parameter wants is walked as many times as it takes.  What the
        answer is an array of is the dimensions that were walked, in order, and
        its elements are what the function answers with.
        """
        shape = self._walking_shape(func, args, expr)
        if shape is None:
            return UndefConst(ERROR)
        answer = self._module.types.array_type(func.ty.ret, shape)
        place = builder.frame(answer, expr.span)
        self._each_of(builder, func, args, shape, place, 0, expr.span)
        made = builder.cast(CastKind.BITCAST, place, answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _walking_shape(self, func: Function, args: Sequence[Value],
                       expr: ast.Call) -> tuple[int, ...] | None:
        """How many along each dimension the walk goes, outermost first.

        Worked out before anything is lowered for it, because the room the
        answer needs is the whole of that shape and is taken once.
        """
        found: list[int] = []
        seen = [value.ty for value in args]
        while True:
            walked = [at for at, (ty, wanted) in enumerate(zip(seen, func.ty.params))
                      if ty is not wanted]
            if not walked:
                return tuple(found)
            along: int | None = None
            for at in walked:
                ty = seen[at]
                if not (isinstance(ty, ArrayType) and ty.fixed):
                    self._diags.emit(D.LANG_LISTABLE_CANNOT_WALK,
                                     expr.args[at].span if at < len(expr.args)
                                     else expr.span,
                                     found=ty.render(),
                                     wanted=func.ty.params[at].render())
                    return None
                if along is None:
                    along = ty.shape[0]
                elif along != ty.shape[0]:
                    self._diags.emit(D.LANG_LISTABLE_SHAPES_DIFFER,
                                     expr.args[at].span if at < len(expr.args)
                                     else expr.span,
                                     found=ty.shape[0], wanted=along)
                    return None
                seen[at] = self._one_less(ty)
            assert along is not None
            found.append(along)

    def _one_less(self, ty: ArrayType) -> Type:
        """What is left of an array once its outermost dimension comes off."""
        if ty.rank == 1:
            return ty.element
        return self._module.types.array_type(ty.element, ty.shape[1:])

    def _each_of(self, builder: IRBuilder, func: Function,
                 args: Sequence[Value], shape: tuple[int, ...],
                 place: Value, at: int, span: Span) -> None:
        """Make the calls one dimension at a time, writing the answers in order.

        *at* is how many answers are already behind this one, which row-major
        makes the place this one goes: the same arithmetic an array written down
        uses, and for the same reason.
        """
        if not shape:
            answer = builder.call(func, tuple(args), func.ty.ret, span)
            builder.store(
                self._element_place(builder, place, func.ty.ret,
                                    builder.int_const(U64, at), span),
                answer, span)
            return
        step = 1
        for along in shape[1:]:
            step *= along
        for index in range(shape[0]):
            self._each_of(builder, func,
                          [self._one_of(builder, value, wanted, index, span)
                           for value, wanted in zip(args, func.ty.params)],
                          shape[1:], place, at + index * step, span)

    def _one_of(self, builder: IRBuilder, value: Value, wanted: Type,
                index: int, span: Span) -> Value:
        """The *index*-th of an argument being walked, or the argument itself.

        An argument that is already what its parameter takes is not walked and
        goes to every call as it stands.
        """
        ty = self._value_type_of(value)
        if ty is wanted or not isinstance(ty, ArrayType):
            return value
        start, lengths = self._shape_of(builder, value, ty, span)
        step = 1
        for along in ty.shape[1:]:
            assert along is not None
            step *= along
        offset = builder.int_const(U64, index * step)
        if ty.rank == 1:
            return builder.load(
                self._element_place(builder, start, ty.element, offset, span),
                span)
        return self._row_at(builder, start, ty, lengths, offset, 1, span)

    def _one_by_one(self, builder: IRBuilder, written: Sequence[ast.Expr],
                    wanted: Sequence[Type] | None,
                    callee: str | None = None) -> list[Value] | None:
        """Lower a list of written things left to right, spreads and all.

        The two such lists are a call's arguments and a tuple's members.  Each
        is lowered where it stands and in the order it was written, so that
        nothing is worked out before something written to its left -- which is
        what the specification says a call does, and what a reader of a call
        that both faults and calls has to be able to rely on.

        *wanted* is what each place is to hold, where something says: a
        parameter's type, or a member's.  A place beyond the end of it is one
        the list should not have had, and what is written there is lowered with
        nothing expected so that its own mistakes are still its own; the list
        being the wrong length is reported by the caller, which is the one that
        knows what length it should have been.

        A spread is taken apart where it stands, so its operand is lowered in
        its turn like everything else.  What it becomes are values already, and
        each is checked against the place it lands on rather than lowered into
        it -- there is nothing left to lower.
        """
        values: list[Value] = []

        def place() -> Type | None:
            """What the next value is to be, where anything says."""
            return wanted[len(values)] \
                if wanted is not None and len(values) < len(wanted) else None

        for one in written:
            outer = self._handing_over
            if callee is not None:
                self._handing_over = (callee, len(values) + 1)
            try:
                if isinstance(one, ast.Spread):
                    taken = self._taken_one_each(builder, one)
                    if taken is None:
                        return None
                    for value in taken:
                        ty = place()
                        if ty is not None and value.ty is not ty:
                            self._report_mismatch(one.operand.span, value.ty, ty)
                        values.append(value)
                    continue
                ty = place()
                values.append(self._lower_into(builder, one, ty, one.span)
                              if ty is not None
                              else self._lower_expr(builder, one, None))
            finally:
                self._handing_over = outer
        return values

    def _taken_one_each(self, builder: IRBuilder, spread: ast.Spread
                        ) -> list[Value] | None:
        """The values `\N{ASTERISM}x` stands for, one each, or nothing where it stands
        for no such list.

        Two types are several values the compiler can count: a tuple, whose
        members its type names one by one, and an array whose type says its
        length.  A tuple is taken apart where it is, since its members are
        already held separately; an array is read, since its elements are one
        run in memory, and the reads are the ones writing the indices out would
        have produced.

        An array of more than one dimension gives its outermost dimension, which
        is the same rule `foreach` follows over one: what an array of tables is
        several of, is tables.
        """
        value = self._lower_expr(builder, spread.operand, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if isinstance(ty, TupleType):
            return [builder.extract(value, at, member, spread.span)
                    for at, member in enumerate(ty.members)]
        if not isinstance(ty, ArrayType):
            self._diags.emit(D.LANG_SPREAD_NOT_SEVERAL, spread.operand.span,
                             found=ty.render())
            return None
        if not ty.fixed:
            # The length is beside the elements rather than in the type, so it
            # is a thing the program works out and this is a thing the compiler
            # writes down.
            self._diags.emit(D.LANG_SPREAD_NOT_FIXED, spread.operand.span,
                             found=ty.render())
            return None
        start, lengths = self._shape_of(builder, value, ty, spread.span)
        along = ty.shape[0]
        assert along is not None
        row = 1
        for rest in ty.shape[1:]:
            assert rest is not None
            row *= rest
        taken: list[Value] = []
        for at in range(along):
            offset = builder.int_const(U64, at * row)
            taken.append(
                builder.load(self._element_place(builder, start, ty.element,
                                                 offset, spread.span),
                             spread.span)
                if ty.rank == 1
                else self._row_at(builder, start, ty, lengths, offset, 1,
                                  spread.span))
        return taken

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
        if ref.name == WRAP_NAME:
            # It looks like a function and is not one: what it does is decide
            # what the operators written inside it mean, which is nothing a
            # value could stand for.
            self._diags.emit(D.LANG_WRAP_IS_NOT_A_VALUE, ref.span)
            return UndefConst(ERROR)
        resolved = self._lookup(ref)
        if resolved is None:
            return UndefConst(ERROR)
        if isinstance(resolved, GlobalVar) and resolved.value_type is ERROR:
            return UndefConst(ERROR)
        if isinstance(resolved, GlobalVar):
            held = resolved.value_type
            if isinstance(held, ArrayType) and held.fixed:
                # What a value of an array type *is*, is where the elements are:
                # how many there are is in the type, so there is nothing else to
                # carry and nothing to read out of memory.
                resolved = builder.cast(CastKind.BITCAST,
                                        builder.address(resolved, ref.span),
                                        held, ref.span)
            else:
                resolved = builder.load(resolved, ref.span)
        if not self._accepts(expected, resolved.ty):
            self._report_mismatch(ref.span, resolved.ty, expected)
        return resolved

    def _lower_into(self, builder: IRBuilder, expr: ast.Expr, expected: Type,
                    span: Span) -> Value:
        """Lower *expr* where a value of *expected* is wanted.

        It differs from lowering with an expectation in two cases.  Where a
        result is wanted, an expression that answers with the result's answer
        type is the *successful* result, and is wrapped as one.  That is the
        only way a program writes a successful result, there being no syntax for
        one -- which is Zig's arrangement for its error unions and C++'s for
        `std::expected`, and not Rust's, where `Ok(x)` is written out.  Rust can
        ask for it because its `Ok` is an ordinary constructor; here it would be
        a piece of syntax existing for one purpose.

        And where an array whose type does not say its shape is wanted, an array
        whose type does say is one: the elements are where they were and the
        shape is what the type it came from said.  That goes one way only, and
        only between two of one rank.  A function taking `T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` takes a vector of
        any length, and one taking `T\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` takes four, which nothing that has lost its
        count can promise.
        """
        if isinstance(expected, ArrayType) and not expected.fixed:
            return self._spread(builder, expr, expected, span)
        if not isinstance(expected, ResultType):
            return self._lower_expr(builder, expr, expected)
        # The whole type goes down, not nothing and not the answer alone:
        # something that can only be an answer asks `_aiming_at` for the
        # answer's type, and something that may be either is measured against
        # both by `_accepts`.  Handing nothing down was what kept a literal
        # with no suffix from standing here.
        value = self._lower_expr(builder, expr, expected)
        found = self._value_type_of(value)
        if found is expected or found is ERROR:
            return value
        if found is expected.ok:
            return builder.wrap(value, builder.bool_const(False), expected, span)
        self._report_mismatch(span, found, expected)
        return UndefConst(ERROR)

    def _spread(self, builder: IRBuilder, expr: ast.Expr, expected: ArrayType,
                span: Span) -> Value:
        """Lower *expr* where an array of no stated length is wanted.

        An array written down is the one thing that has to be told what is
        wanted of it: its elements take their type from where they stand, and
        where they stand is an array of the element type wanted here.  The
        length is the one part the writing settles for itself, which is why
        what comes back still has one and is let go of below.  Everything else
        is a value with a type of its own and is lowered with nothing expected.
        """
        value = self._lower_expr(builder, expr,
                                 expected if isinstance(expr, ast.ArrayLit)
                                 else None)
        found = self._value_type_of(value)
        if found is expected or found is ERROR:
            return value
        if isinstance(found, ArrayType) and _lets_go_of(found, expected):
            start, lengths = self._shape_of(builder, value, found, span)
            return builder.make_tuple((start, *lengths), expected, span)
        self._report_mismatch(span, found, expected)
        return UndefConst(ERROR)

    def _as_declared(self, value: Value, expected: Type) -> Value:
        """Keep a value whose type matches; stand in for one whose type does not.

        The mismatch has been reported.  Carrying the wrong value on would make
        every later use of the name report the same thing again.
        """
        if value.ty is expected or value.ty is ERROR:
            return value
        return UndefConst(ERROR)

    def _walks_down_to(self, found: Type, wanted: Type) -> int | None:
        """How many dimensions come off *found* before it is *wanted*, or nothing.

        Nothing where the two never meet, and nothing where a dimension that
        would have to come off is one the type does not state: what the answer
        is an array of is the shape that was walked, and a shape nobody stated
        is one there is no room to answer with.
        """
        depth = 0
        seen = found
        while seen is not wanted:
            if not (isinstance(seen, ArrayType) and seen.fixed):
                return None
            seen = seen.element if seen.rank == 1 \
                else self._module.types.array_type(seen.element, seen.shape[1:])
            depth += 1
        return depth

    def _aiming_at(self, expected: Type | None) -> Type | None:
        """What is wanted of something that cannot itself be a result.

        A result is an answer and the fact of whether there is one.  Something
        that is only ever an answer -- a literal, an operator's two sides, the
        members of anything written out -- is that answer, so where one stands
        and a result is wanted, what is wanted of *it* is the answer's type and
        the result is made around it afterwards.

        That is what carries a written type all the way down: `let q: u8? = 1`
        wants a `u8?`, the literal is asked for a `u8`, and the answer is made
        into one.
        """
        return expected.ok if isinstance(expected, ResultType) else expected

    def _accepts(self, expected: Type | None, found: Type) -> bool:
        """Whether a value of *found* stands where *expected* is wanted.

        Its own type does, and so does the answer type of a result: a value
        that is an answer stands where the answer and the fact of having one
        are wanted, and `_lower_into` is what makes the one into the other.
        """
        if expected is None or found is ERROR or expected is ERROR:
            return True
        if found is expected:
            return True
        if self._listing and isinstance(found, ArrayType):
            # An argument of a call being walked: an array stands where one of
            # its elements does, and what the call comes to is an array of the
            # same shape.  Whether this one can actually be walked down to what
            # the parameter takes is asked once, of all of them together, by
            # whatever works out the shape -- which is the only place that can
            # say what is wrong with a walk rather than with an argument.
            return True
        return isinstance(expected, ResultType) and found is expected.ok

    def _an_effect(self, which: int, span: Span, **args: object) -> None:
        """Report a change that outlives the call, where the function is pure.

        Everything that makes such a change comes through here, so that what
        "pure" means is one list rather than a rule each place remembers: a
        variable at the top level written, memory the function did not make
        written, and a function that may do either called.
        """
        if self._impure:
            return
        self._diags.emit(which, span, **args)

    def _made_here(self, place: Value) -> bool:
        """Whether a place is storage this call made and this call will lose.

        A frame is that, and so is anything worked out from one -- a bitcast of
        it, a row of it, an element of it.  Anything else came from somewhere
        that outlives the call: a variable at the top level, a parameter the
        caller handed over, something read out of memory.

        Asked of the value rather than of what was written, because the same
        question has the same answer however the place was arrived at.
        """
        seen = place
        while True:
            if isinstance(seen, FrameInst):
                return True
            if isinstance(seen, CastInst) or (
                    isinstance(seen, BinaryInst)
                    and seen.op in (BinOp.ADD, BinOp.SUB)):
                seen = seen.operands[0]
                continue
            if isinstance(seen, ExtractInst):
                seen = seen.operands[0]
                continue
            return False

    def _report_mismatch(self, span: Span, found: Type, expected: Type) -> None:
        """Report a type that does not match what the context requires.

        A type that could not be worked out matches anything: the mistake behind
        it has been reported once already, and saying so again at every place the
        value reaches would add nothing.
        """
        if found is ERROR or expected is ERROR:
            return
        if self._leaving:
            self._diags.emit(D.LANG_LOOP_VALUE_MISMATCH, span,
                             found=found.render(), expected=expected.render())
            return
        if self._operand_of is not None:
            # Three different mistakes: an operand where a truth value is what
            # the operator takes, one of a kind the operator has no meaning for,
            # and two operands of one kind whose widths differ.
            if expected is BOOL:
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_BOOLEAN, span,
                                 operator=self._operand_of, found=found.render())
            elif not isinstance(found, IntType):
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
