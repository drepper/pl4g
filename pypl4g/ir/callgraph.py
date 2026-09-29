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

from typing import Sequence

from .function import Function
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


def names(functions: Sequence[Function]) -> list[str]:
    """What each of them is called, for a log that says what the order was."""
    return [func.name for func in functions]
