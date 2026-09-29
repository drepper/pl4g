"""Finding a backend for a target triple.

The backend is imported only when it is asked for, so that starting the compiler
costs no more than the target actually in use.

A triple is registered either as canonical or as an alias for one.  Only the
canonical triples are reported to the outside, so that a tool which builds one
binary per target does not build the same binary several times under different
names.
"""

from __future__ import annotations

from typing import Callable, Final

from .target import Target

#: Every triple the compiler answers to, and how to build its backend.
_FACTORIES: Final[dict[str, Callable[[], Target]]] = {}

#: The subset of those triples that name a target rather than abbreviate one.
_CANONICAL: Final[list[str]] = []

#: And what each of them calls the conventions it knows.  It is registered
#: beside the factory rather than found from the triple, because a triple and
#: the name of its backend need not agree -- `arm64` is `aarch64` -- and because
#: what asks is the front end, which has no other use for a backend.
_CONVENTIONS: Final[dict[str, Callable[[], frozenset[str]]]] = {}


def register(triple: str, factory: Callable[[], Target], *,
             canonical: bool = True,
             conventions: Callable[[], frozenset[str]] | None = None) -> None:
    """Register a backend factory for *triple*."""
    _FACTORIES[triple] = factory
    if conventions is not None:
        _CONVENTIONS[triple] = conventions
    if canonical and triple not in _CANONICAL:
        _CANONICAL.append(triple)


def _x86_64_factory() -> Target:
    """Build the x86-64 backend."""
    from .x86_64.target import X86_64Target

    return X86_64Target()


def _x86_64_conventions() -> frozenset[str]:
    """What x86-64 calls the conventions it knows."""
    from .x86_64.abi import CONVENTIONS

    return frozenset(CONVENTIONS)


def _aarch64_factory() -> Target:
    """Build the AArch64 backend."""
    from .aarch64.target import AArch64Target

    return AArch64Target()


def _aarch64_conventions() -> frozenset[str]:
    """And what AArch64 calls them."""
    from .aarch64.abi import CONVENTIONS

    return frozenset(CONVENTIONS)


register("x86_64-linux-none", _x86_64_factory, conventions=_x86_64_conventions)
register("x86_64-linux", _x86_64_factory, canonical=False, conventions=_x86_64_conventions)
register("x86_64", _x86_64_factory, canonical=False, conventions=_x86_64_conventions)

def _riscv64_factory() -> Target:
    """Build the RISC-V 64-bit backend."""
    from .riscv64.target import RISCV64Target

    return RISCV64Target()


def _riscv64_conventions() -> frozenset[str]:
    """And RISC-V."""
    from .riscv64.abi import CONVENTIONS

    return frozenset(CONVENTIONS)


register("aarch64-linux-none", _aarch64_factory, conventions=_aarch64_conventions)
register("aarch64-linux", _aarch64_factory, canonical=False, conventions=_aarch64_conventions)
register("aarch64", _aarch64_factory, canonical=False, conventions=_aarch64_conventions)
register("arm64", _aarch64_factory, canonical=False, conventions=_aarch64_conventions)

register("riscv64-linux-none", _riscv64_factory, conventions=_riscv64_conventions)
register("riscv64-linux", _riscv64_factory, canonical=False, conventions=_riscv64_conventions)
register("riscv64", _riscv64_factory, canonical=False, conventions=_riscv64_conventions)

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


def conventions_of(triple: str) -> frozenset[str] | None:
    """Every calling convention the backend for *triple* knows.

    Nothing where no backend answers to the triple.  Only the architecture's
    table of conventions is imported and not its code generator: what asks is
    the front end, and a language server that checked a file should not pay for
    a backend it will never run.
    """
    found = _CONVENTIONS.get(triple)
    return None if found is None else found()


def lookup(triple: str) -> Target | None:
    """Build the backend for *triple*, or return ``None`` if there is none."""
    factory = _FACTORIES.get(triple)
    return None if factory is None else factory()
