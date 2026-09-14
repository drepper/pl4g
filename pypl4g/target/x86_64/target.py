"""The x86-64 backend."""

from __future__ import annotations

from typing import Final

from ...diag import ids as D
from ...diag.engine import DiagEngine
from ...ir.function import SYSTEM_CCONV
from ...ir.mangle import symbol_name
from ...ir.module import Module
from ...mc.asmbuilder import Assembler
from ...source.manager import SourceManager
from ...mc.desc import InstrTable
from ...mc.fixup import FixupApplier, MCFixup, apply_little_endian
from ...mc.inst import MCInst
from ...mc.reg import RegisterInfo
from ...mc.regalloc import RegisterPressureError
from ...mc.machine import clobbered_units
from ...mc.reg import RegUnit
from ...mc.streamer import MCStreamer
from ...ir.layout import DataLayout
from ..allocator import OUT_OF_MEMORY, emit_allocator, wanted_by
from ..faults import Messages
from ..pool import Constants
from ..globals import emit_globals
from ..vectors import Vectors, settle as settle_vectors
from ...ir.inst import BinOp, UnOp
from ..target import ImageDefaults
from .abi import CC_PL4G, lookup as lookup_cconv
from . import levels
from .encoder import EncodingError, encode
from .isel import UnsupportedOperation, X86Selector, lower_function
from .opcodes import X86_INSTRS
from .peephole import passes_for
from .regs import GPR, INFO, VEC
from .startup import (ALLOCATOR_REGS, ABORT_SYMBOL, ENTRY_SYMBOL,
                      SYSCALLS, emit_abort, emit_start)


#: EM_X86_64, loaded at the address a fixed-address executable conventionally
#: uses on this architecture.
#: int3.  Falling into padding traps rather than drifting into the next function.
PAD_BYTE: Final[int] = 0xCC

IMAGE_DEFAULTS: Final[ImageDefaults] = ImageDefaults(
    machine=62, base_vaddr=0x400000, page_size=0x1000, text_alignment=16,
    function_alignment=16)


