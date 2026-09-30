"""Which tests a binary runs, and what it says when one fails.

A test is an ordinary function taking nothing and answering whether it passed.
What differs between one kind and another is not the code but *which binary it
is in*: a test marked `always` is in the program and runs before the startup
function is reached, and one marked `build` or `suite` is in a binary the
compiler builds to run it and in nothing else.

That is the whole of the difference, which is why it is decided here rather than
in three back ends: each of them asks this for the list and for the message, and
emits the same shape of code around them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ir.function import Function, SpecialKind
from ..ir.module import Module
from .faults import Messages


@dataclass(frozen=True, slots=True)
class Failure:
    """What a binary says when one test answers that it did not pass."""

    #: Where the text is, and how long it is.  Both are what the helper a fault
    #: leaves through already takes, so a failing test leaves the same way: it
    #: is a program that was found to be wrong, which is what that helper is.
    symbol: str
    length: int


def run_by(module: Module) -> list[Function]:
    """The tests this binary's entry runs, in the order it runs them.

    A test binary runs what it was built to run.  The program itself runs the
    ones marked `always`, before its startup function: what they are for is to
    say that the program is fit to start, and after it has started is too late.
    """
    if module.test_plan:
        return list(module.test_plan)
    return [one for one in module.tests
            if one.attrs.special is SpecialKind.TEST_ALWAYS]


def observes(module: Module) -> bool:
    """Whether anything in the module reports and goes on.

    The helper that writes and returns is emitted where something uses it, and
    two things do: a test that did not pass, and a condition a build chose to
    observe rather than stop for.  Asked of the instructions rather than of the
    options, so that a module reaching the backend from anywhere carries what it
    needs.
    """
    from ..ir.inst import AssertInst

    return any(isinstance(inst, AssertInst) and inst.observing
               for func in module.functions.values()
               for block in func.blocks for inst in block.insts)


def failures_of(module: Module, messages: Messages) -> dict[int, Failure]:
    """The message each test reports if it fails, by the test's identity.

    Registered before anything is emitted, because whether the helper a fault
    leaves through is emitted at all is decided by whether any message was
    asked for -- and a program whose only message is a failing test needs it as
    much as one that divides by zero.
    """
    found: dict[int, Failure] = {}
    for one in run_by(module):
        text = "".join(("test ", one.name, " failed\n"))
        found[id(one)] = Failure(symbol=messages.symbol(text),
                                 length=len(text.encode("utf-8")))
    return found
