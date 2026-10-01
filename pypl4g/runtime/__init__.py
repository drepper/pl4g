"""The I/O runtime, compiled ahead of time and packaged with the compiler.

It is written in C, in `runtime/`, and compiled once for every architecture the
compiler generates for.  What is packaged is not the C and not an object file
but the *code and its relocations*, extracted by `bin/pl4g-runtime` into a
generated module per architecture -- so building a program needs no C compiler
and the compiler needs no reader for relocatable objects.

What the language reaches it through is a function marked `@[external]`, which
follows the system's calling convention, and a record marked `@[abi]`, which is
laid out the way C lays one out.  Nothing else about it is visible: a program
names a descriptor and calls `read` or `write`.

A blob is placed in an image only where something reaches it, the way the
allocator and the fault helper already are, so a program that does no I/O
carries none of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Piece:
    """One section of the compiled runtime, as bytes and how to place them."""

    #: What the object called it, which is what a reader of the image sees.
    name: str
    contents: bytes
    alignment: int
    writable: bool = False
    executable: bool = False


@dataclass(frozen=True, slots=True)
class Patch:
    """One place in a piece to fill in once the pieces have addresses.

    `kind` names a fixup the target already has, by the name in its own table:
    the extractor maps the object format's relocation types onto them, so what
    the compiler does with one is what it already does with its own.
    """

    #: Which piece holds the bytes to patch, and where in it.
    piece: int
    offset: int
    kind: str
    #: Which piece the value is measured to, and how far into it.
    target: int
    addend: int = 0
    #: How far from this patch the value is measured, where that is not from
    #: the patch itself.  RISC-V computes an address in two instructions and
    #: the second measures from the first, which is the one thing that needs
    #: this; everywhere else it is nought.
    measured_from: int = 0


@dataclass(frozen=True, slots=True)
class Blob:
    """The whole of the runtime for one architecture."""

    triple: str
    pieces: tuple[Piece, ...]
    #: Where each name the language may reach is: which piece, and how far in.
    symbols: Mapping[str, tuple[int, int]]
    patches: tuple[Patch, ...]

    def holds(self, name: str) -> bool:
        """Whether this runtime defines *name*."""
        return name in self.symbols


def blob_for(architecture: str) -> Blob | None:
    """The runtime built for *architecture*, or nothing where there is none."""
    from importlib import import_module
    try:
        module = import_module("".join((__name__, ".", architecture)))
    except ModuleNotFoundError:
        return None
    found = getattr(module, "BLOB", None)
    return found if isinstance(found, Blob) else None


#: The name of the run of numbers the runtime says its shared record is.  Not
#: something the language may call -- it is bytes and not code -- so it is kept
#: apart from the names `@[external]` accepts.
SHAPE_SYMBOL: str = "pl4g_io_shape"


def shape_of(blob: Blob) -> tuple[int, ...]:
    """How big the shared record is, how it is aligned, and each field's place.

    Where a field is *and* how wide it is: where alone would not do, a field
    made narrower being able to leave every offset where it was -- padding takes
    up what it gave back -- and a reader of the wrong width being exactly what
    this is here to catch.

    Read out of the bytes the runtime carries rather than written down twice:
    the C says it once and this reads what the C said.  A test works the same
    numbers out for the record the `std` module declares, which is what keeps
    two declarations in two languages from drifting apart silently.
    """
    found = blob.symbols.get(SHAPE_SYMBOL)
    if found is None:
        return ()
    piece, offset = found
    held = blob.pieces[piece].contents
    # Every number is a machine word, little-endian, which is what all three of
    # these architectures are.
    words = 2 * len(_RING_FIELDS) + 2
    return tuple(int.from_bytes(held[offset + 8 * at:offset + 8 * at + 8],
                                "little")
                 for at in range(words))


#: The fields of the shared record, in the order the runtime lists them.  The
#: names are the language's; what they are called in the C is the C's business
#: and is not compared.
_RING_FIELDS: tuple[str, ...] = (
    "state", "fd", "sq_head", "sq_tail", "sq_mask", "sq_array", "sqes",
    "cq_head", "cq_tail", "cq_mask", "cqes", "held", "answer")


def ring_fields() -> Sequence[str]:
    """The fields the shared record has, in the order the runtime lists them."""
    return _RING_FIELDS


def names() -> Sequence[str]:
    """Every name the runtime defines, which is the same set everywhere.

    Asked of the architecture the compiler is building for; that they agree is
    what the test that rebuilds them checks.
    """
    return _NAMES


#: What `runtime/io.c` defines and the `std` module and the compiler reach.
#: Written down here as well as in the C so that a program naming one the runtime
#: does not have is refused where it is written rather than when an image is laid
#: out.
_NAMES: tuple[str, ...] = ("pl4g_args", "pl4g_env", "pl4g_heap_alloc",
                          "pl4g_heap_free", "pl4g_io_drain", "pl4g_io_submit",
                          "pl4g_io_wait")
