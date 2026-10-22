"""What a branch already settled, and what a value can be, used where it is
known to hold.

A block that a conditional branch is the only way into knows which way the
branch went: in the body of `foreach i … 0…#v`, `i < #v` holds.  And a value
worked out of others is bounded by what they are: a byte less 48 and masked is
below 256, a count of leading noughts is 64 at most, a number below `n` plus one
is `n` at most.  The checker writes checks that ask such questions again, and
each one that the program can never fail is dropped or made plain:

- **an index's check.**  `v⟦i⟧` checks `i < #v` once more and stops the program
  where it does not hold.  Where what is known says it holds, the check goes.
- **an operation's check.**  `at + 1` stops the program where the sum does not
  fit; where `at < n` holds, `at` is below the largest value of its type and the
  sum fits, so the plain add is enough.  The same for a difference whose right
  side is known to be the smaller, a product of two bounded values and a shift
  by a distance known to be below the width -- and for a range's saturating
  step, which cannot reach an end it is below.
- **a comparison.**  One whose answer is known is that answer, which is what
  takes the test out of a narrowing of a value known to fit: `⎕narrow` asks
  whether the value is above what the narrower type holds.

**What holds where.**  A fact is a comparison and which way it went, learned on
the edge into a block that edge is the only way into -- and from a check, which
the program got past only where it held.  It holds in every block that block
dominates, the values compared being SSA values that do not change.

**What a value can be** is a range, the smallest and the largest, worked out
over the SSA form from what each instruction does to its operands' ranges, each
operand's narrowed by the facts that hold where the instruction is.  A block's
parameter is every value its predecessors hand it, each narrowed by what holds
on that edge, so the counter of `while i < 24` is below 24 in the body whatever
it is in the head.  A loop goes round until nothing changes; a bound that is
still moving after a few turns goes to the end of its type, which ends it.

**Facts about two values** answer what ranges cannot: `at < n` with `n` a
length says nothing about `at`'s range that `#v` does not, but it says that
`at + 1 ≤ n` and that `n - at` is at least one.  So a comparison of a value plus
a constant with another plus a constant is answered from what is known of the
two values, wherever neither sum can wrap.

**One value under several names.**  An index is `u64 ¤idx`, a length `u64`, a
count `u64 ¤size`; the checker converts between them with casts that change the
unit and not one bit, and a comparison of `i` with `#v` and one of `zext(i)`
with `#v` are the same comparison.  So the operands are compared with every such
cast looked through, and a range is about the bits: read signed or unsigned as
the comparison asks.

**Every answer is the same whichever order the facts were learned in**: a range
narrowed by facts is narrowed by the tightest of them, and a value known to be
unequal to an end moves past it until it can move no further.  The C++ port
keeps its facts in another order and has to come to the same image.
"""

from __future__ import annotations

from typing import Callable, Final

from ...ir.function import BasicBlock, Function
from ...ir.inst import (AssertInst, BinaryInst, BinOp, CastInst, CastKind,
                        CmpInst, CmpPred, CondBrInst, ExtractInst, Instruction,
                        SelectInst, UnaryInst, UnOp)
from ...ir.module import Module
from ...ir.types import BoolType, IntType
from ...ir.value import BlockParam, BoolConst, IntConst, Value
from ..flow import dominators, predecessors, replace_uses

#: What a comparison asks when it went the other way.
_NEGATED: Final = {
    CmpPred.EQ: CmpPred.NE, CmpPred.NE: CmpPred.EQ,
    CmpPred.ULT: CmpPred.UGE, CmpPred.UGE: CmpPred.ULT,
    CmpPred.ULE: CmpPred.UGT, CmpPred.UGT: CmpPred.ULE,
    CmpPred.SLT: CmpPred.SGE, CmpPred.SGE: CmpPred.SLT,
    CmpPred.SLE: CmpPred.SGT, CmpPred.SGT: CmpPred.SLE,
}

#: And what it asks with its two sides swapped.
_SWAPPED: Final = {
    CmpPred.EQ: CmpPred.EQ, CmpPred.NE: CmpPred.NE,
    CmpPred.ULT: CmpPred.UGT, CmpPred.UGT: CmpPred.ULT,
    CmpPred.ULE: CmpPred.UGE, CmpPred.UGE: CmpPred.ULE,
    CmpPred.SLT: CmpPred.SGT, CmpPred.SGT: CmpPred.SLT,
    CmpPred.SLE: CmpPred.SGE, CmpPred.SGE: CmpPred.SLE,
}

#: What else holds where one holds: a strict order is also the order with
#: equality allowed, and also "not equal".
_IMPLIED: Final = {
    CmpPred.ULT: (CmpPred.ULE, CmpPred.NE), CmpPred.UGT: (CmpPred.UGE, CmpPred.NE),
    CmpPred.SLT: (CmpPred.SLE, CmpPred.NE), CmpPred.SGT: (CmpPred.SGE, CmpPred.NE),
    CmpPred.EQ: (CmpPred.ULE, CmpPred.UGE, CmpPred.SLE, CmpPred.SGE),
}

