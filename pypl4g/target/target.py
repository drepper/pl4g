"""What a backend must provide."""

from dataclasses import dataclass
from typing import Protocol

from ..diag.engine import DiagEngine
from ..ir.module import Module
from ..mc.asmbuilder import Assembler, InstructionSelector
from ..mc.fixup import FixupApplier, MCFixup
from ..mc.inst import MCInst
from ..mc.reg import RegisterInfo
from ..mc.streamer import MCStreamer


@dataclass(frozen=True, slots=True)
class ImageDefaults:
    """The target-specific constants the image writer needs."""

    #: The ELF machine number of the target.
    machine: int
    #: Where a fixed-address executable is loaded.  A position-independent one
    #: uses zero, which is why this is a value rather than a constant.
    base_vaddr: int
    page_size: int
    text_alignment: int
    function_alignment: int


class Target(Protocol):
    """One code generator."""

    @property
    def triple(self) -> str:
        """The triple this backend generates for."""
        ...

    @property
    def pointer_bits(self) -> int:
        """The width of a pointer."""
        ...

    @property
    def registers(self) -> RegisterInfo:
        """The target's register file."""
        ...

    @property
    def entry_symbol(self) -> str:
        """The symbol the image's entry point uses."""
        ...

    def encode(self, inst: MCInst) -> tuple[bytes, list[MCFixup]]:
        """Encode one instruction."""
        ...

    @property
    def apply_fixup(self) -> FixupApplier:
        """How this target stores a fixup's value into the bytes that hold it."""
        ...

    def selector(self, streamer: MCStreamer) -> InstructionSelector:
        """The instruction selector for this target."""
        ...

    def new_assembler(self, streamer: MCStreamer, opt_level: int) -> Assembler:
        """Build an assembler that emits for this target."""
        ...

    def generate(self, module: Module, asm: Assembler, diags: DiagEngine,
                 opt_level: int) -> None:
        """Generate the whole image for *module* through *asm*."""
        ...

    def image_defaults(self) -> ImageDefaults:
        """The layout constants the image writer needs."""
        ...
