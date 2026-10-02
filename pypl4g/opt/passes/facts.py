"""What a branch already settled, used where it is known to hold.

A block that a conditional branch is the only way into knows which way the
branch went: in the body of `foreach i … 0…#v`, `i < #v` holds.  Two things the
checker writes ask exactly that question again:

- **an index's check.**  `v⟦i⟧` in that body checks `i < #v` once more and stops
  the program where it does not hold.  Where a branch dominating the check
  already went the way that says it holds, the check is dropped.
- **a range's step.**  `0…n` steps by a saturating add, so that the last turn of
  `0u8…255u8` ends instead of faulting.  But where `i < anything` holds, `i` is
  below the largest value of its type and `i + 1` fits; the plain add is enough,
  and it is one instruction where the saturating one is four.

**What holds where.**  A fact is a comparison and which way it went, learned on
the edge into a block that edge is the only way into, and it holds in every
block that block dominates -- the values compared are SSA values and do not
change.  The dominator tree is walked once, each block inheriting what its
immediate dominator knew.

**One value under several names.**  An index is `u64 ¤idx`, a length `u64`, a
count `u64 ¤size`; the checker converts between them with casts that change the
unit and not one bit, and a comparison of `i` with `#v` and one of `zext(i)`
with `#v` are the same comparison.  So the operands are compared with every such
cast looked through.
"""

from __future__ import annotations

from typing import Final

from ...ir.function import BasicBlock, Function
from ...ir.inst import (AssertInst, BinaryInst, BinOp, CastInst, CastKind,
                        CmpInst, CmpPred, CondBrInst)
from ...ir.module import Module
from ...ir.types import IntType
from ...ir.value import IntConst, Value
from ..flow import dominators, predecessors

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

#: The comparisons that put a value below another, and so below the largest
#: its type holds: unsigned and signed.
_BELOW: Final = {False: CmpPred.ULT, True: CmpPred.SLT}
_ABOVE: Final = {False: CmpPred.UGT, True: CmpPred.SGT}

_Fact = tuple[CmpPred, int, int]


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


def _facts_of(cond: Value, holds: bool) -> list[_Fact]:
    """What is known where *cond* came out as *holds*."""
    if not isinstance(cond, CmpInst) or cond.pred not in _NEGATED:
        return []
    pred = cond.pred if holds else _NEGATED[cond.pred]
    left, right = (id(_plain(one)) for one in cond.operands)
    found: list[_Fact] = []
    for one in (pred, *_IMPLIED.get(pred, ())):
        found.append((one, left, right))
        found.append((_SWAPPED[one], right, left))
    return found


class Facts:
    """Drops checks and saturation a dominating branch already settled."""

    name = "facts"

    def run(self, module: Module) -> bool:
        """Use what the branches settled; report whether anything changed."""
        changed = False
        for func in module.functions.values():
            if func.is_declaration or not func.blocks:
                continue
            changed |= self._use(func)
        return changed

    def _use(self, func: Function) -> bool:
        """Walk the dominator tree, knowing at each block what holds there."""
        if not any(isinstance(block.insts[-1], CondBrInst)
                   for block in func.blocks if block.insts):
            return False
        tree = dominators(func)
        preds = predecessors(func)
        known: dict[int, frozenset[_Fact]] = {}
        settled: set[int] = set()
        changed = False
        for block in tree.order:
            parent = tree.idom.get(id(block))
            facts = set(known[id(parent)]) if parent is not None else set()
            facts.update(self._on_the_way_in(block, preds[id(block)]))
            known[id(block)] = frozenset(facts)
            for inst in block.insts:
                if isinstance(inst, AssertInst) and not inst.observing \
                        and self._holds(inst.operands[0], facts):
                    settled.add(id(inst))
                elif isinstance(inst, BinaryInst):
                    changed |= self._cannot_saturate(inst, facts)
        if settled:
            for block in func.blocks:
                block.insts = [one for one in block.insts if id(one) not in settled]
        return changed or bool(settled)

    def _on_the_way_in(self, block: BasicBlock,
                       preds: list[BasicBlock]) -> list[_Fact]:
        """What the one branch into *block*, if there is one, says holds."""
        if len(preds) != 1:
            return []
        last = preds[0].insts[-1] if preds[0].insts else None
        if not isinstance(last, CondBrInst):
            return []
        true, false = last.true_target.block, last.false_target.block
        if true is false:
            return []
        return _facts_of(last.operands[0], block is true)

    def _holds(self, cond: Value, facts: set[_Fact]) -> bool:
        """Whether *cond* is known to hold here."""
        if not isinstance(cond, CmpInst):
            return False
        left, right = (id(_plain(one)) for one in cond.operands)
        return (cond.pred, left, right) in facts

    def _cannot_saturate(self, inst: BinaryInst, facts: set[_Fact]) -> bool:
        """Make a saturating step of one a plain one where it cannot reach an
        end of its type."""
        if inst.op not in (BinOp.SAT_ADD, BinOp.SAT_SUB):
            return False
        ty = inst.ty
        step = inst.operands[1]
        if not isinstance(ty, IntType) or not isinstance(step, IntConst) \
                or step.value != 1:
            return False
        moved = id(_plain(inst.operands[0]))
        if inst.op is BinOp.SAT_ADD:
            # Below something, so below the largest the type holds.
            asked = _BELOW[ty.signed]
            new = BinOp.WRAP_ADD
        else:
            asked = _ABOVE[ty.signed]
            new = BinOp.WRAP_SUB
        if not any(pred is asked and left == moved for pred, left, _ in facts):
            return False
        inst.op = new
        return True
