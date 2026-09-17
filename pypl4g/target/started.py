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

from ..ir.layout import DataLayout, align_of, part_offsets_of, size_of
from ..ir.module import Module
from ..ir.types import IntType, ProductType, PtrType, parts_of
from ..mc.asmbuilder import Assembler
from ..mc.symbol import SymBinding, SymKind, SymVisibility

#: The descriptors every process starts with open, in the order `std.Io` writes
#: them down: what it reads from, what it writes to, and where it reports.  The
#: numbers are the system's and are the same on every one of these targets.
INHERITED: Final[tuple[int, ...]] = (0, 1, 2)

#: What the object is called in the image, and where it goes.  It is written --
#: the program may change what it was started with, and something that arrives
#: later will be put there -- so it is the section that may be written.
SYMBOL: Final[str] = "__pl4g_init"
SECTION: Final[str] = ".data"


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


def emit(asm: Assembler, module: Module, layout: DataLayout) -> None:
    """Put the record in the image, filled in with what the process inherited.

    Laid out by the program's own declaration of the type -- the offsets come
    from the same place a field read anywhere else comes from -- so a field
    added to it is a field the entry point fills in without being told.  Every
    one of them is a descriptor, and there are as many as there are descriptors;
    a record that had grown otherwise would stop here rather than be filled in
    part way.
    """
    found = wanted_by(module)
    if found is None:
        return
    parts = parts_of(found)
    assert len(parts) == len(INHERITED), (len(parts), len(INHERITED))
    out = bytearray(size_of(found, layout))
    for number, one, offset in zip(INHERITED, parts,
                                   part_offsets_of(found, layout)):
        assert isinstance(one, IntType), one
        width = one.bits // 8
        out[offset:offset + width] = number.to_bytes(width, "little",
                                                     signed=one.signed)
    asm.section(SECTION, writable=True, alignment=align_of(found, layout))
    asm.align(align_of(found, layout))
    symbol = asm.label(SYMBOL, binding=SymBinding.LOCAL, kind=SymKind.OBJECT,
                       visibility=SymVisibility.HIDDEN)
    asm.bytes(bytes(out))
    asm.end_label(symbol)