#: The comparisons that read their operands signed.
_SIGNED: Final = frozenset((CmpPred.SLT, CmpPred.SLE, CmpPred.SGT, CmpPred.SGE))

#: The orderings as "less than" and "at most", the others being these with
#: their sides swapped: what a comparison asks of `a - b`.
_LESS: Final = {CmpPred.ULT: -1, CmpPred.ULE: 0, CmpPred.SLT: -1, CmpPred.SLE: 0}
_MORE: Final = {CmpPred.UGT: -1, CmpPred.UGE: 0, CmpPred.SGT: -1, CmpPred.SGE: 0}

#: How many times a block's parameter may widen before a bound still moving is
#: taken to the end of its type.  A counter stepping towards a bound its loop
#: tests is bounded by the test in the body and not by its range in the head,
#: so nothing is lost by stopping early, and a loop of a million turns is not
#: walked a million times.
_WIDENINGS: Final = 1

#: And how many times a function is walked at most, which the widening makes
#: more than enough: one that is not settled by then is left as it was.
_ROUNDS: Final = 40

#: The checked operations and the plain one each becomes where it cannot fail.
#: A saturating one that cannot reach an end is the plain one too, and a shift
#: by less than the width is the shift that takes its distance modulo the width.
_PLAIN: Final = {
    BinOp.ADD: BinOp.WRAP_ADD, BinOp.SUB: BinOp.WRAP_SUB, BinOp.MUL: BinOp.WRAP_MUL,
    BinOp.SAT_ADD: BinOp.WRAP_ADD, BinOp.SAT_SUB: BinOp.WRAP_SUB,
    BinOp.SAT_MUL: BinOp.WRAP_MUL,
    BinOp.SHL: BinOp.WRAP_SHL, BinOp.LSHR: BinOp.WRAP_LSHR,
    BinOp.ASHR: BinOp.WRAP_ASHR, BinOp.ROTL: BinOp.WRAP_ROTL,
    BinOp.ROTR: BinOp.WRAP_ROTR,
}

#: The shifts, checked and wrapping, which take their distance as a number of
#: bits and not as a number of the value's kind.
_SHIFTS: Final = frozenset((
    BinOp.SHL, BinOp.LSHR, BinOp.ASHR, BinOp.ROTL, BinOp.ROTR,
    BinOp.WRAP_SHL, BinOp.WRAP_LSHR, BinOp.WRAP_ASHR, BinOp.WRAP_ROTL,
    BinOp.WRAP_ROTR))

#: The operations that add a constant, which a value may be a constant away
#: from another by.
_STEPS: Final = frozenset((BinOp.ADD, BinOp.SUB, BinOp.WRAP_ADD, BinOp.WRAP_SUB))

_Range = tuple[int, int]
_Fact = tuple[CmpPred, int, int]
#: What says a value is another and a constant added to it.
_Offset = Callable[[Value, bool], tuple[Value, int]]


def _plain(value: Value) -> Value:
    """*value* with every cast that changes only its unit looked through."""
    while isinstance(value, CastInst) \
            and value.kind in (CastKind.BITCAST, CastKind.ZEXT, CastKind.SEXT):
        inner = value.operands[0]
        if not (isinstance(value.ty, IntType) and isinstance(inner.ty, IntType)
                and value.ty.bits == inner.ty.bits):
            break
        value = inner
    return value


def _bounds(bits: int, signed: bool) -> _Range:
    """Every value *bits* bits hold, read signed or not."""
    if signed:
        return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return (0, (1 << bits) - 1)


def _reread(found: _Range, bits: int, signed: bool, wanted: bool) -> _Range:
    """*found*, the values of some bits read signed where *signed* says so, as
    they read the way *wanted* says.

    Where every value in the range has its top bit alike, the range is the same
    range moved; where some have it set and some clear, reading them the other way
    splits the range in two, and what is answered is every value there is."""
    if signed == wanted:
        return found
    lo, hi = found
    whole = 1 << bits
    if signed:
        if lo >= 0:
            return found
        if hi < 0:
            return (lo + whole, hi + whole)
        return _bounds(bits, False)
    half = 1 << (bits - 1)
    if hi < half:
        return found
    if lo >= half:
        return (lo - whole, hi - whole)
    return _bounds(bits, True)


def _facts_of(cond: Value, holds: bool) -> list[tuple[CmpPred, Value, Value]]:
    """What is known where *cond* came out as *holds*.

    `a ∧ b` that held is `a` that held and `b` that held, and `a ∨ b` that did
    not is neither; `¬a` is `a` the other way.  Those are the truth values'
    bitwise instructions, both sides worked out."""
    if isinstance(cond, BinaryInst) and isinstance(cond.ty, BoolType):
        left, right = cond.operands
        if cond.op is BinOp.AND and holds or cond.op is BinOp.OR and not holds:
            return [*_facts_of(left, holds), *_facts_of(right, holds)]
        if cond.op is BinOp.XOR and isinstance(right, BoolConst):
            return _facts_of(left, holds is not right.value)
        return []
    if not isinstance(cond, CmpInst) or cond.pred not in _NEGATED:
        return []
    pred = cond.pred if holds else _NEGATED[cond.pred]
    left, right = (_plain(one) for one in cond.operands)
    found: list[tuple[CmpPred, Value, Value]] = []
    for one in (pred, *_IMPLIED.get(pred, ())):
        found.append((one, left, right))
        found.append((_SWAPPED[one], right, left))
    return found


