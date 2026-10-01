"""Running a macro while the compiler runs.

A macro written as a function over the program's text is an ordinary function that
runs at expansion.  What runs it is here, and what it runs is the **intermediate
representation** the ordinary checker lowered its body to -- not the syntax tree.

That is the whole design decision.  A macro's body could be walked as a tree, and
that would be a second statement of what an expression means: what `a + b` does
would be written once in the checker and again here, and the two would drift.
Checking and lowering the macros with the compiler's own front end and then running
*that* keeps one set of rules, in exchange for a machine that has to know what a
frame slot and a store are.  It is the same bargain a requirement makes -- checked
by asking the checker rather than by a walk of its own.

What the machine is:

- **Values** are Python integers for everything a register holds -- a number, a
  truth value, a code point, a piece of the program, an address -- and tuples for
  what is several values at once: a record, a tuple, a result, an alternative.
- **Memory** is one `bytearray` that grows, and an address is an offset into it.
  A frame slot is a bump; nothing is ever given back, a macro running for as long as
  one expansion takes.
- **A piece of the program** is a handle: an index into a table of syntax trees the
  expander holds.  Nothing in the machine looks inside one; the builtins do.

What it refuses rather than guesses: anything outside the subset below.  A macro
reaching one of those is told which instruction it was, which is a message about the
compiler and says so.
"""

from __future__ import annotations

from typing import Callable, Final, Sequence

from ..ir.function import BasicBlock, Function
from ..ir.inst import (AddressInst, AssertInst, BinaryInst, BinOp, BrInst,
                       CallInst, CastInst,
                       CastKind, CmpInst, CmpPred, CondBrInst, ErrorInst,
                       ExtractInst, FailedInst, FieldInst, FrameInst, Instruction,
                       LoadInst, MemStartInst, RetInst, StoreInst, SumGetInst,
                       SumMakeInst, SumTagInst, SwitchInst, TupleInst,
                       UnaryInst, UnOp, UnreachableInst, WrapInst)
from ..ir.layout import DataLayout, align_of, offsets_of, size_of
from ..ir.types import (BOOL, BoolType, CharType, EnumType, FloatType, IntType,
                        ProductType, PtrType, SyntaxType, Type, VOID)
from ..ir.module import GlobalVar
from ..ir.value import (ArrayConst, BlockParam, BoolConst, CharConst, Const,
                        EnumConst, IntConst, RecordConst, UndefConst, Value)

#: How many instructions one expansion may run before it is stopped.  A macro that
#: loops for ever would otherwise hang the compiler, and a limit is the only answer:
#: whether a program ends is not a question a compiler can ask.
STEPS: Final[int] = 1_000_000

#: The layout the machine uses.  A macro runs in the compiler and not on the target,
#: so this is the compiler's own word size and not the one being compiled for --
#: which matters only where a macro asks how big something is, and nothing does yet.
LAYOUT: Final[DataLayout] = DataLayout(8)


