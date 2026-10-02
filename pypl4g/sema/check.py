"""Semantic analysis and lowering to the IR.

Definitions need not be processed in order: every top-level definition is
collected first and only then is any body checked, which is what lets the whole
compilation be parallelized and what makes a forward reference legal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields as fields_of, replace
from fractions import Fraction
from typing import Callable, Final, Sequence

from ..diag import ids as D
from ..target import statuses
from ..diag.engine import DiagEngine, Expectation
from ..front import ast
from ..front.expand import only_for_macros
from ..front.doccomment import Part as DocPart, parse as parse_doc
from ..front.lexer import is_operator_glyph, is_operator_name
from ..front.token import (ACQUIRE_NAME, ANSWER_NAME, ENTRY_NAME,
                           RELEASE_NAME,
                           AT_NAME, SPAN_NAME,
                           ADDRESS_NAME, BYTES_NAME,
                           IS_RECORD_NAME, FIELDS_NAME, TYPENAME_NAME,
                           STR_OF_NAME, EMPTY_NAME,
                           WIDEN_NAME,
                           BOTTOM_GLYPH, BUILTIN_GLYPH, CHR_NAME, DEREF_GLYPH,
                           LIFETIME_GLYPH, LIFT_OPEN_GLYPH,
                           ARRAY_OPEN_GLYPH, ARRAY_CLOSE_GLYPH,
                           SET_OPEN_GLYPH, SET_CLOSE_GLYPH,
                           DROP_NAME, LEAD_NAME, NARROW_NAME, ONES_NAME,
                           UNIT_NAME,
                          ENUMERATE_NAME, TYPEOF_NAME,
                          EMPTY_ARENA_NAME, ORD_NAME,
                          WRAP_NAME,
                          ENVIRON_NAME, ITER_NAME,
                          HEAP_NAME, STATIC_NAME, SYSCALL_NAME, SYSCALL_NUMBER_PREFIX,
                          TOLERANCE_DEFAULT,
                          TOLERANCE_NAME, WILDCARD_NAME)
from ..ir.builder import IRBuilder
from ..ir.reports import ReportKind
from ..ir.layout import (DataLayout, member_offsets_of, offsets_of,
                         size_of, stride_of, tag_offset_of)
from ..ir.inst import (AddressInst, BinaryInst, BinOp, CallInst, CastInst,
                       CastKind, CmpPred,
                       ExtractInst, FrameInst, Instruction, LoadInst, RetInst,
                       Ordering, Terminator, TupleInst, UnOp)
from ..target.registry import architecture_of, conventions_of
from ..target.syscalls import KNOWN as SYSCALL_NAMES, number_of
from ..runtime import names as runtime_names
from . import tables
from .clauses import Conditions
from ..ir.function import (DEFAULT_CCONV, SYSTEM_CCONV, BasicBlock, FuncAttrs,
                           FuncType, Function,
                           InlineHint,
                           Linkage, SpecialKind)
from ..ir.module import GlobalVar, Module
from ..ir.types import (ARENA, ARENA_NAME, StrType, ArrayType, BOOL, BoolType, BUILTIN_TYPES,
                        SYNTAX, SyntaxType, I64,
                        CursorType,
                        DictType,
                        ERROR, EnumType,
                        I64, U16, U32, U64,
                        F64,
                        FloatType, IntType, MEM, ProductType, PtrType,
                        ResultType,
                        ListType, SetType, STR, SumType, TupleType, Type,
                        NARROWING, NO_UNIT, Unit, VecType, VOID,
                        parts_within,
                        without_units,
                        CHAR, MAX_CODE_POINT, U8, made_of_parts, parts_of,
                        held_in_memory as _held_in_memory)
from . import environ, lists, owned, strings, tables
from .modules import (SUFFIX, ImportCycle, LoadedModule, ModuleNotFound,
                      ModuleRegistry, base_name, system_modules)
from ..ir.value import (AddressConst, ArrayConst, BlockParam, BoolConst,
                        CharConst, Const, EnumConst,
                        FloatConst,
                        IntConst, PartsConst, RecordConst,
                        UndefConst,
                        Value)
from pathlib import Path

from ..source.location import INVALID_SPAN, Span
from .notes import (FUNCTION, MODULE, PARAMETER, TYPE, VARIABLE, Note,
                    Notes)
from ..source.manager import SourceManager
from .attributes import (AttrSpec, AttrTarget, BoundAttr, SPECIAL_OF_TEST_KIND,
                         TARGET_NAMES, lookup)

#: The type every startup function must return.
#:
#: Six bits, which is the range that is the program's own: the exit status of a
#: process is eight bits wide, and of those the runtime reserves 64 through 127
#: for the stops it reports and a shell spends 128 through 255 on signals.  So
#: a program may exit with 0 through 63 and the type it answers with is the one
#: that holds exactly those -- which makes a status outside its own range a
#: thing the compiler refuses rather than a thing a reader has to know.
STARTUP_RETURN_TYPE_NAME = "u6"

#: The module the compiler knows about by name, and what it calls the record a
#: program is started with.  One module for now, found beside the compiler; a
#: type is that one by where it was written down and not by its shape, so a
#: record a program defines for itself and calls `Init` is not it.
STD_MODULE_NAME = "std"
STARTUP_ARGUMENT_TYPE_NAME = "Init"
#: And what it calls the record a build is described in.
BUILD_TYPE_NAME = "Build"


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
    #: Whether anything has written the place the name stands for, which only a
    #: name that stands for one can have.  Reading and writing are two ways of
    #: using such a name and the rules about them differ, so they are two
    #: flags: a value nothing reads is worth reporting, and a place nothing
    #: reads may still be the reason the place exists.
    written: bool = False
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
    #: Whether the name stands for a place rather than for a value.  A local is
    #: ordinarily a value and has no address; one a reference is taken of is
    #: given storage of its own, and `value` is then where that storage is.
    placed: bool = False
    #: What that storage holds, which is the type the name has.
    held: Type | None = None
    #: How deep the scope it was bound in is.  A name may be given a reference
    #: only to a place at least as deep as this: what a reference names has to
    #: be there for as long as the name that holds it.
    depth: int = 0
    #: The arenas what it holds was made in, by the identity of the names they are
    #: bound to.  A string, a list or a collection made `in` an arena points into
    #: it, so it is only worth having while the arena still holds what it made.
    arenas: frozenset[int] = frozenset()
    #: Where the arena it was made in was given back, once one has been: from there
    #: on what it holds points at nothing, and reading it is refused.
    gone_at: Span | None = None
    gone_with: str = ""
    #: For a container whose elements point somewhere, the allocator they are
    #: kept in: an arena's identity, or `⎕heap`'s.  Nothing for anything else.
    holds_in: int | None = None
    #: Whether this is the arena the body hands out with its answer, which it
    #: makes and does not give back.
    handed_out: bool = False


@dataclass(slots=True)
class _Borrow:
    """One reference that is out, and what it keeps anything else from doing.

    A `&mut` is the only reference to its place while it lives and a `&` may
    share with other `&`s, so what has to be known at the moment a second one
    is taken is which references are still alive and whether any of the two may
    write.
    """

    #: The name the place belongs to, which is what a message calls it.
    name: str
    #: Whether the place may be written through it.
    mutable: bool
    #: Where it was taken, for pointing at it beside the one that clashes.
    span: Span
    #: How deep the scope is that it lives in, or `_UNTIL_THE_STATEMENT_ENDS`
    #: while nothing has kept it: a reference handed straight to a call is gone
    #: when the call is, so `bump(&mut n); bump(&mut n)` is two turns and not
    #: two references at once.
    depth: int


#: A reference nothing has bound yet, which lives to the end of the statement.
_UNTIL_THE_STATEMENT_ENDS: Final[int] = -1


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
    #: The memory token the arm begins with, where that is not the one current
    #: where the plan was made.  An `elif` asks its condition in a block of its
    #: own, so a condition that writes leaves a token that only the arms below
    #: it may read; each arm therefore carries the token of the block that
    #: branches to it.
    memory: Value | None = None
    #: Whether what it binds is what the error carries rather than the answer.
    #: The two are read out of a result by different instructions, being of
    #: different types.
    carried: bool = False
    #: Whether what it binds is a part of a sum, read where the sum is.  The
    #: read is done in the arm's own block, since a part is read as the type
    #: that part has and no other arm's type is that one.
    payload: bool = False


@dataclass(slots=True)
class _NamedType:
    """A type a program defined, and what it turned out to be.

    What it is made of is worked out on first ask rather than where it is
    written, so that one definition may name another written below it.  The
    `resolving` flag is what catches a type that reaches itself: a value of such
    a type would have to hold a value of it, and a reference is the one
    indirection that makes that finite.

    `shell` is the type object, made before its parts are known so that a
    reference written in those parts has something to point at.  The parts are
    filled into it once they are all resolved, which is the only moment the
    object changes and is over before anything reads them.
    """

    name: str
    node: ast.TypeDef | ast.EnumDef
    origin: str
    exported: bool = False
    #: Whether the definition said `@[abi]`, which is what makes the record one
    #: shared with something compiled by something else.
    abi: bool = False
    #: Whether the definition said `@[unique]`: there is one of a value of this
    #: type and it is never copied.
    unique: bool = False
    #: Whether it said `@[device]`: a value of it is permission to do input or
    #: output.
    device: bool = False
    ty: Type | None = None
    resolving: bool = False
    shell: Type | None = None


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


def _doc_span(lines: Sequence[Span], part: DocPart) -> Span:
    """Where a command of a documentation comment is, in the file.

    One line of the comment's text is one line of the file, and the span kept
    beside it begins where the text does -- so a column in the one is a column in
    the other.  A part whose line is not there at all is placed at the comment's
    beginning rather than nowhere: that cannot happen while the two are built
    together, and answering with nothing would put the diagnostic at the top of
    the file.
    """
    if not lines:
        return INVALID_SPAN
    line = lines[part.line] if 0 <= part.line < len(lines) else lines[0]
    start = line.start + part.column
    return Span(start, min(start + len(part.word) + 1, max(line.end, start + 1)))


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


def _addressed_in(node: object, into: set[str]) -> None:
    """Add to *into* every name `&` is written in front of, anywhere below *node*.

    A local is ordinarily a value and has no address at all, so a name a
    reference is taken of has to be given storage of its own -- and that has to
    be settled before the body is lowered, because a name given storage in one
    arm of a branch and not in another would be two different things where the
    arms meet.

    The walk is over the fields of the tree itself rather than over a list of
    the kinds of node there are, so a kind added later is looked through without
    anything here being told about it.  It goes by name and takes no notice of
    scope, so a name shadowed somewhere may give storage to a binding that never
    needed it; what that costs is a load, and never an answer.
    """
    if isinstance(node, ast.AddressOf) and isinstance(node.operand, ast.NameRef):
        into.add(node.operand.name)
    if isinstance(node, ast.TakeAt) and isinstance(node.base, ast.NameRef):
        # Taking an element out of a list writes the shorter list back where
        # the list was, so the name has to *be* somewhere -- the same thing `&`
        # needs, got the same way.
        into.add(node.base.name)
    if isinstance(node, ast.Call) and isinstance(node.callee, ast.NameRef) \
            and node.callee.name == ITER_NAME and len(node.args) == 1 \
            and isinstance(node.args[0], ast.NameRef):
        # And a walk over one holds where the list is for as long as it lasts.
        into.add(node.args[0].name)
    if isinstance(node, ast.Capture) and node.by_reference:
        # A lambda that brings a variable in rather than what it held needs the
        # variable to be somewhere, which is the same thing `&` needs anywhere
        # else and is got the same way.
        into.add(node.name)
    if isinstance(node, ast.Lambda) \
            and node.brings_in is ast.CaptureAll.BY_REFERENCE:
        # `[&]` says it of every name the body reaches, and which ones those are
        # is not settled until the body is checked -- which is after this.  So
        # every name written in it is given storage: a name that turns out not
        # to be brought in has paid a load for it, which is the price of the
        # list saying "all of them" rather than saying which.
        written: list[str] = []
        _named_in(node.body, written)
        into.update(written)
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            _addressed_in(getattr(node, one.name), into)
    elif isinstance(node, (list, tuple)):
        for one in node:
            _addressed_in(one, into)


def _shell_for(defined: _NamedType) -> Type | None:
    """The type object a definition's parts will be filled into.

    It exists before the parts are known so that a reference written among them
    has something to point at.  Only a product and a sum have one: an
    enumeration's values are numbers and nothing can reach back into it.
    """
    if not isinstance(defined.node, ast.TypeDef):
        return None
    if defined.node.kind is ast.TypeKind.SUM:
        return SumType((), name=defined.name, origin=defined.origin)
    return ProductType((), name=defined.name, origin=defined.origin,
                       abi=defined.abi, unique=defined.unique,
                       device=defined.device)


def _filled_in(shell: Type | None, made: Type) -> Type:
    """Put the parts that were resolved into the object that was handed out.

    The object is the one a reference among those parts already points at, so
    the parts have to land in *it* and not in the one that was built beside it.
    It is frozen, as every type is, and this is the one moment it changes -- a
    moment that is over before anything reads the parts.
    """
    if isinstance(shell, ProductType) and isinstance(made, ProductType):
        object.__setattr__(shell, "fields", made.fields)
        return shell
    if isinstance(shell, SumType) and isinstance(made, SumType):
        object.__setattr__(shell, "variants", made.variants)
        return shell
    return made


#: The seven SI base units, which is what "a unit" means before a program says
#: otherwise.  Written as the words rather than the symbols -- `meter` and not
#: `m` -- because a program is read more often than it is written and because a
#: single letter is a name a program might want.
#: What a lambda's function is called, with a number after it.  The glyph is
#: the compiler's, so nothing a program can write collides with one.
#: How many arguments the kernel takes after the number.  Six on every
#: architecture this compiler generates for, and the same six on all of them.
SYSCALL_ARGUMENTS: Final[int] = 6

LAMBDA_PREFIX: Final[str] = "".join((BUILTIN_GLYPH, "lambda"))

#: What the code standing between an indirect call and a named function is
#: called.  It is no lambda, but it is the same kind of thing -- code the
#: program did not write and the log ties to what it was written for.
SHIM_PREFIX: Final[str] = "".join((BUILTIN_GLYPH, "through"))

#: What a lambda carries what it brought in through.  An address and nothing
#: more: what is there is the lambda's own business and no caller reads it.
_ENVIRONMENT: Final[PtrType] = PtrType(U8, mutable=True)


SI_UNITS: Final[tuple[str, ...]] = ("ampere", "candela", "gram", "kelvin",
                                    "meter", "mole", "second")

#: The two the compiler counts with.  `\N{CURRENCY SIGN}idx` is which one, and `\N{CURRENCY SIGN}size` is how
#: many; they are units and not types because the whole point is that adding a
#: length to a count of seconds is refused and so is indexing with either.
COUNTING_UNITS: Final[tuple[str, ...]] = ("idx", "size")

BUILTIN_UNITS: Final[dict[str, Unit]] = {
    name: Unit(((name, 1),)) for name in (*SI_UNITS, *COUNTING_UNITS)}

IDX_UNIT: Final[Unit] = BUILTIN_UNITS["idx"]
SIZE_UNIT: Final[Unit] = BUILTIN_UNITS["size"]

#: What `#` answers with: a count, in the unit of counting.
SIZE_TYPE: Final[IntType] = IntType(64, False, SIZE_UNIT)


def _unit_of(ty: Type) -> Unit:
    """What a value of *ty* counts, which is nothing for everything but a number."""
    return ty.unit if isinstance(ty, (IntType, FloatType)) else NO_UNIT


def _carrying(ty: Type, unit: Unit) -> Type:
    """*ty* with *unit* on it, where a unit is a thing it can carry."""
    if isinstance(ty, IntType):
        return IntType(ty.bits, ty.signed, unit)
    if isinstance(ty, FloatType):
        return FloatType(ty.bits, unit)
    return ty


#: The kinds of node that bind a name, and the fields the names are in.  What
#: `[=]` and `[&]` bring in is what the body reaches from *outside* itself, so
#: a name the body binds for itself is not one of them however often it is
#: written.
_BINDS: Final[dict[str, tuple[str, ...]]] = {
    "VarDef": ("name", "more"),
    "ForEach": ("name", "more"),
    "Param": ("name",),
    "Capture": ("name",),
    "Pattern": ("name",),
}


#: The fields of each kind of node a walk descends into, by the node's class:
#: asking `dataclasses.fields` of every node of every body is most of a walk.
_CHILDREN: dict[type, tuple[str, ...]] = {}


def _children(node: ast.Node) -> tuple[str, ...]:
    """The names of the fields of *node* a walk descends into."""
    found = _CHILDREN.get(type(node))
    if found is None:
        found = tuple(one.name for one in fields_of(node) if one.name != "span")
        _CHILDREN[type(node)] = found
    return found


def _lambdas_handed_on(node: object, calls: Callable[[ast.Call], bool],
                       taken: set[str], handed: list[ast.Lambda],
                       bound: list[tuple[str, ast.Lambda]]) -> None:
    """Walk *node* for what may take a lambda further than a call.

    Every name mentioned other than as what a call calls or as an argument of a call
    of a function -- which *calls* says of a call -- goes into *taken*: bound to
    another name, put in a field or a list, brought into a lambda, mentioned in a
    macro's invocation, which could do anything with it.  Every lambda written as such
    an argument goes into *handed*, and every one `let` binds to a name that is not
    `mut` into *bound*.  What a callee does with a function it is handed is held to
    its own rules, and those do not let it leave that call.
    """
    match node:
        case ast.NameRef() | ast.Capture():
            taken.add(node.name)
            return
        case ast.Invoke() | ast.Quote():
            seen: list[str] = []
            _named_in(node, seen)
            taken.update(seen)
            return
        case ast.AssignStmt():
            taken.add(node.name)
            taken.update(one for one, _ in node.more)
        case ast.VarDef() if isinstance(node.value, ast.Lambda) \
                and not node.mutable and not node.more and node.made_in is None:
            bound.append((node.name, node.value))
        case ast.Call():
            handing = calls(node)
            if not isinstance(node.callee, ast.NameRef):
                _lambdas_handed_on(node.callee, calls, taken, handed, bound)
            for one in node.args:
                value = one.value if isinstance(one, ast.Named) else one
                if handing and isinstance(value, ast.NameRef):
                    continue
                if handing and isinstance(value, ast.Lambda):
                    handed.append(value)
                _lambdas_handed_on(one, calls, taken, handed, bound)
            return
    if isinstance(node, ast.Node):
        for one in _children(node):
            _lambdas_handed_on(getattr(node, one), calls, taken, handed, bound)
    elif isinstance(node, (list, tuple)):
        for one in node:
            _lambdas_handed_on(one, calls, taken, handed, bound)


def _named_in(node: object, into: list[str]) -> None:
    """Every name written below *node*, in the order they are written.

    A name written as a name, and a name written in a capture list of a lambda
    inside this one -- which is a name that lambda reaches from *this* body, so
    a list that brings in everything this body reaches has to bring it in too.
    """
    if isinstance(node, ast.NameRef) and node.name not in into:
        into.append(node.name)
    if isinstance(node, ast.Capture) and node.name not in into:
        into.append(node.name)
    if isinstance(node, ast.AssignStmt):
        # A name written as the target of an assignment is a name the body
        # reaches, and is kept as a string rather than as a name of its own --
        # so it is not found by looking for names, and has to be looked for.
        for one in (node.name, *(name for name, _ in node.more)):
            if one not in into:
                into.append(one)
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            _named_in(getattr(node, one.name), into)
    elif isinstance(node, (list, tuple)):
        for one in node:
            _named_in(one, into)


def _bound_in(node: object, into: set[str]) -> None:
    """Every name bound below *node*, whichever construct binds it."""
    if isinstance(node, ast.Node):
        for field_name in _BINDS.get(type(node).__name__, ()):
            found = getattr(node, field_name, None)
            if isinstance(found, str):
                into.add(found)
            elif isinstance(found, tuple):
                into.update(one[0] for one in found
                            if isinstance(one, tuple) and isinstance(one[0], str))
        for one in fields_of(node):
            _bound_in(getattr(node, one.name), into)
    elif isinstance(node, (list, tuple)):
        for one in node:
            _bound_in(one, into)


#: What marks a name as standing for a type a call settles rather than for a
#: type.  A trailing mark rather than a leading one, so that the name reads as
#: a name and the mark as a note about it -- which is what the prime has meant
#: in mathematics for three hundred years and in ML and Haskell for fifty.
GENERIC_MARK: Final[str] = "'"


def _is_generic(name: str) -> bool:
    """Whether a name written as a type stands for one a call settles."""
    return name.endswith(GENERIC_MARK)


def _parameters_in(written: object, into: list[str]) -> None:
    """Every type parameter named below *written*, in the order they are written."""
    if isinstance(written, ast.TypeRef) and written.module is None \
            and _is_generic(written.name) and written.name not in into:
        into.append(written.name)
    if isinstance(written, ast.Node):
        for one in fields_of(written):
            _parameters_in(getattr(written, one.name), into)
    elif isinstance(written, (list, tuple)):
        for one in written:
            _parameters_in(one, into)


def _type_names_in(written: object, into: list[str]) -> None:
    """Every type parameter standing where a value would, in the order written.

    A type parameter in an expression is a name like any other as far as the
    grammar is concerned -- what tells it from a value's name is the apostrophe
    -- so this is the same walk as the one over types, over the other kind of
    node.
    """
    if isinstance(written, ast.NameRef) and _is_generic(written.name) \
            and written.name not in into:
        into.append(written.name)
    if isinstance(written, ast.Node):
        for one in fields_of(written):
            _type_names_in(getattr(written, one.name), into)
    elif isinstance(written, (list, tuple)):
        for one in written:
            _type_names_in(one, into)


@dataclass(slots=True)
class _Generic:
    """A function with type parameters: what was written, kept to be repeated.

    Nothing of it is compiled until a call says what the types are.  A call
    that says what an earlier one said gets that same function back -- one
    instantiation per set of types, not one per call.
    """

    node: ast.FuncDef
    path: str
    attrs: list[BoundAttr]
    #: The type parameters, in the order they are first written.
    parameters: tuple[str, ...]
    #: Whether a file importing this one may call it.  Nothing of a generic is
    #: compiled where it is written, so there is no function for the usual test to
    #: ask about and this is what says so instead.
    exported: bool = False
    #: The top-level names of the file that wrote it, and the bundles beside it
    #: there, for the same reason a bundle keeps them: the body means what it means
    #: where it was written, so an instance made for an importing file is checked
    #: with these in force and not with that file's names.  Held by reference, the
    #: tables still being filled when the definition is collected.
    scope: dict[str, object] | None = None
    siblings: dict[str, "_Bundle"] | None = None
    #: What has been made of it so far, by what the types turned out to be.
    made: dict[tuple[Type, ...], Function] = field(default_factory=dict)
    #: Whether it is being made just now, so that a function that calls itself
    #: with the types it already has does not do so for ever.
    making: set[tuple[Type, ...]] = field(default_factory=set)


@dataclass(slots=True)
class _Bundle:
    """A name for a set of requirements, kept to be substituted into.

    Nothing of it is checked where it is written: its lines are over its own type
    parameters, and what those are is what an application of it says.
    """

    node: ast.BundleDef
    path: str
    #: The top-level names of the file that wrote it, and the bundles beside it
    #: there.  A bundle's lines mean what they mean where it was written: a line
    #: naming `next` asks for the `next` its own file can see, not for whatever
    #: the file applying it happens to have, so an application is checked with
    #: these in force.  Held by reference, the tables still being filled when a
    #: bundle is collected.
    scope: dict[str, object] | None = None
    siblings: dict[str, "_Bundle"] | None = None
    #: Whether a file importing this one may apply it.
    exported: bool = False
    #: Whether it is being expanded just now, which is how a cycle among bundles
    #: is found rather than followed.
    opening: bool = False


#: What the purity rules report, which is the family the note about a condition's
#: purity belongs to and no other.  Everything that makes a change outliving the
#: call goes through one place, so this is that place's list and not a guess.
_PURITY: Final[frozenset[int]] = frozenset({
    D.LANG_PURE_CALLS_IMPURE, D.LANG_PURE_CHANGES_A_VARIABLE,
    D.LANG_IO_WITHOUT_A_DEVICE,
    D.LANG_PURE_READS_THE_ROUNDING_MODE, D.LANG_PURE_WRITES_ELSEWHERE})

#: The names of the functions a macro is given, and what each answers.  They are
#: the questions about a piece of the program and the two that build one; the machine
#: that runs a macro is what gives them a meaning, and nothing here does.
QUOTE_NAME: Final[str] = "".join((BUILTIN_GLYPH, "quote"))
FILL_NAME: Final[str] = "".join((BUILTIN_GLYPH, "fill"))
FILL_NUMBER_NAME: Final[str] = "".join((BUILTIN_GLYPH, "fillnumber"))
FILL_TEXT_NAME: Final[str] = "".join((BUILTIN_GLYPH, "filltext"))
REFUSE_NAME: Final[str] = "".join((BUILTIN_GLYPH, "refuse"))
HEAD_NAME: Final[str] = "".join((BUILTIN_GLYPH, "head"))
KIND_NAME: Final[str] = "".join((BUILTIN_GLYPH, "kind"))
PIECE_NAME: Final[str] = "".join((BUILTIN_GLYPH, "name"))
PARTS_NAME: Final[str] = "".join((BUILTIN_GLYPH, "parts"))
PART_NAME: Final[str] = "".join((BUILTIN_GLYPH, "part"))
APPLY_NAME: Final[str] = "".join((BUILTIN_GLYPH, "apply"))
ALIKE_PIECES_NAME: Final[str] = "".join((BUILTIN_GLYPH, "alike"))

#: What each question about a piece of the program takes.  `⎕part` takes which one
#: it wants rather than answering them all together, which is what keeps an array out
#: of the machine that runs a macro: how many there are is a separate question, and a
#: walk over them is a loop the macro writes.
_ASKS_ABOUT_A_PIECE: Final[dict[str, tuple[Type, ...]]] = {
    # Not about a piece, but asked the same way and only where a macro runs: what
    # a macro says about the program it was asked to write.
    REFUSE_NAME: (STR,),
    PIECE_NAME: (SYNTAX,),
    HEAD_NAME: (SYNTAX,),
    PARTS_NAME: (SYNTAX,),
    PART_NAME: (SYNTAX, U64),
}

_SYNTAX_ANSWERS: Final[dict[str, Type]] = {
    QUOTE_NAME: SYNTAX, FILL_NAME: SYNTAX, FILL_NUMBER_NAME: SYNTAX,
    FILL_TEXT_NAME: SYNTAX, REFUSE_NAME: SYNTAX,
    HEAD_NAME: SYNTAX, KIND_NAME: U64, PIECE_NAME: STR,
    PARTS_NAME: U64, PART_NAME: SYNTAX, APPLY_NAME: SYNTAX,
    ALIKE_PIECES_NAME: BOOL,
}


def _holes_of(node: object, into: list[ast.Hole]) -> None:
    """Every hole below *node*, in the order they are written.

    In the order written and not by name, because that is what the call that fills
    them counts by: a hole is a place and two holes of one name in a macro's body are
    two places, each filled with what the expression beside it answers.
    """
    if isinstance(node, ast.Hole):
        into.append(node)
        return
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            _holes_of(getattr(node, one.name), into)
    elif isinstance(node, (list, tuple)):
        for one in node:
            _holes_of(one, into)


#: The two pairs of brackets the language uses as an operator, named so that a
#: definition of one and a use of one agree on what it is called.  The lifting
#: marks are not among them: they are the grammar saying that a type follows.
ARRAY_PAIR: Final[str] = "".join((ARRAY_OPEN_GLYPH, ARRAY_CLOSE_GLYPH))
TABLE_PAIR: Final[str] = "".join((SET_OPEN_GLYPH, SET_CLOSE_GLYPH))

#: What a dummy made for a lifted type is called.  It begins with the lifting
#: mark, which a program cannot write in a name, so it collides with nothing.
DUMMY_PREFIX: Final[str] = LIFT_OPEN_GLYPH


def _written_name(node: ast.FuncDef) -> str:
    """The whole of a definition's name, which is a path where it has one.

    `Walk.next` rather than `next`, so that the name a reader wrote is the name a
    message gives back, the name the symbol is made from, and the name two
    definitions have to agree on to be a repeat of each other.  Two types may each
    have a `next` and neither reserves the word.
    """
    return node.name if node.held is None \
        else ".".join((node.held, node.name))


def _declared_as(node: ast.FuncDef) -> str:
    """The name a definition is written down under, for reporting a second one.

    A function's own name, and for an operator the glyph with the number of
    operands after it: the prefix reading and the infix one are two definitions of
    one glyph and neither is a repeat of the other.  It is what the *duplicate*
    rule keys on and nothing else reads it, a program never writing a glyph where
    a name goes except between the accents.
    """
    if not is_operator_name(node.name):
        return _written_name(node)
    return "".join((_written_name(node), "/", str(len(node.params))))


def _holds_a_piece(ty: Type, seen: frozenset[int] = frozenset()) -> bool:
    """Whether a value of *ty* holds a piece of the program anywhere inside it."""
    if isinstance(ty, SyntaxType):
        return True
    if id(ty) in seen:
        return False
    deeper = seen | {id(ty)}
    match ty:
        case TupleType():
            return any(_holds_a_piece(m, deeper) for m in ty.members)
        case ResultType():
            return (_holds_a_piece(ty.ok, deeper)
                    or (ty.err is not None and _holds_a_piece(ty.err, deeper)))
        case ProductType():
            return any(_holds_a_piece(one, deeper) for _, one in ty.fields)
        case ArrayType() | ListType() | SetType() | VecType():
            return _holds_a_piece(ty.element, deeper)
        case PtrType():
            return _holds_a_piece(ty.pointee, deeper)
        case _:
            return False


def _of_its_own(ty: Type | None) -> bool:
    """Whether a type is one a program defined rather than one the language has.

    A record, a choice between records, and an enumeration.  Not an array or a
    collection of them: what those are is the language's, and the operators it
    gives them are the language's too.

    **And a reference to one of those**, because an operator that changes what it
    is given has to take a reference and the language has no operator on a
    reference to a record either -- so `fn \N{GRAVE ACCENT}\N{UPWARDS WHITE ARROW}\N{GRAVE ACCENT}(t: &mut Walk) \N{RIGHTWARDS ARROW} \N{HORIZONTAL ELLIPSIS}` is a thing to
    write and was refused before this saw through the mark.
    """
    if isinstance(ty, PtrType):
        return _of_its_own(ty.pointee)
    return (isinstance(ty, (ProductType, SumType, EnumType))
            and bool(getattr(ty, "name", "")))


def _can_be_referred_to(ty: Type) -> bool:
    """Whether a reference may name a place holding a value of this type.

    One value in one place is what a reference names, so what it may name is
    what a place holds as one: a number, a truth value, a code point, a value of
    an enumeration, a record, a choice between records, and a reference itself.
    What it may not name is everything that is already several values or already
    a place -- an array, a list, a string, a set, a dictionary, a tuple, a
    result -- because a reference to one of those would be a second way of
    writing what a value of it already is.
    """
    return (isinstance(ty, (IntType, FloatType, EnumType, PtrType, ProductType,
                            SumType))
            or ty is BOOL or ty is CHAR)


def _holds_a_lambda(ty: Type, seen: frozenset[int] = frozenset()) -> bool:
    """Whether a value of *ty* holds a function anywhere inside it."""
    if isinstance(ty, FuncType):
        return True
    if id(ty) in seen:
        return False
    deeper = seen | {id(ty)}
    match ty:
        case TupleType():
            return any(_holds_a_lambda(m, deeper) for m in ty.members)
        case ResultType():
            return (_holds_a_lambda(ty.ok, deeper)
                    or (ty.err is not None and _holds_a_lambda(ty.err, deeper)))
        case ProductType():
            return any(_holds_a_lambda(t, deeper) for _, t in ty.fields)
        case ArrayType() | ListType() | SetType() | VecType():
            return _holds_a_lambda(ty.element, deeper)
        case _:
            return False


def _lambdas_last(ty: Type, seen: frozenset[int] = frozenset()) -> bool:
    """Whether every function a value of *ty* holds says what it brought in lasts
    (`fn(…) in ⎕heap → T`): such a value reaches nothing that is gone before the
    program is, whatever it was made from, its type having been checked where it
    was made."""
    if isinstance(ty, FuncType):
        return ty.lasting
    if id(ty) in seen:
        return True
    deeper = seen | {id(ty)}
    match ty:
        case TupleType():
            return all(_lambdas_last(m, deeper) for m in ty.members)
        case ResultType():
            return (_lambdas_last(ty.ok, deeper)
                    and (ty.err is None or _lambdas_last(ty.err, deeper)))
        case ProductType():
            return all(_lambdas_last(t, deeper) for _, t in ty.fields)
        case ArrayType() | ListType() | SetType() | VecType():
            return _lambdas_last(ty.element, deeper)
        case DictType():
            return _lambdas_last(ty.key, deeper) and _lambdas_last(ty.value, deeper)
    return True


def _lifetimes_of(node: object, found: set[str] | None = None) -> set[str]:
    """Every lifetime name written anywhere in a type, however deep.

    A reference may stand inside an array, a tuple or another reference, and a
    name carried anywhere in a parameter's type is carried by that parameter.
    Walked over the fields rather than by kind, so a type the language gains
    later is walked without this having to learn about it.
    """
    if found is None:
        found = set()
    if isinstance(node, ast.RefTypeRef) and node.lifetime is not None:
        found.add(node.lifetime)
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            value = getattr(node, one.name)
            if isinstance(value, ast.Node):
                _lifetimes_of(value, found)
            elif isinstance(value, tuple):
                for each in value:
                    _lifetimes_of(each, found)
    return found


def _promises_as_much(expected: Type | None, found: Type) -> bool:
    """Whether *found* promises at least what *expected* asks.

    Two promises, each of which may be given away without anything being lost:
    one that lasts as long as the program lasts long enough for a call, and one
    that may be written may be read.  Both go one way only -- a call's storage
    is no use where the program's is wanted, and a place nothing may write is
    no use where writing is -- and the bits are the same either way, so what
    carries a value across is a bitcast and the generated code is unchanged.

    A collection is asked the second of those questions for the reason a
    reference is asked it: it is a handle, and a table nothing may put anything
    in stands wherever one that may be written stands.
    """
    if found is expected:
        return False
    if isinstance(expected, SetType) and isinstance(found, SetType):
        return found.mutable and not expected.mutable \
            and found.element is expected.element
    if isinstance(expected, DictType) and isinstance(found, DictType):
        return found.mutable and not expected.mutable \
            and found.key is expected.key and found.value is expected.value
    if isinstance(expected, FuncType) and isinstance(found, FuncType):
        # A function that walks what it is given does everything one that does
        # not does, and the walk is the caller's business: a name that does not
        # ask for it simply does not do it.
        # And one whose environment lasts does everything one that may not does.
        return ((found.listable or not expected.listable)
                and (found.lasting or not expected.lasting)
                and found.hands_out == expected.hands_out
                and found.params == expected.params
                and found.ret is expected.ret)
    if not isinstance(expected, PtrType) or not isinstance(found, PtrType) \
            or found.pointee is not expected.pointee:
        return False
    return ((found.lasting or not expected.lasting)
            and (found.mutable or not expected.mutable))


def _names_a_place(expr: ast.Expr) -> bool:
    """Whether *expr* is the kind of thing that names a place at all.

    Asked before the place is worked out, so that something taking a list apart
    reports what it is about rather than letting the machinery a reference uses
    report about references.
    """
    return isinstance(expr, (ast.NameRef, ast.Element, ast.Member, ast.Deref))


def _is_a_table(ty: Type) -> bool:
    """Whether a value of *ty* is a collection, and so is built and not written."""
    return isinstance(ty, (SetType, DictType))


def _read_only_table(ty: Type) -> bool:
    """Whether *ty* is a collection nothing may put anything in."""
    return isinstance(ty, (SetType, DictType)) and not ty.mutable


def _reaches_a_place(ty: Type) -> bool:
    """Whether a value of *ty* may reach a place some name stands for.

    A reference does, and so does a lambda: what it brought in by reference is a
    place, reached through the environment it carries.  Both are asked the same
    question -- may this be given to a name that outlives what it reaches -- so
    both are asked it here.
    """
    return _holds_a_reference(ty) or _holds_a_lambda(ty)


def _and_then(names: Sequence[str]) -> str:
    """Several names, written the way a sentence writes them.

    In quotation marks and joined by a comma until the last, which takes `and`.
    A log is read by a person as well as matched on by a program, and the half
    that is prose reads as prose.
    """
    quoted = ["".join(("\'", one, "\'")) for one in names]
    if len(quoted) < 2:
        return "".join(quoted)
    return " and ".join((", ".join(quoted[:-1]), quoted[-1]))


def _written_in(node: object, into: set[str], parts: set[str]) -> None:
    """Every name a body assigns, and every name a part of which it writes."""
    match node:
        case ast.AssignStmt():
            into.add(node.name)
            into.update(name for name, _ in node.more)
        case ast.MemberAssign() | ast.ElementAssign() | ast.EntryAssign():
            root = node.base
            while isinstance(root, (ast.Member, ast.Element, ast.Index)):
                root = root.base
            if isinstance(root, ast.NameRef):
                parts.add(root.name)
        case ast.Lambda():
            return
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            _written_in(getattr(node, one.name), into, parts)
    elif isinstance(node, tuple):
        for one in node:
            _written_in(one, into, parts)


def _is_owning(ty: Type) -> bool:
    """Whether a container of *ty* copies what is put in it into its allocator."""
    return isinstance(ty, ArrayType) and owned.points(ty.element)


def _is_temporary(written: ast.Expr) -> bool:
    """Whether what an expression comes to is something nothing else names.

    A join, a call, a list written out: made by the expression and held by nothing
    but the value.  A name, a field, an element and a literal are held elsewhere --
    the last by the image, which nothing gives back.
    """
    while isinstance(written, ast.Allocated):
        written = written.value
    # A take answers an element still held by every other name for the list, and
    # what an `if` or a block comes to is whatever its arm yields.
    return not isinstance(written, (ast.NameRef, ast.Member, ast.Element,
                                    ast.Index, ast.StringLit, ast.Deref,
                                    ast.Take, ast.TakeAt, ast.TakeThrough,
                                    ast.If, ast.Match, ast.Scoped, ast.Named))


#: Where `→ T in ⎕heap` puts the heap among the places an answer may be made in,
#: which are otherwise parameters.
HEAP_AT: Final[int] = -1
#: And where `→ T in pool` puts an arena of the body's own, handed out with the
#: answer: the parameter nobody wrote, last, where the caller keeps it.
HANDS_AT: Final[int] = -2
#: And where `→ T in ⎕static` puts the image: an answer made while compiling,
#: never given back.
STATIC_AT: Final[int] = -3


def _hands_out_of(node: ast.FuncDef | ast.Lambda) -> str | None:
    """The arena of the body's own a definition hands out with its answer.

    `→ T in pool` where `pool` is neither a parameter nor `⎕heap`: then it can only
    be an arena the body makes, which is what alternative 1 of the proposal says.
    One name alone: an answer made in either of two arenas, one of them handed out,
    would leave the caller not knowing which it holds.
    """
    made_in = node.made_in
    if not made_in:
        return None
    if isinstance(made_in, tuple) and made_in and isinstance(made_in[0], str):
        made_in = (made_in,)
    if len(made_in) != 1:
        return None
    name = made_in[0][0]
    params = {one.name for one in node.params}
    if name in params or name in (HEAP_NAME, STATIC_NAME):
        return None
    return name


def _thin_able(ty: Type) -> bool:
    """Whether an answer of *ty* can travel without its allocator.

    A string or a list itself: what is left is where and how many, and the caller
    puts the allocator back.  Something holding one -- a record, a result -- keeps
    it, the parts it travels in being its own.
    """
    return isinstance(ty, (StrType, ListType))


def _callees_first(collected: Sequence[_Collected]) -> list[_Collected]:
    """The functions in the order the call tree puts them: each after what it calls.

    A walk of the bodies for the names they call -- by a bare name, as a member, or
    as an operator -- and a depth-first order from every function in the order it
    was written, so that a program with no calls between its functions keeps that
    order.  A cycle is entered where the walk first meets it; what is found there is
    a guess the body does not rely on, there being nothing written about it yet.
    """
    by_name: dict[str, list[_Collected]] = {}
    for one in collected:
        by_name.setdefault(one.node.name, []).append(one)
    order: list[_Collected] = []
    seen: set[int] = set()

    def called(node: object, into: list[str]) -> None:
        match node:
            case ast.Call():
                if isinstance(node.callee, ast.NameRef):
                    into.append(node.callee.name)
                elif isinstance(node.callee, ast.Member):
                    into.append(node.callee.name)
            case ast.Fresh():
                into.append(node.glyph)
            case ast.Lambda():
                pass
        if isinstance(node, ast.Node):
            for one in fields_of(node):
                called(getattr(node, one.name), into)
        elif isinstance(node, tuple):
            for one in node:
                called(one, into)

    def visit(entry: _Collected) -> None:
        if id(entry) in seen:
            return
        seen.add(id(entry))
        names: list[str] = []
        called(entry.node.body, names)
        for name in names:
            for callee in by_name.get(name, ()):
                visit(callee)
        order.append(entry)

    for entry in collected:
        visit(entry)
    return order


def _one_of(names: Sequence[str]) -> str:
    """Several names, written as the alternatives they are: `'a' or 'b'`."""
    quoted = ["".join(("\'", one, "\'")) for one in names]
    if len(quoted) < 2:
        return "".join(quoted) or "no arena"
    return " or ".join((", ".join(quoted[:-1]), quoted[-1]))


def _field_index(ty: ProductType, name: str) -> int | None:
    """Where *name* stands among the fields of *ty*, if it is one of them."""
    for at, (written, _) in enumerate(ty.fields):
        if written == name:
            return at
    return None


def _written_as(expr: ast.Expr) -> str:
    """What to call a place in a message, where the program wrote a name.

    The place written through may be worked out rather than named -- an element
    of an array, something a call answered with -- and there is nothing to call
    one of those but what it is.
    """
    match expr:
        case ast.NameRef():
            return expr.name
        case ast.Deref():
            return "".join((_written_as(expr.operand), DEREF_GLYPH))
        case _:
            return "what this writes through"


def _reached_from(value: Value, sources: Sequence[Value],
                  incoming: dict[int, list[object]] | None = None,
                  visited: set[int] | None = None) -> bool:
    """Whether *value* was worked out from one of *sources*.

    Walked back the way provenance is walked everywhere else, through the
    instructions that keep a reference pointing into the same place: reading
    the parameter out of its storage, offsetting it, and reading the same bits
    as another type.

    A block's parameter is what an `if` or a loop comes to, and the walk has to
    go through it or a body that answers differently in two arms could say
    nothing about where its answer came from.  *Every* branch that reaches the
    block has to reach a source, a promise being about what the function does
    and not about what one path through it does; *incoming* is what maps a
    block to the branches that reach it, and *visited* is what keeps a loop
    from being walked twice.
    """
    seen = value
    while True:
        if any(seen is one for one in sources):
            return True
        if isinstance(seen, LoadInst):
            seen = seen.operands[1]
            continue
        if isinstance(seen, AddressInst):
            seen = seen.operands[0]
            continue
        if isinstance(seen, BlockParam) and incoming is not None:
            visited = set() if visited is None else visited
            if id(seen) in visited:
                # Already being asked about further up: a value that reaches
                # itself round a loop is whatever it was on the way in, which
                # is the branch this walk is already looking at.
                return True
            visited.add(id(seen))
            arms = incoming.get(id(seen.block), [])
            return bool(arms) and all(
                seen.index < len(getattr(arm, "args", ()))
                and _reached_from(getattr(arm, "args")[seen.index], sources,
                                  incoming, visited)
                for arm in arms)
        if isinstance(seen, CallInst):
            # A call that promised its own parameters' lifetime names whatever
            # the arguments for them named, so the walk carries on there and
            # the two signatures agree with nothing written between them.  Any
            # one of them reaching the source is enough: what came back was one
            # of them, and every one of them is short enough.
            carried = getattr(seen.callee, "borrows_from", ())
            given = seen.arguments
            return any(at < len(given)
                       and _reached_from(given[at], sources, incoming, visited)
                       for at in carried)
        if isinstance(seen, (CastInst, ExtractInst)) or (
                isinstance(seen, BinaryInst)
                and seen.op in (BinOp.ADD, BinOp.SUB)):
            seen = seen.operands[0]
            continue
        return False


def _points_somewhere(ty: Type, seen: frozenset[int] = frozenset(),
                      lambdas: bool = True) -> bool:
    """Whether a value of *ty* may point into room something else holds.

    A string, a list, a collection, an array whose type does not say its length, a
    cursor, and a reference all are where their contents are rather than being them,
    so one made in an arena points into it.  So does a lambda, which carries where
    what it brought in is -- unless *lambdas* says to leave those out, which is what
    asking about what can be copied and given back does: an environment is neither.
    Anything holding one of those does too; a number, a truth value and a character
    are what they are, wherever they came from.
    """
    if id(ty) in seen:
        return False
    deeper = seen | {id(ty)}
    match ty:
        case StrType() | ListType() | SetType() | DictType() | PtrType() \
                | CursorType():
            return True
        case FuncType():
            return lambdas
        case ArrayType():
            return not ty.fixed or _points_somewhere(ty.element, deeper, lambdas)
        case TupleType():
            return any(_points_somewhere(m, deeper, lambdas) for m in ty.members)
        case ResultType():
            return _points_somewhere(ty.ok, deeper, lambdas) or (
                ty.err is not None and _points_somewhere(ty.err, deeper, lambdas))
        case ProductType():
            return any(_points_somewhere(one, deeper, lambdas)
                       for _, one in ty.fields)
        case SumType():
            return any(_points_somewhere(one, deeper, lambdas)
                       for _, one in ty.variants)
    return False


def _empties_named(node: object, into: set[str]) -> None:
    """Every arena name given back with `⎕empty` below *node*, `defer` or not."""
    if isinstance(node, ast.Call) and isinstance(node.callee, ast.NameRef) \
            and node.callee.name == EMPTY_NAME and node.args \
            and isinstance(node.args[0], ast.NameRef):
        into.add(node.args[0].name)
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            held = getattr(node, one.name)
            if isinstance(held, ast.Node):
                _empties_named(held, into)
            elif isinstance(held, tuple):
                for each in held:
                    if isinstance(each, ast.Node):
                        _empties_named(each, into)


#: The expressions that call something, which is what a reference handed over in
#: one of them lives no longer than, where the answer cannot hold it.
_CALLING: Final = (ast.Call, ast.Fresh, ast.Allocated, ast.Binary, ast.Unary)


def _holds_a_reference(ty: Type, seen: frozenset[int] = frozenset()) -> bool:
    """Whether a value of *ty* holds a reference anywhere inside it."""
    if isinstance(ty, PtrType):
        return True
    if id(ty) in seen:
        return False
    deeper = seen | {id(ty)}
    match ty:
        case TupleType():
            return any(_holds_a_reference(m, deeper) for m in ty.members)
        case ResultType():
            return (_holds_a_reference(ty.ok, deeper)
                    or (ty.err is not None and _holds_a_reference(ty.err, deeper)))
        case ProductType():
            return any(_holds_a_reference(t, deeper) for _, t in ty.fields)
        case SumType():
            return any(_holds_a_reference(t, deeper) for _, t in ty.variants)
        case ArrayType() | ListType() | SetType() | VecType():
            return _holds_a_reference(ty.element, deeper)
        case DictType():
            return (_holds_a_reference(ty.key, deeper)
                    or _holds_a_reference(ty.value, deeper))
        case _:
            return False


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


def _taken_from(written: ast.Expr, said: Type | None) -> Type | None:
    """What to lower one entry of a literal with, given what the others said.

    An entry that says what it is takes nothing from the rest: it says it, and
    where what it says disagrees with what they said, that is a disagreement
    between the entries -- which is one complaint about the whole literal and
    not several about its parts.  Everything else takes the one type they hold.
    """
    return None if _says_its_type(written) else said


def _can_be_a_key(ty: Type) -> bool:
    """Whether a value of *ty* may be a key of a set or a dictionary.

    A key is hashed and then compared, so a type that can be one is a type `=`
    is defined on and answers exactly: the integers, truth values and
    enumerations, a character, and text.  Floating point is left out on purpose
    -- two values that stand for one number may be two values, a not-a-number is
    equal to nothing including itself, and the two zeroes are equal and hash
    differently, so every one of the three properties a key wants fails.

    Text is a key because both questions have an answer over its bytes: two
    strings that say the same thing are one key wherever their bytes are, which
    is what `=` already answers and what the hash is taken over.  It costs a
    wider entry and a walk per lookup where a number costs a multiplication, and
    that is the price of the one key everything outside a program arrives as.
    """
    from ..ir.types import BoolType, CharType, StrType

    return isinstance(ty, (IntType, BoolType, EnumType, CharType, StrType))


def _can_be_a_value(ty: Type) -> bool:
    """Whether a value of *ty* may be what a dictionary's key stands for.

    Anything that is a run of words: an entry is words and the value is copied
    as what it is, so what it may be is settled by whether the compiler can load
    and store one of them.  What is left out is what has no size of its own --
    nothing, and a type that went wrong -- and a collection, which is a place in
    an arena and would make an entry hold something that outlives it by
    accident rather than by saying so.
    """
    return not isinstance(ty, (SetType, DictType)) and ty is not VOID \
        and ty is not ERROR


def _every_value_fits(found: IntType, into: IntType) -> bool:
    """Whether every value of *found* is a value of *into*.

    Which is the question a widening asks, and not "is it wider": an unsigned
    type goes into a signed one only where there is room to spare for the bit
    the sign takes, and a signed type goes into no unsigned one at all, a
    negative number being somewhere the unsigned type does not reach.
    """
    if found.signed and not into.signed:
        return False
    if not found.signed and into.signed:
        return found.bits < into.bits
    return found.bits <= into.bits


def _is_startup_argument(ty: Type) -> bool:
    """Whether *ty* is a reference to the record a program is started with.

    A reference and not the record: what the program is started with is an
    object the image carries, so what the entry point hands over is where it is.
    That is what lets it grow -- the arguments, the environment, whatever else
    arrives -- without the signature every program writes changing, and what
    lets the program write into it.

    Which record it is, is settled by where it was written down and not by its
    shape: one a program defines for itself and calls `Init` is a record it
    defined for itself, and the compiler hands the descriptors to nothing but
    the one type it knows.
    """
    # The record itself and not a reference to it.  A record is somewhere, and what
    # the entry point hands over is where -- but what the *program* has is the record
    # the process was started with, and saying so is what `@[unique]` then makes safe:
    # there is one of it and it is never copied, so the devices in it cannot be had
    # twice over.
    held = ty.pointee if isinstance(ty, PtrType) else ty
    if not isinstance(held, ProductType) \
            or held.name != STARTUP_ARGUMENT_TYPE_NAME or held.origin is None:
        return False
    return any(Path(held.origin) == (where / STD_MODULE_NAME).with_suffix(SUFFIX)
               for where in system_modules())


def _held_by_the_startup(func: Function, index: int,
                         written: ast.Param) -> Type | None:
    """What the startup function's parameter stands for, where this is it.

    The record itself, while the representation hands over where it is: a program
    writing `init: std.Init` has the record the process was started with, and that
    record is in the image rather than in a register, there being no caller to put it
    in one.  So the signature is a pointer and the name is the record -- which is what
    a record name means everywhere else, a record being somewhere and a field of it
    read at an offset from where.

    Safe because the type is `@[unique]`: the program may read the record and lend it
    out, and may not have a second one.
    """
    if func.attrs.special is not SpecialKind.STARTUP or index != 0:
        return None
    if isinstance(written.type, ast.RefTypeRef):
        # A reference was written, which the signature check refuses -- and until it
        # does, the name holds a reference the way any other would, so that one
        # message is given rather than a second about reading it.
        return None
    held = func.ty.params[0]
    if not isinstance(held, PtrType):
        return None
    return held.pointee if isinstance(held.pointee, ProductType) else None


def _reaches_a_device(ty: Type, seen: frozenset[int] = frozenset()) -> bool:
    """Whether a value of *ty* holds a device anywhere inside it.

    Anywhere, because `Init` holds an `Io` which holds three of them: a program
    handed the record the process started with has the devices in it, and saying so
    is what lets the startup function write without declaring anything.  A reference
    counts, a reference being how such a value is passed along at all.
    """
    if id(ty) in seen:
        return False
    deeper = seen | {id(ty)}
    match ty:
        case ProductType():
            return ty.device or any(_reaches_a_device(one, deeper)
                                    for _, one in ty.fields)
        case PtrType():
            return _reaches_a_device(ty.pointee, deeper)
        case TupleType():
            return any(_reaches_a_device(one, deeper) for one in ty.members)
        case ArrayType():
            return _reaches_a_device(ty.element, deeper)
    return False


def _glyph_of(expr: ast.Expr) -> str:
    """The glyph an operator application is written with."""
    match expr:
        case ast.Unary():
            return expr.op.value
        case ast.Binary():
            return expr.op.value
        case ast.Fresh():
            return expr.glyph
    return ""


def _operands_of(expr: ast.Expr) -> tuple[ast.Expr, ...]:
    """What an operator application is applied to."""
    match expr:
        case ast.Unary():
            return (expr.operand,)
        case ast.Binary():
            return (expr.left, expr.right)
        case ast.Fresh():
            return tuple(expr.operands)
    return ()


def _takes_room(expr: ast.Expr) -> bool:
    """Whether what the expression does takes room from an arena.

    A join of two, and a collection written out.  Everything else is worked out in
    registers, so saying which arena it comes from would be saying nothing -- a
    lambda says where what it brings in is kept after its capture list.
    """
    if isinstance(expr, (ast.SetLit, ast.DictLit, ast.ListLit, ast.ArrayLit)):
        return True
    return isinstance(expr, ast.Binary) and expr.op is ast.BinaryOp.CONCAT


def _about_the_shape(expr: ast.Expr) -> str:
    """What an expression is, for a message about where it cannot take room."""
    match expr:
        case ast.NameRef():
            return "".join(("'", expr.name, "'"))
        case ast.Binary():
            return "".join(("'", expr.op.value, "'"))
        case ast.Unary():
            return "".join(("'", expr.op.value, "'"))
    return "this"


def _leaves(node: object) -> bool:
    """Whether a `?` is anywhere inside *node*, which would hand a failure back."""
    if isinstance(node, ast.Try):
        return True
    if isinstance(node, ast.Node):
        for one in fields_of(node):
            held = getattr(node, one.name)
            if isinstance(held, ast.Node) and _leaves(held):
                return True
            if isinstance(held, tuple) and any(
                    isinstance(each, ast.Node) and _leaves(each) for each in held):
                return True
    return False


def _is_build_object(ty: Type) -> bool:
    """Whether *ty* is a mutable reference to the record a build is described in.

    Settled by where the record was written down, exactly as the startup
    function's parameter is: one a program defines for itself and calls `Build`
    is a record it defined for itself, and what the compiler hands a build
    function is the one type it knows.  Mutable, because writing into it is the
    whole of what a build function does.
    """
    if not isinstance(ty, PtrType) or not ty.mutable:
        return False
    held = ty.pointee
    if not isinstance(held, ProductType) \
            or held.name != BUILD_TYPE_NAME or held.origin is None:
        return False
    return any(Path(held.origin) == (where / STD_MODULE_NAME).with_suffix(SUFFIX)
               for where in system_modules())


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
#: The two operators that work a unit out from their operands' rather than
#: demanding that the two agree.  Everything else relates quantities of one
#: kind -- a sum of a length and a time is nothing -- and these two make a new
#: kind out of the two they were given.
_DERIVES: Final[frozenset[ast.BinaryOp]] = frozenset(
    (ast.BinaryOp.MULTIPLY, ast.BinaryOp.DIVIDE))


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
    ast.BinaryOp.ROTATE_LEFT, ast.BinaryOp.ROTATE_RIGHT,
    ast.BinaryOp.MAX, ast.BinaryOp.MIN))

#: The same, written before their operand.
_ALL_AT_ONCE_UNARY: Final[frozenset[ast.UnaryOp]] = frozenset((
    ast.UnaryOp.LOGIC_NOT, ast.UnaryOp.BIT_NOT))

#: What each operator means where it is written inside `⎕wrap`.  Every one of them
#: answers with the low bits of what it came to, whatever it came to -- so the
#: ones that would have stopped the program do not, and the ones that would have
#: stopped at the end of the type do not either.
#:
#: The saturating three are not here, and not because a wrapping form of them
#: would be hard to emit.  They are reported instead: a saturating operator says
#: the end of the type is the answer and a wrap says the low bits are, only one
#: of the two can be what the program meant, and which one is not something to
#: guess at.  Where the saturating step really was meant it is written outside
#: the wrap and its answer handed in.
#:
#: Dividing and taking a remainder are not here either.  Neither of them can go
#: past the end of a type by arithmetic -- what they have is a pair with no
#: answer at all, a divisor of zero -- so there is nothing about them for a wrap
#: to say, and nothing to contradict.
_WRAPPED: Final[dict[BinOp, BinOp]] = {
    BinOp.ADD: BinOp.WRAP_ADD, BinOp.SUB: BinOp.WRAP_SUB,
    BinOp.MUL: BinOp.WRAP_MUL,
    BinOp.SHL: BinOp.WRAP_SHL, BinOp.ASHR: BinOp.WRAP_ASHR,
    BinOp.LSHR: BinOp.WRAP_LSHR,
    BinOp.ROTL: BinOp.WRAP_ROTL, BinOp.ROTR: BinOp.WRAP_ROTR,
}

#: The three that say of themselves that they stop at the end of the type, which
#: is what a wrap says its operators do not do.
_SATURATES: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.SAT_ADD, ast.BinaryOp.SAT_SUB, ast.BinaryOp.SAT_MUL))

#: The larger and the smaller of two, with the signed reading first.  Which of
#: the two comparisons is asked is the type's business, exactly as it is for a
#: division and for a shift to the right.
_EXTREMA: Final[dict[ast.BinaryOp, tuple[BinOp, BinOp]]] = {
    ast.BinaryOp.MAX: (BinOp.SMAX, BinOp.UMAX),
    ast.BinaryOp.MIN: (BinOp.SMIN, BinOp.UMIN),
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

#: The four roundings, and the operation each is.  Three of them name a
#: direction and the fourth asks the processor which it is using.
#: The two that ask whether one number divides another, and the two that ask it
#: of two.  Which of each pair was written says whether the answer is turned
#: round, and nothing else about it differs.
_DIVIDES: Final[frozenset[ast.BinaryOp]] = frozenset((
    ast.BinaryOp.DIVIDES, ast.BinaryOp.NOT_DIVIDES))
_DIVIDES_UNARY: Final[frozenset[ast.UnaryOp]] = frozenset((
    ast.UnaryOp.DIVIDES, ast.UnaryOp.NOT_DIVIDES))

_ROUNDINGS: Final[dict[ast.UnaryOp, UnOp]] = {
    ast.UnaryOp.FLOOR: UnOp.FLOOR,
    ast.UnaryOp.CEILING: UnOp.CEIL,
    ast.UnaryOp.NEAREST: UnOp.NEAREST,
    ast.UnaryOp.ROUNDED: UnOp.ROUNDED,
}


#: What the tolerance is called in the image.  The name a program writes it by
#: is not a name an assembler or a debugger would take, so the two differ; the
#: one in the image says whose it is.
#: What memory looks like.  Every target this compiler has is an eight-byte
#: pointer, and the one thing the front end needs a layout for is how far apart
#: two elements of an array are.
_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8)

#: What the bytes of a value held in memory are copied in, largest first.  The
#: same pieces the pass that routes a large answer through storage copies in,
#: and for the same reason: a size known while compiling is written out.
_CHUNKS: Final[tuple[tuple[Type, int], ...]] = ((U64, 8), (U32, 4), (U16, 2),
                                                (U8, 1))

TOLERANCE_SYMBOL: Final[str] = "__pl4g_tolerance"

#: And the one the arena the compiler provides carries.
#: The conventions every target knows, which are the two the *language* names
#: rather than the ones an architecture does: its own, and whatever this system
#: calls things.  A program writing either asks no target anything.
EVERY_TARGET_KNOWS: Final[frozenset[str]] = frozenset((DEFAULT_CCONV,
                                                       SYSTEM_CCONV))

HEAP_SYMBOL: Final[str] = "__pl4g_heap"

#: What the generated constructor that builds a file's top-level tables is
#: called.  One per file that has any, numbered where two files do.
GLOBALS_SYMBOL: Final[str] = "__pl4g_globals"

#: What the bytes of a string written down are filed under, numbered from zero
#: as they are met.  A program cannot name one: what it wrote is the string, and
#: where the bytes went is the compiler's business.
TEXT_SYMBOL: Final[str] = "__pl4g_text."
#: What the variables are called that hold what a program put `in ⎕static`, and
#: the runs and tables those point at.
STATIC_SYMBOL: Final[str] = "__pl4g_static."


#: What `_static_type` answers where two parts of what is written disagree.
_MIXED: Final[object] = object()


def _merged(found: Sequence[object]) -> object:
    """The one type several parts say together: nothing where none says one, and
    `_MIXED` where two say different ones."""
    seen: object = None
    for one in found:
        if one is _MIXED:
            return _MIXED
        if one is None:
            continue
        if seen is None:
            seen = one
        elif seen is not one:
            return _MIXED
    return seen


def _shape_written(expr: ast.ArrayLit) -> tuple[int, ...] | None:
    """The shape an array written down has, nothing where its rows differ."""
    rows = [one for one in expr.elements if isinstance(one, ast.ArrayLit)]
    if not rows:
        return (len(expr.elements),)
    if len(rows) != len(expr.elements):
        return None
    inner = {_shape_written(one) for one in rows}
    if len(inner) != 1 or None in inner:
        return None
    (shape,) = inner
    assert shape is not None
    return (len(rows), *shape)


def _writes_arrays(node: object, params: Sequence[Type]) -> tuple[int, ...]:
    """Which parameters of a definition are arrays its body may write: `mut` ones."""
    return tuple(at for at, (param, ty) in enumerate(zip(node.params, params))
                 if param.mutable and isinstance(ty, ArrayType))


def _in_the_image(expr: ast.Expr) -> bool:
    """Whether *expr* is written `… in ⎕static`."""
    where = expr.arena if isinstance(expr, (ast.Allocated, ast.SetLit,
                                            ast.DictLit)) else None
    return isinstance(where, ast.NameRef) and where.name == STATIC_NAME


def _a_container_written(expr: ast.Expr) -> bool:
    """Whether *expr* is an array, a list, a set or a dictionary written down,
    whatever `in` says of it."""
    while isinstance(expr, ast.Allocated):
        expr = expr.value
    return isinstance(expr, (ast.ArrayLit, ast.ListLit, ast.SetLit, ast.DictLit))


def _known_while_compiling(expr: ast.Expr,
                           enum_named: Callable[[ast.Member], bool]) -> bool:
    """Whether everything *expr* says is known while compiling.

    A literal; a value of an enumeration, which *enum_named* says of `T.NAME`; a
    container written down of nothing else, in `⎕static` or in nothing; and a
    record written out of nothing else.  Read off what is written: it is asked
    to say a definition has to be `in ⎕static`, before anything is lowered.
    """
    match expr:
        case ast.IntLit() | ast.FloatLit() | ast.BoolLit() | ast.CharLit() \
                | ast.StringLit():
            return True
        case ast.Member():
            return enum_named(expr)
        case ast.Allocated():
            return _in_the_image(expr) \
                and _known_while_compiling(expr.value, enum_named)
        case ast.ArrayLit() | ast.ListLit():
            return all(_known_while_compiling(one, enum_named)
                       for one in expr.elements)
        case ast.SetLit():
            return (expr.arena is None or _in_the_image(expr)) and all(
                _known_while_compiling(one, enum_named) for one in expr.elements)
        case ast.DictLit():
            return (expr.arena is None or _in_the_image(expr)) and all(
                _known_while_compiling(key, enum_named)
                and _known_while_compiling(value, enum_named)
                for key, value in expr.entries)
        case ast.Named():
            return _known_while_compiling(expr.value, enum_named)
    return False


def _table_laid_out(keys: Sequence[tuple[object, Const, Const | None]],
                    hashed: Callable[[object, int], int]
                    ) -> tuple[int, list[tuple[Const, Const | None] | None], int]:
    """Where each entry of a table goes, worked out the way the program would.

    The table a program builds from the same entries in the same order, grown at
    the same points: so walking one put in the image meets its keys in the order
    walking one built while running would.  Answers the capacity, the entries by
    index -- nothing for an empty one -- and how many keys there are.
    """
    capacity = tables.FIRST_CAPACITY
    slots: list[tuple[object, Const, Const | None] | None] = [None] * capacity
    count = used = 0

    def slot_of(bits: object) -> int:
        at = hashed(bits, capacity - 1)
        while True:
            held = slots[at]
            if held is None or held[0] == bits:
                return at
            at = (at + 1) & (capacity - 1)

    for bits, key, value in keys:
        if (used + 1) * tables.LOAD_DENOMINATOR > capacity * tables.LOAD_NUMERATOR:
            room = capacity * 2 if (count + 1) * tables.LOAD_DENOMINATOR \
                > capacity * tables.LOAD_NUMERATOR else capacity
            old, slots = slots, [None] * room
            capacity, count, used = room, 0, 0
            for one in old:
                if one is not None:
                    slots[slot_of(one[0])] = one
                    count += 1
                    used += 1
        at = slot_of(bits)
        if slots[at] is None:
            count += 1
            used += 1
        slots[at] = (bits, key, value)
    return capacity, [None if one is None else (one[1], one[2])
                      for one in slots], count


def _word_hash(bits: object, mask: int) -> int:
    """Where a probe for a key of one word starts: Fibonacci hashing, folded."""
    assert isinstance(bits, int)
    mixed = (bits * tables.GOLDEN) & 0xFFFFFFFFFFFFFFFF
    return (mixed ^ (mixed >> 32)) & mask


def _text_hash(bits: object, mask: int) -> int:
    """Where a probe for a key of text starts: FNV-1a over its bytes."""
    assert isinstance(bits, bytes)
    from .strings import FNV_BASIS, FNV_PRIME

    found = FNV_BASIS
    for byte in bits:
        found = ((found ^ byte) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return found & mask


def _counted(op: UnOp, value: int, ty: IntType) -> int:
    """How many bits of *value* are set, or how many zeroes stand above the
    highest set one, counted over *ty*'s own width.

    Over the bits the value has *there*, which for a negative number is what
    two's complement gives it -- the same bits the machine would count, since
    the machine has nothing else to count.
    """
    bits = value & ((1 << ty.bits) - 1)
    if op is UnOp.COUNT_ONES:
        return bits.bit_count()
    return ty.bits - bits.bit_length()


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
    return tables.heap_global(module)


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
class _Deferred:
    """A statement put off until its block is left, and where it was written.

    `depth` is how many scopes were open where it was written, which is what its
    names are looked up in when it runs: a block inside may call something else by
    the same name, and what a deferred statement names is what was meant where it was
    written.
    """

    node: ast.Defer
    depth: int


#: What a value made in `⎕heap` is made in.  Not the identity of any name: `⎕heap`
#: is no name of the body's, is never given back, and is an allocator a container
#: may be made with all the same -- so what it made has to be told from what was
#: made in nothing, a literal or a number, which is the empty set.
_HEAP: Final[int] = -1


def _container_key(local: object) -> int:
    """What a container of `⎕heap`'s carries in its provenance, to be told by."""
    return -2 - 2 * id(local)


def _is_container_key(key: int) -> bool:
    """Whether *key* names a container rather than an allocator."""
    return key <= -2 and key % 2 == 0


def _element_key(key: int) -> int:
    """What a value read out of the container *key* names carries."""
    return key - 1


def _is_element_key(key: int) -> bool:
    """Whether *key* says a value was read out of a container."""
    return key <= -3 and key % 2 != 0


#: What a name killed by replacing an element carries where the arena's name would
#: be, so that what is said about reading it says what happened.
_REPLACED: Final[str] = "\0"


#: Every name's provenance at one point of a body: the name, the arenas what it holds
#: was made in, and where and by which arena it was killed, if it was.
_Provenance = dict[int, tuple["_Local", frozenset[int], "Span | None", str]]


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
    #: How many blocks' deferred statements were open where the loop began: a jump
    #: out of the body or back to its head leaves every block opened since, and
    #: runs what they put off.
    frame: int = 0
    #: What every name was made in, and whether it was dead, at each `break`:
    #: what holds after the loop is what holds along every way that leaves it.
    left_with: list[_Provenance] = field(default_factory=list)
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
                 top_level: list[_Global] | None = None,
                 notes: Notes | None = None,
                 conditions: Conditions = Conditions.CHECK,
                 for_macros: bool = False,
                 quoted: list[ast.Quote] | None = None) -> None:
        self._module = module
        self._diags = diags
        #: What a condition written in a signature does in this build, which is
        #: the command line's to say and nothing the program can change.
        self._conditions = conditions
        #: Whether what is being checked is the macros rather than the program.  A
        #: macro runs while the compiler does, so a piece of the program is a value
        #: it may hold and the questions about one are functions it may call --
        #: neither of which is true of the program, where a value has to outlive the
        #: compilation and there is nothing for a parse tree to be.
        self._for_macros = for_macros
        #: The trees quoted in the macros, in the order they were written, and the
        #: holes each has.  The expander reads them: a quote lowers to a call that
        #: names one by its place here and fills its holes with what the macro
        #: worked out.
        #: Every quote a macro's body wrote, in the order the calls that name them
        #: were written.  Shared with a checker reading a module, the way the
        #: top-level names are: a macro in a module quotes into the same table, since
        #: what runs them is one machine with one table of trees.
        self.quoted: list[ast.Quote] = [] if quoted is None else quoted
        #: Where to write down what each name turned out to be, for whoever
        #: wants to be asked about one later -- the language server, and nothing
        #: else.  Nothing where nobody asked, which is every build.
        self._notes = notes
        #: The build function, while its body is being checked.  What a
        #: compiler-provided function may be called from is that and nothing
        #: else, and this is how the check knows where it is.
        self._in_build_function: Function | None = None
        self._defined: dict[str, tuple[Span, str]] = {}
        #: The variables at the top level whose value is a table rather than
        #: bytes: what they are given is built by running code, so they are
        #: collected here and one constructor is generated for all of them.
        self._built_globals: list[tuple[GlobalVar, ast.VarDef]] = []
        #: What this file's top-level names stand for.  It is the file's own,
        #: not the compilation's: two files may each define a `counter`, and
        #: neither can see the other's unless it imports it.
        self._top: dict[str, object] = {}
        #: The bundles this file defines, by name.  They are beside the names
        #: above rather than among them because a bundle is neither a type nor a
        #: value: what it stands for is a set of requirements, and the only place
        #: one may be written is a clause.
        self._bundles: dict[str, _Bundle] = {}
        #: The function whose body is being lowered and what was written of it,
        #: so that a return knows which `post` clauses to check before it.
        self._demanding: tuple[ast.FuncDef | None, Function | None] = (None, None)
        #: Whose clauses have had their shapes reported on, so that a generic
        #: compiled for five sets of types is refused once and not five times.
        self._shaped: set[int] = set()
        #: What each parameter of the function being lowered arrived as, which is
        #: what `⎕entry` names.  Nothing is copied for it: a parameter's value at
        #: entry is a value the function already has, and naming it here keeps it
        #: alive to the return rather than making a second one.
        self._at_entry: dict[str, Value] = {}
        #: What a program's own operators are, by the glyph and how many operands
        #: it takes.  Two entries where one glyph is both prefix and infix, which
        #: `⌈` is: the arity is what tells the two definitions apart, so it is part
        #: of the key rather than something looked at afterwards.
        self._operators: dict[tuple[str, int], object] = {}
        #: Where each of them came from, for the one message that has to name two
        #: places: a module's name, or nothing for one written in this file.
        self._operators_from: dict[tuple[str, int], str] = {}
        #: What is named inside a type, by the type's name and the name after the
        #: dot.  Beside the top-level names rather than among them, since a name
        #: inside a type is reached only through the type: two types may each have
        #: a `next` and neither reserves the word.
        self._members: dict[tuple[str, str], object] = {}
        #: Whether a `post` clause is being lowered just now, which is the one
        #: place `⎕answer` and `⎕entry` mean anything.
        self._after: bool = False
        #: The definitions this file owns, which carry its module's name once
        #: that name is settled.
        self._owned: list[object] = []
        #: The shim made for each function named where a value was wanted, so
        #: that a name written twice is one shim and one symbol.
        self._shims: dict[int, Function] = {}
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
        #: The names the body being lowered takes a reference to, which are the
        #: names given storage of their own rather than standing for a value.
        #: Gathered once per body, before any of it is lowered.
        self._addressed: set[str] = set()
        #: How many references are open around the type being resolved.  A
        #: definition that reaches itself is refused, unless a reference stands
        #: somewhere on the way round.
        self._behind_a_reference: int = 0
        #: The units a program has introduced, innermost scope last.  A unit
        #: reaches as far as where it was written does, so the stack is pushed
        #: and popped with the names.
        self._unit_scopes: list[dict[str, Unit]] = [{}]
        #: What may stand where: each pair is one `unit \N{CURRENCY SIGN}FROM \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}TO`, and the
        #: relation is followed as far as it goes but never backwards.
        self._stands: list[tuple[Unit, Unit]] = []
        #: Whether the operands being lowered belong to a product or a quotient,
        #: which are the two operators that work a unit out rather than demand
        #: that both sides carry the same one.
        self._deriving: bool = False
        #: How many lambdas have been given a name, so that the next gets one
        #: nothing else has.
        self._lambdas: int = 0
        #: Whether what is being made into a constant was written `in ⎕static`,
        #: which is what a value that is not one is then told about.
        self._for_the_image: bool = False
        #: The scopes around the lambda being checked, which its body may not
        #: reach.  Kept so that a name it names and did not bring in is
        #: reported as one it did not bring in rather than as one nobody has.
        self._outside: list[dict[str, _Local]] = []
        #: What each type parameter is, while an instantiation of a generic
        #: function is being checked.  Empty everywhere else, a type parameter
        #: being a thing only a call gives a meaning to.
        self._bound: dict[str, Type] = {}
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
        #: The bytes of each distinct string written down, so that two literals
        #: saying the same thing are the one run of bytes in the image.
        #: Whether the function being lowered said it may change things that
        #: outlive the call.  Where it did not, the places that would make such
        #: a change report one instead.
        self._impure: bool = False
        #: Whether what is being checked may touch a device.  A function handed one
        #: may, which is what makes `@[impure]` mean "changes something global".
        self._may_do_io: bool = False
        #: Whether what is being checked has actually done it, which is what makes
        #: the function one a caller may not drop.
        self._did_io: bool = False
        #: Where what is being lowered takes its room from, where `in` said: a
        #: lexical thing, put in force around the expression it was written on.
        self._out_of_arena: Value | None = None
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
        #: What each block being lowered has put off until it is left, innermost
        #: last.  One list per block and not per scope, a block being what `defer`
        #: is about.
        self._deferred: list[list[_Deferred]] = []
        #: Which deferred statements have been lowered with their diagnostics
        #: told.  One is lowered at every way out of its block, and a mistake in it
        #: is one mistake however many ways out there are.
        self._defer_told: set[int] = set()
        #: The arenas given back in each block being lowered, by the identity of
        #: their names, innermost last -- kept beside the deferred statements and
        #: for the same reason: a block is what a way out leaves, and what was given
        #: back on this way is what an open block says was.
        self._emptied: list[set[int]] = []
        #: Every arena a name is bound to, by that name's identity, so that what a
        #: value records about where it was made can be asked about again.
        self._arena_by_id: dict[int, _Local] = {}
        #: Where a lambda reaches into this call -- the frame its environment is in,
        #: a variable it brought in by reference -- as something standing where an
        #: arena would, at the depth of the scope it goes with: a value reaching one
        #: is held to the rule a value made in an arena is.  By key, as arenas are.
        self._stand_ins: dict[int, _Local] = {}
        #: The stand-in for each variable a lambda brought in by reference, by the
        #: variable's identity, so that two lambdas reaching one variable say so.
        self._storage: dict[int, tuple[_Local, _Local]] = {}
        #: The lambdas that provably stay in the call that writes them, whose
        #: environment may therefore be in its frame, by identity.
        self._stays: dict[int, ast.Lambda] = {}
        #: The body being lowered, until its first lambda asks which stay: most
        #: bodies have none, and walking them for nothing is not free.
        self._unscanned: ast.Block | None = None
        #: Parameters whose provenance the body cannot know, by the marker they carry
        #: as their arenas: a function saying where its answer was made may not
        #: answer one of these, there being no saying where it was made.
        self._unknown_by_id: dict[int, _Local] = {}
        #: What the block lowered last gave back on its way off the end, which is
        #: how an `if` learns what every arm reaching the join gave back.
        self._last_emptied: set[int] = set()
        #: What the elements of the array being filled were taken from as they were.
        self._element_made: set[int] = set()
        #: The allocator a call's answer keeps its elements in, by the call.
        self._holds_from: dict[int, int] = {}
        #: Calls whose answer is certainly an object of the heap's, and not text in
        #: the image the heap would be told to give back.
        self._fresh_heap: set[int] = set()
        #: Which function each call lowered named, for an answer that is a call of
        #: the function to itself.
        self._callee_of: dict[int, Function] = {}
        #: What each answer of the body being checked turned out to be, and the
        #: allocator the signature fixed for all of them, where it fixed one.
        self._answer_kinds: list[str] = []
        self._thin_to: int | None = None
        #: The function an `if` or a `match` being lowered is the answer of, so
        #: that its arms answer for themselves; and the same handed to the last
        #: statement of an arm, for an `if` that ends one.
        self._answer_arms: Function | None = None
        self._yield_answer: Function | None = None
        #: The arena of its own the body being checked hands out with its answer:
        #: its name, where the caller keeps it, and how deep the body's own scope is
        #: -- and the local, once `let` has made it.
        self._hands_out: tuple[str, Value, int] | None = None
        self._hands_out_local: _Local | None = None
        #: The arena a definition `let v: T in kept = f(…)` receives into, until a
        #: call hands it over; and the one the last such call was handed.
        self._hands_out_told: set[int] = set()
        self._receiving: tuple[_Local, ast.Expr] | None = None
        self._received: _Local | None = None
        #: The definitions whose `in` has been checked, so a generic one says what
        #: is wrong with it once and not once per instantiation.
        self._made_in_told: set[int] = set()
        #: Where an expression's answer was made, where lowering it said exactly: a
        #: join of two strings is made in its arena and nowhere else, and a call to
        #: a function saying `→ T in a` is made in what `a` was given.  The node is
        #: kept beside what it says, so an id is never taken for another node's.
        self._made_from: dict[int, tuple[ast.Expr, frozenset[int]]] = {}
        #: What the call lowered last said of its answer, for the operator form of
        #: `in`, whose call is one the checker wrote and no node holds.
        self._last_made_in: frozenset[int] | None = None
        #: An `if` some of whose arms were settled while compiling, and the `if` of
        #: the rest that was lowered in its place.
        self._made_alias: dict[int, tuple[ast.Expr, ast.Expr]] = {}
        #: The arenas `in` named around what is being lowered: `⎕heap` where none.
        self._out_of_arenas: frozenset[int] = frozenset({_HEAP})
        #: What the body being checked said its answer was made in: the arena
        #: parameters' markers and how the signature named them.
        self._answer_made_in: tuple[frozenset[int], str] | None = None
        #: The arenas already reported as never given back: there is one arena and
        #: one mistake, however many ways out there are.
        self._told_kept: set[int] = set()
        #: The name a reference is being taken of, while one is: working the
        #: place out reads the name, and that read is the reference itself.
        self._taking_a_reference: str | None = None
        #: Which name bounds how long each value lasts, by the identity of the
        #: value: a place by the name it belongs to, and a lambda by the
        #: shortest-lived name it brought in by reference.  A reference is
        #: walked back to one of these to find out how long what it reaches
        #: lasts and what to call it.
        self._places: dict[int, _Local] = {}
        #: The references that are out, oldest first.  One is dropped when the
        #: statement that made it ends, or when the scope that kept it does.
        self._borrows: list[_Borrow] = []
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
        if not self._for_macros:
            # What was written for the macros is not part of the program.  The
            # expander takes those out of the files it was handed; this is what
            # takes them out of everything else -- a module read from here, and a
            # unit handed straight to the checker by something that is not the
            # driver.  Nothing at run time may hold a piece of the program, so a
            # unit that still has them is one that will not compile.
            units = [replace(unit, items=tuple(
                one for one in unit.items if not only_for_macros(one)))
                for unit in units]
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
        # Units first of all: a type written anywhere may carry one, and what a
        # unit is has to be known before any type is worked out.
        for unit in units:
            for item in unit.items:
                if isinstance(item, ast.UnitDef):
                    self._define_unit(item)
        for unit in units:
            for item in unit.items:
                if isinstance(item, (ast.TypeDef, ast.EnumDef)):
                    self._collect_type(item, unit.path)
        # Bundles before the functions, since a clause of one may apply a bundle
        # written below it, exactly as a type may be named before it is defined.
        for unit in units:
            for item in unit.items:
                if isinstance(item, ast.BundleDef):
                    self._collect_bundle(item, unit.path)
        self._check_bundle_cycles()
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
                    case (ast.ModuleImport() | ast.TypeDef() | ast.EnumDef()
                          | ast.UnitDef() | ast.BundleDef()):
                        pass
                    case ast.MacroDef():
                        # A macro written as rules is no function and has nothing to
                        # lower.  One is here only where the macros are being checked
                        # -- the program's units have had them taken out -- and what
                        # reads it is the expander, which has it already.
                        pass
                    case _:
                        self._diags.internal("unknown kind of top-level definition")
        # Callees before their callers, as far as the call tree goes: what a
        # body shows of where its answer is made is written on the function, and
        # a caller checked after it relies on it.
        for entry in _callees_first(collected):
            self._lower_function(entry)
        self._build_the_tables()
        if whole_program:
            self._check_program()
        return self._module

    def _build_the_tables(self) -> None:
        """Generate the constructor that gives the top-level tables their own.

        A collection is a table in an arena, made by running code; a variable at
        the top level is bytes in the image.  What bridges the two is a
        constructor, which the module already has a place for -- so what a
        program writes as `let d: mut ⸨str: u8⸩ = ⸨"a": 1u8⸩` is a word of
        nought in the image and a few instructions before anything runs.

        **One function for every such variable of a file**, in the order they
        are written, so that one written in terms of another reads what that one
        was given.  And *before* the program's own constructors, which is what
        lets one of those read a table: they are what the program wrote, and
        this is what its variables *are*.

        The body is lowered the way any other is, so everything a definition
        inside a function may say it may say here: a literal, an arena named
        with `in`, a call, an operator over two of them.
        """
        if not self._built_globals:
            return
        made = self._module.add_function(Function(
            self._unused_name(GLOBALS_SYMBOL),
            self._module.types.func_type((), VOID),
            FuncAttrs(special=SpecialKind.CONSTRUCTOR, priority=0, impure=True),
            linkage=Linkage.INTERNAL))
        made.add_block()
        builder = IRBuilder(self._module, made)
        self._borrows = []
        self._places = {}
        outer_answer, self._answering = self._answering, VOID
        outer_impure, self._impure = self._impure, True
        self._push_scope()
        for var, node in self._built_globals:
            value = self._lower_into(builder, node.value, var.value_type,
                                     node.span)
            if self._value_type_of(value) is not ERROR:
                builder.store(var, value, node.span)
        self._pop_scope()
        self._answering = outer_answer
        self._impure = outer_impure
        builder.ret()
        # In front of the program's own, and in front of another file's: what a
        # constructor may read is every table, whichever file it was written in.
        self._module.ctors.insert(0, made)

    def _unused_name(self, wanted: str) -> str:
        """*wanted*, or *wanted* and a number where a file already took it."""
        if wanted not in self._module.functions:
            return wanted
        at = 1
        while ".".join((wanted, str(at))) in self._module.functions:
            at += 1
        return ".".join((wanted, str(at)))

    # -- variables -------------------------------------------------------------

    def _provided(self, name: str) -> object | None:
        """What a name the compiler provides stands for, made on first ask.

        On first ask rather than always, so that a program that never names one
        carries nothing for it and its report log says nothing about dropping
        it.  There is one such name so far.
        """
        found = self._top.get(name)
        if found is None and name == TOLERANCE_NAME:
            found = _tolerance(self._module)
            self._top[name] = found
        if found is None and name == HEAP_NAME:
            found = _heap(self._module)
            self._top[name] = found
        if found is None and name == ENVIRON_NAME:
            found = self._environ()
            self._top[name] = found
        return found

    def _environ(self) -> GlobalVar:
        """The variable the environment waits in, and what fills it.

        Made on first ask, as the heap is and for the reason the heap is: a
        program that never names it has no table, no builder, and nothing asked
        of the system.  Asking for it is what tells the entry point to build one,
        so there is nothing to look for in what the program wrote.
        """
        heap = self._provided(HEAP_NAME)
        assert isinstance(heap, GlobalVar)
        made = environ.variable(self._module)
        environ.make_function(self._module, heap)
        self._module.environ = made
        return made

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
        self._check_doc(node)
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
            if ty is not None and _holds_a_lambda(ty):
                self._diags.emit(D.LANG_LAMBDA_AT_TOP_LEVEL, node.span)
                ty = ERROR
            elif ty is not None and _holds_a_reference(ty) \
                    and not (isinstance(ty, PtrType) and ty.lasting):
                # A variable here lasts as long as the program, so what it names
                # has to as well -- which is what `static` says and what nothing
                # else promises.  Asked before the value is looked at, since
                # there is no value a variable of the other sort could be given.
                self._diags.emit(D.LANG_REF_AT_TOP_LEVEL, node.span)
                ty = ERROR
            # A collection is a table, which is built by running code unless it
            # is `in ⎕static`: what the variable holds until the constructor
            # below has run is nought.
            static = _in_the_image(node.value)
            if ty is not None and not self._image_definition(node, ty):
                ty = ERROR
            outer, self._for_the_image = self._for_the_image, static
            try:
                initializer = (None if ty is None
                               or (_is_a_table(ty) and not static)
                               else self._constant_value(node, ty))
            finally:
                self._for_the_image = outer
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
                             found=ty.written())
            ty = ERROR
        if ty is None:
            ty = ERROR
        var = self._module.add_global(GlobalVar(
            name=node.name, value_type=ty,
            # A table's handle is written by the constructor whatever the
            # program may do with it, so the place it waits in is one that may
            # be written.  What the *program* may do is the type's business, and
            # a collection without `mut` is refused an assignment there.
            ptr_type=self._module.types.ptr_type(
                ty, mutable=node.mutable or (_is_a_table(ty) and not static)),
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
        if _is_a_table(ty) and not static:
            self._built_globals.append((var, node))

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
        self._import_operators(found, node)

    def _import_operators(self, found: LoadedModule,
                          node: ast.ModuleImport) -> None:
        """Put a module's exported operators in force in this file.

        An operator has no name to qualify it by, so an exported one is in force
        wherever its module is imported -- which is what lets a library say what a
        glyph means for the types it defines, and there is nothing else it could
        mean.  Two modules exporting one glyph for one number of operands leave
        nothing to decide between them, so the second import is refused: it points
        at the import rather than at a use, which is where the choice was made.

        A definition in this file wins, and silently.  What applies where it is
        written is what a reader has in front of them, and it is the way out of
        the refusal above.
        """
        for key, what in found.operators.items():
            if key in self._operators:
                self._diags.emit(D.LANG_OPERATOR_TWICE_IMPORTED, node.source_span,
                                 glyph=key[0], count=str(key[1]),
                                 module=node.source,
                                 other=self._operators_from.get(key) or "this file")
                continue
            self._operators[key] = what
            self._operators_from[key] = node.source

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
                            self._sources, self._top_level,
                            conditions=self._conditions,
                            for_macros=self._for_macros,
                            quoted=self.quoted)
            inner.run([unit], whole_program=False)
            loaded.exports = {name: what for name, what in inner._top.items()
                              if _is_exported(what)}
            # Every one of them and not only the exported ones: which it is, is
            # a question the file applying it is answered rather than a name it
            # cannot see at all, so that "there is no such bundle" and "it is not
            # yours to apply" are two different things to be told.
            loaded.bundles = dict(inner._bundles)
            # Only the exported ones, unlike the bundles above: a bundle has a
            # name and so can be told "it is not yours to apply", and an operator
            # has none to be told anything about.
            loaded.operators = {key: what
                                for key, what in inner._operators.items()
                                if _is_exported(what)}
            # A name inside a type is reached through the type, so only the
            # exported ones: one not exported is not a name this file can write
            # at all, the type being somebody else's.
            loaded.members = {key: what
                              for key, what in inner._members.items()
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
        tokens = tokenize(source, self._diags)
        found = parse(tokens, path.as_posix(), self._diags)
        # A module read here is a source of the program like any other, and the
        # bill of materials has to name it.
        self._sources.record(path, tokens, found)
        return found

    def _lower_member(self, builder: IRBuilder, expr: ast.Member,
                      expected: Type | None) -> Value:
        """Lower something named through what it belongs to.

        A module, or an enumeration: `Colour.red` is the value of `Colour`
        called `red`.  The two are one syntax with two meanings decided by what
        the base names, which is what Go, Rust and Zig all do -- and an
        enumeration's values are written with the type in front of them so that
        two enumerations may each have a `red`.

        Both at once where the enumeration came from a module: `std.Error.again`
        is the value of a type reached through the name the module was bound to,
        which is the two readings one after the other and not a third one.
        """
        base = expr.base
        if isinstance(base, ast.Member):
            # A type another module exports, and a value of it.  Nothing else a
            # module holds is reached through two marks, so what stands here is
            # an enumeration or it is a mistake.
            through = self._type_exported(base)
            if isinstance(through, EnumType):
                return self._value_of_enum(through, expr)
        if not isinstance(base, ast.NameRef) \
                or self._holds_a_record(base.name):
            # A field of a record: what stands on the left is a value and not
            # something names are reached through, so what follows the mark is
            # one of the fields its type wrote down.
            return self._field_of(builder, expr, expected)
        provided = BUILTIN_TYPES.get(base.name)
        if isinstance(provided, EnumType):
            # An enumeration the compiler provides.  Its values are written the
            # way every other enumeration's are, which is the point of making it
            # one: what `\N{APL FUNCTIONAL SYMBOL QUAD}narrow` failed for is matched and compared like anything
            # else a program defined for itself.
            return self._value_of_enum(provided, expr)
        held = self._top.get(base.name)
        if isinstance(held, _NamedType):
            return self._enum_value(held, expr)
        if not isinstance(held, LoadedModule):
            self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, base.span, name=base.name)
            return UndefConst(ERROR)
        found = held.exports.get(expr.name)
        if self._notes is not None:
            # The module the name is reached through, and then the name itself:
            # two notes over two spans, the narrower of which is the one an
            # editor asks about when the cursor is on it.
            where = self._defined.get(base.name)
            self._note(base.span, MODULE, base.name, held.path.as_posix(),
                       where[0] if where is not None else INVALID_SPAN)
            if isinstance(found, Function):
                self._note(expr.name_span, FUNCTION, expr.name,
                           found.ty.written(), found.name_span, found.doc or None)
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

    def _holds_a_record(self, name: str) -> bool:
        """Whether *name* stands for a value here rather than for a module.

        Asked of a name before it is read as a module or as an enumeration,
        because those two are reached through a name that is not a value and a
        field is reached through one that is.  A name bound to something that
        was already reported answers yes, so that what follows the mark is
        looked at as a field and nothing further is said about either.
        """
        local = self._find_local(name)
        if local is not None:
            held = self._held_by(local)
            return isinstance(held, ProductType) or held is ERROR
        found = self._provided(name)
        return isinstance(found, GlobalVar) \
            and isinstance(found.value_type, ProductType)

    def _field_of(self, builder: IRBuilder, expr: ast.Member,
                  expected: Type | None) -> Value:
        """Lower `VALUE.NAME`: one field of a record.

        A record is its fields travelling together, which is what a tuple is as
        well, so what reads one out is what reads a tuple member out -- the
        difference between the two being that one of them named its parts, and
        that is a question here and nowhere below.
        """
        somewhere = self._record_at(builder, expr.base)
        if somewhere is not None:
            # The record is somewhere, so only the field is read: reading the
            # whole of it to take one part out would read what nothing asked
            # for, and a record holding a record has no register to be read
            # into at all.
            where, ty = somewhere
            return self._field_of_place(builder, where, ty, expr, expected)
        value = self._lower_expr(builder, expr.base, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, ProductType):
            self._diags.emit(D.LANG_IMPORT_NOT_A_MODULE, expr.base.span,
                             name="an expression")
            return UndefConst(ERROR)
        if _field_index(ty, expr.name) is None:
            self._diags.emit(D.LANG_PRODUCT_NO_SUCH_FIELD, expr.name_span,
                             type=ty.written(), name=expr.name)
            return UndefConst(ERROR)
        # A record that arrived as a value and not as a place -- what a call
        # answered with -- is put somewhere first, so that one rule reads a
        # field however the record got here and a field of a field needs no
        # second one.
        place = builder.frame(ty, expr.span)
        self._record_into(builder, place, value, ty, expr.span)
        return self._field_of_place(builder, place, ty, expr, expected)

    def _field_of_place(self, builder: IRBuilder, where: Value,
                        ty: ProductType, expr: ast.Member,
                        expected: Type | None) -> Value:
        """One field of the record at *where*, read and nothing else."""
        at = _field_index(ty, expr.name)
        if at is None:
            self._diags.emit(D.LANG_PRODUCT_NO_SUCH_FIELD, expr.name_span,
                             type=ty.written(), name=expr.name)
            return UndefConst(ERROR)
        place = self._field_place(builder, where, ty, at, expr.span)
        held = ty.fields[at][1]
        found = (self._record_from(builder, place, held, expr.span)
                 if isinstance(held, ProductType)
                 else builder.load(place, expr.span))
        if not self._accepts(expected, found.ty):
            self._report_mismatch(expr.span, found.ty, expected)
        return found

    def _record_at(self, builder: IRBuilder, expr: ast.Expr,
                   writing: bool = False) -> tuple[Value, ProductType] | None:
        """Where the record *expr* names is, where it is somewhere.

        Four things are: a name given storage of its own, which every record
        local is, and a variable at the top level; what a reference names; an
        element of an array, the elements being where the array is; and a field
        of any of those, which is one offset further in.  A record that is
        somewhere is read a field at a time, which is the only way one holding a
        record can be read at all.
        """
        match expr:
            case ast.NameRef():
                local = self._find_local(expr.name)
                if local is not None and local.placed \
                        and isinstance(local.held, ProductType):
                    if expr.name != self._taking_a_reference \
                            and self._lent_out(expr.name, expr.span, writing):
                        return None
                    local.read = True
                    if self._notes is not None:
                        # What the name is, for whatever asked: a record reached as a
                        # place is still the name being read, and an editor asking
                        # about it here should be told the same thing it is told
                        # where the name stands for a value.
                        self._note(expr.span,
                                   PARAMETER if local.is_parameter else VARIABLE,
                                   expr.name, local.held.written(), local.span)
                    return (local.value, local.held)
                if local is not None:
                    return None
                # A variable at the top level is a place for as long as the
                # program is, which is the same kind of place a record local
                # got given and is reached the same way from here down.
                found = self._provided(expr.name)
                if isinstance(found, GlobalVar) \
                        and isinstance(found.value_type, ProductType):
                    return (builder.address(found, expr.span), found.value_type)
                return None
            case ast.Deref():
                value = self._lower_expr(builder, expr.operand, None)
                ty = self._value_type_of(value)
                if isinstance(ty, PtrType) \
                        and isinstance(ty.pointee, ProductType):
                    return (value, ty.pointee)
                return None
            case ast.Element():
                # An array's elements are where the array is, so one of them is
                # a place whatever it holds.  The index is checked here as it is
                # anywhere else: what this answers is one element and not a run.
                place = self._place_of_an_element(builder, expr)
                if place is None or not isinstance(place[1], ProductType):
                    return None
                return (place[0], place[1])
            case ast.Member():
                # A variable another module exports, which is a place for as long
                # as the program is, exactly as one of this file's is: reached by a
                # path rather than a name and the same thing at the end of it.
                shared = self._exported_variable(expr)
                if shared is not None \
                        and isinstance(shared.value_type, ProductType):
                    return (builder.address(shared, expr.span),
                            shared.value_type)
                found = self._record_at(builder, expr.base, writing)
                if found is None:
                    return None
                where, ty = found
                at = _field_index(ty, expr.name)
                if at is None or not isinstance(ty.fields[at][1], ProductType):
                    return None
                held = ty.fields[at][1]
                assert isinstance(held, ProductType)
                return (self._field_place(builder, where, ty, at, expr.span),
                        held)
            case _:
                return None

    def _enum_value(self, defined: _NamedType, expr: ast.Member) -> Value:
        """The value of an enumeration written as `TYPE.NAME`."""
        return self._value_of_enum(self._resolved(defined), expr)

    def _value_of_enum(self, ty: Type, expr: ast.Member) -> Value:
        """The value *expr* names of the enumeration *ty*."""
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, EnumType):
            self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, expr.name_span,
                             name=expr.name, type=ty.written())
            return UndefConst(ERROR)
        index = ty.index_of(expr.name)
        if index is None:
            self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, expr.name_span,
                             name=expr.name, type=ty.written())
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
            case ast.CharLit():
                return CHAR
            case ast.StringLit():
                return STR
            case ast.Unary() if expr.op is ast.UnaryOp.LENGTH:
                return SIZE_TYPE
            case ast.FloatLit():
                return BUILTIN_TYPES.get(expr.type_name) if expr.type_name else None
            case ast.NameRef():
                placed = self._find_local(expr.name)
                if placed is not None and placed.placed:
                    placed.read = True
                    return placed.held
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
            case ast.IntLit() if ty is CHAR and node.value.type_name is None:
                # A number written where a code point is wanted is that code
                # point, and the one thing about it that can be wrong is that
                # there is no such code point.
                if not 0 <= node.value.value <= MAX_CODE_POINT:
                    self._diags.emit(
                        D.LANG_CHAR_OUTSIDE_UNICODE, node.value.span,
                        value=str(node.value.value),
                        last=format(MAX_CODE_POINT, "X"))
                    return None
                return self._module.char_const(node.value.value)
            case ast.CharLit():
                if ty is not CHAR:
                    return self._wrong_initializer(node, ty, CHAR.written())
                return self._module.char_const(node.value.value)
            case ast.IntLit():
                if not isinstance(ty, IntType):
                    # A suffix names the literal's type outright; without one it
                    # is a number of no particular width, and saying "integer"
                    # is as much as can honestly be said about it.
                    named = BUILTIN_TYPES.get(node.value.type_name or "")
                    return self._wrong_initializer(
                        node, ty, named.written() if named is not None else "integer")
                if not self._literal_matches(node, ty):
                    return None
                if not ty.holds(node.value.value):
                    self._diags.emit(D.LANG_SYNTAX_INTEGER_RANGE, node.value.span,
                                     literal=str(node.value.value), type=ty.written())
                    return None
                return self._module.int_const(ty, node.value.value)
            case ast.BoolLit():
                if ty is not BOOL:
                    return self._wrong_initializer(node, ty, BOOL.written())
                return self._module.bool_const(BOOL, node.value.value)
            case ast.FloatLit():
                if not isinstance(ty, FloatType):
                    return self._wrong_initializer(node, ty, F64.written())
                named = (BUILTIN_TYPES.get(node.value.type_name)
                         if node.value.type_name else None)
                if named is not None and named is not ty:
                    return self._wrong_initializer(node, ty, named.written())
                return self._module.float_const(ty, node.value.value)
            case ast.Allocated() if _in_the_image(node.value):
                # Said outright, which changes nothing about what it comes to.
                return self._constant_value(replace(node, value=node.value.value),
                                            ty)
            case ast.StringLit() if isinstance(ty, StrType):
                return self._text_constant(node.value.value)
            case ast.ListLit() if isinstance(ty, ListType):
                held = []
                for one in node.value.elements:
                    found = self._constant_value(replace(node, value=one),
                                                 ty.element)
                    if not isinstance(found, Const):
                        return None
                    held.append(found)
                pieces = parts_of(ty)
                if not held:
                    run: Const = AddressConst(pieces[0], None)
                else:
                    run = AddressConst(pieces[0], self._image_variable(
                        self._module.types.array_type(ty.element, (len(held),)),
                        self._module.array_const(
                            self._module.types.array_type(ty.element,
                                                          (len(held),)),
                            held)))
                return PartsConst(ty, (run, self._module.int_const(U64, len(held)),
                                       AddressConst(pieces[2], None)))
            case ast.SetLit() if isinstance(ty, SetType) \
                    and (node.value.arena is None or _in_the_image(node.value)):
                return self._table_constant(
                    node, ty, [(one, None) for one in node.value.elements])
            case ast.DictLit() if isinstance(ty, DictType) \
                    and (node.value.arena is None or _in_the_image(node.value)):
                return self._table_constant(node, ty, list(node.value.entries))
            case ast.ArrayLit() if isinstance(ty, ArrayType):
                # Every element has to be one the compiler knows, which is what
                # a variable at the top level is: bytes in the image and not
                # something a program works out.  They are gathered into one run
                # however many dimensions the shape has, since that is what they
                # are in the image.
                held = self._constant_elements(node, ty, node.value, ty.shape)
                return (None if held is None
                        else self._module.array_const(ty, held))
            case ast.Call() if isinstance(ty, ProductType):
                # A record written out, every field of which the compiler
                # knows: bytes in the image and not something a program works
                # out.  It is the same shape a definition inside a body writes
                # and is checked by the same rules -- every field, each once.
                return self._constant_record(node, ty)
            case ast.Member():
                # A value of an enumeration is written `TYPE.NAME` and is known
                # while compiling, so it is a constant like any literal.
                found = self._enum_value_of(node.value)
                if found is None:
                    return None
                if found.ty is not ty:
                    return self._wrong_initializer(node, ty, found.ty.written())
                return found
            case ast.StringLit():
                return self._wrong_initializer(node, ty, STR.written())
            case ast.ListLit() | ast.SetLit() | ast.DictLit() | ast.ArrayLit() \
                    if _a_container_written(node.value) \
                    and _known_while_compiling(node.value, self._names_an_enum):
                # A container of what the compiler knows, of another type than
                # the one wanted -- or a table `in` an arena, which is built while
                # running.
                self._diags.emit(D.LANG_STATIC_WRONG_CONTAINER, node.value.span,
                                 type=ty.written())
                return None
            case _:
                if self._for_the_image:
                    self._diags.emit(D.LANG_STATIC_NOT_KNOWN, node.value.span)
                    return None
                self._diags.emit(
                    D.IMPL_UNIMPLEMENTED_FEATURE, node.value.span,
                    feature="a top-level variable whose value is not a literal")
                return None

    def _image_definition(self, node: ast.VarDef, declared: Type | None) -> bool:
        """Check what a definition says of the image (4637, 4638).

        A variable that may change cannot hold what is in the image; and one that
        never changes, defined as a container written down of nothing but what
        the compiler knows, has to say it is in the image -- which is what it
        then is, made while compiling -- so a reader knows the table is not built
        each time the line runs.
        """
        if _in_the_image(node.value):
            if node.mutable:
                self._diags.emit(D.LANG_STATIC_CHANGES, node.name_span,
                                 name=node.name)
                return False
            return True
        if node.mutable or not _a_container_written(node.value) \
                or isinstance(node.value, ast.Allocated) \
                or isinstance(declared, (SetType, DictType)) and declared.mutable:
            return True
        if not _known_while_compiling(node.value, self._names_an_enum):
            return True
        derived = self._static_type(node.value)
        if derived is _MIXED or (declared is None and node.type is None
                                 and not isinstance(derived, Type)):
            # Parts that disagree, or nothing that says the type: a mistake
            # the ordinary path has the words for.
            return True
        self._diags.emit(D.LANG_CONSTANT_NOT_STATIC, node.value.span,
                         name=node.name)
        return False

    def _static_type(self, expr: ast.Expr) -> object:
        """The type something known while compiling has, read off what is written.

        A suffix, a string, a truth value, a character, a value of an enumeration
        -- and a container of those, whose elements say its type together, any
        one of them saying it for the rest.  Nothing where nothing says it, and
        `_MIXED` where two parts say different things, which is a mistake the
        ordinary path reports.  Asked quietly, before anything is lowered.
        """
        match expr:
            case ast.IntLit() | ast.FloatLit():
                return BUILTIN_TYPES.get(expr.type_name) if expr.type_name else None
            case ast.BoolLit():
                return BOOL
            case ast.CharLit():
                return CHAR
            case ast.StringLit():
                return STR
            case ast.Member() if self._names_an_enum(expr):
                assert isinstance(expr.base, ast.NameRef)
                held = self._top.get(expr.base.name)
                assert isinstance(held, _NamedType)
                return self._resolved(held)
            case ast.Allocated() if _in_the_image(expr):
                return self._static_type(expr.value)
            case ast.ArrayLit():
                # Any element anywhere in it says the type of all of them, a
                # dimension deep or several; the writing says the shape.
                element = _merged(self._static_leaves(expr))
                shape = _shape_written(expr)
                if not isinstance(element, Type) or shape is None:
                    return element if element is _MIXED else None
                return self._module.types.array_type(element, shape)
            case ast.ListLit() | ast.SetLit():
                element = _merged([self._static_type(one)
                                   for one in expr.elements])
                if not isinstance(element, Type):
                    return element
                return (self._module.types.list_type(element)
                        if isinstance(expr, ast.ListLit)
                        else self._module.types.set_type(element))
            case ast.DictLit():
                key = _merged([self._static_type(one) for one, _ in expr.entries])
                value = _merged([self._static_type(one)
                                 for _, one in expr.entries])
                if key is _MIXED or value is _MIXED:
                    return _MIXED
                if not (isinstance(key, Type) and isinstance(value, Type)):
                    return None
                return self._module.types.dict_type(key, value)
        return None

    def _static_leaves(self, expr: ast.ArrayLit) -> list[object]:
        """What each element of an array written down says its type is, however
        many dimensions deep."""
        found: list[object] = []
        for one in expr.elements:
            if isinstance(one, ast.ArrayLit):
                found.extend(self._static_leaves(one))
            else:
                found.append(self._static_type(one))
        return found

    def _names_an_enum(self, expr: ast.Member) -> bool:
        """Whether `T.NAME` is a value of an enumeration, asked quietly."""
        base = expr.base
        held = self._top.get(base.name) if isinstance(base, ast.NameRef) else None
        return isinstance(held, _NamedType) \
            and isinstance(self._resolved(held), EnumType)

    def _text_constant(self, text: str) -> PartsConst:
        """Text written down, as the constant a `str` in the image is."""
        pieces = parts_of(STR)
        return PartsConst(STR, (AddressConst(pieces[0], self._text_variable(text)),
                                self._module.int_const(
                                    U64, len(text.encode("utf-8"))),
                                AddressConst(pieces[2], None)))

    def _image_variable(self, ty: Type, value: Const) -> GlobalVar:
        """A variable of the image nothing names, holding *value*: a run, a table,
        or what a body put `in ⎕static`."""
        return self._module.add_global(GlobalVar(
            name="".join((STATIC_SYMBOL, str(len(self._module.globals)))),
            value_type=ty, ptr_type=self._module.types.ptr_type(ty),
            initializer=value, linkage=Linkage.INTERNAL))

    def _table_constant(self, node: ast.VarDef, ty: SetType | DictType,
                        written: list[tuple[ast.Expr, ast.Expr | None]]
                        ) -> Const | None:
        """A set or a dictionary in the image: the table a program would build.

        Laid out entry by entry where the program's own insertions would put them
        (`_table_laid_out`), so that a lookup -- which is the same generated code
        whatever made the table -- finds every key, and a walk meets them in the
        order it would.  Nothing gives the table back and nothing grows it: its
        type says nothing may be put in it.
        """
        shape = tables.Shape(key=ty.element) if isinstance(ty, SetType) \
            else tables.Shape(key=ty.key, value=ty.value)
        held = tables.key_ir_type(shape)
        keys: list[tuple[object, Const, Const | None]] = []
        for key, value in written:
            found = self._constant_value(replace(node, value=key), shape.key)
            if not isinstance(found, Const):
                return None
            given: Const | None = None
            if value is not None and shape.value is not None:
                given = self._constant_value(replace(node, value=value),
                                             shape.value)
                if not isinstance(given, Const):
                    return None
            bits = self._key_bits(found)
            if bits is None:
                self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, key.span,
                                 feature="a key of this type in the image")
                return None
            stored: Const = (self._module.int_const(U64, bits)
                             if held is U64 and isinstance(bits, int) else found)
            keys.append((bits, stored, given))
        capacity, slots, count = _table_laid_out(
            keys, _word_hash if held is U64 else _text_hash)
        entry = ProductType((("state", U64), ("key", held),
                             *((("value", shape.value),)
                               if shape.value is not None else ())),
                            name="entry")
        if size_of(entry, _LAYOUT) != shape.stride or (
                shape.value is not None
                and offsets_of(entry, _LAYOUT)[2] != shape.value_at):
            self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, node.value.span,
                             feature="a table of this shape in the image")
            return None
        live = self._module.int_const(U64, tables.LIVE)
        run_ty = self._module.types.array_type(entry, (capacity,))
        entries = self._image_variable(run_ty, self._module.array_const(
            run_ty, [UndefConst(entry) if one is None
                     else self._module.record_const(
                         entry, (live, one[0], *((one[1],) if one[1] is not None
                                                 else ())))
                     for one in slots]))
        block = ProductType((("arena", self._module.types.ptr_type(ARENA, True)),
                             ("mask", U64), ("count", U64), ("stride", U64),
                             ("entries", self._module.types.ptr_type(U8, True)),
                             ("used", U64)), name="table")
        made = self._image_variable(block, self._module.record_const(block, (
            AddressConst(block.fields[0][1], None),
            self._module.int_const(U64, capacity - 1),
            self._module.int_const(U64, count),
            self._module.int_const(U64, shape.stride),
            AddressConst(block.fields[4][1], entries),
            self._module.int_const(U64, count))))
        return AddressConst(ty, made)

    def _key_bits(self, key: Const) -> object | None:
        """What a key is to the table: the word a program's key is widened to,
        or the bytes of text."""
        match key:
            case IntConst():
                assert isinstance(key.ty, IntType)
                return key.value & ((1 << key.ty.bits) - 1)
            case BoolConst():
                return int(key.value)
            case CharConst():
                return key.value
            case EnumConst():
                assert isinstance(key.ty, EnumType)
                holder = key.ty.holder
                assert isinstance(holder, IntType)
                return key.number & ((1 << holder.bits) - 1)
            case PartsConst() if key.ty is STR:
                text = key.parts[0]
                assert isinstance(text, AddressConst)
                var = text.target
                assert isinstance(var, GlobalVar) \
                    and isinstance(var.initializer, ArrayConst)
                return bytes(one.value for one in var.initializer.elements
                             if isinstance(one, IntConst))
        return None

    def _constant_record(self, node: ast.VarDef,
                         ty: ProductType) -> Const | None:
        """A record written out at the top level, as the bytes it comes to."""
        written = node.value
        assert isinstance(written, ast.Call)
        if not self._is_record_literal(written, ty):
            self._diags.emit(
                D.IMPL_UNIMPLEMENTED_FEATURE, written.span,
                feature="a top-level variable whose value is not a literal")
            return None
        given = self._fields_given(written, ty)
        if given is None:
            return None
        held: list[Const] = []
        for name, field_ty in ty.fields:
            found = self._constant_value(
                replace(node, value=given[name].value), field_ty)
            if not isinstance(found, Const):
                return None
            held.append(found)
        return self._module.record_const(ty, held)

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
                         name=node.name, expected=ty.written(), found=found)
        return None

    def _literal_matches(self, node: ast.VarDef, ty: Type) -> bool:
        """Check a suffixed literal against the type the variable was declared."""
        literal = node.value
        if not isinstance(literal, ast.IntLit) or literal.type_name is None:
            return True
        named = BUILTIN_TYPES.get(literal.type_name)
        if named is not None and named is not ty:
            self._diags.emit(D.LANG_TYPE_INITIALIZER_MISMATCH, literal.span,
                             name=node.name, expected=ty.written(), found=named.written())
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
        self._unit_scopes.append({})

    def _pop_scope(self) -> None:
        """Leave the innermost scope, reporting values nothing read."""
        # A reference kept by a name in this scope is gone with the name, so
        # the place it named is free to be lent again.
        depth = len(self._scopes)
        self._borrows = [b for b in self._borrows if b.depth < depth]
        self._unit_scopes.pop()
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
        if local.placed and local.written:
            # What such a name stands for is a place, and the value bound to it
            # is where the place is.  Writing through it is using that value as
            # much as reading through it is, so a place written and not read is
            # not a value nobody read -- it is the reason the place is there.
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
                    is_parameter: bool = False,
                    builder: IRBuilder | None = None,
                    placed_as: Type | None = None) -> None:
        """Bind a name in the innermost scope, reporting one already bound there.

        A name the body takes a reference to is given storage of its own here
        and stands for that storage from the start, so that it is one thing
        everywhere in the function: reading it is then a load and assigning to
        it a store, as they are for a variable at the top level.  Which names
        those are was settled before the body was walked, because a name given
        storage in one arm of a branch and not in another would be two different
        things where the arms meet.
        """
        scope = self._scopes[-1]
        previous = scope.get(name)
        if previous is not None:
            self._diags.emit(D.LANG_FILESTRUCT_DUPLICATE_DEFINITION, span,
                             name=name).note(
                D.LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous.span, name=name)
            return
        if placed_as is not None:
            # A name that already stands for a place: what was handed over is
            # the address and not what is there, which is what a lambda's
            # capture by reference brings in.
            scope[name] = _Local(name=name, value=value, span=span,
                                 mutable=mutable, placed=True, held=placed_as,
                                 value_span=value_span if value_span.is_valid
                                 else span, is_parameter=is_parameter,
                                 depth=len(self._scopes))
            self._places[id(value)] = scope[name]
            return
        held = self._value_type_of(value)
        # A record is given storage of its own whether or not anything takes its
        # address: a field is read at an offset, so the record has to be
        # somewhere for there to be an offset from.  Binding one then copies it,
        # which is what a record being a value rather than a place means.
        placed = (builder is not None
                  and (name in self._addressed or isinstance(held, ProductType))
                  and (_can_be_referred_to(held) or isinstance(held, ListType)))
        if placed:
            assert builder is not None
            # A variable the program wrote as an ordinary name and the compiler
            # put in memory: the program did not ask for that, so the log says so
            # and says which of the two reasons it was.  They are different things
            # for a reader to do something about -- one is in the program and one
            # is in the type -- and saying "something takes its address" of a
            # record nothing takes the address of sends a reader looking for a
            # reference that is not there.
            self._module.reports.record(
                ReportKind.PLACE_LOCAL, name,
                "something takes its address, so it is kept in storage of its "
                "own rather than in a register"
                if name in self._addressed else
                "it is a record, and a field is read at an offset, so it is kept "
                "in storage of its own for there to be an offset from",
                span)
            place = builder.frame(held, span)
            if isinstance(held, ProductType):
                self._record_into(builder, place, value, held, span)
            else:
                builder.store(place, value, span)
            value = place
        scope[name] = _Local(name=name, value=value, span=span, mutable=mutable,
                             value_span=value_span if value_span.is_valid else span,
                             is_parameter=is_parameter, depth=len(self._scopes),
                             placed=placed, held=held if placed else None)
        if placed:
            # Where the place is, so that a reference worked out from it can be
            # walked back to how long it lasts.
            self._places[id(value)] = scope[name]

    def _report_undefined(self, name: str, span: Span) -> None:
        """Report a name nothing here stands for, saying which nothing it is.

        Inside a lambda a name may be one the place the lambda was written can
        see perfectly well, and the lambda did not bring it in -- which is a
        different mistake from a name nobody has, and the one worth pointing at:
        what a lambda depends on is written at the top of it.
        """
        if any(name in scope for scope in self._outside):
            self._diags.emit(D.LANG_CAPTURE_NOT_LISTED, span, name=name)
            return
        self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, span, name=name)

    def _held_by(self, local: _Local) -> Type:
        """The type a name has, whether it stands for a value or for a place."""
        if local.placed and local.held is not None:
            return local.held
        return self._value_type_of(local.value)

    def _find_local(self, name: str) -> _Local | None:
        """The innermost binding of *name*, if there is one."""
        for scope in reversed(self._scopes):
            found = scope.get(name)
            if found is not None:
                return found
        return None

    def _note(self, span: Span, kind: str, name: str, detail: str,
              defined: Span = INVALID_SPAN, doc: str | None = None) -> None:
        """Write down what a name turned out to be, where anything is listening.

        One test against nothing where nobody is, which is what makes it
        affordable to call this wherever a name is resolved.
        """
        if self._notes is None:
            return
        self._notes.add(Note(span=span, kind=kind, name=name, detail=detail,
                             defined=defined, doc=doc))

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
            if self._notes is not None:
                # Nothing is worked out unless something asked: a build never
                # reaches past this test.
                held = found.held if found.placed and found.held is not None \
                    else found.value.ty
                self._note(ref.span,
                           PARAMETER if found.is_parameter else VARIABLE,
                           ref.name, held.written(), found.span)
            return found.value
        found_global = self._provided(ref.name)
        if isinstance(found_global, GlobalVar):
            if self._notes is not None:
                self._note(ref.span, VARIABLE, ref.name,
                           found_global.value_type.written(), found_global.span)
            return found_global
        if isinstance(found_global, LoadedModule):
            self._diags.emit(D.LANG_IMPORT_MODULE_AS_VALUE, ref.span, name=ref.name)
            return None
        self._report_undefined(ref.name, ref.span)
        return None

    # -- collection ------------------------------------------------------------

    def _check_doc(self, node: ast.Definition) -> None:
        """Check what a documentation comment claims about what it documents.

        A comment is prose and the compiler has nothing to say about prose.  What
        it does have something to say about is the part of one written the way
        Doxygen writes it: a `\\param` names a parameter, and whether the thing
        has a parameter of that name is a question with an answer.  A comment
        that gets it wrong tells a reader about something that is not there,
        which is worse than saying nothing -- so these are warnings and they are
        on, like every other warning about a program that says one thing and
        means another.
        """
        text = getattr(node, "doc", None)
        if not text:
            return
        found = parse_doc(text)
        if not found.parts:
            return
        lines = getattr(node, "doc_lines", ())
        is_function = isinstance(node, ast.FuncDef)
        takes = {param.name for param in node.params} if is_function else set()
        answers = is_function and node.ret_type is not None
        documented: set[str] = set()
        for part in found.parts:
            where = _doc_span(lines, part)
            written = part.written
            if not part.known:
                self._diags.emit(D.LANG_DOC_UNKNOWN_COMMAND, where,
                                 command=written)
                continue
            if part.command in ("param", "return") and not is_function:
                self._diags.emit(D.LANG_DOC_NOT_A_FUNCTION, where,
                                 command=written, owner=node.name)
                continue
            if part.command == "param":
                if part.subject in documented:
                    self._diags.emit(D.LANG_DOC_PARAM_TWICE, where,
                                     name=part.subject)
                elif part.subject not in takes:
                    self._diags.emit(D.LANG_DOC_PARAM_UNKNOWN, where,
                                     command=written, name=part.subject,
                                     owner=node.name)
                documented.add(part.subject)
            elif part.command == "return" and not answers:
                self._diags.emit(D.LANG_DOC_RETURN_OF_NOTHING, where,
                                 command=written, owner=node.name)

    def _as_the_entry_hands_over(self, node: ast.FuncDef,
                                attrs: Sequence[BoundAttr],
                                params: tuple[Type, ...]) -> tuple[Type, ...]:
        """The startup function's parameters as the entry point passes them.

        A program writes `init: std.Init` and has the record; what the entry point
        hands over is *where* the record is, it being in the image and there being no
        caller to put it in registers.  So the signature carries a pointer and the
        body binds the name to that place, which is what a record name means anyway.

        `mut` on the parameter is what says the program may write the record, and it
        is the pointer that carries it: a reference that may be written is `&mut`.
        """
        if not params or self._function_attrs(attrs)[0].special \
                is not SpecialKind.STARTUP:
            return params
        if not isinstance(params[0], ProductType) \
                or params[0].name != STARTUP_ARGUMENT_TYPE_NAME:
            return params
        return (self._module.types.ptr_type(params[0], node.params[0].mutable),
                *params[1:])

    def _check_several(self, node: ast.FuncDef) -> None:
        """Check a parameter that stands for all the arguments from there on.

        It has to be a macro's, and it has to be the last: there is nothing after
        one for an argument to reach, and how many arrived is a question about a
        piece of the program rather than a count written down.
        """
        for at, param in enumerate(node.params):
            if not param.several:
                continue
            why = ""
            if not node.is_macro:
                why = "only a macro has one"
            elif at != len(node.params) - 1:
                why = "nothing after it could be given anything"
            if why:
                self._diags.emit(D.LANG_SEVERAL_NOT_A_MACRO_TAIL, param.span,
                                 name=param.name, why=why)

    def _a_type_here(self, node: ast.FuncDef) -> bool:
        """Whether the first part of a definition's name is a type this file defines.

        This file and not another's: a type's own file is where what belongs to it
        is written, which is the smaller rule and the one that can be relaxed.
        Saying yes to a type from elsewhere brings with it the question of who may
        add what to whose type, and nobody has asked it.
        """
        assert node.held is not None
        found = self._top.get(node.held)
        if isinstance(found, _NamedType):
            return True
        self._diags.emit(D.LANG_MEMBER_NOT_A_TYPE_HERE,
                         node.held_span or node.name_span, name=node.held,
                         found="nothing here" if found is None
                         else "not a type this file defines")
        return False

    def _collect_function(self, node: ast.FuncDef, path: str) -> _Collected | None:
        """Register one function definition without looking at its body."""
        self._check_doc(node)
        if not self._declare(_declared_as(node), node.name_span, path):
            return None
        whole = _written_name(node)
        if node.held is not None and not self._a_type_here(node):
            return None
        self._check_several(node)
        attrs = self._bind_attributes(node.attrs, AttrTarget.FUNCTION)
        written: list[str] = []
        for param in node.params:
            _parameters_in(param.type, written)
        if written:
            return self._collect_generic(node, path, attrs, tuple(written))
        answered: list[str] = []
        _parameters_in(node.ret_type, answered)
        if answered:
            # A type parameter only in what it answers with: the types are
            # worked out from the arguments, so nothing at a call could say it.
            self._diags.emit(D.LANG_GENERIC_NOT_DETERMINED, node.name_span,
                             name=answered[0])
            return None
        # What a definition says it raises holds while its signature is checked
        # here and again while its body is checked in the second pass, so the
        # same expectation is put back in force there.
        pairs = self._expected_numbers(attrs)
        self._expected_pairs[node.name] = pairs
        expectation = self._begin_expecting(pairs)
        try:
            params = tuple(self._resolve_type(p.type) for p in node.params)
            params = self._as_the_entry_hands_over(node, attrs, params)
            ret = self._return_type(node.ret_type)
            borrows = self._borrowed_from(node, ret)
            made_in, param_made_in = self._made_in_of(node, params, ret)
            hands_out = _hands_out_of(node)
            if hands_out is not None:
                params = (*params, self._module.types.ptr_type(ARENA, mutable=True))
            func_attrs, linkage = self._function_attrs(attrs)
            func = Function(name=whole, hands_out=hands_out,
                            ty=self._module.types.func_type(
                                params, ret, func_attrs.listable),
                            attrs=func_attrs, linkage=linkage,
                            param_names=tuple(p.name for p in node.params),
                            writes_arrays=_writes_arrays(node, params),
                            defaults=self._defaults_of(node, params),
                            borrows_from=borrows,
                            made_in=made_in, param_made_in=param_made_in,
                            exported=self._is_export(attrs),
                            cconv=(func_attrs.abi if func_attrs.abi is not None
                                   else DEFAULT_CCONV),
                            span=node.span, name_span=node.name_span,
                            source_path=path, doc=node.doc or "")
            if func_attrs.listable and not params:
                self._diags.emit(D.LANG_LISTABLE_TAKES_NOTHING, node.name_span,
                                 name=node.name)
            self._check_external(node, func_attrs)
            # Wherever it stands and whoever wrote it: a record laid out for
            # something else to read travels as a reference or not at all, so
            # that one type is not passed two ways.
            for one, param in zip(params, node.params):
                self._not_by_value(one, param.type.span)
                self._not_a_piece(one, param.type.span, node.name)
            self._not_a_piece(ret, node.ret_type.span if node.ret_type is not None
                              else node.name_span, node.name)
            if node.ret_type is not None:
                self._not_by_value(ret, node.ret_type.span)
            self._module.add_function(func, key=self._key(whole))
            if node.held is None:
                self._top[node.name] = func
            else:
                self._members[(node.held, node.name)] = func
            self._an_operator(node, func)
            self._owned.append(func)
            self._register_special(func, node)
        finally:
            if expectation is not None:
                self._diags.release(expectation)
        return _Collected(node=node, attrs=attrs, func=func, expectation=expectation)

    def _check_external(self, node: ast.FuncDef, attrs: FuncAttrs) -> None:
        """Check a function said to be defined somewhere else, and one not.

        The two questions are one: a body and a definition elsewhere are two
        answers to "where is this", and exactly one of them has to be given.
        What may be named is what the runtime packaged with the compiler
        defines, there being no linker and nothing else to reach.
        """
        if attrs.external is None:
            if node.body is None and not attrs.builtin:
                self._diags.emit(D.LANG_FUNCDEF_NO_BODY, node.name_span,
                                 name=node.name)
            return
        if node.body is not None:
            self._diags.emit(D.LANG_FUNCDEF_EXTERNAL_HAS_A_BODY, node.name_span,
                             name=node.name)
        if attrs.external not in runtime_names():
            self._diags.emit(D.LANG_FUNCDEF_EXTERNAL_UNKNOWN, node.name_span,
                             symbol=attrs.external)

    def _not_by_value(self, ty: Type, where: Span) -> None:
        """Report a record laid out for something else crossing a call whole.

        Such a record is passed by rules this compiler does not follow: what
        crosses is a reference, which every convention agrees about and which
        this one therefore does not have to classify.
        """
        if isinstance(ty, ProductType) and ty.abi:
            self._diags.emit(D.LANG_ABI_TYPE_BY_VALUE, where, type=ty.written())

    def _defaults_of(self, node: ast.FuncDef,
                     types: Sequence[Type]) -> tuple[Const | None, ...]:
        """What each parameter of *node* is given where a call gives it nothing.

        A default belongs to the function and not to any one call of it: a
        caller in another file sees the function and never the names its
        definition could have used, so what is written has to be a value the
        compiler settles here, once, and hands over unchanged at every call.
        That is narrower than C++, where a default is an expression looked up in
        the definition's scope and worked out afresh at each call; it is what
        can be promised without a scope travelling with the function.

        A parameter with no default after one that has is refused, because
        arguments written without a name fill the parameters from the left and a
        call one argument short could not say which one it left out.
        """
        found: list[Const | None] = []
        defaulted = False
        for param, ty in zip(node.params, types):
            if param.default is None:
                if defaulted:
                    self._diags.emit(D.LANG_PARAM_DEFAULT_ORDER, param.span,
                                     name=param.name)
                found.append(None)
                continue
            defaulted = True
            found.append(self._default_value(param, ty))
        return tuple(found)

    def _default_value(self, param: ast.Param, ty: Type) -> Const | None:
        """The value written as *param*'s default, where it is one.

        What may be written is what the compiler can settle into a value of the
        type and hand over as an argument: a literal of it, or a value of an
        enumeration.  A collection or a run of elements is not one of them yet,
        since what a call hands over is values and those live in memory.
        """
        written = param.default
        assert written is not None
        if ty is ERROR:
            return None
        if not isinstance(written, (ast.IntLit, ast.CharLit, ast.BoolLit,
                                    ast.FloatLit, ast.Member)):
            self._diags.emit(D.LANG_PARAM_DEFAULT_NOT_SETTLED, written.span,
                             name=param.name)
            return None
        stood = ast.VarDef(span=param.span, name=param.name,
                           name_span=param.span, type=param.type, value=written)
        value = self._constant_value(stood, ty)
        return value if isinstance(value, Const) else None

    def _made_in_of(self, node: ast.FuncDef, params: Sequence[Type],
                    ret: Type) -> tuple[tuple[int, ...] | None,
                                        tuple[int | None, ...]]:
        """Which arena parameters the answer and each parameter were made in.

        `→ T in a, b` and `s: T in a` name parameters, and only one holding an arena
        can be named (4618): a local arena is given back before the call ends, and
        `⎕heap` is what saying nothing already allows.  Said of something that
        points nowhere -- a number, a truth value -- it says nothing (4621).
        """
        told = id(node) in self._made_in_told
        self._made_in_told.add(id(node))
        names = [one.name for one in node.params]

        def arena(name: str, span: Span) -> int | None:
            at = names.index(name) if name in names else None
            if at is None or not (isinstance(params[at], PtrType)
                                  and params[at].pointee is ARENA):
                if not told:
                    self._diags.emit(D.LANG_MADE_IN_NOT_AN_ARENA, span,
                                     name=name)
                return None
            return at

        def pointless(ty: Type, span: Span) -> None:
            if not told and ty is not ERROR and not _points_somewhere(ty):
                self._diags.emit(D.LANG_MADE_IN_POINTS_NOWHERE, span,
                                 type=ty.written())

        made: tuple[int, ...] | None = None
        if node.made_in:
            pointless(ret, node.made_in[0][1])
            # `⎕heap` may be named too, which says what nothing would and fixes
            # it: the answer is the heap's, every one of them, and travels without
            # the allocator.  It is `HEAP_AT` among the places.
            if _hands_out_of(node) is not None:
                made = (HANDS_AT,)
            elif any(name == STATIC_NAME for name, _ in node.made_in):
                # The image is said alone: an answer that may be made in an
                # arena as well carries its allocator, which is what saying the
                # image is there to spare it.
                if len(node.made_in) > 1:
                    self._diags.emit(D.LANG_STATIC_SAID_ALONE,
                                     node.made_in[0][1])
                made = (STATIC_AT,)
            else:
                found = (HEAP_AT if name == HEAP_NAME else arena(name, span)
                         for name, span in node.made_in)
                made = tuple(sorted({at for at in found if at is not None}))
        given: list[int | None] = []
        for param, ty in zip(node.params, params):
            if param.made_in is None:
                given.append(None)
                continue
            pointless(ty, param.made_in[1])
            given.append(arena(*param.made_in))
        return made, tuple(given)

    def _params_made_in(self, node: ast.FuncDef, func: Function) -> None:
        """Give the parameters of a body the provenance the signature says.

        `s: str in a` was made in what `a` names.  One that points somewhere and
        says nothing may have been made anywhere: it carries its allocator, and what
        the body knows is only that it lasts as long as the call.  A container
        handed over keeps its elements in the arena `in` names, or in `⎕heap`.

        What the answer is made in is what `→ T in a` says, and `⎕heap` where it
        says nothing.
        """
        def local_of(at: int) -> _Local | None:
            return self._find_local(node.params[at].name)
        for at, param in enumerate(node.params):
            local = local_of(at)
            if local is None or self._is_arena(local) \
                    or not _points_somewhere(func.ty.params[at]):
                continue
            arena_at = (func.param_made_in[at]
                        if at < len(func.param_made_in) else None)
            arena = local_of(arena_at) if arena_at is not None else None
            if _is_owning(func.ty.params[at]):
                if arena is not None and self._is_arena(arena):
                    local.holds_in = id(arena)
                    local.arenas = frozenset({id(arena)})
                else:
                    local.holds_in = _HEAP
                    local.arenas = frozenset({_HEAP, _container_key(local)})
                continue
            if arena is not None and self._is_arena(arena):
                local.arenas = frozenset({id(arena)})
            else:
                local.arenas = frozenset({id(local)})
                self._unknown_by_id[id(local)] = local
        self._answer_kinds = []
        self._thin_to = None
        if func.made_in is None:
            self._answer_made_in = (frozenset({_HEAP}), HEAP_NAME)
            return
        if func.made_in == (HANDS_AT,):
            # The arena is the body's own and does not exist yet: what answers
            # are allowed is said once `let` has made it.
            self._answer_made_in = (frozenset(), "".join(
                ("'", func.hands_out or "", "'")))
            return
        if func.made_in == (STATIC_AT,):
            # Nothing at all: what is in the image was made in nothing.
            self._answer_made_in = (frozenset(), STATIC_NAME)
            return
        allowed: set[int] = set()
        for at in func.made_in:
            if at == HEAP_AT:
                allowed.add(_HEAP)
                continue
            arena = local_of(at)
            if arena is not None and self._is_arena(arena):
                allowed.add(id(arena))
        self._answer_made_in = (
            frozenset(allowed),
            _one_of([HEAP_NAME if at == HEAP_AT else node.params[at].name
                     for at in func.made_in]))
        if allowed == {_HEAP} and _thin_able(func.ty.ret):
            # `⎕heap` named: every answer is put in it, text in the image
            # included, which is what naming it says -- so the caller can add it
            # to what comes back.  An arena named lets text in the image through
            # as it is, and travels thin only where the body shows none.
            self._thin_to = _HEAP

    def _handing_callee(self, expr: ast.Expr) -> str | None:
        """The name of what *expr* calls, where that hands out an arena."""
        if not isinstance(expr, ast.Call) or not isinstance(expr.callee, ast.NameRef):
            return None
        local = self._find_local(expr.callee.name)
        if local is not None:
            held = self._held_by(local)
            return expr.callee.name if isinstance(held, FuncType) \
                and held.hands_out else None
        found = self._top.get(expr.callee.name)
        if isinstance(found, Function) and found.hands_out is not None:
            return expr.callee.name
        if isinstance(found, _Generic) and _hands_out_of(found.node) is not None:
            return expr.callee.name
        return None

    def _lower_receiving(self, builder: IRBuilder, stmt: ast.VarDef) -> None:
        """Lower `let v: T in kept = f(…)`: an arena received with an answer.

        The definition makes the arena -- `kept`, in this body's frame, as
        `let kept: mut arena = ⎕arena` would -- and the call is handed where it is,
        so what the call makes in its own arena is made here.  The arena is then
        this body's like any it made: given back with `⎕empty`, and before a pure
        function leaves.  What is bound with `in` has to be a call that hands one
        out (4631).
        """
        name, where = stmt.made_in
        self._lower_local(builder, ast.VarDef(
            span=stmt.span, name=name, name_span=where,
            type=ast.TypeRef(span=where, name=ARENA_NAME),
            value=ast.NameRef(span=where, name=EMPTY_ARENA_NAME), mutable=True))
        arena = self._find_local(name)
        if arena is None:
            return
        # Read, so that one only received and given back is not "never read".
        arena.read = True
        outer = (self._receiving, self._received)
        self._receiving, self._received = (arena, stmt.value), None
        try:
            self._lower_local(builder, replace(stmt, made_in=None))
            self._made_in(stmt.name, stmt.value, stmt.span)
            got = self._received
        finally:
            self._receiving, self._received = outer
        if got is None:
            self._diags.emit(D.LANG_BOUND_IN_NOT_HANDED_OUT, where, name=name)

    def _arena_for(self, expr: ast.Call, name: str) -> Value | None:
        """Where the arena a call hands out with its answer is to be kept.

        Only a call that is the whole of what `let v: T in kept = …` is given may
        hand one out (4629): written anywhere else, what it hands out would have no
        name to be given back by.  The definition made the arena already; the call
        is handed where it is, and its own `let` starts it there.
        """
        receiving = self._receiving
        if receiving is None or receiving[1] is not expr:
            self._diags.emit(D.LANG_HANDED_OUT_UNBOUND, expr.span, name=name)
            return None
        self._receiving = None
        self._received = receiving[0]
        return receiving[0].value

    def _written_for(self, expr: ast.Call, func: Function) -> dict[int, ast.Expr]:
        """What a call wrote for each parameter, by the parameter's place."""
        found: dict[int, ast.Expr] = {}
        place = 0
        for one in expr.args:
            if isinstance(one, ast.Named):
                if one.name in func.param_names:
                    found[func.param_names.index(one.name)] = one.value
            else:
                found[place] = one
                place += 1
        return found

    def _where_made(self, expr: ast.Call, func: Function) -> None:
        """Hold a call to what its callee's signature says about allocators.

        What a parameter was said to be made in, it has to have been made in (4620),
        or in something lasting longer; a container handed to a parameter has to keep
        its elements where the parameter says (4625).  The call's answer was made in
        what the named parameters were given, or in `⎕heap` where the signature names
        none -- written down for the walk that follows a value to where it was made.
        And a container of `⎕heap`'s handed over may have had elements replaced, so
        what was read out of it before is dead.
        """
        self._last_made_in = None
        written = self._written_for(expr, func)
        for at, arena_at in enumerate(func.param_made_in):
            if at not in written:
                continue
            param_ty = func.ty.params[at]
            if _is_owning(param_ty):
                # Where its elements are kept matters only to a body that may
                # replace one, which only a `mut` parameter may.
                if at in func.writes_arrays:
                    self._container_handed(expr, func, written, at, arena_at)
                continue
            if arena_at is None or arena_at not in written:
                continue
            stray = self._arenas_in(written[at]) - self._arenas_in(written[arena_at])
            for one in stray:
                where = self._arena_by_id.get(one) or self._unknown_by_id.get(one)
                if where is None and one != _HEAP:
                    continue
                self._diags.emit(D.LANG_MADE_IN_ARGUMENT_ELSEWHERE,
                                 written[at].span, param=func.param_names[at],
                                 found=where.name if where is not None
                                 else HEAP_NAME,
                                 func=func.name,
                                 arena=func.param_names[arena_at])
                break
        for at in func.writes_arrays:
            if at in written and not self._may_write(written[at]):
                self._diags.emit(D.LANG_ARRAY_HANDED_NOT_WRITABLE,
                                 written[at].span, param=func.param_names[at],
                                 func=func.name)
        if not (owned.points(func.ty.ret) or _holds_a_lambda(func.ty.ret)):
            return
        if func.attrs.external is not None:
            # What the outside answers is the outside's, which nothing here gives
            # back: the bytes the kernel handed over are where they are.
            self._made_from[id(expr)] = (expr, frozenset())
            return
        if func.made_in is None:
            self._last_made_in = frozenset({_HEAP})
            self._holds_from[id(expr)] = _HEAP
        else:
            made: set[int] = set()
            for at in func.made_in:
                if at == HEAP_AT:
                    made.add(_HEAP)
                elif at == HANDS_AT:
                    if self._received is not None:
                        made.add(id(self._received))
                elif at in written:
                    made.update(self._arenas_in(written[at]))
            self._last_made_in = frozenset(made)
            found = self._owner_of(self._last_made_in)
            if found is not None:
                self._holds_from[id(expr)] = found
        self._made_from[id(expr)] = (expr, self._last_made_in)
        # An answer whose allocator the callee fixed is in it, never text in the
        # image: where that is the heap, it is fresh, and what holds it owns it.
        if (func.answer_from is not None or func.made_in == (HEAP_AT,)) \
                and self._last_made_in == {_HEAP}:
            self._fresh_heap.add(id(expr))
        self._callee_of[id(expr)] = func

    def _container_handed(self, expr: ast.Call, func: Function,
                          written: dict[int, ast.Expr], at: int,
                          arena_at: int | None) -> None:
        """Check a container handed to a parameter that keeps elements somewhere.

        Where the parameter says `in a`, the container's elements have to be in what
        the call hands to `a`; where it says nothing, in `⎕heap`.  A container of one
        kind handed where the other is expected would be given back from by the
        wrong rule: a pool's shared elements freed, or the heap's never.
        """
        given = written[at]
        local = self._find_local(given.name) \
            if isinstance(given, ast.NameRef) else None
        holds = local.holds_in if local is not None else self._holder_of(given)
        if arena_at is None:
            wanted: int | None = _HEAP
        elif arena_at in written:
            wanted = self._owner_of(self._arenas_in(written[arena_at]))
        else:
            wanted = None
        if holds is None or holds != wanted:
            self._diags.emit(
                D.LANG_CONTAINER_ALLOCATOR_MISMATCH, given.span,
                param=func.param_names[at], func=func.name,
                wanted=HEAP_NAME if wanted == _HEAP else (
                    "".join(("what '", func.param_names[arena_at], "' is given"))
                    if arena_at is not None else "?"),
                found=self._owner_written(holds) if holds is not None
                else "an allocator nothing says")
            return
        if local is not None and holds == _HEAP:
            self._elements_replaced(local.arenas, local.name, expr.span)
            # The call may have put the heap's in it.
            local.arenas = local.arenas | {_HEAP}

    def _borrowed_from(self, node: ast.FuncDef, ret: Type) -> tuple[int, ...]:
        """Which parameters the answer names what was named by, where it says.

        A reference is only worth having while what it names is still there,
        and a caller cannot see into the function to work that out -- so a
        function handing one back says how long that is.  Two ways of saying
        it: `static` in the type, as long as the program, and a lifetime name,
        as long as whatever else in the signature carries that name.

        Several parameters carrying one name means the shorter of what they
        named, which is the only promise that holds whichever one the body
        picked.
        """
        where = (node.ret_type.span if node.ret_type is not None
                 else node.name_span)
        named = _lifetimes_of(node.ret_type)
        if not isinstance(ret, PtrType):
            if _holds_a_reference(ret) and not named:
                # A reference inside something else, written with no lifetime:
                # there is nowhere to put `static`, which belongs to one
                # reference and there may be several.
                self._diags.emit(D.LANG_REF_ANSWERED, where)
            return ()
        if not named:
            if not ret.lasting:
                self._diags.emit(D.LANG_BORROW_SAYS_NOTHING, where)
            return ()
        carried = tuple(at for at, one in enumerate(node.params)
                        if named & _lifetimes_of(one.type))
        if not carried:
            # A name nothing else carries says nothing, and what the answer
            # would live for is then undecided -- which is the one thing a
            # caller needs from the signature.
            self._diags.emit(D.LANG_LIFETIME_NOT_DETERMINED, where,
                             name="".join((LIFETIME_GLYPH, sorted(named)[0])))
        return carried

    def _collect_generic(self, node: ast.FuncDef, path: str,
                         attrs: list[BoundAttr],
                         written: tuple[str, ...]) -> _Collected | None:
        """Register a function whose types a call settles, without building one.

        Nothing of it is compiled here: what it is, is what was written, and
        what it comes to depends on the types a call gives it.  So the tree is
        kept and each call that says something new about the types makes a
        function of its own out of it.
        """
        answered: list[str] = []
        _parameters_in(node.ret_type, answered)
        for one in answered:
            if one not in written:
                self._diags.emit(D.LANG_GENERIC_NOT_DETERMINED, node.name_span,
                                 name=one)
                return None
        kind = self._function_attrs(attrs)[0].special
        if kind is not None:
            # The runtime's entry point, a constructor and a test are each one
            # thing the program has, and something written once per set of types
            # is none of them.
            self._diags.emit(D.LANG_GENERIC_IS_SPECIAL, node.name_span,
                             name=node.name, attribute=str(kind))
            return None
        self._check_clause_shapes(node.clauses)
        made = _Generic(node=node, path=path, attrs=attrs, parameters=written,
                        exported=self._is_export(attrs),
                        scope=self._top, siblings=self._bundles)
        self._top[node.name] = made
        self._an_operator(node, made)
        return None

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
            case SpecialKind.BUILD:
                if self._module.build is not None:
                    previous = self._module.build
                    self._diags.emit(D.LANG_BUILD_MULTIPLE, node.name_span).note(
                        D.LANG_BUILD_PREVIOUS,
                        self._name_spans.get(previous.name, previous.span),
                        name=previous.name)
                    return
                self._check_build_signature(func, node)
                self._module.build = func
            case SpecialKind.CONSTRUCTOR:
                self._check_ctor_signature(func, node, "constructor")
                self._module.ctors.append(func)
            case SpecialKind.DESTRUCTOR:
                self._check_ctor_signature(func, node, "destructor")
                self._module.dtors.append(func)
            case SpecialKind.TEST_ALWAYS | SpecialKind.TEST_BUILD | SpecialKind.TEST_SUITE:
                self._check_test_signature(func, node)
                self._module.tests.append(func)
            case _:
                pass

    def _check_build_signature(self, func: Function, node: ast.FuncDef) -> None:
        """Check the build function's signature.

        One parameter, a mutable reference to the record the `std` module calls
        `Build`, and nothing answered with: the compiler runs it and hands it the
        object that says what to build, and what it has to say it says by writing
        into that object.  The type is asked for by name, the way the startup
        function's parameter is, so that what a build may say can grow without
        every build function that exists by then having to change.
        """
        if func.ty.ret is not VOID or len(func.ty.params) != 1 \
                or not _is_build_object(func.ty.params[0]):
            self._diags.emit(D.LANG_BUILD_BAD_SIGNATURE, node.name_span,
                             name=node.name)

    def _check_startup_signature(self, func: Function, node: ast.FuncDef) -> None:
        """Check the startup function's signature.

        It takes what the program is started with, or nothing: a program that
        wants neither the devices it inherited nor anything else that arrives
        with it writes no parameter, and that is most of them.  What it may take
        is one thing, the record the `std` module calls `Init`, so that what a
        program is started with can grow without every signature that exists by
        then having to change.
        """
        expected = BUILTIN_TYPES[STARTUP_RETURN_TYPE_NAME]
        if func.ty.ret is ERROR or ERROR in func.ty.params:
            return
        problem: str | None = None
        if len(func.ty.params) > 1:
            problem = "takes more than one parameter"
        elif func.ty.params and not _is_startup_argument(func.ty.params[0]):
            problem = "".join(("takes a '", func.ty.params[0].written(), "'"))
        elif node.params and isinstance(node.params[0].type, ast.RefTypeRef):
            # The record and not a reference to it.  There is one of it -- the type
            # says `@[unique]` -- so handing it over is handing over the thing
            # itself, and a program that wants to pass it on takes the reference
            # where it passes it.
            problem = "".join(("takes a reference to '",
                               STARTUP_ARGUMENT_TYPE_NAME,
                               "' rather than the record itself"))
        elif func.ty.ret != expected:
            problem = "".join(("returns '", func.ty.ret.written(), "'"))
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
            problem = "".join(("it returns '", func.ty.ret.written(), "'"))
        if problem is not None:
            self._diags.emit(D.LANG_FUNCDEF_SPECIAL_BAD_CTOR_SIGNATURE, node.name_span,
                             kind=kind, name=func.name, problem=problem)

    def _check_test_signature(self, func: Function, node: ast.FuncDef) -> None:
        """Check that a test takes nothing and answers whether it passed.

        Nothing calls a test but the runner, so there is nothing to give it;
        what it answers is whether it passed, and a truth value is the whole of
        that.
        """
        if func.ty.ret is ERROR or ERROR in func.ty.params:
            return
        problem: str | None = None
        if func.ty.params:
            problem = "this one takes parameters"
        elif func.ty.ret is not BOOL:
            problem = "".join(("this one answers with '", func.ty.ret.written(),
                               "'"))
        if problem is not None:
            self._diags.emit(D.LANG_FUNCDEF_SPECIAL_BAD_TEST_SIGNATURE,
                             node.name_span, name=func.name, problem=problem)

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

    def _known_convention(self, name: str, attr: BoundAttr) -> None:
        """Report a calling convention the target does not know.

        A name it does not know would otherwise mean the language's own
        convention -- and, the attribute being there, a symbol whose name is not
        mangled either: two surprises at once, and both of them silent.

        What a target knows is asked of the target, and only where the name is
        not one every one of them knows: a program writing `@[cdecl]` or naming
        the language's own convention asks nothing of anyone, and one naming an
        architecture's own pays the reading of that architecture's table of
        conventions and nothing else.
        """
        if name in EVERY_TARGET_KNOWS:
            return
        known = conventions_of(self._module.triple)
        if known is None or name in known:
            # Nothing where no backend answers to the triple: that is the
            # driver's to report, and a name checked against nothing would be a
            # name refused for the wrong reason.
            return
        self._diags.emit(D.LANG_ATTR_UNKNOWN_ABI, attr.node.span, name=name,
                         target=self._module.triple,
                         known=_and_then(sorted(known)))

    def _function_attrs(self, bound: Sequence[BoundAttr]) -> tuple[FuncAttrs, Linkage]:
        """Turn checked attributes into the form the IR carries."""
        special: SpecialKind | None = None
        priority: int | None = None
        inline = InlineHint.DEFAULT
        abi: str | None = None
        external: str | None = None
        can_ignore = False
        impure = False
        io = False
        listable = False
        builtin = False
        linkage = self._linkage_of(bound)
        extra: dict[str, int | str | bool] = {}
        for attr in bound:
            match attr.name:
                case "startup":
                    special = SpecialKind.STARTUP
                case "build":
                    special = SpecialKind.BUILD
                    # Writing into what it was handed is the whole of what a
                    # build function does, so it says so by being one: there is
                    # nothing it could be that also had to be written `impure`.
                    impure = True
                case "builtin":
                    builtin = True
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
                case "io":
                    # What it changes is a device.  Impure to everything below the
                    # checker -- nothing may drop or repeat a write -- and to a
                    # *caller* it asks only for the permission a device carries.
                    io = True
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
                    # A name where the program named one, and the system's
                    # otherwise: `@[abi]` on a function says the same thing
                    # `@[cdecl]` does, and on a type it says how one is laid
                    # out and is no business of the convention.
                    abi = attr.as_str("name") or SYSTEM_CCONV
                    self._known_convention(abi, attr)
                    extra["variadic"] = attr.as_bool("variadic")
                case "external":
                    # Defined somewhere else, under a name of its own, and
                    # called the way this system calls things: there is no
                    # other way to reach something this compiler did not
                    # compile.
                    external = attr.as_str("symbol")
                    abi = SYSTEM_CCONV
                    # And impure by being external.  Nothing here can see what
                    # it does, so it is taken to do everything -- which is the
                    # rule a call through a value already follows, asked here
                    # of a call to a body that is not in this compilation.
                    # Believing one pure would let a call nobody read be
                    # dropped, and the I/O would go with it.
                    impure = True
                case "align":
                    extra["align"] = attr.as_int("bytes")
                case "section":
                    extra["section"] = attr.as_str("name")
                case _:
                    pass
        return FuncAttrs(special=special, priority=priority, inline=inline, abi=abi,
                         io=io,
                         external=external, can_ignore=can_ignore, impure=impure,
                         listable=listable, builtin=builtin, extra=extra), linkage

    # -- types -----------------------------------------------------------------

    # -- types the program defines ---------------------------------------------

    def _collect_type(self, node: ast.TypeDef | ast.EnumDef, path: str) -> None:
        """Register one type definition without working out what it is made of.

        The name is registered in the same namespace as everything else at the
        top level, so a type and a function cannot share one: a name in this
        language stands for one thing, and which kind of thing it is is not
        something a reader should have to work out from where it is written.
        """
        self._check_doc(node)
        if not self._declare(node.name, node.name_span, path):
            return
        attrs = self._bind_attributes(node.attrs, AttrTarget.TYPE)
        for one in attrs:
            # What `@[abi]` says of a record is that it is laid out the way one
            # compiled by something else is, which is one thing and the same
            # whoever compiled the other side.  A convention is about how a
            # function is *called*, so a name here is a name with nothing to
            # say -- and it was read and thrown away, which is the silence this
            # is for.
            if one.name == "abi" and one.as_str("name"):
                self._diags.emit(D.LANG_ATTR_ABI_ON_A_TYPE_NAMES_ONE,
                                 one.node.span, name=one.as_str("name"))
        defined = _NamedType(name=node.name, node=node, origin=path,
                             exported=self._is_export(attrs),
                             abi=any(one.name == "abi" for one in attrs),
                             unique=any(one.name == "unique" for one in attrs),
                             device=any(one.name == "device" for one in attrs))
        self._top[node.name] = defined
        self._named_types.append(defined)

    def _resolved(self, defined: _NamedType) -> Type:
        """What a defined type is made of, worked out on first ask."""
        if defined.ty is not None:
            return defined.ty
        if defined.resolving:
            if self._behind_a_reference == 0 or defined.shell is None:
                self._diags.emit(D.LANG_TYPEDEF_CONTAINS_ITSELF,
                                 defined.node.name_span, name=defined.name)
                defined.ty = ERROR
                return ERROR
            # A reference stands between the type and itself, which is the
            # indirection that makes such a type finite: what a reference
            # occupies is the same whatever it names, so the parts need not be
            # known to point at it.  The object being built is what is handed
            # back, and its parts are filled in before anything reads them.
            return defined.shell
        defined.resolving = True
        defined.shell = _shell_for(defined)
        try:
            made = (self._values_of(defined)
                    if isinstance(defined.node, ast.EnumDef)
                    else self._parts_of(defined))
        finally:
            defined.resolving = False
        defined.ty = _filled_in(defined.shell, made)
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
                             type=named.written())
            return None
        outside = next((n for n in numbers if not named.holds(n)), None)
        if outside is not None:
            self._diags.emit(D.LANG_ENUMDEF_HOLDER_TOO_NARROW, node.holder.span,
                             type=named.written(), value=str(outside))
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
            if node.kind is ast.TypeKind.PRODUCT and _held_in_memory(ty) \
                    and isinstance(ty, SumType):
                # A record travels as the values it is made of, and a sum is
                # not one of those: what a value of one is, is where its bytes
                # are.  A record holding one would therefore have to be held in
                # memory itself, which is the next step and not this one.
                self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, field.type.span,
                                 feature="a record holding a sum")
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
                                     type=ty.written())
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
        return ProductType(made, name=defined.name, origin=defined.origin,
                           abi=defined.abi, unique=defined.unique,
                           device=defined.device)

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
        if isinstance(ref, ast.ListTypeRef):
            element = self._resolve_type(ref.element)
            if element is ERROR:
                return ERROR
            if element is VOID:
                self._diags.emit(D.LANG_ARRAY_ELEMENT_IS_NOTHING, ref.span)
                return ERROR
            return self._module.types.list_type(element)
        if isinstance(ref, ast.TupleTypeRef):
            members = [self._resolve_type(m) for m in ref.members]
            if any(m is ERROR for m in members):
                return ERROR
            for written, held in zip(ref.members, members):
                if isinstance(held, SumType):
                    # A tuple travels as its members, and a sum is where its
                    # bytes are: a tuple holding one and handed back would hand
                    # back where the room it was made in was.  The same step a
                    # record holding one waits on.
                    self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, written.span,
                                     feature="a tuple holding a sum")
                    return ERROR
            return self._module.types.tuple_type(members)
        if isinstance(ref, ast.RefTypeRef):
            return self._reference_type(ref)
        if isinstance(ref, ast.UnitTypeRef):
            return self._united_type(ref)
        if isinstance(ref, ast.FuncTypeRef):
            params = tuple(self._resolve_type(one) for one in ref.params)
            ret = self._return_type(ref.ret)
            if ret is ERROR or any(one is ERROR for one in params):
                return ERROR
            if ref.kept_in is not None and ref.kept_in[0] != HEAP_NAME:
                # A type says only that what was brought in lasts, and `⎕heap` is
                # the one place that says so: an arena's lambda lasts as long as
                # the arena, which nothing in a type can name.
                self._diags.emit(D.LANG_KEPT_IN_NOT_HEAP, ref.kept_in[1],
                                 name=ref.kept_in[0])
                return ERROR
            return self._module.types.func_type(
                params, ret, self._walks_what_it_is_given(ref.attrs),
                hands_out=ref.made_in is not None,
                lasting=ref.kept_in is not None)
        return self._named_type(ref)

    # -- units -----------------------------------------------------------------

    def _united_type(self, ref: ast.UnitTypeRef) -> Type:
        """Resolve `TYPE \N{CURRENCY SIGN}UNIT`, checking that the type can carry one."""
        base = self._resolve_type(ref.base)
        if base is ERROR:
            return ERROR
        unit = self._unit_written(ref.unit)
        if unit is None:
            return ERROR
        if not isinstance(base, (IntType, FloatType)):
            self._diags.emit(D.LANG_UNIT_NOT_A_NUMBER, ref.span,
                             found=base.written())
            return ERROR
        return _carrying(base, unit)

    def _unit_written(self, ref: ast.UnitRef) -> Unit | None:
        """The unit a program wrote, worked out into base units and exponents.

        Read left to right, each factor raised to what was written after it and
        put above or below the line by the sign it carries.  What comes of it is
        a product of powers, so two units written differently are one unit where
        they come to the same thing -- which is what makes the seconds cancel
        when a speed is multiplied by a time.
        """
        made = NO_UNIT
        for factor in ref.factors:
            found = self._unit_named(factor.name, factor.span)
            if found is None:
                return None
            made = made.times(found.raised(abs(factor.exponent))) \
                if factor.exponent > 0 \
                else made.over(found.raised(abs(factor.exponent)))
        return made

    def _unit_named(self, name: str, span: Span) -> Unit | None:
        """The unit *name* stands for: one a program introduced, or a builtin."""
        for scope in reversed(self._unit_scopes):
            found = scope.get(name)
            if found is not None:
                return found
        builtin = BUILTIN_UNITS.get(name)
        if builtin is not None:
            return builtin
        self._diags.emit(D.LANG_UNIT_UNKNOWN, span, name=name)
        return None

    def _define_unit(self, node: ast.UnitDef) -> None:
        """Introduce a unit, or say where one may stand.

        A unit the language does not provide is written down before it is used,
        which is what keeps a unit mistyped in one place from quietly becoming a
        unit of its own -- the one mistake a language with no such rule cannot
        tell from a new kind of quantity.
        """
        if node.stands is not None:
            what = self._unit_written(node.stands[0])
            where = self._unit_written(node.stands[1])
            if what is not None and where is not None:
                self._stands.append((what, where))
            return
        scope = self._unit_scopes[-1]
        if node.name in scope or (not self._unit_scopes[1:]
                                  and node.name in BUILTIN_UNITS):
            self._diags.emit(D.LANG_UNIT_ALREADY_DEFINED, node.name_span,
                             name=node.name)
            return
        if node.measured is None:
            # A unit measured in nothing but itself, which is what every base
            # unit is and what a program counting apples wants.
            scope[node.name] = Unit(((node.name, 1),))
            return
        measured = self._unit_written(node.measured)
        if measured is None:
            return
        assert node.scale is not None
        over, under = node.scale
        if under == 0:
            # Nothing is so many of another divided by none of it.
            self._diags.emit(D.LANG_TYPE_DIVISION_BY_ZERO, node.span)
            return
        scope[node.name] = Unit(measured.powers,
                                measured.scale * Fraction(over, under))

    def _stands_for(self, found: Unit, wanted: Unit) -> bool:
        """Whether a value of *found* may stand where *wanted* is asked for.

        Only where the program said so, and only the way round it said it: a
        count of places may be told to stand where an index is wanted without
        an index being allowed to stand for a count.  The relation is followed
        as far as it goes, since a unit that may stand for one that may stand
        for a third may stand for the third.
        """
        if found == wanted:
            return True
        reached = {found}
        while True:
            more = {w for f, w in self._stands if f in reached} - reached
            if not more:
                return False
            if wanted in more:
                return True
            reached |= more

    def _reference_type(self, ref: ast.RefTypeRef) -> Type:
        """Resolve `&T` or `&mut T`, checking that a place can hold a `T`.

        What `mut` says is what may be done to the place, and it is part of the
        type because the one who wrote the reference and the one who reads it
        both reach that place -- which is what distinguishes it from the `mut` a
        variable or a parameter carries, that says only that the name may be
        bound to something else.
        """
        # What a reference occupies does not depend on what it names, so a
        # definition may reach itself through one.  The depth is what says so
        # where the cycle is found, which is in the middle of resolving it.
        self._behind_a_reference += 1
        try:
            pointee = self._resolve_type(ref.pointee)
        finally:
            self._behind_a_reference -= 1
        if pointee is ERROR:
            return ERROR
        if not _can_be_referred_to(pointee):
            self._diags.emit(D.LANG_REF_TYPE_NOT_ALLOWED, ref.span,
                             found=pointee.written())
            return ERROR
        return self._module.types.ptr_type(pointee, ref.mutable, ref.lasting)

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
                             found=element.written())
            element = ERROR
        if ref.value is None:
            return (self._module.types.set_type(element, ref.mutable)
                    if element is not ERROR else ERROR)
        value = self._resolve_type(ref.value)
        if element is ERROR or value is ERROR:
            return ERROR
        if value is VOID:
            self._diags.emit(D.LANG_COLLECTION_VALUE_IS_NOTHING, ref.value.span)
            return ERROR
        return self._module.types.dict_type(element, value, ref.mutable)

    def _named_type(self, ref: ast.TypeRef) -> Type:
        """Resolve a type name, reporting an unknown one.

        A name that resolves to nothing stands in as the type that matches
        anything, so that the one mistake is reported once rather than again
        wherever the type would have been checked.
        """
        found = BUILTIN_TYPES.get(ref.name) if ref.module is None else None
        if found is None and ref.module is None and _is_generic(ref.name):
            # A type a call settled.  Outside an instantiation there is nothing
            # for it to be, which is reported where the definition is written
            # rather than here.
            found = self._bound.get(ref.name)
        if found is None:
            found = self._defined_type(ref)
            # Where the program defined it, there is somewhere to send a reader:
            # this file's own definition, or the one the module it came from has.
            named = None
            if self._notes is not None:
                named = self._top.get(ref.name)
                if ref.module is not None:
                    held = self._top.get(ref.module)
                    named = held.exports.get(ref.name) \
                        if isinstance(held, LoadedModule) else None
            if found is not None and isinstance(named, _NamedType):
                self._note(ref.span, TYPE, ref.name, found.written(),
                           named.node.name_span, named.node.doc)
        elif found is not None and self._notes is not None:
            # A built-in: there is nowhere to send a reader, the specification
            # being where it is defined, but what it is, is still worth saying.
            self._note(ref.span, TYPE, ref.name, found.written())
        if found is None:
            self._diags.emit(D.LANG_TYPE_UNKNOWN, ref.span, name=ref.name)
            return ERROR
        if found is ERROR:
            return ERROR
        if ref.unit is not None:
            written = self._unit_written(ref.unit)
            if written is None:
                return ERROR
            if not isinstance(found, (IntType, FloatType)):
                self._diags.emit(D.LANG_UNIT_NOT_A_NUMBER, ref.span,
                                 found=found.written())
                return ERROR
            found = _carrying(found, written)
        if not ref.result:
            return found
        if found is VOID:
            self._diags.emit(D.LANG_TYPE_RESULT_OF_NOTHING, ref.span)
            return ERROR
        if ref.error is None:
            return self._module.types.result_type(found)
        # What the error carries is a type like any other, named the way the
        # answer is: the same lookup, and the same report where the name is not
        # one.
        carried = self._resolve_type(
            replace(ref, name=ref.error, result=False, error=None, unit=None))
        if carried is ERROR:
            return ERROR
        if carried is VOID:
            self._diags.emit(D.LANG_TYPE_RESULT_OF_NOTHING, ref.span)
            return ERROR
        return self._module.types.result_type(found, carried)

    # -- what a macro is made of -----------------------------------------------

    def _lower_quote(self, builder: IRBuilder, expr: ast.Quote,
                     expected: Type | None) -> Value:
        """Lower `⌜…⌝` in the body of a macro: the tree it holds, as a value.

        The tree is written down here and the *call* names it by where it stands in
        that list, so nothing of the syntax has to be built with instructions.  What
        the instructions do is fill the holes: one call per hole, each handing over
        what the macro worked out for it, which is what makes `⌜$a + $b⌝` a pair of
        calls rather than a variadic one.
        """
        if not self._for_macros:
            self._diags.emit(D.LANG_MACRO_QUOTE_OUTSIDE, expr.span)
            return UndefConst(ERROR)
        at = len(self.quoted)
        self.quoted.append(expr)
        found = builder.call(self._syntax_builtin(QUOTE_NAME, (U64,)),
                             [builder.int_const(U64, at)], SYNTAX, expr.span)
        holes: list[ast.Hole] = []
        _holes_of(expr, holes)
        for number, hole in enumerate(holes):
            if hole.value is None:
                self._diags.emit(D.LANG_MACRO_HOLE_OUTSIDE, hole.span)
                return UndefConst(ERROR)
            given = self._lower_expr(builder, hole.value, None)
            ty = self._value_type_of(given)
            if ty is ERROR:
                return UndefConst(ERROR)
            if ty is SYNTAX:
                name, wanted = FILL_NAME, SYNTAX
            elif isinstance(ty, IntType):
                # A number goes in as what a program would have written to mean it,
                # which is what lets a macro work something out and write the answer.
                name, wanted = FILL_NUMBER_NAME, I64
                given = self._as_a_number(builder, given, ty, hole.span)
            elif ty is STR:
                # And text as the literal a program would have written, which is
                # what a macro taking a template apart puts back.
                name, wanted = FILL_TEXT_NAME, STR
            else:
                self._diags.emit(D.LANG_MACRO_HOLE_NOT_A_PIECE, hole.span,
                                 found=ty.written())
                return UndefConst(ERROR)
            found = builder.call(
                self._syntax_builtin(name, (SYNTAX, U64, wanted)),
                [found, builder.int_const(U64, number), given], SYNTAX, expr.span)
        if not self._accepts(expected, SYNTAX):
            self._report_mismatch(expr.span, SYNTAX, expected)
            return UndefConst(ERROR)
        return found

    def _as_a_number(self, builder: IRBuilder, value: Value, ty: IntType,
                     span: Span) -> Value:
        """A number of any width as the one the tree builder takes."""
        if ty is I64:
            return value
        return builder.cast(CastKind.SEXT if ty.signed else CastKind.ZEXT,
                            value, I64, span)

    def _not_a_piece(self, ty: Type, span: Span, name: str) -> None:
        """Refuse a piece of the program where a value has to outlive the compilation.

        A macro and a function it may call hold one; a function the program calls
        does not, there being nothing for a parse tree to be once the program is the
        program.  Asked of what a signature says rather than of what a body does,
        because a signature is what a caller reads.
        """
        if self._for_macros or not _holds_a_piece(ty):
            return
        self._diags.emit(D.LANG_SYNTAX_AT_RUN_TIME, span, name=name)

    def _lower_asked(self, builder: IRBuilder, expr: ast.Call, name: str,
                     expected: Type | None) -> Value:
        """Lower a question about a piece of the program.

        They are the whole of what is available for looking at one, and they are the
        same mechanism a macro writes with seen from the other end: `\N{APL FUNCTIONAL SYMBOL QUAD}head` and
        `\N{APL FUNCTIONAL SYMBOL QUAD}part` take an expression apart and `\N{APL FUNCTIONAL SYMBOL QUAD}apply` -- when there is one -- puts it back.
        """
        if not self._for_macros:
            self._diags.emit(D.LANG_ASK_OUTSIDE_A_MACRO, expr.span, name=name)
            return UndefConst(ERROR)
        wanted = _ASKS_ABOUT_A_PIECE[name]
        if len(expr.args) != len(wanted):
            self._diags.emit(D.LANG_ASK_ARGUMENT_COUNT, expr.span, name=name,
                             expected=str(len(wanted)), found=str(len(expr.args)))
            return UndefConst(ERROR)
        given: list[Value] = []
        for one, ty in zip(expr.args, wanted):
            value = self._lower_into(builder, one, ty, one.span)
            if self._value_type_of(value) is ERROR:
                return UndefConst(ERROR)
            given.append(value)
        answer = _SYNTAX_ANSWERS[name]
        found = builder.call(self._syntax_builtin(name, wanted), given, answer,
                             expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return found

    def _pieces_alike(self, builder: IRBuilder, expr: ast.Binary,
                      left: Value, right: Value) -> Value:
        """Lower `a = b` and `a ≠ b` over two pieces of the program.

        Two pieces are alike where the same thing is written in both, wherever each
        was written -- so the positions are not compared, which is what makes a piece
        the caller wrote match one the macro did.
        """
        found = builder.call(
            self._syntax_builtin(ALIKE_PIECES_NAME, (SYNTAX, SYNTAX)),
            [left, right], BOOL, expr.span)
        if expr.op is ast.BinaryOp.NOT_EQUAL:
            return builder.binary(BinOp.XOR, found,
                                  builder.bool_const(True), BOOL, expr.span)
        return found

    def _syntax_builtin(self, name: str, params: tuple[Type, ...]) -> Function:
        """The compiler-provided function of that name, made on first ask.

        Bodyless, and the machine that runs a macro is what gives each one a
        meaning: they are the questions about a piece of the program and the two
        that build one, and none of them is anything a program could write.
        """
        found = self._module.functions.get(name)
        if found is None:
            found = self._module.add_function(Function(
                name, self._module.types.func_type(params, _SYNTAX_ANSWERS[name]),
                FuncAttrs(), linkage=Linkage.INTERNAL))
        return found

    # -- operators a program defines -------------------------------------------

    def _an_operator(self, node: ast.FuncDef, what: object) -> None:
        """Write a definition down as an operator, where its name is one.

        A function whose name is a glyph rather than a word is what a program
        writes to say what an operator means for its own types.  It is registered
        by the glyph and by how many operands it takes, one being the prefix
        reading and two the infix one -- which is what tells `fn \N{GRAVE ACCENT}\N{LEFT CEILING}\N{GRAVE ACCENT}(b: Bag)`, the
        largest of what a `Bag` holds, from `fn \N{GRAVE ACCENT}\N{LEFT CEILING}\N{GRAVE ACCENT}(a: C, b: C)`, the larger of two.
        """
        if not is_operator_name(node.name):
            return
        # A pair is written around what it is applied to, so its first parameter
        # is that and the rest are whatever stands inside -- however many, which
        # is what makes `t⟦i, j⟧` sayable.  A glyph is written before one operand
        # or between two and has nowhere to put a third.
        paired = len(node.name) == 2
        enough = len(node.params) >= 2 if paired \
            else len(node.params) in (1, 2)
        if not enough:
            self._diags.emit(D.LANG_OPERATOR_ARITY, node.name_span,
                             glyph=node.name, count=str(len(node.params)),
                             wanted="two or more" if paired
                             else "one operand or two")
            return
        self._operators[(node.name, len(node.params))] = what

    def _paired(self, builder: IRBuilder, base: ast.Expr, pair: str,
                inside: Sequence[ast.Expr], span: Span,
                expected: Type | None) -> Value | None:
        """Lower a bracket pair a program defined, where this is one of those.

        The brackets the language has are operators like any other, so a program
        may say what they mean for a type of its own -- and where the type is one
        of the language's, the language's meaning is what applies and this answers
        nothing.  Asked before the base is lowered, so the operands go to the
        definition's parameters.
        """
        if not self._operators or not _of_its_own(self._hint_of(base)):
            return None
        return self._operator_call(builder, pair, (base, *inside), span,
                                   expected)

    def _operator_stands(self, expr: ast.Binary, context: Type | None) -> bool:
        """Whether the language's own meaning of this operator covers the operands.

        Asked of a *hint* and not of a lowered value, because the answer decides
        which way the operands are lowered: a program's operator wants them lowered
        into its parameters, and the language's wants them lowered into each
        other's types.  Where nothing can be read off the syntax the answer is yes,
        which leaves a program that wrote none exactly as it was.
        """
        found = self._hint_of(expr.left) or self._hint_of(expr.right) or context
        if found is None or found is ERROR:
            return True
        return self._operand_type_stands(expr.op, found)

    def _operator_stands_alone(self, expr: ast.Unary) -> bool:
        """The same question of an operator written before one operand.

        The operators written before one operand are each lowered by a method of
        its own, so there is no one place that says which types the language takes
        -- and the question this has to answer is narrower than that anyway.  None
        of them works on a type a program defined, so a hint naming one is the
        whole of what says the language has nothing to say here.
        """
        found = self._hint_of(expr.operand)
        return not _of_its_own(found)

    def _answers_written(self, expr: ast.Unary | ast.Binary) -> Type | None:
        """What a program's own operator answers, where this expression is one.

        Nothing where the language's own meaning covers the operands, where no
        definition exists, and where the definition is generic -- what a generic
        one answers is what an instantiation settles, and a hint is read off the
        syntax or not at all.

        The glyph is looked up before anything else is asked, because everything
        else is a walk: whether the language's meaning covers the operands is a
        question about their hints, and hinting a hint is how a deep expression
        turns into a deep walk.  A program that defined no operator of this glyph
        stops here.
        """
        if self._operator_written(
                expr.op.value, 1 if isinstance(expr, ast.Unary) else 2) is None:
            return None
        if isinstance(expr, ast.Unary):
            glyph, ours = expr.op.value, self._operator_stands_alone(expr)
        else:
            glyph, ours = expr.op.value, self._operator_stands(expr, None)
        if ours:
            return None
        found = self._operator_written(glyph, 1 if isinstance(expr, ast.Unary)
                                       else 2)
        if not isinstance(found, Function):
            return None
        return None if found.ty.ret is VOID else found.ty.ret

    def _lower_fresh(self, builder: IRBuilder, expr: ast.Fresh,
                     expected: Type | None) -> Value:
        """Lower an operator the language gives no meaning to.

        There is nothing to fall back on: the glyph is a symbol and the language
        has never heard of it, so what it means is the definition or it is a
        mistake.  That makes this the one operator path with no native half.
        """
        found = self._operator_call(builder, expr.glyph, expr.operands,
                                    expr.span, expected)
        if found is not None:
            return found
        self._diags.emit(D.LANG_OPERATOR_NOT_DEFINED, expr.span,
                         glyph=expr.glyph,
                         found="one operand" if len(expr.operands) == 1
                         else "".join((str(len(expr.operands)), " operands")))
        return UndefConst(ERROR)

    def _operator_written(self, glyph: str, arity: int) -> object | None:
        """What a program said an operator of this arity means, if it said."""
        return self._operators.get((glyph, arity))

    def _operator_call(self, builder: IRBuilder, glyph: str,
                       operands: Sequence[ast.Expr], span: Span,
                       expected: Type | None) -> Value | None:
        """Lower an operator a program defined, or nothing where it defined none.

        The operands are lowered by the call rather than beforehand: which
        parameter each goes to is then known while it is lowered, which is what an
        unsuffixed literal beside a program's own type needs, and it is the one
        thing that would be lost by handing over values already worked out.
        """
        found = self._operator_written(glyph, len(operands))
        if found is None:
            return None
        call = ast.Call(span=span,
                        callee=ast.NameRef(span=span, name=glyph),
                        args=tuple(operands))
        if isinstance(found, _Generic):
            return self._lower_generic(builder, call, found, expected)
        assert isinstance(found, Function)
        return self._through(builder, call, found, expected)

    # -- what a signature requires and demands ---------------------------------

    def _collect_bundle(self, node: ast.BundleDef, path: str) -> None:
        """Write down a bundle, whose lines are checked where it is applied.

        Nothing of a bundle is checked here.  Its lines are written over its own
        type parameters, and what those stand for is what an application says --
        so a bundle nobody applies is a bundle nobody has asked a question of,
        which is the same rule a generic function follows.
        """
        for name, where in zip(node.params, node.param_spans):
            if not _is_generic(name):
                self._diags.emit(D.LANG_BUNDLE_PARAMETER_NOT_A_TYPE, where,
                                 name=name, bundle=node.name)
        if node.name in self._bundles or node.name in self._top:
            self._diags.emit(D.LANG_NAME_ALREADY_DEFINED, node.name_span,
                             name=node.name)
            return
        bound = self._bind_attributes(node.attrs, AttrTarget.BUNDLE)
        self._check_clause_shapes(node.clauses)
        for clause in node.clauses:
            if self._levels_of(clause.expr) == {False}:
                # A bundle's parameters are types and it can name no value, so a
                # line over values has nothing to be about.  What abbreviates a
                # condition is a function answering `bool`, which the language
                # already has.
                self._diags.emit(D.LANG_BUNDLE_LINE_OVER_VALUES, clause.span,
                                 bundle=node.name)
        self._bundles[node.name] = _Bundle(
            node=node, path=path, scope=self._top, siblings=self._bundles,
            exported=self._is_export(bound))

    def _as_written(self, clause: ast.Clause) -> str:
        """A requirement as the source has it, for a message about it.

        The source rather than a rendering of the tree: a reader is being told
        which requirement failed, and the words they wrote are what they will
        look for.
        """
        found = self._sources.snippet(clause.expr.span.to(
            clause.answers.span if clause.answers is not None
            else clause.expr.span))
        return found if found else "the requirement"

    def _check_bundle_cycles(self) -> None:
        """Refuse a bundle that reaches itself, once every bundle is known.

        Here rather than where one is applied, because a cycle is a thing about
        the bundles and not about any call: a program that wrote one and never
        applied it has still written something with no meaning.
        """
        told: set[str] = set()
        for name, bundle in self._bundles.items():
            if name in told:
                continue
            reached: set[str] = set()
            if self._reaches(bundle, name, reached):
                # Every bundle of the cycle is in one, and saying so of each
                # would be saying one thing several times: the first names it.
                told |= reached | {name}
                self._diags.emit(D.LANG_BUNDLE_CYCLE, bundle.node.name_span,
                                 bundle=name)

    def _reaches(self, bundle: _Bundle, wanted: str, seen: set[str]) -> bool:
        """Whether *bundle* applies *wanted*, however many bundles apart.

        Each bundle's lines are read with its own file's names in force, for the
        same reason an application is: what a line names is what the file that
        wrote it could see.
        """
        kept = (self._top, self._bundles)
        if bundle.scope is not None and bundle.siblings is not None:
            self._top, self._bundles = bundle.scope, bundle.siblings
        try:
            return self._reaching(bundle, wanted, seen)
        finally:
            self._top, self._bundles = kept

    def _reaching(self, bundle: _Bundle, wanted: str, seen: set[str]) -> bool:
        """The walk itself, with the bundle's own names already in force."""
        for clause in bundle.node.clauses:
            found = self._bundle_named(clause.expr)
            if found is None:
                continue
            if found.node.name == wanted:
                return True
            if found.node.name in seen:
                continue
            seen.add(found.node.name)
            if self._reaches(found, wanted, seen):
                return True
        return False

    def _bundle_named(self, expr: ast.Expr) -> _Bundle | None:
        """The bundle an expression applies, where it applies one.

        Its own file's, named outright, or another file's, named through the name
        that file was imported under -- which is how every other thing a module
        holds is reached, and is why a bundle needs no notation of its own for it.
        """
        if not isinstance(expr, ast.Call):
            return None
        called = expr.callee
        if isinstance(called, ast.NameRef):
            return self._bundles.get(called.name)
        if isinstance(called, ast.Member) \
                and isinstance(called.base, ast.NameRef):
            held = self._top.get(called.base.name)
            if isinstance(held, LoadedModule):
                found = held.bundles.get(called.name)
                return found if isinstance(found, _Bundle) else None
        return None

    def _over_types(self, expr: ast.Expr) -> bool:
        """Whether the operands of *expr* are types, which is what decides
        whether a clause holding it is a requirement or a condition.

        A type parameter is one by its apostrophe, and a lift is one wherever it
        does not stand in one of the three places that read a lift for what the
        type *is*.  Anything else is a value, and a clause is one level or the
        other throughout.
        """
        return True in self._levels_of(expr)

    def _levels_of(self, expr: ast.Expr) -> set[bool]:
        """Which levels the operands of *expr* are on: types, values, or both."""
        found: set[bool] = set()
        self._levels_in(expr, found)
        return found

    def _levels_in(self, expr: object, into: set[bool]) -> None:
        """Whether a type stands anywhere below *expr*, and whether a value does.

        The three places that read a lift for what the type *is* answer values --
        `⌈⌜u8⌝` is 255 -- so they stop the walk with a value recorded, which is
        what keeps `⌈⌜T'⌝` and `⌈T'` two different things.  A call's callee is
        not an operand and is not walked: what a name is of is not what the clause
        is over.
        """
        match expr:
            case ast.NameRef() if _is_generic(expr.name):
                into.add(True)
                return
            case ast.NameRef():
                into.add(False)
                return
            case ast.Unary() if expr.op in (ast.UnaryOp.MAX, ast.UnaryOp.MIN) \
                    and isinstance(expr.operand, ast.Lifted):
                into.add(False)
                return
            case ast.Call() if isinstance(expr.callee, ast.NameRef) \
                    and expr.callee.name == TYPEOF_NAME:
                into.add(False)
                return
            case ast.Binary() if self._type_stood_for(expr.left) is not None \
                    or self._type_stood_for(expr.right) is not None:
                into.add(False)
                return
            case ast.Lifted():
                into.add(True)
                return
            case ast.Call() if self._bundle_named(expr) is not None:
                into.add(True)
                for one in expr.args:
                    self._levels_in(one, into)
                return
            case ast.Call():
                for one in expr.args:
                    self._levels_in(one, into)
                return
        if isinstance(expr, ast.Node):
            for one in fields_of(expr):
                self._levels_in(getattr(expr, one.name), into)
        elif isinstance(expr, (list, tuple)):
            for one in expr:
                self._levels_in(one, into)

    def _reading_of(self, clause: ast.Clause) -> bool | None:
        """Whether a clause is a requirement, a condition, or neither.

        True for a requirement and False for a condition; nothing where the
        clause has no reading at all, which is what the refusals below report and
        what keeps an ill-shaped clause from also being lowered as if it were
        well-shaped.
        """
        levels = self._levels_of(clause.expr)
        if len(levels) > 1:
            return None
        written: list[str] = []
        _named_in(clause.expr, written)
        if ANSWER_NAME in written and clause.kind is not ast.ClauseKind.POST:
            return None
        if True in levels:
            return None if clause.kind is not ast.ClauseKind.PRE else True
        return None if clause.answers is not None else False

    def _check_clause_shapes(self, clauses: Sequence[ast.Clause]) -> bool:
        """Whether every clause is of a shape that has a reading.

        Asked where the definition is read and not where it is called, because
        none of it depends on what the types turn out to be: a clause is over
        values or over types, and which it is decides what the arrow may say and
        whether a `post` is one at all.  A generic nobody calls is still refused
        for these, which is the one thing about a generic's clauses that does not
        wait for a call.
        """
        if not clauses or id(clauses) in self._shaped:
            return True
        self._shaped.add(id(clauses))
        good = True
        for clause in clauses:
            if self._reading_of(clause) is not None:
                continue
            good = False
            levels = self._levels_of(clause.expr)
            written: list[str] = []
            _named_in(clause.expr, written)
            if len(levels) > 1:
                self._diags.emit(D.LANG_CLAUSE_MIXES_LEVELS, clause.span)
            elif ANSWER_NAME in written \
                    and clause.kind is not ast.ClauseKind.POST:
                self._diags.emit(D.LANG_CLAUSE_ANSWER_OUTSIDE_POST, clause.span)
            elif True in levels:
                self._diags.emit(D.LANG_CLAUSE_POST_OVER_TYPES, clause.span)
            else:
                # The arrow says what an unevaluated expression would answer.  A
                # call that happens answers what it answers, and the parameter
                # list is where a type is read off a value.
                self._diags.emit(D.LANG_CLAUSE_ARROW_OVER_VALUES, clause.span)
        return good

    def _check_clauses(self, clauses: Sequence[ast.Clause],
                       bound: dict[str, Type], name: str, span: Span) -> bool:
        """Check every requirement of a signature, settling what it settles.

        The clauses are read in the order written, which is what makes a
        requirement able to settle a type parameter a later one uses.  A
        condition is not checked here -- it is code, and it is lowered into the
        body -- so what this walks is the type-level half.
        """
        for clause in clauses:
            if self._reading_of(clause) is not True:
                continue
            if not self._met(clause, bound, name, span):
                return False
        return True

    def _met(self, clause: ast.Clause, bound: dict[str, Type], name: str,
             span: Span) -> bool:
        """Whether one requirement is met, settling the type its arrow names."""
        found = self._answered_by(clause, bound, name, span)
        if found is None:
            return False
        if clause.answers is None:
            return True
        return self._settling(clause, found, bound, name)

    def _answered_by(self, clause: ast.Clause, bound: dict[str, Type],
                     name: str, span: Span) -> Type | None:
        """What a requirement's expression answers, or nothing where it cannot
        be written at all.

        A bundle applied is expanded here rather than lowered: it answers
        nothing, so there is nothing for an arrow to name and nothing for an
        operand position to hold, and what it means is every line it holds with
        the arguments put in.
        """
        bundle = self._bundle_named(clause.expr)
        if bundle is not None:
            if clause.answers is not None:
                self._diags.emit(D.LANG_CLAUSE_BUNDLE_ANSWERS_NOTHING,
                                 clause.span, bundle=bundle.node.name)
                return None
            assert isinstance(clause.expr, ast.Call)
            return VOID if self._bundle_met(bundle, clause.expr, bound, name,
                                            span) else None
        dummies = self._dummies_for(clause, bound, name)
        if dummies is None:
            return None
        written, values = dummies
        found = self._written_over(values, written)
        if found is None:
            self._diags.emit(D.LANG_CLAUSE_NOT_MET, clause.span, function=name,
                             written=self._as_written(clause),
                             types=", ".join(
                                 "".join((one, " = ", bound[one].written()))
                                 for one in sorted(bound)))
            return None
        return found

    def _bundle_met(self, bundle: _Bundle, expr: ast.Call,
                    bound: dict[str, Type], name: str, span: Span) -> bool:
        """Whether every line of an applied bundle is met.

        The arguments are types, and they stand for the bundle's own parameters
        while its lines are checked -- so a bundle is substitution and nothing
        more, which is why nothing conforms to one and there is no coherence rule
        to write.
        """
        written = bundle.node
        if isinstance(expr.callee, ast.Member) and not bundle.exported:
            self._diags.emit(D.LANG_IMPORT_NOT_EXPORTED, expr.callee.name_span,
                             name=written.name,
                             module=expr.callee.base.name
                             if isinstance(expr.callee.base, ast.NameRef) else "")
            return False
        if len(expr.args) != len(written.params):
            self._diags.emit(D.LANG_BUNDLE_WRONG_ARGUMENT_COUNT, expr.span,
                             bundle=written.name, expected=len(written.params),
                             found=len(expr.args))
            return False
        if bundle.opening:
            self._diags.emit(D.LANG_BUNDLE_CYCLE, expr.span, bundle=written.name)
            return False
        inner: dict[str, Type] = {}
        for at, (arg, param) in enumerate(zip(expr.args, written.params)):
            given = self._type_operand(arg, bound)
            if given is None:
                self._diags.emit(D.LANG_BUNDLE_ARGUMENT_NOT_A_TYPE, arg.span,
                                 bundle=written.name, at=at + 1)
                return False
            inner[param] = given
        bundle.opening = True
        # Where the bundle was written, for as long as its lines are being read.
        # A line naming `next` asks for the `next` its own file can see: a
        # requirement is part of what that file said, and what the file applying
        # it happens to have is nobody's business but its own.
        kept = (self._top, self._bundles)
        if bundle.scope is not None and bundle.siblings is not None:
            self._top, self._bundles = bundle.scope, bundle.siblings
        try:
            for one in written.clauses:
                if not self._met(one, inner, written.name, span):
                    self._diags.emit(D.LANG_BUNDLE_ASKED_HERE, expr.span,
                                     bundle=written.name, name=name)
                    return False
        finally:
            self._top, self._bundles = kept
            bundle.opening = False
        return True

    def _type_operand(self, expr: ast.Expr,
                      bound: dict[str, Type]) -> Type | None:
        """The type an operand stands for, or nothing where it stands for a value.

        Two things stand for a type: a type parameter, which the apostrophe
        marks, and a type between the lifting marks, which is what the marks are
        for everywhere else.
        """
        match expr:
            case ast.NameRef() if _is_generic(expr.name):
                return bound.get(expr.name)
            case ast.Lifted():
                return self._lifted_type(expr)
            case _:
                return None

    def _dummies_for(self, clause: ast.Clause, bound: dict[str, Type],
                     name: str) -> tuple[ast.Expr, list[tuple[str, Type]]] | None:
        """The requirement rewritten so that every type operand is a name, and
        what type each of those names stands for.

        A requirement asks whether its expression can be written over *some*
        value of each type, so that is what it is checked with: one value per
        type operand, bound to the name that stood there.  A type parameter keeps
        its own name, which is what makes `T' + T'` two reads of one value; a
        lifted type gets a name no program could write.
        """
        values: dict[str, Type] = {}
        made = [0]

        def walked(node: object) -> object:
            found = self._type_operand(node, bound) \
                if isinstance(node, ast.Expr) else None
            if found is not None:
                assert isinstance(node, ast.Expr)
                if isinstance(node, ast.NameRef):
                    values[node.name] = found
                    return node
                made[0] += 1
                called = "".join((DUMMY_PREFIX, str(made[0])))
                values[called] = found
                return ast.NameRef(span=node.span, name=called)
            if isinstance(node, ast.Node):
                return replace(node, **{one.name: walked(getattr(node, one.name))
                                        for one in fields_of(node)})
            if isinstance(node, tuple):
                return tuple(walked(one) for one in node)
            return node

        written_over: list[str] = []
        _type_names_in(clause.expr, written_over)
        for one in written_over:
            if one not in bound:
                self._diags.emit(D.LANG_CLAUSE_NOT_SETTLED_YET, clause.span,
                                 name=one, function=name)
                return None
        rewritten = walked(clause.expr)
        assert isinstance(rewritten, ast.Expr)
        return rewritten, list(values.items())

    def _written_over(self, dummies: Sequence[tuple[str, Type]],
                      expr: ast.Expr) -> Type | None:
        """What *expr* answers where each name stands for a value of its type,
        and nothing where it cannot be written.

        The expression is checked the way any other is, with a function of its
        own that nothing will emit and with what it reports thrown away: a
        requirement asks exactly the question the checker already answers, so
        asking it this way is what keeps one set of rules for what may be
        written.
        """
        func = Function(name="", ty=self._module.types.func_type(
            tuple(ty for _, ty in dummies), VOID))
        block = func.add_block()
        builder = IRBuilder(self._module, func)
        told: list[object] = []
        kept = (self._diags, self._impure, self._answering, self._borrows,
                self._places, self._addressed)
        self._diags = DiagEngine(told.append, kept[0].control, kept[0].catalog)
        self._impure, self._answering = True, VOID
        self._borrows, self._places, self._addressed = [], {}, set()
        self._push_scope()
        try:
            for name, ty in dummies:
                self._bind_local(name, block.add_param(ty, name), INVALID_SPAN,
                                 False, is_parameter=True, builder=builder)
            found = self._lower_expr(builder, expr, None)
            answer = self._value_type_of(found)
            self._pop_scope()
        finally:
            (self._diags, self._impure, self._answering, self._borrows,
             self._places, self._addressed) = kept
        return None if answer is ERROR or told else answer

    def _settling(self, clause: ast.Clause, found: Type,
                  bound: dict[str, Type], name: str) -> bool:
        """Bind what a requirement's arrow names, or check it against what is
        already bound.

        A name nothing has settled is settled by this; one already settled is
        compared, and a requirement that answers something else is not met.
        """
        assert clause.answers is not None
        written = clause.answers
        if isinstance(written, ast.TypeRef) and written.module is None \
                and not written.result and _is_generic(written.name) \
                and written.name not in bound:
            bound[written.name] = found
            return True
        outer, self._bound = self._bound, bound
        try:
            wanted = self._resolve_type(written)
        finally:
            self._bound = outer
        if wanted is ERROR:
            return False
        if wanted is not found:
            self._diags.emit(D.LANG_CLAUSE_ANSWERS_OTHERWISE, clause.span,
                             function=name, expected=wanted.written(),
                             found=found.written())
            return False
        return True

    # -- the conditions a body carries -----------------------------------------

    def _lower_conditions(self, builder: IRBuilder, node: ast.FuncDef,
                          func: Function) -> None:
        """Emit the checks a function's `pre` clauses stand for.

        At the top of the body and once: a check in the callee that fails already
        names the function and, through the stack walk, who called it -- so the
        thing a check at every call site would buy is had for nothing, and there
        is one copy of the code.
        """
        for clause in node.clauses:
            if clause.kind is not ast.ClauseKind.PRE \
                    or self._reading_of(clause) is not False:
                continue
            self._demanded(builder, clause, func,
                           "a pre-condition that does not hold",
                           statuses.PRE_CONDITION)

    def _lower_at_entry(self, builder: IRBuilder, expr: ast.Call,
                        expected: Type | None) -> Value:
        """Lower `⎕entry(NAME)`: what a parameter held when the function began.

        A post-condition about a parameter the body has since bound to something
        else needs the value the call brought in, and that value is already
        there: a parameter arrives in the entry block and a name bound to
        something else does not disturb it.  So nothing is copied and nothing is
        stored -- what this costs is keeping the value alive to the return, which
        the register allocator prices like any other live range.  That is the
        whole difference between this and an `old` over an arbitrary expression,
        which would have to be worked out at entry and its answer carried.
        """
        if not self._after:
            self._diags.emit(D.LANG_ENTRY_OUTSIDE_POST, expr.span)
            return UndefConst(ERROR)
        if len(expr.args) != 1 or not isinstance(expr.args[0], ast.NameRef):
            self._diags.emit(D.LANG_ENTRY_TAKES_A_PARAMETER, expr.span)
            return UndefConst(ERROR)
        written = expr.args[0]
        assert isinstance(written, ast.NameRef)
        found = self._at_entry.get(written.name)
        if found is None:
            node, _ = self._demanding
            self._diags.emit(D.LANG_ENTRY_NOT_A_PARAMETER, written.span,
                             name=written.name,
                             function=node.name if node is not None else "")
            return UndefConst(ERROR)
        answer = self._value_type_of(found)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return found

    # -- what an arena holds ---------------------------------------------------

    def _is_arena(self, local: _Local) -> bool:
        """Whether a name is bound to an arena or to a reference to one."""
        held = self._held_by(local)
        found = held is ARENA or (isinstance(held, PtrType)
                                  and held.pointee is ARENA)
        if found:
            self._arena_by_id[id(local)] = local
        return found

    def _arenas_in(self, node: object) -> frozenset[int]:
        """The arenas what an expression comes to may point into.

        Read off what is written rather than off what it was lowered to: an arena is
        named where room is taken from it -- after `in`, or handed to a call -- and a
        name carries the arenas what it holds was made in.  A call is taken to answer
        something made in any arena it was handed, which is the only thing a caller
        can know without seeing into the function; an answer that came from
        somewhere that lasts is a cautious guess and never a wrong one.
        """
        found: set[int] = set()
        self._collect_arenas(node, found)
        return frozenset(found)

    def _collect_arenas(self, node: object, into: set[int]) -> None:
        """The walk `_arenas_in` makes."""
        exact = self._made_from.get(id(node))
        if exact is not None and exact[0] is node:
            into.update(exact[1])
            return
        alias = self._made_alias.get(id(node))
        if alias is not None and alias[0] is node:
            self._collect_arenas(alias[1], into)
            return
        match node:
            case ast.Allocated():
                # The arena named, and what the operands were made in: the room
                # the expression itself takes is the arena's and not `⎕heap`'s.
                self._collect_arenas(node.arena, into)
                exact = self._made_from.get(id(node.value))
                if exact is not None and exact[0] is node.value:
                    into.update(exact[1])
                    return
                if isinstance(node.value, ast.Node):
                    for one in fields_of(node.value):
                        held = getattr(node.value, one.name)
                        for each in (held if isinstance(held, tuple) else (held,)):
                            if isinstance(each, ast.Node):
                                self._collect_arenas(each, into)
                return
            case ast.Binary() if node.op is ast.BinaryOp.CONCAT:
                # Not lowered yet -- a body read ahead of time -- so what it will be
                # made in is what one naming no arena is made in.
                into.add(_HEAP)
            case ast.ListLit() | ast.SetLit() | ast.DictLit():
                into.add(_HEAP)
            case ast.Element():
                # An element is read out of a container: it was made where the
                # container's elements were, and it is the container's -- so it
                # carries a key saying which, for the moment one is replaced.
                # Where the container's elements were made, without the keys that
                # name the container -- an element is not the container -- and, if
                # any may be the heap's, a key saying it was read out of this one.
                inner: set[int] = set()
                self._collect_arenas(node.base, inner)
                keys = {one for one in inner if _is_container_key(one)}
                into.update(inner - keys)
                if _HEAP in inner:
                    into.update(_element_key(one) for one in keys)
                return
            case ast.NameRef():
                local = self._find_local(node.name)
                if local is None:
                    if node.name == HEAP_NAME:
                        into.add(_HEAP)
                    return
                if self._is_arena(local):
                    into.add(id(local))
                elif local.gone_at is None:
                    # A name already dead is refused where it is read (4615), and
                    # that is the one thing to say about it: where it goes after
                    # is the same mistake again.
                    into.update(local.arenas)
                return
            case ast.Lambda():
                # Lowered, it said what it reaches (`_lower_lambda`).  Read ahead
                # of that, it is taken to keep what it brings in in `⎕heap` and
                # to reach what the names it brings in were made in; what its
                # body takes room from is its business when it runs.
                into.add(_HEAP)
                names = [one.name for one in node.captures]
                if node.brings_in is not None:
                    _named_in(node.body, names)
                for one in names:
                    local = self._find_local(one)
                    if local is not None and not self._is_arena(local) \
                            and local.gone_at is None:
                        into.update(local.arenas)
                return
        if isinstance(node, ast.Node):
            for one in fields_of(node):
                held = getattr(node, one.name)
                if isinstance(held, ast.Node):
                    self._collect_arenas(held, into)
                elif isinstance(held, tuple):
                    for each in held:
                        if isinstance(each, ast.Node):
                            self._collect_arenas(each, into)

    def _made_in(self, name: str, value: ast.Expr | None, span: Span,
                 assigning: bool = False) -> None:
        """Write down where what a name now holds was made, and check it fits.

        A name bound further out than an arena outlives it, so a value made in the
        arena may not be put there (4614) -- the rule a reference follows, asked of
        what an arena holds.  A name given something new is alive again whatever its
        old value pointed at.
        """
        local = self._find_local(name)
        if local is None or value is None:
            return
        if not _points_somewhere(self._held_by(local)):
            local.arenas = frozenset()
            return
        made = self._arenas_in(value)
        held = self._held_by(local)
        if _is_owning(held):
            # A container whose elements point somewhere: which allocator they
            # are kept in, and -- for `⎕heap`'s, which gives back what is
            # replaced -- a key naming the container, unless it is another
            # name for one that already has one.
            local.holds_in = self._holder_of(value)
            if local.holds_in == _HEAP \
                    and not any(_is_container_key(one) for one in made):
                made = made | {_container_key(local)}
        local.arenas = made
        local.gone_at = None
        self._report_allocator(local, made, span)
        if not assigning:
            return
        for one in made:
            arena = self._arena_by_id.get(one)
            if arena is not None and arena.depth > local.depth:
                self._outlives_arena(name, arena, span)
                # Said once: what the name holds is not also reported as gone
                # every time it is read after the arena is.
                local.arenas = frozenset()
                return

    def _outlives_arena(self, name: str, arena: _Local, span: Span) -> None:
        """Report something given to *name* that was made in *arena*, or reaches
        what stands for one, while *name* outlives it (4614, 4570).

        A lambda reaching this call's frame, or a variable it brought in by
        reference, reaches a place: that is the reference's rule and is said in
        its words.
        """
        if id(arena) in self._stand_ins:
            self._diags.emit(D.LANG_REF_OUTLIVES_PLACE, span, name=name,
                             place=arena.name) \
                .note(D.LANG_VARDEF_DEFINED_HERE, arena.span, name=arena.name)
            return
        self._diags.emit(D.LANG_ARENA_VALUE_OUTLIVES, span, name=name,
                         arena=arena.name)

    def _holder_of(self, value: ast.Expr) -> int | None:
        """The allocator the elements of the container *value* comes to are kept in.

        What `in` named where the container is written, `⎕heap` where nothing did;
        the container's own for another name of it; and for a call's answer, what
        the signature says.
        """
        match value:
            case ast.Allocated():
                return self._owner_of(self._arenas_in(value.arena))
            case ast.ArrayLit() | ast.ListLit():
                return _HEAP
            case ast.Binary() if value.op is ast.BinaryOp.CONCAT:
                return _HEAP
            case ast.NameRef():
                local = self._find_local(value.name)
                return local.holds_in if local is not None else None
            case ast.Call():
                return self._holds_from.get(id(value))
            case ast.Member():
                # A container reached through a field says nothing of where its
                # elements are, and saying nothing is `⎕heap`.
                return _HEAP
        return None

    def _report_allocator(self, local: _Local, made: frozenset[int],
                          span: Span) -> None:
        """Say in the report log how a name's allocator is known, which is the
        compiler's choice of whether the value has to carry it.

        Every value of a type that points somewhere carries its allocator, and the
        compiler reads it only where it has to: where the allocator is known while
        compiling, the word is never read and goes with the rest of what nothing
        reads.
        """
        func = self._demanding[1]
        if func is None or not owned.points(self._held_by(local)):
            return
        if local.holds_in is not None and isinstance(self._held_by(local),
                                                     ArrayType):
            # An array is room in the frame and carries no allocator of its own;
            # what it says is where its elements are kept.
            pool = local.holds_in != _HEAP
            self._module.reports.record(
                ReportKind.ALLOCATOR, func.name,
                "".join(("'", local.name, "' keeps its elements in ",
                         self._owner_written(local.holds_in),
                         ", a pool: nothing is given back when one is replaced"
                         if pool else
                         ", which gives back what is replaced: each element "
                         "carries the allocator it came from and is asked")),
                local.span if local.span.is_valid else span)
            return
        allocators = sorted({one for one in made
                             if one == _HEAP or (one in self._arena_by_id
                                                 and one not in self._stand_ins)})
        unknown = any(one in self._unknown_by_id or _is_element_key(one)
                      for one in made)
        if not made:
            said = "is in the image, which nothing gives back: its allocator is none"
        elif len(allocators) == 1 and not unknown:
            said = "".join(("is made in ", self._owner_written(allocators[0]),
                            ", known while compiling: the compiler holds the "
                            "allocator, and the value need not carry it"))
        else:
            where = [self._owner_written(one) for one in allocators]
            if any(one in self._unknown_by_id for one in made):
                where.append("whatever the caller handed over")
            if any(_is_element_key(one) for one in made):
                where.append("a container it was read out of")
            said = "".join(("carries its allocator, which is asked of it where it "
                            "is given back: it may be made in ",
                            " or ".join(where)))
        self._module.reports.record(
            ReportKind.ALLOCATOR, func.name,
            "".join(("'", local.name, "' ", said)),
            local.span if local.span.is_valid else span)

    def _leaves_no_arena_behind(self, value: ast.Expr, opened_at: int,
                                func: Function | None, span: Span,
                                ty: Type | None = None) -> None:
        """Check that what a block comes to was not made in an arena it ends (4614).

        An arena made in the block is given back on the way out.  What a function
        answers is not asked here: it is copied into the allocator the signature
        names (`_answered`).
        """
        if ty is not None and not _points_somewhere(ty):
            return
        for one in self._arenas_in(value):
            arena = self._arena_by_id.get(one)
            if arena is None or arena.is_parameter:
                continue
            if arena.depth >= opened_at:
                self._outlives_arena("what the block comes to", arena, span)
                return

    def _given_back(self, arena: _Local, span: Span) -> None:
        """Everything made in an arena goes with it, from here on.

        Every name holding something made there is dead from this point, and stays
        dead after the block this was written in ends: on some way here it was given
        back, and a name read later cannot tell which way it came.  A name given
        something new is alive again.
        """
        if self._emptied:
            self._emptied[-1].add(id(arena))
        for scope in self._scopes:
            for local in scope.values():
                if id(arena) in local.arenas and local.gone_at is None:
                    local.gone_at = span
                    local.gone_with = arena.name

    def _provenance(self) -> _Provenance:
        """What every name in reach was made in and whether it is dead, as it stands.

        The part of a name's state that differs from one way through a body to
        another, as its value does -- so it is taken and put back where the value is:
        at every arm, and around every way out that runs what a block put off.
        """
        return {id(local): (local, local.arenas, local.gone_at, local.gone_with)
                for scope in self._scopes for local in scope.values()}

    def _put_back(self, state: _Provenance) -> None:
        """Make what *state* says of every name in it true again."""
        for local, arenas, gone_at, gone_with in state.values():
            local.arenas, local.gone_at, local.gone_with = arenas, gone_at, gone_with

    def _joined(self, before: _Provenance,
                ways: Sequence[_Provenance]) -> _Provenance:
        """What holds where several ways through meet: the union of what a name was
        made in, and dead where it is dead along any of them."""
        found: _Provenance = {}
        for key, (local, arenas, gone_at, gone_with) in before.items():
            made: set[int] = set()
            dead: tuple[Span | None, str] = (None, "")
            for way in ways:
                _, there, gone, why = way.get(key, (local, arenas, gone_at,
                                                    gone_with))
                made.update(there)
                if gone is not None and dead[0] is None:
                    dead = (gone, why)
            found[key] = (local, frozenset(made), *dead)
        return found

    def _on_the_way_out(self) -> tuple[_Provenance, list[set[int]]]:
        """What a way out may change and the way that goes on must not see.

        What a block put off runs on every way out of it, and an `⎕empty` there
        kills what was made in the arena -- along that way, which ends, and not along
        the one that carries on past the `return` or the `break`.
        """
        return self._provenance(), [set(one) for one in self._emptied]

    def _left(self, kept: tuple[_Provenance, list[set[int]]]) -> None:
        """Put back what `_on_the_way_out` took."""
        self._put_back(kept[0])
        for one, was in zip(self._emptied, kept[1]):
            one.clear()
            one.update(was)

    def _taken_out_of(self, whole: ast.Expr, names: Sequence[str]) -> None:
        """Names bound to a part of something were made where the whole was.

        What a `match` takes out of a value, and what a `foreach` takes out of what
        it walks, point where the whole pointed: an entry of a list made in an arena
        is in the arena, and so is the text a result carried.
        """
        made = self._arenas_in(whole)
        if not made:
            return
        for name in names:
            local = self._find_local(name)
            if local is not None and not self._is_arena(local) \
                    and _points_somewhere(self._held_by(local)):
                local.arenas = local.arenas | made

    def _a_turn_begins(self, body: ast.Block) -> _Provenance:
        """What a turn of a loop may begin with, which is what any turn may end with.

        A name the body assigns may hold, at the top of a turn, what an earlier turn
        gave it -- so it is taken to be made in anything the body names, which a
        walk of the body says before any of it is lowered: the turn after is behind
        it in the text and ahead of it in time.  Then what the body gives back is
        dead for the whole of the loop (`_before_a_turn`).
        """
        assigned: set[str] = set()
        parts: set[str] = set()
        _written_in(body, assigned, parts)
        if assigned or parts:
            anything = self._arenas_in(body)
            for name in assigned | parts:
                local = self._find_local(name)
                if local is None or self._is_arena(local) \
                        or not _points_somewhere(self._held_by(local)):
                    continue
                # An array whose elements the body writes may, at the top of a
                # turn, hold what a later line of the body put in it.
                local.arenas = local.arenas | anything
        self._before_a_turn(body)
        return self._provenance()

    def _after_the_turns(self, head: _Provenance, end: _Provenance | None,
                         loop: _Loop | None) -> None:
        """What holds after a loop: what holds where it may be left.

        At the top of a turn, where the condition or the iterator may end it; at the
        end of the body, which goes back there; and at every `break`.
        """
        ways = [head]
        if end is not None:
            ways.append(end)
        if loop is not None:
            ways.extend(loop.left_with)
        self._put_back(self._joined(head, ways))

    def _made_in_part(self, base: ast.Expr, value: ast.Expr, span: Span) -> None:
        """What a field, an element or an entry was given, added to the whole.

        The whole holds what it held and now this as well, so what it was made in
        grows and is never replaced -- the other parts still hold what they did.
        Put further out than an arena, something made in the arena outlives it, as
        it would in a name of its own (4614).
        """
        root = base
        while isinstance(root, (ast.Member, ast.Element, ast.Index)):
            root = root.base
        if not isinstance(root, ast.NameRef):
            return
        local = self._find_local(root.name)
        if local is None or self._is_arena(local) \
                or not _points_somewhere(self._held_by(local)):
            return
        if _is_owning(self._held_by(local)):
            # An element of an array was copied into the array's allocator on
            # the way in, so what the array was made in is what it was.
            return
        made = self._arenas_in(value)
        for one in made:
            arena = self._arena_by_id.get(one)
            if arena is not None and arena.depth > local.depth:
                self._outlives_arena(root.name, arena, span)
                return
        local.arenas = local.arenas | made

    def _owner_of(self, made: frozenset[int]) -> int | None:
        """The one allocator *made* names, where it names exactly one.

        An arena of this body's or a parameter, or `⎕heap`; anything else -- several,
        a parameter that says nothing, nothing at all -- names none the compiler can
        give back to or copy into.
        """
        if len(made) != 1:
            return None
        (one,) = made
        if one == _HEAP:
            return one
        arena = self._arena_by_id.get(one)
        if arena is None or self._find_local(arena.name) is not arena:
            return None
        return one

    def _owner_place(self, builder: IRBuilder, owner: int, span: Span) -> Value:
        """Where the allocator *owner* names keeps its state."""
        if owner == _HEAP:
            heap = self._provided(HEAP_NAME)
            assert isinstance(heap, GlobalVar)
            return builder.address(heap, span)
        arena = self._arena_by_id[owner]
        return arena.value

    def _owner_written(self, owner: int) -> str:
        """What a report calls the allocator *owner*."""
        if owner == _HEAP:
            return HEAP_NAME
        arena = self._arena_by_id.get(owner)
        return "".join(("'", arena.name if arena is not None else "?", "'"))

    def _lasts(self, key: int, holder: int, depth: int, local: bool) -> bool:
        """Whether what *key* names lasts as long as a container made with *holder*.

        The container's own allocator does; so does an arena of the caller's where
        the container is this call's own -- the caller cannot give it back while the
        call runs -- and an arena of this body's bound further out than the
        container.  Nothing else is provably long enough: an arena bound beside the
        container may be given back while the container is still read.
        """
        if key == holder:
            return True
        arena = self._arena_by_id.get(key)
        if arena is None:
            return False
        return (arena.is_parameter and local) or arena.depth < depth

    def _stored(self, builder: IRBuilder, written: ast.Expr, value: Value,
                ty: Type, holder: int, depth: int, local: bool, span: Span
                ) -> tuple[Value, frozenset[int], Value | None]:
        """*value* as an element of a container whose elements are kept in *holder*.

        Every element carries its allocator, so one need not be in the container's
        own: what it is given is put in as it is wherever that is provably safe, and
        copied into *holder* otherwise -- the defensive answer to not knowing.

        - Text in the image goes in as it is: nothing gives it back.
        - Into a **pool**, which never gives back one element: anything whose
          allocator lasts as long as the container, `⎕heap`'s included, since a pool
          holding it never frees it.
        - Into **`⎕heap`**, whose container gives back what it replaces: what it holds
          from the heap has to be its alone, so a temporary of the heap's is moved in
          and anything else of the heap's is copied.  What a pool that lasts long
          enough made goes in as it is; giving that back is nothing.

        Answers what to store, the provenance the container gains by it, and a
        temporary that was copied and is now the caller's to give back.
        """
        if not owned.points(ty):
            return value, frozenset(), None
        if _is_owning(ty) and not _is_temporary(written):
            # An array of text is its elements, so putting one in by value would
            # make two owners of each: only one nobody else names may go in.
            self._diags.emit(D.LANG_CONTAINER_CANNOT_COPY, written.span,
                             type=ty.written())
            return UndefConst(ERROR), frozenset(), None
        made = self._arenas_in(written)
        if not made:
            return value, made, None
        pool = holder != _HEAP
        if pool:
            fits = all(self._lasts(one, holder, depth, local) or one == _HEAP
                       or _is_element_key(one) or _is_container_key(one)
                       or (one in self._unknown_by_id and local)
                       for one in made)
        else:
            if made == {_HEAP} and _is_temporary(written):
                return value, made, None
            fits = all(self._lasts(one, holder, depth, local) for one in made)
        if fits:
            return value, made, None
        if not owned.can_own(ty):
            self._diags.emit(D.LANG_CONTAINER_CANNOT_COPY, written.span,
                             type=ty.written())
            return UndefConst(ERROR), frozenset(), None
        copied = builder.call(owned.own_function(self._module, ty),
                              (self._owner_place(builder, holder, span), value),
                              ty, span)
        self._module.reports.record(
            ReportKind.COPY_INTO_ALLOCATOR, self._demanding[1].name
            if self._demanding[1] is not None else "",
            "".join(("what is put in is copied into ", self._owner_written(holder),
                     ", its allocator not being provably the container's or long "
                     "enough lived")), written.span)
        spare = value if made == {_HEAP} and _is_temporary(written) else None
        return copied, frozenset({holder}), spare

    def _elements_replaced(self, keys: frozenset[int], name: str,
                           span: Span) -> None:
        """Kill every name read out of a container that may have replaced elements.

        A container of `⎕heap`'s gives back what it replaces, so what was read out of
        it before -- the same object, not a copy -- may be gone.  Which element is
        not asked: telling one index from another is arithmetic.
        """
        wanted = {_element_key(one) for one in keys if _is_container_key(one)}
        if not wanted:
            return
        for scope in self._scopes:
            for local in scope.values():
                if wanted & local.arenas and local.gone_at is None:
                    local.gone_at = span
                    local.gone_with = "".join((_REPLACED, name))

    def _answered(self, builder: IRBuilder, written: ast.Expr, value: Value,
                  func: Function, span: Span) -> Value:
        """What a function answers, kept in the allocator its signature names.

        `→ T in a` names `a`, `→ T in ⎕heap` the heap, and saying nothing names
        `⎕heap` as well.  An answer made anywhere else is copied there before the
        function leaves -- before what it put off runs, so an arena given back on
        the way out has been copied out of first -- and one that cannot be copied is
        refused (4624).  An answer of the heap's is the caller's alone, so only a
        temporary is answered as it is.

        Text in the image is answered as it is where the signature says nothing.
        Where it names one allocator it is copied in too: the answer then travels
        without its allocator, which the caller adds, and has to be in it.
        """
        ty = func.ty.ret
        said = self._answer_made_in
        if said is None or self._value_type_of(value) is ERROR:
            return value
        if _holds_a_lambda(ty) and not _lambdas_last(ty) \
                and not self._lambda_may_leave(written, said[0] | {_HEAP},
                                               func.name, span):
            return UndefConst(ERROR)
        if not owned.points(ty):
            return value
        made = self._arenas_in(written)
        allowed, named = said
        thin = self._thin_to
        if func.made_in == (STATIC_AT,):
            # Nothing is copied into the image, which is made while compiling.
            if made:
                self._diags.emit(
                    D.LANG_ANSWER_NOT_STATIC, span, name=func.name,
                    found=", ".join(sorted({self._reach_written(one)
                                            for one in made})))
                return UndefConst(ERROR)
            self._answer_kinds.append("static")
            return value
        if not allowed and func.made_in == (HANDS_AT,):
            # Answering before the arena it hands out is made: there is nowhere
            # the answer could be, the arena being the body's own.  Said once.
            if id(func) not in self._hands_out_told:
                self._hands_out_told.add(id(func))
                self._diags.emit(D.LANG_HANDED_OUT_NOT_MADE, span,
                                 name=func.hands_out or "")
            return value
        if isinstance(written, ast.Call) and self._callee_of.get(id(written)) is func \
                and (made <= allowed or allowed == {_HEAP}):
            # The function answering what it answers itself: by the same rule, one
            # call down, so nothing is copied -- and nothing comes after the call.
            # It says nothing of where the answers are made either way.
            return value
        if not made and thin is None:
            self._answer_kinds.append("static")
            return value
        if made and allowed == {_HEAP}:
            if self._freshly_heap(written):
                self._answer_kinds.append("heap")
                return value
            if thin is None and made == {_HEAP} and _is_temporary(written):
                # Of the heap's, or perhaps text in the image an answer came
                # back as: as it is, the value saying which.
                self._answer_kinds.append("maybe")
                return value
        elif made and made <= allowed:
            self._answer_kinds.append("param")
            return value
        target = thin if thin is not None else next(iter(sorted(allowed)))
        if not owned.can_own(ty):
            found = (["what another name still holds"] if made and made <= allowed
                     else sorted(self._owner_written(one) for one in made
                                 if one == _HEAP or one in self._arena_by_id)
                     or ["somewhere it cannot say"])
            self._diags.emit(D.LANG_ANSWER_CANNOT_BE_COPIED, span, name=func.name,
                             found=", ".join(found), said=named,
                             type=ty.written())
            return UndefConst(ERROR)
        copied = builder.call(owned.own_function(self._module, ty),
                              (self._owner_place(builder, target, span), value),
                              ty, span)
        self._answer_kinds.append("heap" if target == _HEAP else "param")
        self._module.reports.record(
            ReportKind.COPY_INTO_ALLOCATOR, func.name,
            "".join(("what '", func.name, "' answers is copied into ", named,
                     ", where its signature says it is made")), span)
        if made == {_HEAP} and _is_temporary(written):
            # The original goes back to where it came from, which a value only
            # perhaps of the heap's is asked.
            owned.give_back(builder, value, ty,
                            "heap" if self._freshly_heap(written) else None)
        return copied

    def _lambda_may_leave(self, written: ast.Expr, lasting: frozenset[int],
                          name: str, span: Span) -> bool:
        """Check that a lambda leaving the call reaches nothing that stays (4632).

        What leaves -- an answer, something written through a place from outside
        -- is kept beyond this call, so what it reaches has to be kept that long:
        what the program has from the start, `⎕heap`, which nothing gives back a
        lambda's environment from, and what *lasting* adds.  A lambda is never
        copied: what it brought in is laid out as its body reads it, and nothing
        but its body knows how.  So one reaching anything else -- this call's
        frame, a variable of this call's, an arena of its own, whatever a
        parameter was handed -- is refused rather than copied.
        """
        made = self._arenas_in(written)
        stays = sorted(one for one in made if one not in lasting)
        if not stays:
            return True
        self._diags.emit(D.LANG_LAMBDA_REACHES_THE_CALL, span, name=name,
                         found=", ".join(sorted({self._reach_written(one)
                                                 for one in stays})))
        return False

    def _reach_written(self, key: int) -> str:
        """What a report calls something a value reaches, by its key."""
        reaching = self._stand_ins.get(key)
        if reaching is not None:
            return "".join(("'", reaching.name, "'"))
        if key == _HEAP or key in self._arena_by_id:
            return self._owner_written(key)
        unknown = self._unknown_by_id.get(key)
        if unknown is not None:
            return "".join(("what '", unknown.name, "' was handed"))
        if _is_element_key(key):
            return "a container it was read out of"
        return "something of this call's"

    def _freshly_heap(self, written: ast.Expr) -> bool:
        """Whether *written* comes to an object the heap just made.

        A join, a list written out, `in ⎕heap`, and a call whose answer the callee
        fixed in the heap: nothing else names it, and it is the heap's for certain --
        not text in the image an answer may also be, which the heap must never be
        told to give back.
        """
        while isinstance(written, ast.Named):
            written = written.value
        if isinstance(written, ast.Allocated):
            return self._arenas_in(written.arena) == {_HEAP} \
                and isinstance(written.value, (ast.Binary, ast.ListLit))
        if isinstance(written, ast.Call):
            return id(written) in self._fresh_heap
        if isinstance(written, ast.ListLit) or (
                isinstance(written, ast.Binary)
                and written.op is ast.BinaryOp.CONCAT):
            return self._arenas_in(written) == {_HEAP}
        return False

    def _handed_out_made(self, func: Function, node: object) -> None:
        """Check that a body handing out an arena of its own made it (4627).

        `let NAME: mut arena = ⎕arena` in the body's own scope -- not in a block
        inside it, which a way out could leave without making it.
        """
        if func.hands_out is None or self._hands_out_local is not None \
                or id(func) in self._hands_out_told:
            return
        self._hands_out_told.add(id(func))
        span = getattr(node, "name_span", None) or getattr(node, "span")
        self._diags.emit(D.LANG_HANDED_OUT_NOT_MADE, span, name=func.hands_out)

    def _settle_answer(self, func: Function, node: ast.FuncDef) -> None:
        """Write on the function where its answer is made, where that is one place.

        What the signature fixed, or what the body showed: an answer the heap made
        on every way out, which a caller compiled after it can then rely on -- the
        call tree is walked callees first so that one is.  The answer then travels
        without its allocator (the `thinanswers` pass), the caller adding it.
        """
        func.answer_from = None
        if not _thin_able(func.ty.ret):
            return
        if func.made_in is not None:
            if len(func.made_in) != 1:
                return
            (at,) = func.made_in
            if at == HANDS_AT:
                # The arena handed out is where the caller keeps it: the last
                # parameter, which is what the caller puts back.
                at = len(func.ty.params) - 1
            if at == HEAP_AT:
                func.answer_from = ("heap", -1)
            elif at == STATIC_AT:
                func.answer_from = ("static", -1)
            elif self._answer_kinds and all(one == "param"
                                            for one in self._answer_kinds):
                func.answer_from = ("param", at)
            else:
                return
        elif self._answer_kinds and all(one == "heap"
                                        for one in self._answer_kinds):
            func.answer_from = ("heap", -1)
        elif self._answer_kinds and all(one == "static"
                                        for one in self._answer_kinds):
            func.answer_from = ("static", -1)
        else:
            return
        index = func.answer_from[1]
        where = HEAP_NAME if func.answer_from[0] == "heap" \
            else "".join((STATIC_NAME, ", the image,")) \
            if func.answer_from[0] == "static" else "".join((
            "what '", node.params[index].name, "' is given")) \
            if index < len(node.params) else "".join((
                "'", func.hands_out or "", "', handed out to the caller"))
        self._module.reports.record(
            ReportKind.ALLOCATOR, func.name,
            "".join(("what '", func.name, "' answers is made in ", where,
                     " on every way out",
                     "" if func.answer_from[0] in ("heap", "static")
                     and func.made_in
                     else ", which its body shows",
                     ": it travels without its allocator, which the caller adds")),
            func.name_span if func.name_span.is_valid else func.span)

    def _before_a_turn(self, body: ast.Block) -> None:
        """Kill what an arena given back inside a loop held from before the loop.

        A turn that gives an arena back leaves the next turn reading what was made in
        it before the loop began, which is behind it in the text and ahead of it in
        time -- so it is dead for the whole of the loop.  What a turn makes for itself
        is made again each turn and is not touched.
        """
        named: set[str] = set()
        _empties_named(body, named)
        for name in named:
            arena = self._find_local(name)
            if arena is not None and self._is_arena(arena):
                for scope in self._scopes:
                    for local in scope.values():
                        if id(arena) in local.arenas and local.gone_at is None:
                            local.gone_at = body.span
                            local.gone_with = arena.name

    def _all_given_back(self, opened_at: int, span: Span) -> None:
        """Check that a pure function gives back every arena it made before leaving.

        Room an arena took from the system stays taken until it is given back, so
        leaving with one still holding room changes something that outlives the call
        (4617).  What counts is an `⎕empty` on this way out -- written straight, or put
        off with `defer`, which has run by the time this is asked.
        """
        if self._impure:
            return
        node, func = self._demanding
        name = func.name if func is not None else ""
        given = set().union(*self._emptied) if self._emptied else set()
        for scope in self._scopes[opened_at:]:
            for local in scope.values():
                if local.is_parameter or local.handed_out \
                        or not self._is_arena(local):
                    continue
                if self._held_by(local) is not ARENA:
                    continue
                if id(local) not in given and id(local) not in self._told_kept:
                    self._diags.emit(D.LANG_ARENA_NEVER_GIVEN_BACK, local.span,
                                     arena=local.name, name=name)
                    self._told_kept.add(id(local))

    def _lower_defer(self, stmt: ast.Defer) -> None:
        """Put a statement off until the block it is written in is left.

        Nothing is lowered here: it is lowered at every way out of the block, which
        is where it runs.  What may be deferred is something to *do* -- an
        expression or an assignment -- and not a way out of anything: a deferred
        statement runs while the block is already being left, so a `return` or a
        `break` in one would be leaving twice, and a `?` is a `return` (4612).
        """
        later = stmt.stmt
        why: str | None = None
        match later:
            case ast.ReturnStmt() | ast.Break() | ast.Continue():
                why = "it would leave a block that is already being left"
            case ast.Defer():
                why = "it would put off something already being put off"
            case ast.VarDef():
                why = "what it would name is gone as soon as it is named"
            case ast.ExprStmt() | ast.AssignStmt() | ast.MemberAssign() \
                    | ast.ElementAssign() | ast.DerefAssign():
                if _leaves(later):
                    why = "a '?' in it would leave a block already being left"
            case _:
                why = "it is not something to do"
        if why is not None:
            self._diags.emit(D.LANG_DEFER_NOT_A_THING_TO_DO, later.span, why=why)
            return
        self._deferred[-1].append(_Deferred(node=stmt, depth=len(self._scopes)))

    def _run_deferred(self, builder: IRBuilder, func: Function | None,
                      down_to: int) -> None:
        """Lower what every block from the innermost down to *down_to* put off.

        Innermost block first and, within a block, the last written first: what
        was set up last is taken down first.  The frames stay where they are --
        another way out of the same block will want them again.
        """
        if func is None:
            return
        for frame in range(len(self._deferred) - 1, down_to - 1, -1):
            for entry in reversed(self._deferred[frame]):
                if builder.is_terminated:
                    return
                self._lower_deferred(builder, func, entry)

    def _lower_deferred(self, builder: IRBuilder, func: Function,
                        entry: _Deferred) -> None:
        """Lower one deferred statement, as it was meant where it was written.

        The scopes are cut back to the ones open where it was written, so that a
        name a block inside has taken over means what it meant there.  And it is
        told about once: it is lowered at every way out, and a mistake in it is
        still one mistake.
        """
        told = id(entry.node) in self._defer_told
        self._defer_told.add(id(entry.node))
        kept = (self._scopes, self._unit_scopes, self._diags)
        self._scopes = kept[0][:entry.depth]
        self._unit_scopes = kept[1][:entry.depth]
        if told:
            self._diags = DiagEngine(lambda _: None, kept[2].control,
                                     kept[2].catalog)
        try:
            self._lower_stmt(builder, entry.node.stmt, func, False)
            # A statement of its own, so what it lent out for itself is given back
            # before the next one runs -- as at the end of any statement.
            self._statement_ended()
        finally:
            self._scopes, self._unit_scopes, self._diags = kept

    def _unrun_deferred(self, func: Function, frame: int) -> None:
        """Check, once, what a block put off and no way out of it ever ran.

        A block may never be left in the ordinary way -- a loop with no end, a call
        that stops the program -- and what it put off is then never lowered at a way
        out.  It is still written down and may still be wrong, so it is checked once
        here, where nothing will run it.
        """
        pending = [entry for entry in self._deferred[frame]
                   if id(entry.node) not in self._defer_told]
        if not pending:
            return
        nowhere = func.add_block("deferred.unreached")
        builder = IRBuilder(self._module, func)
        builder.position_at(nowhere)
        for entry in pending:
            if builder.is_terminated:
                break
            self._lower_deferred(builder, func, entry)
        if not builder.is_terminated:
            builder.unreachable()

    def _returning(self, builder: IRBuilder, value: Value | None,
                   span: Span) -> None:
        """Check what a `post` clause demands, and then return.

        Before *every* return, which is where the value there is to speak about
        exists.  One copy per return rather than one at a join, because a
        function may answer from several places and each of them answers its own
        value.
        """
        node, func = self._demanding
        if func is not None:
            # What every open block put off, innermost first, after the answer was
            # worked out and before anything is said about it.  What it kills, it
            # kills along this way out and not along the way past it.
            kept = self._on_the_way_out()
            try:
                self._run_deferred(builder, func, 0)
                if builder.is_terminated:
                    return
                self._all_given_back(0, span)
            finally:
                self._left(kept)
        if node is not None and func is not None:
            for clause in node.clauses:
                if clause.kind is not ast.ClauseKind.POST \
                        or self._reading_of(clause) is not False:
                    continue
                self._push_scope()
                if value is not None:
                    self._bind_local(ANSWER_NAME, value, clause.span)
                outer, self._after = self._after, True
                try:
                    self._demanded(builder, clause, func,
                                   "a post-condition that does not hold",
                                   statuses.POST_CONDITION)
                finally:
                    self._after = outer
                    self._scopes.pop()
                    self._unit_scopes.pop()
        builder.ret(value, span)

    def _demanded(self, builder: IRBuilder, clause: ast.Clause, func: Function,
                  what: str, status: int) -> None:
        """Lower one condition and emit the check that stops where it fails."""
        # A question the compiler settles is settled here and lowered nowhere:
        # `⌜A⌝ = ⌜B⌝` is such a question, and a lift is not a value, so a
        # condition holding one has to be answered before anything tries to
        # lower it.
        known = self._settled(clause.expr)
        if known is not None:
            self._already(clause, func, known)
            return
        # Pure whatever the function is: what makes a condition removable is
        # that removing it changes nothing, and that is a property of the
        # condition and not of what it belongs to.  The note says so on whatever
        # the existing purity rules report.
        outer = self._impure
        self._impure = False
        mark = self._diags.because(D.LANG_CLAUSE_IMPURE, clause.span,
                                   family=_PURITY)
        try:
            value = self._lower_expr(builder, clause.expr, None)
        finally:
            self._diags.and_no_longer(mark)
            self._impure = outer
        found = self._value_type_of(value)
        if found is ERROR:
            return
        if found is not BOOL:
            self._diags.emit(D.LANG_CLAUSE_NOT_A_TRUTH, clause.span,
                             found=found.written())
            return
        if isinstance(value, BoolConst):
            self._already(clause, func, value.value)
            return
        if self._conditions is Conditions.IGNORE:
            # Everything above still happened: what a build turns off is the
            # check and not the checking, so a build with conditions off accepts
            # exactly the programs a build with them on accepts.
            self._module.reports.record(
                ReportKind.CONDITION_DROPPED, func.name,
                "".join(("'", self._as_written(clause),
                         "' was not emitted, this build having asked for none")),
                clause.span)
            return
        builder.check(value, what, status, clause.span,
                      observing=self._conditions is Conditions.OBSERVE)

    def _already(self, clause: ast.Clause, func: Function, holds: bool) -> None:
        """What becomes of a condition the compiler settled.

        What the compiler can see is an error and what it cannot is a fault,
        which is the rule arithmetic already follows: a condition that cannot
        hold would stop every call of the function, so the program is wrong where
        it is written.  One that always holds costs nothing, and the log is where
        a reader is told it was free.
        """
        if not holds:
            self._diags.emit(D.LANG_CLAUSE_CANNOT_HOLD, clause.span,
                             function=func.name)
            return
        self._module.reports.record(
            ReportKind.CONDITION_HOLDS, func.name,
            "".join(("'", self._as_written(clause),
                     "' was settled while compiling, so nothing of it reaches "
                     "the binary")),
            clause.span)

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
        # Which function this is, for the one rule that is about where a call is
        # written rather than about what it calls.
        was_building = self._in_build_function
        self._in_build_function = func \
            if func.attrs.special is SpecialKind.BUILD else None
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
            self._in_build_function = was_building

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
        self._borrows = []
        self._places = {}
        block = func.add_block()
        builder = IRBuilder(self._module, func)
        outer_answer, self._answering = self._answering, func.ty.ret
        outer_impure, self._impure = self._impure, func.attrs.impure
        # What a device travels with: a function handed one may read or write it
        # without saying anything, which is the whole of the arrangement.  Written
        # `@[io]` where there is no parameter to carry it -- the drain a program does
        # before it ends has nothing handed to it and still does I/O.
        outer_io, self._may_do_io = self._may_do_io, (
            func.attrs.io or func.attrs.impure
            or any(_reaches_a_device(one) for one in func.ty.params))
        outer_did, self._did_io = self._did_io, False
        outer_demanding = self._demanding
        self._demanding = (node, func)
        self._push_scope()
        outer_addressed = self._addressed
        self._addressed = set()
        outer_unscanned, self._unscanned = self._unscanned, node.body
        if node.body is not None:
            _addressed_in(node.body, self._addressed)
        # Every parameter gets its register before any of them is given storage:
        # a block's parameters are what it is entered with, and the storage is
        # written by instructions that follow them.
        arriving = [block.add_param(func.ty.params[index], param.name)
                    for index, param in enumerate(node.params)]
        outer_hands = (self._hands_out, self._hands_out_local)
        self._hands_out = self._hands_out_local = None
        if func.hands_out is not None:
            # Where the caller keeps the arena this body hands out, last.
            self._hands_out = (func.hands_out,
                               block.add_param(func.ty.params[-1],
                                               "".join((func.hands_out, ".out"))),
                               len(self._scopes))
        outer_entry, self._at_entry = self._at_entry, {}
        for index, (param, value) in enumerate(zip(node.params, arriving)):
            # What arrived, before anything is done about storage: a parameter a
            # reference was taken of stands for its storage from here on, and
            # what `⎕entry` is for is the value rather than the place.
            self._at_entry[param.name] = value
            held = _held_by_the_startup(func, index, param)
            if held is not None:
                # The record the process was started with.  What the entry point
                # hands over is *where* it is -- it is in the image, and the
                # entry point filled it -- so the name stands for that storage and
                # not for a copy of what is in it.  Which is what a record name
                # already means: a record is somewhere, and a field of it is read
                # at an offset from where.
                self._bind_local(param.name, value, param.span, param.mutable,
                                 is_parameter=True, builder=builder,
                                 placed_as=held)
                continue
            self._bind_local(param.name, value, param.span,
                             param.mutable, is_parameter=True, builder=builder)
        assert node.body is not None
        outer_made_in = self._answer_made_in
        outer_made_from, self._made_from = self._made_from, {}
        outer_kinds = (self._answer_kinds, self._thin_to)
        self._params_made_in(node, func)
        # A function with no type parameters has requirements that are settled
        # where it is written, so they are checked once and here rather than at
        # every call.
        self._check_clause_shapes(node.clauses)
        self._check_clauses(node.clauses, {}, func.name, node.name_span)
        # Before the first statement: a condition is about what the function was
        # called with, so it is checked where nothing of the body has run yet.
        self._lower_conditions(builder, node, func)
        self._lower_block(builder, node.body, func)
        if func.borrows_from:
            # Before the scope goes: a parameter a reference was taken of lives
            # in the frame, and the name is what still knows where.
            sources: list[Value] = []
            for at in func.borrows_from:
                sources.append(arriving[at])
                borrowed = self._find_local(node.params[at].name)
                if borrowed is not None and borrowed.placed:
                    sources.append(borrowed.value)
            self._answers_from(func, node, tuple(sources))
        self._pop_scope()
        self._addressed = outer_addressed
        self._answering = outer_answer
        self._impure = outer_impure
        self._may_do_io = outer_io
        if self._did_io and not func.attrs.io:
            # Written down now that the body has said so: everything below the
            # checker reads one flag to know a call may not be dropped, and an
            # effect the *signature* carried the permission for still has to reach
            # that flag.
            func.attrs = replace(func.attrs, io=True, impure=True)
        self._did_io = outer_did
        if not builder.is_terminated:
            if func.ty.ret is VOID:
                self._returning(builder, None, node.span)
            else:
                self._diags.emit(D.LANG_FUNCDEF_RETURN_MISSING, node.name_span,
                                 name=func.name, type=func.ty.ret.written())
                builder.unreachable()
        self._settle_answer(func, node)
        self._handed_out_made(func, node)
        self._hands_out, self._hands_out_local = outer_hands
        self._demanding = outer_demanding
        self._unscanned = outer_unscanned
        self._at_entry = outer_entry
        self._answer_made_in = outer_made_in
        self._made_from = outer_made_from
        self._answer_kinds, self._thin_to = outer_kinds

    def _lower_block(self, builder: IRBuilder, block: ast.Block, func: Function,
                     as_result: bool = True, wanted: Type | None = None,
                     produces: bool = False) -> Value | None:
        """Lower the statements of one block, and hand back what it comes to.

        `as_result` says whether the last statement of this block is the
        function's result.  It is for a function's body and is not for the body
        of an arm of a `match`.  `produces` says the block is an arm a value is
        wanted of, in which case its last statement has to have one.
        """
        self._deferred.append([])
        self._emptied.append(set())
        frame = len(self._deferred) - 1
        opened_at = len(self._scopes)
        try:
            count = len(block.stmts)
            answer: Value | None = None
            for index, stmt in enumerate(block.stmts):
                is_last = index == count - 1
                if builder.is_terminated:
                    self._diags.emit(D.LANG_STMT_UNREACHABLE, stmt.span)
                    return None
                if is_last and produces:
                    answer = self._lower_yielding(builder, stmt, func, wanted)
                else:
                    self._lower_attributed_stmt(builder, stmt, func,
                                                as_result and is_last)
                self._statement_ended()
            if produces and answer is None and not builder.is_terminated:
                self._diags.emit(D.LANG_MATCH_ARM_HAS_NO_VALUE, block.span)
            if produces and answer is not None and block.stmts:
                # What the block comes to leaves it, and an arena made inside it
                # does not: it is given back on the way out.
                last = block.stmts[-1]
                if isinstance(last, ast.ExprStmt):
                    self._leaves_no_arena_behind(last.value, opened_at, None,
                                                 last.span,
                                                 self._value_type_of(answer))
            # Run off the end: what this block put off is done now, after what
            # it comes to was worked out and before anything after it runs.
            if not builder.is_terminated:
                self._run_deferred(builder, func, frame)
                self._all_given_back(opened_at, block.span)
            return answer
        finally:
            self._unrun_deferred(func, frame)
            self._deferred.pop()
            self._last_emptied = self._emptied.pop()

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
        # An arm of what the function answers ending in another `if` or `match`
        # hands the answering down to that one's arms.
        answering, self._yield_answer = self._yield_answer, None
        outer_arms = self._answer_arms
        if answering is not None and isinstance(stmt, ast.ExprStmt) \
                and isinstance(stmt.value, (ast.If, ast.Match)):
            self._answer_arms = answering
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
            self._answer_arms = outer_arms
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
            case ast.VarDef() if stmt.made_in is not None:
                self._lower_receiving(builder, stmt)
            case ast.VarDef():
                self._lower_local(builder, stmt)
                self._made_in(stmt.name, stmt.value, stmt.span)
            case ast.AssignStmt():
                # An assignment stands for the variable it changed, so it can be
                # a function's result the way any other last statement can.
                wants_value = is_last and func.ty.ret is not VOID
                result = self._lower_assignment(builder, stmt, wants_value)
                if not stmt.more:
                    self._made_in(stmt.name, stmt.value, stmt.span,
                                  assigning=True)
                if wants_value and result is not None:
                    # The mismatch here is between what the statement produced
                    # and what the function returns, which is what a return
                    # mismatch says; the assignment itself was already checked.
                    if result.ty is not func.ty.ret:
                        self._report_mismatch(stmt.span, result.ty, func.ty.ret)
                    result = self._answered(
                        builder, ast.NameRef(span=stmt.name_span, name=stmt.name),
                        result, func, stmt.span)
                    self._returning(builder, result, stmt.span)
            case ast.EntryAssign():
                self._lower_entry_assign(builder, stmt)
                self._made_in_part(stmt.base, stmt.value, stmt.span)
            case ast.UnitDef():
                # Nothing is lowered: a unit is part of a type and a type is
                # nothing the program runs.  What it does is exist from here to
                # the end of the body.
                self._define_unit(stmt)
            case ast.ElementAssign():
                self._lower_element_assign(builder, stmt)
                self._made_in_part(stmt.base, stmt.value, stmt.span)
            case ast.MemberAssign():
                self._lower_member_assign(builder, stmt)
                self._made_in_part(stmt.base, stmt.value, stmt.span)
            case ast.DerefAssign():
                self._lower_deref_assign(builder, stmt)
            case ast.Defer():
                self._lower_defer(stmt)
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
                    # An `if` or a `match` answered is answered arm by arm, the
                    # copy into the signature's allocator made where each arm
                    # ends rather than after they join -- so an arm that ends in
                    # a call of the function to itself still ends in that call.
                    by_arm = isinstance(stmt.value, (ast.If, ast.Match))
                    outer_arms, self._answer_arms = (
                        self._answer_arms, func if by_arm else None)
                    try:
                        value = self._lower_into(builder, stmt.value,
                                                 func.ty.ret, stmt.span)
                    finally:
                        self._answer_arms = outer_arms
                    if not by_arm:
                        value = self._answered(builder, stmt.value, value, func,
                                               stmt.span)
                    self._returning(builder, value, stmt.span)
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
        # A tuple travels as its leaves and not as its members, the same way a
        # record does: a member that is itself several values is that many
        # values here, since what travels is values.
        leaves: list[Value] = []
        for value, member in zip(values, types):
            leaves.extend(builder.leaves(value, member, expr.span))
        return builder.make_tuple(tuple(leaves), ty, expr.span)

    def _taken_apart(self, value: Value, names: Sequence[tuple[str, Span]],
                     span: Span) -> list[Type] | None:
        """The type each name of a destructuring stands for, or nothing where
        the value cannot be taken apart that way."""
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if not isinstance(ty, TupleType):
            self._diags.emit(D.LANG_TUPLE_NOT_A_TUPLE, span, found=ty.written())
            return None
        if len(ty.members) != len(names):
            self._diags.emit(D.LANG_TUPLE_WRONG_COUNT, span,
                             wanted=str(len(names)), found=ty.written(),
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
        outer, self._element_made = self._element_made, set()
        try:
            if not self._fill(builder, expr, ty, place, 0, ty.shape, ready):
                return UndefConst(ERROR)
            made = frozenset(self._element_made)
        finally:
            self._element_made = outer
        if owned.points(ty.element):
            # The array is room in the frame: what it was made in is what its
            # elements were, in the allocator they were put in or kept from.
            self._made_from[id(expr)] = (expr, made)
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
                owner = self._owner_of(self._out_of_arenas)
                if owner is not None and owned.points(ty.element):
                    # What an element points into lasts as long as the array, or
                    # is copied into the allocator `in` named, or `⎕heap`.
                    value, made, spare = self._stored(
                        builder, written, value, ty.element, owner,
                        len(self._scopes), True, written.span)
                    if self._value_type_of(value) is ERROR:
                        return False
                    self._element_made |= made
                    if spare is not None:
                        owned.give_back(builder, spare, ty.element,
                                        "heap" if self._freshly_heap(written)
                                        else None)
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

    def _field_place(self, builder: IRBuilder, where: Value, ty: ProductType,
                     at: int, span: Span) -> Value:
        """Where one field of a record is, given where the record is.

        `offsets_of` is the one thing that says where a field went, so a field
        written and a field read agree without either knowing the other.
        """
        held = ty.fields[at][1]
        start = builder.cast(CastKind.BITCAST, where,
                             self._module.types.ptr_type(U8, mutable=True),
                             span)
        moved = builder.binary(
            BinOp.ADD, start,
            builder.int_const(U64, offsets_of(ty, _LAYOUT)[at]), span)
        return builder.cast(CastKind.BITCAST, moved,
                            self._module.types.ptr_type(held, mutable=True),
                            span)

    def _build_record(self, builder: IRBuilder, where: Value, expr: ast.Expr,
                      ty: ProductType, span: Span) -> None:
        """Write what *expr* comes to into the place *where*.

        A record written out is written a field at a time into the place that
        will hold it, and a field that is itself a record written out goes one
        offset further in -- so a record holding a record is never a value, and
        never needs a register it could not have.  Anything else is lowered the
        ordinary way and copied in, which is what a call answering with one is.
        """
        if not self._is_record_literal(expr, ty):
            self._record_into(builder, where,
                              self._lower_into(builder, expr, ty, span),
                              ty, span)
            return
        assert isinstance(expr, ast.Call)
        given = self._fields_given(expr, ty)
        if given is None:
            return
        for at, (_, held) in enumerate(ty.fields):
            place = self._field_place(builder, where, ty, at, span)
            written = given[ty.fields[at][0]]
            if isinstance(held, ProductType):
                self._build_record(builder, place, written.value, held,
                                   written.span)
                continue
            builder.store(place,
                          self._lower_into(builder, written.value, held,
                                           written.span), span)
            self._owning_field(written.value, ty, ty.fields[at][0], held)

    def _is_record_literal(self, expr: ast.Expr, ty: ProductType) -> bool:
        """Whether *expr* writes a value of *ty* out, field by field."""
        if not isinstance(expr, ast.Call) \
                or not isinstance(expr.callee, ast.NameRef):
            return False
        named = self._top.get(expr.callee.name)
        return isinstance(named, _NamedType) and self._resolved(named) is ty

    def _fields_given(self, expr: ast.Call, ty: ProductType
                      ) -> dict[str, ast.Named] | None:
        """What each field of *ty* was given, checking that each was given once.

        Every field and each once: there is no default to fall back on, and a
        field left out would be storage holding whatever was there.
        """
        given: dict[str, ast.Named] = {}
        spoiled = False
        for one in expr.args:
            if not isinstance(one, ast.Named):
                self._diags.emit(D.LANG_PRODUCT_FIELD_NOT_NAMED, one.span,
                                 type=ty.written())
                spoiled = True
                continue
            if _field_index(ty, one.name) is None:
                self._diags.emit(D.LANG_PRODUCT_NO_SUCH_FIELD, one.span,
                                 type=ty.written(), name=one.name)
                spoiled = True
                continue
            if one.name in given:
                self._diags.emit(D.LANG_PRODUCT_FIELD_TWICE, one.span,
                                 name=one.name)
                spoiled = True
                continue
            given[one.name] = one
        for name, _ in ty.fields:
            if name not in given:
                self._diags.emit(D.LANG_PRODUCT_FIELD_MISSING, expr.span,
                                 type=ty.written(), name=name)
                spoiled = True
        return None if spoiled else given

    def _record_into(self, builder: IRBuilder, where: Value, value: Value,
                     ty: ProductType, span: Span, first: int = 0) -> int:
        """Write a record into the place *where*, a field at a time.

        A field at a time rather than all at once, because a record is its
        fields and each of them is something a store already knows how to
        write.  What a record is made of is its *leaves*: a field that is itself
        a record is spread out where it stands, so `first` is which of them this
        one begins at and what comes back is where the next would.
        """
        at_part = first
        for at, (_, held) in enumerate(ty.fields):
            place = self._field_place(builder, where, ty, at, span)
            if isinstance(held, ProductType):
                at_part = self._record_into(builder, place, value, held, span,
                                            at_part)
                continue
            # A field of several parts -- text, a lambda -- is that many leaves,
            # put back together into the one value the store writes.
            pieces = parts_of(held)
            builder.store(place, builder.whole(
                [builder.extract(value, at_part + which, piece, span)
                 for which, piece in enumerate(pieces)], held, span), span)
            at_part += len(pieces)
        return at_part

    def _record_from(self, builder: IRBuilder, where: Value, ty: ProductType,
                     span: Span) -> Value:
        """Read a record out of the place *where*, a leaf at a time."""
        return builder.make_tuple(
            tuple(self._leaves_from(builder, where, ty, span)), ty, span)

    def _leaves_of(self, builder: IRBuilder, value: Value, ty: Type,
                   span: Span) -> list[Value]:
        """What *value* is made of, which for a record is its leaves.

        A record travels as its leaves and not as its fields, so a field that is
        itself a record contributes all of them and not one of it: what is put
        together here is the same list either way down.
        """
        # A field of several parts -- text, a list, a nested record -- is as many
        # leaves as it has parts, which is what the record's own parts count.
        return builder.leaves(value, ty, span)

    def _leaves_from(self, builder: IRBuilder, where: Value, ty: ProductType,
                     span: Span) -> list[Value]:
        """Every leaf of the record at *where*, in the order it is made of."""
        found: list[Value] = []
        for at, (_, held) in enumerate(ty.fields):
            place = self._field_place(builder, where, ty, at, span)
            if isinstance(held, ProductType):
                found.extend(self._leaves_from(builder, place, held, span))
                continue
            found.extend(builder.leaves(builder.load(place, span), held, span))
        return found

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
                             found=found.written())
            return None
        if not isinstance(written, ast.IntLit) \
                and not self._stands_for(found.unit, IDX_UNIT):
            # Which element is wanted is not a length, a count of seconds or a
            # number of apples.  A literal is whatever it is asked to be and so
            # is never wrong here; anything else says what it counts, and a
            # number counting something else reaches this through `\N{APL FUNCTIONAL SYMBOL QUAD}drop` and
            # `\N{APL FUNCTIONAL SYMBOL QUAD}unit`, which is the program saying it meant to.
            self._diags.emit(D.LANG_UNIT_INDEX, written.span, found=found.written())
            return None
        if isinstance(written, ast.IntLit) and along is not None:
            if not 0 <= written.value < along:
                self._diags.emit(D.LANG_ARRAY_INDEX_OUTSIDE, written.span,
                                 index=str(written.value), length=along)
                return None
            return builder.int_const(U64, written.value)
        wide = self._as_count(builder, index, found, span)
        builder.check(builder.compare(CmpPred.ULT, wide, length, span),
                      "an index outside its array",
                      statuses.OUT_OF_RANGE, span)
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
        found = self._paired(builder, expr.base, ARRAY_PAIR, expr.indices,
                             expr.span, expected)
        if found is not None:
            return found
        base = self._lower_expr(builder, expr.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return UndefConst(ERROR)
        if isinstance(ty, TupleType):
            return self._lower_tuple_member(builder, expr, base, ty, expected)
        if not isinstance(ty, ArrayType):
            self._diags.emit(D.LANG_ARRAY_NOT_AN_ARRAY, expr.base.span,
                             found=ty.written())
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
                             found=ty.written())
            return UndefConst(ERROR)
        if not held.fixed or held.rank > ty.rank \
                or held.shape[:held.rank] != ty.shape[:held.rank]:
            self._diags.emit(D.LANG_MASK_WRONG_SHAPE, expr.indices[0].span,
                             found=held.written(), wanted=ty.written())
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
                             found=ty.written())
            return
        if not held.fixed or held.rank > ty.rank \
                or held.shape[:held.rank] != ty.shape[:held.rank]:
            self._diags.emit(D.LANG_MASK_WRONG_SHAPE, stmt.indices[0].span,
                             found=held.written(), wanted=ty.written())
            return
        if not isinstance(ty.element, (IntType, BoolType)):
            self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, stmt.span,
                             feature="".join((
                                 "writing through a mask into an array of '",
                                 ty.element.written(), "'")))
            return
        start, lengths = self._shape_of(builder, base, ty, stmt.span)
        if self._read_only(start):
            self._diags.emit(D.LANG_STATIC_CHANGES, stmt.base.span,
                             name=_written_as(stmt.base))
            return
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
                                   builder.unary(UnOp.NOT, spread, span=stmt.span),
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
        # Where that member's own parts begin among the tuple's, and how many
        # of them there are: a member is one value to the program and may be
        # several to the machine.
        first, count = parts_within(ty)[at]
        pieces = parts_of(ty)
        return builder.whole(
            [builder.extract(base, first + which, pieces[first + which],
                             expr.span) for which in range(count)],
            member, expr.span)

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
                             found=ty.written())
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
                             found=reaching.written())
            return UndefConst(ERROR)
        end = self._as_count(builder, last, reaching, span)
        builder.check(builder.compare(CmpPred.ULE, end, lengths[0], span),
                      "a slice reaching past the end of its array",
                      statuses.OUT_OF_RANGE, span)
        builder.check(builder.compare(CmpPred.ULE, first, end, span),
                      "a slice that ends before it begins",
                      statuses.OUT_OF_RANGE, span)
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
                             found=ty.written())
            return
        if not self._record_may_change(stmt.base, stmt.span):
            # An element is written only through a name that says it may be:
            # `mut`, as a field is.
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
        place = self._element_place(builder, start, ty.element, offset, stmt.span)
        if not owned.points(ty.element):
            if _holds_a_lambda(ty.element):
                kept = self._into_a_place(builder, start, stmt.base, stmt.value,
                                          value, ty.element, stmt.span)
                if kept is None:
                    return
            builder.store(place, value, stmt.span)
            return
        # An element that points somewhere is kept as the array's allocator
        # says: as it is where that is safe, copied in otherwise.  The array's
        # allocator is known only of one this body made or was handed (4622).
        root = stmt.base
        container = self._find_local(root.name) \
            if isinstance(root, ast.NameRef) else None
        owner = container.holds_in if container is not None else None
        if container is None or owner is None:
            self._diags.emit(D.LANG_CONTAINER_OWNER_UNKNOWN, stmt.base.span,
                             type=ty.written())
            return
        value, made, spare = self._stored(builder, stmt.value, value, ty.element,
                                          owner, container.depth,
                                          not container.is_parameter, stmt.span)
        if self._value_type_of(value) is ERROR:
            return
        if spare is not None:
            owned.give_back(builder, spare, ty.element,
                            "heap" if self._freshly_heap(stmt.value) else None)
        container.arenas = container.arenas | made
        if owner != _HEAP:
            # A pool gives back only all at once: what is replaced stays where it
            # is until then, and nothing is called for it.
            builder.store(place, value, stmt.span)
            return
        # The heap gives back what is replaced, asking the element which
        # allocator it came from -- and what was read out of the array before
        # may be what went.
        replaced = builder.load(place, stmt.span)
        builder.store(place, value, stmt.span)
        owned.give_back(builder, replaced, ty.element, None)
        self._elements_replaced(container.arenas, root.name, stmt.span)

    def _lower_member_assign(self, builder: IRBuilder,
                             stmt: ast.MemberAssign) -> None:
        """Lower `p.x ← v`, which writes one field of a record.

        Written the way the field is read, which is what every place in this
        language is.  Only the field is written: the rest of the record is not
        read, not copied and not touched, which is what having an address for
        each field is for.
        """
        found = self._record_at(builder, stmt.base, writing=True)
        if found is None:
            self._diags.emit(D.LANG_ASSIGN_NOT_A_RECORD, stmt.base.span)
            return
        where, ty = found
        at = _field_index(ty, stmt.name)
        if at is None:
            self._diags.emit(D.LANG_PRODUCT_NO_SUCH_FIELD, stmt.name_span,
                             type=ty.written(), name=stmt.name)
            return
        if not self._record_may_change(stmt.base, stmt.name_span):
            return
        if _read_only_table(ty.fields[at][1]):
            # A field holding a collection the type says nothing may be put in
            # is not a place to put a different collection either: what stands
            # there is what everything reading the record reads.
            self._diags.emit(D.LANG_PLACE_NOT_WRITABLE, stmt.name_span,
                             name=stmt.name, found=ty.fields[at][1].written())
            return
        if not self._made_here(where):
            # The record may be the caller's -- a reference says nothing about
            # whose place it names -- so writing a field of it is a change that
            # outlives the call unless the storage is this call's own.
            self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, stmt.span)
        held = ty.fields[at][1]
        place = self._field_place(builder, where, ty, at, stmt.span)
        if isinstance(held, ProductType) and not _holds_a_reference(held) \
                and not self._kept_by_the_place(where, stmt.base, held) \
                and self._is_record_literal(stmt.value, held):
            # A record written out goes straight into the place that will hold
            # it, for the reason a definition's does: there is no register a
            # value of one could be made in.  One holding a reference goes the
            # other way, so that how long what it holds lasts is asked below.
            self._build_record(builder, place, stmt.value, held, stmt.span)
            return
        value = self._lower_into(builder, stmt.value, held, stmt.span)
        if self._value_type_of(value) is ERROR:
            return
        self._owning_field(stmt.value, ty, stmt.name, held)
        kept = self._into_a_place(builder, where, stmt.base, stmt.value, value,
                                  held, stmt.span)
        if kept is None:
            return
        value = kept
        if _holds_a_reference(held) \
                and not self._long_enough_for(where, value, stmt):
            return
        if isinstance(held, ProductType):
            self._record_into(builder, place, value, held, stmt.span)
            return
        builder.store(place, value, stmt.span)

    def _kept_by_the_place(self, where: Value, base: ast.Expr, ty: Type) -> bool:
        """Whether what goes into a place is asked how long it lasts
        (`_into_a_place`), which a record built where it goes would not be."""
        if not (owned.points(ty) or _holds_a_lambda(ty)):
            return False
        root = base
        while isinstance(root, (ast.Member, ast.Element, ast.Index)):
            root = root.base
        return isinstance(root, ast.Deref) or not self._made_here(where)

    def _into_a_place(self, builder: IRBuilder, where: Value, base: ast.Expr,
                      written: ast.Expr, value: Value, ty: Type, span: Span,
                      through: bool = False) -> Value | None:
        """What goes into a place reached through a reference or handed over, kept
        as long as the place is.

        A place from outside the call -- a parameter's, or one read out of memory --
        is the caller's, and what is written there is read after the call has
        ended: it has to last that long (4633), which `⎕heap` and the image do.  A
        string or a list made anywhere else is copied into `⎕heap`, as an answer
        is; a lambda is never copied (4632).  A place of this call's own reached
        through a reference is the variable the reference names, and what goes in
        is held to that variable's scope, as it would be written there by name.

        Answers what to write, or nothing where it may not be written.
        """
        if not (owned.points(ty) or _holds_a_lambda(ty)):
            return value
        func = self._demanding[1]
        if not self._made_here(where):
            if _holds_a_lambda(ty):
                if _lambdas_last(ty):
                    return value
                return value if self._lambda_may_leave(
                    written, frozenset({_HEAP}),
                    func.name if func is not None else "", span) else None
            made = self._arenas_in(written)
            stays = sorted(one for one in made if one != _HEAP)
            if not stays:
                return value
            if not owned.can_own(ty):
                self._diags.emit(D.LANG_WRITTEN_BEYOND_THE_CALL, span,
                                 found=", ".join(sorted({self._reach_written(one)
                                                         for one in stays})))
                return None
            if func is not None:
                self._module.reports.record(
                    ReportKind.COPY_INTO_ALLOCATOR, func.name,
                    "".join(("what is written into a place from outside the call "
                             "is copied into ", HEAP_NAME, ", which lasts as long "
                             "as the caller can read it")), span)
            return builder.call(owned.own_function(self._module, ty),
                                (self._owner_place(builder, _HEAP, span), value),
                                ty, span)
        root = base
        while isinstance(root, (ast.Member, ast.Element, ast.Index)):
            root = root.base
        if not (through or isinstance(root, ast.Deref)):
            # Written by name, which `_made_in_part` holds to the name's scope.
            return value
        named = self._named_place_of(where)
        if named is None:
            return value
        made = self._arenas_in(written)
        for one in made:
            arena = self._arena_by_id.get(one)
            if arena is not None and not arena.is_parameter \
                    and arena.depth > named.depth:
                self._outlives_arena(named.name, arena, span)
                return None
        named.arenas = named.arenas | made
        return value

    def _long_enough_for(self, where: Value, value: Value,
                         stmt: ast.MemberAssign) -> bool:
        """Whether what goes into this field lasts as long as the field does.

        The rule a write through a reference already follows, asked of a field
        for the same reason: the place written holds a reference, so what goes
        into it has to last as long as the place does, and how long that is is
        how long what holds it does.  Before this a field was the way round it.
        """
        named = self._named_place_of(where)
        if named is None:
            # Nothing here made the place, so it came in from outside and how
            # long it lasts is not this call's to know.  A reference of this
            # call's own put there would outlive the call that made it, and
            # neither end can see it.
            if self._lasting(value):
                return True
            self._diags.emit(D.LANG_REF_WRITTEN_INTO_A_PLACE_FROM_OUTSIDE,
                             stmt.span)
            return False
        return not self._outlives_it(
            value, named.depth,
            "".join((_written_as(stmt.base), ".", stmt.name)), stmt.span)

    def _record_may_change(self, expr: ast.Expr, where: Span) -> bool:
        """Whether the record *expr* names may be written, said by its root.

        A field is as mutable as the record holding it, and a record reached
        through a reference is as mutable as the reference: the mark is written
        once, where the thing was made or where it was lent, and a field does
        not get to say it again.
        """
        match expr:
            case ast.Member() | ast.Element():
                return self._record_may_change(expr.base, where)
            case ast.Deref():
                if not isinstance(expr.operand, ast.NameRef):
                    # A reference worked out rather than named.  What it
                    # promises is in its type and nothing here has it without
                    # lowering the expression a second time.
                    return True
                held = self._find_local(expr.operand.name)
                if held is None:
                    return True
                ty = self._held_by(held)
                if isinstance(ty, PtrType) and not ty.mutable:
                    self._diags.emit(D.LANG_REF_NOT_WRITABLE, where,
                                     found=ty.written())
                    return False
                return True
            case ast.NameRef():
                local = self._find_local(expr.name)
                if local is not None:
                    if local.mutable:
                        return True
                    self._diags.emit(D.LANG_VARDEF_NOT_MUTABLE, where,
                                     name=expr.name).note(
                        D.LANG_VARDEF_DEFINED_HERE, local.span, name=expr.name)
                    return False
                found = self._provided(expr.name)
                if isinstance(found, GlobalVar) and not found.mutable:
                    self._diags.emit(D.LANG_VARDEF_NOT_MUTABLE, where,
                                     name=expr.name)
                    return False
                if isinstance(found, GlobalVar) \
                        and _read_only_table(found.value_type):
                    # A name at the top level standing for a table nothing may
                    # put anything in is not a place to put a different table:
                    # what stands there is what every part of the program reads.
                    # `⎕environ` is such a name, which is what makes the
                    # environment one thing they all agree about.
                    self._diags.emit(D.LANG_PLACE_NOT_WRITABLE, where,
                                     name=expr.name,
                                     found=found.value_type.written())
                    return False
                return True
            case _:
                return True

    def _callee_value(self, expr: ast.Expr) -> _Local | None:
        """The name a call names, where it names a function held in one.

        A name bound to a function is called through what it holds rather than
        by naming a definition: which function it is, is not a question the
        compiler answers, which is the whole point of a function being a value.
        """
        if not isinstance(expr, ast.NameRef):
            return None
        local = self._find_local(expr.name)
        if local is None or not isinstance(self._held_by(local), FuncType):
            return None
        return local

    def _lower_indirect(self, builder: IRBuilder, expr: ast.Call,
                        local: _Local, expected: Type | None) -> Value:
        """Lower a call through a function held in a name.

        What it brought in goes first, before what the call wrote, which is the
        parameter nobody wrote and the one thing an indirect call has to agree
        about beyond the types.  Nothing here knows what the callee does, so it
        is taken to do everything: a pure function may not make one.
        """
        local.read = True
        if local.gone_at is not None:
            # What it brought in went with an arena, or with a container that
            # replaced what it was read out of: calling it would read that.
            self._told_gone(local, local.name, expr.callee.span)
            return UndefConst(ERROR)
        ty = self._held_by(local)
        assert isinstance(ty, FuncType)
        held = self._read_capture(builder, local, expr.span)
        outer_listing, self._listing = self._listing, ty.listable
        try:
            args = self._one_by_one(builder, expr.args, ty.params, local.name)
        finally:
            self._listing = outer_listing
        if args is None:
            return UndefConst(ERROR)
        if len(args) != len(ty.params):
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=local.name, expected=len(ty.params),
                             found=len(args))
            return UndefConst(ERROR)
        if any(one.ty is ERROR for one in args):
            return UndefConst(ERROR)
        self._an_effect(D.LANG_PURE_CALLS_IMPURE, expr.span, name=local.name)
        # Both halves are read once, before any call is made: a walk makes one
        # call per element and they all go to the same code with the same
        # environment.
        code = builder.extract(held, 0, _ENVIRONMENT, expr.span)
        environment = builder.extract(held, 1, _ENVIRONMENT, expr.span)

        handing: list[Value] = []
        if ty.hands_out:
            place = self._arena_for(expr, local.name)
            if place is None:
                return UndefConst(ERROR)
            handing.append(place)

        def make(given: Sequence[Value], span: Span) -> Value:
            return builder.call(code, (environment, *given, *handing), ty.ret,
                                span)

        if ty.listable \
                and any(one.ty is not wanted
                        for one, wanted in zip(args, ty.params)):
            return self._walked(builder, ty, make, args, expr, expected)
        answer = make(args, expr.span)
        if ty.ret is VOID and expected is not None:
            self._diags.emit(D.LANG_CALL_HAS_NO_VALUE, expr.span, name=local.name)
            return UndefConst(ERROR)
        if not self._accepts(expected, answer.ty):
            self._report_mismatch(expr.span, answer.ty, expected)
            return UndefConst(ERROR)
        return answer

    # -- lambdas ---------------------------------------------------------------

    def _make_instance(self, written: _Generic, bound: dict[str, Type],
                       key: tuple[Type, ...], span: Span) -> Function | None:
        """Compile the generic function for one set of types.

        The body is checked here and not where the function was written, which
        is the whole of what "checked at each instantiation" means: what may be
        done to a value of a type parameter is what may be done to the type it
        turned out to be, and nothing before this knows what that is.  So an
        operation the types do not admit is reported at the call that asked for
        them, with a note pointing at the line it is written on.

        It is a function of the module like any other once it is made.  Two sets
        of types make two, and the symbol tells them apart on its own: a symbol
        is the signature written out, and two instantiations have two.
        """
        node = written.node
        outer_bound, self._bound = self._bound, dict(bound)
        outer_discard = self._discard_function
        self._discard_function = False
        try:
            params = tuple(self._resolve_type(one.type) for one in node.params)
            answer = self._return_type(node.ret_type)
            if answer is ERROR or any(one is ERROR for one in params):
                return None
            attrs, linkage = self._function_attrs(written.attrs)
            made_in, param_made_in = self._made_in_of(node, params, answer)
            hands_out = _hands_out_of(node)
            if hands_out is not None:
                params = (*params, self._module.types.ptr_type(ARENA, mutable=True))
            func = Function(
                name=node.name, hands_out=hands_out,
                ty=self._module.types.func_type(params, answer,
                                                attrs.listable),
                attrs=attrs, linkage=linkage, exported=False,
                cconv=attrs.abi if attrs.abi is not None else DEFAULT_CCONV,
                span=node.span, name_span=node.name_span,
                source_path=written.path,
                param_names=tuple(one.name for one in node.params),
                writes_arrays=_writes_arrays(node, params),
                defaults=self._defaults_of(node, params),
                borrows_from=self._borrowed_from(node, answer),
                made_in=made_in, param_made_in=param_made_in)
            self._module.add_function(
                func, key=self._key("".join((node.name, "\N{TOP LEFT CORNER}",
                                             ",".join(one.mangled() for one in key),
                                             "\N{TOP RIGHT CORNER}",
                                             # an operator may be defined for one
                                             # operand and for two, over one type
                                             "/", str(len(node.params))))))
            self._owned.append(func)
            written.made[key] = func
            self._module.reports.record(
                ReportKind.INSTANTIATE, node.name,
                "".join(("compiled for ",
                         ", ".join(one.written() for one in key),
                         ", which is what a call gave it")),
                span)
            mark = self._diags.because(
                D.LANG_GENERIC_ASKED_HERE, span, name=node.name,
                types=", ".join(one.written() for one in key))
            try:
                self._lower_instance(func, node, span)
            finally:
                self._diags.and_no_longer(mark)
            return func
        finally:
            self._bound = outer_bound
            self._discard_function = outer_discard

    def _lower_instance(self, func: Function, node: ast.FuncDef,
                        span: Span) -> None:
        """Check and lower one instantiation's body, as a function's own is.

        Everything about the function being checked is put aside and put back,
        the way a lambda's is: a call to a generic function stands in the middle
        of another body, and what is being checked has to be this one while its
        body is.
        """
        block = func.add_block()
        inner = IRBuilder(self._module, func)
        outer = (self._scopes, self._addressed, self._answering, self._impure,
                 self._carried, self._loops, self._outside, self._initializing,
                 self._assigning, self._operand_of, self._handing_over,
                 self._deferred, self._emptied, self._answer_made_in,
                 self._out_of_arena, self._out_of_arenas, self._made_from,
                 self._answer_kinds, self._thin_to, self._answer_arms,
                 self._yield_answer, self._hands_out, self._hands_out_local,
                 self._receiving, self._unscanned)
        self._scopes, self._addressed = [], set()
        self._answer_arms = self._yield_answer = None
        self._hands_out = self._hands_out_local = self._receiving = None
        # What was worked out about where an expression was made belongs to the
        # body it was lowered in: this one lowers the same nodes for other types.
        self._made_from = {}
        # Where `in` said room comes from is the expression's around the call, and
        # this body is a function of its own.
        self._out_of_arena, self._out_of_arenas = None, frozenset({_HEAP})
        # What the body this call stands in put off is that body's: a `return` in
        # here leaves this function and runs only what this one put off.
        self._deferred, self._emptied = [], []
        self._answering, self._impure = func.ty.ret, func.attrs.impure
        self._carried, self._loops, self._outside = set(), [], []
        self._initializing = self._assigning = self._operand_of = None
        self._handing_over = None
        self._push_scope()
        try:
            assert node.body is not None
            _addressed_in(node.body, self._addressed)
            self._unscanned = node.body
            arriving = [block.add_param(one, param.name)
                        for one, param in zip(func.ty.params, node.params)]
            for param, value in zip(node.params, arriving):
                self._bind_local(param.name, value, param.span, param.mutable,
                                 is_parameter=True, builder=inner)
            if func.hands_out is not None:
                self._hands_out = (func.hands_out,
                                   block.add_param(func.ty.params[-1], "".join(
                                       (func.hands_out, ".out"))),
                                   len(self._scopes))
            self._params_made_in(node, func)
            self._lower_block(inner, node.body, func)
            if not inner.is_terminated:
                if func.ty.ret is VOID:
                    inner.ret()
                else:
                    self._diags.emit(D.LANG_FUNCDEF_RETURN_MISSING,
                                     node.name_span, name=func.name,
                                     type=func.ty.ret.written())
                    inner.unreachable(node.span)
            self._settle_answer(func, node)
            self._handed_out_made(func, node)
            self._pop_scope()
        finally:
            (self._scopes, self._addressed, self._answering, self._impure,
             self._carried, self._loops, self._outside, self._initializing,
             self._assigning, self._operand_of, self._handing_over,
             self._deferred, self._emptied, self._answer_made_in,
             self._out_of_arena, self._out_of_arenas, self._made_from,
             self._answer_kinds, self._thin_to, self._answer_arms,
             self._yield_answer, self._hands_out, self._hands_out_local,
             self._receiving, self._unscanned) = outer

    def _lower_generic(self, builder: IRBuilder, expr: ast.Call,
                       written: _Generic, expected: Type | None) -> Value:
        """Lower a call to a function whose types the call settles.

        The arguments are lowered left to right as any call's are, and what
        each one turns out to be says more about the types -- so an argument is
        lowered knowing what the ones to its left already said, and a literal
        with no suffix takes the type an earlier argument settled.  That is the
        rule an ordinary call follows, with the parameter's type worked out
        rather than looked up.
        """
        if len(expr.args) != len(written.node.params):
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=written.node.name,
                             expected=len(written.node.params),
                             found=len(expr.args))
            return UndefConst(ERROR)
        bound: dict[str, Type] = {}
        args: list[Value] = []
        for at, (one, param) in enumerate(zip(expr.args, written.node.params)):
            wanted = self._worked_out(param.type, bound)
            outer = self._handing_over
            self._handing_over = (written.node.name, at + 1)
            try:
                value = (self._lower_into(builder, one, wanted, one.span)
                         if wanted is not None
                         else self._lower_expr(builder, one, None))
            finally:
                self._handing_over = outer
            found = self._value_type_of(value)
            if found is ERROR:
                return UndefConst(ERROR)
            if wanted is None and not self._reading(param.type, found, bound,
                                                   one.span, param.name):
                return UndefConst(ERROR)
            args.append(value)
        # The requirements come between the arguments and the instance: they are
        # what the types must satisfy, and an arrow in one of them may settle a
        # parameter no argument could -- which is why they are checked before the
        # test for a parameter nothing settles.  What a requirement reports is
        # about the definition, so the call that asked is hung on it as a note:
        # the message points at what cannot be written and this says who wanted
        # it, which is the one thing the definition cannot show.
        mark = self._diags.because(
            D.LANG_CLAUSE_ASKED_HERE, expr.span,
            function=written.node.name,
            types=", ".join("".join((one, " = ", bound[one].written()))
                            for one in written.parameters if one in bound))
        # From here on it is the definition's file that is being read, not this
        # one: what its requirements name and what its body calls are what the
        # file that wrote it could see.  The arguments above were this file's
        # expressions and were lowered with this file's names, which is the
        # division a bundle already makes.
        kept = (self._top, self._bundles)
        if written.scope is not None and written.siblings is not None:
            self._top, self._bundles = written.scope, written.siblings
        try:
            try:
                met = self._check_clauses(written.node.clauses, bound,
                                         written.node.name, expr.span)
            finally:
                self._diags.and_no_longer(mark)
            if not met:
                return UndefConst(ERROR)
            missing = [name for name in written.parameters if name not in bound]
            if missing:
                self._diags.emit(D.LANG_GENERIC_NOT_DETERMINED, expr.span,
                                 name=missing[0])
                return UndefConst(ERROR)
            func = self._instance_of(written, bound, expr.span)
        finally:
            self._top, self._bundles = kept
        if func is None:
            return UndefConst(ERROR)
        return self._made_call(builder, expr, func, args, expected)

    def _worked_out(self, param: ast.TypeExpr,
                    bound: dict[str, Type]) -> Type | None:
        """What a parameter's type is, where everything in it is settled.

        Nothing where some type parameter in it is not, which is what says the
        argument has to be lowered on its own and read rather than lowered into
        something.
        """
        named: list[str] = []
        _parameters_in(param, named)
        if any(one not in bound for one in named):
            return None
        outer, self._bound = self._bound, bound
        try:
            found = self._resolve_type(param)
        finally:
            self._bound = outer
        return None if found is ERROR else found

    def _reading(self, param: ast.TypeExpr, found: Type,
                 bound: dict[str, Type], span: Span, name: str) -> bool:
        """Read what the type parameters are out of an argument's type.

        The type a parameter is written with says how to read the argument's:
        a bare type parameter against `u8` says it is `u8`, and one with the
        array brackets after it against an array of `u8` says the same.  Where
        the two are not the same shape there is nothing to read.
        """
        match param:
            case ast.TypeRef() if param.module is None \
                    and _is_generic(param.name) and not param.result:
                earlier = bound.get(param.name)
                if earlier is not None and earlier is not found:
                    self._diags.emit(D.LANG_GENERIC_TWO_WAYS, span,
                                     name=param.name, first=earlier.written(),
                                     second=found.written())
                    return False
                bound[param.name] = found
                return True
            case ast.ArrayTypeRef() if isinstance(found, ArrayType):
                return self._reading(param.element, found.element, bound, span,
                                     name)
            case ast.ListTypeRef() if isinstance(found, ListType):
                return self._reading(param.element, found.element, bound, span,
                                     name)
            case ast.RefTypeRef() if isinstance(found, PtrType):
                return self._reading(param.pointee, found.pointee, bound, span,
                                     name)
            case ast.CollectionTypeRef() if param.value is None \
                    and isinstance(found, SetType):
                return self._reading(param.element, found.element, bound, span,
                                     name)
            case ast.CollectionTypeRef() if param.value is not None \
                    and isinstance(found, DictType):
                return (self._reading(param.element, found.key, bound, span, name)
                        and self._reading(param.value, found.value, bound, span,
                                          name))
            case ast.TupleTypeRef() if isinstance(found, TupleType) \
                    and len(param.members) == len(found.members):
                return all(self._reading(one, other, bound, span, name)
                           for one, other in zip(param.members, found.members))
            case ast.FuncTypeRef() if isinstance(found, FuncType) \
                    and len(param.params) == len(found.params):
                if not all(self._reading(one, other, bound, span, name)
                           for one, other in zip(param.params, found.params)):
                    return False
                if param.ret is None:
                    return found.ret is VOID
                return self._reading(param.ret, found.ret, bound, span, name)
            case _:
                self._diags.emit(D.LANG_GENERIC_NOT_MATCHED, span, name=name)
                return False

    def _as_wanted(self, builder: IRBuilder, value: Value, wanted: Type,
                   span: Span) -> Value:
        """A value already worked out, standing where *wanted* is asked for.

        An argument of a generic call is lowered before the types are settled,
        so it cannot be lowered *into* its parameter's type the way an ordinary
        call's is.  What that path does and this one has to do too is let an
        array's length go: a fixed array stands where one of no stated length is
        wanted, which is the one thing the language converts and is a rewriting
        of the value rather than of what it came from.
        """
        found = self._value_type_of(value)
        if found is wanted or found is ERROR:
            return value
        if isinstance(found, ArrayType) and isinstance(wanted, ArrayType) \
                and _lets_go_of(found, wanted):
            start, lengths = self._shape_of(builder, value, found, span)
            return builder.make_tuple((start, *lengths), wanted, span)
        self._report_mismatch(span, found, wanted)
        return UndefConst(ERROR)

    def _instance_of(self, written: _Generic, bound: dict[str, Type],
                     span: Span) -> Function | None:
        """The function this generic one comes to for these types.

        One per set of types and not one per call: a second call saying what an
        earlier one said gets the same function back, which is what keeps a loop
        that calls one from emitting a copy each time round.
        """
        key = tuple(bound[name] for name in written.parameters)
        found = written.made.get(key)
        if found is not None:
            return found
        if key in written.making:
            # It calls itself with the types it already has.  What it will come
            # to is the one being made just now, which is not finished -- so
            # this is refused rather than looped over for ever.
            self._diags.emit(D.IMPL_UNIMPLEMENTED_FEATURE, span,
                             feature="a generic function that calls itself "
                                     "with the types it was given")
            return None
        written.making.add(key)
        try:
            found = self._make_instance(written, bound, key, span)
        finally:
            written.making.discard(key)
        return found

    def _made_call(self, builder: IRBuilder, expr: ast.Call, func: Function,
                   args: Sequence[Value], expected: Type | None) -> Value:
        """Lower the call itself, once the function it names has been made."""
        if any(one.ty is ERROR for one in args):
            return UndefConst(ERROR)
        made: list[Value] = []
        for at, (one, wanted) in enumerate(zip(args, func.ty.params)):
            given = self._as_wanted(builder, one, wanted, expr.args[at].span)
            if self._value_type_of(given) is ERROR:
                return UndefConst(ERROR)
            made.append(given)
        if func.hands_out is not None:
            place = self._arena_for(expr, func.name)
            if place is None:
                return UndefConst(ERROR)
            made.append(place)
        args = made
        self._what_it_changes(func, expr.span)
        answer = builder.call(func, list(args), func.ty.ret, expr.span)
        answer = self._as_long_as_given(builder, func, args, answer, expr.span)
        self._where_made(expr, func)
        if func.ty.ret is VOID and expected is not None:
            self._diags.emit(D.LANG_CALL_HAS_NO_VALUE, expr.span, name=func.name)
            return UndefConst(ERROR)
        if not self._accepts(expected, answer.ty):
            self._report_mismatch(expr.span, answer.ty, expected)
            return UndefConst(ERROR)
        return answer

    def _walks_what_it_is_given(self,
                                attrs: Sequence[ast.Attribute]) -> bool:
        """Whether what is written before a lambda or a function type says
        `listable`.

        A caller is who does the walking, so what may be said in either place is
        what a caller reads off the type; everything else a function can say is
        about its body, and a body is not what a name holds.  Anything else
        written here is reported as an attribute that does not apply.
        """
        return any(one.name == "listable"
                   for one in self._bind_attributes(attrs, AttrTarget.CALLABLE))

    def _lower_lambda(self, builder: IRBuilder, expr: ast.Lambda,
                      expected: Type | None) -> Value:
        """Lower `\N{GREEK SMALL LETTER LAMDA} \N{HORIZONTAL ELLIPSIS}`: a function written where a value is wanted.

        What it comes to is three words -- where its code is, where what it
        brought in with it is, and the allocator that room came from -- which is
        one type whether it brought anything in or nothing, so either stands
        where a `fn(\N{HORIZONTAL ELLIPSIS})` is wanted.

        The body becomes a function of the module like any other, with the
        things it brought in reached through a first parameter nobody wrote.
        What it may name is its parameters, what it brought in, and what the
        whole program has: the scope it is checked in holds those and nothing
        else, which is what makes the capture list the list of what it depends
        on rather than something a reader works out by reading the body.

        Where what it brought in is kept is the compiler's choice -- this call's
        frame for a lambda that provably stays in it, `\N{APL FUNCTIONAL SYMBOL QUAD}heap` for every other --
        unless `in` after the capture list says: `\N{APL FUNCTIONAL SYMBOL QUAD}heap`, which makes the
        lambda's type say it lasts (`fn(\N{HORIZONTAL ELLIPSIS}) in \N{APL FUNCTIONAL SYMBOL QUAD}heap \N{RIGHTWARDS ARROW} T`), or an arena.  What
        the lambda then reaches is written down as where it was made, so that
        every place it may go asks the question a string made in an arena is
        asked.  A lambda that brings nothing in lasts whatever is said, and may
        be given where one that lasts is wanted.
        """
        # The name comes first so that everything recorded about this lambda
        # can say which one it was: a lambda is written with none, so the log
        # is where the two are tied together.
        self._lambdas += 1
        name = "".join((LAMBDA_PREFIX, str(self._lambdas)))
        self._module.reports.record(
            ReportKind.NAME_LAMBDA, name,
            "the code of a lambda written here, which the program left unnamed",
            expr.span)
        taken = self._captures_of(expr, name)
        if taken is None:
            return UndefConst(ERROR)
        params = tuple(self._resolve_type(one.type) for one in expr.params)
        answer = self._return_type(expr.ret_type)
        if answer is ERROR or any(one is ERROR for one in params):
            return UndefConst(ERROR)
        if _writes_arrays(expr, params):
            # Called through the value it is, nothing would say the array it is
            # handed has to be one that may be written.
            self._diags.emit(D.LANG_WRITES_ARRAYS_AS_A_VALUE, expr.span, name=name)
            return UndefConst(ERROR)
        walks = self._walks_what_it_is_given(expr.attrs)
        arena: tuple[Value, frozenset[int]] | None = None
        lasting = False
        if expr.kept_in is not None:
            where, at = expr.kept_in
            if not taken and expr.brings_in is None:
                # Nothing brought in is kept nowhere: there is nothing to say.
                self._diags.emit(D.LANG_KEPT_IN_NOTHING_BROUGHT, at)
                return UndefConst(ERROR)
            if where == HEAP_NAME:
                lasting = True
            else:
                written = ast.NameRef(span=at, name=where)
                found = self._arena_named(builder, written)
                if found is None:
                    return UndefConst(ERROR)
                arena = (found, self._arenas_in(written))
        elif not taken:
            # Brings nothing in, so it lasts as long as the program: where that
            # is asked for, it is what is given.
            wanted = self._aiming_at(expected)
            lasting = isinstance(wanted, FuncType) and wanted.lasting
        ty = self._module.types.func_type(
            params, answer, walks, hands_out=_hands_out_of(expr) is not None,
            lasting=lasting)
        if walks and not params:
            # Named the way the report log names it, that being the one name
            # this function has and the one a reader can look up.
            self._diags.emit(D.LANG_LISTABLE_TAKES_NOTHING, expr.span,
                             name=name)
        held = tuple(self._held_by_capture(one, local) for one, local in taken)
        if any(one is ERROR for one in held):
            return UndefConst(ERROR)
        if self._unscanned is not None:
            self._lambdas_that_stay(self._unscanned)
            self._unscanned = None
        stays = arena is None and expr.kept_in is None \
            and self._stays.get(id(expr)) is expr
        # What it reaches is worked out before the body is lowered and the
        # environment filled, from the names as they stand where it is written.
        reaches = self._reached_by(builder, taken)
        if lasting and taken:
            # Its type says it lasts, so everything it reaches has to: the heap,
            # the image, and lambdas whose own type says the same.
            short = sorted({self._reach_written(one) for one in reaches
                            if one != _HEAP})
            if short:
                self._diags.emit(D.LANG_LASTING_LAMBDA_REACHES, expr.kept_in[1]
                                 if expr.kept_in is not None else expr.span,
                                 name=name, found=", ".join(short))
                return UndefConst(ERROR)
        place, offsets, allocator, kept = self._environment(
            builder, taken, held, expr.span, stays, arena)
        func = self._function_of_a_lambda(expr, params, answer, held, offsets,
                                          taken, name)
        if func is None:
            return UndefConst(ERROR)
        # What it carries is an address and says nothing about what is there:
        # one type covers every lambda, and what is at the address is the
        # lambda's own business.
        carried = (place if place.ty is _ENVIRONMENT
                   else builder.cast(CastKind.BITCAST, place, _ENVIRONMENT,
                                     expr.span))
        made = builder.make_tuple(
            (builder.code_address(func, expr.span), carried, allocator), ty,
            expr.span)
        if stays and taken:
            # The frame it is in goes with the scope it was written in, as far
            # as anything holding the lambda is concerned: a turn of a loop
            # writes the same room again.
            frame = _Local(name="".join(("what ", name, " brought in")),
                           value=place, span=expr.span,
                           depth=len(self._scopes))
            self._stand_ins[id(frame)] = frame
            self._arena_by_id[id(frame)] = frame
            kept = frozenset({id(frame)})
        self._made_from[id(expr)] = (expr, kept | reaches)
        self._report_environment(name, taken, stays, arena, expr.span)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        return made

    def _reached_by(self, builder: IRBuilder,
                    taken: Sequence[tuple[ast.Capture, _Local]]
                    ) -> frozenset[int]:
        """Everything a lambda reaches through what it brought in, as keys.

        A variable brought in by reference is reached where it is, which goes
        with the scope it was bound in; a value brought in reaches whatever it
        was made in -- a string in an arena, a lambda whose environment is in
        the frame -- and a reference brought in by value reaches the variable it
        names, where that is one of this call's.
        """
        found: set[int] = set()
        for one, local in taken:
            if one.by_reference:
                found.add(self._storage_key(local))
                continue
            what = self._held_by(local)
            if not _points_somewhere(what) or local.gone_at is not None:
                continue
            if isinstance(what, FuncType) and what.lasting:
                # Its type says what it reaches lasts, wherever it came from.
                continue
            found.update(local.arenas)
            if _holds_a_reference(what) and not local.placed:
                named = self._named_place_of(local.value)
                if named is not None:
                    found.add(self._storage_key(named))
        return frozenset(found)

    def _storage_key(self, local: _Local) -> int:
        """What stands for the place a variable is, where a lambda reaches it.

        One per variable, at its depth: it is the variable's storage that is
        reached, and that goes when the variable's scope does.
        """
        found = self._storage.get(id(local))
        if found is not None and found[0] is local:
            return id(found[1])
        stand_in = _Local(name=local.name, value=local.value, span=local.span,
                          depth=local.depth)
        self._storage[id(local)] = (local, stand_in)
        self._stand_ins[id(stand_in)] = stand_in
        self._arena_by_id[id(stand_in)] = stand_in
        return id(stand_in)

    def _report_environment(self, name: str,
                            taken: Sequence[tuple[ast.Capture, _Local]],
                            stays: bool,
                            arena: tuple[Value, frozenset[int]] | None,
                            span: Span) -> None:
        """Say in the report log where what a lambda brought in is kept."""
        func = self._demanding[1]
        if not taken:
            said = "brings nothing in, so it carries no environment and lasts " \
                "as long as the program"
        elif arena is not None:
            said = "keeps what it brought in in the arena 'in' named"
        elif stays:
            said = "keeps what it brought in in this call's frame: it is only " \
                "called, or handed to a call, and so provably stays in the call"
        else:
            said = "".join(("keeps what it brought in in ", HEAP_NAME,
                            ": it may leave the call that wrote it"))
        self._module.reports.record(
            ReportKind.ALLOCATOR, func.name if func is not None else name,
            "".join(("'", name, "' ", said)), span)

    def _lambdas_that_stay(self, body: ast.Node) -> None:
        """Find every lambda in *body* that provably stays in the call writing it.

        Written as an argument of a call of a function, or bound by `let` to a
        name that is not `mut` and is only ever called or handed to a call of a
        function: what a callee does with a function it is handed is held to
        its own rules, and those do not let it leave that call.  What such a
        lambda brought in may be in this call's frame.  Every other lambda may
        leave, and what it brings in is kept in `\N{APL FUNCTIONAL SYMBOL QUAD}heap` -- or the arena `in` names.
        """
        taken: set[str] = set()
        handed: list[ast.Lambda] = []
        bound: list[tuple[str, ast.Lambda]] = []
        _lambdas_handed_on(body, self._calls_a_function, taken, handed, bound)
        for one in handed:
            self._stays[id(one)] = one
        for name, one in bound:
            # Any other mention of the name anywhere in the body, the same name
            # bound again included: telling two bindings apart is not worth a
            # second walk, and the cautious answer is only an allocation.
            if name not in taken:
                self._stays[id(one)] = one

    def _calls_a_function(self, call: ast.Call) -> bool:
        """Whether *call* calls a function, rather than making a record or a variant
        or asking the compiler something -- which keep what they are handed."""
        callee = call.callee
        if isinstance(callee, ast.NameRef):
            if callee.name.startswith(BUILTIN_GLYPH):
                return False
            # A function, or a name bound inside a body -- which, called, holds
            # a lambda.
            named = self._top.get(callee.name)
            return named is None or isinstance(named, (Function, _Generic))
        if isinstance(callee, ast.Member) and isinstance(callee.base, ast.NameRef):
            return isinstance(self._top.get(callee.base.name), LoadedModule) \
                and self._type_exported(callee) is None
        return False

    def _function_as_a_value(self, builder: IRBuilder, func: object,
                             ref: ast.NameRef, expected: Type | None) -> Value:
        """A named function where a value is wanted, shaped the way a lambda is.

        What a name of function type holds is where the code is, where what was
        brought in is, and its allocator.  A function brings nothing in, so the
        last two are nothing -- but the first cannot
        be the function itself: everything called through such a name is called
        with the environment first, and a definition has no such parameter.

        So what the value points at is a shim, made once per function, that
        takes the environment, drops it, and calls the function.  One per
        function and not one per mention, since two mentions of one name are
        two ways of writing the same value.
        """
        assert isinstance(func, Function)
        if func.writes_arrays:
            # Called through a value, nothing would say the array it is handed
            # has to be one that may be written.
            self._diags.emit(D.LANG_WRITES_ARRAYS_AS_A_VALUE, ref.span,
                             name=func.name)
            return UndefConst(ERROR)
        if func.hands_out is not None:
            # Called through a value, it would hand its arena out where nothing
            # receives it by name.  A lambda may hand one out; its type says so.
            self._diags.emit(D.LANG_HANDED_OUT_UNBOUND, ref.span, name=func.name)
            return UndefConst(ERROR)
        shim = self._shim_for(func, ref.span)
        if shim is None:
            return UndefConst(ERROR)
        # It brought nothing in, so it carries no environment and no allocator,
        # and lasts as long as the program -- which its type says where that
        # is what is wanted.
        wanted = self._aiming_at(expected)
        ty = func.ty
        if isinstance(wanted, FuncType) and wanted.lasting:
            ty = self._module.types.func_type(ty.params, ty.ret, ty.listable,
                                              ty.hands_out, lasting=True)
        made = builder.make_tuple(
            (builder.code_address(shim, ref.span),
             builder.cast(CastKind.BITCAST, builder.int_const(U64, 0),
                          _ENVIRONMENT, ref.span),
             tables.no_allocator(builder)),
            ty, ref.span)
        if not self._accepts(expected, ty):
            self._report_mismatch(ref.span, ty, expected)
            return UndefConst(ERROR)
        return made

    def _shim_for(self, func: Function, span: Span) -> Function | None:
        """The function that stands between an indirect call and *func*.

        It takes the environment nobody wrote, drops it, and hands the rest on.
        Made on the first mention and kept, so that a name written twice is one
        shim and one symbol.
        """
        found = self._shims.get(id(func))
        if found is not None:
            return found
        if func.attrs.abi is not None and func.attrs.extra.get("variadic"):
            # What such a call takes is not what its type says, so there is no
            # shim that could stand for it.
            self._diags.emit(D.LANG_CALL_NOT_A_FUNCTION, span, name=func.name)
            return None
        name = "".join((SHIM_PREFIX, func.name))
        shim = Function(
            name=name,
            ty=self._module.types.func_type((_ENVIRONMENT, *func.ty.params),
                                            func.ty.ret),
            attrs=FuncAttrs(impure=True), linkage=Linkage.INTERNAL,
            span=func.span, name_span=func.name_span,
            source_path=self._path.as_posix(),
            param_names=("", *func.param_names))
        self._module.add_function(shim, key=self._key(name))
        self._owned.append(shim)
        block = shim.add_block()
        inner = IRBuilder(self._module, shim)
        arriving = [block.add_param(one, "")
                    for one in (_ENVIRONMENT, *func.ty.params)]
        answer = inner.call(func, tuple(arriving[1:]), func.ty.ret, func.span)
        inner.ret(None if func.ty.ret is VOID else answer, func.span)
        self._shims[id(func)] = shim
        self._module.reports.record(
            ReportKind.NAME_LAMBDA, name,
            "".join(("code that hands an indirect call on to '", func.name,
                     "', which the program named where a value was wanted")),
            span)
        return shim

    def _captures_of(self, expr: ast.Lambda, name: str
                     ) -> list[tuple[ast.Capture, _Local]] | None:
        """What the lambda brings in, looked up where the lambda is written."""
        written = (self._everything_reached(expr, name)
                   if expr.brings_in is not None else expr.captures)
        found: list[tuple[ast.Capture, _Local]] = []
        seen: set[str] = set()
        spoiled = False
        for one in written:
            if one.name in seen:
                self._diags.emit(D.LANG_CAPTURE_TWICE, one.span, name=one.name)
                spoiled = True
                continue
            seen.add(one.name)
            local = self._find_local(one.name)
            if local is None:
                self._diags.emit(D.LANG_CAPTURE_UNKNOWN, one.span, name=one.name)
                spoiled = True
                continue
            local.read = True
            found.append((one, local))
        return None if spoiled else found

    def _everything_reached(self, expr: ast.Lambda,
                            name: str) -> tuple[ast.Capture, ...]:
        """What `[=]` or `[&]` brings in: everything the body reaches outside.

        A name the body writes and does not bind for itself, that stands for
        something where the lambda is written.  Its own parameters are not among
        them -- they come from the caller -- and neither is anything it binds
        inside, however often that name is written.

        The order is the order the names are first written, which is the only
        order there is: nothing about a set of names says which comes first, and
        one that changed with the phase of the moon would make two builds of one
        program differ.
        """
        seen: list[str] = []
        _named_in(expr.body, seen)
        inside: set[str] = {one.name for one in expr.params}
        _bound_in(expr.body, inside)
        by_reference = expr.brings_in is ast.CaptureAll.BY_REFERENCE
        found = tuple(
            ast.Capture(span=expr.span, name=name, by_reference=by_reference)
            for name in seen
            if name not in inside and self._find_local(name) is not None)
        # The program wrote `[=]` or `[&]` and not the names, so which names
        # those turned out to be is the compiler's answer and not the
        # program's -- which is what the log is for.
        for one in found:
            self._module.reports.record(
                ReportKind.CAPTURE, one.name,
                "".join(("brought into '", name, "' ",
                         "by reference" if by_reference else "by value",
                         ", which is what '", expr.brings_in.value,
                         "' said of every name its body reaches")),
                expr.span)
        return found

    def _held_by_capture(self, one: ast.Capture, local: _Local) -> Type:
        """What the environment holds for one capture.

        By value, the type the name has: a copy of what it held where the
        lambda was written, so what the lambda answers depends on its
        parameters and on what it was given.  By reference, a reference to it,
        which is the same thing `&` makes anywhere else -- and the name is
        given storage of its own for the same reason it is there.
        """
        held = self._held_by(local)
        if not one.by_reference:
            return held
        if not local.placed:
            self._diags.emit(D.LANG_REF_TYPE_NOT_ALLOWED, one.span,
                             found=held.written())
            return ERROR
        return self._module.types.ptr_type(held, local.mutable)

    def _environment(self, builder: IRBuilder,
                     taken: Sequence[tuple[ast.Capture, _Local]],
                     held: Sequence[Type], span: Span, stays: bool,
                     arena: tuple[Value, frozenset[int]] | None
                     ) -> tuple[Value, tuple[int, ...], Value, frozenset[int]]:
        """Room for what the lambda brings in, filled where it is written.

        In this call's frame where the lambda provably stays in the call, in the
        arena `in` named where the program said, and in `\N{APL FUNCTIONAL SYMBOL QUAD}heap` otherwise.
        Answers the room, where in it each capture is, the allocator it came
        from -- nothing for the frame -- and where the room was made, as keys.
        """
        if not taken:
            # Room for nothing, which is nowhere: one type covers the lambda
            # that brought something in and the one that brought nothing, and
            # this one carries no address and no allocator, nothing being there
            # to read or give back.
            return (builder.cast(CastKind.BITCAST, builder.int_const(U64, 0),
                                 _ENVIRONMENT, span),
                    (), tables.no_allocator(builder), frozenset())
        slots = tuple(self._slot_for(one) for one in held)
        if len(slots) == 1:
            inside: Type = slots[0]
            offsets = (0,)
        else:
            inside = self._module.types.tuple_type(slots)
            offsets = member_offsets_of(inside, _LAYOUT)
        if stays:
            place = builder.frame(inside, span)
            allocator = tables.no_allocator(builder)
            kept: frozenset[int] = frozenset()
        else:
            if arena is not None:
                allocator, kept = arena
            else:
                allocator = self._owner_place(builder, _HEAP, span)
                kept = frozenset({_HEAP})
            place = tables.allocate(
                builder, allocator,
                builder.int_const(U64, max(size_of(inside, _LAYOUT), 1)))
        for at, ((one, local), what) in enumerate(zip(taken, held)):
            value = self._read_capture(builder, local, span)
            if one.by_reference:
                # The place itself.  What may be done to it through the lambda
                # is what may be done to it here, so the address is read as the
                # reference the body will have -- the same bits either way.
                value = (local.value if local.value.ty is what
                         else builder.cast(CastKind.BITCAST, local.value, what,
                                           span))
            self._put_away(builder, place, offsets[at], what, value, span)
        return (place, offsets, allocator, kept)

    def _slot_for(self, what: Type) -> Type:
        """What the environment holds one capture in.

        A value of several parts is kept as its parts, laid out as a tuple of
        them would be: what a store writes and what a load reads is one part
        each, so the room has to be the parts' and not the whole's.
        """
        pieces = parts_of(what)
        return what if len(pieces) == 1 else self._module.types.tuple_type(pieces)

    def _put_away(self, builder: IRBuilder, place: Value, offset: int,
                  what: Type, value: Value, span: Span) -> None:
        """Write one capture into the room kept for it."""
        pieces = parts_of(what)
        if len(pieces) == 1:
            builder.store(self._inside(builder, place, offset, what, span),
                          value, span)
            return
        slot = self._module.types.tuple_type(pieces)
        for at, (part, inner) in enumerate(
                zip(pieces, member_offsets_of(slot, _LAYOUT))):
            builder.store(
                self._inside(builder, place, offset + inner, part, span),
                builder.extract(value, at, part, span), span)

    def _taken_out(self, builder: IRBuilder, place: Value, offset: int,
                   what: Type, span: Span) -> Value:
        """Read one capture back out of the room kept for it."""
        pieces = parts_of(what)
        if len(pieces) == 1:
            return builder.load(self._inside(builder, place, offset, what, span),
                                span)
        slot = self._module.types.tuple_type(pieces)
        return builder.make_tuple(
            [builder.load(self._inside(builder, place, offset + inner, part, span),
                          span)
             for part, inner in zip(pieces, member_offsets_of(slot, _LAYOUT))],
            what, span)

    def _read_capture(self, builder: IRBuilder, local: _Local,
                      span: Span) -> Value:
        """What a name held where the lambda was written."""
        return builder.load(local.value, span) if local.placed else local.value

    def _inside(self, builder: IRBuilder, place: Value, offset: int,
                what: Type, span: Span) -> Value:
        """Where one of the things brought in sits, given where they all are."""
        start = builder.cast(CastKind.BITCAST, place,
                             self._module.types.ptr_type(what, mutable=True), span)
        if offset == 0:
            return start
        return builder.binary(BinOp.ADD, start,
                              builder.int_const(U64, offset), span)

    def _function_of_a_lambda(self, expr: ast.Lambda, params: Sequence[Type],
                              answer: Type, held: Sequence[Type],
                              offsets: Sequence[int],
                              taken: Sequence[tuple[ast.Capture, _Local]],
                              name: str) -> Function | None:
        """The function a lambda's body becomes, checked and lowered.

        It takes one parameter nobody wrote -- where what the lambda brought in
        is -- and then the ones that were written.  The body is checked in a
        scope holding those and nothing else, so a name from around the lambda
        that was not brought in is not a name here at all.
        """
        hands_out = _hands_out_of(expr)
        hidden = (self._module.types.ptr_type(ARENA, mutable=True),) \
            if hands_out is not None else ()
        func = Function(
            name=name,
            ty=self._module.types.func_type((_ENVIRONMENT, *params, *hidden),
                                            answer),
            attrs=FuncAttrs(impure=True), linkage=Linkage.INTERNAL,
            span=expr.span, name_span=expr.span, source_path=self._path.as_posix(),
            param_names=("", *(one.name for one in expr.params)),
            hands_out=hands_out,
            made_in=(HANDS_AT,) if hands_out is not None else None)
        self._module.add_function(func, key=self._key(name))
        # Owned by the file it is written in, as every other definition is: the
        # module's name goes in front of it when the routes to that module are
        # settled, and without that two files each holding a lambda would
        # produce one symbol twice.
        self._owned.append(func)
        block = func.add_block()
        inner = IRBuilder(self._module, func)
        arriving = [block.add_param(one, "")
                    for one in (_ENVIRONMENT, *params)]
        outer = (self._scopes, self._addressed, self._answering, self._impure,
                 self._carried, self._loops, self._outside, self._deferred,
                 self._emptied, self._out_of_arena, self._out_of_arenas,
                 self._answer_made_in, self._made_from, self._answer_kinds,
                 self._thin_to, self._answer_arms, self._yield_answer,
                 self._hands_out, self._hands_out_local, self._receiving)
        self._deferred, self._emptied, self._made_from = [], [], {}
        self._answer_kinds, self._thin_to = [], None
        self._answer_arms = self._yield_answer = None
        self._hands_out = self._hands_out_local = self._receiving = None
        self._out_of_arena, self._out_of_arenas = None, frozenset({_HEAP})
        # A lambda answers in `⎕heap`, saying nothing else, as a function does --
        # or in an arena of its own it hands out, where it says so.
        self._answer_made_in = (frozenset({_HEAP}), HEAP_NAME)
        if hands_out is not None:
            self._answer_made_in = (frozenset(), "".join(("'", hands_out, "'")))
        # A lambda's body binds its own names, so a reference out in the body
        # around it says nothing about a name of the same spelling in here.
        outer_borrows, self._borrows = self._borrows, []
        self._outside = self._outside + self._scopes
        self._scopes, self._addressed = [], set()
        self._answering, self._impure = answer, True
        self._carried, self._loops = set(), []
        self._push_scope()
        if hands_out is not None:
            self._hands_out = (hands_out,
                               block.add_param(hidden[0], "".join(
                                   (hands_out, ".out"))),
                               len(self._scopes))
        try:
            _addressed_in(expr.body, self._addressed)
            brought: list[tuple[ast.Capture, _Local]] = []
            for at, ((one, local), what) in enumerate(zip(taken, held)):
                # What is in the environment is what was put there: the value
                # for a capture by value, and the address of the variable for
                # one by reference -- which the name then stands for, so that
                # reading it reads the variable and writing it writes it.
                inside = self._taken_out(inner, arriving[0], offsets[at], what,
                                         expr.span)
                self._bind_local(one.name, inside, one.span,
                                 mutable=local.mutable if one.by_reference
                                 else False,
                                 builder=inner,
                                 placed_as=(what.pointee
                                            if isinstance(what, PtrType)
                                            else None))
                bound = self._find_local(one.name)
                if bound is not None:
                    brought.append((one, bound))
            for one, value in zip(expr.params, arriving[1:]):
                self._bind_local(one.name, value, one.span, one.mutable,
                                 is_parameter=True, builder=inner)
            # What a lambda is handed, and what it brought in, may have been made
            # anywhere: it carries its allocator, and lasts as long as the call.
            for scope in self._scopes:
                for local in scope.values():
                    held_there = self._held_by(local)
                    if not self._is_arena(local) \
                            and (owned.points(held_there)
                                 or _holds_a_lambda(held_there)):
                        local.arenas = frozenset({id(local)})
                        self._unknown_by_id[id(local)] = local
            self._lower_block(inner, expr.body, func)
            self._all_of_it_used(brought)
            if not inner.is_terminated:
                if answer is VOID:
                    inner.ret()
                else:
                    self._diags.emit(D.LANG_FUNCDEF_RETURN_MISSING, expr.span,
                                     name=name, type=answer.written())
                    # The mistake is reported and the compilation is over, but
                    # what was built has to be well formed all the same: a
                    # function with no terminator is one the verifier would
                    # complain about instead, which would say nothing useful.
                    inner.unreachable(expr.span)
            self._handed_out_made(func, expr)
            self._pop_scope()
        finally:
            (self._scopes, self._addressed, self._answering, self._impure,
             self._carried, self._loops, self._outside, self._deferred,
             self._emptied, self._out_of_arena, self._out_of_arenas,
             self._answer_made_in, self._made_from, self._answer_kinds,
             self._thin_to, self._answer_arms, self._yield_answer,
             self._hands_out, self._hands_out_local, self._receiving) = outer
            self._borrows = outer_borrows
        return func

    def _all_of_it_used(self,
                        brought: Sequence[tuple[ast.Capture, _Local]]) -> None:
        """Report anything the list brought in that the body never reaches.

        A capture list says what a lambda depends on, so a name in it the body
        never reaches is a thing the list says and the lambda does not do.  It
        costs room in what the lambda carries and a copy where it is written,
        and -- worse than either -- it tells a reader the lambda depends on
        something it does not.

        Asked of the binding rather than of the writing, because whether a name
        was read is a thing the scope already knows: it is what the rule about a
        value nothing reads is built on, and asking it twice in two ways would
        be two answers to one question.  Where this reports, that rule is told
        the name was read, so that one mistake is reported once.
        """
        for one, bound in brought:
            if bound.read or bound.written:
                # Written and not read is using it: a name brought in by
                # reference may be brought in *to* be written, which is the
                # whole of what `&` is for.
                continue
            self._diags.emit(D.LANG_CAPTURE_NOT_USED, one.span, name=one.name)
            bound.read = True

    def _lower_deref_assign(self, builder: IRBuilder,
                            stmt: ast.DerefAssign) -> None:
        """Lower `r\N{POSITION INDICATOR} \N{LEFTWARDS ARROW} v`, which writes the place a reference names.

        Assigning to the name binds the name to another place, which is what
        assigning to a name does everywhere else; this writes what is *at* the
        place, and the mark is what says which of the two was meant.
        """
        target = self._lower_expr(builder, stmt.target, None)
        ty = self._value_type_of(target)
        if ty is ERROR:
            return
        if not isinstance(ty, PtrType):
            self._diags.emit(D.LANG_DEREF_NOT_A_REFERENCE, stmt.target.span,
                             found=ty.written())
            return
        if not ty.mutable:
            self._diags.emit(D.LANG_REF_NOT_WRITABLE, stmt.span, found=ty.written())
            return
        if not self._made_here(target):
            # The place may be the caller's -- there is no telling which from
            # the type -- so writing through it is a change that outlives the
            # call unless the storage is this call's own.  That is the rule an
            # array written through already follows, asked here of a reference.
            self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, stmt.span)
        value = self._lower_into(builder, stmt.value, ty.pointee, stmt.span)
        if self._value_type_of(value) is ERROR:
            return
        kept = self._into_a_place(builder, target, stmt.target, stmt.value, value,
                                  ty.pointee, stmt.span, through=True)
        if kept is None:
            return
        value = kept
        if _holds_a_reference(ty.pointee):
            # The place written holds a reference, so the same question is asked
            # of it as of a name: what goes into it has to last as long as it
            # does, and how long that is, is how long what this names does.
            named = self._named_place_of(target)
            if named is None:
                # Nothing here made the place, so it came in from outside and
                # how long it lasts is the caller's business.  A reference of
                # this call's own put there would outlive the call that made
                # it, and neither end can see it: the body does not know how
                # long the place lasts and the caller does not know what the
                # body did with it.  What lasts as long as the program outlives
                # any caller and may go there.
                if not self._lasting(value):
                    self._diags.emit(
                        D.LANG_REF_WRITTEN_INTO_A_PLACE_FROM_OUTSIDE,
                        stmt.span)
                    return
            elif self._outlives_it(value, named.depth,
                                   "".join((_written_as(stmt.target),
                                            DEREF_GLYPH)), stmt.span):
                return
        builder.store(target, value, stmt.span)

    def _lower_deref(self, builder: IRBuilder, expr: ast.Deref,
                     expected: Type | None) -> Value:
        """Lower `r\N{POSITION INDICATOR}`: what is at the place a reference names.

        A cursor is read through the same way, and for the same reason: it says
        where something is, and this is the mark that asks for what is there.
        Where it is is one element of a list, which the walk is at.
        """
        value = self._lower_expr(builder, expr.operand, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        if isinstance(ty, CursorType):
            answer = self._through_a_cursor(builder, value, ty, expr.span)
            if not self._accepts(expected, self._value_type_of(answer)):
                self._report_mismatch(expr.span, self._value_type_of(answer),
                                      expected)
            return answer
        if not isinstance(ty, PtrType):
            self._diags.emit(D.LANG_DEREF_NOT_A_REFERENCE, expr.span,
                             found=ty.written())
            return UndefConst(ERROR)
        answer = builder.load(value, expr.span)
        if not self._accepts(expected, answer.ty):
            self._report_mismatch(expr.span, answer.ty, expected)
        return answer

    def _statement_ended(self) -> None:
        """Drop the references nothing kept, the statement that made them being
        over.

        A reference handed straight to a call is gone when the call is, so
        `bump(&mut n); bump(&mut n)` is two turns and not two references at
        once.  One a name was bound to was promoted when the name was bound and
        lives until its scope does.
        """
        if self._borrows:
            self._borrows = [b for b in self._borrows
                             if b.depth != _UNTIL_THE_STATEMENT_ENDS]

    def _kept_by_a_name(self, ty: Type | None) -> None:
        """Let the references this statement made live as long as this scope.

        Called where a name was bound to something holding one: what keeps a
        reference alive is a name, and until one does the reference is the
        call's own and goes with the statement.
        """
        if ty is None or not _holds_a_reference(ty):
            return
        depth = len(self._scopes) - 1
        for one in self._borrows:
            if one.depth == _UNTIL_THE_STATEMENT_ENDS:
                one.depth = depth

    def _borrowed_name(self, expr: ast.Expr) -> str | None:
        """Which local's place `&expr` reaches, where it reaches one.

        An element is part of the array and a field is part of the record, so
        lending one lends the whole: two elements are two places, but nothing
        here tells one index from another and a promise that depends on
        arithmetic is no promise.  Two fields really are two places and could be
        told apart, which is a finer rule than this one and is written down
        rather than guessed at.  A reference reached through another reference
        belongs to whoever owns that one, and there is nothing here to hold to
        it.
        """
        seen = expr
        while True:
            match seen:
                case ast.NameRef():
                    return (seen.name if self._find_local(seen.name) is not None
                            else None)
                case ast.Element() | ast.Member():
                    seen = seen.base
                case _:
                    return None

    def _lend(self, name: str | None, mutable: bool, span: Span) -> None:
        """Take a reference of *name*'s place, refusing one that clashes.

        A `&mut` is the only reference to its place while it lives and a `&`
        may share with other `&`s, so a second one is refused exactly when
        either of the two may write.
        """
        if name is None:
            return
        for one in self._borrows:
            if one.name != name or not (one.mutable or mutable):
                continue
            self._diags.emit(D.LANG_BORROW_ALIASED, span, name=name) \
                .note(D.LANG_BORROW_LENT_HERE, one.span, name=name)
            return
        self._borrows.append(_Borrow(name, mutable, span,
                                     _UNTIL_THE_STATEMENT_ENDS))

    def _lent_out(self, name: str, span: Span, writing: bool) -> bool:
        """Report using *name*'s own name while a reference to it is out.

        Whoever holds the reference was promised that nothing else changes the
        place, so writing the name is refused whichever kind is out.  Reading it
        is refused only where a `&mut` is out, that one being the only way to
        its place while it lives; a `&` shares, and the name is another sharer.
        """
        for one in self._borrows:
            if one.name != name or not (writing or one.mutable):
                continue
            self._diags.emit(D.LANG_BORROW_NAME_WRITTEN if writing
                             else D.LANG_BORROW_NAME_READ, span, name=name) \
                .note(D.LANG_BORROW_LENT_HERE, one.span, name=name)
            return True
        return False

    def _lower_address(self, builder: IRBuilder, expr: ast.AddressOf,
                       expected: Type | None) -> Value:
        """Lower `&x` and `&mut x`: a reference to the place *x* names.

        Whether the reference may be written through is part of its type, so
        where something says what type is wanted it says that too: `&x` bound
        to a `&mut T` is a reference that may write, exactly as an integer
        literal bound to a `u8` is a `u8`.  `mut` is written where nothing
        says -- a name whose type is read off its value, a `_`, an argument of
        a call being walked -- and says it outright wherever a reader wants it
        said.
        """
        wanted = self._aiming_at(expected)
        writes = expr.mutable or (isinstance(wanted, PtrType) and wanted.mutable)
        # Asked before the place is worked out: working it out reads the name,
        # and a name being lent out is what is being asked about.
        lent = self._borrowed_name(expr.operand)
        self._lend(lent, writes, expr.span)
        outer, self._taking_a_reference = self._taking_a_reference, lent
        try:
            found = self._place_written(builder, expr.operand)
        finally:
            self._taking_a_reference = outer
        if found is None:
            return UndefConst(ERROR)
        address, held, may_change, what, lasting = found
        if not _can_be_referred_to(held):
            self._diags.emit(D.LANG_REF_TYPE_NOT_ALLOWED, expr.span,
                             found=held.written())
            return UndefConst(ERROR)
        if writes and not may_change:
            self._diags.emit(D.LANG_REF_PLACE_NOT_MUTABLE, expr.span, name=what)
            return UndefConst(ERROR)
        ty = self._module.types.ptr_type(held, writes, lasting)
        answer = (address if address.ty is ty
                  else builder.cast(CastKind.BITCAST, address, ty, expr.span))
        if not self._accepts(expected, answer.ty):
            self._report_mismatch(expr.span, answer.ty, expected)
        return answer

    def _place_written(self, builder: IRBuilder, expr: ast.Expr
                       ) -> tuple[Value, Type, bool, str, bool] | None:
        """Where what *expr* names is, what it holds, whether it may be written,
        and what to call it in a message.

        Three things are somewhere: a name, which is storage of its own because
        a reference is taken of it; a variable at the top level, which is an
        address already; and an element of an array, whose elements are a run in
        memory.  Everything else is a value the program worked out, and a value
        is in no particular place.
        """
        match expr:
            case ast.NameRef():
                return self._place_of_a_name(builder, expr)
            case ast.Element():
                return self._place_of_an_element(builder, expr)
            case ast.Member():
                return self._place_of_a_field(builder, expr)
            case ast.Deref():
                # `&r\N{POSITION INDICATOR}` is the place `r` already names, so it is `r` -- with
                # whatever this asks for about writing, which the reference in
                # hand has to allow.
                value = self._lower_expr(builder, expr.operand, None)
                ty = self._value_type_of(value)
                if ty is ERROR:
                    return None
                if not isinstance(ty, PtrType):
                    self._diags.emit(D.LANG_DEREF_NOT_A_REFERENCE, expr.span,
                                     found=ty.written())
                    return None
                return (value, ty.pointee, ty.mutable, "what it names", ty.lasting)
            case _:
                self._diags.emit(D.LANG_REF_NOT_A_PLACE, expr.span)
                return None

    def _lasting(self, value: Value) -> bool:
        """Whether a place reached from *value* is there as long as the program.

        Walked back the way provenance is walked everywhere else: an address
        worked out from a variable at the top level is still that variable's,
        and one worked out from anything else is not.
        """
        seen = value
        while True:
            if isinstance(seen, GlobalVar):
                return True
            if isinstance(seen, AddressInst):
                seen = seen.operands[0]
                continue
            if isinstance(seen, (CastInst, ExtractInst)) or (
                    isinstance(seen, BinaryInst)
                    and seen.op in (BinOp.ADD, BinOp.SUB)):
                seen = seen.operands[0]
                continue
            if isinstance(seen, PtrType):
                return False
            return isinstance(seen.ty, PtrType) and seen.ty.lasting

    def _answers_from(self, func: Function, node: ast.FuncDef,
                      sources: tuple[Value, ...]) -> None:
        """Check that what each `return` hands back really comes from there.

        The promise `from v` is only kept if what comes back was worked out
        from what `v` named; a reference to anything else lives for its own
        time, which is not the one the caller was told.  A reference that
        lasts as long as the program keeps any promise, so it passes too.
        """
        name = ", ".join(node.params[at].name for at in func.borrows_from)
        # Which branches reach each block, so that the walk can go through a
        # block's parameter -- which is what an `if` answering two ways comes
        # to, and what a body of any shape hands back.
        incoming: dict[int, list[object]] = {}
        for block in func.blocks:
            last = block.insts[-1] if block.insts else None
            if isinstance(last, Terminator):
                for arm in last.successors():
                    incoming.setdefault(id(arm.block), []).append(arm)
        for block in func.blocks:
            for inst in block.insts:
                if not isinstance(inst, RetInst) or not inst.operands:
                    continue
                value = inst.operands[0]
                if self._lasting(value) \
                        or _reached_from(value, sources, incoming):
                    continue
                self._diags.emit(D.LANG_BORROW_NOT_FROM_IT, inst.span,
                                 name=name)

    def _as_long_as_given(self, builder: IRBuilder, func: Function,
                          args: Sequence[Value], answer: Value,
                          span: Span) -> Value:
        """What a call answers with lives as long as what it borrowed from.

        The function promised no more than its parameters' lifetime, so the
        rest is worked out here, where both ends are in hand: the answer is
        there as long as the program exactly when every argument was, and as
        long as the shortest-lived of them otherwise.

        The answer is written to the log whichever way it comes out.  It is the
        one thing about such a call that neither the signature nor the call site
        says, and where a name stands on several parameters it is not even a
        thing a reader can work out from one of them.
        """
        carried = func.borrows_from
        if not carried or max(carried) >= len(args) \
                or not isinstance(answer.ty, PtrType):
            return answer
        lasting = all(self._lasting(args[at]) for at in carried)
        self._module.reports.record(ReportKind.LIFETIME, func.name,
                                    self._how_long(func, args, carried, lasting),
                                    span)
        if answer.ty.lasting or not lasting:
            return answer
        return builder.cast(
            CastKind.BITCAST, answer,
            self._module.types.ptr_type(answer.ty.pointee, answer.ty.mutable,
                                        lasting=True), span)

    def _how_long(self, func: Function, args: Sequence[Value],
                  carried: Sequence[int], lasting: bool) -> str:
        """How long the answer of one call lives, in a sentence.

        Which of the arguments decided it is the point: a lifetime name written
        on two parameters means the shorter of what the two named, and which one
        that was is a thing about this call and about no other.
        """
        named = _and_then([func.param_names[at] for at in carried])
        many = len(carried) > 1
        if lasting:
            return "".join((
                "answers with a reference that lasts as long as the program, "
                "because ", "every argument" if many else "the argument",
                " for ", named, " does"))
        found = [one for one in (self._named_place_of(args[at])
                                 for at in carried) if one is not None]
        if not found:
            return "".join((
                "answers with a reference that lives as long as this call, "
                "which is all ", "the arguments" if many else "the argument",
                " for ", named, " said"))
        # Every one of the shortest-lived, not the first of them: two names of
        # one scope live equally long, and saying one of the two would read as
        # though the other had been looked at and turned down.
        deepest = max(one.depth for one in found)
        shortest = _and_then([one.name for one in found
                              if one.depth == deepest])
        return "".join((
            "answers with a reference that lives as long as ", shortest, ", ",
            "the shortest-lived of what the arguments for " if many
            else "which is what the argument for ",
            named, " named"))

    def _named_place_of(self, value: Value) -> _Local | None:
        """The shortest-lived name whose place *value* reaches, where it reaches
        one.

        Walked back the way provenance is walked everywhere else, and through
        the parts of something made of several: a tuple holding a reference
        lasts as long as the shortest-lived thing in it, since taking it apart
        gives that back.  A variable at the top level is nobody's local and
        answers nothing, which is right -- it is there as long as the program
        and outlives every name.
        """
        seen = value
        while True:
            found = self._places.get(id(seen))
            if found is not None:
                return found
            if isinstance(seen, TupleInst):
                deepest: _Local | None = None
                for one in seen.operands:
                    part = self._named_place_of(one)
                    if part is not None and (deepest is None
                                             or part.depth > deepest.depth):
                        deepest = part
                return deepest
            if isinstance(seen, (CastInst, ExtractInst)) or (
                    isinstance(seen, BinaryInst)
                    and seen.op in (BinOp.ADD, BinOp.SUB)):
                seen = seen.operands[0]
                continue
            return None

    def _outlives_it(self, value: Value, depth: int, where: str,
                     span: Span) -> bool:
        """Report a reference given to a name that outlives what it names.

        A reference is only worth having while what it names is still there, and
        a name bound further out lasts longer than a place made further in: the
        place is gone -- out of reach and no longer nameable -- while the name
        that holds the reference is still in hand.  It is the rule a function
        answering with a reference follows, asked of the third place a reference
        can escape to.
        """
        named = self._named_place_of(value)
        if named is None or named.depth <= depth:
            return False
        self._diags.emit(D.LANG_REF_OUTLIVES_PLACE, span,
                         name=where, place=named.name) \
            .note(D.LANG_VARDEF_DEFINED_HERE, named.span, name=named.name)
        return True

    def _place_of_a_name(self, builder: IRBuilder, expr: ast.NameRef
                         ) -> tuple[Value, Type, bool, str, bool] | None:
        """Where the name *expr* is, for a reference being taken of it."""
        local = self._find_local(expr.name)
        if local is not None:
            local.read = True
            if self._notes is not None and local.held is not None:
                self._note(expr.span,
                           PARAMETER if local.is_parameter else VARIABLE,
                           expr.name, local.held.written(), local.span)
            if not local.placed:
                # Every name a reference is taken of anywhere in the body was
                # given storage before the body was walked, so a name that has
                # none is one whose type a place cannot hold.
                self._diags.emit(D.LANG_REF_TYPE_NOT_ALLOWED, expr.span,
                                 found=self._held_by(local).written())
                return None
            assert local.held is not None
            # A name inside a call is gone when the call is, whatever it holds.
            return (local.value, local.held, local.mutable, expr.name, False)
        found = self._provided(expr.name)
        if isinstance(found, GlobalVar):
            if found.value_type is ERROR:
                return None
            # A variable at the top level is there for as long as the program
            # is, so a reference to it is too -- which is the one place a
            # lasting reference comes from.
            return (builder.address(found, expr.span), found.value_type,
                    found.mutable, expr.name, True)
        self._diags.emit(D.LANG_FILESTRUCT_UNDEFINED_NAME, expr.span,
                         name=expr.name)
        return None

    def _exported_variable(self, expr: ast.Member) -> GlobalVar | None:
        """The variable another module exports under this path, or nothing.

        Asked quietly: a path naming anything else is somebody else's to report, and
        what this is for is that such a variable is a *place* -- so a reference may be
        taken of it and a field of it read at an offset, which is what every variable
        at the top level already is.
        """
        if not isinstance(expr.base, ast.NameRef):
            return None
        held = self._top.get(expr.base.name)
        if not isinstance(held, LoadedModule):
            return None
        found = held.exports.get(expr.name)
        return found if isinstance(found, GlobalVar) else None

    def _place_of_a_field(self, builder: IRBuilder, expr: ast.Member
                          ) -> tuple[Value, Type, bool, str, bool] | None:
        """Where one field of a record is, for a reference being taken of it."""
        shared = self._exported_variable(expr)
        if shared is not None:
            # The variable itself and not a field of one: a path whose first part is
            # a module names the variable, and a reference to it is a reference to
            # that place.  It lasts as long as the program does.
            return (builder.address(shared, expr.span), shared.value_type,
                    shared.mutable, expr.name, True)
        found = self._record_at(builder, expr.base)
        if found is None:
            self._diags.emit(D.LANG_REF_NOT_A_PLACE, expr.span)
            return None
        where, ty = found
        at = _field_index(ty, expr.name)
        if at is None:
            self._diags.emit(D.LANG_PRODUCT_NO_SUCH_FIELD, expr.name_span,
                             type=ty.written(), name=expr.name)
            return None
        # A field lives as long as the record does, which is what says how long
        # a reference to it may be held.
        return (self._field_place(builder, where, ty, at, expr.span),
                ty.fields[at][1], True, expr.name, self._lasting(where))

    def _place_of_an_element(self, builder: IRBuilder, expr: ast.Element
                             ) -> tuple[Value, Type, bool, str, bool] | None:
        """Where one element of an array is, for a reference being taken of it."""
        base = self._lower_expr(builder, expr.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return None
        if not isinstance(ty, ArrayType):
            self._diags.emit(D.LANG_ARRAY_NOT_AN_ARRAY, expr.base.span,
                             found=ty.written())
            return None
        if len(expr.indices) != ty.rank:
            # One element, so every index: a run of them is several places and
            # a reference names one.
            self._diags.emit(D.LANG_ARRAY_WRONG_RANK, expr.span,
                             given=len(expr.indices), wanted=ty.rank)
            return None
        start, lengths = self._shape_of(builder, base, ty, expr.span)
        offset = self._offset_of(builder, expr.indices, ty, lengths, expr.span)
        if offset is None:
            return None
        place = self._element_place(builder, start, ty.element, offset, expr.span)
        # An element lives as long as the array does, and an array written at
        # the top level is the one that outlives the call.  One in a part of the
        # image nothing may write may not be written through a reference either.
        return (place, ty.element,
                self._may_write(expr.base) and not self._read_only(base),
                "an element", self._lasting(base))

    def _may_write(self, expr: ast.Expr) -> bool:
        """Whether what *expr* names may be written, asked quietly of its root:
        a name that is `mut`, a reference that is `&mut`."""
        match expr:
            case ast.Member() | ast.Element():
                return self._may_write(expr.base)
            case ast.Deref():
                if isinstance(expr.operand, ast.NameRef):
                    held = self._find_local(expr.operand.name)
                    ty = self._held_by(held) if held is not None else None
                    if isinstance(ty, PtrType):
                        return ty.mutable
                return True
            case ast.NameRef():
                local = self._find_local(expr.name)
                if local is not None:
                    return local.mutable
                found = self._provided(expr.name)
                return not isinstance(found, GlobalVar) or found.mutable
            case ast.ArrayLit():
                return True
        return not _in_the_image(expr)

    def _read_only(self, value: Value) -> bool:
        """Whether *value* is, or is worked out from, a variable of the image
        nothing may write: one that is not `mut`, or what was put `in ⎕static`."""
        seen = value
        while True:
            if isinstance(seen, GlobalVar):
                return not seen.mutable
            if isinstance(seen, AddressInst):
                seen = seen.operands[0]
                continue
            if isinstance(seen, (CastInst, ExtractInst, TupleInst)) or (
                    isinstance(seen, BinaryInst)
                    and seen.op in (BinOp.ADD, BinOp.SUB)):
                seen = seen.operands[0]
                continue
            return False

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
        if _in_the_image(expr):
            return self._lower_static(builder, expr, expr, expected)
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
                                 written[0][0].span, found=keys.written())
                return UndefConst(ERROR)
            # What is written down is fresh and belongs to whoever is given
            # it, so it is the writable type; a name or a parameter that says
            # otherwise takes it as the weaker one, which costs nothing.
            if isinstance(expr, ast.SetLit):
                ty = self._module.types.set_type(keys, True)
            else:
                held = self._same_type(
                    ready.values, [v.span for _, v in written if v is not None],
                    ready.wanted_value)
                if held is ERROR:
                    return UndefConst(ERROR)
                ty = self._module.types.dict_type(keys, held, True)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        if isinstance(ty, DictType) and not _can_be_a_value(ty.value):
            self._diags.emit(D.LANG_COLLECTION_VALUE_TOO_LARGE, expr.span,
                             found=ty.value.written())
            return UndefConst(ERROR)
        # Making one takes room out of an arena, which changes nothing anybody else
        # can see: what is made there is new, and filling the table writes only it.
        return self._build_collection(builder, expr, ty,
                                      self._arena_named(builder, expr.arena),
                                      ready)

    def _arena_named(self, builder: IRBuilder,
                     written: ast.Expr | None) -> Value | None:
        """Where the allocator something was told to come out of keeps its state.

        Nothing where none was named, which is what says to use the one the compiler
        provides.  A name that is not an arena is reported here rather than where the
        thing is made, because what is wrong with it is what it is and not what it is
        being used for.

        **Three things are an arena to name**: a variable at the top level, a name
        inside a function given storage of its own, and a parameter holding a
        reference to one.  All three are a *place*, which is what an arena is: the
        allocator keeps its state there, and what this answers is where.
        """
        if written is None:
            return None
        if isinstance(written, ast.Member):
            # An arena another module exports, reached by the path every other name
            # of a module is reached by: a variable at the top level is a place, and
            # so is one named through the module that wrote it.
            shared = self._exported_variable(written)
            if shared is not None and shared.value_type is ARENA:
                return builder.address(shared, written.span)
        if not isinstance(written, ast.NameRef):
            self._diags.emit(D.LANG_NOT_AN_ARENA, written.span,
                             name=_about_the_shape(written))
            return None
        local = self._find_local(written.name)
        if local is not None:
            held = self._held_by(local)
            if local.placed and held is ARENA:
                local.read = True
                local.written = True
                return local.value
            if isinstance(held, PtrType) and held.pointee is ARENA:
                local.read = True
                return local.value
            self._diags.emit(D.LANG_NOT_AN_ARENA, written.span,
                             name=written.name)
            return None
        found = self._provided(written.name)
        if not (isinstance(found, GlobalVar) and found.value_type is ARENA):
            self._diags.emit(D.LANG_NOT_AN_ARENA, written.span,
                             name=written.name)
            return None
        return builder.address(found, written.span)

    def _build_collection(self, builder: IRBuilder,
                          expr: ast.SetLit | ast.DictLit,
                          ty: SetType | DictType,
                          arena: Value | None,
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
        shape = self._table_shape(ty)
        held = iter(ready.values)
        for at, key in enumerate(ready.keys):
            place = self._put_key(builder, shape, table, key, expr.span)
            if isinstance(ty, DictType):
                builder.store(self._value_place(builder, shape, place, ty.value),
                              next(held), expr.span)
        return table

    def _table_shape(self, ty: SetType | DictType) -> tables.Shape:
        """What the entries of a table of this type are shaped by.

        Asked here rather than worked out at each of the places that needs it:
        where the value of an entry is and which generated function to call are
        two answers to one question, and one wrong answer to it is a table read
        differently by two pieces of the same program.
        """
        if isinstance(ty, SetType):
            return tables.Shape(key=ty.element)
        return tables.Shape(key=ty.key, value=ty.value)

    def _new_table(self, builder: IRBuilder, ty: SetType | DictType,
                   span: Span, arena: Value | None = None,
                   comes_from: Value | None = None) -> Value:
        """Make an empty table of the shape *ty* calls for.

        Out of the arena the program named, or out of the one another table came
        from, or -- where it said nothing -- out of the one the compiler
        provides.  Which of the three it was is settled here so that everything
        that makes a table asks the same question once.
        """
        shape = self._table_shape(ty)
        tables.ensure_runtime(self._module, shape)
        if comes_from is not None:
            place = tables.arena_of(builder, comes_from)
        else:
            if arena is not None:
                place = arena
            else:
                found = self._provided(HEAP_NAME)
                assert isinstance(found, GlobalVar)
                place = builder.address(found, span)
        made = builder.call(self._module.functions[tables.NEW_SYMBOL],
                            (place, builder.int_const(U64, shape.stride)),
                            tables.table_type(self._module), span)
        return builder.cast(CastKind.BITCAST, made, ty, span)

    def _as_held(self, builder: IRBuilder, shape: tables.Shape, key: Value,
                 span: Span) -> Value:
        """A key as the table holds it.

        A key that fits a word is a word, whatever its type says -- a truth value
        and a value of an enumeration are both whole numbers -- so it is widened
        and two keys that are the same number are the same key however narrow
        the type they were written in.  A key that does not fit a word is held as
        what it is and needs no widening.
        """
        if tables.key_ir_type(shape) is not U64:
            return key
        ty = self._value_type_of(key)
        if ty is U64:
            return key
        return builder.cast(CastKind.ZEXT, key, U64, span)

    def _put_key(self, builder: IRBuilder, shape: tables.Shape, table: Value,
                 key: Value, span: Span) -> Value:
        """The entry for *key*, made if the table did not have it."""
        tables.ensure_runtime(self._module, shape)
        return builder.call(
            self._module.functions[tables.named(tables.PUT_SYMBOL, shape)],
            (builder.cast(CastKind.BITCAST, table,
                          tables.table_type(self._module), span),
             self._as_held(builder, shape, key, span)),
            tables.table_type(self._module), span)

    def _find_key(self, builder: IRBuilder, shape: tables.Shape, table: Value,
                  key: Value, span: Span) -> Value:
        """The entry the key is in, or the one it would go in."""
        tables.ensure_runtime(self._module, shape)
        return builder.call(
            self._module.functions[tables.named(tables.SLOT_SYMBOL, shape)],
            (builder.cast(CastKind.BITCAST, table,
                          tables.table_type(self._module), span),
             self._as_held(builder, shape, key, span)),
            tables.table_type(self._module), span)

    def _is_live(self, builder: IRBuilder, place: Value, span: Span) -> Value:
        """Whether an entry holds a key rather than standing empty."""
        return builder.compare(CmpPred.EQ, builder.load(place, span),
                               builder.int_const(U64, tables.LIVE), span)

    def _value_place(self, builder: IRBuilder, shape: tables.Shape,
                     place: Value, ty: Type) -> Value:
        """Where in an entry the value belonging to its key is."""
        word = builder.binary(BinOp.ADD, place,
                              builder.int_const(U64, shape.value_at))
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

        Where nothing outside says what they are, what one of them says is what
        they all are: they hold one type by definition, so one entry saying
        which says it for the rest.  That is read off the writing before any of
        it is lowered, which is what lets an entry take its type from one
        written after it.
        """
        aim = wanted if wanted is not None else self._said_by(written)
        values = [self._lower_expr(builder, entry, _taken_from(entry, aim))
                  for entry in self._nothing_expected(written)]
        if into is not None:
            into.extend(values)
        return self._same_type(values, [entry.span for entry in written], wanted)

    def _said_by(self, written: Sequence[ast.Expr]) -> Type | None:
        """The type the entries of a literal say they hold, where one says.

        An array, a list and a collection hold one type, so an entry that says
        what it is says it for every other -- wherever that entry stands, and
        however deep the writing goes.  `\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1, 2u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` therefore means what
        `\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1u8, 2\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` means, and a row of a table may take its type from a row
        written after it.

        Only what an entry says on its own is read here, which is a literal
        carrying a suffix.  An entry whose type is known only once it has been
        lowered -- a name, a call -- is left to the lowering, where the first
        one of them settles it for the rest as it always did.
        """
        for one in written:
            found = (self._said_by(one.elements)
                     if isinstance(one, (ast.ArrayLit, ast.ListLit))
                     else self._type_of(one) if _says_its_type(one) else None)
            if found is not None:
                return found
        return None

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
                                 found=ty.written(), expected=found.written())
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
        # What one entry says is what they all are: a collection holds one type
        # of key and one of value, so one entry saying which says it for the
        # rest wherever it stands, and what the collection was declared to hold
        # says it where no entry does.
        said_key = self._said_by([key for key, _ in written]) or wanted_key
        said_value = self._said_by(
            [v for _, v in written if v is not None]) or wanted_value
        keys: list[Value] = []
        values: list[Value] = []
        outer = self._initializing, self._assigning
        self._initializing, self._assigning = None, None
        try:
            for key, value in written:
                keys.append(self._lower_expr(builder, key, _taken_from(key, said_key)))
                if value is not None:
                    values.append(self._lower_expr(
                        builder, value, _taken_from(value, said_value)))
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
        found = self._paired(builder, expr.base, TABLE_PAIR, (expr.key,),
                             expr.span, expected)
        if found is not None:
            return found
        base = self._lower_expr(builder, expr.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, (SetType, DictType)):
            self._diags.emit(D.LANG_INDEX_NOT_A_COLLECTION, expr.base.span,
                             found=ty.written())
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
        shape = self._table_shape(ty)
        place = self._find_key(builder, shape, base, key, expr.span)
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
            builder.load(self._value_place(builder, shape, place, ty.value),
                         expr.span),
            missing, answer, expr.span)

    def _lower_take(self, builder: IRBuilder, expr: ast.Take,
                    expected: Type | None) -> Value:
        """Check `†c⸨k⸩`, which takes a key out and answers what was there.

        The same answer the lookup it is written before gives: what the key
        stood for, or whether it was there at all.  So it is read the same way,
        and a program that only wants the key gone need read nothing.

        The entry is found once and used twice -- read, then given up -- which
        is why this is not the lookup and the taking-out written one after the
        other: two probes would be two walks for one question.
        """
        base = self._lower_expr(builder, expr.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, (SetType, DictType)):
            self._diags.emit(D.LANG_INDEX_NOT_A_COLLECTION, expr.base.span,
                             found=ty.written())
            return UndefConst(ERROR)
        if not ty.mutable:
            self._diags.emit(D.LANG_COLLECTION_NOT_WRITABLE, expr.base.span,
                             found=ty.written())
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
        self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, expr.span)
        shape = self._table_shape(ty)
        tables.ensure_take(self._module, shape)
        place = self._find_key(builder, shape, base, key, expr.span)
        held = builder.load(place, expr.span)
        # What was there is read before the entry is given up, because giving it
        # up is what makes the entry free for the next key put in that path.
        found = (None if isinstance(ty, SetType)
                 else builder.load(
                     self._value_place(builder, shape, place, ty.value),
                     expr.span))
        builder.call(self._module.functions[tables.GIVE_UP_SYMBOL],
                     (builder.cast(CastKind.BITCAST, base,
                                   tables.table_type(self._module), expr.span),
                      place), VOID, expr.span)
        if found is None:
            return builder.compare(CmpPred.EQ, held,
                                   builder.int_const(U64, tables.LIVE),
                                   expr.span)
        return builder.wrap(
            found,
            builder.compare(CmpPred.NE, held,
                            builder.int_const(U64, tables.LIVE), expr.span),
            answer, expr.span)

    # -- lists, and the cursors that walk them ---------------------------------

    def _list_place(self, builder: IRBuilder, expr: ast.Expr, span: Span
                    ) -> tuple[Value, ListType] | None:
        """The place a list is in, for something that is about to shorten it.

        A list is where its elements are and how many there are, so a list with
        one element fewer is a different pair of words: taking one out has to
        put what is left back where the list was.  That is a place -- a name, a
        field, an element -- and a list that was worked out has none.
        """
        if not _names_a_place(expr):
            self._diags.emit(D.LANG_TAKE_NOT_A_PLACE, span)
            return None
        found = self._place_written(builder, expr)
        if found is None:
            return None
        address, held, may_change, what, _ = found
        if held is ERROR:
            return None
        if not isinstance(held, ListType):
            self._diags.emit(D.LANG_TAKE_NOT_A_CURSOR, span,
                             found=held.written())
            return None
        if not may_change:
            self._diags.emit(D.LANG_VARDEF_NOT_MUTABLE, span, name=what)
            return None
        return (builder.cast(CastKind.BITCAST, address,
                             self._module.types.ptr_type(held, mutable=True),
                             span), held)

    def _list_at(self, builder: IRBuilder, place: Value, ty: ListType,
                 span: Span) -> tuple[Value, Value, Value]:
        """The list in a place, taken apart: where, how many, and the list."""
        held = builder.load(place, span)
        return (builder.extract(held, 0, parts_of(ty)[0], span),
                builder.extract(held, 1, U64, span), held)

    def _erased(self, builder: IRBuilder, place: Value, ty: ListType,
                elements: Value, count: Value, at: Value, span: Span) -> None:
        """Move what follows one element down, and write the shorter list back."""
        stride = stride_of(ty.element, _LAYOUT)
        builder.call(lists.erase_function(self._module),
                     (builder.cast(CastKind.BITCAST, elements,
                                   self._module.types.ptr_type(U8, mutable=True),
                                   span),
                      builder.int_const(U64, stride), count, at),
                     VOID, span)
        held = builder.load(place, span)
        builder.store(place, builder.make_tuple(
            (elements, builder.binary(BinOp.WRAP_SUB, count,
                                      builder.int_const(U64, 1), span),
             builder.extract(held, 2, parts_of(ty)[2], span)),
            ty, span), span)

    def _element_of(self, builder: IRBuilder, elements: Value, ty: ListType,
                    at: Value, span: Span) -> Value:
        """The element *at* places along, read where the elements are."""
        stride = stride_of(ty.element, _LAYOUT)
        return builder.load(builder.binary(
            BinOp.ADD, elements,
            builder.binary(BinOp.WRAP_MUL, at, builder.int_const(U64, stride),
                           span), span), span)

    def _lower_take_at(self, builder: IRBuilder, expr: ast.TakeAt,
                       expected: Type | None) -> Value:
        """Check `†l⟦i⟧`, which takes one element out and answers what it was.

        What follows the element moves down into its place and the list is one
        shorter; an index past the end stops the program, which is what an index
        past the end of an array does -- one rule for indexing, whatever is
        indexed.
        """
        found = self._list_place(builder, expr.base, expr.base.span)
        if found is None:
            return UndefConst(ERROR)
        place, ty = found
        at = self._index_into(builder, expr.index, expr.span)
        if at is None:
            return UndefConst(ERROR)
        if not self._accepts(expected, ty.element):
            self._report_mismatch(expr.span, ty.element, expected)
            return UndefConst(ERROR)
        self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, expr.span)
        elements, count, _ = self._list_at(builder, place, ty, expr.span)
        builder.check(builder.compare(CmpPred.ULT, at, count, expr.span),
                      "an index outside its list",
                      statuses.OUT_OF_RANGE, expr.span)
        gone = self._element_of(builder, elements, ty, at, expr.span)
        self._erased(builder, place, ty, elements, count, at, expr.span)
        return gone

    def _index_into(self, builder: IRBuilder, written: ast.Expr,
                    span: Span) -> Value | None:
        """An index written for a list, read as the count it is.

        The same rule an array's index follows: which element is wanted is not
        a length and not a count of apples, so anything that is not a literal
        says what it counts.
        """
        index = self._on_its_own(builder, written)
        found = self._value_type_of(index)
        if found is ERROR:
            return None
        if not isinstance(found, IntType):
            self._diags.emit(D.LANG_ARRAY_INDEX_NOT_A_NUMBER, written.span,
                             found=found.written())
            return None
        if not isinstance(written, ast.IntLit) \
                and not self._stands_for(found.unit, IDX_UNIT):
            self._diags.emit(D.LANG_UNIT_INDEX, written.span,
                             found=found.written())
            return None
        return self._as_count(builder, index, found, span)

    def _cursor_parts(self, builder: IRBuilder, value: Value, ty: CursorType,
                      span: Span) -> tuple[Value, Value, ListType]:
        """A cursor taken apart: the place the list is in, and how far along."""
        held = self._module.types.list_type(ty.element)
        return (builder.extract(value, 0,
                                self._module.types.ptr_type(held, mutable=True),
                                span),
                builder.extract(value, 1, U64, span), held)

    def _lower_iter(self, builder: IRBuilder, expr: ast.Call,
                    expected: Type | None) -> Value:
        """Lower `⎕iter(l)`: where a walk over the list in *l* begins.

        It is given the place and not the list, because a walk may take an
        element out: what is left has to go back where the one holding the list
        will read it.  So what may be walked is what may be written.
        """
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=ITER_NAME, expected=1, found=len(expr.args))
            return UndefConst(ERROR)
        if not _names_a_place(expr.args[0]):
            self._diags.emit(D.LANG_TAKE_NOT_A_PLACE, expr.args[0].span)
            return UndefConst(ERROR)
        found = self._place_written(builder, expr.args[0])
        if found is None:
            return UndefConst(ERROR)
        address, held, may_change, what, _ = found
        if held is ERROR:
            return UndefConst(ERROR)
        if not isinstance(held, ListType):
            self._diags.emit(D.LANG_CURSOR_NOT_A_LIST, expr.args[0].span,
                             found=held.written())
            return UndefConst(ERROR)
        if not may_change:
            self._diags.emit(D.LANG_VARDEF_NOT_MUTABLE, expr.args[0].span,
                             name=what)
            return UndefConst(ERROR)
        ty = self._module.types.cursor_type(held.element)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        return builder.make_tuple(
            (builder.cast(CastKind.BITCAST, address,
                          self._module.types.ptr_type(held, mutable=True),
                          expr.span),
             builder.int_const(U64, 0)), ty, expr.span)

    def _lower_step(self, builder: IRBuilder, expr: ast.Unary, value: Value,
                    ty: CursorType, expected: Type | None) -> Value:
        """Lower `⇧it` and `⇩it`: the cursor at the next element, and at the
        one before.

        A walk that stepped off either end of its list stops the program.  There
        is nowhere for such a cursor to point and no answer to give instead: a
        cursor is not a result, and making it one would put a `??` on every step
        of every walk.
        """
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        place, at, held = self._cursor_parts(builder, value, ty, expr.span)
        onward = expr.op is ast.UnaryOp.NEXT
        if onward:
            _, count, _ = self._list_at(builder, place, held, expr.span)
            builder.check(builder.compare(CmpPred.ULT, at, count, expr.span),
                          "a walk moved past the end of its list",
                          statuses.WALK_ENDED, expr.span)
        else:
            builder.check(
                builder.compare(CmpPred.NE, at, builder.int_const(U64, 0),
                                expr.span),
                "a walk moved before the start of its list",
                statuses.WALK_ENDED, expr.span)
        return builder.make_tuple(
            (place, builder.binary(BinOp.WRAP_ADD if onward else BinOp.WRAP_SUB,
                                   at, builder.int_const(U64, 1), expr.span)),
            ty, expr.span)

    def _lower_take_through(self, builder: IRBuilder, expr: ast.TakeThrough,
                            expected: Type | None) -> Value:
        """Check `†it`: the element the cursor is at goes, and the cursor is
        answered with.

        Which is the same cursor: what followed has moved down into the place
        the element left, so the walk goes on from where it was -- and where
        what went was the last element, the cursor is now one past the end,
        which is what a walk that is over looks like.
        """
        value = self._lower_expr(builder, expr.operand, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, CursorType):
            self._diags.emit(D.LANG_TAKE_NOT_A_CURSOR, expr.operand.span,
                             found=ty.written())
            return UndefConst(ERROR)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, expr.span)
        place, at, held = self._cursor_parts(builder, value, ty, expr.span)
        elements, count, _ = self._list_at(builder, place, held, expr.span)
        builder.check(builder.compare(CmpPred.ULT, at, count, expr.span),
                      "taking an element out through a walk that is over",
                      statuses.WALK_ENDED, expr.span)
        self._erased(builder, place, held, elements, count, at, expr.span)
        return value

    def _through_a_cursor(self, builder: IRBuilder, value: Value,
                          ty: CursorType, span: Span) -> Value:
        """`it⌖`: the element a cursor is at.

        A cursor past the last element has none, and reading through one stops
        the program the way reading past the end of an array does.
        """
        place, at, held = self._cursor_parts(builder, value, ty, span)
        elements, count, _ = self._list_at(builder, place, held, span)
        builder.check(builder.compare(CmpPred.ULT, at, count, span),
                      "reading through a walk that is over",
                      statuses.WALK_ENDED, span)
        return self._element_of(builder, elements, held, at, span)

    def _asked(self, builder: IRBuilder, value: Value, span: Span) -> Value:
        """What a condition comes to, where what was written is a cursor.

        A cursor standing where a truth value is wanted asks the one question
        anything walking a list asks of it: is the walk over.  That is what
        `unless` is for -- `unless it:` runs the body until the walk is -- and
        it is why the answer is that way round rather than the other.
        """
        ty = self._value_type_of(value)
        if not isinstance(ty, CursorType):
            return value
        place, at, held = self._cursor_parts(builder, value, ty, span)
        _, count, _ = self._list_at(builder, place, held, span)
        return builder.compare(CmpPred.UGE, at, count, span)

    def _lower_entry_assign(self, builder: IRBuilder, stmt: ast.EntryAssign) -> None:
        """Check `d⸨k⸩ ← v`, which puts a value under a key."""
        base = self._lower_expr(builder, stmt.base, None)
        ty = self._value_type_of(base)
        if ty is ERROR:
            return
        if not isinstance(ty, DictType):
            self._diags.emit(D.LANG_ENTRY_ASSIGN_NOT_A_DICT, stmt.base.span,
                             found=ty.written())
            return
        if not ty.mutable:
            self._diags.emit(D.LANG_COLLECTION_NOT_WRITABLE, stmt.base.span,
                             found=ty.written())
            return
        key = self._lower_expr(builder, stmt.key, ty.key)
        value = self._lower_into(builder, stmt.value, ty.value, stmt.span)
        if self._value_type_of(key) is ERROR or self._value_type_of(value) is ERROR:
            return
        self._an_effect(D.LANG_PURE_WRITES_ELSEWHERE, stmt.span)
        shape = self._table_shape(ty)
        place = self._put_key(builder, shape, base, key, stmt.span)
        builder.store(self._value_place(builder, shape, place, ty.value), value,
                      stmt.span)

    # -- if --------------------------------------------------------------------

    def _settled(self, condition: ast.Expr) -> bool | None:
        """What a condition written after `comptime` comes to, or nothing where
        it is not a question the compiler can settle.

        Nothing here is lowered.  That is the whole point: a question the
        compiler answers leaves nothing behind for the program to ask, so the
        condition never becomes a value and `\N{APL FUNCTIONAL SYMBOL QUAD}typeof` never has to have a
        representation.  What may be asked is whether two types are the one
        type, and those answers joined the way truth values are joined.
        """
        match condition:
            case ast.BoolLit():
                return condition.value
            case ast.Unary() if condition.op is ast.UnaryOp.LOGIC_NOT:
                found = self._settled(condition.operand)
                return None if found is None else not found
            case ast.Binary() if condition.op in (ast.BinaryOp.LOGIC_AND,
                                                  ast.BinaryOp.SHORT_AND):
                return self._both_settled(condition, all)
            case ast.Binary() if condition.op in (ast.BinaryOp.LOGIC_OR,
                                                  ast.BinaryOp.SHORT_OR):
                return self._both_settled(condition, any)
            case ast.Binary() if condition.op in (ast.BinaryOp.EQUAL,
                                                  ast.BinaryOp.NOT_EQUAL):
                left = self._type_stood_for(condition.left)
                right = self._type_stood_for(condition.right)
                if left is None or right is None:
                    return None
                alike = left is right
                return alike if condition.op is ast.BinaryOp.EQUAL else not alike
            case ast.Call() if isinstance(condition.callee, ast.NameRef) \
                    and condition.callee.name == IS_RECORD_NAME:
                return self._settled_is_record(condition)
            case _:
                return None

    def _settled_is_record(self, call: ast.Call) -> bool | None:
        """Whether `⎕isrecord` was asked of a record.

        A truth the compiler settles and not a value, so it stands in a condition
        `comptime` was written on and nowhere else.  What it is for is the one
        thing a generic definition cannot otherwise ask: whether the type it was
        given has fields to walk, there being no list of every record to compare
        against the way there is a list of the language's own types.
        """
        if len(call.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, call.span,
                             name=IS_RECORD_NAME, expected=1,
                             found=len(call.args))
            return None
        found = self._type_stood_for(call.args[0])
        if found is None:
            return None
        return isinstance(found, ProductType)

    def _both_settled(self, condition: ast.Binary,
                      joined: object) -> bool | None:
        """Both sides of a logical operator, where both are settled."""
        sides = [self._settled(condition.left), self._settled(condition.right)]
        if any(side is None for side in sides):
            return None
        return bool(joined(sides))  # type: ignore[operator]

    def _type_stood_for(self, expr: ast.Expr) -> Type | None:
        """The type an operand of a compile-time comparison stands for.

        Two things stand for one: a type lifted out of the program, and
        `\N{APL FUNCTIONAL SYMBOL QUAD}typeof` of a lifted expression, which is that expression's type.
        Neither is lowered -- a type is not a value, and what makes that
        bearable is that the only place either may stand is a question the
        compiler answers.
        """
        match expr:
            case ast.Call() if isinstance(expr.callee, ast.NameRef) \
                    and expr.callee.name == TYPEOF_NAME:
                if len(expr.args) != 1:
                    return None
                return self._type_of_a_lift(expr.args[0])
            case ast.Lifted():
                found = self._lifted_type(expr)
                return found
            case _:
                return None

    def _lifted_type(self, expr: ast.Lifted) -> Type | None:
        """The type `\N{TOP LEFT CORNER}\N{HORIZONTAL ELLIPSIS}\N{TOP RIGHT CORNER}` lifted, or nothing where it lifted an expression.

        A bare name is read as a type by the parser and may turn out to be a
        value, which is the one thing left for here to settle: a name that is
        not a type is a name of something, and what was lifted is that.
        """
        if expr.written is None:
            return None
        if isinstance(expr.written, ast.TypeRef) and expr.written.module is None \
                and not expr.written.result \
                and self._find_local(expr.written.name) is not None:
            # A local by that name, so what stands there is the value and not a
            # type of the same spelling.
            return None
        found = self._resolve_type(expr.written)
        return None if found is ERROR else found

    def _type_of_a_lift(self, asked: ast.Expr) -> Type | None:
        """The type of what a lift lifted, which is what `\N{APL FUNCTIONAL SYMBOL QUAD}typeof` answers.

        Its argument is a lift and nothing else.  Written without the brackets
        the argument would be an ordinary expression, and a type's name and a
        value's name being both identifiers, what it meant would depend on what
        the names turned out to be -- which is the thing the brackets are there
        to stop.
        """
        if not isinstance(asked, ast.Lifted):
            self._diags.emit(D.LANG_TYPEOF_TAKES_A_LIFT, asked.span)
            return None
        lifted = self._lifted_type(asked)
        if lifted is not None:
            # A type was lifted, so its own type is being asked for, and a type
            # is not a thing this language has a type of.
            self._diags.emit(D.LANG_TYPEOF_OF_A_TYPE, asked.span,
                             found=lifted.written())
            return None
        written = asked.value if asked.value is not None else \
            (ast.NameRef(span=asked.span, name=asked.written.name)
             if isinstance(asked.written, ast.TypeRef) else None)
        if written is None:
            return None
        if isinstance(written, ast.NameRef):
            # Asking what a name is of is reading it, as far as a reader is
            # concerned: the program mentioned it and the compiler used it.
            # What it did not do is lower it, which is why nothing of the value
            # reaches the program.
            local = self._find_local(written.name)
            if local is not None:
                local.read = True
        found = self._hint_of(written)
        return None if found is ERROR else found

    def _chosen_arms(self, stmt: ast.If) -> tuple[ast.IfArm, ...] | None:
        """The arms left once every one the compiler settled has been answered.

        An arm it settled as true is the whole of the `if` -- what follows it
        cannot be reached -- and one it settled as false is not there at all.
        Neither leaves a test in the program: that is what `comptime` says, and
        it is why the arms it removes may be of types that do not agree with the
        ones that stay.
        """
        found: list[ast.IfArm] = []
        for arm in stmt.arms:
            if not arm.comptime or arm.condition is None:
                found.append(arm)
                continue
            holds = self._settled(arm.condition)
            if holds is None:
                self._diags.emit(D.LANG_COMPTIME_NOT_SETTLED, arm.condition.span)
                return None
            if holds:
                # The arm is taken, so what it does is what the whole `if` does.
                found.append(replace(arm, condition=None, comptime=False))
                break
        return tuple(found)

    def _lower_scoped(self, builder: IRBuilder, expr: ast.Scoped,
                      wanted: Type | None) -> Value:
        """Lower statements a macro wrote where a value stands.

        A scope of their own and a block whose last statement is the value: what
        they bound is gone and what they put off has run when the value is used.
        """
        self._push_scope()
        try:
            found = self._lower_block(builder, expr.body, builder.function,
                                      as_result=False, wanted=wanted,
                                      produces=True)
        finally:
            self._pop_scope()
        return found if found is not None else UndefConst(ERROR)

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
        if any(arm.comptime for arm in stmt.arms):
            arms = self._chosen_arms(stmt)
            if arms is None:
                return UndefConst(ERROR)
            if not arms:
                # Every arm the compiler settled was false and there was no
                # `else`: the `if` does nothing, which is a thing to do.
                return UndefConst(VOID) if not produces else UndefConst(ERROR)
            written = stmt
            if len(arms) == 1 and arms[0].condition is None:
                found = self._lower_block(builder, arms[0].body, func,
                                          as_result=produces, wanted=wanted,
                                          produces=produces)
                # What the `if` comes to is what the arm taken comes to: an arm
                # not taken was never this type's, and what it names may be
                # another instantiation's.
                chosen = arms[0].body.stmts
                self._made_from[id(written)] = (written, (
                    self._arenas_in(chosen[-1].value)
                    if chosen and isinstance(chosen[-1], ast.ExprStmt)
                    else frozenset()))
                return found if found is not None else UndefConst(VOID)
            stmt = replace(stmt, arms=arms)
            # What the arms left come to, which the lowering below records for the
            # `if` it lowers -- the narrowed one, which no other node is.
            self._made_alias[id(written)] = (written, stmt)
        last = stmt.arms[-1]
        has_else = last.condition is None
        if produces and not has_else:
            self._diags.emit(D.LANG_IF_NEEDS_AN_ELSE, stmt.span)
            return UndefConst(ERROR)
        asked = [arm for arm in stmt.arms if arm.condition is not None]
        blocks = [builder.new_block("then") for _ in asked]
        # One per condition: the token current where that condition branched,
        # which is what the arm it branches to begins with.
        asked_memory: list[Value] = []
        otherwise = builder.new_block("otherwise") if has_else else None
        spoiled = False
        for index, arm in enumerate(asked):
            assert arm.condition is not None
            # Lowered with nothing expected of it, so that a condition of the
            # wrong type is reported once, as a condition, rather than by
            # whatever wording the place it stands in would have used.
            condition = self._asked(builder, self._lower_expr(
                builder, arm.condition, None), arm.condition.span)
            found = self._value_type_of(condition)
            if found is not BOOL and found is not ERROR:
                self._diags.emit(D.LANG_IF_CONDITION_NOT_BOOLEAN,
                                 arm.condition.span, found=found.written())
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
            asked_memory.append(builder.memory())
            builder.position_at(following)
        if spoiled:
            return UndefConst(ERROR)
        plan = [_ArmPlan(body=arm.body, block=block, memory=token)
                for arm, block, token in zip(asked, blocks, asked_memory)]
        # What falls past every condition begins with the last one's token.
        after = asked_memory[-1] if asked_memory else builder.memory()
        if has_else:
            assert otherwise is not None
            plan.append(_ArmPlan(body=last.body, block=otherwise,
                                 memory=after))
            return self._run_arms(builder, stmt, func, plan, wanted, produces)
        return self._run_arms(builder, stmt, func, plan, wanted, produces,
                              otherwise=otherwise, otherwise_memory=after)

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
        condition = self._asked(builder, self._lower_expr(
            builder, stmt.condition, None), stmt.condition.span)
        found = self._value_type_of(condition)
        if found is not BOOL:
            if found is not ERROR:
                self._diags.emit(D.LANG_LOOP_CONDITION_NOT_BOOLEAN,
                                 stmt.condition.span, found=found.written())
            condition = UndefConst(BOOL)
        elif stmt.until:
            # `unless` is this loop with its condition read the other way
            # round: the body runs until it holds.
            condition = self._negate(builder, condition, stmt.span)
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
        head = self._a_turn_begins(stmt.body)
        self._lower_block(builder, stmt.body, func, as_result=False)
        end = self._provenance() if not builder.is_terminated else None
        self._end_loop(label)
        self._pop_scope()
        self._after_the_turns(head, end, one)
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

        Five things are iterators -- a range, an array, a list, a string and a
        table -- and none of them is called: each one's `next` is lowered where
        it is asked, which for a range is a comparison and an addition, for an
        array a comparison and a read, and for a table a walk that steps past
        the places holding nothing.  What they have in common is
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
        if stmt.comptime:
            return self._written_out(builder, stmt, func, expected, produces)
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
        self._taken_out_of(stmt.iterable, (stmt.name, *(n for n, _ in stmt.more)))
        one = self._begin_loop(label, header, after, carried, state, found.step,
                               produces, produces and stmt.alternative is None,
                               handing)
        head = self._a_turn_begins(stmt.body)
        self._lower_block(builder, stmt.body, func, as_result=False)
        end = self._provenance() if not builder.is_terminated else None
        self._end_loop(label)
        self._pop_scope()
        self._after_the_turns(head, end, one)
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
                    answers=answers, wraps=wraps, handing=handing,
                    frame=len(self._deferred))
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
        # Leaving every block opened since the loop began, so what they put off is
        # done now -- after what is handed over was worked out, as a return's is.
        kept = self._on_the_way_out()
        self._run_deferred(builder, self._demanding[1], found.frame)
        # What holds here holds after the loop, which this way reaches.
        found.left_with.append(self._provenance())
        self._left(kept)
        if builder.is_terminated:
            return
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
        # The next turn begins with the body left, which runs what it put off.
        kept = self._on_the_way_out()
        self._run_deferred(builder, self._demanding[1], found.frame)
        self._left(kept)
        if builder.is_terminated:
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
                UndefConst(handing), builder.bool_const(True), answer, stmt.span,
                None if answer.err is None else UndefConst(answer.err))
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
                         value_span=stmt.iterable.span, builder=builder)

    # -- what a loop can take its values from ----------------------------------

    def _written_out(self, builder: IRBuilder, stmt: ast.ForEach,
                     func: Function, expected: Type | None,
                     produces: bool) -> Value:
        """Lower `comptime foreach`, which is the body written out once per turn.

        A tuple's members are of whatever types they were written with, so a
        loop over one cannot be one body run again: the name would have to be of
        one type and there is no one type.  Written out there is no such
        problem -- each body is lowered on its own, with the name standing for
        that member, and what the name is of is what that member is of.

        There is no loop in what comes out.  No counter, no branch backwards, no
        test: the turns are taken here and what is left is the bodies, one after
        another.  That is why it is the one loop whose turns may differ, and it
        is also why it has nothing to hand over -- a `break` needs somewhere to
        jump to and there is nowhere.
        """
        value = self._lower_expr(builder, stmt.iterable, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, TupleType):
            self._diags.emit(D.LANG_COMPTIME_NEEDS_A_TUPLE, stmt.iterable.span,
                             found=ty.written())
            return UndefConst(ERROR)
        if produces or stmt.alternative is not None or stmt.label is not None:
            self._diags.emit(D.LANG_COMPTIME_NEEDS_A_TUPLE, stmt.span,
                             found=ty.written())
            return UndefConst(ERROR)
        declared = self._resolve_type(stmt.type) if stmt.type is not None else None
        # A member is one value to the program and may be several to the machine,
        # so which leaves belong to it is asked rather than assumed: a member that
        # is itself a tuple, or holds a string, is more than one.
        inside = parts_within(ty)
        held = parts_of(ty)
        for at, member in enumerate(ty.members):
            if declared is not None and declared is not member:
                self._report_mismatch(stmt.iterable.span, member, declared)
                return UndefConst(ERROR)
            first, count = inside[at]
            turn = builder.whole(
                [builder.extract(value, first + which, held[first + which],
                                 stmt.span) for which in range(count)],
                member, stmt.span)
            self._push_scope()
            self._bind_local(stmt.name, turn, stmt.name_span, builder=builder)
            self._lower_block(builder, stmt.body, func, as_result=False)
            self._pop_scope()
        return UndefConst(VOID)

    def _iteration_over(self, builder: IRBuilder,
                        stmt: ast.ForEach) -> _Iteration | None:
        """What the loop's expression turns out to be, as a thing to walk.

        Two things are recognised before anything is lowered, and for the same
        reason: neither has a type of its own.  A range is written where it is
        used, and `\N{APL FUNCTIONAL SYMBOL QUAD}enumerate` makes an iterator out of another one.  Everything
        else is a value, and what it is, is its type's business.
        """
        counted = self._enumerating(stmt.iterable)
        if counted is not None:
            return self._enumerated(builder, stmt, counted)
        return self._iteration_of(builder, stmt)

    def _enumerating(self, written: ast.Expr
                     ) -> tuple[ast.Expr, ast.Expr | None] | None:
        """What `\N{APL FUNCTIONAL SYMBOL QUAD}enumerate` was asked to walk and what to count from, or
        nothing where the loop's expression is not one of those."""
        if not (isinstance(written, ast.Call)
                and isinstance(written.callee, ast.NameRef)
                and written.callee.name == ENUMERATE_NAME):
            return None
        if not 1 <= len(written.args) <= 2:
            self._diags.emit(D.LANG_ENUMERATE_TAKES_ONE_OR_TWO, written.span,
                             found=str(len(written.args)))
            return None
        return (written.args[0],
                written.args[1] if len(written.args) == 2 else None)

    def _enumerated(self, builder: IRBuilder, stmt: ast.ForEach,
                    counted: tuple[ast.Expr, ast.Expr | None]
                    ) -> _Iteration | None:
        """Walk something and count the turns, which is the two as one.

        It is the iterator the loop would have had with a number carried beside
        it, so it works over everything a loop works over -- an array, a list, a
        string, a set, a dictionary, a range -- without any of them knowing
        about it.  What a turn gives is the count and what the walk gave, as a
        tuple, which two names take apart exactly as they take a dictionary's
        pair apart.

        **The count goes up by one and the addition is an addition**, with the
        check every other one carries.  So counting a hundred things in a `u6`
        stops the program, which is what says the type was too narrow -- and is
        why what to count from says what type the count has.
        """
        walked, from_written = counted
        inner = self._iteration_of(
            builder, replace(stmt, iterable=walked, type=None))
        if inner is None:
            return None
        if from_written is None:
            index: Type = U64
            first: Value = builder.int_const(U64, 0)
        else:
            first = self._lower_expr(
                builder, from_written,
                U64 if isinstance(from_written, ast.IntLit)
                and from_written.type_name is None else None)
            index = self._value_type_of(first)
            if index is ERROR:
                return None
            if not isinstance(index, IntType):
                self._diags.emit(D.LANG_ENUMERATE_COUNTS_IN_INTEGERS,
                                 from_written.span, found=index.written())
                return None
        if len(parts_of(inner.element)) > 1:
            # A turn that is already several values -- a dictionary's pair --
            # would make a tuple holding a tuple, and a value of one of those
            # is not a thing this compiler can hold: what it travels in is one
            # register per part, and a part that is itself several has nowhere
            # to go.  The to-do list records it beside the tuple holding an
            # array, which is the same gap.
            self._diags.emit(
                D.IMPL_UNIMPLEMENTED_FEATURE, stmt.iterable.span,
                feature="".join((
                    "counting the turns of something whose turn is already "
                    "several values, which would make a tuple holding the "
                    "tuple '", inner.element.written(), "'")))
            return None
        element = self._module.types.tuple_type((index, inner.element))
        one = builder.int_const(index, 1)
        span = stmt.span

        def take(b: IRBuilder, state: tuple[Value, ...]) -> Value:
            return b.make_tuple((state[-1], inner.take(b, state[:-1])),
                                element, span)

        declared = self._resolve_type(stmt.type) if stmt.type is not None else None
        if declared is not None and declared is not element:
            self._report_mismatch(stmt.iterable.span, element, declared)
            return None
        return _Iteration(
            element=element, start=(*inner.start, first),
            more=lambda b, s: inner.more(b, s[:-1]),
            take=take,
            step=lambda b, s: (*inner.step(b, s[:-1]),
                               b.binary(BinOp.ADD, s[-1], one, span)))

    def _iteration_of(self, builder: IRBuilder,
                      stmt: ast.ForEach) -> _Iteration | None:
        """What the loop's expression is as a thing to walk, without counting.

        A range is written where it is used and has no type of its own, so it is
        recognised before anything is lowered; everything else is a value, and
        what it is, is its type's business.
        """
        if isinstance(stmt.iterable, ast.Range):
            return self._over_a_range(builder, stmt)
        declared = self._resolve_type(stmt.type) if stmt.type is not None else None
        value = self._lower_expr(builder, stmt.iterable,
                                 self._holding(declared, stmt.iterable))
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        found: _Iteration | None
        if isinstance(ty, ArrayType):
            found = self._over_an_array(builder, value, ty, stmt.span)
        elif isinstance(ty, ListType):
            found = self._over_a_list(builder, value, ty, stmt.span)
        elif ty is STR:
            found = self._over_a_string(builder, value, stmt.span)
        elif isinstance(ty, (SetType, DictType)):
            found = self._over_a_table(builder, value, ty, stmt.span)
        elif isinstance(ty, TupleType):
            self._diags.emit(D.LANG_FOREACH_TUPLE_NEEDS_COMPTIME,
                             stmt.iterable.span)
            return None
        else:
            self._diags.emit(D.LANG_LOOP_NOT_AN_ITERATOR, stmt.iterable.span,
                             found=ty.written())
            return None
        if declared is not None and declared is not found.element:
            self._report_mismatch(stmt.iterable.span, found.element, declared)
            return None
        return found

    def _holding(self, declared: Type | None,
                 iterable: ast.Expr) -> Type | None:
        """What is wanted of a loop's expression, given what a turn is declared
        to be.

        A `foreach` binds a name the way `let` does, so a type written on that
        name says what its value is -- and a value written where the loop takes
        its turns from can take its own type from that, which is what makes
        `foreach x: u8 = [1, 2, 3]` the three bytes it reads as.  Without it the
        numbers inside would have nothing to say what they are, the list being
        the only thing that could say and having been asked first.

        What is wanted is worked out from how the loop's expression is written,
        because that is what says which container it is: a list of them, an
        array of as many of them as are written, a set of them.  Anything else
        -- a name, a call, a range -- either says its own type already or is
        asked for one another way.
        """
        if declared is None or declared is ERROR:
            return None
        match iterable:
            case ast.ListLit():
                return self._module.types.list_type(declared)
            case ast.ArrayLit():
                # A turn gives an element where the array has one dimension and
                # a row where it has more, so a row declared says every
                # dimension but the first and the writing says the first.
                inner = declared.shape if isinstance(declared, ArrayType) else ()
                element = declared.element if isinstance(declared, ArrayType) \
                    else declared
                return self._module.types.array_type(
                    element, (len(iterable.elements), *inner))
            case ast.SetLit():
                return self._module.types.set_type(declared, True)
            case _:
                return None

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

    def _over_a_list(self, builder: IRBuilder, value: Value, ty: ListType,
                     span: Span) -> _Iteration:
        """Walk the elements of a list, which is what walking one means.

        The same walk an array of unstated length gets, and for the same reason:
        where the elements are and how many there are do not change from turn to
        turn, so they are read once here and a turn is a comparison and a read.
        """
        elements = builder.extract(value, 0, parts_of(ty)[0], span)
        length = builder.extract(value, 1, U64, span)
        return _Iteration(
            element=ty.element, start=(builder.int_const(U64, 0),),
            more=lambda b, s: b.compare(CmpPred.ULT, s[0], length, span),
            take=lambda b, s: b.load(
                self._element_place(b, elements, ty.element, s[0], span), span),
            step=lambda b, s: (b.binary(BinOp.WRAP_ADD, s[0],
                                        b.int_const(U64, 1), span),))

    def _over_a_string(self, builder: IRBuilder, value: Value,
                       span: Span) -> _Iteration:
        """Walk the characters of a string, which is what walking one means.

        The bytes are UTF-8 and the characters are what they encode, so a turn
        is not a byte: what the loop carries is where in the bytes it is, and a
        turn moves it on by however many that character took.  Walking the bytes
        instead is a different loop over a different thing, and the type does not
        offer it -- the *n*-th byte of UTF-8 is not the *n*-th character, and a
        walk whose obvious reading is wrong is worse than no walk.

        Taking the character and moving past it are one call and not two.  Both
        want the leading byte and what it says, so they are asked together and
        the answer is kept for whichever of them is asked second.
        """
        bytes_ = builder.extract(value, 0, parts_of(STR)[0], span)
        length = builder.extract(value, 1, U64, span)
        taker = strings.next_function(self._module)
        taken = strings.taken_type(self._module)
        held: dict[int, Value] = {}

        def reach(b: IRBuilder, s: tuple[Value, ...]) -> Value:
            found = held.get(id(s[0]))
            if found is None:
                found = b.call(taker, (bytes_, s[0]), taken, span)
                held[id(s[0])] = found
            return found

        return _Iteration(
            element=CHAR, start=(builder.int_const(U64, 0),),
            more=lambda b, s: b.compare(CmpPred.ULT, s[0], length, span),
            take=lambda b, s: b.extract(reach(b, s), 0, CHAR, span),
            step=lambda b, s: (b.extract(reach(b, s), 1, U64, span),))

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
        shape = self._table_shape(ty)
        tables.ensure_runtime(self._module, shape)
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
                (key, read(b, place, shape.value_at, ty.value)), element, span)

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
                                 found=found.written())
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
                             found=element.written())
            return None
        last = self._lower_expr(builder, written.stop, element)
        other = self._value_type_of(last)
        if other is ERROR:
            return None
        if other != element:
            self._diags.emit(D.LANG_RANGE_ENDS_DIFFER, written.span,
                             first=element.written(), second=other.written())
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
                             literal=str(distance), type=element.written())
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
            # A name with storage of its own stands for the same place at every
            # turn: what a turn changes is what is in the place, which is read
            # where it is read and carried nowhere.
            if local is not None and not local.placed:
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
                             found=ty.written())
            return UndefConst(ERROR)
        taken = self._matched_arms(stmt, ty)
        if taken is None:
            return UndefConst(ERROR)
        if isinstance(ty, SumType):
            return self._lower_match_on_sum(builder, stmt, func, subject, ty,
                                            taken, wanted, produces)
        if isinstance(ty, EnumType):
            return self._lower_match_on_enum(builder, stmt, func, subject, ty,
                                             taken, wanted, produces)
        return self._lower_match_on_result(builder, stmt, func, subject, ty,
                                           taken, wanted, produces)

    def _alternatives(self, ty: Type) -> list[tuple[str, Type | None, bool]]:
        """What a value of *ty* may be: a name to report it by, what the
        alternative carries, and whether it is a result's error."""
        if isinstance(ty, ResultType):
            return [(ty.ok.written(), ty.ok, False), (BOTTOM_GLYPH, ty.err, True)]
        if isinstance(ty, EnumType):
            # One alternative per *number*, not per name: two names given one
            # number are one value, and nothing at run time can tell an arm
            # naming the first from an arm naming the second.
            seen: dict[int, str] = {}
            for member, number in zip(ty.members, ty.values):
                seen.setdefault(number, member)
            return [(member, None, False) for member in seen.values()]
        assert isinstance(ty, SumType)
        return [(variant.written(), variant, False) for _, variant in ty.variants]

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
                             type=ty.written())
            spoiled = True
        return None if spoiled else found

    def _alternative_of(self, pattern: ast.Pattern, ty: Type,
                        alternatives: Sequence[tuple[str, Type | None, bool]]
                        ) -> int | None:
        """Which alternative a pattern takes, reporting one that takes none."""
        if pattern.type is None:
            if not isinstance(ty, ResultType):
                self._diags.emit(D.LANG_MATCH_BOTTOM_NEEDS_A_RESULT, pattern.span,
                                 found=ty.written())
                return None
            return next(i for i, (_, _, bottom) in enumerate(alternatives) if bottom)
        if isinstance(ty, EnumType):
            # The alternatives of an enumeration are its values, so an arm of one
            # names a value and not a type.
            written = pattern.type
            if not isinstance(written, ast.TypeRef):
                self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, pattern.span,
                                 name="that", type=ty.written())
                return None
            index = ty.index_of(written.name)
            if index is None or written.module is not None or written.result:
                self._diags.emit(D.LANG_ENUM_UNKNOWN_VALUE, pattern.span,
                                 name=written.name, type=ty.written())
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
                         type=named.written(), found=ty.written())
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
        # The error's arm binds too, where the error carries something: a name
        # written there stands for what it carries, exactly as a name on the
        # answer's arm stands for the answer.
        carried = None
        if second.pattern.name is not None and ty.err is not None:
            carried = (second.pattern.name, second.pattern.name_span,
                       second.pattern.span, subject, ty.err)
        elif second.pattern.name is not None:
            self._diags.emit(D.LANG_MATCH_BOTTOM_CARRIES_NOTHING,
                             second.pattern.span, found=ty.written())
            return UndefConst(ERROR)
        return self._run_arms(
            builder, stmt, func,
            [_ArmPlan(body=first.body, block=answered, binds=binds),
             _ArmPlan(body=second.body, block=failed, binds=carried,
                      carried=True)], wanted, produces)

    def _lower_match_on_sum(self, builder: IRBuilder, stmt: ast.Match,
                            func: Function, subject: Value, ty: SumType,
                            taken: list[tuple[ast.MatchArm, frozenset[int]]],
                            wanted: Type | None, produces: bool) -> Value:
        """Lower a `match` over a sum.

        A chain of comparisons over the tag, which is the shape an enumeration
        is taken apart with and for the same reason -- what a value holds is a
        number, and which arm runs follows from it.  What differs is what an arm
        binds: the part is at the start of the place the sum is in, read as the
        type that part has.

        One arm may take every part, where it is the wildcard: then there is
        nothing to ask, and the arm is the whole of it.
        """
        if len(taken) == 1 and taken[0][0].pattern.wildcard:
            return self._run_arms(builder, stmt, func,
                                  [_ArmPlan(body=taken[0][0].body)],
                                  wanted, produces)
        place = builder.cast(CastKind.BITCAST, subject,
                             self._module.types.ptr_type(ty, mutable=True),
                             stmt.span)
        tag = builder.load(self._tag_place(builder, place, ty, stmt.span),
                           stmt.span)
        fallback = next((arm for arm, _ in taken if arm.pattern.wildcard),
                        taken[-1][0])
        blocks = {id(arm): builder.new_block("case") for arm, _ in taken}
        for arm, indices in taken:
            if arm is fallback:
                continue
            for index in sorted(indices):
                following = builder.new_block("otherwise")
                builder.condbr(
                    builder.compare(CmpPred.EQ, tag,
                                    builder.int_const(U8, index),
                                    arm.pattern.span),
                    blocks[id(arm)], following, span=arm.pattern.span)
                builder.position_at(following)
        builder.br(blocks[id(fallback)], (), stmt.span)
        plan: list[_ArmPlan] = []
        for arm, indices in taken:
            binds = None
            if arm.pattern.name is not None:
                # One part, since an arm that binds names one type: what it
                # binds is what that part carries, read where the sum is.
                held = ty.variants[min(indices)][1]
                binds = (arm.pattern.name, arm.pattern.name_span,
                         arm.pattern.span, place, held)
            plan.append(_ArmPlan(body=arm.body, block=blocks[id(arm)],
                                 binds=binds, payload=True))
        return self._run_arms(builder, stmt, func, plan, wanted, produces)

    def _lower_match_on_enum(self, builder: IRBuilder, stmt: ast.Match,
                             func: Function, subject: Value, ty: EnumType,
                             taken: list[tuple[ast.MatchArm, frozenset[int]]],
                             wanted: Type | None, produces: bool) -> Value:
        """Lower a `match` over an enumeration.

        One `switch`: the whole question said at once, one case per value an arm
        names, and the wildcard -- or the last arm, where there is none -- as
        the way taken when no case matches.

        What the backend makes of it is the backend's business.  It makes a
        chain of comparisons today, which is what this used to build out of a
        block per value; a jump table is the other way and wants relocations
        that only enough cases pay for.  Saying the question once rather than
        spelling out one answer to it is what lets that be decided there.
        """
        fallback = next((arm for arm, _ in taken if arm.pattern.wildcard),
                        taken[-1][0])
        # The way taken when no case matches gets its block first, so that it is
        # the block laid out after this one: what falls off the end of the chain
        # a backend makes of the switch then falls into it rather than jumping.
        blocks = {id(fallback): builder.new_block("case")}
        for arm, _ in taken:
            if id(arm) not in blocks:
                blocks[id(arm)] = builder.new_block("case")
        distinct = list(dict.fromkeys(ty.values))
        cases: list[tuple[int, BasicBlock]] = []
        for arm, indices in taken:
            if arm is fallback:
                continue
            for index in sorted(indices):
                cases.append((distinct[index], blocks[id(arm)]))
        builder.switch(subject, cases, blocks[id(fallback)], stmt.span)
        return self._run_arms(
            builder, stmt, func,
            [_ArmPlan(body=arm.body, block=blocks[id(arm)]) for arm, _ in taken],
            wanted, produces)

    def _run_arms(self, builder: IRBuilder, stmt: ast.Match | ast.If,
                  func: Function,
                  plan: Sequence[_ArmPlan], wanted: Type | None = None,
                  produces: bool = False,
                  otherwise: BasicBlock | None = None,
                  otherwise_memory: Value | None = None) -> Value:
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
        # What a name was made in differs from arm to arm the way its value does,
        # so each arm starts from what held before the arms and the join gets what
        # the arms that reach it leave.
        provenance = self._provenance()
        # Where this is what the function answers, each arm answers for itself.
        answering, self._answer_arms = self._answer_arms, None
        reaching: list[_Provenance] = []
        given_back: list[set[int]] = []
        yielded: set[int] = set()
        outer_carried = self._carried
        self._carried = outer_carried | {id(local) for local, _, _ in before}
        before_memory = builder.memory()
        if otherwise_memory is None:
            otherwise_memory = before_memory
        outcomes: list[tuple[BasicBlock, dict[int, Value], Value, Value | None]] = []
        changed: dict[int, _Local] = {}
        touched = False
        answer: Type | None = wanted
        for arm in plan:
            if arm.block is not None:
                builder.position_at(arm.block)
            builder.set_memory(arm.memory if arm.memory is not None
                               else before_memory)
            self._push_scope()
            if arm.binds is not None:
                name, name_span, where_span, value, answer_ty = arm.binds
                bound = (
                    self._payload_from(builder, value, answer_ty, where_span)
                    if arm.payload else
                    builder.error(value, answer_ty, where_span) if arm.carried
                    else builder.unwrap(value, answer_ty, where_span))
                self._name_value(bound, name, name_span)
                self._bind_local(name, bound, name_span, value_span=where_span,
                                 builder=builder)
                if isinstance(stmt, ast.Match):
                    self._taken_out_of(stmt.subject, (name,))
            self._put_back(provenance)
            outer_yield, self._yield_answer = self._yield_answer, answering
            try:
                given = self._lower_block(builder, arm.body, func,
                                          as_result=False, wanted=answer,
                                          produces=produces)
            finally:
                self._yield_answer = outer_yield
            last = arm.body.stmts[-1] if arm.body.stmts else None
            if answering is not None and given is not None \
                    and not builder.is_terminated \
                    and isinstance(last, ast.ExprStmt) \
                    and not isinstance(last.value, (ast.If, ast.Match)):
                given = self._answered(builder, last.value, given, answering,
                                       last.span)
            emptied_here = self._last_emptied
            if produces and given is not None and not builder.is_terminated:
                # What this arm comes to, asked while the names it bound are still
                # in reach: what the arm did along the way is not what it yields.
                last = arm.body.stmts[-1] if arm.body.stmts else None
                if isinstance(last, ast.ExprStmt):
                    yielded.update(self._arenas_in(last.value))
                elif last is not None:
                    yielded.update(self._arenas_in(arm.body))
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
                reaching.append(self._provenance())
                given_back.append(emptied_here)
            for local, held, where in before:
                local.value, local.value_span = held, where
        if otherwise is not None:
            # A way through that runs no arm at all, which is what an `if` with
            # no `else` has.  Nothing changed along it and nothing was written.
            outcomes.append((otherwise, {}, otherwise_memory, None))
            reaching.append(provenance)
            given_back.append(set())
        self._carried = outer_carried
        self._put_back(self._joined(provenance, reaching) if reaching
                       else provenance)
        if given_back and self._emptied:
            # Given back along every way that reaches the join is given back.
            self._emptied[-1].update(set.intersection(*given_back))
        if produces:
            self._made_from[id(stmt)] = (stmt, frozenset(yielded))
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
        # Where no way through wrote anything, the token is the one every way that
        # arrives carries -- and not whatever an arm that left the function wrote
        # last, which nothing after the join can see.
        builder.set_memory(memory if memory is not None else outcomes[-1][2])
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

        Taking something out is the other: `†d⸨k⸩`, `†l⟦i⟧` and `†it` all take
        it out whether or not anyone wants what was there, so a program that
        only wants it gone writes the line and reads nothing.
        """
        if isinstance(expr, ast.Call):
            self._answer_is_taken(expr)
            return
        if isinstance(expr, (ast.Take, ast.TakeAt, ast.TakeThrough)):
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
        if not self._image_definition(node, None):
            self._bind_local(node.name, UndefConst(ERROR), node.name_span,
                             node.mutable, value_span=node.span)
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
        if isinstance(declared, ProductType) and not node.more:
            # Built where it will live rather than made and then copied.  A
            # record holding a record has no register to be made in, so writing
            # it straight into its place is not an economy but the only way.
            handed = self._hands_out
            out = (handed is not None and declared is ARENA
                   and node.name == handed[0] and len(self._scopes) == handed[2])
            # The arena this body hands out lives where the caller keeps it, so
            # what is made in it is already there when the call returns.
            place = handed[1] if out else builder.frame(declared, node.span)
            self._initializing = node.name
            try:
                self._build_record(builder, place, node.value, declared,
                                   node.span)
            finally:
                self._initializing = None
            self._bind_local(node.name, place, node.name_span, node.mutable,
                             value_span=node.span, builder=builder,
                             placed_as=declared)
            if out:
                local = self._find_local(node.name)
                assert local is not None
                local.handed_out = True
                self._hands_out_local = local
                self._answer_made_in = (frozenset({id(local)}), "".join(
                    ("'", node.name, "'")))
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
        self._kept_by_a_name(declared)
        self._name_value(bound, node.name, node.name_span)
        self._bind_local(node.name, bound, node.name_span, node.mutable,
                         value_span=node.span, builder=builder)

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
        self._kept_by_a_name(self._value_type_of(value))
        self._name_value(value, node.name, node.name_span)
        self._bind_local(node.name, value, node.name_span, node.mutable,
                         value_span=node.span, builder=builder)

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
            self._bind_local(name, part, where, mutable, value_span=node.span,
                             builder=builder)

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
        elif isinstance(value, BlockParam) and value.name_hint == "answer":
            # What an `if` or a `match` comes to, joined: the name it is bound to
            # is a better one than the word the join was made with.
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
        if local is not None and local.placed:
            # A name with storage of its own is written the way a variable at
            # the top level is.  What `mut` asks about is still the name: it
            # says the program may put something else there.
            if not self._check_mutable(node, local.mutable, local.span):
                return None
            if self._lent_out(node.name, node.span, True):
                return None
            assert local.held is not None
            value = self._checked_value(builder, node, local.held)
            if _reaches_a_place(local.held) \
                    and self._outlives_it(value, local.depth, node.name,
                                          node.span):
                return None
            self._kept_by_a_name(local.held)
            if isinstance(local.held, ProductType):
                self._record_into(builder, local.value, value, local.held,
                                  node.span)
            else:
                builder.store(local.value, value, node.span)
            local.written = True
            local.is_parameter = False
            if not wants_value:
                return None
            return (self._record_from(builder, local.value, local.held,
                                      node.span)
                    if isinstance(local.held, ProductType)
                    else builder.load(local.value, node.span))
        if local is not None:
            if not self._check_mutable(node, local.mutable, local.span):
                return None
            value = self._checked_value(builder, node, local.value.ty)
            if _reaches_a_place(local.value.ty) \
                    and self._outlives_it(value, local.depth, node.name,
                                          node.span):
                return None
            self._kept_by_a_name(local.value.ty)
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
        if _read_only_table(target.value_type):
            # A name at the top level standing for a table nothing may put
            # anything in is not a place to put a different table either: what
            # stands there is what every part of the program reads.  `⎕environ`
            # is such a name, which is what makes the environment one thing
            # they all agree about.
            self._diags.emit(D.LANG_PLACE_NOT_WRITABLE, node.name_span,
                             name=node.name,
                             found=target.value_type.written())
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
                             found=found.written())
            return ERROR
        return found

    def _lower_return(self, builder: IRBuilder, stmt: ast.ReturnStmt,
                      func: Function) -> None:
        """Lower a return statement."""
        if stmt.value is None:
            if func.ty.ret is not VOID:
                self._diags.emit(D.LANG_FUNCDEF_RETURN_MISSING, stmt.span,
                                 name=func.name, type=func.ty.ret.written())
            self._returning(builder, None, stmt.span)
            return
        if func.ty.ret is VOID:
            if isinstance(stmt.value, ast.Call):
                # `return f()` where both answer with nothing is the call and
                # then a return carrying nothing.  No value is named anywhere in
                # it, which is why it is an abbreviation and not an exception to
                # the rule that such a call has nothing to use.
                answer = self._lower_expr(builder, stmt.value, None)
                if answer.ty is VOID or answer.ty is ERROR:
                    self._returning(builder, None, stmt.span)
                    return
            self._diags.emit(D.LANG_FUNCDEF_RETURN_VALUE_IN_VOID, stmt.span, name=func.name)
            self._returning(builder, None, stmt.span)
            return
        value = self._lower_into(builder, stmt.value, func.ty.ret, stmt.span)
        self._returning(builder, self._answered(builder, stmt.value, value, func,
                                                stmt.span), stmt.span)

    def _lower_expr(self, builder: IRBuilder, expr: ast.Expr,
                    expected: Type | None) -> Value:
        """Lower an expression, checking it against the expected type.

        **A reference handed to a call is gone when the call is**, where what the
        call answers cannot hold one: nothing is left that could reach the place
        through it.  So `(⍕a in pool) ⧺ (⍕b in pool)` lends `pool` twice in turn and
        not twice at once.  An answer that may hold a reference keeps what it was
        handed until the statement ends, or until the name it is bound to goes.
        """
        if isinstance(expr, (ast.Member, ast.Element, ast.Index)) \
                and self._read_through_the_dead(expr):
            return UndefConst(ERROR)
        if not isinstance(expr, _CALLING):
            return self._lower_written(builder, expr, expected)
        before = len(self._borrows)
        found = self._lower_written(builder, expr, expected)
        if len(self._borrows) > before \
                and not _holds_a_reference(self._value_type_of(found)):
            self._borrows[before:] = [
                one for one in self._borrows[before:]
                if one.depth != _UNTIL_THE_STATEMENT_ENDS]
        return found

    def _told_gone(self, local: _Local, name: str, span: Span) -> None:
        """Say why a dead name may not be read: its arena, or its container."""
        if local.gone_with.startswith(_REPLACED):
            self._diags.emit(D.LANG_ELEMENT_REPLACED, span, name=name,
                             container=local.gone_with[len(_REPLACED):])
            return
        self._diags.emit(D.LANG_ARENA_VALUE_GIVEN_BACK, span, name=name,
                         arena=local.gone_with)

    def _read_through_the_dead(self, expr: ast.Expr) -> bool:
        """Refuse reading part of a name whose arena was given back (4615).

        A field, an element and an entry are the whole's, and the whole is dead
        when anything it holds may be: the name says what it holds as one set.
        """
        root = expr
        while isinstance(root, (ast.Member, ast.Element, ast.Index)):
            root = root.base
        if not isinstance(root, ast.NameRef):
            return False
        local = self._find_local(root.name)
        if local is None or local.gone_at is None:
            return False
        local.read = True
        self._told_gone(local, root.name, root.span)
        return True

    def _lower_written(self, builder: IRBuilder, expr: ast.Expr,
                       expected: Type | None) -> Value:
        """Lower an expression as it is written: the dispatch `_lower_expr` makes."""
        match expr:
            case ast.IntLit() if self._aiming_at(expected) is CHAR \
                    and expr.type_name is None:
                # A number written where a code point is wanted is that code
                # point, and the one thing that has to be checked about it is
                # the one thing a code point can fail to be: there is a last
                # one, and it is not the last thirty-two bit number.
                return self._code_point(builder, expr.value, expr.span)
            case ast.IntLit():
                ty = self._literal_type(expr, expected)
                if ty is None:
                    return UndefConst(ERROR)
                if not ty.holds(expr.value):
                    self._diags.emit(D.LANG_SYNTAX_INTEGER_RANGE, expr.span,
                                     literal=str(expr.value), type=ty.written())
                    return builder.int_const(ty, 0)
                return builder.int_const(ty, expr.value)
            case ast.CharLit():
                if not self._accepts(expected, CHAR):
                    self._report_mismatch(expr.span, CHAR, expected)
                    return UndefConst(ERROR)
                return builder.module.char_const(expr.value)
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
            case ast.Allocated():
                return self._lower_allocated(builder, expr, expected)
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
            case ast.Take():
                return self._lower_take(builder, expr, expected)
            case ast.TakeAt():
                return self._lower_take_at(builder, expr, expected)
            case ast.TakeThrough():
                return self._lower_take_through(builder, expr, expected)
            case ast.Index():
                return self._lower_index(builder, expr, expected)
            case ast.If():
                return self._lower_if(builder, expr, builder.function, expected,
                                      True)
            case ast.Scoped():
                return self._lower_scoped(builder, expr, expected)
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
            case ast.Call() if isinstance(expr.callee, ast.NameRef) \
                    and expr.callee.name == ENUMERATE_NAME:
                # A loop's expression is looked at before it is lowered, so
                # reaching this is standing where a value stands.
                self._diags.emit(D.LANG_ENUMERATE_IS_NOT_A_VALUE, expr.span)
                return UndefConst(ERROR)
            case ast.Lambda():
                return self._lower_lambda(builder, expr, expected)
            case ast.AddressOf():
                return self._lower_address(builder, expr, expected)
            case ast.Deref():
                return self._lower_deref(builder, expr, expected)
            case ast.Failure():
                return self._lower_failure(builder, expr, expected)
            case ast.Quote():
                return self._lower_quote(builder, expr, expected)
            case ast.Hole():
                # Outside a quote there is nothing for one to go into.  Inside one
                # it is read by the quote itself and never reaches here.
                self._diags.emit(D.LANG_MACRO_HOLE_OUTSIDE, expr.span)
                return UndefConst(ERROR)
            case ast.Fresh():
                return self._lower_fresh(builder, expr, expected)
            case ast.Lifted() if expected is SYNTAX \
                    and isinstance(expr.written, ast.TypeRef) \
                    and expr.written.module is None:
                # A piece of the program is wanted, and what stands between the marks
                # is a bare name -- which the parser reads as a type because it could
                # be one.  Which it is, is a question about the program and not about
                # its syntax, and this is where that question is answered: a name
                # where a piece is wanted is the name as a piece.
                return self._lower_quote(builder, ast.Quote(
                    span=expr.span,
                    pieces=(ast.NameRef(span=expr.span,
                                        name=expr.written.name),)), expected)
            case ast.Lifted():
                # Every place one may stand looks at it before it gets here:
                # `\N{APL FUNCTIONAL SYMBOL QUAD}typeof`, a comparison the compiler settles, and the two
                # operators that answer a type's ends.  Reaching this is
                # standing where a value stands.
                self._diags.emit(D.LANG_LIFT_IS_NOT_A_VALUE, expr.span)
                return UndefConst(ERROR)
            case ast.Raised():
                return self._lower_raised(builder, expr, expected)
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
            case ast.ListLit():
                return self._lower_list(builder, expr, expected)
            case ast.StringLit():
                if not self._accepts(expected, STR):
                    self._report_mismatch(expr.span, STR, expected)
                    return UndefConst(ERROR)
                return self._written_text(builder, expr.value, expr.span)
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
        if context is SYNTAX and expr.op in (ast.BinaryOp.EQUAL,
                                             ast.BinaryOp.NOT_EQUAL):
            # Two pieces of the program compared, which asks whether the same thing
            # is written in both wherever each was written.  It is a question about
            # trees and not about numbers, so it is not the comparison below.
            left = self._lower_into(builder, expr.left, SYNTAX, expr.left.span)
            right = self._lower_into(builder, expr.right, SYNTAX, expr.right.span)
            if ERROR in (self._value_type_of(left), self._value_type_of(right)):
                return UndefConst(ERROR)
            return self._pieces_alike(builder, expr, left, right)
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._comparable(expr.left.span, expr.op,
                                  self._scalar_of(self._value_type_of(left)))
            if ty is not ERROR:
                other: Type | None = ty
            elif self._value_type_of(left) is ERROR:
                # Whatever is wrong with the left has been reported, and the
                # right takes its type from it: handing the error down is what
                # keeps a literal there from asking for a type of its own and
                # being told the compiler has not got one.
                other = ERROR
            else:
                other = context
            right = self._lower_expr(builder, expr.right, other)
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
                             operator=expr.op.value, expected=ty.written(),
                             found=found.written())
            return UndefConst(ERROR)
        if isinstance(ty, FloatType) and expr.op in _EXACT_ON_FLOATS:
            # Two floating-point values computed different ways are rarely the
            # one value, so asking whether they are is nearly always the wrong
            # question -- but not always, which is why this is a warning and why
            # the approximate comparisons are written differently rather than
            # this one quietly becoming approximate.
            self._diags.emit(D.LANG_TYPE_EXACT_FLOAT_COMPARISON, expr.span,
                             operator=expr.op.value, type=ty.written())
        if ty is STR:
            if not self._accepts(expected, BOOL):
                self._report_mismatch(expr.span, BOOL, expected)
                return UndefConst(ERROR)
            return self._compared_text(builder, expr, left, right)
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

    def _compared_text(self, builder: IRBuilder, expr: ast.Binary, left: Value,
                       right: Value) -> Value:
        """Lower a comparison of two strings.

        **No decoding.**  UTF-8 was designed so that comparing the bytes of two
        strings answers what comparing the code points they stand for would, so
        the whole of the ordering is a walk of bytes -- the loop `memcmp` is and
        not the one `foreach` is.  Every one of the six goes through it, and
        each is what it always was, asked of which of the two came first rather
        than of the strings.

        The equal pair could be cheaper: two strings of different lengths are
        different strings, so a comparison of the two counts would answer
        without reading a byte.  It is not done, because the walk answers on the
        first byte that differs and two strings that are meant to be different
        nearly always differ early -- the case the check would save is two
        strings where one is a prefix of the other, which is the case the walk
        has to do anyway to know that it is.
        """
        which = builder.call(
            strings.compare_function(self._module),
            (builder.extract(left, 0, parts_of(STR)[0], expr.span),
             builder.extract(left, 1, U64, expr.span),
             builder.extract(right, 0, parts_of(STR)[0], expr.span),
             builder.extract(right, 1, U64, expr.span)),
            I64, expr.span)
        return builder.compare(_COMPARISONS[expr.op][0], which,
                               builder.int_const(I64, 0), expr.span)

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
                                 found=ty.written())
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
                             operator=expr.op.value, found=found.written())
            return UndefConst(ERROR)
        if found is not ty:
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, expr.right.span,
                             operator=expr.op.value, expected=ty.written(),
                             found=found.written())
            return UndefConst(ERROR)
        exchanged, magnitude, pred = _APPROXIMATE[expr.op]
        first, second = (right, left) if exchanged else (left, right)
        difference = builder.binary(BinOp.SUB, first, second, expr.span)
        if magnitude:
            difference = builder.unary(UnOp.FABS, difference, span=expr.span)
        if without_units(ty) is not F64:
            # A unit is no part of the bits, so what is widened is decided by
            # the width alone -- and the difference between two lengths is
            # measured against a tolerance that is a plain number.
            difference = builder.cast(CastKind.FEXT, difference, F64, expr.span)
        elif not _unit_of(ty).is_none:
            difference = builder.cast(CastKind.BITCAST, difference, F64, expr.span)
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
                             operator="?", found=ty.written())
            return UndefConst(ERROR)
        answering = self._answering
        if not isinstance(answering, ResultType) or answering.err != ty.err:
            self._diags.emit(D.LANG_TYPE_TRY_NEEDS_A_RESULT, expr.span,
                             found=(answering.written() if answering is not None
                                    else VOID.written()))
            return UndefConst(ERROR)
        if not self._accepts(expected, ty.ok):
            self._report_mismatch(expr.span, ty.ok, expected)
            return UndefConst(ERROR)
        leaving = builder.new_block("leaving")
        answered = builder.new_block("answered")
        builder.condbr(builder.failed(value, expr.span), leaving, answered,
                       span=expr.span)
        builder.position_at(leaving)
        # What the way out runs -- what was put off -- may write memory and give
        # names new values, and none of it happened on the way that goes on.
        token = builder.memory()
        held = [(local, local.value, local.value_span)
                for scope in self._scopes for local in scope.values()]
        # What is handed back is an answer nothing may read beside the truth
        # value that forbids reading it -- and, where the error carries
        # something, that something, taken from the failure being propagated.
        # The two error types agree: it is what was checked above.
        self._returning(builder, builder.wrap(
            UndefConst(answering.ok), builder.bool_const(True), answering,
            expr.span,
            None if answering.err is None
            else builder.error(value, answering.err, expr.span)), expr.span)
        builder.set_memory(token)
        for local, was, where in held:
            local.value, local.value_span = was, where
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
                             operator=expr.op.value, found=ty.written())
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
                             operator=op.value, found=ty.written())
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
        select = tables.ensure_operators(self._module, self._table_shape(ty))
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
        if ty is CHAR:
            # Code points are ordered, and the order is a real one: Unicode
            # numbers them, and every collation in the world starts from that
            # numbering before it does anything else.  So both questions are
            # asked of them, unlike an enumeration, whose order is the order
            # somebody happened to write the values in.
            return ty
        if ty is STR:
            # Text is ordered, and the order is the one the bytes are already
            # in: UTF-8 was built so that comparing the bytes of two strings
            # gives the same answer as comparing the code points they stand
            # for.  So the ordering is a real one and is the cheap one, which
            # is not a coincidence -- it is what the encoding was designed for.
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
                             operator=op.value, found=ty.written())
        else:
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_COMPARABLE, span,
                             operator=op.value, found=ty.written())
        return ERROR

    def _lower_reshape(self, builder: IRBuilder, expr: ast.Binary,
                       expected: Type | None) -> Value:
        """Lower `SHAPE \N{APL FUNCTIONAL SYMBOL RHO} VALUES`: something of that shape, filled with those.

        The shape has to be written down -- an array carries its shape in its
        type, so how many there are along each dimension is settled while the
        program is compiled.  What fills it is either one value, which goes
        everywhere, or an array, which is walked in the order its elements lie
        in and begun again where it runs out.  More values than the new object
        holds is refused: which ones would be left out is not something to guess
        at.

        Going round again is what makes `n \N{APL FUNCTIONAL SYMBOL RHO} \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0u8, 1u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}` alternate, which is the
        thing this operator is for and is why the rule is "round again" rather
        than "pad with something".  What it would pad with is a question no type
        answers.
        """
        shape = self._written_shape(expr.left)
        source = self._lower_expr(builder, expr.right, None)
        found = self._value_type_of(source)
        if shape is None or found is ERROR:
            return UndefConst(ERROR)
        total = 1
        for along in shape:
            total *= along
        element, count = (found.element, found.count) \
            if isinstance(found, ArrayType) else (found, None)
        if isinstance(found, ArrayType) and not found.fixed:
            self._diags.emit(D.LANG_CONCAT_NEEDS_A_STATED_SHAPE, expr.right.span,
                             found=found.written())
            return UndefConst(ERROR)
        if count is not None and count > total:
            self._diags.emit(D.LANG_SHAPE_TOO_MANY, expr.span, found=count,
                             wanted=total)
            return UndefConst(ERROR)
        answer = self._module.types.array_type(element, shape)
        place = builder.frame(answer, expr.span)
        if count is None:
            # One value everywhere, which is one operation over the whole run
            # rather than one per element: the same machinery an operator over
            # an array uses, asked for a run of one value.
            held = self._module.types.vec_type(element, total)
            builder.store(
                builder.cast(CastKind.BITCAST, place,
                             self._module.types.ptr_type(held, mutable=True),
                             expr.span),
                builder.splat(source, held, expr.span), expr.span)
        else:
            start, _ = self._shape_of(builder, source, found, expr.span)
            for at in range(total):
                builder.store(
                    self._element_place(builder, place, element,
                                        builder.int_const(U64, at), expr.span),
                    builder.load(
                        self._element_place(builder, start, element,
                                            builder.int_const(U64, at % count),
                                            expr.span),
                        expr.span),
                    expr.span)
        made = builder.cast(CastKind.BITCAST, place, answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _written_shape(self, expr: ast.Expr) -> tuple[int, ...] | None:
        """The shape written on the left of the operator, or nothing where what
        is written is not one.

        Read off the syntax and not lowered.  Three things are one: a number
        the compiler knows -- a literal, or a name bound at the top level to one
        -- a tuple of those, and the shape of something whose type says its
        shape.  The last is what makes `(⍴a) ⍴ b` well formed and is the reason
        the one glyph does both jobs.
        """
        if isinstance(expr, ast.Unary) and expr.op is ast.UnaryOp.SHAPE:
            # The shape of something, which is known where its type says it --
            # and where its type says it is exactly where this needs it.  That
            # is what makes `(\N{APL FUNCTIONAL SYMBOL RHO}a) \N{APL FUNCTIONAL SYMBOL RHO} b` well formed, which is the reason the one
            # glyph does both jobs.
            of = self._hint_of(expr.operand)
            if isinstance(of, ArrayType) and of.fixed:
                return tuple(along for along in of.shape if along is not None)
            self._diags.emit(D.LANG_SHAPE_NOT_WRITTEN_DOWN, expr.span)
            return None
        written = expr.members if isinstance(expr, ast.TupleLit) else (expr,)
        found: list[int] = []
        for one in written:
            along = self._constant_number(one)
            if along is None:
                self._diags.emit(D.LANG_SHAPE_NOT_WRITTEN_DOWN, one.span)
                return None
            if along <= 0:
                self._diags.emit(D.LANG_SHAPE_NOT_A_COUNT, one.span,
                                 found=str(along))
                return None
            found.append(along)
        return tuple(found)

    def _lower_failure(self, builder: IRBuilder, expr: ast.Failure,
                       expected: Type | None) -> Value:
        """Lower `\N{UP TACK}` and `\N{UP TACK} VALUE`: a result that has no answer, written out.

        **What it is a failure of is what stands where it stands.**  That is
        the same rule a value of the answer type follows -- `0u8` written where
        a `u8?` is wanted is the successful result -- and it is why nothing is
        written beside the glyph to say which result this is.  So it needs a
        place that wants one, and there is no reading of it anywhere else.

        Whether it carries a value is the type's to say and not the program's:
        an error written `TYPE?` is the fact that there is no answer and nothing
        more, and one written `TYPE?ERROR` is that fact and a value beside it.
        """
        # What is wanted as it stands, and not what a value of the answer type
        # would be aimed at: a failure is the *result* and not its answer, so
        # the one place that unwraps a result before looking at it is the one
        # place this must not ask.
        wanted = expected
        if not isinstance(wanted, ResultType):
            self._diags.emit(
                D.LANG_FAILURE_NEEDS_A_RESULT, expr.span,
                found="nothing in particular" if wanted is None
                else "".join(("'", wanted.written(), "'")))
            return UndefConst(ERROR)
        if wanted.err is None:
            if expr.value is not None:
                self._diags.emit(D.LANG_FAILURE_CARRIES_NOTHING,
                                 expr.value.span, found=wanted.written())
                return UndefConst(ERROR)
            carried: Value | None = None
        else:
            if expr.value is None:
                self._diags.emit(D.LANG_FAILURE_NEEDS_A_VALUE, expr.span,
                                 found=wanted.written(),
                                 carried=wanted.err.written())
                return UndefConst(ERROR)
            carried = self._lower_into(builder, expr.value, wanted.err,
                                       expr.value.span)
            if self._value_type_of(carried) is ERROR:
                return UndefConst(ERROR)
        # The answer half is a value nothing may read, which is what the truth
        # value beside it says.
        return builder.wrap(UndefConst(wanted.ok), builder.bool_const(True),
                            wanted, expr.span, carried)

    def _lower_divides(self, builder: IRBuilder, expr: ast.Expr,
                       written: ast.Expr | None, over: ast.Expr, name: str,
                       negated: bool, expected: Type | None) -> Value:
        """Lower `a \N{DIVIDES} b`: whether *a* divides *b* with nothing left over.

        It relates two numbers and answers a truth value, which is a
        comparison's shape, so it is written where a comparison is written and
        binds as one.  What it asks is the remainder's question with the
        remainder thrown away.

        **It is total, where dividing is not.**  `\N{DIVIDES}` by zero has an answer and
        `\N{DIVISION SIGN}` by zero has not: zero divides nothing but zero, which is the
        definition and not a rule invented here.  So this answers a truth value
        whatever it is given, and nothing about it is a result.

        *written* is nothing where the operator was written before one operand,
        which is the same operator with two on the left -- so the two is made
        here, of the type the operand turned out to be.
        """
        left: Value | None = None
        outer, self._operand_of = self._operand_of, name
        was_listing, self._listing = self._listing, True
        try:
            if written is not None:
                # What is wanted of each side is what an array of them would be
                # an array of, which is what lets a number stand beside one.
                left = self._lower_expr(builder, written,
                                        self._scalar_of(self._hint_of(over)))
                first = self._scalar_of(self._value_type_of(left))
                if first is not None and first is not ERROR \
                        and not isinstance(first, IntType):
                    self._diags.emit(D.LANG_DIVIDES_IS_FOR_INTEGERS,
                                     written.span, operator=name,
                                     found=first.written())
                    first = ERROR
            else:
                first = None
            right = self._lower_expr(
                builder, over,
                first if first is not None and first is not ERROR else None)
        finally:
            self._operand_of = outer
            self._listing = was_listing
        ty = self._scalar_of(self._value_type_of(right))
        if first is ERROR or ty is None or ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, IntType):
            self._diags.emit(D.LANG_DIVIDES_IS_FOR_INTEGERS, over.span,
                             operator=name, found=ty.written())
            return UndefConst(ERROR)
        if left is None:
            if not ty.holds(2):
                self._diags.emit(D.LANG_DIVIDES_NEEDS_A_TWO, expr.span,
                                 operator=name, found=ty.written())
                return UndefConst(ERROR)
            left = IntConst(ty, 2)
        elif first is not ty:
            assert first is not None
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, over.span,
                             operator=name, expected=first.written(),
                             found=ty.written())
            return UndefConst(ERROR)
        walked = self._walk_operands(builder, expr,
                                     (("left", left), ("right", right))
                                     if written is not None
                                     else (("operand", right),), expected)
        if walked is not None:
            return walked
        # What it answers depends on whether the divisor can be seen: written
        # down -- or left out, which writes two in -- the compiler knows it is
        # not zero, so the answer is a truth value.  Worked out, it may be
        # zero, and a truth value has no room to say so.
        # A `_Ready` stands for a value already lowered, which is what an
        # operand becomes while the operator is being applied to each element of
        # an array -- so a literal written on the left is still a literal there.
        settled = (written is None or isinstance(written, ast.IntLit)
                   or (isinstance(written, _Ready)
                       and isinstance(written.value, IntConst)))
        answer: Type = BOOL if settled else \
            self._module.types.result_type(BOOL, ty)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        if settled:
            assert isinstance(left, IntConst)
            if left.value == 0:
                self._diags.emit(D.LANG_DIVIDES_BY_A_WRITTEN_ZERO, expr.span,
                                 operator=name)
                return UndefConst(ERROR)
            found = self._divides(builder, left, right, ty, expr.span)
            return (self._negate(builder, found, expr.span) if negated
                    else found)
        return self._divides_or_not(builder, left, right, ty, answer, negated,
                                    expr.span)

    def _divides_or_not(self, builder: IRBuilder, left: Value, right: Value,
                        ty: IntType, answer: Type, negated: bool,
                        span: Span) -> Value:
        """Whether one number divides another, where the divisor may be zero.

        Nothing divides by zero, so there is a pair of operands this has no
        answer for -- which is what a result is for, and is the same shape `\N{DIVISION SIGN}`
        already has.  **What the error carries is the number the question was
        asked about**, since what made it fail is known from the failure itself:
        the divisor was zero, and a zero says nothing a reader did not have.

        There is no test for zero written here.  The remainder already answers
        a result, failing on exactly the divisor this does, so the failure is
        taken from it and the answer is the comparison beside it.
        """
        remainder = self._module.types.result_type(ty)
        left_over = builder.binary(BinOp.SREM if ty.signed else BinOp.UREM,
                                   right, left, span, remainder)
        divides = builder.compare(CmpPred.EQ,
                                  builder.unwrap(left_over, ty, span),
                                  builder.int_const(ty, 0), span)
        if negated:
            divides = self._negate(builder, divides, span)
        return builder.wrap(divides, builder.failed(left_over, span), answer,
                            span, right)

    def _divides(self, builder: IRBuilder, left: Value, right: Value,
                 ty: IntType, span: Span) -> Value:
        """Whether *left* divides *right*, as a truth value.

        Zero divides nothing but zero, so where the divisor is zero the answer
        is whether what it was asked about is zero too.  Written down, that is
        settled here and costs nothing; worked out, it is one comparison and a
        branch, and the two ways the answer is reached hand it over to the same
        block.
        """
        nothing = builder.int_const(ty, 0)
        remainder = self._module.types.result_type(ty)
        taking = BinOp.SREM if ty.signed else BinOp.UREM

        def evenly() -> Value:
            left_over = builder.binary(taking, right, left, span, remainder)
            return builder.compare(CmpPred.EQ,
                                   builder.unwrap(left_over, ty, span),
                                   nothing, span)

        if isinstance(left, IntConst):
            # The divisor is written down, so which of the two cases this is, is
            # written down with it.
            if left.value == 0:
                return builder.compare(CmpPred.EQ, right, nothing, span)
            return evenly()
        by_zero = builder.new_block("divides.by.nothing")
        ordinary = builder.new_block("divides")
        done = builder.new_block("divided")
        builder.condbr(builder.compare(CmpPred.EQ, left, nothing, span),
                       by_zero, ordinary, span=span)
        builder.position_at(by_zero)
        builder.br(done, (builder.compare(CmpPred.EQ, right, nothing, span),),
                   span)
        builder.position_at(ordinary)
        builder.br(done, (evenly(),), span)
        builder.position_at(done)
        return done.add_param(BOOL, "divides")

    def _lower_power(self, builder: IRBuilder, expr: ast.Binary,
                     expected: Type | None) -> Value:
        """Lower `a \N{SUPERSCRIPT LATIN SMALL LETTER N} b`: raising by an exponent the compiler cannot see.

        It is not one of the operators the arithmetic path handles, and the
        reason is its two sides: everywhere else they are of one type and here
        they are not.  What is raised is a number of whatever type it is, and
        what it is raised by is a *count* -- how many times to multiply the one
        by itself -- so a rule that made the two agree would have refused
        `1.5f64 \N{SUPERSCRIPT LATIN SMALL LETTER N} 3u8`.

        **It answers a result**, for the reason division does: there are
        exponents it has no answer for.  A negative one means one divided by the
        positive power, and that division has no answer where what was raised is
        zero -- so the answer is the number where there is one and the fact that
        there is none where there is not.  Whether the exponent is negative is
        not known here, so the type cannot depend on it, exactly as a division's
        cannot depend on whether the divisor turns out to be zero.

        What is written with a raised number is a different question, answered
        by `_lower_raised`: there the exponent is written down.
        """
        base, ty, times, count = self._sides_of_a_power(builder, expr,
                                                        expr.left, expr.right,
                                                        expected)
        if ty is None or count is None:
            return UndefConst(ERROR)
        answer = self._module.types.result_type(ty)
        walked = self._walk_operands(builder, expr,
                                     (("left", base), ("right", times)),
                                     expected)
        if walked is not None:
            return walked
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        if isinstance(times, IntConst):
            # Written down after all, which settles which of the two it is --
            # and the answer is a result either way, the type being the
            # operator's and not this one exponent's.
            if times.value < 0:
                return self._reciprocal(builder, base, ty,
                                        -times.value, answer, expr.span)
            if isinstance(base, IntConst) \
                    and self._power_is_already_known(expr, ty, base,
                                                     times.value):
                return UndefConst(ERROR)
            return builder.wrap(self._raised_by(builder, base, ty, times.value,
                                                expr.span),
                                builder.bool_const(False), answer, expr.span)
        return self._raised_over(builder, base, ty, times, count, answer,
                                 expr.span)

    def _lower_raised(self, builder: IRBuilder, expr: ast.Raised,
                      expected: Type | None) -> Value:
        """Lower `a\N{SUPERSCRIPT TWO}`: raising by an exponent written as a raised number.

        The exponent is written down, so which of the two things this is, is
        known while compiling.  **A non-negative one answers a value of what was
        raised**: every such power exists, and one that will not fit stops the
        program the way a multiplication that will not fit does.  **A negative
        one is a division** -- one divided by the positive power -- and answers
        a result, as every division here does, because what was raised may be
        zero.

        That is why this is not the operator with a number on the right.  The
        operator cannot see its exponent and so answers a result whatever it
        turns out to be; this one can, and a `?` on `a\N{SUPERSCRIPT TWO}` would be a mark for a
        failure that cannot happen.
        """
        wanted = expected
        if expr.exponent < 0 and isinstance(self._aiming_at(expected), ResultType):
            found = self._aiming_at(expected)
            assert isinstance(found, ResultType)
            wanted = found.ok
        base, ty, _, _ = self._sides_of_a_power(builder, expr, expr.base, None,
                                                wanted)
        if ty is None:
            return UndefConst(ERROR)
        answer: Type = (self._module.types.result_type(ty) if expr.exponent < 0
                        else ty)
        walked = self._walk_operands(builder, expr, (("base", base),), expected)
        if walked is not None:
            return walked
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        if expr.exponent < 0:
            return self._reciprocal(builder, base, ty, -expr.exponent, answer,
                                    expr.span)
        if isinstance(base, IntConst) \
                and self._power_is_already_known(expr, ty, base, expr.exponent):
            return UndefConst(ERROR)
        return self._raised_by(builder, base, ty, expr.exponent, expr.span)

    def _sides_of_a_power(self, builder: IRBuilder, expr: ast.Expr,
                          written: ast.Expr, exponent: ast.Expr | None,
                          expected: Type | None
                          ) -> tuple[Value, Type | None, Value, IntType | None]:
        """Lower what is raised and what it is raised by, and check both.

        Shared by the two, since what may be raised and what may raise it are
        the same question however the exponent was written.
        """
        context = self._scalar_of(self._aiming_at(expected)) \
            if expected is not None else self._hint_of(written)
        outer, self._operand_of = self._operand_of, ast.BinaryOp.POWER.value
        was_listing, self._listing = self._listing, True
        try:
            base = self._lower_expr(builder, written, context)
        finally:
            self._operand_of = outer
            self._listing = was_listing
        ty = self._scalar_of(self._value_type_of(base))
        if ty is not None and ty is not ERROR \
                and not isinstance(ty, (IntType, FloatType)):
            self._diags.emit(D.LANG_POWER_BASE_NOT_A_NUMBER, written.span,
                             found=ty.written())
            ty = ERROR
        if exponent is None:
            return base, None if ty is ERROR else ty, UndefConst(ERROR), None
        # A count with nothing to say what width it is, is the widest there is:
        # an exponent is never the thing a program is being careful about, and
        # it would otherwise have to take its width from the thing being
        # raised, which is exactly what it is not.  Only a number written
        # without a suffix is told that; anything else says what it is, and
        # being told otherwise would report a mistake in the wrong place.
        times = self._lower_expr(
            builder, exponent,
            U64 if isinstance(exponent, ast.IntLit)
            and exponent.type_name is None else None)
        count = self._value_type_of(times)
        if not isinstance(count, IntType):
            if count is not ERROR:
                self._diags.emit(D.LANG_POWER_EXPONENT_NOT_A_COUNT,
                                 exponent.span, found=count.written())
            return base, None, times, None
        if ty is ERROR or ty is None:
            return base, None, times, count
        return base, ty, times, count

    def _reciprocal(self, builder: IRBuilder, base: Value, ty: Type, times: int,
                    answer: Type, span: Span) -> Value:
        """One divided by a power, which is what a negative exponent means.

        The division is the language's own, so what it answers where it has no
        answer is what every other division answers there, and a program that
        reads it reads it the same way.  For an integer that makes a negative
        power almost always zero, which is what dividing one by a whole number
        greater than one *is*; the operator is not the place to decide that a
        program did not mean it.
        """
        return self._reciprocal_of(
            builder, self._raised_by(builder, base, ty, times, span), ty,
            answer, span)

    def _reciprocal_of(self, builder: IRBuilder, raised: Value, ty: Type,
                       answer: Type, span: Span) -> Value:
        """One divided by a value, in whichever division its type has."""
        if isinstance(ty, FloatType):
            return builder.binary(BinOp.FDIV, self._unity(builder, ty, span),
                                  raised, span, answer)
        assert isinstance(ty, IntType)
        return builder.binary(BinOp.SDIV if ty.signed else BinOp.UDIV,
                              self._unity(builder, ty, span), raised, span,
                              answer)

    def _power_is_already_known(self, expr: ast.Expr, ty: Type,
                                base: IntConst, times: int) -> bool:
        """Report a power of two written-down numbers that will not fit.

        The same rule every other operator follows where both sides are known:
        a program whose answer cannot exist is refused where it is written
        rather than built and left to stop when it is run -- and, as for every
        other operator, inside a wrap there is no answer that does not fit.
        """
        if self._wrapping:
            return False
        assert isinstance(ty, IntType)
        answer = base.value ** times
        if ty.holds(answer):
            return False
        self._diags.emit(D.LANG_TYPE_ANSWER_DOES_NOT_FIT, expr.span,
                         value=str(answer), type=ty.written())
        return True

    def _unity(self, builder: IRBuilder, ty: Type, span: Span) -> Value:
        """The value anything raised to no power at all comes to.

        One, including for a base of zero.  That is what every language and
        every mathematician writing a polynomial means by it: the empty product
        is one, and the alternative -- a special case for `0\N{SUPERSCRIPT ZERO}` -- would make the
        operator answer differently for a value the program may not know.
        """
        if isinstance(ty, FloatType):
            return builder.float_const(ty, 1.0)
        assert isinstance(ty, IntType)
        return builder.int_const(ty, 1)

    def _raised_by(self, builder: IRBuilder, base: Value, ty: Type, times: int,
                   span: Span) -> Value:
        """Raise something to a power written down, with no loop.

        Squaring and multiplying, which is one multiplication per bit of the
        exponent and one more per bit that is set -- eleven for a cube and four
        for a fourteenth power, where multiplying it out would be thirteen.

        Squaring cannot go past the end of the type where the answer does not.
        Every square this computes is a power the answer itself contains, and
        for an integer of magnitude at least two a smaller power is a smaller
        number; for the three integers where it is not, and for a
        floating-point value of magnitude below one, every power is at most one.
        So the check each multiplication carries reports the answer and never a
        step towards it.
        """
        if times == 0:
            return self._unity(builder, ty, span)
        found: Value | None = None
        power = base
        left = times
        while True:
            if left & 1:
                found = power if found is None else builder.binary(
                    self._wrapped(BinOp.MUL), found, power, span)
            left >>= 1
            if not left:
                assert found is not None
                return found
            power = builder.binary(self._wrapped(BinOp.MUL), power, power, span)

    def _raised_over(self, builder: IRBuilder, base: Value, ty: Type,
                     times: Value, count: IntType, answer: Type,
                     span: Span) -> Value:
        """Raise something to a power the program works out, which is a loop.

        The same squaring and multiplying the written-down form is, with the
        exponent in a register: a turn looks at its lowest bit, folds the
        running square into the answer where it is set, and squares the running
        square for the turn after.  Sixty-four turns at the very most, and as
        many as the exponent has bits in practice.

        **The squaring is not done on the last turn**, and that is not an
        optimization: squaring one more time than the answer needs would be
        going past the end of the type for an answer that fits, which would stop
        a program that was right.  So the test for another turn comes between
        the two multiplications rather than at the top of the loop.

        **What the loop walks is the exponent's magnitude**, and the sign is
        looked at twice: once before, to take the magnitude, and once after, to
        divide one by what came out.  The bits are walked with a logical shift
        whatever the type, because what is in the register after the first of
        those is a magnitude and not a signed number -- which is also what makes
        the most negative exponent there is come out right, its magnitude being
        one more than the largest the type holds.
        """
        if not count.signed:
            # No sign to look at: what it walks is what it was given, and there
            # is no answer it has not got.
            return builder.wrap(
                self._power_loop(builder, base, ty, times, count, span),
                builder.bool_const(False), answer, span)
        negative = builder.compare(CmpPred.SLT, times,
                                   builder.int_const(count, 0), span)
        flip = builder.new_block("power.flip")
        keep = builder.new_block("power.keep")
        sized = builder.new_block("power.by")
        builder.condbr(negative, flip, keep, span=span)
        builder.position_at(flip)
        builder.br(sized, (builder.binary(BinOp.WRAP_SUB,
                                          builder.int_const(count, 0), times,
                                          span),), span)
        builder.position_at(keep)
        builder.br(sized, (times,), span)
        builder.position_at(sized)
        magnitude = sized.add_param(count, "times")
        raised = self._power_loop(builder, base, ty, magnitude, count, span)
        over = builder.new_block("power.over")
        plain = builder.new_block("power.plain")
        finish = builder.new_block("power.answered")
        builder.condbr(negative, over, plain, span=span)
        builder.position_at(over)
        # One divided by the power, which is what a negative exponent means and
        # which is the one pair this operator has no answer for.
        divided = self._reciprocal_of(builder, raised, ty, answer, span)
        builder.br(finish, (builder.unwrap(divided, ty, span),
                            builder.failed(divided, span)), span)
        builder.position_at(plain)
        builder.br(finish, (raised, builder.bool_const(False)), span)
        builder.position_at(finish)
        # The two halves rather than the whole, so that what a block hands over
        # is values of the kinds a block hands over.
        value = finish.add_param(ty, "raised")
        bad = finish.add_param(BOOL, "failed")
        return builder.wrap(value, bad, answer, span)

    def _power_loop(self, builder: IRBuilder, base: Value, ty: Type,
                    times: Value, count: IntType, span: Span) -> Value:
        """Squaring and multiplying, with the exponent's magnitude in a
        register."""
        one = self._unity(builder, ty, span)
        start = builder.new_block("raise")
        header = builder.new_block("raising")
        folding = builder.new_block("fold")
        skipping = builder.new_block("skip")
        after = builder.new_block("folded")
        again = builder.new_block("square")
        last = builder.new_block("raised.last")
        none = builder.new_block("raised.none")
        done = builder.new_block("raised")
        # Raised to no power at all is one, and it is the only turn count the
        # loop below cannot take: it enters with at least one bit to look at.
        # A branch that asks a question carries nothing with it, so what the
        # loop starts from is handed over by the block after it.
        builder.condbr(
            builder.compare(CmpPred.EQ, times, builder.int_const(count, 0), span),
            none, start, span=span)
        builder.position_at(none)
        builder.br(done, (one,), span)
        builder.position_at(start)
        builder.br(header, (one, base, times), span)
        builder.position_at(header)
        # What it carries: the answer so far, the power of the base this bit
        # stands for, and the bits left to look at.
        found = header.add_param(ty, "found")
        power = header.add_param(ty, "power")
        left = header.add_param(count, "left")
        builder.condbr(
            builder.compare(CmpPred.NE,
                            builder.binary(BinOp.AND, left,
                                           builder.int_const(count, 1), span),
                            builder.int_const(count, 0), span),
            folding, skipping, span=span)
        builder.position_at(folding)
        builder.br(after, (builder.binary(self._wrapped(BinOp.MUL), found,
                                          power, span),), span)
        builder.position_at(skipping)
        builder.br(after, (found,), span)
        builder.position_at(after)
        carried = after.add_param(ty, "found")
        rest = builder.binary(BinOp.WRAP_LSHR, left,
                              builder.int_const(count, 1), span)
        builder.condbr(
            builder.compare(CmpPred.EQ, rest, builder.int_const(count, 0), span),
            last, again, span=span)
        builder.position_at(last)
        builder.br(done, (carried,), span)
        builder.position_at(again)
        builder.br(header, (carried,
                            builder.binary(self._wrapped(BinOp.MUL), power,
                                           power, span),
                            rest), span)
        builder.position_at(done)
        return done.add_param(ty, "raised")

    def _lower_concat(self, builder: IRBuilder, expr: ast.Binary,
                      expected: Type | None) -> Value:
        """Lower `A \N{DOUBLE PLUS} B`: one array's elements after another's.

        It is not one of the operators the arithmetic path handles and cannot
        be.  Those are defined on values and reach an array by being applied to
        every element of it; this one is defined on arrays themselves, its two
        sides are of two different types, and what it answers with is of a third
        -- so there is nothing about it for that path to share.

        Nothing is wanted of either side.  What each is, is what says how long
        the answer is, so neither can be told what to be by the other or by what
        the whole is wanted to be; an array written out as a side says its own
        type, which is what it would have done anyway.
        """
        left = self._lower_expr(builder, expr.left, None)
        # What is wanted of the right is what the left turned out to be, where
        # that type says nothing about how many there are -- a list and a string
        # do not, so `a ⧺ []` has something to take its type from; an array
        # does, and two arrays being joined are of two different lengths and so
        # of two different types.
        held = self._value_type_of(left)
        want = held if held is STR or isinstance(held, ListType) else None
        right = self._lower_expr(builder, expr.right, want)
        if self._value_type_of(left) is STR or self._value_type_of(right) is STR:
            return self._joined_text(builder, expr, left, right, expected)
        if isinstance(self._value_type_of(left), ListType) \
                or isinstance(self._value_type_of(right), ListType):
            return self._joined_list(builder, expr, left, right, expected)
        joined = self._joined_type(expr, left, right)
        if joined is None:
            return UndefConst(ERROR)
        place = builder.frame(joined, expr.span)
        at = 0
        for side in (left, right):
            ty = self._value_type_of(side)
            assert isinstance(ty, ArrayType) and ty.count is not None
            self._copied(builder, side, ty, place, joined.element, at, expr.span)
            at += ty.count
        found = builder.cast(CastKind.BITCAST, place, joined, expr.span)
        if not self._accepts(expected, joined):
            self._report_mismatch(expr.span, joined, expected)
            return UndefConst(ERROR)
        return found

    def _joined_list(self, builder: IRBuilder, expr: ast.Binary, left: Value,
                     right: Value, expected: Type | None) -> Value:
        """Lower `A \N{DOUBLE PLUS} B` where the two are lists.

        The same shape a join of two strings has, and the same bytes moved: how
        long the answer is, is not known while compiling, so room for it comes
        from the arena.  What differs is only that a list's elements are as wide
        as its type says rather than one byte -- so the two lengths are in
        elements and the copy is in bytes, which is one multiplication apiece.
        """
        holds: Type | None = None
        for side, value in ((expr.left, left), (expr.right, right)):
            found = self._value_type_of(value)
            if not isinstance(found, ListType):
                if found is not ERROR:
                    self._diags.emit(D.LANG_CONCAT_NEEDS_AN_ARRAY, side.span,
                                     found=found.written())
                return UndefConst(ERROR)
            if holds is None:
                holds = found.element
            elif found.element is not holds:
                self._diags.emit(D.LANG_CONCAT_ELEMENTS_DIFFER, expr.span,
                                 left=self._value_type_of(left).written(),
                                 right=self._value_type_of(right).written())
                return UndefConst(ERROR)
        assert holds is not None
        answer = self._module.types.list_type(holds)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        # What the elements of either side point into goes where the answer
        # is made: a side not provably made there is copied in first, and the
        # copy, which only the join reads, is given back after it.
        owner = self._owner_of(self._out_of_arenas)
        spare: list[Value] = []
        made: set[int] = set()
        if owner is not None and owned.points(holds):
            # A side whose elements the answer may not share is copied first,
            # and a run only the join reads -- a copy, or a temporary of the
            # heap's whose elements it takes over -- is given back after it.
            sides: list[Value] = []
            for written, value in ((expr.left, left), (expr.right, right)):
                one, added, moved = self._stored(builder, written, value, answer,
                                                 owner, len(self._scopes), True,
                                                 written.span)
                if self._value_type_of(one) is ERROR:
                    return UndefConst(ERROR)
                sides.append(one)
                made |= added
                if one is not value:
                    spare.append(one)
                    if moved is not None:
                        spare.append(moved)
                elif owner == _HEAP and self._freshly_heap(written):
                    spare.append(one)
            left, right = sides
        self._made_from[id(expr)] = (expr, self._out_of_arenas | made)
        stride = builder.int_const(U64, stride_of(holds, _LAYOUT))
        pointer = parts_of(answer)[0]
        bytes_ = self._module.types.ptr_type(U8, mutable=True)
        counts = [builder.extract(side, 1, U64, expr.span)
                  for side in (left, right)]
        made = strings.joined(
            builder, self._out_of(builder, expr.span),
            (builder.cast(CastKind.BITCAST,
                          builder.extract(left, 0, pointer, expr.span),
                          bytes_, expr.span),
             builder.binary(BinOp.WRAP_MUL, counts[0], stride, expr.span),
             builder.cast(CastKind.BITCAST,
                          builder.extract(right, 0, pointer, expr.span),
                          bytes_, expr.span),
             builder.binary(BinOp.WRAP_MUL, counts[1], stride, expr.span)),
            bytes_, expr.span)
        for one in spare:
            owned.give_back_storage(builder, one, answer,
                                    "heap" if owner == _HEAP else "pool")
        return builder.make_tuple(
            (builder.cast(CastKind.BITCAST, made, pointer, expr.span),
             builder.binary(BinOp.WRAP_ADD, counts[0], counts[1], expr.span),
             self._out_of(builder, expr.span)),
            answer, expr.span)

    def _joined_text(self, builder: IRBuilder, expr: ast.Binary, left: Value,
                     right: Value, expected: Type | None) -> Value:
        """Lower `A \N{DOUBLE PLUS} B` where the two are strings.

        The answer is as long as the two together and that length is not known
        while compiling, so the bytes cannot go where a join of two arrays puts
        them -- room for them is taken from the arena the compiler provides,
        which is where everything that outlives an expression and was not
        written down already comes from.

        Which is why this is a change that outlives the call and a pure function
        may not make one.  A program that joins strings says `@[impure]`, the
        same as one that puts something in a collection.
        """
        for side, value in ((expr.left, left), (expr.right, right)):
            found = self._value_type_of(value)
            if found is not STR and found is not ERROR:
                self._diags.emit(D.LANG_CONCAT_NEEDS_AN_ARRAY, side.span,
                                 found=found.written())
                return UndefConst(ERROR)
        if self._value_type_of(left) is ERROR \
                or self._value_type_of(right) is ERROR:
            return UndefConst(ERROR)
        # The bytes of both are copied, so the answer is made in its arena and
        # nowhere else: what the two halves were made in stays theirs.
        self._made_from[id(expr)] = (expr, self._out_of_arenas)
        pointer = parts_of(STR)[0]
        first = builder.extract(left, 0, pointer, expr.span)
        first_len = builder.extract(left, 1, U64, expr.span)
        second = builder.extract(right, 0, pointer, expr.span)
        second_len = builder.extract(right, 1, U64, expr.span)
        arena = self._out_of(builder, expr.span)
        bytes_ = strings.joined(
            builder, arena, (first, first_len, second, second_len), pointer,
            expr.span)
        found = builder.make_tuple(
            (bytes_, builder.binary(BinOp.WRAP_ADD, first_len, second_len,
                                    expr.span), arena),
            STR, expr.span)
        if not self._accepts(expected, STR):
            self._report_mismatch(expr.span, STR, expected)
            return UndefConst(ERROR)
        return found

    def _joined_type(self, expr: ast.Binary, left: Value,
                     right: Value) -> ArrayType | None:
        """What joining these two answers with, or nothing where they do not join.

        Four things have to hold and each is its own mistake.  Both sides are
        arrays; both say their shape, since how much room the answer takes is
        how long the two are together; both hold the same thing, an array
        holding one type; and both agree about every dimension but the first,
        which is the one the join goes along.
        """
        sides = (self._value_type_of(left), self._value_type_of(right))
        if any(ty is ERROR for ty in sides):
            return None
        for ty, side in zip(sides, (expr.left, expr.right)):
            if not isinstance(ty, ArrayType):
                self._diags.emit(D.LANG_CONCAT_NEEDS_AN_ARRAY, side.span,
                                 found=ty.written())
                return None
            if not ty.fixed:
                self._diags.emit(D.LANG_CONCAT_NEEDS_A_STATED_SHAPE, side.span,
                                 found=ty.written())
                return None
        first, second = sides
        assert isinstance(first, ArrayType) and isinstance(second, ArrayType)
        if first.element is not second.element:
            self._diags.emit(D.LANG_CONCAT_ELEMENTS_DIFFER, expr.span,
                             left=first.written(), right=second.written())
            return None
        if first.shape[1:] != second.shape[1:]:
            self._diags.emit(D.LANG_CONCAT_SHAPES_DIFFER, expr.span,
                             left=first.written(), right=second.written())
            return None
        along = (first.shape[0] or 0) + (second.shape[0] or 0)
        return self._module.types.array_type(first.element,
                                             (along, *first.shape[1:]))

    def _copied(self, builder: IRBuilder, side: Value, ty: ArrayType,
                place: Value, element: Type, at: int, span: Span) -> None:
        """Put the elements of *side* into *place*, starting at the *at*-th.

        Read and written as one value of as many lanes as there are elements,
        which is the same machinery an operator over a whole run uses and comes
        to the same three answers: one instruction where the machine holds that
        many at once, a few where it holds fewer, and an element at a time where
        it holds none.  A copy is a copy whatever the elements are, so this asks
        for it in the one shape every target already knows how to cut up.
        """
        count = ty.count
        assert count is not None
        held = self._module.types.vec_type(element, count)
        pointer = self._module.types.ptr_type(held, mutable=True)
        start, _ = self._shape_of(builder, side, ty, span)
        builder.store(
            builder.cast(CastKind.BITCAST,
                         self._element_place(builder, place, element,
                                             builder.int_const(U64, at), span),
                         pointer, span),
            builder.load(builder.cast(CastKind.BITCAST, start, pointer, span),
                         span),
            span)

    def _lower_binary(self, builder: IRBuilder, expr: ast.Binary,
                      expected: Type | None) -> Value:
        """Lower an operator written between two operands.

        Both sides have the same type and the result has it too, so whichever
        side says what that type is says it for the whole expression.  That is
        what lets a literal without a suffix stand on either side of one that
        has a type, which a rule that only looked leftwards would not allow.
        """
        if expr.op is ast.BinaryOp.CONCAT:
            return self._lower_concat(builder, expr, expected)
        if expr.op is ast.BinaryOp.SHAPE:
            return self._lower_reshape(builder, expr, expected)
        if expr.op is ast.BinaryOp.POWER:
            return self._lower_power(builder, expr, expected)
        if expr.op in _DIVIDES:
            return self._lower_divides(
                builder, expr, expr.left, expr.right, expr.op.value,
                expr.op is ast.BinaryOp.NOT_DIVIDES, expected)
        # An operator is defined on values and not on arrays, so what is wanted
        # of each side is what an array of them would be an array of -- which
        # lets a number stand beside an array and take its element's type.
        context = self._scalar_of(self._aiming_at(expected)) \
            if expected is not None else self._hint_of(expr)
        # A product and a quotient are the two that work a unit out rather than
        # demand one, so while their operands are lowered any unit stands where
        # any other does -- and a literal among them takes no unit at all, `d \N{MULTIPLICATION SIGN} 3`
        # being three of whatever `d` is and not three metres times a metre.
        # What a program said this operator means, where it said anything: asked
        # before either side is lowered, so that the operands go to the
        # definition's parameters and are lowered knowing what those are.  The
        # language's own meaning is not looked up and cannot be replaced -- a
        # definition answers only where the operator had no meaning to begin
        # with, which is what keeps `1u8 + 2u8` the same in every program.
        written = self._operators and self._operator_written(expr.op.value, 2)
        if written and not self._operator_stands(expr, context):
            found = self._operator_call(builder, expr.op.value,
                                        (expr.left, expr.right), expr.span,
                                        expected)
            if found is not None:
                return found
        was_deriving, self._deriving = self._deriving, expr.op in _DERIVES
        outer, self._operand_of = self._operand_of, expr.op.value
        was_listing, self._listing = self._listing, True
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._scalar_of(self._value_type_of(left))
            assert ty is not None
            if ty is not ERROR and not self._operand_type_stands(expr.op, ty):
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.left.span,
                                 operator=expr.op.value, found=ty.written())
                ty = ERROR
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
            self._deriving = was_deriving
            self._listing = was_listing
        if self._wrapping and expr.op in _SATURATES:
            # Both halves have been lowered, so a mistake in either is reported
            # as well; what is reported here is the one thing that is about the
            # operator rather than about its operands.
            self._diags.emit(D.LANG_WRAP_SATURATING_INSIDE, expr.span,
                             operator=expr.op.value)
            return UndefConst(ERROR)
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
                             operator=expr.op.value, found=found.written())
            return UndefConst(ERROR)
        if found is not ty and not (expr.op in _DERIVES
                                    and without_units(found) is without_units(ty)):
            self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, expr.right.span,
                             operator=expr.op.value, expected=ty.written(),
                             found=found.written())
            return UndefConst(ERROR)
        derived: Type | None = None
        if expr.op in _DERIVES and self._value_type_of(left) is ty:
            # The unit of what comes out is worked out from the two that went
            # in: the exponents added for a product and subtracted for a
            # quotient, so a length over a time is a speed and a speed times a
            # time is a length again.  Asked of the operand's own type rather
            # than of the scalar it is one of, because a run of them is a run
            # and what it answers with has to stay one.
            made = (_unit_of(ty).times(_unit_of(found))
                    if expr.op is ast.BinaryOp.MULTIPLY
                    else _unit_of(ty).over(_unit_of(found)))
            if made != _unit_of(ty):
                ty = _carrying(ty, made)
                derived = ty
            # What the operands carried was not measured against what the place
            # wants -- a product takes any two units -- so what came out of them
            # is measured here instead, and this is the only place that can.
            aimed = self._scalar_of(self._aiming_at(expected)) \
                if expected is not None else None
            if aimed is not None and not self._accepts(aimed, ty):
                self._report_mismatch(expr.span, ty, aimed)
                return UndefConst(ERROR)
        if self._answer_is_already_known(expr, ty, left, right):
            return UndefConst(ERROR)
        if isinstance(ty, SetType):
            return self._lower_set_operation(builder, expr.op, ty, left, right,
                                             expr.span)
        if expr.op in _EXTREMA:
            # Which of the two are ordered is the same question a comparison
            # asks, and the answer is the same: numbers and code points.
            if self._comparable(expr.span, ast.BinaryOp.LESS, ty) is ERROR:
                return UndefConst(ERROR)
            signed = isinstance(ty, FloatType) or (isinstance(ty, IntType)
                                                   and ty.signed)
            return builder.binary(_EXTREMA[expr.op][0 if signed else 1],
                                  left, right, expr.span)
        if expr.op in _SHIFTS:
            if expr.op in (ast.BinaryOp.ROTATE_LEFT, ast.BinaryOp.ROTATE_RIGHT) \
                    and isinstance(ty, IntType) and ty.signed:
                # Turning the bits of a signed number round has no meaning as a
                # number, and this language's types say what a value is.
                self._diags.emit(D.LANG_TYPE_ROTATE_IS_UNSIGNED, expr.span,
                                 found=ty.written())
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
                              expr.span, derived)

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
        if op in _EXTREMA:
            # Whatever a comparison orders: the larger of two is the one a
            # comparison would have put second, so the two ask the same thing of
            # their operands.
            return isinstance(ty, (IntType, FloatType)) or ty is CHAR
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
                                 value=str(-ty.low), type=ty.written())
            return False
        working = self._ARITHMETIC.get(expr.op)
        if working is None or not isinstance(ty, IntType):
            return False
        answer = working(left.value, right.value)
        if ty.holds(answer):
            return False
        self._diags.emit(D.LANG_TYPE_ANSWER_DOES_NOT_FIT, expr.span,
                         value=str(answer), type=ty.written())
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
            type=left.ty.written())
        return True

    def _lower_unary(self, builder: IRBuilder, expr: ast.Unary,
                     expected: Type | None) -> Value:
        """Lower an operator written before its operand."""
        # What a program said this operator means before one operand of its own
        # type.  Each of the language's own is lowered by a method of its own
        # below, and none of them works on such a type, so this is asked first and
        # answers only where there was nothing to answer.
        if self._operators \
                and self._operator_written(expr.op.value, 1) is not None \
                and not self._operator_stands_alone(expr):
            found = self._operator_call(builder, expr.op.value, (expr.operand,),
                                        expr.span, expected)
            if found is not None:
                return found
        if expr.op is ast.UnaryOp.LOGIC_NOT:
            return self._lower_not(builder, expr, expected)
        if expr.op is ast.UnaryOp.LENGTH:
            return self._lower_length(builder, expr, expected)
        if expr.op is ast.UnaryOp.SHAPE:
            return self._lower_shape(builder, expr, expected)
        if expr.op in (ast.UnaryOp.MAX, ast.UnaryOp.MIN):
            return self._lower_extremum(builder, expr, expected)
        if expr.op in _ROUNDINGS:
            return self._lower_rounding(builder, expr, expected)
        if expr.op in (ast.UnaryOp.NEXT, ast.UnaryOp.PREV):
            moved = self._lower_expr(builder, expr.operand, None)
            found = self._value_type_of(moved)
            if found is ERROR:
                return UndefConst(ERROR)
            if not isinstance(found, CursorType):
                self._diags.emit(D.LANG_CURSOR_STEP_NOT_A_CURSOR,
                                 expr.operand.span, operator=expr.op.value,
                                 found=found.written())
                return UndefConst(ERROR)
            return self._lower_step(builder, expr, moved, found, expected)
        if expr.op in _DIVIDES_UNARY:
            # The same operator with two on the left, which is what the
            # question "is it even" is.
            return self._lower_divides(
                builder, expr, None, expr.operand, expr.op.value,
                expr.op is ast.UnaryOp.NOT_DIVIDES, expected)
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
                             operator=expr.op.value, found=ty.written())
            return UndefConst(ERROR)
        return builder.unary(_UNARY_OPS[expr.op], operand, span=expr.span)

    def _lower_rounding(self, builder: IRBuilder, expr: ast.Unary,
                        expected: Type | None) -> Value:
        """Lower one of the four roundings: the whole number a number goes to.

        What comes back is of the type it was given -- a rounded `f64` is an
        `f64` and not an integer.  That is the arrangement every machine's
        instruction has, and it is the honest one: which integer type the answer
        would fit in is a question about the value and not about the type, and
        one that turned out wrong would have to stop the program.  A program
        that wants an integer says so, and the conversion is where it is
        written.

        Three of the four say which way they go.  The fourth asks the processor,
        which is state outside the function, so a function that writes it says
        `@[impure]`.

        They are listable, and it costs nothing to say so: the walk over an
        array is the one every operator written before its operand already has,
        and what is different about these is only what they are defined on.
        """
        if expr.op is ast.UnaryOp.ROUNDED:
            self._an_effect(D.LANG_PURE_READS_THE_ROUNDING_MODE, expr.span)
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
        # What it is defined on is never a run of values, so the question is
        # asked of what a run of them is a run of.
        ty = self._scalar_of(self._value_type_of(operand))
        if ty is ERROR or ty is None:
            return UndefConst(ERROR)
        if not isinstance(ty, FloatType):
            self._diags.emit(D.LANG_TYPE_OPERAND_NOT_FLOAT, expr.operand.span,
                             operator=expr.op.value, found=ty.written())
            return UndefConst(ERROR)
        return builder.unary(_ROUNDINGS[expr.op], operand, span=expr.span)

    def _lower_length(self, builder: IRBuilder, expr: ast.Unary,
                      expected: Type | None) -> Value:
        """Lower `#x`: how many things *x* is made of.

        It is not walked over an array the way the other operators written
        before their operand are.  Those are defined on values and reach an
        array by being applied to every element; this one is defined on the
        array itself, and asking it of every element would be asking a different
        question about a different thing.

        Five answers and three of them cost nothing.  A tuple's is how many
        members its type names; an array's is the first number of its shape
        where the type says it, and the count it carries beside the elements
        where it does not; a string's is a walk, there being no arithmetic on
        the number of bytes that gives the number of characters; a table's is a
        field of the table, kept by the two operations that put things in.
        """
        value = self._lower_expr(builder, expr.operand, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        found = self._counted(builder, value, ty, expr.span)
        if found is None:
            self._diags.emit(D.LANG_LENGTH_HAS_NO_COUNT, expr.operand.span,
                             found=ty.written())
            return UndefConst(ERROR)
        if not self._accepts(expected, SIZE_TYPE):
            self._report_mismatch(expr.span, SIZE_TYPE, expected)
            return UndefConst(ERROR)
        # How many there are is a count, and a count is a quantity like any
        # other: it is not a length in metres and it is not which one is wanted,
        # and saying so here is what stops either being written by mistake.
        # A count the compiler worked out is simply made in that unit; one the
        # program works out is the same bits read as it, which costs nothing.
        if isinstance(found, IntConst):
            return builder.int_const(SIZE_TYPE, found.value)
        return builder.cast(CastKind.BITCAST, found, SIZE_TYPE, expr.span)

    def _counted(self, builder: IRBuilder, value: Value, ty: Type,
                 span: Span) -> Value | None:
        """How many things a value is made of, or nothing where it is one thing."""
        if isinstance(ty, TupleType):
            return builder.int_const(U64, len(ty.members))
        if isinstance(ty, ArrayType):
            along = ty.shape[0]
            if along is not None:
                return builder.int_const(U64, along)
            # The outermost dimension, which such an array carries beside where
            # its elements are -- the first count of however many it has.
            return builder.extract(value, 1, U64, span)
        if isinstance(ty, ListType):
            return builder.extract(value, 1, U64, span)
        if ty is STR:
            return builder.call(
                strings.length_function(self._module),
                (builder.extract(value, 0, parts_of(STR)[0], span),
                 builder.extract(value, 1, U64, span)),
                U64, span)
        if isinstance(ty, (SetType, DictType)):
            # What a program holds is where the table is; the count is a field
            # of the table, kept by the two operations that put things in.
            return tables.count_of(
                builder, builder.cast(CastKind.BITCAST, value,
                                      tables.table_type(self._module), span))
        return None

    def _lower_extremum(self, builder: IRBuilder, expr: ast.Unary,
                        expected: Type | None) -> Value:
        """Lower `\N{LEFT CEILING}x` and `\N{LEFT FLOOR}x`: the largest or the smallest of what it holds.

        What "of what it holds" means is the thing's own business, and every
        answer is the one a reader would give: the elements of a vector, the
        characters of a string, the keys of a set or a dictionary, the members
        of a tuple.  An array of more than one dimension is the one that is not
        obvious and is the one APL settles: its outermost dimension is walked
        and the elements underneath are compared with each other, so what comes
        back has the shape of one of its rows.  That works however deep the
        array goes, a row of a row being a row.

        Three ways of doing it, decided by what is known while compiling.  A
        tuple's members and a fixed array's elements are known one by one, so
        the comparisons are written out.  Everything else is a loop, which is
        also where a thing with nothing in it has to be reported: the largest of
        nothing is not a value, so the program stops.
        """
        if isinstance(expr.operand, ast.Lifted):
            return self._end_of_a_type(expr, expr.operand, expected)
        value = self._lower_expr(builder, expr.operand, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        found = self._extremum_of(builder, expr, value, ty)
        if found is None:
            self._diags.emit(D.LANG_EXTREMUM_HAS_NONE, expr.operand.span,
                             operator=expr.op.value, found=ty.written())
            return UndefConst(ERROR)
        answer = self._value_type_of(found)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return found

    def _end_of_a_type(self, expr: ast.Unary, lifted: ast.Lifted,
                       expected: Type | None) -> Value:
        """Lower `\N{LEFT CEILING}\N{TOP LEFT CORNER}u8\N{TOP RIGHT CORNER}`: the largest or the smallest value a type has.

        The same operator and the same word.  Written before something that
        holds several values it answers the largest of them; written before a
        *type* it answers the largest the type has -- which is the largest of
        its values, so nothing had to be decided about what the word means.

        For a floating-point type the smallest is the most negative number it
        holds and not the smallest positive one.  C++ calls the second `min()`
        and the first `lowest()`, and the number of programs that have reached
        for `min()` and got a tiny positive number is the argument for not
        repeating it: `\N{LEFT FLOOR}` means the smallest, and a value below it is not one.
        """
        ty = self._lifted_type(lifted)
        if ty is None:
            self._diags.emit(D.LANG_EXTREMUM_HAS_NONE, lifted.span,
                             operator=expr.op.value, found="what was lifted")
            return UndefConst(ERROR)
        largest = expr.op is ast.UnaryOp.MAX
        if isinstance(ty, IntType):
            found: Value = IntConst(ty, ty.high if largest else ty.low)
        elif ty is CHAR:
            found = self._module.char_const(MAX_CODE_POINT if largest else 0)
        elif isinstance(ty, FloatType):
            # The largest finite number of the format, and its negation: what
            # is beyond either is an infinity, which is not a value a program
            # of this language may hold.
            widest = (3.4028234663852886e38 if ty.bits == 32
                      else 1.7976931348623157e308)
            found = FloatConst(ty, widest if largest else -widest)
        else:
            self._diags.emit(D.LANG_EXTREMUM_HAS_NONE, lifted.span,
                             operator=expr.op.value, found=ty.written())
            return UndefConst(ERROR)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        return found

    def _extremum_of(self, builder: IRBuilder, expr: ast.Unary, value: Value,
                     ty: Type) -> Value | None:
        """The largest or smallest of what a value holds, or nothing where it
        holds nothing that can be compared."""
        if isinstance(ty, TupleType):
            if not ty.members or any(m is not ty.members[0] for m in ty.members):
                return None
            if not self._ordered(ty.members[0]):
                return None
            return self._folded(builder, expr,
                                [builder.extract(value, at, member, expr.span)
                                 for at, member in enumerate(ty.members)],
                                ty.members[0])
        if isinstance(ty, ArrayType):
            # A type that says its shape is walked while compiling, whatever its
            # rank; one that does not says neither how many there are nor how
            # long a row is, so only the one-dimensional case is left and it is
            # a loop like a list's.
            if ty.fixed:
                return self._extremum_of_array(builder, expr, value, ty)
            if ty.rank > 1:
                self._diags.emit(
                    D.IMPL_UNIMPLEMENTED_FEATURE, expr.span,
                    feature=("the largest or smallest of an array of more than "
                             "one dimension whose type does not say its shape"))
                return UndefConst(ERROR)
            return self._extremum_walked(builder, expr, value, ty)
        if isinstance(ty, (ListType, SetType, DictType)) or ty is STR:
            return self._extremum_walked(builder, expr, value, ty)
        return None

    def _ordered(self, ty: Type) -> bool:
        """Whether a comparison puts two of these in an order, which is what
        being the largest of several means."""
        return isinstance(ty, (IntType, FloatType)) or ty is CHAR

    def _folded(self, builder: IRBuilder, expr: ast.Unary,
                values: Sequence[Value], element: Type) -> Value:
        """The largest or smallest of values known one by one."""
        op = self._extremum_op(expr, element)
        found = values[0]
        for one in values[1:]:
            found = builder.binary(op, found, one, expr.span)
        return found

    def _extremum_of_array(self, builder: IRBuilder, expr: ast.Unary,
                           value: Value, ty: ArrayType) -> Value | None:
        """The largest or smallest of an array whose shape its type states.

        One dimension answers one value.  More than one answers a row: the
        outermost dimension is walked and the elements underneath are compared
        with each other, so the *j*-th of the answer is the largest of the
        *j*-th of every row.  Nothing here is a loop -- both counts are in the
        type -- and nothing is recursive either, row-major making a row of a row
        a run of elements and every one of them reachable by one index.
        """
        if not self._ordered(ty.element):
            return None
        along = ty.shape[0]
        assert along is not None
        start, _ = self._shape_of(builder, value, ty, expr.span)
        wide = 1
        for further in ty.shape[1:]:
            assert further is not None
            wide *= further

        def held(row: int, at: int) -> Value:
            return builder.load(
                self._element_place(builder, start, ty.element,
                                    builder.int_const(U64, row * wide + at),
                                    expr.span), expr.span)

        if ty.rank == 1:
            return self._folded(builder, expr,
                                [held(0, at) for at in range(along)], ty.element)
        answer = self._module.types.array_type(ty.element, ty.shape[1:])
        place = builder.frame(answer, expr.span)
        for at in range(wide):
            builder.store(
                self._element_place(builder, place, ty.element,
                                    builder.int_const(U64, at), expr.span),
                self._folded(builder, expr,
                             [held(row, at) for row in range(along)],
                             ty.element),
                expr.span)
        return builder.cast(CastKind.BITCAST, place, answer, expr.span)

    def _extremum_walked(self, builder: IRBuilder, expr: ast.Unary, value: Value,
                         ty: Type) -> Value | None:
        """The largest or smallest of something whose count is not known while
        compiling, which is a loop.

        What it starts from is the first thing there is, and the walk then
        includes that thing again -- comparing something with itself answering
        itself, so a turn spent on it costs one instruction and saves needing a
        value of the type to start from.  That matters more than it looks: the
        value to start from would have to be the end of the type, which every
        integer has and which a floating-point type has only as an infinity and
        a character type only by knowing what a code point may be.

        Taking the first thing is what a thing with nothing in it cannot do, so
        that is asked before anything else and stops the program -- the largest
        of nothing not being a value of any type.
        """
        if isinstance(ty, ListType):
            found = self._over_a_list(builder, value, ty, expr.span)
        elif ty is STR:
            found = self._over_a_string(builder, value, expr.span)
        elif isinstance(ty, ArrayType):
            found = self._over_an_array(builder, value, ty, expr.span)
        else:
            assert isinstance(ty, (SetType, DictType))
            found = self._over_a_table(builder, value, ty, expr.span)
        element = found.element
        if isinstance(ty, DictType):
            # A turn of a dictionary gives a key and what it stands for; which
            # of the two is being looked at is the key, as the instruction says.
            assert isinstance(element, TupleType)
            element = element.members[0]
        if not self._ordered(element):
            return None
        # How many there are, except for a string, where that is a walk of its
        # own and all this asks is whether there is one: no bytes is no
        # characters, and any byte is at least one character.
        count = (builder.extract(value, 1, U64, expr.span) if ty is STR
                 else self._counted(builder, value, ty, expr.span))
        assert count is not None
        builder.check(
            builder.compare(CmpPred.NE, count, builder.int_const(U64, 0),
                            expr.span),
            "".join(("the ", "largest" if expr.op is ast.UnaryOp.MAX
                     else "smallest", " of nothing")),
            statuses.OUT_OF_RANGE, expr.span)
        op = self._extremum_op(expr, element)
        header = builder.new_block("finding")
        body = builder.new_block("comparing")
        done = builder.new_block("found")
        builder.br(header, (*found.start,
                            self._offered(builder, found, found.start, ty,
                                          element, expr.span),
                            builder.memory()), expr.span)
        builder.position_at(header)
        state = tuple(header.add_param(self._value_type_of(one), "at")
                      for one in found.start)
        best = header.add_param(element, "best")
        builder.set_memory(header.add_param(MEM, "mem"))
        builder.condbr(found.more(builder, state), body, done, span=expr.span)
        builder.position_at(body)
        one = self._offered(builder, found, state, ty, element, expr.span)
        builder.br(header, (*found.step(builder, state),
                            builder.binary(op, best, one, expr.span),
                            builder.memory()), expr.span)
        builder.position_at(done)
        return best

    def _offered(self, builder: IRBuilder, found: _Iteration,
                 state: tuple[Value, ...], ty: Type, element: Type,
                 span: Span) -> Value:
        """What one turn of a walk offers to be compared."""
        one = found.take(builder, state)
        return (builder.extract(one, 0, element, span)
                if isinstance(ty, DictType) else one)

    def _extremum_type(self, of: Type | None) -> Type | None:
        """What one of these operators answers with, given what it is asked of.

        Said here and used before anything is lowered, so that a comparison
        against what it answers knows what to want of the other side.  What it
        does is state the rule the lowering follows: one of what was looked
        through, except where what was looked through has rows, where it is one
        of its rows.
        """
        if isinstance(of, TupleType):
            return of.members[0] if of.members else None
        if isinstance(of, ArrayType):
            if of.rank == 1:
                return of.element
            return (self._module.types.array_type(of.element, of.shape[1:])
                    if of.fixed else None)
        if isinstance(of, ListType):
            return of.element
        if isinstance(of, SetType):
            return of.element
        if isinstance(of, DictType):
            return of.key
        return CHAR if of is STR else None

    def _extremum_op(self, expr: ast.Unary, element: Type) -> BinOp:
        """Which of the four instructions one of these operators is, over this
        type: the two of them times signed and unsigned, a character being the
        unsigned number it is stored as."""
        signed = isinstance(element, FloatType) or (isinstance(element, IntType)
                                                    and element.signed)
        return _EXTREMA[ast.BinaryOp.MAX if expr.op is ast.UnaryOp.MAX
                        else ast.BinaryOp.MIN][0 if signed else 1]

    def _lower_shape(self, builder: IRBuilder, expr: ast.Unary,
                     expected: Type | None) -> Value:
        """Lower `\N{APL FUNCTIONAL SYMBOL RHO}x`: how many there are along each of its dimensions.

        What it answers is what it takes on the other side of itself: a number
        for one dimension and a tuple of numbers for more, which is exactly what
        may be written on the left of the two-sided form.  So `(\N{APL FUNCTIONAL SYMBOL RHO}a) \N{APL FUNCTIONAL SYMBOL RHO} b` is
        well formed for any array `a`, and that is not a coincidence -- it is
        the reason the one glyph does both jobs in APL and here.

        A tuple of one is not a thing this language has, which is why one
        dimension answers the number itself.  APL answers a vector of one there
        and can, its arrays having no types to agree with.
        """
        value = self._lower_expr(builder, expr.operand, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, ArrayType):
            self._diags.emit(D.LANG_SHAPE_HAS_NONE, expr.operand.span,
                             found=ty.written())
            return UndefConst(ERROR)
        along = [builder.int_const(U64, count) if count is not None
                 else builder.extract(value, at + 1, U64, expr.span)
                 for at, count in enumerate(ty.shape)]
        found = along[0] if ty.rank == 1 else builder.make_tuple(
            along, self._module.types.tuple_type((U64,) * ty.rank), expr.span)
        answer = self._value_type_of(found)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return found

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
        # An operator a program defined for a type of its own answers what the
        # definition answers, and asking the language what one of its operators
        # answers would be asking the wrong question: `⍴p` over a record is a
        # `u64` because the definition says so.
        #
        # Before the match and not a case in it: a case would be two tests on
        # every hint of every program, and a program that defined no operator
        # should pay one -- which is the empty table.
        if self._operators and isinstance(expr, (ast.Unary, ast.Binary)):
            found = self._answers_written(expr)
            if found is not None:
                return found
        match expr:
            case ast.Fresh():
                # It has no meaning but the definition's, so what it answers is
                # what the definition answers -- and nothing where the definition
                # is generic, an instantiation being what settles that.
                written = self._operator_written(expr.glyph,
                                                 len(expr.operands))
                if not isinstance(written, Function):
                    return None
                return None if written.ty.ret is VOID else written.ty.ret
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
            case ast.Quote():
                return SYNTAX
            case ast.Call() if isinstance(expr.callee, ast.NameRef) \
                    and expr.callee.name == TYPENAME_NAME:
                return STR
            case ast.Call() if isinstance(expr.callee, ast.NameRef) \
                    and expr.callee.name in _SYNTAX_ANSWERS:
                return _SYNTAX_ANSWERS[expr.callee.name]
            case ast.Call() if isinstance(expr.callee, ast.NameRef):
                # What a call answers with, which its callee says.  A record
                # written out is a call of the type's name, so this is what lets a
                # program's own operator be written between two of them.
                named = self._top.get(expr.callee.name)
                if isinstance(named, _NamedType):
                    found = self._resolved(named)
                    return None if found is ERROR else found
                return named.ty.ret if isinstance(named, Function) else None
            case ast.AddressOf() if self._operators:
                # A reference to the place a name stands for, which is a pointer
                # to whatever that holds.  The other direction of the mark below,
                # and what lets an operator a program wrote for `&mut T` be found
                # where one written for `T` already was.
                #
                # Only where a program defined an operator, because that is the
                # only thing that reads it and because making the type is not
                # free: it is interned under a key holding the type it points at,
                # so asking costs a hash of that type.  Every program takes
                # references and almost none defines an operator.
                found = self._hint_of(expr.operand)
                return None if found is None or found is ERROR \
                    else self._module.types.ptr_type(found, mutable=expr.mutable)
            case ast.Deref():
                # What is at the place a reference names, which its type says
                # without anything being read.  It is what lets `0 = r⌖` mean
                # what `r⌖ = 0` means.
                found = self._hint_of(expr.operand)
                return found.pointee if isinstance(found, PtrType) else None
            case ast.Unary() if expr.op is ast.UnaryOp.LOGIC_NOT:
                return BOOL
            case ast.Unary() if expr.op in _DIVIDES_UNARY:
                # Whether two divides it, which is a truth value about a number
                # and not a number.  Two is never zero, so there is nothing for
                # it to have no answer for.
                return BOOL
            case ast.Binary() if expr.op in _DIVIDES:
                # A truth value where the divisor is written down, and a result
                # where it is not: what it answers is what the compiler can see
                # about the divisor.
                if isinstance(expr.left, ast.IntLit):
                    return BOOL
                found = self._hint_of(expr.left) or self._hint_of(expr.right)
                return (None if found is None
                        else self._module.types.result_type(BOOL, found))
            case ast.Unary() if expr.op is ast.UnaryOp.LENGTH:
                # How many, which is a count and not whatever was counted --
                # and the unit says so, `\N{CURRENCY SIGN}size` being what a count is measured in.
                return SIZE_TYPE
            case ast.Unary() if expr.op in (ast.UnaryOp.MAX, ast.UnaryOp.MIN):
                # One of what was looked through, which is not what was looked
                # through -- and where what was looked through has rows, one of
                # its rows.
                return self._extremum_type(self._hint_of(expr.operand))
            case ast.Binary() if expr.op is ast.BinaryOp.OR_ELSE:
                # What `??` answers with is the answer inside the result, which
                # is the left side's type with the mark taken off.
                found = self._hint_of(expr.left)
                return found.ok if isinstance(found, ResultType) else found
            case ast.Try():
                found = self._hint_of(expr.operand)
                return found.ok if isinstance(found, ResultType) else found
            case ast.Raised():
                # A power of a written-down number is a value of what was
                # raised, except where the number is negative: that is a
                # division, and a division answers a result.
                found = self._hint_of(expr.base)
                if found is None or expr.exponent >= 0:
                    return found
                return self._module.types.result_type(found)
            case ast.Binary() if expr.op is ast.BinaryOp.POWER:
                # The operator takes an exponent it cannot see, so what it
                # answers is a result whatever the exponent turns out to be.
                found = self._hint_of(expr.left)
                return (None if found is None
                        else self._module.types.result_type(found))
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
            return self._held_by(local)
        found = self._provided(name)
        return (self._value_type_of(found) if isinstance(found, GlobalVar)
                else None)

    def _float_literal_type(self, expr: ast.FloatLit,
                            expected: Type | None) -> FloatType | None:
        """The type a floating-point literal has, from its suffix or its place."""
        expected = self._aiming_at(expected)
        named = BUILTIN_TYPES.get(expr.type_name) if expr.type_name is not None else None
        if named is not None and expected is not None \
                and named is not without_units(expected):
            # A suffix names a type and says nothing about a unit, so what it
            # has to agree with is the type the place wants and not the unit.
            self._report_mismatch(expr.span, named, expected)
            return None
        found = named if named is not None else expected
        if found is None:
            self._diags.emit(
                D.IMPL_UNIMPLEMENTED_FEATURE, expr.span,
                feature=("a floating-point literal with neither a type suffix nor a "
                         "context that gives it a type"))
            return None
        if found is ERROR:
            return None
        if not isinstance(found, FloatType):
            self._report_mismatch(expr.span, named or F64, found)
            return None
        # A number written down beside a product or a quotient counts nothing,
        # which is the rule an integer literal follows in the same place.
        return found.bare if self._deriving else found

    def _literal_type(self, expr: ast.IntLit, expected: Type | None) -> IntType | None:
        """The type an integer literal has, from its suffix or from the context.

        A suffix says what the literal is; a context says what is wanted.  Where
        both are present they must agree, and where neither is the literal is an
        untyped value, which this compiler does not have yet.
        """
        expected = self._aiming_at(expected)
        if expected is ERROR:
            # Something this literal would have taken its type from is wrong,
            # and that has been reported.  A literal with nowhere to look is a
            # thing this compiler has not got, but that is not what happened.
            return None
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
        # A number written down beside a product or a quotient counts nothing:
        # doubling a length gives a length, and it would be a length times a
        # length if the literal took the unit standing beside it.
        return chosen.bare if self._deriving else chosen

    def _lower_bit_count(self, builder: IRBuilder, expr: ast.Call,
                         expected: Type | None) -> Value:
        """Lower `⎕ones(N)` and `⎕lead(N)`: how many of a value's bits are set,
        and how many zeroes stand above the highest one that is.

        **Both count over the value's own type.**  A `u8` whose bits are all set
        has eight of them, and the answer for a wider type holding the same
        number is different -- which is the only thing either could sensibly
        mean, the bits above a value's type not being the value's.

        **`⎕lead` of nought is the width of the type.**  Two of the three
        architectures have an instruction whose answer for nought is whatever
        was in the register, and one has none at all; saying what it is here is
        what makes the three agree, and the answer that makes the count of
        leading zeroes a count of leading zeroes.

        The answer is a `u8` whatever the type asked about: the widest count
        there is is sixty-four, and a count is not of the same kind as the thing
        counted -- adding it to the value it came from would be adding a length
        to a number.
        """
        name = expr.callee.name if isinstance(expr.callee, ast.NameRef) else ""
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=name, expected=1, found=len(expr.args))
            return UndefConst(ERROR)
        given = self._lower_expr(builder, expr.args[0], None)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, IntType):
            self._diags.emit(D.LANG_BITS_NOT_AN_INTEGER, expr.args[0].span,
                             name=name, found=found.written())
            return UndefConst(ERROR)
        if not self._accepts(expected, U8):
            self._report_mismatch(expr.span, U8, expected)
            return UndefConst(ERROR)
        op = UnOp.COUNT_ONES if name == ONES_NAME else UnOp.COUNT_LEADING
        if isinstance(given, IntConst):
            # A number the program wrote down is counted here, where counting it
            # is one line, rather than in the instructions of three backends.
            return builder.int_const(U8, _counted(op, given.value, found))
        return builder.unary(op, given, U8, expr.span)

    def _lower_code_point(self, builder: IRBuilder, expr: ast.Call,
                          expected: Type | None) -> Value:
        """Lower `\N{APL FUNCTIONAL SYMBOL QUAD}ord(C)` and `\N{APL FUNCTIONAL SYMBOL QUAD}chr(N)`: the two conversions between a code
        point and the number Unicode gave it.

        The two are not each other's mirror image and that is the whole of what
        they are about.  Every code point is a number, so `\N{APL FUNCTIONAL SYMBOL QUAD}ord` cannot fail and
        is the same bits read as another type; not every number is a code point,
        so `\N{APL FUNCTIONAL SYMBOL QUAD}chr` checks and stops the program where what it was given is not
        one.  Which of the two directions can fail is why neither is written as
        an assignment: a conversion that stops the program is a thing a reader
        should be able to see.
        """
        name = expr.callee.name if isinstance(expr.callee, ast.NameRef) else ""
        to_a_number = name == ORD_NAME
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CHAR_TAKES_ONE, expr.span, name=name,
                             found=len(expr.args))
            return UndefConst(ERROR)
        wanted = CHAR if to_a_number else U32
        given = self._lower_expr(builder, expr.args[0], wanted)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if found is not wanted:
            self._report_mismatch(expr.args[0].span, wanted, found)
            return UndefConst(ERROR)
        answer = U32 if to_a_number else CHAR
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        if isinstance(given, CharConst) and to_a_number:
            return builder.int_const(U32, given.value)
        if not to_a_number:
            # There is a last code point and it is not the last number, so every
            # number that is not written down has to be asked.  One written down
            # is asked here instead, and a program that cannot answer is one
            # that need not be built.
            if isinstance(given, IntConst):
                return self._code_point(builder, given.value, expr.args[0].span)
            builder.check(
                builder.compare(CmpPred.ULE, given,
                                builder.int_const(U32, MAX_CODE_POINT), expr.span),
                "a number that is not a code point",
                statuses.NOT_A_CODE_POINT, expr.span)
        return builder.cast(CastKind.BITCAST, given, answer, expr.span)

    def _lower_list(self, builder: IRBuilder, expr: ast.ListLit,
                    expected: Type | None) -> Value:
        """Lower `[a, b, c]`: a list written down.

        What it holds is what its elements turned out to be, or what is wanted
        of it where nothing is written inside.  For now they must all be of one
        type: a list is the sequence whose elements need not be, and what makes
        that work is boxing, which this compiler does not do yet.  The type it
        holds is recorded all the same -- when boxing arrives, the case where
        they do agree is the one worth not boxing, and a compiler that had
        thrown the type away could not find it.

        The elements go in an arena, because how many there are is not in the
        type and room for them cannot be taken where the list is written.  So
        making one is a change that outlives the call, as making a collection
        is.
        """
        wanted = self._aiming_at(expected)
        holds = wanted.element if isinstance(wanted, ListType) else None
        if holds is None:
            # What one of them says is what they all are, read off the writing
            # so that an entry may take its type from one written after it.
            holds = self._said_by(expr.elements)
        values: list[Value] = []
        for written in expr.elements:
            one = self._lower_expr(builder, written, _taken_from(written, holds))
            found = self._value_type_of(one)
            if found is ERROR:
                return UndefConst(ERROR)
            if holds is None:
                holds = found
            elif found is not holds:
                self._diags.emit(D.LANG_LIST_ELEMENTS_DIFFER, written.span,
                                 found=found.written(), wanted=holds.written())
                return UndefConst(ERROR)
            values.append(one)
        if holds is None:
            self._diags.emit(D.LANG_LIST_HOLDS_NOTHING, expr.span)
            return UndefConst(ERROR)
        answer = self._module.types.list_type(holds)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        # The list is made in the arena `in` named, or `⎕heap`, and what its
        # elements point into is there too.
        owner = self._owner_of(self._out_of_arenas)
        made: set[int] = set()
        if owner is not None and owned.points(holds):
            stored: list[Value] = []
            for written, one in zip(expr.elements, values):
                kept, added, spare = self._stored(builder, written, one, holds, owner,
                                                  len(self._scopes), True,
                                                  written.span)
                if self._value_type_of(kept) is ERROR:
                    return UndefConst(ERROR)
                made |= added
                if spare is not None:
                    owned.give_back(builder, spare, holds,
                                    "heap" if self._freshly_heap(written) else None)
                stored.append(kept)
            values = stored
        elements = self._room_for(builder, holds, len(values), expr.span)
        for at, one in enumerate(values):
            builder.store(
                self._element_place(builder, elements, holds,
                                    builder.int_const(U64, at), expr.span),
                one, expr.span)
        self._made_from[id(expr)] = (expr, self._out_of_arenas | made)
        return builder.make_tuple(
            (elements, builder.int_const(U64, len(values)),
             self._out_of(builder, expr.span)), answer, expr.span)

    def _room_for(self, builder: IRBuilder, element: Type, count: Value | int,
                  span: Span) -> Value:
        """Room in the arena for *count* elements, as a place to put them."""
        stride = stride_of(element, _LAYOUT)
        room = (builder.int_const(U64, count * stride) if isinstance(count, int)
                else builder.binary(BinOp.WRAP_MUL, count,
                                    builder.int_const(U64, stride), span))
        return builder.cast(
            CastKind.BITCAST,
            tables.allocate(builder, self._out_of(builder, span), room),
            self._module.types.ptr_type(element, mutable=True), span)

    def _written_text(self, builder: IRBuilder, text: str, span: Span) -> Value:
        """A string written down: where its bytes are, and how many there are.

        The bytes go in the image, once per distinct text -- two literals that
        say the same thing are the same bytes, which costs nothing to arrange
        and is what a program that writes one string in several places would
        expect.  They are read-only in the sense that nothing can reach them to
        write: a `str` has no index and no way to take a place out of one.

        The encoding is done here, by the compiler, which is what makes the
        invariant that a `str` is well-formed UTF-8 true by construction rather
        than by inspection.
        """
        found = self._text_variable(text)
        bytes_ = builder.cast(
            CastKind.BITCAST, builder.address(found, span),
            self._module.types.ptr_type(U8, mutable=True), span)
        return builder.make_tuple(
            (bytes_, builder.int_const(U64, len(text.encode("utf-8"))),
             tables.no_allocator(builder)), STR, span)

    def _text_variable(self, text: str) -> GlobalVar:
        """The variable of the image text written down is the bytes of, made once
        per distinct text."""
        found = self._module.texts.get(text)
        if found is None:
            data = text.encode("utf-8")
            held = self._module.types.array_type(U8, (len(data),))
            found = self._module.add_global(GlobalVar(
                name="".join((TEXT_SYMBOL, str(len(self._module.texts)))),
                value_type=held,
                ptr_type=self._module.types.ptr_type(held, mutable=True),
                initializer=self._module.array_const(
                    held, [self._module.int_const(U8, byte) for byte in data]),
                linkage=Linkage.INTERNAL))
            self._module.texts[text] = found
        return found

    def _code_point(self, builder: IRBuilder, value: int, span: Span) -> Value:
        """The code point *value*, or a report where there is no such code point.

        Both ends are checked and neither is about how wide the type is.  The
        last code point is U+10FFFF, which is where UTF-16's surrogate pairs
        stop and which every encoding has had to agree with since; below zero
        there are none at all.
        """
        if not 0 <= value <= MAX_CODE_POINT:
            self._diags.emit(D.LANG_CHAR_OUTSIDE_UNICODE, span,
                             value=str(value), last=format(MAX_CODE_POINT, "X"))
            return builder.module.char_const(0)
        return builder.module.char_const(value)

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
        # Every name the compiler provides begins with the quad and no name a
        # program may write does, so one character says whether any of these can
        # match.  A call of something the program wrote is the common case and it
        # is what the test is here for: it was fifteen comparisons before.
        called = expr.callee
        if isinstance(called, ast.NameRef) \
                and called.name.startswith(BUILTIN_GLYPH):
            given = called.name
            if given == WRAP_NAME:
                return self._lower_wrap(builder, expr, expected)
            if given in (ORD_NAME, CHR_NAME):
                return self._lower_code_point(builder, expr, expected)
            if given in (ONES_NAME, LEAD_NAME):
                return self._lower_bit_count(builder, expr, expected)
            if given in (DROP_NAME, UNIT_NAME):
                return self._lower_unit_call(builder, expr, expected)
            if given == ENTRY_NAME:
                return self._lower_at_entry(builder, expr, expected)
            if given == NARROW_NAME:
                return self._lower_narrow(builder, expr, expected)
            if given == WIDEN_NAME:
                return self._lower_widen(builder, expr, expected)
            if given == SYSCALL_NAME:
                return self._lower_syscall(builder, expr, expected)
            if given in (ACQUIRE_NAME, RELEASE_NAME):
                return self._lower_ordered(builder, expr, expected)
            if given in (AT_NAME, SPAN_NAME):
                return self._lower_place_at(builder, expr, expected)
            if given == ADDRESS_NAME:
                return self._lower_address_of(builder, expr, expected)
            if given == ITER_NAME:
                return self._lower_iter(builder, expr, expected)
            if given == BYTES_NAME:
                return self._lower_bytes(builder, expr, expected)
            if given == STR_OF_NAME:
                return self._lower_str_of(builder, expr, expected)
            if given == EMPTY_NAME:
                return self._lower_empty(builder, expr, expected)
            if given == FIELDS_NAME:
                return self._lower_fields(builder, expr, expected)
            if given == TYPENAME_NAME:
                return self._lower_typename(builder, expr, expected)
            if given == IS_RECORD_NAME:
                # Reaching here means it stood where a value was wanted, a
                # condition the compiler settles never lowering what is in it.
                self._diags.emit(D.LANG_ISRECORD_OUTSIDE_COMPTIME, expr.span)
                return UndefConst(ERROR)
            if given in _ASKS_ABOUT_A_PIECE:
                return self._lower_asked(builder, expr, given, expected)
            if given == TYPEOF_NAME:
                # Reaching here means it stood somewhere a value was wanted,
                # since a condition the compiler settles never lowers what is in
                # it.
                self._diags.emit(D.LANG_TYPEOF_OUTSIDE_COMPTIME, expr.span)
                return UndefConst(ERROR)
        if isinstance(expr.callee, ast.NameRef):
            named = self._top.get(expr.callee.name)
            if isinstance(named, _NamedType) \
                    and isinstance(self._resolved(named), ProductType):
                return self._lower_record(builder, expr,
                                          self._resolved(named), expected)
            if isinstance(named, _NamedType) \
                    and isinstance(self._resolved(named), SumType):
                return self._lower_sum(builder, expr,
                                       self._resolved(named), expected)
        if isinstance(expr.callee, ast.Member):
            # A record another module defined, written the way one defined here
            # is: what the mark before a field means does not change with where
            # the type was written down.
            through = self._type_exported(expr.callee)
            if isinstance(through, ProductType):
                return self._lower_record(builder, expr, through, expected)
        held = self._callee_value(expr.callee)
        if held is not None:
            return self._lower_indirect(builder, expr, held, expected)
        # A function whose types this call settles, which is not a function yet:
        # what it comes to depends on what the arguments turn out to be, so the
        # call is what makes it.  Reached by a bare name or through the module it
        # was imported from, those being the two ways any name is reached.
        template = self._generic_named(expr.callee)
        if template is not None:
            return self._lower_generic(builder, expr, template, expected)
        func = self._callee(expr.callee)
        if func is None:
            return UndefConst(ERROR)
        return self._through(builder, expr, func, expected)

    def _through(self, builder: IRBuilder, expr: ast.Call, func: Function,
                 expected: Type | None) -> Value:
        """Lower a call whose callee is already known to be *func*.

        Apart from the ordinary path because an operator reaches it by another
        route: what a program's `+` is, is looked up by the glyph and the number
        of operands rather than by a name a call wrote.
        """
        wanted = func.ty.params
        # Left to right, one argument at a time, a tuple handed over as several
        # becoming them where it stands.  Which parameter an argument goes to is
        # therefore known by the time it is lowered, which is what an unsuffixed
        # literal needs, and nothing is worked out before something written to
        # its left.
        outer_listing, self._listing = self._listing, func.attrs.listable
        try:
            args = self._given_arguments(builder, expr, func)
        finally:
            self._listing = outer_listing
        if args is None:
            return UndefConst(ERROR)
        if any(value.ty is ERROR for value in args):
            return UndefConst(ERROR)
        # Asked before the walk and not after, a walked call being as much a call as
        # any other: what the callee does, it does once for every element.
        self._what_it_changes(func, expr.span)
        if func.attrs.listable \
                and any(value.ty is not ty for value, ty in zip(args, wanted)):
            return self._walked(
                builder, func.ty,
                lambda given, span: builder.call(func, tuple(given),
                                                 func.ty.ret, span),
                args, expr, expected)
        answer = builder.call(func, args, func.ty.ret, expr.span)
        answer = self._as_long_as_given(builder, func, args, answer, expr.span)
        self._where_made(expr, func)
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

    def _given_arguments(self, builder: IRBuilder, expr: ast.Call,
                         func: Function) -> list[Value] | None:
        """Every parameter's value, in the order the function takes them.

        An argument may say which parameter it is for and a parameter may say
        what it is given where no argument does, so what a call writes and what
        a function takes are no longer the same list.  What is written is still
        lowered where it stands and in the order it was written -- a call that
        both faults and calls has to be readable -- and the values are put into
        the parameters' order afterwards, which costs nothing because by then
        every one of them has been worked out.

        Arguments written by place come first and ones written by name after,
        for the reason the places exist: once a name has been written the places
        no longer count from anywhere.
        """
        wanted = func.ty.params[:-1] if func.hands_out is not None \
            else func.ty.params
        placed: list[ast.Expr] = []
        named: list[ast.Named] = []
        failed = False
        for one in expr.args:
            if isinstance(one, ast.Named):
                named.append(one)
            elif named:
                self._diags.emit(D.LANG_CALL_POSITION_AFTER_NAME, one.span)
                failed = True
            else:
                placed.append(one)
        values = self._one_by_one(builder, placed, wanted, func.name)
        if values is None:
            return None
        if len(values) > len(wanted):
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=func.name, expected=len(wanted),
                             found=len(values) + len(named))
            return None
        if len(values) < len(wanted) and not named and not any(func.defaults):
            # Nothing in the call or the definition says anything but the
            # places, so what is wrong with it is its length and that is what
            # to say about it.
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=func.name, expected=len(wanted),
                             found=len(values))
            return None
        held: list[Value | None] = [*values]
        held.extend([None] * (len(wanted) - len(values)))
        for one in named:
            at = (func.param_names.index(one.name)
                  if one.name in func.param_names else None)
            if at is None or at >= len(wanted):
                self._diags.emit(D.LANG_CALL_UNKNOWN_PARAMETER, one.name_span,
                                 name=one.name, func=func.name)
                self._lower_expr(builder, one.value, None)
                failed = True
                continue
            if held[at] is not None:
                self._diags.emit(D.LANG_CALL_PARAMETER_TWICE, one.name_span,
                                 name=one.name)
                failed = True
            outer = self._handing_over
            self._handing_over = (func.name, at + 1)
            try:
                held[at] = self._lower_into(builder, one.value, wanted[at],
                                            one.value.span)
            finally:
                self._handing_over = outer
        for at, value in enumerate(held):
            if value is not None:
                continue
            given = func.defaults[at] if at < len(func.defaults) else None
            if given is None:
                self._diags.emit(D.LANG_CALL_PARAMETER_MISSING, expr.span,
                                 name=(func.param_names[at]
                                       if at < len(func.param_names)
                                       else str(at + 1)))
                failed = True
                continue
            held[at] = given
        if failed or any(value is None for value in held):
            return None
        given = [value for value in held if value is not None]
        if func.hands_out is not None:
            place = self._arena_for(expr, func.name)
            if place is None:
                return None
            given.append(place)
        return given

    def _lower_unit_call(self, builder: IRBuilder, expr: ast.Call,
                         expected: Type | None) -> Value:
        """Lower `\N{APL FUNCTIONAL SYMBOL QUAD}drop(x)` and `\N{APL FUNCTIONAL SYMBOL QUAD}unit(x, \N{TOP LEFT CORNER}UNIT\N{TOP RIGHT CORNER})`, which cross between units.

        Nothing crosses on its own, so these two are where a program says it
        meant to -- and they are a pair rather than one operation with a
        direction, because going from one unit to another is two steps and
        saying so is the point: a number in metres that is to become a number
        of seconds has its metres taken off and its seconds put on, and both
        are written.

        Neither is a conversion.  No bits change and no factor is applied: what
        changes is the type, which is the whole of what a unit is.
        """
        name = expr.callee.name if isinstance(expr.callee, ast.NameRef) else ""
        dropping = name == DROP_NAME
        if len(expr.args) != (1 if dropping else 2):
            self._diags.emit(D.LANG_UNIT_TAKES_TWO, expr.span, name=name,
                             wanted=1 if dropping else 2, found=len(expr.args))
            return UndefConst(ERROR)
        given = self._lower_expr(builder, expr.args[0], None)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, (IntType, FloatType)):
            self._diags.emit(D.LANG_UNIT_NOT_A_NUMBER, expr.args[0].span,
                             found=found.written())
            return UndefConst(ERROR)
        if dropping:
            if found.unit.is_none:
                self._diags.emit(D.LANG_UNIT_DROP_HAS_NONE, expr.span)
                return UndefConst(ERROR)
            return self._as_united(builder, given, found.bare, expected, expr.span)
        if not found.unit.is_none:
            self._diags.emit(D.LANG_UNIT_PUT_ON_A_UNIT, expr.args[1].span,
                             found=found.unit.written())
            return UndefConst(ERROR)
        written = expr.args[1]
        if not isinstance(written, ast.Lifted):
            self._diags.emit(D.LANG_UNIT_NOT_LIFTED, written.span)
            return UndefConst(ERROR)
        unit = self._unit_lifted(written)
        if unit is None:
            return UndefConst(ERROR)
        return self._as_united(builder, given, _carrying(found, unit), expected,
                               expr.span)

    def _as_united(self, builder: IRBuilder, given: Value, wanted: Type,
                   expected: Type | None, span: Span) -> Value:
        """*given*, read as *wanted*, which differs from its type only in a unit."""
        if not self._accepts(expected, wanted):
            self._report_mismatch(span, wanted, expected)
            return UndefConst(ERROR)
        if isinstance(given, IntConst):
            return builder.int_const(wanted, given.value)
        if isinstance(given, FloatConst):
            return builder.float_const(wanted, given.value)
        return builder.cast(CastKind.BITCAST, given, wanted, span)

    def _unit_lifted(self, written: ast.Lifted) -> Unit | None:
        """The unit lifted out of the program between `\N{TOP LEFT CORNER}` and `\N{TOP RIGHT CORNER}`.

        What is written there reads as an expression -- `meter\N{DIVISION SIGN}second\N{SUPERSCRIPT TWO}` is a
        division and a power as far as the parser is concerned -- and is read
        here as what it is: a product of powers of units.  The brackets are what
        say the names are the compiler's to look up and not the program's.
        """
        found = written.value if written.value is not None else None
        if found is None and isinstance(written.written, ast.TypeRef) \
                and written.written.module is None:
            return self._unit_named(written.written.name, written.span)
        if found is None:
            self._diags.emit(D.LANG_UNIT_NOT_LIFTED, written.span)
            return None
        return self._unit_expression(found, 1)

    def _unit_expression(self, expr: ast.Expr, sign: int) -> Unit | None:
        """One written unit, read out of what the parser made of it."""
        match expr:
            case ast.NameRef():
                found = self._unit_named(expr.name, expr.span)
                return None if found is None else \
                    (found if sign > 0 else NO_UNIT.over(found))
            case ast.StringLit():
                found = self._unit_named(expr.value, expr.span)
                return None if found is None else \
                    (found if sign > 0 else NO_UNIT.over(found))
            case ast.Raised():
                inner = self._unit_expression(expr.base, 1)
                if inner is None:
                    return None
                made = inner.raised(abs(expr.exponent))
                return made if sign > 0 and expr.exponent > 0 \
                    else NO_UNIT.over(made)
            case ast.Binary() if expr.op in (ast.BinaryOp.MULTIPLY,
                                             ast.BinaryOp.DIVIDE):
                left = self._unit_expression(expr.left, sign)
                right = self._unit_expression(
                    expr.right, sign if expr.op is ast.BinaryOp.MULTIPLY else -sign)
                if left is None or right is None:
                    return None
                return left.times(right)
            case _:
                self._diags.emit(D.LANG_SYNTAX_EXPECTED_UNIT, expr.span)
                return None

    def _syscall_number(self, ref: ast.NameRef) -> Value:
        """Lower `⎕sc@NAME`: the number that call has where this is built for.

        The compiler holds the table because the compiler is what knows which
        architecture it is, and a module has no other way to ask: `write` is 1
        on x86-64 and 64 on the other two, and a standard library written once
        has to be able to say `write` and mean the right one.
        """
        call = ref.name[len(SYSCALL_NUMBER_PREFIX):]
        architecture = architecture_of(self._module.triple)
        found = number_of(architecture, call)
        if found is not None:
            return self._module.int_const(U32, found)
        if call in SYSCALL_NAMES:
            self._diags.emit(D.LANG_SYSCALL_NOT_ON_THIS_ARCHITECTURE, ref.span,
                             name=call, architecture=architecture)
        else:
            self._diags.emit(D.LANG_SYSCALL_UNKNOWN_NAME, ref.span, name=call)
        return UndefConst(ERROR)

    def _lower_record(self, builder: IRBuilder, expr: ast.Call,
                      ty: Type, expected: Type | None) -> Value:
        """Lower `Point(.x ← 1f64, .y ← 2f64)`: a value of a record.

        Written the way a call names a parameter, which is the same idea asked
        of a field: the mark says the name is the thing's and not a variable's.
        Every field is given and each once -- there is no default to fall back
        on, and a field left out would be storage holding whatever was there,
        which is what this language does not have.
        """
        assert isinstance(ty, ProductType)
        given: dict[str, Value] = {}
        spoiled = False
        for one in expr.args:
            if not isinstance(one, ast.Named):
                self._diags.emit(D.LANG_PRODUCT_FIELD_NOT_NAMED, one.span,
                                 type=ty.written())
                spoiled = True
                continue
            at = _field_index(ty, one.name)
            if at is None:
                self._diags.emit(D.LANG_PRODUCT_NO_SUCH_FIELD, one.span,
                                 type=ty.written(), name=one.name)
                spoiled = True
                continue
            if one.name in given:
                self._diags.emit(D.LANG_PRODUCT_FIELD_TWICE, one.span,
                                 name=one.name)
                spoiled = True
                continue
            given[one.name] = self._lower_into(builder, one.value,
                                               ty.fields[at][1], one.span)
            self._owning_field(one.value, ty, one.name, ty.fields[at][1])
        for name, _ in ty.fields:
            if name not in given:
                self._diags.emit(D.LANG_PRODUCT_FIELD_MISSING, expr.span,
                                 type=ty.written(), name=name)
                spoiled = True
        if spoiled:
            return UndefConst(ERROR)
        leaves: list[Value] = []
        for name, held in ty.fields:
            leaves.extend(self._leaves_of(builder, given[name], held,
                                          expr.span))
        made = builder.make_tuple(tuple(leaves), ty, expr.span)
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        return made

    def _lower_sum(self, builder: IRBuilder, expr: ast.Call, ty: Type,
                   expected: Type | None) -> Value:
        """Lower `Number(.whole ← 5i64)`: a value of a sum.

        Written the way a record is and saying one part where a record says
        every one, which is the difference between the two types said in the
        one place a reader is looking: a sum holds exactly one of its parts.

        What it comes to is a *place*.  A sum is its largest part with a tag
        after it, which is bytes and not a value in a register -- so the value
        is room in the frame, the part written at its start and the tag written
        after it, and what travels is where that room is.
        """
        assert isinstance(ty, SumType)
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_SUM_ONE_PART, expr.span, type=ty.written(),
                             found="none" if not expr.args else str(len(expr.args)))
            return UndefConst(ERROR)
        written = expr.args[0]
        if not isinstance(written, ast.Named):
            self._diags.emit(D.LANG_PRODUCT_FIELD_NOT_NAMED, written.span,
                             type=ty.written())
            return UndefConst(ERROR)
        at = next((index for index, (name, _) in enumerate(ty.variants)
                   if name == written.name), None)
        if at is None:
            self._diags.emit(D.LANG_SUM_NO_SUCH_PART, written.span,
                             type=ty.written(), name=written.name)
            return UndefConst(ERROR)
        held = ty.variants[at][1]
        if not self._accepts(expected, ty):
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        place = builder.frame(ty, expr.span)
        if held is not VOID:
            value = self._lower_into(builder, written.value, held, written.span)
            if self._value_type_of(value) is ERROR:
                return UndefConst(ERROR)
            self._payload_into(builder, place, value, held, expr.span)
        elif not isinstance(written.value, ast.BoolLit) \
                or written.value.value is not True:
            # A part carrying nothing is written `.name ← true`: something has
            # to stand there, and what it says is that this is the part.
            self._diags.emit(D.LANG_SUM_PART_CARRIES_NOTHING, written.span,
                             type=ty.written(), name=written.name)
            return UndefConst(ERROR)
        builder.store(self._tag_place(builder, place, ty, expr.span),
                      builder.int_const(U8, at), expr.span)
        return builder.cast(CastKind.BITCAST, place, ty, expr.span)

    def _tag_place(self, builder: IRBuilder, place: Value, ty: SumType,
                   span: Span) -> Value:
        """Where the tag of a sum is: after its largest part.

        After rather than before, which is what the layout says and why: a tag
        ahead of a part wanting eight bytes is seven bytes of padding, and
        behind it is often none.
        """
        return builder.cast(
            CastKind.BITCAST,
            builder.binary(BinOp.ADD, place,
                           builder.int_const(U64, tag_offset_of(ty, _LAYOUT)),
                           span),
            self._module.types.ptr_type(U8, mutable=True), span)

    def _payload_into(self, builder: IRBuilder, place: Value, value: Value,
                      held: Type, span: Span) -> None:
        """Write what a part carries at the start of the place holding a sum."""
        where = builder.cast(CastKind.BITCAST, place,
                             self._module.types.ptr_type(held, mutable=True),
                             span)
        if isinstance(held, ProductType):
            self._record_into(builder, where, value, held, span)
            return
        if _held_in_memory(held):
            # A part that is itself bytes in a place -- a sum -- is copied into
            # this one's room: what the value in hand says is where those bytes
            # are, and where they are is the room the part was made in.
            self._copy_bytes(builder, where, value, size_of(held, _LAYOUT), span)
            return
        builder.store(where, value, span)

    def _copy_bytes(self, builder: IRBuilder, into: Value, from_: Value,
                    size: int, span: Span) -> None:
        """Copy *size* bytes, in the largest pieces that fit.

        Written out rather than looped over: the size is known while compiling
        and is a handful of words, so a loop would be blocks and a counter for
        what is four instructions.
        """
        at = 0
        while at < size:
            part, width = next(one for one in _CHUNKS if one[1] <= size - at)
            builder.store(self._byte_place(builder, into, part, at, span),
                          builder.load(
                              self._byte_place(builder, from_, part, at, span),
                              span), span)
            at += width

    def _byte_place(self, builder: IRBuilder, place: Value, part: Type,
                    offset: int, span: Span) -> Value:
        """One piece of a copy, as a pointer to a value of *part*."""
        where = builder.cast(CastKind.BITCAST, place,
                             self._module.types.ptr_type(part, mutable=True),
                             span)
        if offset == 0:
            return where
        return builder.binary(BinOp.ADD, where,
                              builder.int_const(U64, offset), span)

    def _payload_from(self, builder: IRBuilder, place: Value, held: Type,
                      span: Span) -> Value:
        """Read what a part carries out of the place holding a sum.

        A part that is itself a place -- a record, another sum -- is read as
        where it is and not copied out: that is what a value of such a type is,
        and the arm that took it apart is what decides whether it keeps one.
        """
        where = builder.cast(CastKind.BITCAST, place,
                             self._module.types.ptr_type(held, mutable=True),
                             span)
        if isinstance(held, ProductType):
            return self._record_from(builder, where, held, span)
        if _held_in_memory(held):
            return builder.cast(CastKind.BITCAST, place, held, span)
        return builder.load(where, span)

    def _lower_syscall(self, builder: IRBuilder, expr: ast.Call,
                       expected: Type | None) -> Value:
        """Lower `⎕syscall(NUMBER, ARG...)`: what the kernel answered.

        The number and the arguments are the kernel's and not the language's:
        nothing here knows what call one is, and a program asking for it is
        asking the system.  What comes back is what the kernel put in the
        register it answers in -- an `i64`, negative where it refused, which is
        the kernel's own convention and not one invented here.

        It changes what outlives the call by definition, so a function making
        one says `impure`, which is the rule every other such thing follows.
        """
        if not expr.args:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=SYSCALL_NAME, expected=1, found=0)
            return UndefConst(ERROR)
        if len(expr.args) - 1 > SYSCALL_ARGUMENTS:
            self._diags.emit(D.LANG_SYSCALL_TOO_MANY, expr.span,
                             expected=SYSCALL_ARGUMENTS,
                             found=len(expr.args) - 1)
            return UndefConst(ERROR)
        self._an_effect(D.LANG_PURE_CALLS_IMPURE, expr.span, name=SYSCALL_NAME)
        given: list[Value] = []
        for at, one in enumerate(expr.args):
            value = self._lower_expr(builder, one, None)
            ty = self._value_type_of(value)
            if ty is ERROR:
                return UndefConst(ERROR)
            if not isinstance(ty, (IntType, PtrType)):
                self._diags.emit(D.LANG_SYSCALL_BAD_ARGUMENT, one.span,
                                 position=at + 1, found=ty.written())
                return UndefConst(ERROR)
            given.append(self._as_a_word(builder, value, ty, expr.span))
        answer = builder.syscall(given[0], given[1:], I64, expr.span)
        if not self._accepts(expected, answer.ty):
            self._report_mismatch(expr.span, answer.ty, expected)
            return UndefConst(ERROR)
        return answer

    def _lower_place_at(self, builder: IRBuilder, expr: ast.Call,
                        expected: Type | None) -> Value:
        """Lower `⎕at(ADDRESS, ⌜TYPE⌝)` and `⎕span(ADDRESS, COUNT, ⌜TYPE⌝)`.

        A place at an address the program worked out, and a run of places from
        one.  Nothing in the language makes a place out of a number, so these
        are the compiler's names: what is there is what the program says is
        there, and there is nothing to check it against.  They are how a program
        reaches memory something else gave it -- what `mmap` answered, what a
        device said -- and they are the only way, which is what keeps that door
        in one place rather than in every type that wanted one.

        The lifted type is *what the answer is* and not what is pointed at, so
        the words on the definition and the words in the brackets are the same
        words: `let head: &mut u32 = ⎕at(a, ⌜&mut u32⌝)`.
        """
        assert isinstance(expr.callee, ast.NameRef)
        name = expr.callee.name
        several = name == SPAN_NAME
        wanted = 3 if several else 2
        if len(expr.args) != wanted:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=name, expected=wanted, found=len(expr.args))
            return UndefConst(ERROR)
        written = expr.args[-1]
        if not isinstance(written, ast.Lifted):
            self._diags.emit(D.LANG_ADDRESS_NOT_A_TYPE, written.span, name=name)
            return UndefConst(ERROR)
        answer = self._lifted_type(written)
        if answer is None:
            self._diags.emit(D.LANG_ADDRESS_NOT_A_TYPE, written.span, name=name)
            return UndefConst(ERROR)
        if several:
            if not isinstance(answer, ArrayType) or answer.fixed \
                    or answer.rank != 1:
                self._diags.emit(D.LANG_SPAN_NOT_AN_ARRAY, written.span,
                                 found=answer.written())
                return UndefConst(ERROR)
            place_ty = self._module.types.ptr_type(answer.element, mutable=True)
        else:
            if not isinstance(answer, PtrType):
                self._diags.emit(D.LANG_AT_NOT_A_REFERENCE, written.span,
                                 found=answer.written())
                return UndefConst(ERROR)
            place_ty = answer
        address = self._as_an_address(builder, expr.args[0], name)
        if address is None:
            return UndefConst(ERROR)
        place = builder.cast(CastKind.BITCAST, address, place_ty, expr.span)
        if not several:
            if not self._accepts(expected, answer):
                self._report_mismatch(expr.span, answer, expected)
                return UndefConst(ERROR)
            return place
        count = self._lower_expr(builder, expr.args[1], None)
        counting = self._value_type_of(count)
        if counting is ERROR:
            return UndefConst(ERROR)
        if not isinstance(counting, IntType):
            self._diags.emit(D.LANG_SPAN_NOT_A_COUNT, expr.args[1].span,
                             found=counting.written())
            return UndefConst(ERROR)
        made = builder.make_tuple(
            (place, self._as_count(builder, count, counting, expr.span)),
            answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _lower_bytes(self, builder: IRBuilder, expr: ast.Call,
                     expected: Type | None) -> Value:
        """Lower `⎕bytes(TEXT)`: what a string is made of.

        The compiler's name because nothing in the language reaches inside a
        string: there is no index, `#` answers characters, and what a value of
        one *is* -- where the bytes are and how many there are -- is the
        representation's business.

        **It costs nothing.**  A string and an array of bytes whose length is
        not in its type are the same two words, so this says which of the two is
        meant and emits no instruction of its own.  Saying it is the point: a
        language where one became the other on its own would be a language where
        a walk over characters and a walk over bytes read alike.
        """
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=BYTES_NAME, expected=1, found=len(expr.args))
            return UndefConst(ERROR)
        given = self._lower_expr(builder, expr.args[0], STR)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if found is not STR:
            self._diags.emit(D.LANG_BYTES_NOT_TEXT, expr.args[0].span,
                             found=found.written())
            return UndefConst(ERROR)
        answer = self._module.types.array_type(U8, (None,))
        # Where the bytes are and how many: the allocator is the string's, and an
        # array of bytes is a view of them that gives nothing back.
        parts = parts_of(STR)[:2]
        made = builder.make_tuple(
            tuple(builder.extract(given, at, one, expr.span)
                  for at, one in enumerate(parts)), answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _lower_typename(self, builder: IRBuilder, expr: ast.Call,
                        expected: Type | None) -> Value:
        """Lower `⎕typename(⌜T⌝)`: the type written out, as text.

        A literal in the image and nothing else: what a type is called is settled
        while compiling, so this is the text the compiler would have printed in a
        message about that type.  Defined for every type and not only a record's,
        `⌜u8⌝` being as much a type as `⌜Point⌝` is -- and what wants it is a
        value's text, which puts the name in front of the fields.
        """
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=TYPENAME_NAME, expected=1,
                             found=len(expr.args))
            return UndefConst(ERROR)
        found = self._type_stood_for(expr.args[0])
        if found is None:
            self._diags.emit(D.LANG_TYPENAME_TAKES_A_LIFT, expr.args[0].span)
            return UndefConst(ERROR)
        if not self._accepts(expected, STR):
            self._report_mismatch(expr.span, STR, expected)
            return UndefConst(ERROR)
        return self._written_text(builder, found.written(), expr.span)

    def _lower_fields(self, builder: IRBuilder, expr: ast.Call,
                      expected: Type | None) -> Value:
        """Lower `⎕fields(v)`: a record's fields as a tuple of name and value.

        **In the order the type declares them**, which is not the order they lie
        in: the compiler is allowed to reorder a product's fields, and what a
        reader of the answer wants is what the program wrote.

        A tuple of pairs and not two tuples, because `comptime foreach` walks one
        tuple and two walks cannot be taken together -- so the name and the value
        travel as one turn.  It costs nothing beyond the names: a record already
        travels as its fields, so each pair is the field read where it lies.
        """
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=FIELDS_NAME, expected=1, found=len(expr.args))
            return UndefConst(ERROR)
        given = self._lower_expr(builder, expr.args[0], None)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, ProductType):
            self._diags.emit(D.LANG_FIELDS_NOT_A_RECORD, expr.args[0].span,
                             found=found.written())
            return UndefConst(ERROR)
        # The leaves of the whole answer, in order.  A tuple is built from one
        # value per leaf and not one per member: a `str` is two of them and so is
        # a field that is itself several values, which is why the pairs are not
        # assembled one at a time and then put together.
        inside = parts_within(found)
        held = parts_of(found)
        text = parts_of(STR)
        leaves: list[Value] = []
        kinds: list[Type] = []
        for at, (name, kind) in enumerate(found.fields):
            written = self._written_text(builder, name, expr.span)
            leaves.extend(builder.extract(written, which, text[which], expr.span)
                          for which in range(len(text)))
            first, count = inside[at]
            leaves.extend(builder.extract(given, first + which,
                                          held[first + which], expr.span)
                          for which in range(count))
            kinds.append(self._module.types.tuple_type((STR, kind)))
        answer = self._module.types.tuple_type(tuple(kinds))
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return builder.make_tuple(tuple(leaves), answer, expr.span)

    def _lower_str_of(self, builder: IRBuilder, expr: ast.Call,
                      expected: Type | None) -> Value:
        """Lower `⎕str(CHAR)`: a string of one character.

        The third way a `str` is made, after a literal and a join, and the
        compiler's name for the reason those two are not a program's to write
        either: a string's bytes being well-formed UTF-8 is an invariant, and
        encoding one code point is what keeps it one.  There is deliberately no way
        in from bytes, which would have to be checked rather than constructed.

        It allocates, since how many bytes a code point takes is not known until it
        is looked at, so a function using it says `impure` -- as one joining two
        strings does, and for the same reason.
        """
        if not 1 <= len(expr.args) <= 2:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=STR_OF_NAME, expected=1, found=len(expr.args))
            return UndefConst(ERROR)
        # Where the bytes go, where a second argument said: a character takes room
        # like everything else that becomes a string, and a function that was told
        # which arena is one whose caller accounted for it.
        where = None
        if len(expr.args) == 2:
            where = self._arena_place(builder, expr.args[1])
            if where is None:
                return UndefConst(ERROR)
        given = self._lower_expr(builder, expr.args[0], CHAR)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if found is not CHAR:
            self._diags.emit(D.LANG_STR_OF_NOT_A_CHARACTER, expr.args[0].span,
                             found=found.written())
            return UndefConst(ERROR)
        if not self._accepts(expected, STR):
            self._report_mismatch(expr.span, STR, expected)
            return UndefConst(ERROR)
        if where is None:
            where = self._out_of(builder, expr.span)
        # The character itself goes over, not its number: the two helpers read it as
        # a number inside themselves, where what they are reading is a parameter.
        bytes_ = builder.call(strings.char_function(self._module),
                              (where, given),
                              self._module.types.ptr_type(U8, mutable=True),
                              expr.span)
        count = builder.call(strings.length_in_bytes(self._module), (given,),
                             U64, expr.span)
        return builder.make_tuple((bytes_, count, where), STR, expr.span)

    def _lower_empty(self, builder: IRBuilder, expr: ast.Call,
                     expected: Type | None) -> Value:
        """Lower `⎕empty(ARENA)`: everything it holds, given back at once.

        The one granularity the allocator has.  Nothing is given back on its own, so
        this is what makes an arena a *pool*: room is taken from it for as long as it
        is wanted and the whole of it goes in one call.  What is left is an arena with
        nothing in it, so taking room from it again asks the system for a first chunk.

        It takes the arena named rather than computed, an arena being a place -- and a
        reference to one, that being how an arena is handed to a function.
        """
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=EMPTY_NAME, expected=1, found=len(expr.args))
            return UndefConst(ERROR)
        written = expr.args[0]
        owner = self._arena_owned(written)
        if owner is None:
            return UndefConst(ERROR)
        place = self._arena_place(builder, written)
        if place is None:
            return UndefConst(ERROR)
        if expected is not None:
            self._report_mismatch(expr.span, VOID, expected)
            return UndefConst(ERROR)
        self._given_back(owner, expr.span)
        builder.call(tables.release_function(self._module), (place,), VOID,
                     expr.span)
        return UndefConst(VOID)

    def _arena_owned(self, written: ast.Expr) -> _Local | None:
        """The arena an `⎕empty` names, where it is this body's to give back.

        Only what made an arena gives it back (4616).  One handed over by reference
        is its maker's, who may be holding things made in it; `⎕heap` and one at the
        top level last as long as the program, and anything may be holding things
        made in those.
        """
        if isinstance(written, ast.Deref) \
                and isinstance(written.operand, ast.NameRef):
            # The arena a reference names, which is whoever's the reference is.
            written = written.operand
        name = written.name if isinstance(written, ast.NameRef) else \
            _about_the_shape(written)
        if isinstance(written, ast.NameRef):
            local = self._find_local(written.name)
            if local is not None and local.handed_out:
                # It leaves with the answer, which the caller gives back.
                self._diags.emit(D.LANG_HANDED_OUT_GIVEN_BACK, written.span,
                                 name=written.name)
                return None
            if local is not None and self._is_arena(local):
                if self._held_by(local) is ARENA:
                    return local
                self._diags.emit(D.LANG_ARENA_NOT_YOURS_TO_EMPTY, written.span,
                                 arena=name, whose="one this was handed")
                return None
            if local is None and isinstance(self._provided(written.name),
                                            GlobalVar):
                self._diags.emit(D.LANG_ARENA_NOT_YOURS_TO_EMPTY, written.span,
                                 arena=name,
                                 whose="there for as long as the program is")
                return None
        if isinstance(written, ast.Member):
            self._diags.emit(D.LANG_ARENA_NOT_YOURS_TO_EMPTY, written.span,
                             arena=name, whose="there for as long as the program is")
            return None
        self._diags.emit(D.LANG_EMPTY_NOT_AN_ARENA, written.span, found=name)
        return None

    def _arena_place(self, builder: IRBuilder, written: ast.Expr) -> Value | None:
        """Where the arena an expression names keeps its state, or nothing."""
        if isinstance(written, ast.NameRef):
            found = self._arena_named(builder, written)
            if found is not None:
                return found
            return None
        value = self._lower_expr(builder, written, None)
        held = self._value_type_of(value)
        if isinstance(held, PtrType) and held.pointee is ARENA:
            return value
        if held is not ERROR:
            self._diags.emit(D.LANG_EMPTY_NOT_AN_ARENA, written.span,
                             found=held.written())
        return None

    def _operator_takes_an_arena(self, expr: ast.Expr) -> bool:
        """Whether a program defined this operator with an arena as its last operand.

        What `in` means then is that operand, which is how a definition asks to be
        told where to put what it builds -- `⍕v in scratch` being the case it was
        wanted for.
        """
        found = self._operator_written(_glyph_of(expr),
                                      len(_operands_of(expr)) + 1)
        node = found.node if isinstance(found, _Generic) else None
        if node is not None:
            written = node.params[-1].type if node.params else None
            return isinstance(written, ast.RefTypeRef) \
                and isinstance(written.pointee, ast.TypeRef) \
                and written.pointee.name == ARENA_NAME
        if not isinstance(found, Function) or not found.ty.params:
            return False
        last = found.ty.params[-1]
        return isinstance(last, PtrType) and last.pointee is ARENA

    def _out_of(self, builder: IRBuilder, span: Span) -> Value:
        """Where what is being lowered takes its room from.

        The arena `in` named, where one was written around this expression, and the
        one the compiler provides otherwise.  It is a lexical thing and not an
        argument: `a ⧺ b in scratch` says where the join's room comes from, and what
        reads it is the lowering of the join.
        """
        if self._out_of_arena is not None:
            return self._out_of_arena
        heap = self._provided(HEAP_NAME)
        assert isinstance(heap, GlobalVar)
        return builder.address(heap, span)

    def _owning_field(self, written: ast.Expr, record: Type, name: str,
                      held: Type) -> None:
        """Check an array of text put in a field of a record.

        A field says nothing of where its elements are kept, so they are kept in
        `⎕heap` (4625); and the array is its elements, so only one nobody else names
        may go in, or each element would have two owners (4623).
        """
        if not _is_owning(held):
            return
        if not _is_temporary(written):
            self._diags.emit(D.LANG_CONTAINER_CANNOT_COPY, written.span,
                             type=held.written())
            return
        holds = self._holder_of(written)
        if holds != _HEAP:
            self._diags.emit(
                D.LANG_CONTAINER_ALLOCATOR_MISMATCH, written.span, param=name,
                func=record.written(), wanted=HEAP_NAME,
                found=self._owner_written(holds) if holds is not None
                else "an allocator nothing says")

    def _held_by_reference(self, written: ast.Expr) -> bool:
        """Whether a name stands for a reference to an arena rather than for one."""
        if not isinstance(written, ast.NameRef):
            return False
        local = self._find_local(written.name)
        if local is None:
            return False
        held = self._held_by(local)
        return isinstance(held, PtrType) and held.pointee is ARENA

    def _lower_allocated(self, builder: IRBuilder, expr: ast.Allocated,
                         expected: Type | None) -> Value:
        """Lower `EXPR in NAME`: which arena the expression takes its room from.

        What may follow it is something that takes room: a join, a collection written
        out, or an operator whose definition asks for an arena -- which is what lets a
        program's own `⍕` be told where the text it builds should go.  Everything else
        is worked out in registers and has nowhere to come from (4568).
        """
        if _in_the_image(expr):
            return self._lower_static(builder, expr, expr.value, expected)
        place = self._arena_named(builder, expr.arena)
        if place is None:
            return UndefConst(ERROR)
        inside = expr.value
        if isinstance(inside, (ast.Unary, ast.Binary, ast.Fresh)) \
                and self._operators and self._operator_takes_an_arena(inside):
            # A program's own operator, handed the arena as its last operand: the
            # definition asked for one, and `in` is how a caller writes it.
            # The place and not the name: the definition asked for a reference to
            # an arena, which is how an arena is handed over at all.
            # An arena already held by reference -- a parameter `a: &mut arena`
            # -- is handed on as the reference it is.
            handed: ast.Expr = expr.arena
            if not self._held_by_reference(expr.arena):
                handed = ast.AddressOf(span=expr.arena.span, operand=expr.arena,
                                       mutable=True)
            self._last_made_in = None
            found = self._operator_call(
                builder, _glyph_of(inside), (*_operands_of(inside), handed),
                expr.span, expected)
            if self._last_made_in is not None:
                # The operator says its answer is made in the arena it is handed,
                # which is the one `in` names and nothing its operands were made in.
                self._made_from[id(expr)] = (expr, self._last_made_in)
            return found
        handing = self._handing_callee(inside)
        if handing is not None:
            self._diags.emit(D.LANG_HANDED_OUT_IN_AFTER_CALL, expr.span,
                             name=handing)
            return UndefConst(ERROR)
        if not _takes_room(inside):
            self._diags.emit(D.LANG_ALLOCATED_NOT_A_PLACE, expr.span,
                             found=_about_the_shape(inside))
            return UndefConst(ERROR)
        outer = (self._out_of_arena, self._out_of_arenas)
        self._out_of_arena = place
        self._out_of_arenas = self._arenas_in(expr.arena)
        try:
            found = self._lower_expr(builder, inside, expected)
        finally:
            self._out_of_arena, self._out_of_arenas = outer
        held = self._value_type_of(found)
        if isinstance(inside, ast.ArrayLit) and isinstance(held, ArrayType) \
                and not owned.points(held.element):
            # An array is room in the frame, so `in` says which allocator what
            # its elements point into comes from -- and these point nowhere.
            self._diags.emit(D.LANG_ALLOCATED_NOT_A_PLACE, expr.span,
                             found="an array of values that point nowhere")
            return UndefConst(ERROR)
        return found

    def _lower_static(self, builder: IRBuilder, whole: ast.Expr,
                      written: ast.Expr, expected: Type | None) -> Value:
        """Lower `… in ⎕static`: something made while compiling, in the image.

        Every part of it has to be known while compiling (4636), and it is laid
        out in a read-only part of the image as a variable at the top level is --
        a list's run, a table's entries, every string's bytes -- so that nothing
        is made while the program runs, and nothing is ever given back.  What the
        expression comes to is where that is.
        """
        ty = expected
        if isinstance(ty, ArrayType) and not ty.fixed:
            # What is wanted does not say the length, which what is written
            # does: the array is the length written, and is let go of after.
            shape = _shape_written(written) \
                if isinstance(written, ast.ArrayLit) else None
            ty = (self._module.types.array_type(ty.element, shape)
                  if shape is not None and len(shape) == len(ty.shape) else None)
        elif ty is None:
            # Nothing around says the type: what is written says it.
            derived = self._static_type(written)
            ty = derived if isinstance(derived, Type) else None
        if ty is None or ty is ERROR:
            if ty is None:
                self._diags.emit(
                    D.IMPL_UNIMPLEMENTED_FEATURE, whole.span,
                    feature="a value in the image whose type nothing around it says")
            return UndefConst(ERROR)
        if isinstance(ty, (SetType, DictType)) and ty.mutable:
            self._diags.emit(D.LANG_STATIC_CHANGES, whole.span,
                             name=ty.written())
            return UndefConst(ERROR)
        outer, self._for_the_image = self._for_the_image, True
        try:
            found = self._constant_value(
                ast.VarDef(span=written.span, name=STATIC_NAME,
                           name_span=written.span, type=None, value=written), ty)
        finally:
            self._for_the_image = outer
        if not isinstance(found, Const):
            return UndefConst(ERROR)
        self._made_from[id(whole)] = (whole, frozenset())
        span = whole.span
        match found:
            case ArrayConst() | RecordConst():
                # A place, as an array or a record always is: the variable is it.
                at = builder.address(self._image_variable(ty, found), span)
                if isinstance(ty, ProductType):
                    return self._record_from(builder, at, ty, span)
                return builder.cast(CastKind.BITCAST, at, ty, span)
            case PartsConst():
                return builder.make_tuple(
                    [self._from_the_image(builder, one, piece, span)
                     for one, piece in zip(found.parts, parts_of(ty))], ty, span)
            case AddressConst():
                return self._from_the_image(builder, found, ty, span)
        return found

    def _from_the_image(self, builder: IRBuilder, part: Const, ty: Type,
                        span: Span) -> Value:
        """One word of something in the image, as the value it is."""
        if isinstance(part, AddressConst):
            if part.target is None:
                return builder.cast(CastKind.BITCAST, builder.int_const(U64, 0),
                                    ty, span)
            assert isinstance(part.target, GlobalVar)
            return builder.cast(CastKind.BITCAST,
                                builder.address(part.target, span), ty, span)
        return part

    def _lower_address_of(self, builder: IRBuilder, expr: ast.Call,
                          expected: Type | None) -> Value:
        """Lower `⎕address(REF)`: a place as the number it is.

        The way back from `⎕at`, and the compiler's name for the same reason:
        what a program does with a number that was a place is nothing the
        compiler can check.  It is what a program has for handing an address to
        something outside it -- a ring's submission entry holds one in a field,
        and the kernel reads it.

        It makes no function impure.  Asking where something is changes nothing
        and reads nothing; what is done through the address says so itself.
        """
        if len(expr.args) != 1:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=ADDRESS_NAME, expected=1,
                             found=len(expr.args))
            return UndefConst(ERROR)
        place = self._lower_expr(builder, expr.args[0], None)
        ty = self._value_type_of(place)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, PtrType):
            self._diags.emit(D.LANG_ADDRESS_OF_NOT_A_REFERENCE,
                             expr.args[0].span, found=ty.written())
            return UndefConst(ERROR)
        found = builder.cast(CastKind.BITCAST, place, U64, expr.span)
        if not self._accepts(expected, U64):
            self._report_mismatch(expr.span, U64, expected)
            return UndefConst(ERROR)
        return found

    def _as_an_address(self, builder: IRBuilder, written: ast.Expr,
                       name: str) -> Value | None:
        """What was written as the address, read as one.

        A machine word, which is what a request to the kernel answers with, or
        a reference, which is already a place and is here being read as a place
        of another type.  A narrower number is not an address: half of one is
        not a place, and widening it would be the compiler deciding which half.
        """
        value = self._lower_expr(builder, written, None)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if isinstance(ty, PtrType):
            return value
        if isinstance(ty, IntType) and ty.bits == 64:
            # Signed or unsigned: what the kernel answers with is an `i64`, and
            # the bits of an address do not change with how a program reads
            # them.  The word is read as the unsigned one because that is what
            # an address is held as, which costs nothing at this width.
            return self._as_count(builder, value, ty, written.span)
        self._diags.emit(D.LANG_ADDRESS_NOT_AN_ADDRESS, written.span,
                         name=name, found=ty.written())
        return None

    def _lower_ordered(self, builder: IRBuilder, expr: ast.Call,
                       expected: Type | None) -> Value:
        """Lower `⎕acquire(REF)` and `⎕release(REF, VALUE)`.

        What each says is about the machine and not about the value: an
        acquiring read is one nothing written after it may be seen by another
        observer to have happened before, and a releasing write is one nothing
        written before it may be seen to have happened after.  Nothing a program
        could write for itself says that, which is why they are the compiler's
        names.

        Said at the access and not by the type of the place, so the same place
        read the ordinary way elsewhere is an ordinary read -- which is what
        driving a ring wants: one index is published with a release and read
        back plainly by the same program a moment later.

        Both change what outlives them, or notice that something else did, so a
        function using one says `impure`.
        """
        assert isinstance(expr.callee, ast.NameRef)
        name = expr.callee.name
        writing = name == RELEASE_NAME
        wanted = 2 if writing else 1
        if len(expr.args) != wanted:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=name, expected=wanted, found=len(expr.args))
            return UndefConst(ERROR)
        self._an_effect(D.LANG_PURE_CALLS_IMPURE, expr.span, name=name)
        place = self._lower_expr(builder, expr.args[0], None)
        ty = self._value_type_of(place)
        if ty is ERROR:
            return UndefConst(ERROR)
        if not isinstance(ty, PtrType):
            self._diags.emit(D.LANG_ORDERED_NOT_A_REFERENCE, expr.args[0].span,
                             name=name, found=ty.written())
            return UndefConst(ERROR)
        if made_of_parts(ty.pointee):
            # One access and not several, which is what the back ends say too:
            # which of the parts the ordering belonged to would have no answer.
            self._diags.emit(D.LANG_ORDERED_SEVERAL_VALUES, expr.args[0].span,
                             name=name, found=ty.pointee.written())
            return UndefConst(ERROR)
        if not writing:
            found = builder.load(place, expr.span, ordering=Ordering.ACQUIRE)
            if not self._accepts(expected, found.ty):
                self._report_mismatch(expr.span, found.ty, expected)
                return UndefConst(ERROR)
            return found
        if not ty.mutable:
            self._diags.emit(D.LANG_ORDERED_NOT_MUTABLE, expr.args[0].span,
                             name=name)
            return UndefConst(ERROR)
        value = self._lower_into(builder, expr.args[1], ty.pointee,
                                 expr.args[1].span)
        if self._value_type_of(value) is ERROR:
            return UndefConst(ERROR)
        builder.store(place, value, expr.span, ordering=Ordering.RELEASE)
        if expected is not None:
            # It writes and answers nothing, so somewhere wanting a value from
            # it is somewhere wanting what there is none of.
            self._diags.emit(D.LANG_CALL_HAS_NO_VALUE, expr.span, name=name)
            return UndefConst(ERROR)
        return UndefConst(VOID)

    def _as_a_word(self, builder: IRBuilder, value: Value, ty: Type,
                   span: Span) -> Value:
        """*value* as the machine word the kernel reads out of a register.

        Widened where it is narrower, by its own signedness, and read as a whole
        number where it is an address.  Doing it here rather than in three back
        ends is what keeps them each to one shape: by the time one sees a
        request to the kernel, everything in it is the width of a register.
        """
        if isinstance(ty, PtrType):
            return builder.cast(CastKind.BITCAST, value, U64, span)
        assert isinstance(ty, IntType)
        if ty.bits == 64:
            return value
        return builder.cast(CastKind.SEXT if ty.signed else CastKind.ZEXT,
                            value, I64 if ty.signed else U64, span)

    def _lower_narrow(self, builder: IRBuilder, expr: ast.Call,
                      expected: Type | None) -> Value:
        """Lower `\N{APL FUNCTIONAL SYMBOL QUAD}narrow(EXPR, \N{TOP LEFT CORNER}TYPE\N{TOP RIGHT CORNER})`: a value of a narrower type, or why not.

        Nothing in this language widens or narrows on its own, so a value that
        is to become one of another type is written as becoming one -- and the
        question that makes narrowing different from widening is that it can
        fail.  So what it answers with is a result: the value where it fits, and
        which way it did not where it does not.

        **What went wrong is worth more than the fact that something did.**  The
        error carries a value saying whether the number was above the top of the
        type, below the bottom of it, or negative where the type has no negative
        values at all -- which is a case of being below the bottom that the
        language can say more about, a reader told "sign" knowing which mistake
        was made.

        The unit stays.  Narrowing a length gives a length: what changes is how
        much room the number has, and a unit says nothing about that.
        """
        if len(expr.args) != 2:
            self._diags.emit(D.LANG_UNIT_TAKES_TWO, expr.span, name=NARROW_NAME,
                             wanted=2, found=len(expr.args))
            return UndefConst(ERROR)
        written = expr.args[1]
        if not isinstance(written, ast.Lifted):
            self._diags.emit(D.LANG_NARROW_NOT_A_TYPE, written.span)
            return UndefConst(ERROR)
        wanted = self._lifted_type(written)
        if wanted is None:
            self._diags.emit(D.LANG_NARROW_NOT_A_TYPE, written.span)
            return UndefConst(ERROR)
        if isinstance(wanted, EnumType):
            return self._narrowed_to_enum(builder, expr, wanted, expected)
        if not isinstance(wanted, IntType):
            self._diags.emit(D.LANG_NARROW_NOT_AN_INTEGER, written.span,
                             found=wanted.written())
            return UndefConst(ERROR)
        given = self._lower_expr(builder, expr.args[0], None)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, IntType):
            self._diags.emit(D.LANG_NARROW_NOT_AN_INTEGER, expr.args[0].span,
                             found=found.written())
            return UndefConst(ERROR)
        # What it counts is its own and travels with it; what the type says is
        # how much room the number has.
        into = _carrying(wanted, found.unit)
        answer = self._module.types.result_type(into, NARROWING)
        value, failed, why = self._fitted(builder, given, found, into, expr.span)
        made = builder.wrap(value, failed, answer, expr.span, why)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _lower_widen(self, builder: IRBuilder, expr: ast.Call,
                     expected: Type | None) -> Value:
        """Lower `⎕widen(EXPR, ⌜TYPE⌝)`: the value, as a type with room for it.

        It answers the value itself and not a result, which it may do only
        because it cannot fail: every value of the type written has to be a
        value of the type wanted.  That is the rule and not "is it wider" -- an
        unsigned type goes into a signed one with room to spare, and a signed
        one goes into no unsigned type at all, a negative number being
        somewhere the unsigned type does not reach.

        What it counts is its own and travels with it, as a narrowing's does:
        the type says how much room the number has and nothing about what the
        number is of.
        """
        if len(expr.args) != 2:
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=WIDEN_NAME, expected=2, found=len(expr.args))
            return UndefConst(ERROR)
        written = expr.args[1]
        if not isinstance(written, ast.Lifted):
            self._diags.emit(D.LANG_WIDEN_NOT_A_TYPE, written.span)
            return UndefConst(ERROR)
        wanted = self._lifted_type(written)
        if wanted is None:
            self._diags.emit(D.LANG_WIDEN_NOT_A_TYPE, written.span)
            return UndefConst(ERROR)
        if not isinstance(wanted, IntType):
            self._diags.emit(D.LANG_WIDEN_NOT_AN_INTEGER, written.span,
                             found=wanted.written())
            return UndefConst(ERROR)
        given = self._lower_expr(builder, expr.args[0], None)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, IntType):
            self._diags.emit(D.LANG_WIDEN_NOT_AN_INTEGER, expr.args[0].span,
                             found=found.written())
            return UndefConst(ERROR)
        into = _carrying(wanted, found.unit)
        if not _every_value_fits(found, into):
            self._diags.emit(D.LANG_WIDEN_DOES_NOT_FIT, expr.span,
                             found=found.written(), wanted=into.written())
            return UndefConst(ERROR)
        made = (given if found.bits == into.bits
                else builder.cast(CastKind.SEXT if found.signed else CastKind.ZEXT,
                                  given, into, expr.span))
        if not self._accepts(expected, into):
            self._report_mismatch(expr.span, into, expected)
            return UndefConst(ERROR)
        return made

    def _narrowed_to_enum(self, builder: IRBuilder, expr: ast.Call,
                          wanted: EnumType, expected: Type | None) -> Value:
        """Lower `⎕narrow(EXPR, ⌜ENUM⌝)`: a number read as one of its values.

        An enumeration is a narrower type than the one it is held as, so making
        one out of a number is a narrowing like any other and can fail the same
        way -- what differs is that its values are not a range, so the number
        has to *be* one of them and `absent` is what says it was not.

        **No branch.**  An enumeration is held as its number, so where the
        number is one of them the answer is the same bits; what has to be worked
        out is only whether it is one, which is one comparison per value folded
        together.  A chain of arms would be the same comparisons with a jump
        between each, and every program that read a number off a device would
        have had to write it out.
        """
        given = self._lower_expr(builder, expr.args[0], None)
        found = self._value_type_of(given)
        if found is ERROR:
            return UndefConst(ERROR)
        if not isinstance(found, IntType):
            self._diags.emit(D.LANG_NARROW_NOT_AN_INTEGER, expr.args[0].span,
                             found=found.written())
            return UndefConst(ERROR)
        span = expr.span
        answer = self._module.types.result_type(wanted, NARROWING)
        holds: Value | None = None
        for number in wanted.values:
            if not found.holds(number):
                # A value the number's own type cannot reach: the comparison
                # would be false however it were written, so it is not written.
                continue
            same = builder.compare(CmpPred.EQ, given,
                                   builder.int_const(found, number), span)
            holds = same if holds is None else builder.binary(BinOp.OR, holds,
                                                              same, span)
        if holds is None:
            holds = builder.bool_const(False)
        value = self._as_enum(builder, given, found, wanted, span)
        made = builder.wrap(value, self._negate(builder, holds, span), answer,
                            span, self._module.enum_const(NARROWING, 3))
        if not self._accepts(expected, answer):
            self._report_mismatch(span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _as_enum(self, builder: IRBuilder, given: Value, found: IntType,
                 wanted: EnumType, span: Span) -> Value:
        """The number as a value of the enumeration, cut to its width.

        Only read where the number turned out to be one of the values, so
        nothing is lost by cutting it down: a number that did not fit the width
        the enumeration is held as was none of them.  A value of an enumeration
        is a number in a register whatever the language says it means, which is
        what lets one be made the way a narrower number is.
        """
        if found.bits >= wanted.holder.bits:
            return builder.cast(CastKind.TRUNC, given, wanted, span)
        return builder.cast(CastKind.SEXT if found.signed else CastKind.ZEXT,
                            given, wanted, span)

    def _fitted(self, builder: IRBuilder, given: Value, found: IntType,
                into: IntType, span: Span) -> tuple[Value, Value, Value]:
        """The value as the narrower type, whether it fits, and why it does not.

        Each of the three conditions is asked only where it *can* hold: a type
        whose every value the other one has cannot overflow into it, and a
        source with no negative values cannot be negative.  So the comparisons
        that are settled by the two types are settled here and none of them
        reaches the program -- which is what makes narrowing to a wider type
        cost nothing at all.

        The conditions are exclusive by construction, so which one it was is
        their numbers added up rather than a choice between them: `sign` is
        asked only where the type has no negative values and `underflow` only
        where it has, and `overflow` is nought, which is what makes it the one a
        failure reports where neither of the others holds.
        """
        signed = found.signed
        above = CmpPred.SGT if signed else CmpPred.UGT
        below = CmpPred.SLT if signed else CmpPred.ULT
        no = builder.bool_const(False)

        def past(pred: CmpPred, bound: int, possible: bool) -> Value:
            """Whether the value is past *bound*, where it can be."""
            return builder.compare(pred, given, builder.int_const(found, bound),
                                   span) if possible else no

        over = past(above, into.high, found.high > into.high)
        # A negative number put where there are no negative values is the case
        # of being below the bottom that has a name of its own; where the type
        # does have negative values, below the bottom is all it is.
        wrong_sign = past(below, 0, signed and not into.signed)
        under = past(below, into.low, into.signed and found.low < into.low)
        failed = self._either(builder, self._either(builder, over, under, span),
                              wrong_sign, span)
        why = self._reason(builder, under, wrong_sign, span)
        return (self._as_wide(builder, given, found, into, span), failed, why)

    def _either(self, builder: IRBuilder, one: Value, other: Value,
                span: Span) -> Value:
        """Whether either holds, with a condition that cannot hold dropped."""
        if isinstance(one, BoolConst) and not one.value:
            return other
        if isinstance(other, BoolConst) and not other.value:
            return one
        return builder.binary(BinOp.OR, one, other, span)

    def _reason(self, builder: IRBuilder, under: Value, wrong_sign: Value,
                span: Span) -> Value:
        """Which condition it was, as a value of the enumeration.

        Overflow is nought, so it needs no term: where neither of the other two
        holds the sum is nought and that is what it was.  Each of the others is
        its own number times whether it holds, and at most one of them can.
        """
        made: Value | None = None
        for at, held in ((1, under), (2, wrong_sign)):
            if isinstance(held, BoolConst) and not held.value:
                continue
            one = builder.cast(CastKind.ZEXT, held, NARROWING.holder, span)
            if at != 1:
                one = builder.binary(BinOp.WRAP_MUL, one,
                                     builder.int_const(NARROWING.holder, at), span)
            made = one if made is None else builder.binary(BinOp.WRAP_ADD, made,
                                                           one, span)
        if made is None:
            return self._module.enum_const(NARROWING, 0)
        return builder.cast(CastKind.BITCAST, made, NARROWING, span)

    def _as_wide(self, builder: IRBuilder, given: Value, found: IntType,
                 into: IntType, span: Span) -> Value:
        """*given* read as *into*, the value having been checked to fit it.

        What decides the instruction is how much room each is held in and not
        how many bits the type says: a `u5` and a `u8` are one byte apiece, so
        there is nothing to cut off between them -- and the value is in range by
        the time this is reached, so the bits above its own width are already
        what they should be.
        """
        if into.held == found.held:
            return builder.cast(CastKind.TRUNC, given, into, span)
        if into.held < found.held:
            return builder.cast(CastKind.TRUNC, given, into, span)
        return builder.cast(CastKind.SEXT if found.signed else CastKind.ZEXT,
                            given, into, span)

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
                                     found=ty.written(), wanted=wanted[at].written())
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

    def _walked(self, builder: IRBuilder, ty: FuncType,
                make: Callable[[Sequence[Value], Span], Value],
                args: list[Value], expr: ast.Call,
                expected: Type | None) -> Value:
        """Call *make* once for each element of what was handed it as an array.

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
        shape = self._walking_shape(ty, args, expr)
        if shape is None:
            return UndefConst(ERROR)
        answer = self._module.types.array_type(ty.ret, shape)
        place = builder.frame(answer, expr.span)
        self._each_of(builder, ty, make, args, shape, place, 0, expr.span)
        made = builder.cast(CastKind.BITCAST, place, answer, expr.span)
        if not self._accepts(expected, answer):
            self._report_mismatch(expr.span, answer, expected)
            return UndefConst(ERROR)
        return made

    def _walking_shape(self, ty: FuncType, args: Sequence[Value],
                       expr: ast.Call) -> tuple[int, ...] | None:
        """How many along each dimension the walk goes, outermost first.

        Worked out before anything is lowered for it, because the room the
        answer needs is the whole of that shape and is taken once.
        """
        found: list[int] = []
        seen = [value.ty for value in args]
        while True:
            walked = [at for at, (one, wanted) in enumerate(zip(seen, ty.params))
                      if one is not wanted]
            if not walked:
                return tuple(found)
            along: int | None = None
            for at in walked:
                one = seen[at]
                if not (isinstance(one, ArrayType) and one.fixed):
                    self._diags.emit(D.LANG_LISTABLE_CANNOT_WALK,
                                     expr.args[at].span if at < len(expr.args)
                                     else expr.span,
                                     found=one.written(),
                                     wanted=ty.params[at].written())
                    return None
                if along is None:
                    along = one.shape[0]
                elif along != one.shape[0]:
                    self._diags.emit(D.LANG_LISTABLE_SHAPES_DIFFER,
                                     expr.args[at].span if at < len(expr.args)
                                     else expr.span,
                                     found=one.shape[0], wanted=along)
                    return None
                seen[at] = self._one_less(one)
            assert along is not None
            found.append(along)

    def _one_less(self, ty: ArrayType) -> Type:
        """What is left of an array once its outermost dimension comes off."""
        if ty.rank == 1:
            return ty.element
        return self._module.types.array_type(ty.element, ty.shape[1:])

    def _each_of(self, builder: IRBuilder, ty: FuncType,
                 make: Callable[[Sequence[Value], Span], Value],
                 args: Sequence[Value], shape: tuple[int, ...],
                 place: Value, at: int, span: Span) -> None:
        """Make the calls one dimension at a time, writing the answers in order.

        *at* is how many answers are already behind this one, which row-major
        makes the place this one goes: the same arithmetic an array written down
        uses, and for the same reason.
        """
        if not shape:
            answer = make(tuple(args), span)
            builder.store(
                self._element_place(builder, place, ty.ret,
                                    builder.int_const(U64, at), span),
                answer, span)
            return
        step = 1
        for along in shape[1:]:
            step *= along
        for index in range(shape[0]):
            self._each_of(builder, ty, make,
                          [self._one_of(builder, value, wanted, index, span)
                           for value, wanted in zip(args, ty.params)],
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
                             found=ty.written())
            return None
        if not ty.fixed:
            # The length is beside the elements rather than in the type, so it
            # is a thing the program works out and this is a thing the compiler
            # writes down.
            self._diags.emit(D.LANG_SPREAD_NOT_FIXED, spread.operand.span,
                             found=ty.written())
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

    def _only_at_build(self, func: Function, span: Span) -> None:
        """Refuse a call to a compiler-provided function from code that runs.

        Such a function has no body and no symbol: what it does is change a
        description the compiler is holding while it works out a build.  Inside
        the build function that is the point of it; anywhere else it is a call
        to something that is not there.
        """
        if func.attrs.builtin and self._in_build_function is None:
            self._diags.emit(D.LANG_BUILTIN_AT_RUNTIME, span, name=func.name)

    def _callee(self, expr: ast.Expr) -> Function | None:
        """The function a call names, or nothing where it does not name one."""
        match expr:
            case ast.NameRef():
                found = self._top.get(expr.name)
                if isinstance(found, Function):
                    if self._notes is not None:
                        self._note(expr.span, FUNCTION, expr.name,
                                   found.ty.written(), found.name_span,
                                   found.doc or None)
                    self._only_at_build(found, expr.span)
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

    def _type_exported(self, expr: ast.Member) -> Type | None:
        """The type another module exports under this name, asked quietly.

        Nothing is reported for a name that is not one: this is asked of every
        call written `a.b(...)` to tell a record from a call, and what a name
        that is neither turns out to be is the ordinary path's to say.
        """
        if not isinstance(expr.base, ast.NameRef):
            return None
        held = self._top.get(expr.base.name)
        if not isinstance(held, LoadedModule):
            return None
        found = held.exports.get(expr.name)
        if not isinstance(found, _NamedType):
            return None
        return found.ty

    def _generic_named(self, callee: ast.Expr) -> "_Generic | None":
        """The generic definition a callee names, by a bare name or through a module.

        Asked quietly: a callee that names something else is not this, and what it
        is, is decided by whatever is asked next.
        """
        # The bare name first, which is what almost every callee is: a name inside a
        # type is reached through the type, so it is a path, and asking about a path
        # before asking about a name would put two dictionary lookups on every call in
        # every program for the sake of the few that write one.
        match callee:
            case ast.NameRef():
                found = self._top.get(callee.name)
            case ast.Member() if isinstance(callee.base, ast.NameRef):
                held = self._top.get(callee.base.name)
                if not isinstance(held, LoadedModule):
                    return None
                found = held.exports.get(callee.name)
            case _:
                return None
        if isinstance(found, _Generic):
            return found
        inside = self._member_named(callee)
        return inside if isinstance(inside, _Generic) else None

    def _member_named(self, callee: ast.Expr) -> object | None:  # noqa: D401
        """What a path naming something inside a type stands for, or nothing.

        Two paths reach one: `T.name`, where `T` is a type this file defines, and
        `m.T.name`, where `m` is a module and `T` a type it exports.  Asked quietly,
        a callee that names something else being somebody else's to report.
        """
        if not self._members and not isinstance(callee, ast.Member):
            return None
        if not isinstance(callee, ast.Member):
            return None
        match callee.base:
            case ast.NameRef() as base if self._members and isinstance(
                    self._top.get(base.name), _NamedType):
                return self._members.get((base.name, callee.name))
            case ast.Member() as through if isinstance(through.base, ast.NameRef):
                held = self._top.get(through.base.name)
                if not isinstance(held, LoadedModule):
                    return None
                return held.members.get((through.name, callee.name))
        return None

    def _a_member_path(self, callee: ast.Expr) -> tuple[str, str] | None:
        """The type and the name a path names, where it is one of those two shapes.

        What this is for is the message: a path that *looks* like a member and finds
        nothing should be told that the type has no such name, rather than that a
        type is not a module.
        """
        if not isinstance(callee, ast.Member):
            return None
        match callee.base:
            case ast.NameRef() as base if isinstance(self._top.get(base.name),
                                                     _NamedType):
                return (base.name, callee.name)
            case ast.Member() as through if isinstance(through.base, ast.NameRef) \
                    and isinstance(self._top.get(through.base.name), LoadedModule):
                if self._type_exported(through) is None:
                    return None
                return (through.name, callee.name)
        return None

    def _callee_of_module(self, expr: ast.Member) -> Function | None:
        """The function another module exports under this name."""
        found = self._member_named(expr)
        if isinstance(found, Function):
            self._only_at_build(found, expr.span)
            return found
        named = self._a_member_path(expr)
        if named is not None:
            self._diags.emit(D.LANG_MEMBER_UNKNOWN, expr.name_span,
                             name=named[1], held=named[0])
            return None
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
        if self._notes is not None:
            # The module the name is reached through, and then the function
            # itself, which is defined in that module's file: two notes over two
            # spans, and the narrower is the one the cursor is on.
            where = self._defined.get(base.name)
            self._note(base.span, MODULE, base.name, held.path.as_posix(),
                       where[0] if where is not None else INVALID_SPAN)
            self._note(expr.name_span, FUNCTION, expr.name,
                       found.ty.written(), found.name_span, found.doc or None)
        self._only_at_build(found, expr.span)
        return found

    def _would_copy(self, local: _Local, ref: ast.NameRef) -> bool:
        """Whether reading this name is copying a value that is never copied.

        **Reading a name as a value is what a copy is**, and this is the one place a
        name becomes one -- so it is the one place this has to be asked.  A field of
        such a value is read from where the value is and a reference to it is taken of
        that place, and neither comes through here: both are things that may be done,
        and only having the whole of it somewhere else is not.

        What it is for is a value that stands for something outside the program.  Two
        of those would be two names for one thing, which is what the rule about a
        second `&mut` already refuses for a reference and what nothing refused for the
        value itself.
        """
        held = self._held_by(local)
        if not isinstance(held, ProductType) or not held.unique:
            return False
        self._diags.emit(D.LANG_UNIQUE_COPIED, ref.span, type=held.written(),
                         what="".join(("reading '", ref.name,
                                       "' here would be a copy of it")))
        return True

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
        if ref.name.startswith(SYSCALL_NUMBER_PREFIX):
            return self._syscall_number(ref)
        if ref.name == EMPTY_ARENA_NAME:
            # An arena that has asked the system for nothing yet, which is three
            # words of nought.  There is exactly one thing to write where an arena
            # is wanted, and this is it: a name inside a function is then an
            # ordinary record local, given storage of its own and emptied with
            # `⎕empty` -- which is what makes a pool of one's own writable.
            return builder.make_tuple(
                tuple(builder.int_const(U64, 0) for _ in ARENA.fields),
                ARENA, ref.span)
        local = self._find_local(ref.name)
        if local is None and isinstance(self._top.get(ref.name), _Generic):
            # Written once and compiled once per set of types, and which sets
            # those are is what the calls ask for.  There is no call here to
            # ask, so there is no one function for the name to stand for.
            self._diags.emit(D.LANG_GENERIC_IS_NOT_A_VALUE, ref.span,
                             name=ref.name)
            return UndefConst(ERROR)
        if local is None and isinstance(self._provided(ref.name), Function):
            # A function named where a value is wanted, which a call is not: a
            # call names its callee and resolves it before ever coming here.
            return self._function_as_a_value(
                builder, self._provided(ref.name), ref, expected)
        if local is not None and self._would_copy(local, ref):
            return UndefConst(ERROR)
        if local is not None and local.gone_at is not None:
            # Read all the same, so that what is said about it is the one thing.
            local.read = True
            self._told_gone(local, ref.name, ref.span)
            return UndefConst(ERROR)
        if local is not None and local.placed:
            # The name stands for storage of its own, because somewhere in this
            # body a reference to it is taken.  Reading it is therefore a load,
            # exactly as reading a variable at the top level is.
            if ref.name != self._taking_a_reference \
                    and self._lent_out(ref.name, ref.span, False):
                return UndefConst(ERROR)
            local.read = True
            held = local.held
            found = (self._record_from(builder, local.value, held, ref.span)
                     if isinstance(held, ProductType)
                     else builder.load(local.value, ref.span))
            if not self._accepts(expected, found.ty):
                self._report_mismatch(ref.span, found.ty, expected)
            return found
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
            return self._shorter_life(builder,
                                      self._lower_expr(builder, expr, expected),
                                      expected, span)
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
            # An answer, so what the error would have carried is not there and
            # nothing may read it.
            return builder.wrap(
                value, builder.bool_const(False), expected, span,
                None if expected.err is None else UndefConst(expected.err))
        self._report_mismatch(span, found, expected)
        return UndefConst(ERROR)

    def _shorter_life(self, builder: IRBuilder, value: Value, expected: Type,
                      span: Span) -> Value:
        """A value promising more than is wanted, read as what is wanted.

        A place that is there as long as the program is there for as long as
        any one call, and a place that may be written may certainly be read.
        So such a reference stands where the weaker one is wanted -- and what
        stands there has that type, the same bits read as the smaller promise.
        """
        found = self._value_type_of(value)
        if not _promises_as_much(expected, found):
            return value
        if isinstance(expected, FuncType):
            # A function is three words and not one, so there is nothing for
            # one instruction to read differently: the three are taken out and
            # put back under the type that promises less, which is the same
            # registers and no work at all.
            return builder.make_tuple(
                [builder.extract(value, at, part, span)
                 for at, part in enumerate(parts_of(expected))],
                expected, span)
        return builder.cast(CastKind.BITCAST, value, expected, span)

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
                                 or _in_the_image(expr) else None)
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
        if isinstance(expected, (IntType, FloatType)) \
                and isinstance(found, (IntType, FloatType)) \
                and found.bare is expected.bare \
                and self._stands_for(found.unit, expected.unit):
            # The same number in a unit the program said may stand here.  The
            # bits are the same bits, so nothing is emitted for it.
            return True
        if _promises_as_much(expected, found):
            # A reference that promises at least what is wanted.  What lives as
            # long as the program lives long enough for anything, and what may
            # be written may be read; both go one way only, a call's storage
            # being no use where the program's is wanted and a place nothing
            # may write being no use where writing is.
            return True
        if self._deriving and isinstance(expected, (IntType, FloatType)) \
                and isinstance(found, (IntType, FloatType)) \
                and found.bare is expected.bare:
            # An operand of a product or a quotient: whatever unit it carries is
            # one the operator can work with, and what comes out says which.
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

    def _what_it_changes(self, func: Function, span: Span) -> None:
        """Report a call whose callee changes more than this function may.

        **Two permissions and not one.**  A callee that reads or writes a *device*
        asks for the permission a device travels with, which a function holding one
        has and which `@[io]` says outright.  A callee that changes anything else asks
        for `@[impure]`, which is what that attribute is left meaning: something
        global, rather than the device somebody handed over.
        """
        if not func.attrs.impure:
            return
        if func.attrs.io:
            if not self._may_do_io:
                self._an_effect(D.LANG_IO_WITHOUT_A_DEVICE, span, name=func.name)
            # It did it, so this function does it: what a caller may not drop or
            # repeat is a write, and whether this one writes is what its body just
            # said rather than what its attributes did.  A function that holds a
            # device and never touches it stays a function a caller may drop.
            self._did_io = True
            return
        self._an_effect(D.LANG_PURE_CALLS_IMPURE, span, name=func.name)

    def _an_effect(self, which: int, span: Span, **args: object) -> None:
        """Report a change that outlives the call, where the function is pure.

        Everything that makes such a change comes through here, so that what
        "pure" means is one list rather than a rule each place remembers: a
        variable at the top level written, memory the function did not make
        written, and a function that may do either called.

        **Which function is pure is said, and pointed at.**  It is not always the one
        the message is about or even in the same file: a call a macro wrote is
        reported where the macro wrote it, which is wherever that macro lives -- and
        what has to change is one attribute on the *caller's* line.
        """
        if self._impure:
            return
        held = self._demanding[0] if self._demanding is not None else None
        name = getattr(held, "name", "")
        told = self._diags.emit(which, span, caller=name, **args) \
            if name and which in (D.LANG_PURE_CALLS_IMPURE,
                                  D.LANG_IO_WITHOUT_A_DEVICE) \
            else self._diags.emit(which, span, **args)
        if held is not None and name:
            # Which attribute would answer it, which is not the same question: a
            # device asks for `@[io]` or for a parameter carrying one, and saying
            # `@[impure]` there would be the wrong advice.
            told.note(D.LANG_IO_FUNCTION_HERE
                      if which == D.LANG_IO_WITHOUT_A_DEVICE
                      else D.LANG_PURE_FUNCTION_HERE,
                      getattr(held, "name_span", span), name=name)

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
                             found=found.written(), expected=expected.written())
            return
        if self._operand_of is not None:
            # Three different mistakes: an operand where a truth value is what
            # the operator takes, one of a kind the operator has no meaning for,
            # and two operands of one kind whose widths differ.
            if expected is BOOL:
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_BOOLEAN, span,
                                 operator=self._operand_of, found=found.written())
            elif not isinstance(found, IntType):
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, span,
                                 operator=self._operand_of, found=found.written())
            else:
                self._diags.emit(D.LANG_TYPE_OPERAND_MISMATCH, span,
                                 operator=self._operand_of,
                                 expected=expected.written(), found=found.written())
            return
        if self._handing_over is not None:
            name, position = self._handing_over
            self._diags.emit(D.LANG_CALL_ARGUMENT_MISMATCH, span, position=position,
                             name=name, expected=expected.written(),
                             found=found.written())
            return
        if self._assigning is not None:
            self._diags.emit(D.LANG_TYPE_ASSIGNMENT_MISMATCH, span,
                             name=self._assigning, expected=expected.written(),
                             found=found.written())
            return
        if self._initializing is not None:
            self._diags.emit(D.LANG_TYPE_INITIALIZER_MISMATCH, span,
                             name=self._initializing, expected=expected.written(),
                             found=found.written())
            return
        self._diags.emit(D.LANG_TYPE_RETURN_MISMATCH, span, found=found.written(),
                         expected=expected.written())

    # -- program level ---------------------------------------------------------

    def _check_program(self) -> None:
        """Check the properties the whole program must have."""
        if self._module.startup is None and self._module.build is None:
            # A file with a build function in it describes a build rather than
            # being a program, so nothing of it is ever started.
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



def check_macros(units: Sequence[ast.SourceUnit], diags: DiagEngine,
                 sources: SourceManager | None = None,
                 registry: ModuleRegistry | None = None,
                 path: Path | None = None) -> object:
    """Check and lower the macros, answering the module and the trees they quoted.

    A run of its own over a unit holding the macros, the `comptime` functions and the
    definitions their signatures name -- and not the program's functions, which a
    macro runs before.  So a macro calling one is told the name is not defined, which
    is the truth of it: at expansion there is no such function yet.
    """
    from ..front.expand import _Checked

    module = Module(name="macros", triple="")
    found = Checker(module, diags,
                    registry if registry is not None else ModuleRegistry(),
                    path, "", sources, for_macros=True)
    found.run(units, whole_program=False)
    return _Checked(module, found.quoted)


def check(module: Module, units: Sequence[ast.SourceUnit], diags: DiagEngine,
          registry: ModuleRegistry | None = None,
          sources: SourceManager | None = None,
          notes: Notes | None = None,
          conditions: Conditions = Conditions.CHECK) -> Module:
    """Check *units* and lower them into *module*.

    The units are the files named on the command line, which share one
    namespace and are the program's own module; anything they import is read
    from here, and every module's name is settled once all of them are read.
    """
    found = registry if registry is not None else ModuleRegistry()
    path = Path(units[0].path) if units else None
    result = Checker(module, diags, found, path, "", sources,
                     notes=notes, conditions=conditions).run(units)
    found.settle_names()
    return result