class _Known:
    """The facts that hold at one point of a function.

    As a set, to be asked whether one holds; the values each is known to
    differ from; and, for two values a constant away from what was compared,
    how far apart they are known to be at the least -- `at + 1 < n` says that
    `at - n` is at most minus two -- by the pair, with the tightest kept, and
    by each of the two, to narrow its range."""

    __slots__ = ("facts", "unequal", "bounds", "upper", "lower")

    def __init__(self, other: _Known | None = None) -> None:
        self.facts: set[_Fact] = set(other.facts) if other is not None else set()
        self.unequal: dict[int, tuple[Value, ...]] = \
            dict(other.unequal) if other is not None else {}
        #: `x - y` at most the number, as the truth value says it is read.
        self.bounds: dict[tuple[int, int, bool], tuple[int, Value, Value]] = \
            dict(other.bounds) if other is not None else {}
        #: For each value, what it is known to be at most a constant above, and
        #: what at least a constant below.
        self.upper: dict[int, tuple[tuple[Value, bool], ...]] = \
            dict(other.upper) if other is not None else {}
        self.lower: dict[int, tuple[tuple[Value, bool], ...]] = \
            dict(other.lower) if other is not None else {}

    def learn(self, cond: Value, holds: bool, offset: _Offset) -> None:
        """Add what *cond* coming out as *holds* says, *offset* saying what a
        value is a constant away from."""
        for pred, left, right in _facts_of(cond, holds):
            fact = (pred, id(left), id(right))
            if fact in self.facts:
                continue
            self.facts.add(fact)
            if pred is CmpPred.NE:
                self.unequal[id(left)] = (*self.unequal.get(id(left), ()), right)
            elif pred in _LESS:
                # The orderings the other way round are these with their sides
                # swapped, which are among the facts too.
                signed = pred in _SIGNED
                x, at_x = offset(left, signed)
                y, at_y = offset(right, signed)
                if x is not y:
                    self._bound(x, y, signed, _LESS[pred] - at_x + at_y)

    def take(self, other: _Known) -> None:
        """Add everything *other* knows."""
        for left, rights in other.unequal.items():
            for right in rights:
                if (CmpPred.NE, left, id(right)) not in self.facts:
                    self.unequal[left] = (*self.unequal.get(left, ()), right)
        self.facts |= other.facts
        for (_, _, signed), (most, x, y) in other.bounds.items():
            self._bound(x, y, signed, most)

    def _bound(self, x: Value, y: Value, signed: bool, most: int) -> None:
        """Know that `x - y` is at most *most*."""
        key = (id(x), id(y), signed)
        old = self.bounds.get(key)
        if old is not None and old[0] <= most:
            return
        self.bounds[key] = (most, x, y)
        if old is None:
            self.upper[id(x)] = (*self.upper.get(id(x), ()), (y, signed))
            self.lower[id(y)] = (*self.lower.get(id(y), ()), (x, signed))

    def apart(self, x: Value, y: Value, signed: bool) -> int | None:
        """What `x - y` is known to be at most, if anything."""
        found = self.bounds.get((id(x), id(y), signed))
        return None if found is None else found[0]


class Facts:
    """Drops checks, saturation and comparisons that what is known settles."""

    name = "facts"

    def run(self, module: Module) -> bool:
        """Use what is known; report whether anything changed."""
        self.touched: list[Function] = []
        for func in module.functions.values():
            if func.is_declaration or not func.blocks:
                continue
            if _Ranges(module, func).use():
                self.touched.append(func)
        return bool(self.touched)