class Refused(Exception):
    """Raised where a macro's body reaches something the machine will not run."""

    def __init__(self, detail: str, inst: Instruction | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.inst = inst


class Stopped(Exception):
    """Raised where a check in a macro's body did not hold, or it ran too long."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


#: What each binary operation does to two numbers already in range.  The signed and
#: the unsigned divisions are separate entries because they are separate questions,
#: and the answer is brought back into range by the caller, which knows the width.
_ARITHMETIC: Final[dict[BinOp, Callable[[int, int], int]]] = {
    BinOp.ADD: lambda a, b: a + b,
    BinOp.SUB: lambda a, b: a - b,
    BinOp.MUL: lambda a, b: a * b,
    BinOp.AND: lambda a, b: a & b,
    BinOp.OR: lambda a, b: a | b,
    BinOp.XOR: lambda a, b: a ^ b,
    BinOp.SHL: lambda a, b: a << b,
    BinOp.ASHR: lambda a, b: a >> b,
    BinOp.LSHR: lambda a, b: a >> b,
    BinOp.SMAX: max,
    BinOp.UMAX: max,
    BinOp.SMIN: min,
    BinOp.UMIN: min,
    # The wrapping and the saturating forms compute the same thing here, what to do
    # about a value that does not fit being the business of the arm below that knows
    # how wide the type is.
    BinOp.WRAP_ADD: lambda a, b: a + b,
    BinOp.WRAP_SUB: lambda a, b: a - b,
    BinOp.WRAP_MUL: lambda a, b: a * b,
    BinOp.WRAP_SHL: lambda a, b: a << b,
    BinOp.WRAP_ASHR: lambda a, b: a >> b,
    BinOp.WRAP_LSHR: lambda a, b: a >> b,
    BinOp.SAT_ADD: lambda a, b: a + b,
    BinOp.SAT_SUB: lambda a, b: a - b,
    BinOp.SAT_MUL: lambda a, b: a * b,
}

#: What each comparison asks of two numbers.  They arrive already signed or unsigned
#: according to their type, so one entry per predicate says the whole of it.
_COMPARISONS: Final[dict[CmpPred, Callable[[int, int], bool]]] = {
    CmpPred.EQ: lambda a, b: a == b,
    CmpPred.NE: lambda a, b: a != b,
    CmpPred.SLT: lambda a, b: a < b,
    CmpPred.SLE: lambda a, b: a <= b,
    CmpPred.SGT: lambda a, b: a > b,
    CmpPred.SGE: lambda a, b: a >= b,
    CmpPred.ULT: lambda a, b: a < b,
    CmpPred.ULE: lambda a, b: a <= b,
    CmpPred.UGT: lambda a, b: a > b,
    CmpPred.UGE: lambda a, b: a >= b,
}


class Machine:
    """One run of the compiler's own macros.

    The memory and the step count are the machine's and last as long as it does, so
    a macro that allocates in a loop is stopped by the step count rather than by
    running out of room.
    """

    def __init__(self, builtins: dict[str, Callable[..., object]]) -> None:
        #: What a call to a function with no body means.  The questions about a
        #: piece of the program are these, and the expander puts them here: the
        #: machine knows that a handle is a number and nothing else about one.
        self._builtins = builtins
        self._memory = bytearray(16)
        #: Where each variable the program wrote has been put, so that a second
        #: ask answers the same place: a variable is one place and a macro may
        #: read one twice.
        self._placed: dict[str, int] = {}
        self._steps = 0

    def knows(self, builtins: dict[str, Callable[..., object]]) -> None:
        """Say what the functions with no body do.

        Apart from the constructor so that one of them may be the machine's own
        allocator, which cannot be written down before the machine exists.
        """
        self._builtins = builtins

    # -- running ---------------------------------------------------------------

    def call(self, func: Function, args: Sequence[object]) -> object:
        """Run *func* on *args* and answer what it returned."""
        block = func.entry
        if block is None:
            found = self._builtins.get(func.name)
            if found is None:
                raise Refused("".join(("a call to '", func.name,
                                       "', which has no body")))
            return found(*args)
        values: dict[int, object] = {}
        incoming: Sequence[object] = args
        while True:
            for param, given in zip(block.params, incoming):
                values[id(param)] = given
            found = self._block(block, values)
            if isinstance(found, _Returned):
                return found.value
            block, incoming = found

    def _block(self, block: BasicBlock,
               values: dict[int, object]) -> _Returned | tuple[BasicBlock,
                                                               list[object]]:
        """Run one block, answering what it returned or where it goes next."""
        for inst in block.insts:
            self._steps += 1
            if self._steps > STEPS:
                raise Stopped("it ran for too long")
            if isinstance(inst, RetInst):
                return _Returned(self._operand(inst.operands[0], values)
                                 if inst.operands else None)
            if isinstance(inst, BrInst):
                return self._goes(inst.target, values)
            if isinstance(inst, CondBrInst):
                taken = inst.true_target \
                    if self._operand(inst.operands[0], values) \
                    else inst.false_target
                return self._goes(taken, values)
            if isinstance(inst, SwitchInst):
                asked = self._operand(inst.operands[0], values)
                for number, target in inst.cases:
                    if asked == number:
                        return self._goes(target, values)
                return self._goes(inst.default, values)
            if isinstance(inst, UnreachableInst):
                raise Stopped("it reached somewhere it said it would not")
            found = self._one(inst, values)
            if found is not _NOTHING:
                values[id(inst)] = found
        raise Refused("a block with no end, which the verifier should have caught")

    def _goes(self, target: object,
              values: dict[int, object]) -> tuple[BasicBlock, list[object]]:
        """Where a branch goes, and what it hands the block it goes to."""
        block = getattr(target, "block")
        assert isinstance(block, BasicBlock)
        return block, [self._operand(one, values) for one in getattr(target, "args")]

    # -- one instruction -------------------------------------------------------

    def _one(self, inst: Instruction, values: dict[int, object]) -> object:
        """What one instruction computes, or nothing where it computes nothing."""
        match inst:
            case MemStartInst():
                # Memory is the machine's and needs no token to order it: the
                # instructions are run in the order they are written.
                return 0
            case BinaryInst():
                return self._binary(inst, values)
            case UnaryInst():
                return self._unary(inst, values)
            case CmpInst():
                left = self._number(inst.operands[0], values)
                right = self._number(inst.operands[1], values)
                asked = _COMPARISONS.get(inst.pred)
                if asked is None:
                    raise Refused("".join(("the comparison ", inst.pred.value)),
                                  inst)
                return asked(left, right)
            case CastInst():
                return self._cast(inst, values)
            case TupleInst():
                return tuple(self._operand(one, values) for one in inst.operands)
            case ExtractInst():
                held = self._operand(inst.operands[0], values)
                assert isinstance(held, tuple)
                return held[inst.index]
            case WrapInst():
                return tuple(self._operand(one, values) for one in inst.operands)
            case FailedInst():
                held = self._operand(inst.operands[0], values)
                assert isinstance(held, tuple)
                return held[1]
            case ErrorInst():
                held = self._operand(inst.operands[0], values)
                assert isinstance(held, tuple)
                return held[2]
            case SumMakeInst():
                return tuple(self._operand(one, values) for one in inst.operands)
            case SumTagInst():
                held = self._operand(inst.operands[0], values)
                assert isinstance(held, tuple)
                return held[0]
            case SumGetInst():
                held = self._operand(inst.operands[0], values)
                assert isinstance(held, tuple)
                return held[1]
            case FrameInst():
                return self._room(inst.held)
            case AddressInst():
                return self._at(inst.operands[0])
            case FieldInst():
                base = self._number(inst.operands[0], values)
                return base + self._offset(inst)
            case LoadInst():
                where = self._number(inst.operands[1], values)
                return self._read(where, inst.ty)
            case StoreInst():
                where = self._number(inst.operands[1], values)
                self._write(where, inst.operands[2].ty,
                            self._operand(inst.operands[2], values))
                # A store answers the memory after it, which a later load or a
                # branch carries.  Memory is the machine's and needs no token to
                # order it -- the instructions run in the order they are written --
                # but the token is a value and something reads it, so it has to be
                # one.
                return 0
            case AssertInst():
                if not self._operand(inst.operands[0], values):
                    raise Stopped(inst.what)
                return _NOTHING
            case CallInst():
                callee = inst.callee
                if not isinstance(callee, Function):
                    raise Refused("a call through a value", inst)
                # A direct call keeps its callee beside the instruction and its
                # operands are the arguments; one through a value keeps the callee
                # as the first operand, so that everything asking what an
                # instruction uses finds it.
                given = [self._operand(one, values) for one in inst.operands]
                wanted = len(callee.ty.params)
                if len(given) > wanted:
                    # A call may carry more operands than the callee takes: what a
                    # record answered through storage is handed is one of them, and a
                    # bodyless function the machine implements takes only what its
                    # signature says.
                    given = given[:wanted]
                return self.call(callee, given)
        raise Refused("".join(("the instruction '", inst.opcode, "'")), inst)

    def _unwrap(self, inst: Instruction) -> object:  # pragma: no cover - unused
        """Kept out of the match above so that it stays a list of shapes."""
        raise Refused(inst.opcode, inst)

    def _binary(self, inst: BinaryInst, values: dict[int, object]) -> object:
        """What an arithmetic or bitwise operation answers."""
        left = self._number(inst.operands[0], values)
        right = self._number(inst.operands[1], values)
        ty = inst.ty
        if ty is BOOL:
            found = _ARITHMETIC.get(inst.op)
            if found is None:
                raise Refused("".join(("the operation ", inst.op.value)), inst)
            return bool(found(int(left), int(right)))
        if isinstance(ty, PtrType):
            # An address is a whole number here, as it is to the instruction
            # selectors: what a place computed from another place means is the
            # arithmetic and nothing about what is at either.
            found = _ARITHMETIC.get(inst.op)
            if found is None:
                raise Refused("".join(("the operation ", inst.op.value)), inst)
            return int(found(int(left), int(right))) & ((1 << 64) - 1)
        if not isinstance(ty, IntType):
            raise Refused("".join(("arithmetic on ", ty.render())), inst)
        if inst.op in (BinOp.SDIV, BinOp.UDIV, BinOp.SREM, BinOp.UREM):
            if right == 0:
                raise Stopped("a division with no answer")
            whole = abs(left) // abs(right)
            if (left < 0) != (right < 0):
                whole = -whole
            if inst.op in (BinOp.SDIV, BinOp.UDIV):
                return _in_range(whole, ty)
            return _in_range(left - whole * right, ty)
        if inst.op in (BinOp.ASHR, BinOp.LSHR):
            return _in_range(left >> right, ty)
        if inst.op in (BinOp.ROTL, BinOp.ROTR):
            bits = ty.held
            turn = right % bits
            if inst.op is BinOp.ROTR:
                turn = (bits - turn) % bits
            raw = _as_written(left, ty)
            return _in_range(((raw << turn) | (raw >> (bits - turn)))
                             & ((1 << bits) - 1), ty)
        found = _ARITHMETIC.get(inst.op)
        if found is None:
            raise Refused("".join(("the operation ", inst.op.value)), inst)
        answer = found(left, right)
        if not ty.holds(answer):
            # The same thing the generated program does: what will not fit is not
            # wrapped quietly.  A macro is a program and stops the way one does.
            raise Stopped("an answer that will not fit its type")
        return answer

    def _unary(self, inst: UnaryInst, values: dict[int, object]) -> object:
        """What an operation on one number answers."""
        held = self._number(inst.operands[0], values)
        ty = inst.operands[0].ty
        assert isinstance(ty, IntType)
        raw = _as_written(held, ty)
        if inst.op is UnOp.COUNT_ONES:
            return bin(raw).count("1")
        if inst.op is UnOp.COUNT_LEADING:
            return ty.held - raw.bit_length()
        raise Refused("".join(("the operation ", inst.op.value)), inst)

    def _cast(self, inst: CastInst, values: dict[int, object]) -> object:
        """What a widening, a narrowing or a reinterpretation answers."""
        held = self._operand(inst.operands[0], values)
        source = inst.operands[0].ty
        if inst.kind is CastKind.BITCAST:
            return held
        if not isinstance(held, int):
            raise Refused("".join(("a cast of ", source.render())), inst)
        if inst.kind is CastKind.TRUNC:
            assert isinstance(inst.ty, (IntType, CharType))
            target = inst.ty if isinstance(inst.ty, IntType) else inst.ty.holder
            return _in_range(_as_written(held, source) & ((1 << target.held) - 1),
                             target)
        if inst.kind in (CastKind.ZEXT, CastKind.SEXT):
            return held if inst.kind is CastKind.SEXT else _as_written(held, source)
        raise Refused("".join(("the cast ", inst.kind.value)), inst)

    def _offset(self, inst: Instruction) -> int:
        """Where a field lies in the record it belongs to.

        The layout answers it, which is the same layout the back end uses: a
        machine that worked offsets out for itself would be a second answer to
        where a field is.
        """
        held = inst.operands[0].ty
        if isinstance(held, PtrType):
            held = held.pointee
        if not isinstance(held, ProductType):
            raise Refused("".join(("a field of ", held.render())), inst)
        return offsets_of(held, LAYOUT)[inst.index]

    # -- memory ----------------------------------------------------------------

    def _at(self, var: Value) -> int:
        """Where a variable lives, putting what it starts out holding there.

        A variable the program wrote is bytes in the image, and a macro reading one
        -- the bytes of a string literal, which is what a template is -- has to find
        those bytes.  So the first ask makes room and writes the initializer in;
        every later one answers the same address, since a variable is one place.
        """
        if not isinstance(var, GlobalVar):
            raise Refused("".join(("the address of ", var.ty.render())))
        found = self._placed.get(var.name)
        if found is not None:
            return found
        at = self._room(var.value_type)
        self._placed[var.name] = at
        if var.initializer is not None:
            self._put(at, var.initializer)
        return at

    def _put(self, at: int, held: Const) -> None:
        """Write a constant into memory, walking what it is made of."""
        match held:
            case ArrayConst():
                stride = max(1, size_of(held.ty.element, LAYOUT))
                for which, one in enumerate(held.elements):
                    self._put(at + which * stride, one)
            case RecordConst():
                for which, one in enumerate(held.fields):
                    self._put(at + offsets_of(held.ty, LAYOUT)[which], one)
            case UndefConst():
                # Nothing was written, so nought is what is there -- which is
                # what the image does for a variable with no initializer.
                pass
            case _:
                self._write(at, held.ty, self._operand(held, {}))

    def text(self, held: str) -> tuple[int, int, int]:
        """Text as a value of `str`: where the bytes are, how many, and no allocator.

        The bytes go into the machine's memory, which is where everything a macro
        builds lives.  What comes back is the pair a `str` is, so the ordinary code
        that joins two or walks one runs over it with nothing said about where it
        came from.
        """
        raw = held.encode("utf-8")
        at = self.allocate(None, max(1, len(raw)))
        self._memory[at:at + len(raw)] = raw
        return (at, len(raw), 0)

    def read_text(self, held: tuple[int, ...]) -> str:
        """And the way back: what a value of `str` says."""
        at, length = int(held[0]), int(held[1])
        return bytes(self._memory[at:at + length]).decode("utf-8", "replace")

    def allocate(self, _arena: object, size: object) -> int:
        """Room out of the machine's own memory, for a macro that allocates.

        The one callee with no body that is not a question about the program: the
        allocator is per-target assembly and there is nothing to run, so the machine
        is the allocator while a macro runs.  Nothing is given back -- a macro runs
        for one expansion, and the step count is what stops one that allocates
        without end.
        """
        want = max(1, int(size))  # type: ignore[arg-type]
        at = len(self._memory)
        at += (-at) % 16
        self._memory.extend(bytes(at + want - len(self._memory)))
        return at

    def _room(self, held: Type) -> int:
        """An address with room for a value of *held* behind it."""
        want = max(1, size_of(held, LAYOUT))
        boundary = max(1, align_of(held, LAYOUT))
        at = len(self._memory)
        at += (-at) % boundary
        self._memory.extend(bytes(at + want - len(self._memory)))
        return at

    def _read(self, where: int, ty: Type) -> object:
        """What is in memory at *where*, read as a value of *ty*."""
        width = max(1, size_of(ty, LAYOUT))
        raw = int.from_bytes(self._memory[where:where + width], "little")
        if isinstance(ty, IntType):
            return _in_range(raw, ty)
        return raw

    def _write(self, where: int, ty: Type, held: object) -> None:
        """Put *held* into memory at *where* as a value of *ty*."""
        if isinstance(held, tuple):
            raise Refused("".join(("a store of ", ty.render(),
                                   ", which is several values at once")))
        width = max(1, size_of(ty, LAYOUT))
        number = int(held) if not isinstance(held, bool) else int(held)
        if isinstance(ty, IntType):
            number = _as_written(number, ty)
        if where + width > len(self._memory):
            self._memory.extend(bytes(where + width - len(self._memory)))
        self._memory[where:where + width] = (number & ((1 << (8 * width)) - 1)
                                             ).to_bytes(width, "little")

    # -- operands --------------------------------------------------------------

    def _operand(self, value: Value, values: dict[int, object]) -> object:
        """What an operand is, whether a constant or something computed."""
        match value:
            case IntConst():
                return value.value
            case BoolConst():
                return value.value
            case CharConst():
                return value.value
            case EnumConst():
                return value.value
            case UndefConst():
                # Nothing read it in a program that compiled; a macro that does
                # read one is asking what was never written, and nought is the
                # answer that cannot be mistaken for a computation.
                return 0
        found = values.get(id(value))
        if found is None and not isinstance(value, BlockParam):
            raise Refused("".join(("a value of ", value.ty.render(),
                                   " that nothing computed")))
        return found

    def _number(self, value: Value, values: dict[int, object]) -> int:
        """An operand read as a number, signed where its type is."""
        found = self._operand(value, values)
        if isinstance(found, bool):
            return int(found)
        if not isinstance(found, int):
            raise Refused("".join(("a number wanted and ", value.ty.render(),
                                   " given")))
        return found


class _Returned:
    """What a block answers where it returned rather than branched."""

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value


class _Nothing:
    """What an instruction that computes nothing answers."""

    __slots__ = ()


_NOTHING: Final[_Nothing] = _Nothing()


def _in_range(number: int, ty: Type) -> int:
    """*number* as a value of *ty*, signed where the type is."""
    if not isinstance(ty, IntType):
        return number
    bits = ty.held
    raw = number & ((1 << bits) - 1)
    if ty.signed and raw >= (1 << (bits - 1)):
        return raw - (1 << bits)
    return raw


def _as_written(number: int, ty: Type) -> int:
    """*number* as the bits that hold it, which is what a shift reads."""
    if not isinstance(ty, IntType):
        return number
    return number & ((1 << ty.held) - 1)


def what_it_holds(ty: Type) -> str:
    """How a value of *ty* is held in the machine, for a message about one."""
    if isinstance(ty, (IntType, BoolType, CharType, EnumType, PtrType,
                       SyntaxType)):
        return "a number"
    if isinstance(ty, FloatType):
        return "a floating-point number, which the machine does not hold"
    return "several values at once"
