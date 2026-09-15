"""IR instructions.

The hierarchy describes the *shapes* an instruction can have; the individual
operations are enumerated inside a shape.  That keeps the number of classes
bounded as operators multiply while still letting an exhaustive match over the
shapes be checked statically.
"""

from __future__ import annotations

from enum import Enum
from typing import Sequence

from ..source.location import INVALID_SPAN, Span
from .types import MEM, Type, VOID
from .value import Value


class BinOp(Enum):
    """The binary arithmetic and bitwise operations."""

    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    SDIV = "sdiv"
    UDIV = "udiv"
    SREM = "srem"
    UREM = "urem"
    AND = "and"
    OR = "or"
    XOR = "xor"
    SHL = "shl"
    ASHR = "ashr"
    LSHR = "lshr"
    ROTL = "rotl"
    ROTR = "rotr"

    #: The three that answer with the nearest value their type can hold rather
    #: than going past it.  They are operations of their own and not a flag on
    #: the three above, because what a backend emits for one has the comparison
    #: and the bound in it and looks nothing like an addition.
    #: Dividing two floating-point numbers.  It is not `SDIV` or `UDIV`: those
    #: two are one question asked of a signed and an unsigned number, and a
    #: floating-point number is neither.
    FDIV = "fdiv"

    SAT_ADD = "sat.add"
    SAT_SUB = "sat.sub"
    SAT_MUL = "sat.mul"

    #: The ones that answer with the low bits of what the operation came to,
    #: whatever it came to.  A program writes one by putting the operator inside
    #: `⎕wrap`, which is what says that going past the end of the type is
    #: what was meant; the compiler also generates them for itself, where a hash
    #: is defined on the bits and there is nothing about an overflow to report.
    WRAP_ADD = "wrap.add"
    WRAP_SUB = "wrap.sub"
    WRAP_MUL = "wrap.mul"

    #: The larger and the smaller of two values, which is one question asked of
    #: a signed and an unsigned number and is therefore two instructions.
    SMAX = "smax"
    UMAX = "umax"
    SMIN = "smin"
    UMIN = "umin"

    #: And the moving ones, whose distance is taken modulo the width of the type
    #: rather than being a question the program can get wrong.  Every width is a
    #: power of two, so that is a mask and not a division.
    WRAP_SHL = "wrap.shl"
    WRAP_ASHR = "wrap.ashr"
    WRAP_LSHR = "wrap.lshr"
    WRAP_ROTL = "wrap.rotl"
    WRAP_ROTR = "wrap.rotr"


class UnOp(Enum):
    """The unary operations."""

    NEG = "neg"
    NOT = "not"
    #: The magnitude of a floating-point number, which is the number with its
    #: sign cleared.  There is no integer form: the magnitude of the smallest
    #: signed number is not a number of its type.
    FABS = "fabs"
    #: The four roundings of a floating-point number to a whole one: the one
    #: below it, the one above it, the nearer of the two with a tie going to
    #: the even one, and whichever the processor's own rounding mode names.
    FLOOR = "floor"
    CEIL = "ceil"
    NEAREST = "nearest"
    ROUNDED = "rounded"


class CmpPred(Enum):
    """The comparison predicates."""

    EQ = "eq"
    NE = "ne"
    SLT = "slt"
    SLE = "sle"
    SGT = "sgt"
    SGE = "sge"
    ULT = "ult"
    ULE = "ule"
    UGT = "ugt"
    UGE = "uge"


class CastKind(Enum):
    """The conversions between representations."""

    ZEXT = "zext"
    SEXT = "sext"
    TRUNC = "trunc"
    BITCAST = "bitcast"
    #: A floating-point value in a wider floating-point type.  Every wider
    #: format holds every value of a narrower one exactly, so nothing is lost
    #: and there is nothing to check.
    FEXT = "fext"


