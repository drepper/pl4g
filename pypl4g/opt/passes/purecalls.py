"""What a pure function's purity buys besides dropping a call nothing reads.

A pure function changes nothing that outlives the call, so what a call to one
answers depends on its arguments and on whatever memory it reads.  Where it reads
none but its own frame, the answer depends on the arguments alone, and three things
follow without the language saying anything more:

- **A call made twice with the same arguments is worked out once.**  The second is
  where the first already was -- the first dominates it -- and stands for the first
  (`pure-call-reused`).
- **A call whose arguments do not change round a loop is moved out of it**, into the
  block that enters the loop (`pure-call-hoisted`).  Only from the loop's head, which
  runs every time the loop is entered, and only before anything there that could
  stop the program: a pure function may still stop it, and the program has to stop
  the same way it would have.
- **A call whose arguments are all constants is worked out while compiling**
  (`pure-call-folded`), by the machine that runs the macros, on the function's own
  intermediate representation.  Where that stops -- a check that does not hold, a
  thing it will not run, too many steps -- the call stays, so the program stops
  where it would have and says what it would have said.

**Only answers that are values** are worked out once: a number, a truth value, a code
point.  A function that answers text made in the heap answers a new object each
time, and two holders of one object would give it back twice.  Arguments may be
anything the answer depends on alone, which is what reading no memory but the
function's own frame says.
"""

from __future__ import annotations

from typing import Final

from ...front.interpret import Machine, Refused, Stopped
from ...ir.function import BasicBlock, Function
from ...ir.inst import (AddressInst, AssertInst, BrInst, CallInst, CastInst,
                        CmpInst, ExtractInst, FrameInst, Instruction, LoadInst,
                        StoreInst, TupleInst, BinaryInst, BinOp)
from ...ir.module import GlobalVar, Module
from ...ir.reports import ReportKind
from ...ir.rewrite import all_stand_for
from ...ir.types import BoolType, CharType, FloatType, IntType, Type
from ...ir.value import BlockParam, BoolConst, CharConst, Const, IntConst, Value

#: What may stand in the head of a loop before a call moved out of it: nothing that
#: could stop the program, so that what stops it, if anything, is still the same.
_HARMLESS: Final = (CastInst, ExtractInst, TupleInst, CmpInst, AddressInst,
                    FrameInst)


