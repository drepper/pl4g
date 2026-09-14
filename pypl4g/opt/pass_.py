"""The pass manager.

The verifier runs after every pass, at every optimization level, because the
compiler must always perform all conformance checks.  The manager also records
how long each pass took, which is what ``--time-report`` prints.
"""

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
        """Run every pass, verifying the module after each one."""
        for item in self.passes:
            start = perf_counter()
            changed = item.run(module)
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
    if level <= 0:
        return ("largeanswers", "dropunreached")
    # Dead code is swept before that, since both of the others leave some.
    return ("largeanswers", "constfold", "simplifycfg", "dce", "dropunreached")


def build_manager(level: int) -> PassManager:
    """Build the pass manager for optimization level *level*."""
    from .passes.constfold import ConstantFolding
    from .passes.dce import DeadCodeElimination
    from .passes.dropunreached import DropUnreached
    from .passes.largeanswers import LargeAnswers
    from .passes.simplifycfg import SimplifyCFG

    available: dict[str, Pass] = {
        "constfold": ConstantFolding(),
        "dce": DeadCodeElimination(),
        "dropunreached": DropUnreached(),
        "largeanswers": LargeAnswers(),
        "simplifycfg": SimplifyCFG(),
    }
    manager = PassManager()
    for name in pipeline_for(level):
        manager.add(available[name])
    return manager
