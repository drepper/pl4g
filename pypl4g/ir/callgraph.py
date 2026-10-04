"""Who calls whom, and the order that follows from it.

Two things want the same answer.  The backend wants to generate a callee before
its caller, because what a call destroys is asked of the callee once it has been
generated: a caller built after its callee saves only the registers that callee
actually wrote, and one built before it has to assume the convention's whole
caller-saved set.  The inliner wants the same order for the same reason one step
up: a callee already dealt with is one whose size and whose calls are final, and
those are what the decision rests on.

So the order is computed once, here, and both ask for it.

**A cycle has no such order**, and the answer is to stop asking: the functions of
a cycle come out in the order they were met, and each of them is a function whose
callees are not all finished.  For the backend that means the convention's set
rather than the exact one, which is what it had for every function before this
existed; for the inliner it means not inlining round a cycle, which is what an
inliner must not do anyway.
"""

from __future__ import annotations

from typing import Final, Sequence

from .function import Function
from .inst import (AddressInst, AssertInst, BinOp, BinaryInst, CallInst, CastInst,
                   CastKind, ErrorInst, ExtractInst, FailedInst, Instruction,
                   MemStartInst, SizeOfInst, SumMakeInst, SwitchInst, SyscallInst,
                   TupleInst, UnreachableInst, UnwrapInst, WrapInst)
from .module import Module


def callees_of(func: Function) -> list[Function]:
    """Every function this one names, in the order it names them, once each.

    Asked of the instructions rather than of a table kept beside them: an
    instruction says what it names, so a shape that learns to name a function
    is one this follows without being told.
    """
    found: dict[int, Function] = {}
    for block in func.blocks:
        for inst in block.insts:
            for named in inst.references():
                if isinstance(named, Function):
                    found.setdefault(id(named), named)
    return list(found.values())


def in_call_order(module: Module) -> list[Function]:
    """Every function of *module*, callees before callers.

    A depth-first walk left in the order it finished, which is the topological
    order where there is one.  Where a cycle makes that impossible the functions
    of the cycle come out in the order the walk met them, and whoever asked is
    the one that decides what to do about a callee it has not seen yet.

    The walk starts from the functions in the order the module holds them, so
    two programs that differ in nothing differ in nothing here either: the order
    is a property of the program and not of a dictionary's iteration.
    """
    order: list[Function] = []
    done: set[int] = set()
    open_: set[int] = set()
    for root in module.functions.values():
        if id(root) in done:
            continue
        # An explicit stack rather than recursion: a chain of calls is as deep
        # as a program cares to make it, and a compiler is not the place to find
        # out what the interpreter's limit is.
        stack: list[tuple[Function, list[Function]]] = [(root, callees_of(root))]
        open_.add(id(root))
        while stack:
            func, waiting = stack[-1]
            if not waiting:
                stack.pop()
                open_.discard(id(func))
                if id(func) not in done:
                    done.add(id(func))
                    order.append(func)
                continue
            callee = waiting.pop()
            if id(callee) in done or id(callee) in open_:
                # Seen, or on the way back to here, which is the cycle.
                continue
            open_.add(id(callee))
            stack.append((callee, callees_of(callee)))
    return order


def in_a_cycle(module: Module) -> frozenset[int]:
    """The identities of the functions a call can come back round to.

    A function that calls itself, and every function of a ring of them.  What
    asks is the inliner: inlining round a cycle does not finish, and a callee
    whose own callees are not final is one whose size says nothing yet.
    """
    reaching: dict[int, set[int]] = {}
    for func in module.functions.values():
        reaching[id(func)] = {id(one) for one in callees_of(func)}
    # Warshall over what is a small graph: a program's call graph has as many
    # edges as it has calls, and this runs once.
    changed = True
    while changed:
        changed = False
        for at, reached in reaching.items():
            grown = set(reached)
            for other in reached:
                grown |= reaching.get(other, set())
            if grown != reached:
                reaching[at] = grown
                changed = True
    return frozenset(at for at, reached in reaching.items() if at in reached)


def called_by_count(module: Module) -> dict[int, int]:
    """How many calls in the whole program name each function.

    A function called once and reachable only through that call is one inlining
    *removes*: the copy is the only one there is, and what is left behind is
    dropped by the pass that drops what nothing reaches.
    """
    counted: dict[int, int] = {}
    for func in module.functions.values():
        for block in func.blocks:
            for inst in block.insts:
                for named in inst.references():
                    if isinstance(named, Function):
                        counted[id(named)] = counted.get(id(named), 0) + 1
    return counted


def size_of_body(func: Function) -> int:
    """How many instructions a function is, which is what "small" is measured in.

    The instructions and not the bytes: what the bytes come to is the backend's
    to say and is not known where this is asked.  A block's parameters cost
    nothing to speak of -- they are where values arrive -- so they are not
    counted.
    """
    return sum(len(block.insts) for block in func.blocks)


#: What one of each costs where it is generated, in machine instructions -- the
#: answer the back ends give, read off the language tests at `-O0`, where every
#: operation of the representation is one stretch of the code.  An estimate and
#: not a measure: the same operation is a different number on each target and
#: next to different neighbours.  What it gets right is that some operations are
#: nothing -- they name registers -- and some carry a whole fault path.
#:
#: Naming a register, or reading one of several: nothing.
_FREE: Final[tuple[type, ...]] = (MemStartInst, TupleInst, ExtractInst, WrapInst,
                                  UnwrapInst, ErrorInst, FailedInst, SizeOfInst,
                                  UnreachableInst)
#: A checked operation is the operation, the test and the way to the fault.
_CHECKED: Final[dict[BinOp, int]] = {
    BinOp.ADD: 6, BinOp.SUB: 6, BinOp.MUL: 8,
    BinOp.SDIV: 5, BinOp.UDIV: 5, BinOp.SREM: 5, BinOp.UREM: 5,
    BinOp.SAT_ADD: 2, BinOp.SAT_SUB: 2, BinOp.SAT_MUL: 3,
    BinOp.SMAX: 2, BinOp.UMAX: 2, BinOp.SMIN: 2, BinOp.UMIN: 2,
}
#: The arguments put where the callee wants them and the call itself.
CALL_COST: Final[int] = 4


def cost_of(inst: Instruction) -> int:
    """What *inst* comes to where it is generated, in machine instructions."""
    match inst:
        case _ if isinstance(inst, _FREE):
            return 0
        case CastInst():
            return 0 if inst.kind is CastKind.BITCAST else 1
        case BinaryInst():
            return _CHECKED.get(inst.op, 1)
        case CallInst() | SyscallInst():
            return CALL_COST
        case AssertInst():
            return 8
        case AddressInst() | SumMakeInst():
            return 2
        case SwitchInst():
            return 1 + 2 * len(inst.cases)
    return 1


def cost_of_body(func: Function) -> int:
    """What a copy of *func* comes to where it is generated, which is what an
    inliner weighs: `cost_of` every instruction.  A count of instructions says
    a `bitcast` and a checked multiplication are one each, and the one is
    nothing and the other eight."""
    return sum(cost_of(inst) for block in func.blocks for inst in block.insts)


def names(functions: Sequence[Function]) -> list[str]:
    """What each of them is called, for a log that says what the order was."""
    return [func.name for func in functions]
