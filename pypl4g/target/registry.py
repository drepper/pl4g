"""Finding a backend for a target triple.

The backend is imported only when it is asked for, so that starting the compiler
costs no more than the target actually in use.
"""

from typing import Callable, Final

from .target import Target

#: The triples the compiler knows, and how to build the backend for each.
_FACTORIES: Final[dict[str, Callable[[], Target]]] = {}


def register(triple: str, factory: Callable[[], Target]) -> None:
    """Register a backend factory for *triple*."""
    _FACTORIES[triple] = factory


def _x86_64_factory() -> Target:
    """Build the x86-64 backend."""
    from .x86_64.target import X86_64Target

    return X86_64Target()


register("x86_64-linux-none", _x86_64_factory)
register("x86_64-linux", _x86_64_factory)
register("x86_64", _x86_64_factory)

DEFAULT_TRIPLE: Final[str] = "x86_64-linux-none"


def known_triples() -> list[str]:
    """Every triple with a backend."""
    return sorted(_FACTORIES)


def lookup(triple: str) -> Target | None:
    """Build the backend for *triple*, or return ``None`` if there is none."""
    factory = _FACTORIES.get(triple)
    return None if factory is None else factory()