class Instruction(Value):
    """Base of every instruction."""

    __slots__ = ("operands", "parent", "span")

    def __init__(self, ty: Type, operands: Sequence[Value] = (),
                 span: Span = INVALID_SPAN, name_hint: str | None = None) -> None:
        super().__init__(ty, name_hint)
        self.operands: list[Value] = list(operands)
        self.parent: object | None = None
        self.span = span

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        raise NotImplementedError

    @property
    def has_result(self) -> bool:
        """Whether this instruction defines a value that can be named."""
        return self.ty is not VOID

    @property
    def has_effects(self) -> bool:
        """Whether removing this instruction would change what the program does.

        An instruction that has none and that nothing uses can be dropped, so
        this is the question a dead-code pass asks.  Each shape answers for
        itself rather than a pass keeping a list, so a shape added later cannot
        be forgotten -- and answering "no" has to be a deliberate act, which is
        the safe way round.
        """
        return False

    def references(self) -> Sequence[object]:
        """The functions this instruction names.

        Calling one is naming it, and for now that is the only way there is.
        When a function can be a value -- a pointer to one, a table of them --
        the shape that does it answers here too, and what decides whether a
        function is reachable does not have to change.
        """
        return ()

    def reads(self) -> Sequence[Value]:
        """The places this instruction reads from.

        A place is whatever a pointer names -- today only a variable at the top
        level, since nothing else has an address.  Together with ``writes`` this
        is what lets a pass over the whole program ask which variables are read
        and which are only ever written, without matching on instruction classes.
        """
        return ()

    def writes(self) -> Sequence[Value]:
        """The places this instruction writes to."""
        return ()


class BinaryInst(Instruction):
    """An arithmetic or bitwise operation on two values of the same type."""

    __slots__ = ("op",)

    def __init__(self, op: BinOp, lhs: Value, rhs: Value, span: Span = INVALID_SPAN,
                 ty: Type | None = None) -> None:
        # The answer has the operands' type except where the operation may not
        # have one: a division answers with a result type, whose answer type is
        # what the operands are.
        super().__init__(lhs.ty if ty is None else ty, (lhs, rhs), span)
        self.op = op

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return self.op.value


class UnaryInst(Instruction):
    """A unary operation."""

    __slots__ = ("op",)

    def __init__(self, op: UnOp, value: Value, span: Span = INVALID_SPAN) -> None:
        super().__init__(value.ty, (value,), span)
        self.op = op

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return self.op.value


class CmpInst(Instruction):
    """A comparison, whose result is a boolean."""

    __slots__ = ("pred",)

    def __init__(self, pred: CmpPred, lhs: Value, rhs: Value, result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (lhs, rhs), span)
        self.pred = pred

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "".join(("icmp.", self.pred.value))


