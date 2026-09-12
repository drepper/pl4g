"""The RISC-V 64-bit backend."""

from typing import Final

from ...diag import ids as D
from ...diag.engine import DiagEngine
from ...ir.module import Module
from ...mc.asmbuilder import Assembler
from ...mc.desc import InstrTable
from ...mc.fixup import FixupApplier, MCFixup
from ...mc.inst import MCInst
from ...mc.reg import RegisterInfo
from ...mc.streamer import MCStreamer
from ...ir.layout import DataLayout
from ..globals import emit_globals
from ..target import ImageDefaults
from .abi import lookup as lookup_cconv
from .encoder import EncodingError, encode
from .fixups import apply_fixup
from .isel import RVSelector, UnsupportedOperation, lower_function
from .opcodes import PAD_BYTE, RISCV_INSTRS
from .regs import INFO
from .startup import ENTRY_SYMBOL, emit_start

#: EM_RISCV.  The header also carries flags describing which extensions and
#: which floating-point convention the code uses; zero is the right value while
#: nothing beyond the base integer set is emitted.
IMAGE_DEFAULTS: Final[ImageDefaults] = ImageDefaults(
    machine=243, base_vaddr=0x400000, page_size=0x1000, text_alignment=16,
    function_alignment=16)


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
                         pad_byte=PAD_BYTE)

    def generate(self, module: Module, asm: Assembler, diags: DiagEngine,
                 opt_level: int) -> None:
        """Generate the whole image for *module*."""
        del opt_level
        emit_globals(asm, module, DataLayout(pointer_size=self.pointer_bits // 8))
        asm.section(".text", executable=True,
                    alignment=IMAGE_DEFAULTS.text_alignment)
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
