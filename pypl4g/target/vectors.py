"""Bringing an operation over a whole run of elements down to what a machine has.

The front end asks the question of the whole run: `v + w` over four elements is
one addition of four lanes, however many lanes the machine it is being built for
can actually add at once.  That is deliberate -- the meaning of a program does
not depend on what a processor can do -- and it leaves exactly one thing to do
here, which is to say how many instructions that turns into on *this* machine.

Three answers, and every target gives all three for different runs:

**As it stands.**  The machine has a register that holds the whole run and an
instruction that does the operation to all of it.  Nothing is rewritten.

**In pieces.**  The run is longer than a register, so it is cut into as many
whole registers' worth as fit.  Each piece is the same operation over fewer
lanes, reading and writing its own part of the run -- which is correct because
the elements are laid out one after another with nothing between them, so a
piece of a run is itself a run.

**One element at a time.**  Whatever is left over, and everything on a machine
with no such registers at all or with none for this operation.  This is what the
front end would have written had it asked the question element by element, and
it is the floor under all of it: a target that says it can do nothing still
compiles every program, in as many instructions as there are elements.

The three are one mechanism.  A value of a run is held here as a list of
*pieces*, each of a stated number of lanes; a piece of one lane is an ordinary
value of the element type, which is why the last of the three needs no code of
its own.

Nothing here builds through ``IRBuilder``.  The memory tokens are already in the
instructions being rewritten and say what follows what; a builder would start a
chain of its own beside them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ir.function import BasicBlock, Function
from ..ir.inst import (AnyLaneInst, BinaryInst, BinOp, CastInst, CastKind,
                       CmpInst, Instruction, LoadInst, SplatInst, StoreInst,
                       UnaryInst, UnOp)
from ..ir.layout import DataLayout, stride_of
from ..ir.module import Module
from ..ir.types import BOOL, Type, TypeContext, U64, VecType
from ..ir.value import IntConst, Value
from ..source.location import Span


@dataclass(frozen=True, slots=True, eq=False)
class Vectors:
    """What one target can do to a run of elements at once.

    *bits* is how wide the registers holding one are; zero says there are none,
    which is the answer a target gives until it has any, and the answer RISC-V
    gives for as long as its vector extension is not in the base it builds for.

    *binary* and *unary* say, for each operation it can do to a whole register,
    which lane widths it has it at -- because that differs between them, and not
    always as a range.  A machine may add sixteen bytes at once and have no
    instruction that multiplies them; it may saturate a byte and not a word; it
    may multiply halfwords and words and not bytes.  An operation not named at
    all is one it cannot do, and naming them one by one rather than assuming a
    set is what lets a target take them in whatever order suits it with
    everything it has not taken still compiling.
    """

    bits: int = 0
    binary: dict[BinOp, frozenset[int]] = field(default_factory=dict)
    unary: dict[UnOp, frozenset[int]] = field(default_factory=dict)
    #: The lane widths it can compare at, answering a truth value per lane.
    compares: frozenset[int] = frozenset()

    def lanes_at_once(self, element: Type, layout: DataLayout,
                      widths: frozenset[int]) -> int:
        """How many elements of this type fit in one of its registers, where an
        operation it has at *widths* is what is being asked about."""
        if self.bits == 0 or not widths:
            return 0
        wide = stride_of(element, layout) * 8
        if wide not in widths:
            return 0
        return self.bits // wide


#: Every width a lane can be, which is what most operations have.
EVERY: frozenset[int] = frozenset((8, 16, 32, 64))


#: A target that has nothing, which every program still compiles for.
NONE: Vectors = Vectors()

#: The fewest bytes worth taking as a piece.  Four, because a piece is read and
#: written with one instruction and the narrowest read into one of these
#: registers that every machine has is four bytes; anything shorter is read an
#: element at a time, which is the same number of instructions and no new ones.
SMALLEST: int = 4


def _pieces(lanes: int, stride: int, at_once: int) -> list[int]:
    """How a run of *lanes* elements is cut up, given how many fit at once.

    Each piece is a power of two elements long, so that the bytes it covers are
    a size a machine reads and writes in one instruction: sixteen bytes, or
    eight, or four.  Greedily the largest that fits what is left, which puts the
    whole of a short run in one piece -- a run of four bytes is four bytes read
    at once and not four reads -- and leaves at most three bytes over at the end,
    which go an element apiece.
    """
    found: list[int] = []
    left = lanes
    while left:
        take = 1
        size = at_once
        while size >= 1:
            if size <= left and size * stride >= SMALLEST:
                take = size
                break
            size //= 2
        found.append(take)
        left -= take
    return found


def settle(module: Module, able: Vectors, layout: DataLayout) -> None:
    """Rewrite every run-at-a-time operation into what *able* says it can do."""
    for func in module.functions.values():
        if func.is_declaration:
            continue
        if any(_is_a_run(inst) for block in func.blocks for inst in block.insts):
            _Settler(module.types, able, layout).run(func)


def _is_a_run(inst: Instruction) -> bool:
    """Whether this instruction is about a run of elements held as one value."""
    return isinstance(inst.ty, VecType) or any(
        isinstance(operand.ty, VecType) for operand in inst.operands)


class _Settler:
    """Rewrites one function's run-at-a-time operations."""

    def __init__(self, types: TypeContext, able: Vectors,
                 layout: DataLayout) -> None:
        self._types = types
        self._able = able
        self._layout = layout
        #: What each value of a run has become: one piece per register's worth,
        #: in order, each of as many lanes as its own type says.
        self._pieces: dict[int, list[Value]] = {}
        #: What a value that is not a run has become, where it has become
        #: something: the token the last of a run of writes leaves behind, and
        #: the one truth value a question about every lane comes to.
        self._became: dict[int, Value] = {}
        self._made: list[Instruction] = []
        self._block: BasicBlock | None = None
        #: How each run in the block being rewritten is cut up.
        self._cuts: dict[int, list[int]] = {}

    def run(self, func: Function) -> None:
        """Rewrite the whole function."""
        for block in func.blocks:
            self._rewrite(block)

    def _rewrite(self, block: BasicBlock) -> None:
        """Rewrite one block in place."""
        self._made = []
        self._block = block
        self._plan(block)
        for inst in block.insts:
            if self._one(inst):
                continue
            self._settle_operands(inst)
            self._add(inst)
        block.insts = self._made

    def _add(self, inst: Instruction) -> Instruction:
        """Put an instruction in the block being built."""
        inst.parent = self._block
        self._made.append(inst)
        return inst

    def _settle_operands(self, inst: Instruction) -> None:
        """Point an instruction that is staying at what its operands became."""
        for at, operand in enumerate(inst.operands):
            found = self._became.get(id(operand))
            if found is not None:
                inst.operands[at] = found

    def _plan(self, block: BasicBlock) -> None:
        """Decide how every run in this block is cut up, before anything moves.

        The cut is the operation's to decide and not each value's: what a run is
        read into has to be what the operation reads, so the question is asked of
        the operation and the answer is handed to the values that reach it and to
        the one it comes to.  A run that reaches no operation of its own -- there
        is none today, every one the front end writes being read by exactly one --
        falls to an element at a time, which is always right.
        """
        self._cuts = {}
        for inst in block.insts:
            match inst:
                case BinaryInst() | UnaryInst() | CmpInst() \
                        if isinstance(inst.ty, VecType):
                    self._spread(inst, self._cut_for(inst))
                case _:
                    pass

    def _spread(self, inst: Instruction, cut: list[int]) -> None:
        """Give this cut to an operation, to what reaches it and to nothing else."""
        self._cuts[id(inst)] = cut
        for operand in inst.operands:
            if isinstance(operand.ty, VecType):
                self._cuts.setdefault(id(operand), cut)

    def _cut_for(self, inst: Instruction) -> list[int]:
        """How the run an operation works on is cut up: how many lanes in each
        piece, in order.

        As many whole registers' worth as the machine can do this operation to,
        and then one lane apiece.  A tail shorter than a register could be done
        in a register too, by reading part of one -- but a partial read is its
        own instruction on every target and a different one on each, where an
        element at a time is the code that is already there.
        """
        held = inst.ty
        assert isinstance(held, VecType)
        # A comparison answers truth values and reads something else, and what
        # says how many fit in a register is what it reads.
        read = inst.operands[0].ty
        element = read.element if isinstance(read, VecType) else held.element
        at_once = self._able.lanes_at_once(element, self._layout,
                                           self._widths(inst))
        return _pieces(held.lanes, stride_of(element, self._layout), at_once)

    def _widths(self, inst: Instruction) -> frozenset[int]:
        """Which lane widths this machine has this operation at; none where it
        does not have it at all."""
        match inst:
            case BinaryInst():
                return self._able.binary.get(inst.op, frozenset())
            case UnaryInst():
                return self._able.unary.get(inst.op, frozenset())
            case CmpInst():
                return self._able.compares
            case _:
                return frozenset()

    def _cut(self, value: Value, lanes: int) -> list[int]:
        """How the run this value holds is cut up."""
        return self._cuts.get(id(value), [1] * lanes)

    def _one(self, inst: Instruction) -> bool:
        """Rewrite one instruction, saying whether it was rewritten at all."""
        match inst:
            case SplatInst() if isinstance(inst.ty, VecType):
                self._splat(inst, inst.ty)
            case LoadInst() if isinstance(inst.ty, VecType):
                self._load(inst, inst.ty)
            case StoreInst() if isinstance(inst.operands[2].ty, VecType):
                self._store(inst)
            case BinaryInst() if isinstance(inst.ty, VecType):
                self._binary(inst)
            case UnaryInst() if isinstance(inst.ty, VecType):
                self._unary(inst)
            case CmpInst() if isinstance(inst.ty, VecType):
                self._compare(inst, inst.ty)
            case AnyLaneInst() if isinstance(inst.operands[0].ty, VecType):
                self._any_lane(inst)
            case _:
                return False
        return True

    def _held(self, value: Value) -> list[Value]:
        """The pieces one value of a run came to."""
        found = self._pieces.get(id(value))
        assert found is not None, "a run whose pieces were never made"
        return found

    def _piece_type(self, element: Type, lanes: int) -> Type:
        """What a piece of this many lanes is; one lane is the element itself."""
        return element if lanes == 1 else self._types.vec_type(element, lanes)

    def _splat(self, inst: SplatInst, ty: VecType) -> None:
        """One value in every lane: one piece per register, and the value itself
        wherever a piece holds a single lane."""
        source = inst.operands[0]
        made: list[Value] = []
        for lanes in self._cut(inst, ty.lanes):
            made.append(source if lanes == 1 else self._add(
                SplatInst(source, self._types.vec_type(ty.element, lanes),
                          inst.span)))
        self._pieces[id(inst)] = made

    def _piece_place(self, base: Value, element: Type, at: int, held: Type,
                     span: Span) -> Value:
        """Where the piece that starts at the *at*-th element is."""
        start: Value = self._add(CastInst(
            CastKind.BITCAST, base, self._types.ptr_type(element, mutable=True),
            span))
        if at:
            start = self._add(BinaryInst(
                BinOp.ADD, start,
                IntConst(U64, at * stride_of(element, self._layout)), span))
        return self._add(CastInst(
            CastKind.BITCAST, start, self._types.ptr_type(held, mutable=True),
            span))

    def _load(self, inst: LoadInst, ty: VecType) -> None:
        """Read a run one piece at a time."""
        token, address = inst.operands[0], inst.operands[1]
        made: list[Value] = []
        at = 0
        for lanes in self._cut(inst, ty.lanes):
            held = self._piece_type(ty.element, lanes)
            made.append(self._add(LoadInst(
                held,
                (token, self._piece_place(address, ty.element, at, held, inst.span)),
                inst.span)))
            at += lanes
        self._pieces[id(inst)] = made

    def _store(self, inst: StoreInst) -> None:
        """Write a run one piece at a time; the last write is the new token."""
        ty = inst.operands[2].ty
        assert isinstance(ty, VecType)
        address = inst.operands[1]
        written: Value = inst.operands[0]
        at = 0
        for lanes, piece in zip(self._cut(inst.operands[2], ty.lanes),
                                self._held(inst.operands[2])):
            written = self._add(StoreInst(
                written,
                self._piece_place(address, ty.element, at, piece.ty, inst.span),
                piece, inst.span))
            at += lanes
        self._became[id(inst)] = written

    def _binary(self, inst: BinaryInst) -> None:
        """One operation per piece."""
        self._pieces[id(inst)] = [
            self._add(BinaryInst(inst.op, one, other, inst.span))
            for one, other in zip(self._held(inst.operands[0]),
                                  self._held(inst.operands[1]))]

    def _unary(self, inst: UnaryInst) -> None:
        """One operation per piece."""
        self._pieces[id(inst)] = [
            self._add(UnaryInst(inst.op, one, inst.span))
            for one in self._held(inst.operands[0])]

    def _compare(self, inst: CmpInst, ty: VecType) -> None:
        """One comparison per piece, each answering as many truth values."""
        made: list[Value] = []
        for one, other in zip(self._held(inst.operands[0]),
                              self._held(inst.operands[1])):
            answer = (BOOL if not isinstance(one.ty, VecType)
                      else self._types.vec_type(BOOL, one.ty.lanes))
            made.append(self._add(CmpInst(inst.pred, one, other, answer,
                                          inst.span)))
        self._pieces[id(inst)] = made

    def _any_lane(self, inst: AnyLaneInst) -> None:
        """Whether any lane is true, asked of each piece and then of the answers.

        A piece of one lane answers for itself; a piece of more is asked with the
        instruction that asks it.  What comes back is ored together, which is
        "any of them" written the way a truth value allows.
        """
        found: Value | None = None
        for piece in self._held(inst.operands[0]):
            one = (piece if not isinstance(piece.ty, VecType)
                   else self._add(AnyLaneInst(piece, BOOL, inst.span)))
            found = one if found is None else self._add(
                BinaryInst(BinOp.OR, found, one, inst.span))
        assert found is not None
        self._became[id(inst)] = found
