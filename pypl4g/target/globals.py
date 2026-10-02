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

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ..ir.layout import (DataLayout, align_of, encode_float, encode_scalar,
                         member_offsets_of, offsets_of, size_of, stride_of,
                         tag_offset_of)
from ..ir.function import Linkage
from ..ir.module import GlobalVar, Module
from ..ir.types import (ArrayType, CharType, EnumType, ProductType,
                        ResultType, TupleType, Type, parts_of)
from ..ir.value import (AddressConst, ArrayConst, BoolConst, CharConst,
                        EnumConst, FloatConst, IntConst, PartsConst, RecordConst,
                        ResultConst)
from ..mc.fixup import ABS64, MCFixup
from ..mc.operand import SymExpr
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


def layout_for(var: GlobalVar, layout: DataLayout) -> DataLayout:
    """How *var* is laid out, which its own definition may have to decide.

    A variable something outside the image reads is laid out the way that world
    expects; every other one is laid out whichever way is better, which is the
    freedom the specification gives and which nothing outside can tell.
    """
    if not var.system_layout or layout.system:
        return layout
    return replace(layout, system=True)


def _emit_group(asm: Assembler, variables: Sequence[GlobalVar], name: str,
                writable: bool, layout: DataLayout) -> None:
    """Emit *variables* into the section *name*.

    The section is only created when something goes in it: an empty section
    would still cost a segment, and a segment costs a page.
    """
    if not variables:
        return
    alignment = max(align_of(var.value_type, layout_for(var, layout))
                    for var in variables)
    asm.section(name, writable=writable, alignment=alignment)
    for var in variables:
        asm.align(align_of(var.value_type, layout_for(var, layout)))
        visible = var.linkage is Linkage.VISIBLE
        symbol = asm.label(symbol_of(var),
                           binding=SymBinding.GLOBAL if visible else SymBinding.LOCAL,
                           kind=SymKind.OBJECT,
                           visibility=(SymVisibility.DEFAULT if visible
                                       else SymVisibility.HIDDEN))
        addresses: list[tuple[int, GlobalVar]] = []
        data = initial_bytes(var, layout_for(var, layout), addresses)
        asm.bytes(data, tuple(
            MCFixup(offset=offset, kind=ABS64,
                    target=SymExpr(asm.symbol_named(symbol_of(target))))
            for offset, target in addresses))
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


def initial_bytes(var: GlobalVar, layout: DataLayout,
                  addresses: list[tuple[int, GlobalVar]] | None = None) -> bytes:
    """The bytes a variable starts out holding.

    A value too large for the variable's type is refused, never stored with its
    upper bits dropped: a program that began with a value other than the one it
    named would not be behaving as it reads.  Where a word in it is the address
    of another variable, the word is nought and *addresses* is told where it is
    and whose address goes there.
    """
    return _encoded(var.initializer, var.value_type, layout,
                    addresses if addresses is not None else [], 0)


def _encoded(initializer: object, ty: Type, layout: DataLayout,
             addresses: list[tuple[int, GlobalVar]], at: int) -> bytes:
    """The bytes a constant occupies, laid out as its type says.

    *at* is where in the variable they go, which is what an address inside them
    is reported at.
    """
    match initializer:
        case AddressConst():
            if initializer.target is not None:
                addresses.append((at, initializer.target))
            return bytes(layout.pointer_size)
        case PartsConst():
            # Each part where it is whenever such a value is in memory: laid out
            # as a tuple of the parts would be.
            pieces = parts_of(ty)
            out = bytearray(size_of(ty, layout))
            for offset, piece, part in zip(
                    member_offsets_of(TupleType(pieces), layout), pieces,
                    initializer.parts):
                bits = _encoded(part, piece, layout, addresses, at + offset)
                out[offset:offset + len(bits)] = bits
            return bytes(out)
        case IntConst():
            return encode_scalar(initializer.value, ty, layout)
        case BoolConst():
            return encode_scalar(1 if initializer.value else 0, ty, layout)
        case FloatConst():
            return encode_float(initializer.value, ty, layout)
        case CharConst() if isinstance(ty, CharType):
            # The number Unicode gave the code point, written as the type it is
            # held as says to write it.
            return encode_scalar(initializer.value, ty.holder, layout)
        case EnumConst() if isinstance(ty, EnumType):
            # Which value it is, written as the number it is stored as.  The
            # numbering is the representation's business and lives in one place.
            return encode_scalar(initializer.number, ty.holder, layout)
        case ArrayConst() if isinstance(ty, ArrayType):
            # Element after element, each where the stride puts it.  Nothing
            # says how many there are: the type does, and a reader of the image
            # who knows the type knows where each one is.
            step = stride_of(ty.element, layout)
            out = bytearray(size_of(ty, layout))
            for index, element in enumerate(initializer.elements):
                written = _encoded(element, ty.element, layout, addresses,
                                   at + index * step)
                out[index * step:index * step + len(written)] = written
            return bytes(out)
        case RecordConst() if isinstance(ty, ProductType):
            # Each field where `offsets_of` puts it, and whatever padding lies
            # between them left as zeroes.  The one thing that says where a
            # field went says it here too, so what an image holds and what a
            # program reads out of it cannot drift.
            out = bytearray(size_of(ty, layout))
            for offset, (written, (_, held)) in zip(
                    offsets_of(ty, layout),
                    zip(initializer.fields, ty.fields)):
                bits = _encoded(written, held, layout, addresses, at + offset)
                out[offset:offset + len(bits)] = bits
            return bytes(out)
        case ResultConst() if isinstance(ty, ResultType):
            # The answer where an answer goes, the truth value where the layout
            # says, and whatever is between and after them left as zeroes --
            # padding a program cannot read and so cannot tell from anything.
            out = bytearray(size_of(ty, layout))
            answer = _encoded(initializer.answer, ty.ok, layout, addresses, at)
            out[:len(answer)] = answer
            out[tag_offset_of(ty, layout)] = 1 if initializer.failed else 0
            return bytes(out)
        case _:
            return bytes(size_of(ty, layout))