class PureCalls:
    """Reuses, hoists and works out calls to pure functions that read no memory."""

    name = "purecalls"

    def __init__(self) -> None:
        self._free: dict[int, bool] = {}
        self._floats: dict[int, bool] = {}

    def run(self, module: Module) -> bool:
        """Do all three to every function with a body."""
        self._free = {}
        self._floats = {}
        changed = False
        for func in list(module.functions.values()):
            if not func.blocks:
                continue
            changed |= self._fold(module, func)
            calls = sum(1 for block in func.blocks for inst in block.insts
                        if self._candidate(inst) is not None)
            if not calls:
                # Nothing for the other two to do, and finding that out costs
                # the dominators.
                continue
            dominators = _dominators(func)
            if calls > 1:
                changed |= self._reuse(module, func, dominators)
                dominators = _dominators(func)
            changed |= self._hoist(module, func, dominators)
        return changed

    # -- what may be done to a call ------------------------------------------

    def _candidate(self, inst: Instruction) -> Function | None:
        """The callee of a call whose answer depends on its arguments alone."""
        if not isinstance(inst, CallInst) or inst.has_effects:
            return None
        callee = inst.callee
        if not isinstance(callee, Function) or not callee.blocks \
                or not _a_value(inst.ty):
            return None
        return callee if self._reads_nothing(callee, set()) else None

    def _reads_nothing(self, func: Function, seen: set[int]) -> bool:
        """Whether *func* reads no memory but its own frame, nor calls anything
        that does -- so that what it answers is what its arguments say."""
        known = self._free.get(id(func))
        if known is not None:
            return known
        if id(func) in seen:
            # A call of itself: as free as the rest of it turns out to be.
            return True
        seen.add(id(func))
        found = True
        for block in func.blocks:
            for inst in block.insts:
                if isinstance(inst, (LoadInst, StoreInst)):
                    if not all(_in_its_frame(one) for one in
                               (*inst.reads(), *inst.writes())):
                        found = False
                elif isinstance(inst, AddressInst):
                    var = inst.operands[0]
                    if isinstance(var, GlobalVar) and var.mutable:
                        found = False
                elif isinstance(inst, CallInst):
                    callee = inst.callee
                    if not isinstance(callee, Function) or not callee.blocks \
                            or inst.has_effects \
                            or not self._reads_nothing(callee, seen):
                        found = False
                if not found:
                    break
            if not found:
                break
        seen.discard(id(func))
        self._free[id(func)] = found
        return found

    # -- worked out while compiling ------------------------------------------

    def _fold(self, module: Module, func: Function) -> bool:
        """Work out every call whose arguments are all constants."""
        instead: dict[int, Value] = {}
        for block in func.blocks:
            for inst in list(block.insts):
                callee = self._candidate(inst)
                if callee is None:
                    continue
                if any(not isinstance(one, (IntConst, BoolConst, CharConst))
                       for one in inst.operands):
                    continue
                if self._uses_floats(callee, set()):
                    # The machine counts in Python's numbers, which are not the
                    # target's floating point everywhere: left to the target.
                    continue
                answer = _worked_out(module, callee, inst)
                if answer is None:
                    continue
                instead[id(inst)] = answer
                block.insts.remove(inst)
                module.reports.record(
                    ReportKind.PURE_CALL_FOLDED, func.name,
                    "".join(("the call of '", callee.name, "' is worked out while "
                             "compiling: it is pure, reads no memory, and every "
                             "argument is a constant")),
                    inst.span if inst.span.is_valid else func.span)
        if not instead:
            return False
        all_stand_for(func, instead)
        return True

    def _uses_floats(self, func: Function, seen: set[int]) -> bool:
        """Whether a floating-point value is worked out anywhere *func* reaches."""
        known = self._floats.get(id(func))
        if known is not None:
            return known
        if id(func) in seen:
            return False
        seen.add(id(func))
        found = any(isinstance(param.ty, FloatType)
                    for block in func.blocks for param in block.params) \
            or any(isinstance(inst.ty, FloatType)
                   or any(isinstance(one.ty, FloatType) for one in inst.operands)
                   or (isinstance(inst, CallInst)
                       and isinstance(inst.callee, Function)
                       and self._uses_floats(inst.callee, seen))
                   for block in func.blocks for inst in block.insts)
        self._floats[id(func)] = found
        return found

    # -- worked out once -------------------------------------------------------

    def _reuse(self, module: Module, func: Function,
               dominators: dict[int, set[int]]) -> bool:
        """Let a call stand for a dominating one to the same callee with the same
        arguments."""
        order = _preorder(func, dominators)
        made: list[tuple[BasicBlock, tuple[object, ...], CallInst]] = []
        instead: dict[int, Value] = {}
        for block in order:
            for inst in list(block.insts):
                if self._candidate(inst) is None:
                    continue
                assert isinstance(inst, CallInst)
                key = (id(inst.callee), *(id(_stood_for(one, instead))
                                          for one in inst.operands))
                earlier = next((call for where, written, call in made
                                if written == key
                                and id(where) in dominators[id(block)]
                                and (where is not block
                                     or block.insts.index(call)
                                     < block.insts.index(inst))), None)
                if earlier is None:
                    made.append((block, key, inst))
                    continue
                instead[id(inst)] = earlier
                block.insts.remove(inst)
                module.reports.record(
                    ReportKind.PURE_CALL_REUSED, func.name,
                    "".join(("this call of '", inst.callee.name, "' is the one "
                             "made earlier with the same arguments: it is pure "
                             "and reads no memory, so it is worked out once")),
                    inst.span if inst.span.is_valid else func.span)
        if not instead:
            return False
        all_stand_for(func, instead)
        return True

    # -- moved out of a loop ---------------------------------------------------

    def _hoist(self, module: Module, func: Function,
               dominators: dict[int, set[int]]) -> bool:
        """Move a call from a loop's head, where nothing it is handed changes
        round the loop, to the block that enters the loop."""
        changed = False
        for head, body in _loops(func, dominators):
            entering = _entering(func, head, body)
            if entering is None:
                continue
            for inst in list(head.insts):
                if isinstance(inst, _HARMLESS) or (
                        isinstance(inst, BinaryInst) and inst.op in _PLAIN):
                    continue
                if self._candidate(inst) is None \
                        or any(_inside(one, body) for one in inst.operands):
                    # Anything else may stop the program, and a call after it
                    # has to stay after it.
                    break
                assert isinstance(inst, CallInst)
                head.insts.remove(inst)
                entering.insts.insert(len(entering.insts) - 1, inst)
                inst.parent = entering
                changed = True
                module.reports.record(
                    ReportKind.PURE_CALL_HOISTED, func.name,
                    "".join(("the call of '", inst.callee.name, "' is moved out "
                             "of the loop: it is pure, reads no memory, and "
                             "nothing it is handed changes round the loop")),
                    inst.span if inst.span.is_valid else func.span)
        return changed


#: How many instructions working out one call while compiling may take.
_STEPS: Final[int] = 5_000

#: Arithmetic that cannot stop the program: it wraps or answers in range.
_PLAIN: Final = frozenset({BinOp.WRAP_ADD, BinOp.WRAP_SUB, BinOp.WRAP_MUL,
                           BinOp.AND, BinOp.OR, BinOp.XOR, BinOp.SMAX, BinOp.SMIN,
                           BinOp.UMAX, BinOp.UMIN})


