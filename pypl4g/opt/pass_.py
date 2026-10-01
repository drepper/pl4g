"""The pass manager.

The verifier runs after every pass that changed something, at every optimization
level, and after the last pass whatever it says -- so the module that reaches the
backend has always been verified, and so has every module a pass handed on.  A
pass that changed nothing would be verifying what the verifier passed the last
time it ran, which on the larger programs is most of the verifying that was being
done: several of the passes change nothing on most programs, and one of them --
the one that puts a block on an edge -- changes nothing on any program that
exists today.

What it gives up is which pass to blame: a pass that mutates the module and
reports that it did not would be found by the next pass that does report one,
and named as the culprit.  That is a defect in a pass either way, and the timing
report would already be lying about it.

The manager also records how long each pass took, which is what ``--time-report``
prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol, Sequence

from ..ir.module import Module
from ..ir.verify import verify


class Pass(Protocol):
    """Anything that transforms a module in place."""

    name: str

    def run(self, module: Module) -> bool:
        """Transform *module*, returning whether anything changed."""
        ...


@dataclass(slots=True)
class PassTiming:
    """How long one pass took and whether it changed anything."""

    name: str
    seconds: float
    changed: bool


@dataclass(slots=True)
class PassManager:
    """Runs a sequence of passes over a module."""

    passes: list[Pass] = field(default_factory=list)
    timings: list[PassTiming] = field(default_factory=list)

    def add(self, item: Pass) -> None:
        """Append a pass to the sequence."""
        self.passes.append(item)

    def run(self, module: Module) -> None:
        """Run every pass, verifying the module after each one that changed it."""
        for item in self.passes:
            start = perf_counter()
            changed = item.run(module)
            if changed or item is self.passes[-1]:
                verify(module)
            self.timings.append(PassTiming(item.name, perf_counter() - start, changed))


def pipeline_for(level: int) -> Sequence[str]:
    """The names of the passes to run at optimization level *level*."""
    # Dropping what nothing can reach is not an optimization and so does not
    # wait for one to be asked for: compilation covers the whole program, so a
    # function nothing reaches is one nothing can ever call, and keeping it
    # would put bytes in the image that no program can run.  It goes last,
    # since a pass before it can be what makes something unreachable.
    # Answering through the caller's storage is not an optimization either: it
    # is how a function of that shape is called, so it runs whatever was asked
    # for, and it runs first, the passes after it seeing the calls as they will
    # be rather than as they were written.
    # A call whose answer the program said it did not want is not an
    # optimization either, and for the same reason: what it acts on is not
    # something the compiler noticed but something the program said, so a reader
    # who wrote `_ \N{LEFTWARDS ARROW}` is told the same thing at every level.  It goes before the
    # rest so that it is what reports the call, whatever else runs afterwards.
    # Answering a string without its allocator, where the function fixed it, is
    # an ABI the checker decided on and not an optimization: it runs at every
    # level, before `largeanswers` asks how many words an answer is.
    # It runs before `largeanswers`, which is what asks the question about the
    # program as written: after that pass a call of the wrong shape writes the
    # caller's storage and is not droppable at all.
    # And putting a block on an edge that has moves to make is not one either:
    # it is a shape the machine needs rather than anything the program asked
    # for, so it runs whatever was asked for.  It goes last, after everything
    # that could make such an edge and after everything that might otherwise
    # take the block it puts there straight back out again.
    if level <= 0:
        return ("dropignored", "thinanswers", "largeanswers", "dropunreached",
                "splitedges")
    # Inlining goes after the two that are not optimizations and before the
    # rest: what it leaves behind is a call gone and a body in its place, which
    # is what folding, simplifying and sweeping are for -- and a function
    # nothing calls any more, which the last pass drops.
    # Dead code is swept before that, since both of the others leave some.
    # Folding runs twice, and the second time is not belt and braces: simplifying
    # the graph is what turns a block's parameter into the one value every way in
    # carries, so a body inlined with a constant argument only *becomes* constant
    # after that pass -- and a bitcast of a constant is a value the back end has
    # nowhere to read, a bitcast emitting no instruction of its own.
    return ("dropignored", "thinanswers", "largeanswers", "inline", "constfold",
            "simplifycfg",
            "constfold", "dce", "dropunreached", "splitedges")


def build_manager(level: int) -> PassManager:
    """Build the pass manager for optimization level *level*."""
    from .passes.constfold import ConstantFolding
    from .passes.dce import DeadCodeElimination
    from .passes.dropignored import DropIgnoredCalls
    from .passes.dropunreached import DropUnreached
    from .passes.inline import Inlining
    from .passes.largeanswers import LargeAnswers
    from .passes.simplifycfg import SimplifyCFG
    from .passes.splitedges import SplitEdges
    from .passes.thinanswers import ThinAnswers

    available: dict[str, Pass] = {
        "constfold": ConstantFolding(),
        "dce": DeadCodeElimination(),
        "dropignored": DropIgnoredCalls(),
        "dropunreached": DropUnreached(),
        "inline": Inlining(),
        "largeanswers": LargeAnswers(),
        "simplifycfg": SimplifyCFG(),
        "splitedges": SplitEdges(),
        "thinanswers": ThinAnswers(),
    }
    manager = PassManager()
    for name in pipeline_for(level):
        manager.add(available[name])
    return manager
