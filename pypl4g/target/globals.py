"""Placing the program's variables in the image.

Every variable is given a value where it is defined, so every one of them is
initialized data: there is nothing for a section of zeroes to hold, and none is
produced.  They go in a writable section because that is what a variable is,
even while the language has no way to assign to one yet.

This is the same for every target, so it is written once.  What differs between
targets -- how an address is computed and how a value of a given width is loaded
-- is in each backend.
"""

from ..ir.layout import DataLayout, align_of, size_of
from ..ir.module import GlobalVar, Module
from ..ir.value import BoolConst, IntConst
from ..mc.asmbuilder import Assembler
from ..mc.symbol import SymBinding, SymKind

#: Where the program's variables live.
DATA_SECTION = ".data"


def emit_globals(asm: Assembler, module: Module, layout: DataLayout) -> None:
    """Emit every variable of *module* into the data section."""
    if not module.globals:
        return
    alignment = max(align_of(var.value_type, layout)
                    for var in module.globals.values())
    asm.section(DATA_SECTION, writable=True, alignment=alignment)
    for var in module.globals.values():
        asm.align(align_of(var.value_type, layout))
        symbol = asm.label(symbol_of(var),
                           binding=(SymBinding.GLOBAL if var.linkage.value == "exported"
                                    else SymBinding.LOCAL),
                           kind=SymKind.OBJECT)
        asm.bytes(initial_bytes(var, layout))
        asm.end_label(symbol)


def symbol_of(var: GlobalVar) -> str:
    """The name a variable is known by in the image.

    A variable is not overloaded on anything, so its name is its name; what
    distinguishes two functions of one name is their signature, and a variable
    has none.
    """
    if var.module:
        return "".join((var.module, ".", var.name))
    return var.name


def initial_bytes(var: GlobalVar, layout: DataLayout) -> bytes:
    """The bytes a variable starts out holding."""
    size = size_of(var.value_type, layout)
    initializer = var.initializer
    order = "little" if layout.little_endian else "big"
    match initializer:
        case IntConst():
            return (initializer.value & ((1 << (size * 8)) - 1)).to_bytes(size, order)  # type: ignore[arg-type]
        case BoolConst():
            return (1 if initializer.value else 0).to_bytes(size, order)  # type: ignore[arg-type]
        case _:
            return bytes(size)
