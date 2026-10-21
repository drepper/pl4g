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
                       CmpInst, CmpPred, Instruction, LoadInst, SplatInst,
                       StoreInst, Terminator, UnaryInst, UnOp)
from ..ir.layout import DataLayout, stride_of
from ..ir.module import Module
from ..ir.types import IntType, BOOL, Type, TypeContext, U64, VecType
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
    #: Whether it can gather a register of truth values into the bits of an
    #: ordinary one, one bit per lane, which is what `\N{APL FUNCTIONAL SYMBOL QUAD}mask` asks.
    masks: bool = False

    def lanes_at_once(self, element: Type, layout: DataLayout,
                      widths: frozenset[int]) -> int:
        """How many elements of this type fit in one of its registers, where an
        operation it has at *widths* is what is being asked about."""
        if self.bits == 0 or not widths:
            return 0
        if isinstance(element, IntType) and not element.whole:
            # A type narrower than what holds it is not a lane.  The lane
            # operations are the machine's own and what they check is the width
            # they work at, so a run of them would answer for the byte and not
            # for the three bits the program wrote -- and a sum of two values
            # of three bits that came to nine would go unreported.  Such a run
            # comes apart into elements, where the check is the one the type
            # asks for.
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


def works_on_runs(func: Function) -> bool:
    """Whether *func* has anything to do with a run of elements held as one
    value -- asked before they are brought down to what a machine has, after
    which a machine with no registers of them has none left to ask about."""
    return any(_is_a_run(inst) for block in func.blocks for inst in block.insts)


