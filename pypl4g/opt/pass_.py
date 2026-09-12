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
    if level <= 0:
        return ()
    # Dead code is swept last, since both of the others can leave some behind.
    return ("constfold", "simplifycfg", "dce")


def build_manager(level: int) -> PassManager:
    """Build the pass manager for optimization level *level*."""
    from .passes.constfold import ConstantFolding
    from .passes.dce import DeadCodeElimination
    from .passes.simplifycfg import SimplifyCFG

    available: dict[str, Pass] = {
        "constfold": ConstantFolding(),
        "dce": DeadCodeElimination(),
        "simplifycfg": SimplifyCFG(),
    }
    manager = PassManager()
    for name in pipeline_for(level):
        manager.add(available[name])
    return manager
