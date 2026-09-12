"""Finding a backend for a target triple.

The backend is imported only when it is asked for, so that starting the compiler
costs no more than the target actually in use.

A triple is registered either as canonical or as an alias for one.  Only the
canonical triples are reported to the outside, so that a tool which builds one
binary per target does not build the same binary several times under different
names.
"""

from typing import Callable, Final

from .target import Target

#: Every triple the compiler answers to, and how to build its backend.
_FACTORIES: Final[dict[str, Callable[[], Target]]] = {}

#: The subset of those triples that name a target rather than abbreviate one.
_CANONICAL: Final[list[str]] = []


def register(triple: str, factory: Callable[[], Target], *,
             canonical: bool = True) -> None:
    """Register a backend factory for *triple*."""
    _FACTORIES[triple] = factory
    if canonical and triple not in _CANONICAL:
        _CANONICAL.append(triple)


def _x86_64_factory() -> Target:
    """Build the x86-64 backend."""
    from .x86_64.target import X86_64Target

    return X86_64Target()


def _aarch64_factory() -> Target:
    """Build the AArch64 backend."""
    from .aarch64.target import AArch64Target

    return AArch64Target()


register("x86_64-linux-none", _x86_64_factory)
register("x86_64-linux", _x86_64_factory, canonical=False)
register("x86_64", _x86_64_factory, canonical=False)

def _riscv64_factory() -> Target:
    """Build the RISC-V 64-bit backend."""
    from .riscv64.target import RISCV64Target

    return RISCV64Target()


register("aarch64-linux-none", _aarch64_factory)
register("aarch64-linux", _aarch64_factory, canonical=False)
register("aarch64", _aarch64_factory, canonical=False)
register("arm64", _aarch64_factory, canonical=False)

register("riscv64-linux-none", _riscv64_factory)
register("riscv64-linux", _riscv64_factory, canonical=False)
register("riscv64", _riscv64_factory, canonical=False)

DEFAULT_TRIPLE: Final[str] = "x86_64-linux-none"


def canonical_triples() -> list[str]:
    """The triples that name a target, in the order they were registered.

    This is what a build system should ask for: one entry per target that can
    actually be generated for.
    """
    return list(_CANONICAL)


def known_triples() -> list[str]:
    """Every triple the compiler accepts, including the abbreviations."""
    return sorted(_FACTORIES)


def architecture_of(triple: str) -> str:
    """The architecture named by *triple*, which is its first component."""
    return triple.split("-", 1)[0]


def lookup(triple: str) -> Target | None:
    """Build the backend for *triple*, or return ``None`` if there is none."""
    factory = _FACTORIES.get(triple)
    return None if factory is None else factory()
