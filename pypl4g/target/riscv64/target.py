"""The RISC-V 64-bit backend."""

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
from ...mc.fixup import FixupApplier, MCFixup
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
from ..target import ImageDefaults
from .abi import CC_PL4G, lookup as lookup_cconv
from .encoder import EncodingError, encode
from .fixups import apply_fixup
from .isel import RVSelector, UnsupportedOperation, lower_function
from .opcodes import PAD_BYTE, RISCV_INSTRS
from .regs import FPR, GPR, INFO
from .startup import (ALLOCATOR_REGS, ABORT_SYMBOL, ENTRY_SYMBOL,
                      SYSCALLS, emit_abort, emit_start)

#: What the header's flag word says on this architecture.  The low bit says the
#: code uses the compressed encoding, the two above it say which floating-point
#: convention its functions follow, and the rest are reserved.  DOUBLE is what
#: is emitted: a floating-point value is passed and returned in a
#: floating-point register of the hardware, which is what the requirement to
#: assume the hardware's floating point comes to on this architecture, and a
#: program that links against one convention with the other is what the field
#: exists to prevent.  It says DOUBLE whether or not a particular program uses
#: floating point, because what the field states is the convention the
#: functions follow and they follow that one either way.
FLOAT_ABI_SOFT: Final[int] = 0x0
FLOAT_ABI_SINGLE: Final[int] = 0x2
FLOAT_ABI_DOUBLE: Final[int] = 0x4
COMPRESSED: Final[int] = 0x1

#: EM_RISCV.
IMAGE_DEFAULTS: Final[ImageDefaults] = ImageDefaults(
    machine=243, base_vaddr=0x400000, page_size=0x1000, text_alignment=16,
    function_alignment=16, header_flags=FLOAT_ABI_DOUBLE)


class RISCV64Target:
    """Code generation for RISC-V, sixty-four bit."""

    def __init__(self) -> None:
        self.triple: str = "riscv64-linux-none"
        self.pointer_bits: int = 64
        self.registers: RegisterInfo = INFO
        self.table = InstrTable(RISCV_INSTRS)

    def encode(self, inst: MCInst) -> tuple[bytes, list[MCFixup]]:
        """Encode one instruction."""
        return encode(inst)

    @property
    def apply_fixup(self) -> FixupApplier:
        """A jump offset is scattered through its word, so it is inserted."""
        return apply_fixup

    def selector(self, streamer: MCStreamer) -> RVSelector:
        """The instruction selector for this target."""
        del streamer
        return RVSelector(self.table)

    def image_defaults(self) -> ImageDefaults:
        """The layout constants the image writer needs."""
        return IMAGE_DEFAULTS

    def new_assembler(self, streamer: MCStreamer, opt_level: int) -> Assembler:
        """Build an assembler that emits for this target."""
        del opt_level
        return Assembler(self.selector(streamer), streamer,
                         function_alignment=IMAGE_DEFAULTS.function_alignment,
                         pad_byte=PAD_BYTE, registers=self.registers,
                         allocation_order=CC_PL4G.orders(GPR.name, FPR.name),
                         callee_saved=CC_PL4G.callee_saved)

    @property
    def vectors(self) -> Vectors:
        """What this machine can do to a run of elements at once.

        Nothing.  The vector extension is not in the base this compiler builds
        for and there is no level to ask for it with, so a run is done an
        element at a time -- which is what the program means, and is the whole
        of what this target has to say about it.
        """
        return Vectors()

    def generate(self, module: Module, asm: Assembler, diags: DiagEngine,
                 opt_level: int, sources: SourceManager | None = None) -> None:
        """Generate the whole image for *module*."""
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
                    alignment=IMAGE_DEFAULTS.text_alignment)
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
        emit_start(asm, module, lookup_cconv(module.startup.cconv))
        messages.emit(asm)
        constants.emit(asm)

    @property
    def entry_symbol(self) -> str:
        """The symbol the image's entry point uses."""
        return ENTRY_SYMBOL
