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
from typing import Final, Protocol, Sequence

from ..ir.module import Module
from ..ir.function import Function
from ..ir.verify import verify, verifying_asked


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


#: How many times folding and simplifying are taken in turn at most.  Each round
#: is a level of a chain of branches settled by what the one before settled, and
#: chains that long are not written.
SETTLE_ROUNDS: Final = 8


@dataclass(slots=True)
class Settle:
    """Folding and simplifying the graph in turn, and writing into a function
    what its callers hand it alike, until none of them changes anything: each
    opens work for the others."""

    folding: Pass
    simplifying: Pass
    knowing: Pass
    name: str = "settle"
    #: The functions any of the three changed, or nothing where one of them
    #: could not say.
    touched: list[Function] | None = None

    def run(self, module: Module) -> bool:
        """Run the three until all are done; report whether anything changed."""
        found: dict[int, Function] = {}
        whole = False
        for _ in range(SETTLE_ROUNDS):
            round_ = False
            for one in (self.folding, self.simplifying, self.knowing):
                if one.run(module):
                    round_ = True
                    said = _touched(one)
                    if said is None:
                        whole = True
                    else:
                        found.update((id(func), func) for func in said)
            if not round_:
                break
        self.touched = None if whole else list(found.values())
        return whole or bool(found)


@dataclass(slots=True)
class PassManager:
    """Runs a sequence of passes over a module."""

    passes: list[Pass] = field(default_factory=list)
    timings: list[PassTiming] = field(default_factory=list)
    #: Whether the module is verified after each pass that changed it, and then
    #: only the functions that pass changed: a debugging aid, which `--verify`
    #: and the test suite ask for and an ordinary compilation goes without.
    verifying: bool = field(default_factory=verifying_asked)

    def add(self, item: Pass) -> None:
        """Append a pass to the sequence."""
        self.passes.append(item)

    def run(self, module: Module) -> None:
        """Run every pass, verifying what each one changed where that is asked
        for -- and the whole module after the last."""
        for item in self.passes:
            start = perf_counter()
            changed = item.run(module)
            self.timings.append(PassTiming(item.name, perf_counter() - start, changed))
            if not self.verifying:
                continue
            if item is self.passes[-1]:
                verify(module)
            elif changed:
                verify(module, _touched(item))


def _touched(item: Pass) -> list[Function] | None:
    """The functions *item* says its last run changed, or nothing where it does
    not say -- in which case every function is to be checked."""
    return getattr(item, "touched", None)


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
    # `largeanswers` first answers a string without its allocator where the
    # function fixed it, which is the same question -- how an answer travels --
    # and is no optimization either: it is the ABI the checker decided on.
    # It runs before `largeanswers`, which is what asks the question about the
    # program as written: after that pass a call of the wrong shape writes the
    # caller's storage and is not droppable at all.
    # And putting a block on an edge that has moves to make is not one either:
    # it is a shape the machine needs rather than anything the program asked
    # for, so it runs whatever was asked for.  It goes last, after everything
    # that could make such an edge and after everything that might otherwise
    # take the block it puts there straight back out again.
    # What nothing reaches is dropped right after that, before anything else
    # looks at it: a module brings every function it defines, and a pass that
    # works on one nothing calls is work on bytes no image will hold.  After
    # `dropignored` and not before, so that what it reports about a function is
    # reported whether or not anything calls the function.  It runs again at the
    # end, inlining being what leaves more of them behind.
    if level <= 0:
        return ("dropignored", "dropunreached", "tailcalls", "largeanswers",
                "coroutines", "leanvalues", "dropunreached", "splitedges")
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
    # Values worked out twice are shared after the first folding, which is
    # what makes two of them the same -- an address at an index times one is
    # the index once the one is gone -- and before the second, which then sees
    # a length compared with itself.  What a branch settled is used after that,
    # once the values it compares are the ones the checks compare.
    # What `facts` settles is folded and simplified again, and the two of them
    # until neither finds anything: a branch settled leaves a block with one
    # way in, whose parameter is then what that way carries -- a tuple made in
    # a caller inlined, whose parts then fold, settling the next branch.  With
    # them goes writing into a function what every caller hands it alike, which
    # a fold in a caller can be what makes alike.
    # A division by a constant is made multiplications and shifts once the
    # graph is simple and before the second folding, which reads through the
    # result that never fails it leaves; and before values worked out twice
    # are shared again, which is what makes `x ÷ 10` and `x % 10` one product.
    # A branch on a condition made of parts is made a branch on each part
    # before `facts` looks, so that what each part settled is known on the
    # edge it settled it on -- `at < n` where `bytes⟦at⟧` is read -- and
    # before `settle`, which removes the blocks it leaves nothing reaching.
    # A generator's body is split at its stops as soon as its calls have the
    # shape they keep, and before anything else looks at it: nothing below
    # this pass knows what a stop is, and every pass after it sees ordinary
    # functions.
    return ("dropignored", "dropunreached", "tailcalls", "largeanswers",
            "coroutines", "inline",
            "leanvalues",
            "constfold", "samevalues", "purecalls",
            "simplifycfg", "divisions",
            "constfold", "samevalues", "conditions", "facts", "settle", "dce",
            "dropunreached", "splitedges")


def build_manager(level: int, verifying: bool | None = None) -> PassManager:
    """Build the pass manager for optimization level *level*."""
    from .passes.conditions import BranchConditions
    from .passes.constfold import ConstantFolding
    from .passes.coroutines import Coroutines
    from .passes.dce import DeadCodeElimination
    from .passes.divisions import DivisionByConstants
    from .passes.dropignored import DropIgnoredCalls
    from .passes.dropunreached import DropUnreached
    from .passes.facts import Facts
    from .passes.inline import Inlining
    from .passes.largeanswers import LargeAnswers
    from .passes.leanvalues import LeanValues
    from .passes.purecalls import PureCalls
    from .passes.samevalues import SameValues
    from .passes.simplifycfg import SimplifyCFG
    from .passes.splitedges import SplitEdges
    from .passes.knownargs import KnownArguments
    from .passes.tailcalls import TailCalls

    available: dict[str, Pass] = {
        "conditions": BranchConditions(),
        "constfold": ConstantFolding(),
        "coroutines": Coroutines(),
        "purecalls": PureCalls(),
        "dce": DeadCodeElimination(),
        "divisions": DivisionByConstants(),
        "dropignored": DropIgnoredCalls(),
        "dropunreached": DropUnreached(),
        "facts": Facts(),
        "samevalues": SameValues(),
        "inline": Inlining(),
        "largeanswers": LargeAnswers(),
        "leanvalues": LeanValues(),
        "simplifycfg": SimplifyCFG(),
        "settle": Settle(ConstantFolding(), SimplifyCFG(), KnownArguments()),
        "splitedges": SplitEdges(),
        "tailcalls": TailCalls(),
    }
    manager = PassManager() if verifying is None \
        else PassManager(verifying=verifying)
    for name in pipeline_for(level):
        manager.add(available[name])
    return manager
