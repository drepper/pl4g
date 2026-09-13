"""Placing the program's variables in the image.

Every variable is given a value where it is defined, so every one of them is
initialized data: there is nothing for a section of zeroes to hold, and none is
produced.

Where a variable goes follows from its type.  One declared ``mut`` can be
assigned to, so it needs a writable section; one that is not is a constant for
the whole run of the program, and putting it in a read-only section is what
makes that guarantee hold against the program itself and not merely against the
type checker.  The two kinds cannot share a section, because a segment carries
one set of permissions for everything mapped through it.

This is the same for every target, so it is written once.  What differs between
targets -- how an address is computed and how a value of a given width is loaded
-- is in each backend.
"""

from collections.abc import Sequence

from ..ir.layout import (DataLayout, align_of, encode_float, encode_scalar,
                         size_of)
from ..ir.function import Linkage
from ..ir.module import GlobalVar, Module
from ..ir.value import BoolConst, FloatConst, IntConst
from ..mc.asmbuilder import Assembler
from ..mc.symbol import SymBinding, SymKind, SymVisibility

#: Where a variable lives that the program can assign to.
DATA_SECTION = ".data"

#: Where a variable lives that nothing can assign to.
RODATA_SECTION = ".rodata"


def emit_globals(asm: Assembler, module: Module, layout: DataLayout) -> None:
    """Emit every variable of *module* into the section its type calls for."""
    variables = list(module.globals.values())
    _emit_group(asm, [v for v in variables if not v.mutable], RODATA_SECTION,
                False, layout)
    _emit_group(asm, [v for v in variables if v.mutable], DATA_SECTION, True, layout)


def _emit_group(asm: Assembler, variables: Sequence[GlobalVar], name: str,
                writable: bool, layout: DataLayout) -> None:
    """Emit *variables* into the section *name*.

    The section is only created when something goes in it: an empty section
    would still cost a segment, and a segment costs a page.
    """
    if not variables:
        return
    alignment = max(align_of(var.value_type, layout) for var in variables)
    asm.section(name, writable=writable, alignment=alignment)
    for var in variables:
        asm.align(align_of(var.value_type, layout))
        visible = var.linkage is Linkage.VISIBLE
        symbol = asm.label(symbol_of(var),
                           binding=SymBinding.GLOBAL if visible else SymBinding.LOCAL,
                           kind=SymKind.OBJECT,
                           visibility=(SymVisibility.DEFAULT if visible
                                       else SymVisibility.HIDDEN))
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
    """The bytes a variable starts out holding.

    A value too large for the variable's type is refused, never stored with its
    upper bits dropped: a program that began with a value other than the one it
    named would not be behaving as it reads.
    """
    initializer = var.initializer
    match initializer:
        case IntConst():
            return encode_scalar(initializer.value, var.value_type, layout)
        case BoolConst():
            return encode_scalar(1 if initializer.value else 0, var.value_type, layout)
        case FloatConst():
            return encode_float(initializer.value, var.value_type, layout)
        case _:
            return bytes(size_of(var.value_type, layout))
