"""Running a function at compile time.

**Over the syntax tree, and after it has been checked.**  The checker has already
said the program is well typed and what every name means; what is left is to work
out the values, which is what this does -- one walk of the tree with an
environment, the way a small interpreter works.  Doing it over the intermediate
representation instead would be nearer the compiler's own idea of the program,
and was not chosen: a string joined to another at compile time is one Python
string joined to another here and is an arena and a runtime call there, and the
whole point of this is that none of it happens at run time.

**What it cannot do, it refuses.**  A language has more in it than a compiler can
work out before the program runs -- anything that reads a device, anything that
depends on the machine -- and this is a first evaluator besides: it does numbers,
truth values, characters, strings, arrays, records, references, the ordinary
control flow and calls to functions written in the same program.  Everything else
answers with a diagnostic naming the construct, so what is missing is a sentence
a reader can act on rather than a wrong answer.

**Numbers are worked out without a width.**  The checker has already settled the
types and the ranges of the literals; what the evaluator computes with is whole
numbers, so an intermediate value that would not have fitted in the type it is
being computed in does not stop it here.  Where a value is finally *used* -- put
into a field of the build object, say -- what it has to fit is checked there.
The entry in `TODO-pypl4g.md` says what it would take to do better.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Final, Mapping, Sequence

from ..front import ast
from ..front.token import ENVIRON_NAME, STATIC_NAME
from ..source.location import INVALID_SPAN, Span

#: How many steps one evaluation may take before it is called an endless one.
#: A build function is a few dozen statements; a hundred thousand is beyond
#: anything a person writes and short enough that a compiler that has run into a
#: loop with no end says so rather than hanging.
STEPS: Final[int] = 100_000

#: And how deep a call may go, for the same reason: a recursion with no end is a
#: Python stack overflow otherwise, which reports nothing a reader can use.
DEPTH: Final[int] = 128


class CannotEvaluate(Exception):
    """What the compiler could not work out, and where.

    Carries the place and the words rather than a diagnostic, so that whoever
    asked decides which diagnostic it becomes: the same failure is reported
    differently by a build function and by an expression that had to be constant.
    """

    def __init__(self, span: Span, detail: str, *, ran_out: bool = False) -> None:
        super().__init__(detail)
        self.span = span
        self.detail = detail
        #: Whether what went wrong is that it did not finish, which is a
        #: different thing to tell a reader than a construct it cannot do.
        self.ran_out = ran_out


@dataclass(slots=True)
class Cell:
    """Somewhere a value lives, so that a reference has something to point at."""

    value: object


@dataclass(slots=True)
class Record:
    """A value of a record type, while it is being worked out."""

    type_name: str
    fields: dict[str, object] = field(default_factory=dict)


class _Nothing:
    """What a lookup that found nothing answers with.

    A collection answers with a result -- what is there, or the fact that
    nothing is -- and `??` is what reads one.  There is one of these and it is
    compared by identity, so that a dictionary holding nothing under a key is
    told apart from one holding something that happens to be false.
    """

    def __repr__(self) -> str:
        """What a message about it says, which is what the language calls it."""
        return "\N{UP TACK}"


#: The one of them.
MISSING: Final[_Nothing] = _Nothing()


@dataclass(slots=True)
class Reference:
    """What `&x` and `&mut x` answer with: the cell, not the value in it."""

    cell: Cell
    mutable: bool = False


@dataclass(frozen=True, slots=True)
class Builtin:
    """A function the compiler provides while an evaluation is running.

    What it is for is the things a program cannot say for itself -- adding an
    artifact to a build, asking the command line a question -- so its body is
    Python and it is reached by the name the module declares it under.
    """

    name: str
    #: Called with the place the call is written first and the arguments after,
    #: so that what it refuses it refuses where a reader can see it.
    run: Callable[..., object]


class _Return(Exception):
    """A function answered."""

    def __init__(self, value: object) -> None:
        super().__init__("return")
        self.value = value


class _Break(Exception):
    """A loop was left."""

    def __init__(self, label: str | None, value: object) -> None:
        super().__init__("break")
        self.label = label
        self.value = value


class _Continue(Exception):
    """A loop was begun again."""

    def __init__(self, label: str | None) -> None:
        super().__init__("continue")
        self.label = label


class Evaluator:
    """One evaluation: the program it is over, and what it has worked out.

    Built for one run and thrown away.  It holds no state a second run would
    want: what a compile-time evaluation leaves behind is its answer, and
    everything else about it was about getting there.
    """

    def __init__(self, units: Sequence[ast.SourceUnit],
                 builtins: Mapping[str, Mapping[str, Builtin]] | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        #: Every function the program defines, by the name it was defined under.
        self._functions: dict[str, ast.FuncDef] = {}
        #: The top-level variables, and what they came to once anything asked.
        #: Worked out on first ask rather than in order, so that one written
        #: below another that names it is not a question about the order of a
        #: file.
        self._globals: dict[str, ast.VarDef] = {}
        self._settled: dict[str, Cell] = {}
        #: Which module each imported name stands for, so that `std.f(...)`
        #: finds what `std` was bound to.
        self._modules: dict[str, str] = {}
        self._builtins = dict(builtins or {})
        #: What `⎕environ` stands for while the compiler is working something
        #: out, which is the compiler's own environment.  A program reads the
        #: process's under that name and the compiler is the process here, so
        #: the one name answers the one question in both places.
        self._environ: Mapping[str, str] = dict(environ or {})
        self._scopes: list[dict[str, Cell]] = []
        self._steps = 0
        self._depth = 0
        for unit in units:
            for item in unit.items:
                if isinstance(item, ast.FuncDef):
                    self._functions[item.name] = item
                elif isinstance(item, ast.VarDef):
                    self._globals[item.name] = item
                elif isinstance(item, ast.ModuleImport):
                    self._modules[item.name] = item.source

    # -- what a caller asks for ------------------------------------------------

    def call(self, func: ast.FuncDef, arguments: Sequence[object]) -> object:
        """Run *func* over *arguments* and answer with what it answered."""
        return self._call(func, list(arguments), func.name_span)

    # -- functions -------------------------------------------------------------

    def _call(self, func: ast.FuncDef, arguments: list[object],
              span: Span) -> object:
        """One call, with its own scope and its own depth."""
        if func.body is None:
            raise CannotEvaluate(span, "".join((
                "'", func.name, "' has no body the compiler can run")))
        if self._depth >= DEPTH:
            raise CannotEvaluate(span, "".join((
                "calls went ", str(DEPTH), " deep")), ran_out=True)
        if len(arguments) != len(func.params):
            # The checker settles this; a default the call left out is what is
            # left, and nothing yet works one out at compile time.
            raise CannotEvaluate(span, "".join((
                "'", func.name, "' wants ", str(len(func.params)),
                " arguments and this call works out ", str(len(arguments)))))
        scope = {param.name: Cell(value)
                 for param, value in zip(func.params, arguments)}
        self._depth += 1
        self._scopes.append(scope)
        try:
            return self._block(func.body)
        except _Return as answered:
            return answered.value
        finally:
            self._scopes.pop()
            self._depth -= 1

    # -- statements ------------------------------------------------------------

    def _block(self, block: ast.Block) -> object:
        """Run every statement, answering with what the last one came to.

        What a `defer` puts off runs when the block is left, the last put off first:
        at its end, and on a `return`, `break` or `continue` going through it.  A
        statement that cannot be run stops the evaluation, and nothing after it is
        run at all.
        """
        self._scopes.append({})
        later: list[ast.Stmt] = []
        try:
            answer: object = None
            for stmt in block.stmts:
                if isinstance(stmt, ast.Defer):
                    self._step(stmt.span)
                    later.append(stmt.stmt)
                    continue
                answer = self._stmt(stmt)
            self._put_off(later)
            return answer
        except (_Return, _Break, _Continue):
            self._put_off(later)
            raise
        finally:
            self._scopes.pop()

    def _put_off(self, later: list[ast.Stmt]) -> None:
        """Run what a block put off, the last first."""
        for stmt in reversed(later):
            self._stmt(stmt)

    def _stmt(self, stmt: ast.Stmt) -> object:
        """Run one statement, answering with its value where it has one."""
        self._step(stmt.span)
        match stmt:
            case ast.VarDef() | ast.ModuleImport():
                return self._define(stmt)
            case ast.ExprStmt():
                return self._expr(stmt.value)
            case ast.ReturnStmt():
                raise _Return(None if stmt.value is None else self._expr(stmt.value))
            case ast.AssignStmt():
                self._assign_name(stmt)
                return None
            case ast.MemberAssign():
                self._assign_member(stmt)
                return None
            case ast.DerefAssign():
                self._assign_deref(stmt)
                return None
            case ast.ElementAssign():
                self._assign_element(stmt)
                return None
            case ast.Break():
                raise _Break(stmt.label,
                             None if stmt.value is None else self._expr(stmt.value))
            case ast.Continue():
                raise _Continue(stmt.label)
            case ast.EmptyStmt():
                return None
            case _:
                raise self._refuse(stmt.span, stmt)

    def _define(self, stmt: ast.VarDef | ast.ModuleImport) -> object:
        """Bind a name in the innermost scope."""
        if isinstance(stmt, ast.ModuleImport):
            self._modules[stmt.name] = stmt.source
            return None
        if stmt.more:
            raise CannotEvaluate(stmt.span,
                                 "taking a value apart into several names")
        self._scopes[-1][stmt.name] = Cell(self._expr(stmt.value))
        return None

    def _assign_name(self, stmt: ast.AssignStmt) -> None:
        """`name ← value`, which rebinds what the name stands for."""
        if stmt.more:
            raise CannotEvaluate(stmt.span,
                                 "taking a value apart into several names")
        self._cell_of(stmt.name, stmt.name_span).value = self._expr(stmt.value)

    def _assign_member(self, stmt: ast.MemberAssign) -> None:
        """`record.field ← value`, through a name or through a reference."""
        held = self._record_of(stmt.base)
        held.fields[stmt.name] = self._expr(stmt.value)

    def _assign_deref(self, stmt: ast.DerefAssign) -> None:
        """`reference⌖ ← value`, which writes where the reference points."""
        found = self._expr(stmt.target)
        if not isinstance(found, Reference):
            raise CannotEvaluate(stmt.span, "writing through something that is "
                                            "not a reference")
        found.cell.value = self._expr(stmt.value)

    def _assign_element(self, stmt: ast.ElementAssign) -> None:
        """`array⟦i⟧ ← value`."""
        held = self._expr(stmt.base)
        if isinstance(held, Reference):
            held = held.cell.value
        if not isinstance(held, list) or len(stmt.indices) != 1:
            raise CannotEvaluate(stmt.span, "writing into this")
        index = self._as_index(stmt.indices[0], len(held))
        held[index] = self._expr(stmt.value)

    # -- control flow, which is written where a value is wanted ---------------

    def _if(self, stmt: ast.If) -> object:
        """The first arm whose condition holds, or the one with none."""
        for arm in stmt.arms:
            if arm.condition is None:
                return self._block(arm.body)
            if self._truth(arm.condition):
                return self._block(arm.body)
        return None

    def _while(self, stmt: ast.While) -> object:
        """A loop, until the condition stops holding or something leaves it."""
        while self._truth(stmt.condition):
            self._step(stmt.span)
            try:
                self._block(stmt.body)
            except _Break as left:
                if left.label not in (None, stmt.label):
                    raise
                return left.value
            except _Continue as again:
                if again.label not in (None, stmt.label):
                    raise
        if stmt.alternative is not None:
            return self._block(stmt.alternative)
        return None

    def _foreach(self, stmt: ast.ForEach) -> object:
        """A loop over an array, a list or a range."""
        if stmt.more:
            raise CannotEvaluate(stmt.span,
                                 "taking each element apart into several names")
        over = self._iterable(stmt.iterable)
        for one in over:
            self._step(stmt.span)
            self._scopes.append({stmt.name: Cell(one)})
            try:
                self._block(stmt.body)
            except _Break as left:
                if left.label not in (None, stmt.label):
                    raise
                return left.value
            except _Continue as again:
                if again.label not in (None, stmt.label):
                    raise
            finally:
                self._scopes.pop()
        if stmt.alternative is not None:
            return self._block(stmt.alternative)
        return None

    def _iterable(self, expr: ast.Expr) -> list[object]:
        """What a loop walks: a run of values, worked out before it begins."""
        if isinstance(expr, ast.Range):
            start = self._number(expr.start) if expr.start is not None else 0
            stop = self._number(expr.stop) if expr.stop is not None else None
            step = self._number(expr.step) if expr.step is not None else 1
            if stop is None:
                raise CannotEvaluate(expr.span, "a range with no end")
            if step == 0:
                raise CannotEvaluate(expr.span, "a range whose step is nought")
            return list(range(start, stop, step))
        found = self._expr(expr)
        if isinstance(found, Reference):
            found = found.cell.value
        if isinstance(found, list):
            return list(found)
        if isinstance(found, str):
            return list(found)
        raise CannotEvaluate(expr.span, "walking over this")

    # -- expressions -----------------------------------------------------------

    def _expr(self, expr: ast.Expr) -> object:
        """What an expression comes to."""
        self._step(expr.span)
        match expr:
            case ast.IntLit():
                return expr.value
            case ast.FloatLit():
                return expr.value
            case ast.BoolLit():
                return expr.value
            case ast.CharLit():
                return expr.value
            case ast.StringLit():
                return expr.value
            case ast.NameRef():
                return self._name(expr)
            case ast.Binary():
                return self._binary(expr)
            case ast.Unary():
                return self._unary(expr)
            case ast.Call():
                return self._call_expr(expr)
            case ast.Member():
                return self._member(expr)
            case ast.Element():
                return self._element(expr)
            case ast.Index():
                return self._index(expr)
            case ast.ArrayLit() | ast.ListLit():
                return [self._expr(one) for one in expr.elements]
            case ast.Allocated() if isinstance(expr.arena, ast.NameRef) \
                    and expr.arena.name == STATIC_NAME:
                # What is in the image is what it says, before the program runs
                # as much as after.
                return self._expr(expr.value)
            case ast.TupleLit():
                return [self._expr(one) for one in expr.members]
            case ast.AddressOf():
                return self._address_of(expr)
            case ast.Deref():
                return self._deref(expr)
            case ast.Named():
                return self._expr(expr.value)
            case ast.Scoped():
                return self._block(expr.body)
            case ast.If():
                return self._if(expr)
            case ast.While():
                return self._while(expr)
            case ast.ForEach():
                return self._foreach(expr)
            case _:
                raise self._refuse(expr.span, expr)

    def _name(self, expr: ast.NameRef) -> object:
        """What a name stands for: a local, then a variable of the program."""
        return self._cell_of(expr.name, expr.span).value

    def _cell_of(self, name: str, span: Span) -> Cell:
        """Where the value of *name* lives."""
        for scope in reversed(self._scopes):
            found = scope.get(name)
            if found is not None:
                return found
        settled = self._settled.get(name)
        if settled is not None:
            return settled
        if name == ENVIRON_NAME:
            # Made on first ask and kept, so that everything reading it reads
            # the one dictionary -- which is what the name means at run time
            # too.
            cell = Cell(self._environ)
            self._settled[name] = cell
            return cell
        defined = self._globals.get(name)
        if defined is not None:
            # Worked out on first ask, and once: a variable at the top level is
            # one value however many things read it.
            cell = Cell(None)
            self._settled[name] = cell
            cell.value = self._expr(defined.value)
            return cell
        raise CannotEvaluate(span, "".join((
            "'", name, "' is not something the compiler can work out here")))

    def _binary(self, expr: ast.Binary) -> object:
        """What an operator comes to, over what its two sides came to."""
        op = expr.op
        # The two that do not work both sides out: what is on the right is only
        # asked where what is on the left did not settle it.
        if op in (ast.BinaryOp.SHORT_AND, ast.BinaryOp.LOGIC_AND):
            return self._truth(expr.left) and self._truth(expr.right)
        if op in (ast.BinaryOp.SHORT_OR, ast.BinaryOp.LOGIC_OR):
            return self._truth(expr.left) or self._truth(expr.right)
        if op is ast.BinaryOp.OR_ELSE:
            # The one that reads a result: what is on the right is worked out
            # only where the left found nothing.
            found = self._value(expr.left, missing=True)
            return self._value(expr.right) if found is MISSING else found
        left = self._value(expr.left)
        right = self._value(expr.right)
        try:
            return _apply(op, left, right)
        except _NotForThis:
            raise CannotEvaluate(expr.span, "".join((
                "'", str(op.value), "' over these"))) from None
        except ZeroDivisionError:
            raise CannotEvaluate(expr.span, "a division by nought") from None

    def _unary(self, expr: ast.Unary) -> object:
        """And what a one-sided operator comes to."""
        found = self._value(expr.operand)
        match expr.op:
            case ast.UnaryOp.LOGIC_NOT if isinstance(found, bool):
                return not found
            case ast.UnaryOp.LENGTH if isinstance(found, (list, str, dict,
                                                          set, frozenset)):
                return len(found)
            case ast.UnaryOp.BIT_NOT if isinstance(found, int) \
                    and not isinstance(found, bool):
                return ~found
            case _:
                raise CannotEvaluate(expr.span, "".join((
                    "'", str(expr.op.value), "' over this")))

    def _call_expr(self, expr: ast.Call) -> object:
        """A call: to something the compiler provides, or to the program's own."""
        arguments = [self._expr(one) for one in expr.args]
        match expr.callee:
            case ast.Member(base=ast.NameRef() as base, name=name):
                found = self._builtin(base.name, name, expr.span)
                return found.run(expr.span, *arguments)
            case ast.NameRef(name=name):
                func = self._functions.get(name)
                if func is None:
                    raise CannotEvaluate(expr.span, "".join((
                        "'", name, "' is not a function of this program")))
                return self._call(func, arguments, expr.span)
            case _:
                raise CannotEvaluate(expr.span, "a call to something worked out")

    def _builtin(self, module: str, name: str, span: Span) -> Builtin:
        """The compiler-provided function *module*.*name*, where there is one."""
        source = self._modules.get(module)
        provided = self._builtins.get(source or "", {})
        found = provided.get(name)
        if found is None:
            raise CannotEvaluate(span, "".join((
                "'", module, ".", name,
                "' is not something the compiler provides here")))
        return found

    def _member(self, expr: ast.Member) -> object:
        """A field of a record, read through a name or through a reference."""
        held = self._record_of(expr.base)
        if expr.name not in held.fields:
            raise CannotEvaluate(expr.span, "".join((
                "'", held.type_name, "' has nothing called '", expr.name, "'")))
        return held.fields[expr.name]

    def _element(self, expr: ast.Element) -> object:
        """One element of an array."""
        held = self._value(expr.base)
        if not isinstance(held, (list, str)) or len(expr.indices) != 1:
            raise CannotEvaluate(expr.span, "reading an element of this")
        return held[self._as_index(expr.indices[0], len(held))]

    def _index(self, expr: ast.Index) -> object:
        """One entry of a collection: what the key stands for, or nothing.

        A dictionary answers with what it holds under the key and a set with
        whether the key is in it, which is what each of them answers at run
        time.  Nothing found is `MISSING`, which `??` reads and everything else
        refuses -- so a value that was not there cannot be used as one.
        """
        held = self._value(expr.base)
        key = self._value(expr.key)
        if isinstance(held, dict):
            if not isinstance(key, (str, int, bool)):
                raise CannotEvaluate(expr.span, "a key of this kind")
            return held.get(key, MISSING)
        if isinstance(held, (set, frozenset)):
            return key in held
        raise CannotEvaluate(expr.span, "reading an entry of this")

    def _address_of(self, expr: ast.AddressOf) -> object:
        """`&x` and `&mut x`, which answer with where the value lives."""
        if isinstance(expr.operand, ast.NameRef):
            return Reference(self._cell_of(expr.operand.name, expr.span),
                             expr.mutable)
        raise CannotEvaluate(expr.span, "a reference to this")

    def _deref(self, expr: ast.Deref) -> object:
        """`r⌖`, which answers with what the reference points at."""
        found = self._expr(expr.operand)
        if not isinstance(found, Reference):
            raise CannotEvaluate(expr.span, "reading through something that is "
                                            "not a reference")
        return found.cell.value

    # -- odds and ends ---------------------------------------------------------

    def _value(self, expr: ast.Expr, *, missing: bool = False) -> object:
        """What an expression comes to, with a reference followed.

        A lookup that found nothing is refused unless the caller is `??`, which
        is the one thing that reads it: everywhere else, using what was not
        there is the mistake and saying so where it is written is the report.
        """
        found = self._expr(expr)
        if isinstance(found, Reference):
            found = found.cell.value
        if found is MISSING and not missing:
            raise CannotEvaluate(expr.span, "a value that is not there, which "
                                            "\N{DOUBLE QUESTION MARK} reads and nothing else does")
        return found

    def _record_of(self, expr: ast.Expr) -> Record:
        """The record an expression names, however it names it."""
        found = self._value(expr)
        if isinstance(found, Record):
            return found
        raise CannotEvaluate(expr.span, "this is not a record")

    def _truth(self, expr: ast.Expr) -> bool:
        """What a condition comes to, which has to be a truth value."""
        found = self._value(expr)
        if isinstance(found, bool):
            return found
        raise CannotEvaluate(expr.span, "a condition that is not a truth value")

    def _number(self, expr: ast.Expr) -> int:
        """What a place wanting a whole number comes to."""
        found = self._value(expr)
        if isinstance(found, bool) or not isinstance(found, int):
            raise CannotEvaluate(expr.span, "this is not a whole number")
        return found

    def _as_index(self, expr: ast.Expr, length: int) -> int:
        """An index into something *length* long, checked against it."""
        index = self._number(expr)
        if not 0 <= index < length:
            raise CannotEvaluate(expr.span, "".join((
                "the index ", str(index), " is outside a run of ", str(length))))
        return index

    def _step(self, span: Span) -> None:
        """Count one step, and give up where there have been too many."""
        self._steps += 1
        if self._steps > STEPS:
            raise CannotEvaluate(span, "".join((
                "it was still going after ", str(STEPS), " steps")),
                ran_out=True)

    def _refuse(self, span: Span, node: object) -> CannotEvaluate:
        """The refusal for a piece of the language nothing here works out."""
        return CannotEvaluate(span if span.is_valid else INVALID_SPAN,
                              _DESCRIBED.get(type(node).__name__,
                                             type(node).__name__.lower()))