class _Ranges:
    """What every value of one function can be, and what that settles."""

    def __init__(self, module: Module, func: Function) -> None:
        self.module = module
        self.func = func
        #: The range of every integer worked out so far, as its own type reads
        #: it, by identity.  A value of the function that is not here yet has
        #: no range yet -- its definition was not reached -- which is not the
        #: same as one that could be anything.
        self.ranges: dict[int, _Range] = {}
        self.defined: set[int] = set()
        #: How many times each block's parameter has widened.
        self.widened: dict[int, int] = {}
        #: What holds at the end of each block.
        self.at_end: dict[int, _Known] = {}
        #: The additions and differences known not to wrap, and the checked
        #: operations known not to fail -- as of the last walk.
        self.fits: set[int] = set()
        #: What the last walk settled: checks that hold and comparisons whose
        #: answer is known.
        self.settled: list[AssertInst] = []
        self.answers: dict[int, bool] = {}
        #: The blocks the walk under way has reached.
        self.seen: set[int] = set()

    # -- the walk ---------------------------------------------------------------

    def use(self) -> bool:
        """Work the ranges out and change what they settle; say whether anything
        changed."""
        if not any(_worth(inst) for block in self.func.blocks for inst in block.insts):
            return False
        tree = dominators(self.func)
        preds = predecessors(self.func)
        for block in tree.order:
            for param in block.params:
                self.defined.add(id(param))
            for inst in block.insts:
                self.defined.add(id(inst))
        for _ in range(_ROUNDS):
            if not self._walk(tree.order, tree.idom, preds):
                break
        else:
            return False
        return self._apply()

    def _walk(self, order: list[BasicBlock], idom: dict[int, BasicBlock | None],
              preds: dict[int, list[BasicBlock]]) -> bool:
        """Walk every block once, working out its values' ranges and what they
        settle; say whether the parameter of a block already walked changed,
        which is what another walk is wanted for.  A function with no loop is
        walked once."""
        changed = False
        self.fits = set()
        self.settled = []
        self.answers = {}
        self.seen = set()
        entry = order[0]
        for block in order:
            self.seen.add(id(block))
            parent = idom.get(id(block))
            ways_in = preds[id(block)]
            last = ways_in[0].insts[-1] if len(ways_in) == 1 and ways_in[0].insts \
                else None
            branched = isinstance(last, CondBrInst) \
                and last.true_target.block is not last.false_target.block
            # What the block's dominator knew is what it knows, and is shared
            # rather than copied where the block learns nothing of its own.
            if parent is None:
                known = _Known()
            elif branched or any(isinstance(one, AssertInst) and not one.observing
                                 for one in block.insts):
                known = _Known(self.at_end[id(parent)])
            else:
                known = self.at_end[id(parent)]
            if branched:
                assert isinstance(last, CondBrInst)
                holds = last.true_target.block is block
                known.learn(last.operands[0], holds, self._offset)
                self._joined(known, ways_in[0], last.operands[0], holds, preds)
            if block is entry:
                for param in block.params:
                    if isinstance(param.ty, IntType):
                        self.ranges[id(param)] = _bounds(param.ty.bits,
                                                         param.ty.signed)
            for inst in block.insts:
                self._visit(inst, known)
            self.at_end[id(block)] = known
            changed |= self._hand_on(block)
        return changed

    def _joined(self, known: _Known, branching: BasicBlock, cond: Value,
                holds: bool, preds: dict[int, list[BasicBlock]]) -> None:
        """Add to *known* what the one way into *branching* that can make its
        parameter *cond* come out as *holds* says.

        `a and b` is a truth value joined from two ways: `b` where `a` held, and
        a written-down falsehood where it did not.  A branch on what was joined
        that went the way of the truth came by the first way, so what held
        there holds here -- `a`, and `b` itself.  Where more than one way could
        have made it, nothing is added: what holds on all of them is not worth
        the asking."""
        if not isinstance(cond, BlockParam) or cond.block is not branching:
            return
        found: tuple[BasicBlock, Value] | None = None
        for one in preds[id(branching)]:
            last = one.insts[-1] if one.insts else None
            targets = getattr(last, "successors", None)
            if targets is None:
                continue
            for target in targets():
                if target.block is not branching:
                    continue
                arg = target.args[cond.index]
                if isinstance(arg, BoolConst) and arg.value is not holds:
                    continue
                if found is not None:
                    return
                found = (one, arg)
        if found is None or id(found[0]) not in self.at_end:
            return
        way, arg = found
        known.take(self.at_end[id(way)])
        known.learn(arg, holds, self._offset)

    def _hand_on(self, block: BasicBlock) -> bool:
        """Widen each parameter of the blocks *block* goes to by what it hands
        them; say whether one of a block already walked changed."""
        last = block.insts[-1] if block.insts else None
        targets = getattr(last, "successors", None)
        if last is None or targets is None:
            return False
        known = self.at_end[id(block)]
        changed = False
        for at, target in enumerate(targets()):
            if not any(isinstance(one.ty, IntType) for one in target.block.params):
                continue
            here = known
            if isinstance(last, CondBrInst) \
                    and last.true_target.block is not last.false_target.block:
                here = _Known(known)
                here.learn(last.operands[0], at == 0, self._offset)
            for param, arg in zip(target.block.params, target.args):
                if not isinstance(param.ty, IntType):
                    continue
                found = self._at(arg, here, param.ty.signed)
                if found is not None and self._widen(param, found):
                    changed |= id(target.block) in self.seen
        return changed

    def _widen(self, param: BlockParam, found: _Range) -> bool:
        """Make *param*'s range take *found* in; say whether it grew."""
        old = self.ranges.get(id(param))
        if old is None:
            self.ranges[id(param)] = found
            return True
        lo, hi = min(old[0], found[0]), max(old[1], found[1])
        if (lo, hi) == old:
            return False
        times = self.widened.get(id(param), 0) + 1
        self.widened[id(param)] = times
        if times > _WIDENINGS:
            assert isinstance(param.ty, IntType)
            low, high = _bounds(param.ty.bits, param.ty.signed)
            lo = low if lo < old[0] else lo
            hi = high if hi > old[1] else hi
        self.ranges[id(param)] = (lo, hi)
        return True

    def _visit(self, inst: Instruction, known: _Known) -> None:
        """Work out *inst*'s range, and what is known of it, where *known*
        holds."""
        if isinstance(inst, AssertInst):
            cond = inst.operands[0]
            if not inst.observing:
                if isinstance(cond, CmpInst) and self._decide(cond, known) is True:
                    self.settled.append(inst)
                # What a check stops the program for not holding holds after it.
                known.learn(cond, True, self._offset)
            return
        if isinstance(inst, CmpInst):
            answer = self._decide(inst, known)
            if answer is not None:
                self.answers[id(inst)] = answer
            return
        if not isinstance(inst.ty, IntType):
            return
        found = self._range_of(inst, known)
        if found is None:
            self.ranges.pop(id(inst), None)
        else:
            self.ranges[id(inst)] = found

    # -- what a value is --------------------------------------------------------

    def _own(self, value: Value) -> _Range | None:
        """*value*'s range as its type reads it, before anything narrows it --
        or nothing, where it has none yet."""
        if isinstance(value, IntConst):
            return (value.value, value.value)
        found = self.ranges.get(id(value))
        if found is not None:
            return found
        if id(value) in self.defined:
            return None
        ty = value.ty
        assert isinstance(ty, IntType)
        return _bounds(ty.bits, ty.signed)

    def _at(self, value: Value, known: _Known, signed: bool) -> _Range | None:
        """*value*'s range where *known* holds, read signed or not."""
        base = _plain(value)
        ty = base.ty
        if not isinstance(ty, IntType):
            return None
        if isinstance(base, IntConst):
            return _reread((base.value, base.value), ty.bits, ty.signed, signed)
        own = self._own(base)
        if own is None:
            return None
        return _reread(self._narrowed(base, own, known), ty.bits, ty.signed, signed)

    def _narrowed(self, base: Value, own: _Range, known: _Known) -> _Range:
        """*own*, the range of *base*, narrowed by every fact *known* has about
        it -- or about what it is a constant away from.

        Each bound moves one end, and the tightest wins whatever order they come
        in; then a value known to differ from an end moves past it, as long as
        it can.  A contradiction -- a block nothing reaches -- leaves the range
        as it was."""
        if id(base) not in known.unequal and id(base) not in known.upper \
                and id(base) not in known.lower \
                and not (isinstance(base, BinaryInst) and base.op in _STEPS):
            return own
        ty = base.ty
        assert isinstance(ty, IntType)
        bits, signed = ty.bits, ty.signed
        ends = [list(_reread(own, bits, signed, False)),
                list(_reread(own, bits, signed, True))]
        narrowed = False
        for reading in (False, True):
            x, moved = self._offset(base, reading)
            end = ends[reading]
            for y, kind in known.upper.get(id(x), ()):
                found = self._own(y) if kind is reading else None
                if found is None:
                    continue
                assert isinstance(y.ty, IntType)
                most = known.bounds[(id(x), id(y), kind)][0]
                hi = _reread(found, bits, y.ty.signed, reading)[1]
                end[1] = min(end[1], hi + most + moved)
                narrowed = True
            for y, kind in known.lower.get(id(x), ()):
                found = self._own(y) if kind is reading else None
                if found is None:
                    continue
                assert isinstance(y.ty, IntType)
                most = known.bounds[(id(y), id(x), kind)][0]
                lo = _reread(found, bits, y.ty.signed, reading)[0]
                end[0] = max(end[0], lo - most + moved)
                narrowed = True
        unequal: list[int] = []
        for other in known.unequal.get(id(base), ()):
            found = self._own(other)
            if found is not None and found[0] == found[1]:
                assert isinstance(other.ty, IntType)
                unequal.append(_reread(found, bits, other.ty.signed, signed)[0])
        if not narrowed and not unequal:
            return own
        if any(end[0] > end[1] for end in ends):
            return own
        lo, hi = own
        for reading, end in enumerate(ends):
            one = _reread((end[0], end[1]), bits, bool(reading), signed)
            lo, hi = max(lo, one[0]), min(hi, one[1])
        if lo > hi:
            return own
        moved_on = True
        while moved_on and lo <= hi:
            moved_on = False
            if lo in unequal:
                lo += 1
                moved_on = True
            if hi in unequal and lo <= hi:
                hi -= 1
                moved_on = True
        return own if lo > hi else (lo, hi)

    def _range_of(self, inst: Instruction, known: _Known) -> _Range | None:
        """What *inst*, an integer, can be where *known* holds -- or nothing,
        where an operand has no range yet."""
        ty = inst.ty
        assert isinstance(ty, IntType)
        whole = _bounds(ty.bits, ty.signed)
        match inst:
            case BinaryInst():
                return self._binary(inst, ty, known)
            case CastInst():
                return self._cast(inst, ty, known)
            case UnaryInst():
                return self._unary(inst, ty, known)
            case SelectInst():
                first = self._at(inst.operands[1], known, ty.signed)
                second = self._at(inst.operands[2], known, ty.signed)
                if first is None or second is None:
                    return None
                return (min(first[0], second[0]), max(first[1], second[1]))
            case ExtractInst() if inst.index == 0 \
                    and isinstance(inst.operands[0], BinaryInst) \
                    and inst.operands[0].op is BinOp.WIDE_MUL:
                # The upper half of a whole product, which grows with both
                # factors as long as neither is read signed.
                product = inst.operands[0]
                left = self._at(product.operands[0], known, False)
                right = self._at(product.operands[1], known, False)
                if left is None or right is None or ty.signed:
                    return whole
                return ((left[0] * right[0]) >> 64, (left[1] * right[1]) >> 64)
        return whole

    def _binary(self, inst: BinaryInst, ty: IntType, known: _Known) -> _Range | None:
        """What an operation on two integers can be."""
        op = inst.op
        whole = _bounds(ty.bits, ty.signed)
        if op in _SHIFTS:
            return self._shift(inst, ty, known)
        if op in (BinOp.UDIV, BinOp.UREM) and ty.signed:
            # Asked of a value held with its sign above it, which is not the
            # value its bits read unsigned.
            return whole
        if op in (BinOp.AND, BinOp.OR, BinOp.XOR, BinOp.UDIV, BinOp.UREM,
                  BinOp.UMIN, BinOp.UMAX):
            # Questions about the bits, which are asked of them unsigned.
            left = self._at(inst.operands[0], known, False)
            right = self._at(inst.operands[1], known, False)
            if left is None or right is None:
                return None
            found = _unsigned(op, left, right)
            if found is None:
                return whole
            return _reread(found, ty.bits, False, ty.signed)
        if op in (BinOp.SMIN, BinOp.SMAX):
            left = self._at(inst.operands[0], known, True)
            right = self._at(inst.operands[1], known, True)
            if left is None or right is None:
                return None
            pick = min if op is BinOp.SMIN else max
            return _reread((pick(left[0], right[0]), pick(left[1], right[1])),
                           ty.bits, True, ty.signed)
        if op not in (BinOp.ADD, BinOp.SUB, BinOp.MUL, BinOp.WRAP_ADD,
                      BinOp.WRAP_SUB, BinOp.WRAP_MUL, BinOp.SAT_ADD,
                      BinOp.SAT_SUB, BinOp.SAT_MUL):
            return whole
        left = self._at(inst.operands[0], known, ty.signed)
        right = self._at(inst.operands[1], known, ty.signed)
        if left is None or right is None:
            return None
        match op:
            case BinOp.ADD | BinOp.WRAP_ADD | BinOp.SAT_ADD:
                lo, hi = left[0] + right[0], left[1] + right[1]
            case BinOp.SUB | BinOp.WRAP_SUB | BinOp.SAT_SUB:
                lo, hi = left[0] - right[1], left[1] - right[0]
                if not ty.signed and lo < 0:
                    # A difference whose right side is known to be the smaller
                    # does not go below nought, whatever the ranges say: the
                    # two are related, and their ranges are not.
                    if self._holds(CmpPred.ULT, inst.operands[1],
                                   inst.operands[0], known):
                        lo = 1
                    elif self._holds(CmpPred.ULE, inst.operands[1],
                                     inst.operands[0], known):
                        lo = 0
            case _:
                corners = (left[0] * right[0], left[0] * right[1],
                           left[1] * right[0], left[1] * right[1])
                lo, hi = min(corners), max(corners)
        low, high = whole
        if low <= lo and hi <= high:
            self.fits.add(id(inst))
            return (lo, hi)
        if op in (BinOp.SAT_ADD, BinOp.SAT_SUB, BinOp.SAT_MUL):
            return (min(max(lo, low), high), max(min(hi, high), low))
        if op in (BinOp.ADD, BinOp.SUB, BinOp.MUL):
            # A checked one that went past stopped the program, so what it
            # answered is inside the type -- and where nothing it could answer
            # is, it never answers at all.
            lo, hi = max(lo, low), min(hi, high)
            return (lo, hi) if lo <= hi else whole
        return whole

    def _shift(self, inst: BinaryInst, ty: IntType, known: _Known) -> _Range | None:
        """What a shift or a rotation can be."""
        whole = _bounds(ty.bits, ty.signed)
        distance = self._at(inst.operands[1], known, False)
        if distance is None:
            return None
        op = inst.op
        # The wrapping shift takes its distance modulo the width, which below
        # the width is the distance itself -- so a checked shift that cannot go
        # as far is the wrapping one.  Made that where the modulo costs no more
        # than the check: a width that is a power of two, whose modulo is an
        # `and`, and a distance written down, which the code generator reduces
        # itself.  Any other distance at any other width is a remainder by the
        # width, dearer than a comparison that never branches.
        cheap = ty.bits & (ty.bits - 1) == 0 \
            or isinstance(inst.operands[1], IntConst)
        if op in _PLAIN:
            # A checked shift as far as the width or further stopped the program.
            if distance[0] >= ty.bits:
                return whole
            if distance[1] < ty.bits and cheap:
                self.fits.add(id(inst))
            distance = (distance[0], min(distance[1], ty.bits - 1))
        elif distance[1] >= ty.bits:
            distance = (0, ty.bits - 1)
        near, far = distance
        if op in (BinOp.SHL, BinOp.WRAP_SHL):
            value = self._at(inst.operands[0], known, ty.signed)
            if value is None:
                return None
            if value[0] < 0:
                return whole
            lo, hi = value[0] << near, value[1] << far
            return (lo, hi) if hi <= whole[1] else whole
        # A value narrower than its register is shifted in the whole register,
        # its sign or its noughts above it -- so a shift that brings in the
        # other kind of bit is the other shift.  Where the top bit is clear the
        # two are one, and what they come to is known; where it may be set, it
        # is known only where the shift is the one the type's own reading asks.
        if op in (BinOp.ASHR, BinOp.WRAP_ASHR) and ty.signed:
            value = self._at(inst.operands[0], known, True)
            if value is None:
                return None
            return (min(value[0] >> near, value[0] >> far),
                    max(value[1] >> near, value[1] >> far))
        if op in (BinOp.LSHR, BinOp.WRAP_LSHR, BinOp.ASHR, BinOp.WRAP_ASHR):
            value = self._at(inst.operands[0], known, False)
            if value is None:
                return None
            if (ty.signed or op in (BinOp.ASHR, BinOp.WRAP_ASHR)) \
                    and value[1] >= 1 << (ty.bits - 1):
                return whole
            return (value[0] >> far, value[1] >> near)
        return whole

    def _cast(self, inst: CastInst, ty: IntType, known: _Known) -> _Range | None:
        """What a number read as another kind of number can be."""
        whole = _bounds(ty.bits, ty.signed)
        inner = inst.operands[0]
        if not isinstance(inner.ty, IntType):
            return whole
        match inst.kind:
            case CastKind.BITCAST | CastKind.ZEXT | CastKind.SEXT \
                    if inner.ty.bits == ty.bits:
                return self._at(inner, known, ty.signed)
            case CastKind.ZEXT:
                found = self._at(inner, known, False)
            case CastKind.SEXT:
                found = self._at(inner, known, True)
                if found is not None and not ty.signed:
                    return _reread(found, ty.bits, True, False)
            case CastKind.TRUNC:
                found = self._at(inner, known, ty.signed)
                if found is not None and not (whole[0] <= found[0]
                                              and found[1] <= whole[1]):
                    return whole
            case _:
                return whole
        if found is None:
            return None
        return found if whole[0] <= found[0] and found[1] <= whole[1] else whole

    def _unary(self, inst: UnaryInst, ty: IntType, known: _Known) -> _Range | None:
        """What an operation on one integer can be."""
        whole = _bounds(ty.bits, ty.signed)
        inner = inst.operands[0]
        if not isinstance(inner.ty, IntType):
            return whole
        match inst.op:
            case UnOp.COUNT_ONES | UnOp.COUNT_LEADING | UnOp.COUNT_TRAILING:
                return (0, inner.ty.bits) if inner.ty.bits <= whole[1] else whole
            case UnOp.NOT if inner.ty is ty:
                found = self._at(inner, known, ty.signed)
                if found is None:
                    return None
                if ty.signed:
                    return (-found[1] - 1, -found[0] - 1)
                return (whole[1] - found[1], whole[1] - found[0])
            case UnOp.NEG if inner.ty is ty and ty.signed:
                found = self._at(inner, known, True)
                if found is None:
                    return None
                lo, hi = max(-found[1], whole[0]), min(-found[0], whole[1])
                return (lo, hi) if lo <= hi else whole
        return whole

    # -- what a comparison answers ---------------------------------------------

    def _holds(self, pred: CmpPred, left: Value, right: Value,
               known: _Known) -> bool:
        """Whether `left pred right` is known to hold where *known* does."""
        return self._answer(pred, left, right, known) is True

    def _decide(self, cmp: CmpInst, known: _Known) -> bool | None:
        """What *cmp* answers where *known* holds, where that is known."""
        if cmp.pred not in _NEGATED:
            return None
        left, right = cmp.operands
        if not isinstance(left.ty, IntType) or not isinstance(right.ty, IntType):
            return None
        return self._answer(cmp.pred, left, right, known)

    def _answer(self, pred: CmpPred, left: Value, right: Value,
                known: _Known) -> bool | None:
        """What `left pred right` answers where *known* holds, where that is
        known: the same value on both sides, a fact, the two ranges, or a fact
        about what the two are a constant away from."""
        a, b = _plain(left), _plain(right)
        if a is b:
            return pred in (CmpPred.EQ, CmpPred.ULE, CmpPred.UGE, CmpPred.SLE,
                            CmpPred.SGE)
        if (pred, id(a), id(b)) in known.facts:
            return True
        if (_NEGATED[pred], id(a), id(b)) in known.facts:
            return False
        signed = pred in _SIGNED
        first = self._at(a, known, signed)
        second = self._at(b, known, signed)
        if first is not None and second is not None:
            found = _compared(pred, first, second)
            if found is not None:
                return found
        if pred in (CmpPred.EQ, CmpPred.NE):
            return None
        x, at_x = self._offset(a, signed)
        y, at_y = self._offset(b, signed)
        # `x - y` at most `below`, `y - x` at most `above`, as far as is known.
        if x is y:
            below = above = 0
        else:
            below = known.apart(x, y, signed)
            above = known.apart(y, x, signed)
        # And so `a - b` at most `below + at_x - at_y`, `b - a` at most
        # `above + at_y - at_x`.
        if pred in _LESS:
            wanted = _LESS[pred]
            if below is not None and below + at_x - at_y <= wanted:
                return True
            if above is not None and above + at_y - at_x <= -1 - wanted:
                return False
        else:
            wanted = _MORE[pred]
            if above is not None and above + at_y - at_x <= wanted:
                return True
            if below is not None and below + at_x - at_y <= -1 - wanted:
                return False
        return None

    def _offset(self, value: Value, signed: bool) -> tuple[Value, int]:
        """*value* as another value and a constant added to it, where the sum is
        known not to wrap as *signed* reads it: `at + 1` is `at` and one."""
        moved = 0
        while True:
            if not isinstance(value, BinaryInst) or not isinstance(value.ty, IntType) \
                    or value.ty.signed != signed:
                return value, moved
            op = value.op
            if op not in _STEPS:
                return value, moved
            if op not in (BinOp.ADD, BinOp.SUB) and not (
                    op in (BinOp.WRAP_ADD, BinOp.WRAP_SUB) and id(value) in self.fits):
                return value, moved
            left, right = value.operands
            if isinstance(right, IntConst):
                step = right.value if op in (BinOp.ADD, BinOp.WRAP_ADD) else -right.value
                value = _plain(left)
            elif isinstance(left, IntConst) and op in (BinOp.ADD, BinOp.WRAP_ADD):
                step = left.value
                value = _plain(right)
            else:
                return value, moved
            moved += step

    # -- changing the function --------------------------------------------------

    def _apply(self) -> bool:
        """Change what the last walk settled; say whether anything changed."""
        changed = False
        for block in self.func.blocks:
            for inst in block.insts:
                if isinstance(inst, BinaryInst) and inst.op in _PLAIN \
                        and id(inst) in self.fits and isinstance(inst.ty, IntType):
                    inst.op = _PLAIN[inst.op]
                    changed = True
        instead: dict[int, Value] = {}
        for block in self.func.blocks:
            for inst in block.insts:
                answer = self.answers.get(id(inst))
                if answer is not None:
                    instead[id(inst)] = self.module.bool_const(inst.ty, answer)
        dropped = {id(one) for one in self.settled}
        if instead or dropped:
            replace_uses(self.func, instead, dropped)
            changed = True
        return changed