def _runs_of(inst: Instruction) -> list[Value]:
    """The runs this instruction makes and reads, the one it makes first."""
    found: list[Value] = [inst] if isinstance(inst.ty, VecType) else []
    found.extend(operand for operand in inst.operands
                 if isinstance(operand.ty, VecType))
    return found


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
        self._plan(func)
        for block in func.blocks:
            self._rewrite(block)

    def _rewrite(self, block: BasicBlock) -> None:
        """Rewrite one block in place."""
        self._made = []
        self._block = block
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
        """Point an instruction that is staying at what its operands became --
        and a branch at what what it hands the block it goes to became, which is
        read as much as an operand is."""
        for at, operand in enumerate(inst.operands):
            found = self._became.get(id(operand))
            if found is not None:
                inst.operands[at] = found
        if isinstance(inst, Terminator):
            for edge in inst.successors():
                edge.args = [self._became.get(id(one), one) for one in edge.args]

    def _plan(self, func: Function) -> None:
        """Decide how every run in this function is cut up, before anything moves.

        **A run is cut as everything it meets is.**  What a run is read into has
        to be what the operation reads, and what an operation answers has to be
        what the next one reads, so the runs an operation joins -- what it reads
        and what it answers -- are cut alike, and so, by the same rule, are the
        runs joined to those.  Each such family is cut by the operation in it
        that can do the least at once: an operation that has sixteen lanes at a
        time does eight just as well, and one that has none does every lane on
        its own.  For long that was one operation and its operands, everything
        the front end wrote going through room of its own between operations;
        what a mask is made of does not, and `(w = 3u16) \N{LOGICAL OR} (w = 5u16)`
        would otherwise read a comparison answered a lane apiece as though it
        were answered eight at a time.

        **A family with no operation in it is only being moved**: read out of
        one place and written into another, which is what joining two arrays
        asks for.  There is no operation to ask which widths it exists at, and
        none is needed -- a register holds a run of any lane width, so what cuts
        it up is only how wide the register is.  A run that is neither moved nor
        worked on falls to an element at a time, which is always right.

        Over the whole function and not a block at a time, so that a run made
        in one block and read in another is cut the same in both.
        """
        self._cuts = {}
        family: dict[int, int] = {}
        members: dict[int, Value] = {}

        def root(one: Value) -> int:
            key = id(one)
            members.setdefault(key, one)
            while family.setdefault(key, key) != key:
                family[key] = family[family[key]]
                key = family[key]
            return key

        doing: dict[int, int] = {}
        moved: set[int] = set()
        for block in func.blocks:
            for inst in block.insts:
                runs = _runs_of(inst)
                if not runs:
                    continue
                first = root(runs[0])
                for one in runs[1:]:
                    other = root(one)
                    if other == first:
                        continue
                    family[other] = first
                    if other in doing:
                        found = doing.pop(other)
                        doing[first] = min(doing.get(first, found), found)
                    if other in moved:
                        moved.discard(other)
                        moved.add(first)
                at_once = self._at_once(inst)
                if at_once is not None:
                    doing[first] = min(doing.get(first, at_once), at_once)
                elif isinstance(inst, StoreInst):
                    moved.add(first)
        stride: dict[int, int] = {}
        lanes: dict[int, int] = {}
        for one in members.values():
            held = one.ty
            assert isinstance(held, VecType)
            top = root(one)
            stride[top] = max(stride.get(top, 0),
                              stride_of(held.element, self._layout))
            lanes[top] = held.lanes
        cut_of: dict[int, list[int]] = {}
        for top, count in lanes.items():
            if top in doing:
                at_once = doing[top]
            elif top in moved:
                at_once = 0 if self._able.bits == 0 or stride[top] == 0 \
                    else self._able.bits // (stride[top] * 8)
            else:
                at_once = 0
            cut_of[top] = _pieces(count, stride[top], at_once)
        for key, one in members.items():
            self._cuts[key] = cut_of[root(one)]

    def _at_once(self, inst: Instruction) -> int | None:
        """How many lanes at once this operation can be done to here, nought
        where it is done a lane apiece; nothing where it is not an operation.

        As many whole registers' worth as the machine can do this operation to,
        and then one lane apiece.  A tail shorter than a register could be done
        in a register too, by reading part of one -- but a partial read is its
        own instruction on every target and a different one on each, where an
        element at a time is the code that is already there.
        """
        match inst:
            case BinaryInst() | UnaryInst() | CmpInst() \
                    if isinstance(inst.ty, VecType):
                # A comparison answers truth values and reads something else,
                # and what says how many fit in a register is what it reads.
                read = inst.operands[0].ty
                assert isinstance(read, VecType)
                if isinstance(inst, CmpInst) and read.element is BOOL \
                        and inst.pred not in (CmpPred.EQ, CmpPred.NE):
                    # A truth value in a register of them is every bit of its
                    # lane set or every bit clear, which a signed comparison
                    # would order the wrong way round; whether two are equal is
                    # the question worth asking of a run of them.
                    return 0
                return self._able.lanes_at_once(read.element, self._layout,
                                                self._widths(inst))
            case UnaryInst() if inst.op is UnOp.MASK:
                return self._able.lanes_at_once(
                    BOOL, self._layout,
                    frozenset((8,)) if self._able.masks else frozenset())
            case _:
                return None

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
            case UnaryInst() if inst.op is UnOp.MASK:
                self._mask(inst)
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
        cuts = self._cut(inst.operands[2], ty.lanes)
        if all(lanes == 1 for lanes in cuts):
            done = self._store_repeated(inst, ty)
            if done is not None:
                self._became[id(inst)] = done
                return
        at = 0
        for lanes, piece in zip(cuts, self._held(inst.operands[2])):
            written = self._add(StoreInst(
                written,
                self._piece_place(address, ty.element, at, piece.ty, inst.span),
                piece, inst.span))
            at += lanes
        self._became[id(inst)] = written

    def _store_repeated(self, inst: StoreInst, ty: VecType) -> Value | None:
        """Write one number written down into every element eight bytes at a
        time, where nothing holds a run at once: the number repeated across a
        word is the same bytes as the elements one after another.  What does not
        fill a word is written an element at a time.  Nothing where the run is
        not that -- or is too short for it to matter.

        Without this, `1024 ⍴ 0u8` on a machine with no vector registers is a
        thousand stores, and a function holding them is longer than a branch
        across it can reach.
        """
        stored = inst.operands[2]
        element = ty.element
        if not isinstance(stored, SplatInst) \
                or not isinstance(stored.operands[0], IntConst) \
                or not isinstance(element, IntType) \
                or element.bits not in (8, 16, 32) \
                or stride_of(element, self._layout) * 8 != element.bits:
            return None
        per = 64 // element.bits
        whole = ty.lanes // per
        if whole < 2:
            return None
        one = stored.operands[0].value & ((1 << element.bits) - 1)
        pattern = 0
        for lane in range(per):
            pattern |= one << (lane * element.bits)
        written: Value = inst.operands[0]
        address = inst.operands[1]
        for word in range(whole):
            written = self._add(StoreInst(
                written,
                self._piece_place(address, element, word * per, U64, inst.span),
                IntConst(U64, pattern), inst.span))
        for at in range(whole * per, ty.lanes):
            written = self._add(StoreInst(
                written,
                self._piece_place(address, element, at, element, inst.span),
                IntConst(element, one), inst.span))
        return written

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

    def _mask(self, inst: UnaryInst) -> None:
        """The bits of each piece, each moved up to where its first lane is.

        A piece of one lane is a truth value, which is its own bit once it is a
        number; a piece of more is asked with the instruction that gathers a
        register's lanes.  What comes back is ored together.
        """
        found: Value | None = None
        at = 0
        for piece in self._held(inst.operands[0]):
            one: Value = self._add(
                UnaryInst(UnOp.MASK, piece, inst.span, inst.ty)
                if isinstance(piece.ty, VecType)
                else CastInst(CastKind.ZEXT, piece, inst.ty, inst.span))
            if at:
                one = self._add(BinaryInst(BinOp.SHL, one,
                                           IntConst(inst.ty, at), inst.span))
            found = one if found is None else self._add(
                BinaryInst(BinOp.OR, found, one, inst.span))
            at += piece.ty.lanes if isinstance(piece.ty, VecType) else 1
        assert found is not None
        self._became[id(inst)] = found

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