class SplatInst(Instruction):
    """One value in every lane of a vector.

    What an operator's side that is not an array becomes where the other side
    is: `v + 10u8` adds ten to every element, and this is the ten, said once for
    all of them.
    """

    __slots__ = ()

    def __init__(self, value: Value, target: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(target, (value,), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "splat"


class AnyLaneInst(Instruction):
    """Whether any lane of a vector of truth values is true.

    This is how a check over a whole vector is asked: the check is made in every
    lane at once and answers a lane apiece, and what the branch wants is the one
    question "did any of them". Nothing in the language writes one -- it is what
    a backend needs to keep a vector operation's meaning the same as the
    element-by-element one it stands for, which stops where the first element
    goes past the end of its type.
    """

    __slots__ = ()

    def __init__(self, value: Value, result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (value,), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "anylane"


class WrapInst(Instruction):
    """A value, and whether it is the answer, as one value of a result type.

    Both halves are always given: a result is the answer beside a truth value
    saying whether there is one, and an error that carries nothing still leaves
    the answer half a value -- an undefined one, which nothing may read, since
    reading it is what the truth value forbids.
    """

    __slots__ = ()

    def __init__(self, value: Value, failed: Value, result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (value, failed), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "wrap"


class TupleInst(Instruction):
    """Several values made into one that travels together."""

    __slots__ = ()

    def __init__(self, values: Sequence[Value], ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(ty, tuple(values), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "tuple"


class ExtractInst(Instruction):
    """One of the values a tuple is made of, named by its position."""

    __slots__ = ("index",)

    def __init__(self, value: Value, index: int, ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(ty, (value,), span)
        self.index = index

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "".join(("extract.", str(self.index)))


class UnwrapInst(Instruction):
    """The answer half of a result, which means nothing where there is none."""

    __slots__ = ()

    def __init__(self, value: Value, ok_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(ok_ty, (value,), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "unwrap"


class FailedInst(Instruction):
    """Whether a result is the error rather than the answer."""

    __slots__ = ()

    def __init__(self, value: Value, bool_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(bool_ty, (value,), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "failed"


class CastInst(Instruction):
    """A conversion between representations."""

    __slots__ = ("kind",)

    def __init__(self, kind: CastKind, value: Value, target: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(target, (value,), span)
        self.kind = kind

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return self.kind.value


class CallInst(Instruction):
    """A call, direct or indirect.

    The calling convention travels with the callee rather than with the target,
    because the specification allows conventions to differ between functions of a
    single compilation.
    """

    __slots__ = ("callee",)

    @property
    def has_effects(self) -> bool:
        """A call does whatever the callee does, which the callee says.

        A function that may change something that outlives the call has to be
        made whether or not anyone wants its answer; a function that only works
        out an answer does not, so a call to one that nothing reads is an
        instruction the program need not run.

        Asked of the callee by name rather than by type, since a callee that is
        not a function this module knows -- an indirect call, when there is one
        -- says nothing, and what it does not say has to be assumed.
        """
        attrs = getattr(self.callee, "attrs", None)
        return bool(getattr(attrs, "impure", True))

    def __init__(self, callee: object, args: Sequence[Value], result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, args, span)
        self.callee = callee

    def references(self) -> Sequence[object]:
        """The callee, where the call names one rather than computing it."""
        return (self.callee,)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "call"


class AddressInst(Instruction):
    """The address of a variable, as a value a register can hold.

    A variable is already a pointer and a load or a store may name one
    directly; this is what puts that pointer where arithmetic can reach it, so
    that a place computed from it -- the element after this one, the field
    beside it -- is read and written the same way any other place is.
    """

    __slots__ = ()

    def __init__(self, var: Value, span: Span = INVALID_SPAN) -> None:
        super().__init__(var.ty, (var,), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "address"


class FrameInst(Instruction):
    """Storage of this function's own, which lasts exactly as long as the call.

    An array whose type says how many elements it has is the first thing that
    wants it: the elements have to be somewhere, that somewhere lasts as long as
    the name does, and an arena that never frees would leak one per call.  What
    it answers with is where the storage is.

    Nothing outside the function can be given this address, because nothing in
    the language yet hands an address anywhere: a value of an array type may be
    read and written through, taken apart and sliced, and that is all.
    """

    __slots__ = ("held",)

    def __init__(self, held: Type, result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (), span)
        self.held = held

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "frame"


class AssertInst(Instruction):
    """Stops the program where what it is given is false, saying what was wanted.

    The same thing an addition that does not fit does, and through the same
    path: the message is built whole while compiling and what runs at the moment
    it fails is a write and a trap.  What makes this an instruction of its own
    rather than a flag on something else is that what it checks is not about the
    operation it guards -- an index is checked against a length, and neither is
    part of the read it belongs to.
    """

    __slots__ = ("what",)

    def __init__(self, condition: Value, what: str,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(VOID, (condition,), span)
        self.what = what

    @property
    def has_effects(self) -> bool:
        """Whether the program goes on is what it decides, so it is never
        dropped for having no result."""
        return True

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "assert"


class MemStartInst(Instruction):
    """The state of memory where a function begins.

    Every load takes a memory token and every store produces one, so the chain
    has to start somewhere; this is where.  It is an instruction rather than a
    parameter of the function so that the function's type says nothing about
    memory, which is the caller's business and not part of the signature.
    """

    __slots__ = ()

    def __init__(self, span: Span = INVALID_SPAN) -> None:
        super().__init__(MEM, (), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "mem.start"


class LoadInst(Instruction):
    """Reads memory.  Operands are the memory token and the address.

    Reading has no effect: every place the language can name is the program's
    own, so a read nobody looks at is one nobody can tell happened.  A place
    where reading is itself an action -- a device register -- would have to say
    so on the instruction, and none exists.
    """

    __slots__ = ()

    def reads(self) -> Sequence[Value]:
        """The place the address operand names."""
        return (self.operands[1],)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "load"


class StoreInst(Instruction):
    """Writes memory and produces a new memory token.

    Operands are the token it follows, the address, and the value.  Producing a
    token rather than merely consuming one is what puts a write in the dataflow
    graph: a read that takes the new token is ordered after this write, and one
    that takes the old token provably is not.
    """

    __slots__ = ()

    def __init__(self, token: Value, address: Value, value: Value,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(MEM, (token, address, value), span)

    @property
    def has_effects(self) -> bool:
        """A write is visible after the function that made it has returned."""
        return True

    def writes(self) -> Sequence[Value]:
        """The place the address operand names."""
        return (self.operands[1],)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "store"


class FieldInst(Instruction):
    """Selects a field of a product value by *index*, never by byte offset."""

    __slots__ = ("field",)

    def __init__(self, value: Value, field: int, result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (value,), span)
        self.field = field

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "field"


class SumMakeInst(Instruction):
    """Builds a value of a sum type from a variant index and a payload."""

    __slots__ = ("variant",)

    def __init__(self, variant: int, payload: Value, result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (payload,), span)
        self.variant = variant

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "sum.make"


class SumTagInst(Instruction):
    """Reads the variant index of a sum value."""

    __slots__ = ()

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "sum.tag"


class SumGetInst(Instruction):
    """Reads the payload of a known variant of a sum value."""

    __slots__ = ("variant",)

    def __init__(self, value: Value, variant: int, result_ty: Type,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (value,), span)
        self.variant = variant

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "sum.get"


class BlockTarget:
    """A branch destination together with the arguments it supplies."""

    __slots__ = ("block", "args")

    def __init__(self, block: object, args: Sequence[Value] = ()) -> None:
        self.block = block
        self.args: list[Value] = list(args)


class Terminator(Instruction):
    """The last instruction of a block, and the only one that may transfer control."""

    __slots__ = ()

    @property
    def has_effects(self) -> bool:
        """Where control goes next is the effect; a block without one is broken."""
        return True

    def successors(self) -> Sequence[BlockTarget]:
        """The destinations control may reach from here."""
        return ()


class RetInst(Terminator):
    """Returns from the function, with a value unless the return type is void."""

    __slots__ = ()

    def __init__(self, value: Value | None = None, span: Span = INVALID_SPAN) -> None:
        super().__init__(VOID, () if value is None else (value,), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "ret"


class BrInst(Terminator):
    """An unconditional branch."""

    __slots__ = ("target",)

    def __init__(self, target: BlockTarget, span: Span = INVALID_SPAN) -> None:
        super().__init__(VOID, (), span)
        self.target = target

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "br"

    def successors(self) -> Sequence[BlockTarget]:
        """The destinations control may reach from here."""
        return (self.target,)


class CondBrInst(Terminator):
    """A branch on a boolean condition."""

    __slots__ = ("true_target", "false_target")

    def __init__(self, cond: Value, true_target: BlockTarget, false_target: BlockTarget,
                 span: Span = INVALID_SPAN) -> None:
        super().__init__(VOID, (cond,), span)
        self.true_target = true_target
        self.false_target = false_target

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "condbr"

    def successors(self) -> Sequence[BlockTarget]:
        """The destinations control may reach from here."""
        return (self.true_target, self.false_target)


class SwitchInst(Terminator):
    """A multi-way branch on an integer value."""

    __slots__ = ("cases", "default")

    def __init__(self, value: Value, cases: Sequence[tuple[int, BlockTarget]],
                 default: BlockTarget, span: Span = INVALID_SPAN) -> None:
        super().__init__(VOID, (value,), span)
        self.cases = list(cases)
        self.default = default

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "switch"

    def successors(self) -> Sequence[BlockTarget]:
        """The destinations control may reach from here."""
        return [t for _, t in self.cases] + [self.default]


class UnreachableInst(Terminator):
    """Marks a point control has been proved never to reach."""

    __slots__ = ()

    def __init__(self, span: Span = INVALID_SPAN) -> None:
        super().__init__(VOID, (), span)

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "unreachable"
