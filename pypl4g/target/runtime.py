"""Placing the packaged I/O runtime in an image.

The runtime is C, compiled ahead of time; what the compiler has of it is bytes,
where each name in it is, and what has to be filled in once the bytes have an
address.  This puts those bytes in the image and turns each of those into a
fixup, which is the same thing the compiler does with its own code -- so the
runtime is laid out and patched by machinery that was already there, and
nothing about it is special after this point.

It is emitted only where something reaches it, the way the allocator and the
fault helper already are, so a program that does no I/O carries none of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..ir.mangle import symbol_name
from ..mc.asmbuilder import Assembler
from ..mc.fixup import FixupKind, MCFixup
from ..mc.operand import BinExpr, ConstExpr, SymExpr
from ..mc.symbol import SymBinding, SymKind, SymVisibility
from ..runtime import Blob, Patch

#: What each piece of the runtime is called in the image: its own name with a
#: mark in front, so that a reader of the image sees where it came from and
#: nothing collides with a section the compiler makes for itself.
PREFIX = ".pl4grt"


def piece_symbol(index: int) -> str:
    """What the start of the *index*th piece is called."""
    return "".join((".Lpl4grt.", str(index)))


def reaches(module: object, runtime: "Runtime") -> bool:
    """Whether anything the program will emit calls into the runtime.

    Asked of what the functions *name*, not of what the module holds: a
    declaration nothing calls is a declaration, and a program that carried the
    runtime for one of those would carry it for nothing.
    """
    for func in getattr(module, "functions", {}).values():
        if func.is_declaration:
            continue
        for block in func.blocks:
            for inst in block.insts:
                for named in inst.references():
                    if getattr(named, "is_declaration", False) \
                            and runtime.holds(symbol_name(named)):
                        return True
    return False


@dataclass(slots=True)
class Runtime:
    """The packaged runtime, and whether anything reached it."""

    blob: Blob | None = None
    #: Whether any of it is wanted.  A name is enough to want the whole of it:
    #: the pieces are placed whole, there being no telling which bytes of one
    #: compiled function another one of them needs.
    wanted: bool = False

    def holds(self, name: str) -> bool:
        """Whether the runtime defines *name*."""
        return self.blob is not None and self.blob.holds(name)

    def reached(self) -> None:
        """Record that something in the program calls into it."""
        self.wanted = True

    def emit(self, asm: Assembler, kinds: Mapping[str, FixupKind]) -> None:
        """Put every piece into the image, with its names and its patches."""
        if not self.wanted or self.blob is None:
            return
        by_piece: dict[int, list[Patch]] = {}
        for patch in self.blob.patches:
            by_piece.setdefault(patch.piece, []).append(patch)
        inside: dict[int, dict[int, list[str]]] = {}
        for name, (piece, offset) in self.blob.symbols.items():
            inside.setdefault(piece, {}).setdefault(offset, []).append(name)
        for index, piece in enumerate(self.blob.pieces):
            asm.section("".join((PREFIX, piece.name)),
                        executable=piece.executable, writable=piece.writable,
                        alignment=piece.alignment)
            asm.align(piece.alignment)
            whole = asm.label(piece_symbol(index), binding=SymBinding.LOCAL,
                              kind=SymKind.OBJECT,
                              visibility=SymVisibility.HIDDEN)
            self._emit_piece(asm, kinds, piece.contents,
                             inside.get(index, {}), by_piece.get(index, []))
            asm.end_label(whole)

    def _emit_piece(self, asm: Assembler, kinds: Mapping[str, FixupKind],
                    contents: bytes, inside: Mapping[int, Sequence[str]],
                    patches: Sequence[Patch]) -> None:
        """Emit one piece, cut where a name falls so that each can be defined.

        A symbol is defined where emission has got to, so the bytes are handed
        over in the runs between the names in them; a patch goes with the run
        its bytes are in, and its offset is measured from there.
        """
        cuts = sorted({0, len(contents), *inside})
        opened = []
        for first, last in zip(cuts, cuts[1:]):
            for name in inside.get(first, ()):
                opened.append(asm.label(name, binding=SymBinding.LOCAL,
                                        kind=SymKind.FUNC,
                                        visibility=SymVisibility.HIDDEN))
            asm.bytes(contents[first:last],
                      [self._as_fixup(asm, kinds, one, first)
                       for one in patches if first <= one.offset < last])
        for one in opened:
            asm.end_label(one)

    def _as_fixup(self, asm: Assembler, kinds: Mapping[str, FixupKind],
                  patch: Patch, first: int) -> MCFixup:
        """One packaged patch, as the fixup the compiler already applies.

        What it measures *to* is the piece it names plus how far into it, and
        what it measures *from* is itself unless the format said otherwise --
        which is what an architecture that computes an address in two
        instructions needs, the second measuring from the first.
        """
        kind = kinds.get(patch.kind)
        if kind is None:
            raise KeyError("".join((
                "the runtime wants the fixup '", patch.kind,
                "', which this target does not have")))
        where = SymExpr(asm.symbol_named(piece_symbol(patch.target)))
        return MCFixup(offset=patch.offset - first, kind=kind,
                       target=(where if patch.addend == 0
                               else BinExpr("+", where,
                                            ConstExpr(patch.addend))),
                       base_adjust=patch.measured_from)
