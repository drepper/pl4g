"""The AArch64 backend."""

from typing import Final

from ...diag import ids as D
from ...diag.engine import DiagEngine
from ...ir.module import Module
from ...mc.asmbuilder import Assembler
from ...source.manager import SourceManager
from ...mc.desc import InstrTable
from ...mc.fixup import FixupApplier, MCFixup
from ...mc.inst import MCInst
from ...mc.reg import RegisterInfo
from ...mc.regalloc import RegisterPressureError
from ...mc.streamer import MCStreamer
from ...ir.layout import DataLayout
from ..faults import Messages
from ..globals import emit_globals
from ..target import ImageDefaults
from .abi import CC_PL4G_V0, lookup as lookup_cconv
from .encoder import EncodingError, encode
from .fixups import apply_fixup
from .isel import A64Selector, UnsupportedOperation, lower_function
from .opcodes import AARCH64_INSTRS, PAD_BYTE
from .regs import INFO
from .startup import ENTRY_SYMBOL, emit_abort, emit_start

#: EM_AARCH64.  The page size is the largest a kernel may be configured with, so
#: that one image loads whatever the running kernel chose; the congruence the
#: format requires between a segment's offset and its address holds for it.
IMAGE_DEFAULTS: Final[ImageDefaults] = ImageDefaults(
    machine=183, base_vaddr=0x400000, page_size=0x10000, text_alignment=16,
    function_alignment=16)


class AArch64Target:
    """Code generation for AArch64."""

    def __init__(self) -> None:
        self.triple: str = "aarch64-linux-none"
        self.pointer_bits: int = 64
        self.registers: RegisterInfo = INFO
        self.table = InstrTable(AARCH64_INSTRS)

    def encode(self, inst: MCInst) -> tuple[bytes, list[MCFixup]]:
        """Encode one instruction."""
        return encode(inst)

    @property
    def apply_fixup(self) -> FixupApplier:
        """A relocation shares its word with the opcode, so it is inserted."""
        return apply_fixup

    def selector(self, streamer: MCStreamer) -> A64Selector:
        """The instruction selector for this target."""
        del streamer
        return A64Selector(self.table)

    def image_defaults(self) -> ImageDefaults:
        """The layout constants the image writer needs."""
        return IMAGE_DEFAULTS

    def new_assembler(self, streamer: MCStreamer, opt_level: int) -> Assembler:
        """Build an assembler that emits for this target."""
        del opt_level
        return Assembler(self.selector(streamer), streamer,
                         function_alignment=IMAGE_DEFAULTS.function_alignment,
                         pad_byte=PAD_BYTE, registers=self.registers,
                         allocation_order=CC_PL4G_V0.allocation_order)

    def generate(self, module: Module, asm: Assembler, diags: DiagEngine,
                 opt_level: int, sources: "SourceManager | None" = None) -> None:
        """Generate the whole image for *module*."""
        del opt_level
        messages = Messages()
        emit_globals(asm, module, DataLayout(pointer_size=self.pointer_bits // 8))
        asm.section(".text", executable=True,
                    alignment=IMAGE_DEFAULTS.text_alignment)
        for func in module.functions.values():
            if func.is_declaration:
                continue
            try:
                lower_function(asm, func, lookup_cconv(func.cconv), self.registers,
                               messages, sources)
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
        if messages.wanted:
            emit_abort(asm, lookup_cconv(module.startup.cconv))
        emit_start(asm, module, lookup_cconv(module.startup.cconv))
        messages.emit(asm)

    @property
    def entry_symbol(self) -> str:
        """The symbol the image's entry point uses."""
        return ENTRY_SYMBOL
