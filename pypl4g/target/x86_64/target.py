"""The x86-64 backend."""

from typing import Final

from ...diag import ids as D
from ...diag.engine import DiagEngine
from ...ir.module import Module
from ...mc.asmbuilder import Assembler
from ...mc.desc import InstrTable
from ...mc.fixup import MCFixup
from ...mc.inst import MCInst
from ...mc.reg import RegisterInfo
from ...mc.streamer import MCStreamer
from ..target import ImageDefaults
from .abi import lookup as lookup_cconv
from .encoder import EncodingError, encode
from .isel import UnsupportedOperation, X86Selector, lower_function
from .opcodes import X86_INSTRS
from .peephole import passes_for
from .regs import INFO
from .startup import ENTRY_SYMBOL, emit_start


#: EM_X86_64, loaded at the address a fixed-address executable conventionally
#: uses on this architecture.
IMAGE_DEFAULTS: Final[ImageDefaults] = ImageDefaults(
    machine=62, base_vaddr=0x400000, page_size=0x1000, text_alignment=16,
    function_alignment=16)


class X86_64Target:
    """Code generation for x86-64."""

    def __init__(self) -> None:
        self.triple: str = "x86_64-linux-none"
        self.pointer_bits: int = 64
        self.registers: RegisterInfo = INFO
        self.table = InstrTable(X86_INSTRS)

    def encode(self, inst: MCInst) -> tuple[bytes, list[MCFixup]]:
        """Encode one instruction."""
        return encode(inst)

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
                         machine_passes=passes_for(self.table, opt_level))

    def generate(self, module: Module, asm: Assembler, diags: DiagEngine,
                 opt_level: int) -> None:
        """Generate the whole image for *module*.

        The entry point is emitted last so that the functions it calls are
        already defined; nothing depends on that order, since emission never
        waits for a symbol, but it keeps the image in a readable order.
        """
        del opt_level
        asm.section(".text", executable=True,
                    alignment=self.image_defaults().text_alignment)
        for func in module.functions.values():
            if func.is_declaration:
                continue
            try:
                lower_function(asm, func, lookup_cconv(func.cconv), self.registers)
            except UnsupportedOperation as exc:
                diags.emit(D.IMPL_BACKEND_UNSUPPORTED,
                           exc.span if exc.span is not None else func.span,
                           construct=exc.detail)
                return
            except EncodingError as exc:
                diags.emit(D.IMPL_BACKEND_UNENCODABLE,
                           exc.span if exc.span is not None else func.span,
                           detail=exc.detail)
                return
        if module.startup is None:
            return
        emit_start(asm, module, lookup_cconv(module.startup.cconv))

    @property
    def entry_symbol(self) -> str:
        """The symbol the image's entry point uses."""
        return ENTRY_SYMBOL
