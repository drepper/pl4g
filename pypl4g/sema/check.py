"""Semantic analysis and lowering to the IR.

Definitions need not be processed in order: every top-level definition is
collected first and only then is any body checked, which is what lets the whole
compilation be parallelized and what makes a forward reference legal.
"""

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Final, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine, Expectation
from ..front import ast
from ..front.token import (BOTTOM_GLYPH, BUILTIN_GLYPH, EMPTY_ARENA_NAME,
                          HEAP_NAME, TOLERANCE_DEFAULT,
                          TOLERANCE_NAME, WILDCARD_NAME)
from ..ir.builder import IRBuilder
from ..ir.layout import DataLayout, stride_of
from ..ir.inst import BinOp, CastKind, CmpPred, Instruction, UnOp
from . import tables
from ..ir.function import (DEFAULT_CCONV, SYSTEM_CCONV, BasicBlock, FuncAttrs,
                           Function,
                           InlineHint,
                           Linkage, SpecialKind)
from ..ir.module import GlobalVar, Module
from ..ir.types import (ARENA, ArrayType, BOOL, BUILTIN_TYPES, DictType,
                        ERROR, EnumType,
                        U64,
                        F64,
                        FloatType, IntType, MEM, ProductType, ResultType,
                        SetType, SumType, TupleType, Type, VOID)
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
    block: "BasicBlock | None" = None
    binds: "tuple[str, Span, Span, Value, Type] | None" = None


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
    node: "ast.TypeDef | ast.EnumDef"
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


def _assigned_in(block: "ast.Block") -> list[str]:
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


def _collect_assigned(block: "ast.Block", into: list[str]) -> None:
    """Add to *into* every name *block* assigns, looking through nested bodies."""
    for stmt in block.stmts:
        match stmt:
            case ast.AssignStmt():
                into.append(stmt.name)
                into.extend(name for name, _ in stmt.more)
            case ast.While() | ast.ForEach():
                _collect_assigned(stmt.body, into)
            case ast.ExprStmt(value=ast.If() as asked):
                for arm in asked.arms:
                    _collect_assigned(arm.body, into)
            case ast.ExprStmt(value=ast.Match() as matched):
                for arm in matched.arms:
                    _collect_assigned(arm.body, into)
            case _:
                pass


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
    more: "Callable[[IRBuilder, tuple[Value, ...]], Value]"
    take: "Callable[[IRBuilder, tuple[Value, ...]], Value]"
    step: "Callable[[IRBuilder, tuple[Value, ...]], tuple[Value, ...]]"