class _NotForThis(Exception):
    """An operator over values it is not for, which the caller reports."""


def _apply(op: ast.BinaryOp, left: object, right: object) -> object:
    """One operator over two values it has already been given."""
    if op is ast.BinaryOp.EQUAL:
        return left == right
    if op is ast.BinaryOp.NOT_EQUAL:
        return left != right
    if op is ast.BinaryOp.CONCAT:
        # A string joined to a string, and a run joined to a run.
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        if isinstance(left, list) and isinstance(right, list):
            return [*left, *right]
        raise _NotForThis
    numbers = (isinstance(left, (int, float)) and not isinstance(left, bool)
               and isinstance(right, (int, float)) and not isinstance(right, bool))
    both_strings = isinstance(left, str) and isinstance(right, str)
    order = {ast.BinaryOp.LESS: "<", ast.BinaryOp.LESS_EQUAL: "<=",
             ast.BinaryOp.GREATER: ">", ast.BinaryOp.GREATER_EQUAL: ">="}
    if op in order:
        if not numbers and not both_strings:
            raise _NotForThis
        match order[op]:
            case "<":
                return left < right
            case "<=":
                return left <= right
            case ">":
                return left > right
            case _:
                return left >= right
    if not numbers:
        raise _NotForThis
    match op:
        case ast.BinaryOp.ADD | ast.BinaryOp.SAT_ADD:
            return left + right
        case ast.BinaryOp.SUBTRACT | ast.BinaryOp.SAT_SUB:
            return left - right
        case ast.BinaryOp.MULTIPLY | ast.BinaryOp.SAT_MUL:
            return left * right
        case ast.BinaryOp.DIVIDE:
            return left // right if isinstance(left, int) and isinstance(right, int) \
                else left / right
        case ast.BinaryOp.REMAINDER:
            return left % right
        case ast.BinaryOp.BIT_AND:
            return left & right
        case ast.BinaryOp.BIT_OR:
            return left | right
        case ast.BinaryOp.BIT_XOR:
            return left ^ right
        case ast.BinaryOp.SHIFT_LEFT:
            return left << right
        case ast.BinaryOp.SHIFT_RIGHT:
            return left >> right
        case ast.BinaryOp.MAX:
            return max(left, right)
        case ast.BinaryOp.MIN:
            return min(left, right)
        case ast.BinaryOp.POWER:
            return left ** right
        case ast.BinaryOp.DIVIDES:
            return right % left == 0
        case ast.BinaryOp.NOT_DIVIDES:
            return right % left != 0
        case _:
            raise _NotForThis


#: How each piece of the language this cannot work out is named in the refusal.
#: The words are the ones the specification uses, so that what a reader is told
#: is a thing they can look up.
_DESCRIBED: Final[Mapping[str, str]] = {
    "Match": "a match", "Lambda": "a lambda", "Try": "a `?`",
    "Failure": "a failure", "Lifted": "a lifted type", "Raised": "a power",
    "SetLit": "a set", "DictLit": "a dictionary", "Spread": "a spread",
    "Range": "a range outside a loop", "Capture": "a capture",
    "CaptureAll": "a capture", "Index": "an entry of a collection",
}
