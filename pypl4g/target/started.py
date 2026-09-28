"""What a program is started with, and where the entry point puts it.

The startup function may take a reference to the record the `std` module calls
`Init`, whose fields hold the three devices a process inherits.  What the image
carries is the record itself -- an object in the writable data, laid out the way
the program's own declaration of it says -- and what the entry point hands over
is where that object is.

A reference and not the record: it is what lets the record grow, as the
arguments and the environment will make it grow, without the one signature every
program writes changing; and it is what lets the program write into it, which a
value passed in registers could not be made to do.
"""

from __future__ import annotations

from typing import Final

from ..ir.layout import (DataLayout, align_of, offsets_of, part_offsets_of,
                         size_of)
from ..ir.module import Module
from ..ir.types import IntType, ProductType, PtrType, Type, parts_of
from ..mc.asmbuilder import Assembler
from ..mc.symbol import SymBinding, SymKind, SymVisibility

#: The descriptors every process starts with open, in the order `std.Io` writes
#: them down: what it reads from, what it writes to, and where it reports.  The
#: numbers are the system's and are the same on every one of these targets.
INHERITED: Final[tuple[int, ...]] = (0, 1, 2)

#: What the object is called in the image, and where it goes.  It is written --
#: the program may change what it was started with, and the entry point fills
#: part of it in before anything runs -- so it is the section that may be
#: written.
SYMBOL: Final[str] = "__pl4g_init"
SECTION: Final[str] = ".data"

#: The field holding the devices, whose every part is one of them, and the field
#: holding the words the program was named with.  Found by name: the record is
#: the program's own declaration and what the entry point knows about it is
#: which fields it has to reach, not where they are.
DEVICES: Final[str] = "io"
ARGUMENTS: Final[str] = "args"
#: And the field holding the environment, which is a dictionary and so is not
#: the runtime's to make: what fills it is a function of `std` that the entry
#: point calls, the runtime handing over the strings and the language building
#: the table out of them.
ENVIRONMENT: Final[str] = "env"

#: What the runtime is called that reads the arguments off the stack, and what
#: the one that reads the environment off it is called.  The second answers
#: with the strings rather than writing them anywhere: what is made of them is
#: a table, which only the language can build.
READS_ARGUMENTS: Final[str] = "pl4g_args"
READS_ENVIRONMENT: Final[str] = "pl4g_env"


def wanted_by(module: Module) -> ProductType | None:
    """The record the startup function takes a reference to, where it takes one.

    Nothing where the program wrote no parameter, which is most of them: a
    program that wants neither the devices it inherited nor anything else that
    came with it is started with nothing handed over.
    """
    startup = module.startup
    if startup is None or not startup.ty.params:
        return None
    held = startup.ty.params[0]
    assert isinstance(held, PtrType), held
    found = held.pointee
    assert isinstance(found, ProductType), found
    return found


def where_in(found: ProductType, name: str,
             layout: DataLayout) -> tuple[int, Type]:
    """How far into the record the field *name* lies, and what it is."""
    for at, (called, held) in enumerate(found.fields):
        if called == name:
            return offsets_of(found, layout)[at], held
    raise KeyError("".join((
        "the record a program is started with has no '", name,
        "'; ", SYMBOL, " is filled in by name and not by position")))


def arguments_at(module: Module, layout: DataLayout) -> int | None:
    """How far into the record the words it was named with go.

    Nothing where the program takes no such record.  What is there is the two
    words a run of strings is -- where they are and how many -- which the
    runtime fills in before the startup function is reached.
    """
    found = wanted_by(module)
    if found is None:
        return None
    return where_in(found, ARGUMENTS, layout)[0]


def environment_at(module: Module, layout: DataLayout) -> int | None:
    """How far into the record the environment goes.

    Nothing where the program takes no such record, where the record it takes
    has no such field, or where nothing in the program can make the table: the
    three are one question -- is this a program whose entry point has an
    environment to hand over -- and one answer.
    """
    found = wanted_by(module)
    if found is None or module.environment is None \
            or not module.reads_environment \
            or not has_field(found, ENVIRONMENT):
        return None
    return where_in(found, ENVIRONMENT, layout)[0]


def has_field(found: ProductType, name: str) -> bool:
    """Whether the record has a field of this name."""
    return any(called == name for called, _ in found.fields)


def entry_wants_runtime(module: Module) -> bool:
    """Whether the entry point itself calls into the packaged runtime.

    It does where the program asks for a stack of its own, and where it takes
    the record it was started with, which the entry point arranges by calling,
    in instructions it writes rather than in anything the program wrote.  So it
    is asked here: nothing walking the program's own calls would see it.

    The stack a program makes for itself is not one of these.  It was, while the
    making of it was compiled from C; it is now a few dozen instructions the
    entry point selects like any others, so a program that wants nothing else
    from the packaged runtime carries none of it.
    """
    return wanted_by(module) is not None


def emit(asm: Assembler, module: Module, layout: DataLayout) -> None:
    """Put the record in the image, with the descriptors filled in.

    Laid out by the program's own declaration of the type -- the offsets come
    from the same place a field read anywhere else comes from -- so a field
    added to it is one the entry point reaches without being told where.  What
    is written here is the descriptors, whose numbers are known before anything
    runs; everything else starts as nought, and what the runtime fills in it
    fills in when the program starts.
    """
    found = wanted_by(module)
    if found is None:
        return
    out = bytearray(size_of(found, layout))
    start, devices = where_in(found, DEVICES, layout)
    assert isinstance(devices, ProductType), devices
    parts = parts_of(devices)
    assert len(parts) == len(INHERITED), (len(parts), len(INHERITED))
    for number, one, offset in zip(INHERITED, parts,
                                   part_offsets_of(devices, layout)):
        assert isinstance(one, IntType), one
        width = one.bits // 8
        out[start + offset:start + offset + width] = \
            number.to_bytes(width, "little", signed=one.signed)
    asm.section(SECTION, writable=True, alignment=align_of(found, layout))
    asm.align(align_of(found, layout))
    symbol = asm.label(SYMBOL, binding=SymBinding.LOCAL, kind=SymKind.OBJECT,
                       visibility=SymVisibility.HIDDEN)
    asm.bytes(bytes(out))
    asm.end_label(symbol)
