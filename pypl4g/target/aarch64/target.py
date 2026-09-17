"""The AArch64 backend."""

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
from ..tests import failures_of
from ..pool import Constants
from ..registry import architecture_of
from .. import started
from ..runtime import Runtime, reaches
from ...runtime import blob_for
from .fixups import BY_NAME
from ..globals import emit_globals
from ..vectors import EVERY, Vectors, settle as settle_vectors
from ...ir.inst import BinOp, UnOp
from ..target import ImageDefaults
from .abi import CC_PL4G, lookup as lookup_cconv
from .encoder import EncodingError, encode
from .fixups import apply_fixup
from .isel import A64Selector, UnsupportedOperation, lower_function
from .opcodes import AARCH64_INSTRS, PAD_BYTE
from .regs import GPR, INFO, VEC
from .startup import (ALLOCATOR_REGS, ABORT_SYMBOL, ENTRY_SYMBOL,
                      SYSCALLS, emit_abort, emit_report, emit_start)

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
                         allocation_order=CC_PL4G.orders(GPR.name, VEC.name),
                         callee_saved=CC_PL4G.callee_saved)

    @property
    def vectors(self) -> Vectors:
        """What this machine can do to a run of elements at once.

        Sixteen bytes at a time.  The Advanced SIMD instructions are in the
        base this compiler builds for -- a processor of this architecture
        running Linux has them -- so this is what every one of them can do and
        there is no level to ask for it with.

        The bitwise three at every lane width, since a register of bits is the
        same answer however it is divided into lanes.  Adding and subtracting at
        every lane width too, each with the check that says whether any lane
        went past.  And the saturating pair at every width, which this machine
        has and the other one has only for the two narrow ones.  Multiplying is
        not here in its checked form: seeing that a product went past wants the
        upper half of it.  Where the program said it may wrap, only the low half
        is wanted and this machine gives that at every width but the widest.
        """
        return Vectors(
            bits=128,
            binary={BinOp.AND: EVERY, BinOp.OR: EVERY, BinOp.XOR: EVERY,
                    BinOp.ADD: EVERY, BinOp.SUB: EVERY,
                    BinOp.SAT_ADD: EVERY, BinOp.SAT_SUB: EVERY,
                    BinOp.WRAP_ADD: EVERY, BinOp.WRAP_SUB: EVERY,
                    BinOp.WRAP_MUL: frozenset((8, 16, 32))},
            unary={UnOp.NOT: EVERY})

    def generate(self, module: Module, asm: Assembler, diags: DiagEngine,
                 opt_level: int, sources: SourceManager | None = None) -> None:
        """Generate the whole image for *module*."""
        del opt_level
        messages = Messages()
        constants = Constants()
        # The runtime compiled ahead of time, placed only where the program
        # reaches it -- which is where a call names something it defines.
        runtime = Runtime(blob=blob_for(architecture_of(self.triple)))
        if reaches(module, runtime):
            runtime.reached()
        layout = DataLayout(pointer_size=self.pointer_bits // 8)
        # What the front end asked of a whole run of elements at once, brought
        # down to what this machine has -- which is done to the program before a
        # single instruction is chosen, so that everything below sees only
        # operations it can emit.
        settle_vectors(module, self.vectors, layout)
        emit_globals(asm, module, layout)
        # What the program was started with, where it takes it: an object of the
        # writable data, laid out by the program's own declaration of the type.
        started.emit(asm, module, layout)
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
        # Before the question below: a binary whose only message is a failing
        # test needs the helper as much as one that divides by zero.
        failures = failures_of(module, messages)
        if messages.wanted:
            # The runtime follows the system's convention whatever the
            # function that faults follows: it is written as instructions,
            # and hand-written code names its registers outright.
            emit_abort(asm, lookup_cconv(SYSTEM_CCONV))
        if failures:
            # Only where a test runs: what says a test failed is a write that
            # comes back, which nothing else has any use for.
            emit_report(asm, lookup_cconv(SYSTEM_CCONV))
        emit_start(asm, module, lookup_cconv(module.startup.cconv),
                   failures)
        runtime.emit(asm, BY_NAME)
        messages.emit(asm)
        constants.emit(asm)

    @property
    def entry_symbol(self) -> str:
        """The symbol the image's entry point uses."""
        return ENTRY_SYMBOL
