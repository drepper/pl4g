"""IR instructions.

The hierarchy describes the *shapes* an instruction can have; the individual
operations are enumerated inside a shape.  That keeps the number of classes
bounded as operators multiply while still letting an exhaustive match over the
shapes be checked statically.
"""

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
    SAT_ADD = "sat.add"
    SAT_SUB = "sat.sub"
    SAT_MUL = "sat.mul"


class UnOp(Enum):
    """The unary operations."""

    NEG = "neg"
    NOT = "not"


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

    def __init__(self, op: BinOp, lhs: Value, rhs: Value, span: Span = INVALID_SPAN) -> None:
        super().__init__(lhs.ty, (lhs, rhs), span)
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
        """A call does whatever the callee does, which is not known here.

        When purity is inferred or declared, a call to a function that has no
        effects will be able to say so; until then every call is kept.
        """
        return True

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


class AllocaInst(Instruction):
    """Reserves storage whose address is taken."""

    __slots__ = ("allocated",)

    def __init__(self, allocated: Type, result_ty: Type, span: Span = INVALID_SPAN) -> None:
        super().__init__(result_ty, (), span)
        self.allocated = allocated

    @property
    def opcode(self) -> str:
        """The mnemonic used in the textual form."""
        return "alloca"


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