def _a_value(ty: Type) -> bool:
    """Whether an answer of *ty* is a value rather than something that is somewhere."""
    return isinstance(ty, (IntType, BoolType, CharType, FloatType))


def _in_its_frame(address: Value) -> bool:
    """Whether *address* is, or is worked out from, a frame of the function."""
    seen = address
    while True:
        if isinstance(seen, FrameInst):
            return True
        if isinstance(seen, (CastInst, ExtractInst)) or (
                isinstance(seen, BinaryInst)
                and seen.op in (BinOp.ADD, BinOp.SUB)):
            seen = seen.operands[0]
            continue
        return False


def _stood_for(value: Value, instead: dict[int, Value]) -> Value:
    """What *value* stands for once the calls already reused are."""
    return instead.get(id(value), value)


def _worked_out(module: Module, callee: Function, call: CallInst) -> Const | None:
    """What a call answers, worked out by the macros' machine, or nothing where it
    stops or will not run what it was given."""
    # A budget, so that a call worked out while compiling never costs more than
    # a little: one that would take longer is left to the program.
    machine = Machine({}, steps=_STEPS)
    try:
        args = [one.value for one in call.operands]  # type: ignore[attr-defined]
        found = machine.call(callee, args)
    except (Refused, Stopped, RecursionError, ZeroDivisionError):
        return None
    ty = call.ty
    if isinstance(ty, BoolType) and isinstance(found, (bool, int)):
        return module.bool_const(ty, bool(found))
    if isinstance(ty, CharType) and isinstance(found, int):
        return module.char_const(found)
    if isinstance(ty, IntType) and isinstance(found, int) \
            and not isinstance(found, bool):
        mask = (1 << ty.bits) - 1
        number = found & mask
        if ty.signed and number >> (ty.bits - 1):
            number -= 1 << ty.bits
        return module.int_const(ty, number)
    return None


def _successors(block: BasicBlock) -> list[BasicBlock]:
    """The blocks *block* may go to next."""
    last = block.insts[-1] if block.insts else None
    found = getattr(last, "successors", None)
    return [one.block for one in found()] if found is not None else []


def _dominators(func: Function) -> dict[int, set[int]]:
    """For each block, the blocks that dominate it."""
    blocks = func.blocks
    every = {id(one) for one in blocks}
    preds: dict[int, list[BasicBlock]] = {id(one): [] for one in blocks}
    for block in blocks:
        for one in _successors(block):
            preds[id(one)].append(block)
    found = {id(one): set(every) for one in blocks}
    found[id(blocks[0])] = {id(blocks[0])}
    changed = True
    while changed:
        changed = False
        for block in blocks[1:]:
            new = set(every)
            for pred in preds[id(block)]:
                new &= found[id(pred)]
            new |= {id(block)}
            if new != found[id(block)]:
                found[id(block)] = new
                changed = True
    return found


def _preorder(func: Function, dominators: dict[int, set[int]]) -> list[BasicBlock]:
    """The blocks, each after every block that dominates it."""
    return sorted(func.blocks, key=lambda one: len(dominators[id(one)]))


def _loops(func: Function, dominators: dict[int, set[int]]
           ) -> list[tuple[BasicBlock, set[int]]]:
    """Each natural loop: its head, and the blocks of it by identity."""
    preds: dict[int, list[BasicBlock]] = {id(one): [] for one in func.blocks}
    for block in func.blocks:
        for one in _successors(block):
            preds[id(one)].append(block)
    found: dict[int, tuple[BasicBlock, set[int]]] = {}
    for block in func.blocks:
        for head in _successors(block):
            if id(head) not in dominators[id(block)]:
                continue
            # A branch back to a block that dominates it: the loop is what reaches
            # the branch without going through the head.
            _, body = found.setdefault(id(head), (head, {id(head)}))
            waiting = [block]
            while waiting:
                one = waiting.pop()
                if id(one) in body:
                    continue
                body.add(id(one))
                waiting.extend(preds[id(one)])
    return list(found.values())


def _entering(func: Function, head: BasicBlock,
              body: set[int]) -> BasicBlock | None:
    """The one block outside the loop that goes to its head, and nowhere else."""
    outside = [block for block in func.blocks if id(block) not in body
               and head in _successors(block)]
    if len(outside) != 1:
        return None
    (block,) = outside
    last = block.insts[-1] if block.insts else None
    return block if isinstance(last, BrInst) else None


def _inside(value: Value, body: set[int]) -> bool:
    """Whether *value* is worked out, or arrives, in one of the loop's blocks."""
    if isinstance(value, BlockParam):
        return id(getattr(value, "block", None)) in body
    parent = getattr(value, "parent", None)
    return parent is not None and id(parent) in body