class Checker:
    """Checks one program and lowers it into a module."""

    def __init__(self, module: Module, diags: DiagEngine,
                 registry: "ModuleRegistry | None" = None,
                 path: "Path | None" = None, prefix: str = "",
                 sources: "SourceManager | None" = None,
                 top_level: "list[_Global] | None" = None) -> None:
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

    def _read_module(self, path: Path, node: ast.ModuleImport) -> "LoadedModule | None":
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

    def _read_unit(self, path: Path, node: ast.ModuleImport) -> "ast.SourceUnit | None":
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
                           shape: "tuple[int | None, ...]"
                           ) -> "list[Const] | None":
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

    def _enum_value_of(self, expr: ast.Member) -> "EnumConst | None":
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

    def _begin_expecting(self, pairs: Sequence["_Expected"]) -> Expectation | None:
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
                          pairs: Sequence["_Expected"]) -> bool:
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
        """Resolve a name: the innermost binding first, then the top level."""
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
                            span=node.span, source_path=path)
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
                         extra=extra), linkage

    # -- types -----------------------------------------------------------------

    # -- types the program defines ---------------------------------------------

    def _collect_type(self, node: "ast.TypeDef | ast.EnumDef", path: str) -> None:
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

    def _holder_for(self, node: ast.EnumDef, numbers: Sequence[int]) -> "IntType | None":
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

    def _resolve_type(self, ref: "ast.TypeExpr") -> Type:
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
        if any(a is None for a in shape) and any(a is not None for a in shape):
            # An array either carries its shape in the type or carries the whole
            # of it beside the elements.  Half of each would mean a value whose
            # parts depend on which half, which is a second kind of array for a
            # case nothing has asked for.
            self._diags.emit(D.LANG_ARRAY_SHAPE_IS_HALF_TOLD, ref.span)
            return ERROR
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
        self._push_scope()
        for index, param in enumerate(node.params):
            value = block.add_param(func.ty.params[index], param.name)
            self._bind_local(param.name, value, node.params[index].span,
                             is_parameter=True)
        assert node.body is not None
        self._lower_block(builder, node.body, func)
        self._pop_scope()
        self._answering = outer_answer
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
                self._diags.emit(D.LANG_FUNCDEF_RETURN_UNREACHABLE, stmt.span)
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
            case ast.While():
                self._lower_while(builder, stmt, func)
            case ast.ForEach():
                self._lower_foreach(builder, stmt, func)
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
        """Lower `〈a, b〉`: several values made into one."""
        wanted = expected.members if isinstance(expected, TupleType) \
            and len(expected.members) == len(expr.members) else None
        values = [self._lower_into(builder, written,
                                   wanted[index] if wanted is not None else ERROR,
                                   written.span)
                  if wanted is not None
                  else self._lower_expr(builder, written, None)
                  for index, written in enumerate(expr.members)]
        types = [self._value_type_of(value) for value in values]
        if any(ty is ERROR for ty in types):
            return UndefConst(ERROR)
        ty = self._module.types.tuple_type(types)
        if expected is not None and expected is not ty:
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        return builder.make_tuple(values, ty, expr.span)

    def _taken_apart(self, value: Value, names: "Sequence[tuple[str, Span]]",
                     span: Span) -> "list[Type] | None":
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
        ty = self._array_written(expr, expected)
        if ty is None:
            return UndefConst(ERROR)
        place = builder.frame(ty, expr.span)
        if not self._fill(builder, expr, ty, place, 0, ty.shape):
            return UndefConst(ERROR)
        return builder.cast(CastKind.BITCAST, place, ty, expr.span)

    def _array_written(self, expr: ast.ArrayLit,
                       expected: Type | None) -> "ArrayType | None":
        """What type an array written down has.

        The shape comes from what it is wanted as where that says, and from how
        deep and how wide the writing is where it does not.  A shape written on
        both sides has to agree: an array of a different length is an array of a
        different type, and nothing is padded out or dropped.
        """
        wanted = expected if isinstance(expected, ArrayType) else None
        if wanted is not None and wanted.fixed:
            if len(expr.elements) != wanted.shape[0]:
                self._diags.emit(D.LANG_ARRAY_WRONG_LENGTH, expr.span,
                                 given=len(expr.elements), wanted=wanted.shape[0])
                return None
            return wanted
        shape, inner = self._shape_written(expr)
        element = self._one_type(
            None, inner, wanted.element if wanted is not None else None)
        if element is ERROR:
            return None
        found = self._module.types.array_type(element, shape)
        if wanted is not None and wanted is not found:
            self._report_mismatch(expr.span, found, wanted)
            return None
        return found

    def _shape_written(self, expr: ast.ArrayLit
                       ) -> "tuple[tuple[int | None, ...], tuple[ast.Expr, ...]]":
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
              place: Value, at: int, shape: "tuple[int | None, ...]") -> bool:
        """Write what was written down into the run of elements, in order.

        *at* is how many elements are already behind it, so that a dimension
        deeper simply carries on where the one above it left off -- which is
        what row-major order is.
        """
        if len(expr.elements) != shape[0]:
            self._diags.emit(D.LANG_ARRAY_WRONG_LENGTH, expr.span,
                             given=len(expr.elements), wanted=shape[0])
            return False
        if len(shape) == 1:
            for index, written in enumerate(expr.elements):
                value = self._lower_into(builder, written, ty.element,
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
                              shape[1:]):
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
                  span: Span) -> "tuple[Value, list[Value]]":
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
                       along: "int | None", length: Value,
                       span: Span) -> "Value | None":
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

    def _offset_of(self, builder: IRBuilder, written: "Sequence[ast.Expr]",
                   ty: ArrayType, lengths: "Sequence[Value]",
                   span: Span) -> "Value | None":
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
                left: "Sequence[Value]", span: Span) -> Value:
        """A count of rows turned into a count of elements."""
        for along in left:
            offset = builder.binary(BinOp.WRAP_MUL, offset, along, span)
        return offset

    def _row_type(self, ty: ArrayType, taken: int) -> ArrayType:
        """The type of what is left of an array once *taken* indices are given."""
        return self._module.types.array_type(ty.element, ty.shape[taken:])

    def _row_at(self, builder: IRBuilder, start: Value, ty: ArrayType,
                lengths: "Sequence[Value]", offset: Value, taken: int,
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
        start, lengths = self._shape_of(builder, base, ty, expr.span)
        offset = self._offset_of(builder, expr.indices, ty, lengths, expr.span)
        if offset is None:
            return UndefConst(ERROR)
        if len(expr.indices) < ty.rank:
            # Fewer indices than dimensions names a row rather than an element.
            row = self._row_at(builder, start, ty, lengths, offset,
                               len(expr.indices), expr.span)
            found_ty = self._value_type_of(row)
            if expected is not None and expected is not found_ty:
                self._report_mismatch(expr.span, found_ty, expected)
            return row
        value = builder.load(
            self._element_place(builder, start, ty.element, offset, expr.span),
            expr.span)
        if expected is not None and expected is not ty.element:
            self._report_mismatch(expr.span, ty.element, expected)
        return value

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
        if expected is not None and expected is not member:
            self._report_mismatch(expr.span, member, expected)
        return builder.extract(base, at, member, expr.span)

    def _constant_number(self, expr: ast.Expr) -> "int | None":
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
        if expected is not None and expected is not answer:
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
        if len(stmt.indices) != ty.rank:
            # Every index, because what is assigned is one element.  Assigning a
            # whole row would be copying one array into another, which nothing
            # in the language does yet and which is not what `←` means anywhere
            # else: it binds a name or writes one place.
            self._diags.emit(D.LANG_ARRAY_WRONG_RANK, stmt.span,
                             given=len(stmt.indices), wanted=ty.rank)
            return
        start, lengths = self._shape_of(builder, base, ty, stmt.span)
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
                          expr: "ast.SetLit | ast.DictLit",
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
        if empty:
            if not isinstance(expected, (SetType, DictType)):
                self._diags.emit(D.LANG_COLLECTION_EMPTY_UNKNOWN, expr.span)
                return UndefConst(ERROR)
            ty: Type = expected
        elif isinstance(expr, ast.SetLit):
            wanted = expected.element if isinstance(expected, SetType) else None
            element = self._one_type(builder, expr.elements, wanted)
            if element is ERROR:
                return UndefConst(ERROR)
            if not _can_be_a_key(element):
                self._diags.emit(D.LANG_COLLECTION_KEY_NOT_HASHABLE,
                                 expr.elements[0].span, found=element.render())
                return UndefConst(ERROR)
            ty = self._module.types.set_type(element)
        else:
            wanted_key = expected.key if isinstance(expected, DictType) else None
            wanted_value = expected.value if isinstance(expected, DictType) else None
            key = self._one_type(builder, tuple(k for k, _ in expr.entries),
                                 wanted_key)
            value = self._one_type(builder, tuple(v for _, v in expr.entries),
                                   wanted_value)
            if key is ERROR or value is ERROR:
                return UndefConst(ERROR)
            if not _can_be_a_key(key):
                self._diags.emit(D.LANG_COLLECTION_KEY_NOT_HASHABLE,
                                 expr.entries[0][0].span, found=key.render())
                return UndefConst(ERROR)
            ty = self._module.types.dict_type(key, value)
        if expected is not None and expected is not ty:
            self._report_mismatch(expr.span, ty, expected)
            return UndefConst(ERROR)
        if isinstance(ty, DictType) and not _can_be_a_key(ty.value):
            # The same restriction the key has, and for a duller reason: an
            # entry is words, and what goes in one has to fit in one.  The
            # to-do list says what a value of any type would need.
            self._diags.emit(D.LANG_COLLECTION_VALUE_TOO_LARGE, expr.span,
                             found=ty.value.render())
            return UndefConst(ERROR)
        return self._build_collection(builder, expr, ty,
                                      self._arena_named(expr.arena))

    def _arena_named(self, written: "ast.NameRef | None") -> "GlobalVar | None":
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
                          expr: "ast.SetLit | ast.DictLit",
                          ty: "SetType | DictType",
                          arena: "GlobalVar | None" = None) -> Value:
        """Make the table a collection is, and put what was written down in it.

        The entries are put in one at a time through the same call an assignment
        uses, so a collection written with a key twice holds it once -- which is
        what a set is, and what Python answers for a dictionary written that way.
        """
        table = self._new_table(builder, ty, expr.span, arena)
        written = (tuple((e, None) for e in expr.elements)
                   if isinstance(expr, ast.SetLit) else expr.entries)
        for key, value in written:
            key_ty = ty.element if isinstance(ty, SetType) else ty.key
            place = self._put_key(builder, table,
                                  self._lower_into(builder, key, key_ty, key.span),
                                  expr.span)
            if value is not None and isinstance(ty, DictType):
                builder.store(self._value_place(builder, place, ty.value),
                              self._lower_into(builder, value, ty.value,
                                               value.span), expr.span)
        return table

    def _new_table(self, builder: IRBuilder, ty: "SetType | DictType",
                   span: Span, arena: "GlobalVar | None" = None,
                   comes_from: "Value | None" = None) -> Value:
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

    def _one_type(self, builder: "IRBuilder | None", written: "Sequence[ast.Expr]",
                  wanted: Type | None) -> Type:
        """Lower each of *written* and give back the one type they share."""
        found: Type | None = wanted
        spoiled = False
        # Nothing is expected of an entry: what it is, is what the collection
        # is made of, and a mismatch between two of them is about the
        # collection rather than about wherever it is being given to.
        outer = self._initializing, self._assigning
        self._initializing, self._assigning = None, None
        for entry in written:
            value = self._lower_expr(builder, entry, None)
            ty = self._value_type_of(value)
            if ty is ERROR:
                spoiled = True
                continue
            if found is None:
                found = ty
            elif ty is not found:
                self._diags.emit(D.LANG_COLLECTION_MIXED_ENTRIES, entry.span,
                                 found=ty.render(), expected=found.render())
                spoiled = True
        self._initializing, self._assigning = outer
        return ERROR if spoiled or found is None else found

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
        if expected is not None and expected is not answer:
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

    def _lower_while(self, builder: IRBuilder, stmt: ast.While,
                     func: Function) -> None:
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

        Nothing travels on the conditional branch.  What the block after the
        loop reads are the loop's own parameters, which it dominates: the only
        way out of the loop is the test.
        """
        if builder.block is None:
            return
        carried = self._loop_locals(stmt.body)
        header = builder.new_block("loop")
        body = builder.new_block("body")
        after = builder.new_block("done")
        builder.br(header,
                   (*(local.value for local in carried), builder.memory()),
                   stmt.span)
        builder.position_at(header)
        params = [header.add_param(self._value_type_of(local.value), local.name)
                  for local in carried]
        token = header.add_param(MEM, "mem")
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
        builder.condbr(condition, body, after, span=stmt.span)
        outer_carried = self._carried
        # A name the loop carries is read by the next turn, so replacing the
        # value it stands for is not throwing that value away.
        self._carried = outer_carried | {id(local) for local in carried}
        builder.position_at(body)
        self._push_scope()
        self._lower_block(builder, stmt.body, func, as_result=False)
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
        builder.position_at(after)
        for local, param in zip(carried, params):
            local.value = param
            local.value_span = stmt.span
        builder.set_memory(token)

    def _lower_foreach(self, builder: IRBuilder, stmt: ast.ForEach,
                       func: Function) -> None:
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
            return
        found = self._iteration_over(builder, stmt)
        if found is None:
            # Bound to nothing that means anything, so that a later mention of
            # the name reports nothing of its own.
            self._push_scope()
            self._bind_local(stmt.name, UndefConst(ERROR), stmt.name_span)
            self._lower_block(builder, stmt.body, func, as_result=False)
            self._pop_scope()
            return
        carried = self._loop_locals(stmt.body)
        header = builder.new_block("loop")
        body = builder.new_block("body")
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
        for local, param in zip(carried, params):
            local.value = param
            local.value_span = stmt.span
            local.read = False
        builder.set_memory(token)
        builder.condbr(found.more(builder, state), body, after, span=stmt.span)
        outer_carried = self._carried
        self._carried = outer_carried | {id(local) for local in carried}
        builder.position_at(body)
        self._push_scope()
        self._bind_turn(builder, stmt, found.take(builder, state))
        self._lower_block(builder, stmt.body, func, as_result=False)
        self._pop_scope()
        self._carried = outer_carried
        if not builder.is_terminated and builder.block is not None:
            builder.br(header,
                       (*found.step(builder, state),
                        *(local.value for local in carried),
                        builder.memory()), stmt.span)
            for local in carried:
                local.read = True
        builder.position_at(after)
        for local, param in zip(carried, params):
            local.value = param
            local.value_span = stmt.span
        builder.set_memory(token)

    def _bind_turn(self, builder: IRBuilder, stmt: ast.ForEach,
                   value: Value) -> None:
        """Bind what a turn gave to the names the loop was written with."""
        if stmt.more:
            self._name_value(value, stmt.name)
            self._bind_apart(builder, stmt, value)
            return
        if stmt.name == WILDCARD_NAME:
            # The name that is not a name, as it is in a `match` arm: the loop
            # runs a turn for each value there is and the value itself is not
            # wanted.  Nothing is bound, so nothing is reported as unread.
            return
        self._name_value(value, stmt.name)
        self._bind_local(stmt.name, value, stmt.name_span,
                         value_span=stmt.iterable.span)

    # -- what a loop can take its values from ----------------------------------

    def _iteration_over(self, builder: IRBuilder,
                        stmt: ast.ForEach) -> "_Iteration | None":
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
        found: "_Iteration | None"
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
                      stmt: ast.ForEach) -> "_Iteration | None":
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
                       span: Span) -> "_Iteration":
        """Walk an array along its outermost dimension.

        A turn gives an element where the array has one dimension and a row
        where it has more, which row-major makes a run of elements and so
        arithmetic on the place rather than a copy.  Where the elements are and
        how many there are do not change from turn to turn, so they are worked
        out once here and read from where they were left.
        """
        start, lengths = self._shape_of(builder, value, ty, span)
        element = ty.element if ty.rank == 1 else self._row_type(ty, 1)

        def take(b: IRBuilder, s: "tuple[Value, ...]") -> Value:
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
                      ty: "SetType | DictType", span: Span) -> "_Iteration":
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

        def take(b: IRBuilder, s: "tuple[Value, ...]") -> Value:
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
                  ) -> "tuple[Value, Value, Value, bool, IntType] | None":
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
                    element: IntType) -> "tuple[int | None, bool]":
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

    def _loop_locals(self, body: ast.Block) -> list[_Local]:
        """The names in scope that a turn of the loop may change.

        Asked of the syntax rather than of what the lowering turns out to do,
        because the answer is wanted before the body is lowered.  Over-counting
        would cost a parameter the allocator then coalesces away; under-counting
        would be wrong, so what is collected is every assignment anywhere in the
        body, including inside a nested loop or the arms of an `if`.  A
        definition binds a new name and is not one of these.
        """
        found: dict[int, _Local] = {}
        for name in _assigned_in(body):
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
                      ty: Type) -> "list[tuple[ast.MatchArm, frozenset[int]]] | None":
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
                               taken: "list[tuple[ast.MatchArm, frozenset[int]]]",
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
                             taken: "list[tuple[ast.MatchArm, frozenset[int]]]",
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

    def _run_arms(self, builder: IRBuilder, stmt: "ast.Match | ast.If",
                  func: Function,
                  plan: "Sequence[_ArmPlan]", wanted: Type | None = None,
                  produces: bool = False,
                  otherwise: "BasicBlock | None" = None) -> Value:
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
                self._name_value(bound, name)
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

        A call will be the first expression this cannot say that about, since a
        call does whatever the callee does whether or not anyone wants its
        result.  There is no way to write one yet, and when there is, this asks
        the expression whether it has an effect instead of knowing that none
        has.
        """
        if isinstance(expr, ast.Call):
            # The exception this rule was written to leave room for.  A call
            # does whatever the function does, whether or not anyone wants what
            # it answers with.
            return
        found = self._diags.emit(D.LANG_STMT_VALUE_DISCARDED, expr.span)
        if isinstance(expr, ast.Binary) and expr.op is ast.BinaryOp.EQUAL:
            found.note(D.LANG_STMT_ASSIGNMENT_IS_AN_ARROW, expr.span)

    def _lower_local(self, builder: IRBuilder, node: ast.VarDef) -> None:
        """Lower a variable defined inside a function body.

        A local is a value, not a place: the name is bound to whatever the
        initializer produced.  Nothing is reserved in memory, because nothing
        can take its address yet -- and where the language later lets a name be
        assigned, a block parameter is what carries the new value across a
        branch, which is why the representation has them.
        """
        self._bind_attributes(node.attrs, AttrTarget.VARIABLE)
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
        self._name_value(bound, node.name)
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
        self._name_value(value, node.name)
        self._bind_local(node.name, value, node.name_span, node.mutable,
                         value_span=node.span)

    def _bind_apart(self, builder: IRBuilder,
                    node: "ast.VarDef | ast.ForEach", value: Value) -> None:
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
            self._name_value(part, name)
            self._bind_local(name, part, where, mutable, value_span=node.span)

    def _name_value(self, value: Value, name: str) -> None:
        """Record which local a computed value belongs to.

        Only an instruction is named, and only if it has no name already.  A
        constant is interned and shared with every other use of the same number,
        so writing a name on one would put that name on all of them; and where
        two locals stand for one value, the first name is the one that stays.

        The name is a hint and nothing reads it to decide anything.  It is what
        lets the textual form be read against the source it came from, and what
        lets a pass that removes a value say which local went with it.
        """
        if isinstance(value, Instruction) and value.name_hint is None:
            value.name_hint = name

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

    def _return_type(self, ref: "ast.TypeExpr | None") -> Type:
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
                if expected is not None and expected is not BOOL:
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
            case ast.Match():
                return self._lower_match(builder, expr, builder.function,
                                         expected, True)
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
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        context = self._hint_of(expr.left) or self._hint_of(expr.right)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._comparable(expr.left.span, expr.op, self._value_type_of(left))
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
        found = self._comparable(expr.right.span, expr.op, self._value_type_of(right))
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
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        context = self._hint_of(expr.left) or self._hint_of(expr.right)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._value_type_of(left)
            if ty is not ERROR and not isinstance(ty, FloatType):
                self._diags.emit(D.LANG_TYPE_APPROXIMATE_NEEDS_A_FLOAT,
                                 expr.left.span, operator=expr.op.value,
                                 found=ty.render())
                ty = ERROR
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
        found = self._value_type_of(right)
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
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._boolean(builder, expr.left, expr.op)
            right = self._boolean(builder, expr.right, expr.op)
        finally:
            self._operand_of = outer
        if left is None or right is None:
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
        if expected is not None and expected is not ty.ok:
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
        if expected is not None and expected is not ty.ok:
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
        if expected is not None and expected is not BOOL:
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
                 op: ast.BinaryOp | ast.UnaryOp) -> Value | None:
        """Lower *expr* where only a truth value will do, or report why not.

        Nothing else counts as one.  C's rule that any number other than zero is
        true is what `if (x = 0)` comes from, and where the question really is
        whether a number is zero, `≠` asks it.
        """
        value = self._lower_expr(builder, expr, BOOL)
        ty = self._value_type_of(value)
        if ty is ERROR:
            return None
        if ty is not BOOL:
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
        return builder.binary(BinOp.XOR, value, builder.bool_const(True), span)

    #: What each operator a set answers comes to, as walks of the two tables:
    #: which table is walked, which is asked about, and what is wanted of the
    #: answer.  Every one of them makes a table of its own rather than changing
    #: either operand, which is what an operator does everywhere else here.
    _SET_WALKS: "Final[dict[ast.BinaryOp, tuple[tuple[bool, int], ...]]]" = {
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
        context = expected if expected is not None else self._hint_of(expr)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            left = self._lower_expr(builder, expr.left, context)
            ty = self._value_type_of(left)
            if ty is not ERROR and not self._operand_type_stands(expr.op, ty):
                self._diags.emit(D.LANG_TYPE_OPERAND_NOT_INTEGER, expr.left.span,
                                 operator=expr.op.value, found=ty.render())
                ty = ERROR
            right = self._lower_expr(builder, expr.right,
                                     ty if ty is not ERROR else context)
        finally:
            self._operand_of = outer
        found = self._value_type_of(right)
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
            return builder.binary(_SHIFTS[expr.op][0 if signed else 1],
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
        return builder.binary(_BINARY_OPS[expr.op], left, right, expr.span)

    #: What each operator that can fault does, where both sides are known.  The
    #: saturating ones are not here: theirs is the answer nearest the end of the
    #: type, which always fits and is never a mistake.
    _ARITHMETIC: Final[dict[ast.BinaryOp, "Callable[[int, int], int]"]] = {
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
    _FLOAT_ARITHMETIC: Final[dict[ast.BinaryOp, "Callable[[float, float], float]"]] = {
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
        try:
            operand = self._lower_expr(builder, expr.operand, expected)
        finally:
            self._operand_of = outer
        ty = self._value_type_of(operand)
        if ty is ERROR:
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
        if expected is not None and expected is not BOOL:
            self._report_mismatch(expr.span, BOOL, expected)
            return UndefConst(ERROR)
        outer, self._operand_of = self._operand_of, expr.op.value
        try:
            operand = self._boolean(builder, expr.operand, expr.op)
        finally:
            self._operand_of = outer
        if operand is None:
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
                            expected: Type | None) -> "FloatType | None":
        """The type a floating-point literal has, from its suffix or its place."""
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

    def _lower_call(self, builder: IRBuilder, expr: ast.Call,
                    expected: Type | None) -> Value:
        """Lower a call, checking it against what the function takes and gives.

        A function is not a value, so what is called is resolved here rather
        than lowered as an expression: there is nothing for a name that stands
        for a function to become.
        """
        func = self._callee(expr.callee)
        if func is None:
            return UndefConst(ERROR)
        wanted = func.ty.params
        if len(expr.args) != len(wanted):
            self._diags.emit(D.LANG_CALL_WRONG_ARGUMENT_COUNT, expr.span,
                             name=func.name, expected=len(wanted),
                             found=len(expr.args))
            return UndefConst(ERROR)
        args: list[Value] = []
        for position, (written, ty) in enumerate(zip(expr.args, wanted), start=1):
            outer, self._handing_over = self._handing_over, (func.name, position)
            try:
                args.append(self._lower_into(builder, written, ty, written.span))
            finally:
                self._handing_over = outer
        if any(value.ty is ERROR for value in args):
            return UndefConst(ERROR)
        answer = builder.call(func, args, func.ty.ret, expr.span)
        if func.ty.ret is VOID and expected is not None:
            # Somewhere wants a value and there is none.  The two places a call
            # like this may stand are a statement of its own and after `return`
            # in a function that also answers with nothing, and neither of them
            # asks for one.
            self._diags.emit(D.LANG_CALL_HAS_NO_VALUE, expr.span, name=func.name)
            return UndefConst(ERROR)
        if expected is not None and answer.ty is not expected:
            self._report_mismatch(expr.span, answer.ty, expected)
            return UndefConst(ERROR)
        return answer

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
        if expected is not None and resolved.ty != expected:
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
        value = self._lower_expr(builder, expr, None)
        found = self._value_type_of(value)
        if found is expected or found is ERROR:
            return value
        if found is expected.ok:
            return builder.wrap(value, builder.bool_const(False), expected, span)
        self._report_mismatch(span, found, expected)
        return UndefConst(ERROR)

    def _spread(self, builder: IRBuilder, expr: ast.Expr, expected: ArrayType,
                span: Span) -> Value:
        """Lower *expr* where an array of no stated length is wanted."""
        value = self._lower_expr(builder, expr, None)
        found = self._value_type_of(value)
        if found is expected or found is ERROR:
            return value
        if isinstance(found, ArrayType) and found.fixed \
                and found.element is expected.element \
                and found.rank == expected.rank:
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

    def _report_mismatch(self, span: Span, found: Type, expected: Type) -> None:
        """Report a type that does not match what the context requires.

        A type that could not be worked out matches anything: the mistake behind
        it has been reported once already, and saying so again at every place the
        value reaches would add nothing.
        """
        if found is ERROR or expected is ERROR:
            return
        if self._operand_of is not None:
            # Two different mistakes: an operand of a kind the operator has no
            # meaning for, and two operands of one kind whose widths differ.
            if not isinstance(found, IntType):
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