class X86_64Target:
    """Code generation for x86-64."""

    #: The levels this architecture defines, oldest first, and the one a
    #: program is built for unless it says otherwise.  Everything about what
    #: they mean is in `levels`; what is here is only that this target has them.
    mclevels: tuple[str, ...] = levels.NAMES
    mclevel_default: str = levels.DEFAULT

    def __init__(self) -> None:
        self.triple: str = "x86_64-linux-none"
        self.pointer_bits: int = 64
        self.registers: RegisterInfo = INFO
        self.table = InstrTable(X86_INSTRS)
        self._mclevel: str = levels.DEFAULT

    def use_mclevel(self, name: str) -> None:
        """Generate for this level from now on.

        Checked before it is set, by whoever read the command line: what a name
        means is this target's business, so what is and is not a name is too.
        """
        assert name in levels.NAMES, name
        self._mclevel = name

    def encode(self, inst: MCInst) -> tuple[bytes, list[MCFixup]]:
        """Encode one instruction."""
        return encode(inst)

    @property
    def apply_fixup(self) -> FixupApplier:
        """A displacement occupies its whole field, so it is simply overwritten."""
        return apply_little_endian

    def selector(self, streamer: MCStreamer) -> X86Selector:
        """The instruction selector for this target."""
        del streamer
        return X86Selector(self.table)

    def image_defaults(self) -> ImageDefaults:
        """The layout constants the image writer needs."""
        return IMAGE_DEFAULTS

    def new_assembler(self, streamer: MCStreamer, opt_level: int) -> Assembler:
        """Build an assembler that emits for this target."""
        return Assembler(self.selector(streamer), streamer,
                         function_alignment=self.image_defaults().function_alignment,
                         pad_byte=PAD_BYTE,
                         machine_passes=passes_for(self.table, opt_level),
                         registers=self.registers,
                         allocation_order=CC_PL4G.orders(GPR.name, VEC.name),
                         callee_saved=CC_PL4G.callee_saved)

    @property
    def vectors(self) -> Vectors:
        """What this machine can do to a run of elements at once.

        Sixteen bytes at a time, at every level: the SSE2 integer instructions
        are part of what "x86-64" means, so this is what the oldest machine the
        architecture defines can do and there is no level to ask for it with.
        The wider registers the newer levels add are not used yet.

        The bitwise three at every lane width, since a register of bits is the
        same answer however it is divided into lanes.  Adding and subtracting at
        every lane width too, each with the check that says whether any lane went
        past.  The saturating pair only at a byte and a halfword, which are the
        widths the instructions exist for.  Multiplying is not here at all:
        seeing that a product went past wants the upper half of it, which these
        instructions do not give at every width.
        """
        every = 64
        return Vectors(
            bits=128,
            binary={BinOp.AND: every, BinOp.OR: every, BinOp.XOR: every,
                    BinOp.ADD: every, BinOp.SUB: every,
                    BinOp.SAT_ADD: 16, BinOp.SAT_SUB: 16},
            unary={UnOp.NOT: every})

    def generate(self, module: Module, asm: Assembler, diags: DiagEngine,
                 opt_level: int, sources: SourceManager | None = None) -> None:
        """Generate the whole image for *module*.

        The entry point is emitted last so that the functions it calls are
        already defined; nothing depends on that order, since emission never
        waits for a symbol, but it keeps the image in a readable order.
        """
        del opt_level
        messages = Messages()
        constants = Constants()
        layout = DataLayout(pointer_size=self.pointer_bits // 8)
        # What the front end asked of a whole run of elements at once, brought
        # down to what this machine has -- which is done to the program before a
        # single instruction is chosen, so that everything below sees only
        # operations it can emit.
        settle_vectors(module, self.vectors, layout)
        emit_globals(asm, module, layout)
        asm.section(".text", executable=True,
                    alignment=self.image_defaults().text_alignment)
        # What each function turned out to destroy, so that a call to one saves
        # only what it has to.  A function is asked after it is built, so a
        # callee built before its caller is one the caller knows about and one
        # built after is not -- which is why the to-do list wants the functions
        # sorted by the call graph.
        clobbers: dict[str, frozenset[RegUnit]] = {}
        for func in module.functions.values():
            if func.is_declaration:
                continue
            try:
                lower_function(asm, func, lookup_cconv(func.cconv), self.registers,
                               messages, sources, constants, clobbers)
                clobbers[symbol_name(func)] = clobbered_units(asm.functions[-1])
            except UnsupportedOperation as exc:
                diags.emit(D.IMPL_BACKEND_UNSUPPORTED,
                           exc.span if exc.span is not None else func.span,
                           construct=exc.detail)
                return
            except RegisterPressureError as exc:
                # Running out of registers is a limit of this compiler like any
                # other, so it is reported where the function is rather than
                # raised at whoever called it.
                diags.emit(D.IMPL_BACKEND_UNSUPPORTED, func.span,
                           construct=str(exc))
                return
            except EncodingError as exc:
                diags.emit(D.IMPL_BACKEND_UNENCODABLE,
                           exc.span if exc.span is not None else func.span,
                           detail=exc.detail)
                return
        if module.startup is None:
            return
        if wanted_by(module):
            # The allocator is emitted where something calls it and nowhere
            # else, so a program that never allocates carries none of it.
            emit_allocator(asm, SYSCALLS, ALLOCATOR_REGS, ABORT_SYMBOL,
                           messages.symbol(OUT_OF_MEMORY))
        if messages.wanted:
            # The runtime follows the system's convention whatever the
            # function that faults follows: it is written as instructions,
            # and hand-written code names its registers outright.
            emit_abort(asm, lookup_cconv(SYSTEM_CCONV))
        refused = messages.symbol(levels.described(self._mclevel)) \
            if levels.requirements(self._mclevel) else None
        emit_start(asm, module, lookup_cconv(module.startup.cconv),
                   self._mclevel, refused)
        messages.emit(asm)
        constants.emit(asm)

    @property
    def entry_symbol(self) -> str:
        """The symbol the image's entry point uses."""
        return ENTRY_SYMBOL