def _worth(inst: Instruction) -> bool:
    """Whether *inst* is something a range or a fact could settle."""
    if isinstance(inst, AssertInst):
        return not inst.observing
    if isinstance(inst, BinaryInst):
        return inst.op in _PLAIN and isinstance(inst.ty, IntType)
    return isinstance(inst, CmpInst) and isinstance(inst.operands[0].ty, IntType)


def _unsigned(op: BinOp, left: _Range, right: _Range) -> _Range | None:
    """What an operation on the bits of two numbers can be, both read
    unsigned -- or nothing, where that is every value there is."""
    match op:
        case BinOp.AND:
            return (0, min(left[1], right[1]))
        case BinOp.OR | BinOp.XOR:
            top = (1 << max(left[1], right[1]).bit_length()) - 1
            return (max(left[0], right[0]) if op is BinOp.OR else 0, top)
        case BinOp.UDIV if right[0] > 0:
            return (left[0] // right[1], left[1] // right[0])
        case BinOp.UREM if right[0] > 0:
            return (0, min(left[1], right[1] - 1))
        case BinOp.UMIN:
            return (min(left[0], right[0]), min(left[1], right[1]))
        case BinOp.UMAX:
            return (max(left[0], right[0]), max(left[1], right[1]))
    return None


def _compared(pred: CmpPred, left: _Range, right: _Range) -> bool | None:
    """What `left pred right` answers for every value in the two ranges, where
    it answers the same for all of them."""
    match pred:
        case CmpPred.ULT | CmpPred.SLT:
            if left[1] < right[0]:
                return True
            if left[0] >= right[1]:
                return False
        case CmpPred.ULE | CmpPred.SLE:
            if left[1] <= right[0]:
                return True
            if left[0] > right[1]:
                return False
        case CmpPred.UGT | CmpPred.SGT:
            if left[0] > right[1]:
                return True
            if left[1] <= right[0]:
                return False
        case CmpPred.UGE | CmpPred.SGE:
            if left[0] >= right[1]:
                return True
            if left[1] < right[0]:
                return False
        case CmpPred.EQ | CmpPred.NE:
            if left[1] < right[0] or right[1] < left[0]:
                return pred is CmpPred.NE
            if left[0] == left[1] == right[0] == right[1]:
                return pred is CmpPred.EQ
    return None
