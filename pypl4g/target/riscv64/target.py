"""The RISC-V 64-bit backend."""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from ...diag import ids as D
from ...diag.engine import DiagEngine
from ...ir.function import SYSTEM_CCONV, Function
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
from ...ir.types import (ArrayType, FloatType, ListType, PtrType, ResultType,
                         TupleType, Type, VecType)
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
from ..vectors import Vectors, settle as settle_vectors
from ..target import ImageDefaults
from .abi import CC_PL4G, lookup as lookup_cconv
from .encoder import EncodingError, encode
from .fixups import apply_fixup
from . import attributes, isa
from .isel import RVSelector, UnsupportedOperation, lower_function
from .opcodes import PAD_BYTE, RISCV_INSTRS
from .regs import FPR, GPR, INFO
from .startup import (ALLOCATOR_REGS, ABORT_SYMBOL, ENTRY_SYMBOL,
                      SYSCALLS, emit_abort, emit_report, emit_start)

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

#: EM_RISCV.  The flag word is settled per build, the floating-point convention
#: being a property of what the program was built for rather than of the
#: architecture; this is the shape of it and the default it carries.
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
        #: What a program is built for: the base and every extension of it.
        #: Unlike the levels the other architectures have, this is a list rather
        #: than a name, and what the code generator asks of it is which of a
        #: handful of extensions are there.
        self.isa: isa.ISA = isa.parse(isa.DEFAULT)

    def use_mclevel(self, name: str) -> None:
        """Build for this from now on, which here is an ISA string or a profile.

        What is wrong with a name that is neither is what comes back, since
        whoever asked has the name already and what a reader needs is the part
        they got wrong.

        Two things the string may say that this target cannot be.  **The
        width**: `rv32gc` names a machine whose addresses are half as wide, and
        it is a different target rather than a different level of this one --
        the comparison is against what this target says its addresses are, so a
        thirty-two bit target of the same family refuses `rv64` by the same
        line.  **The reduced base**: `rv64e` has sixteen registers and a calling
        convention of its own, and this compiler's register file and convention
        are the full ones.
        """
        found = isa.parse(name)
        if found.bits != self.pointer_bits:
            raise ValueError("".join((
                "it names a base whose addresses are ", str(found.bits),
                " bits wide, and this target's are ", str(self.pointer_bits))))
        if found.embedded:
            raise ValueError(
                "it names the reduced base, which has sixteen registers and a "
                "calling convention of its own, and this compiler has neither")
        self.isa = found

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
        return RVSelector(self.table, self.isa)

    def image_defaults(self) -> ImageDefaults:
        """The layout constants the image writer needs.

        The floating-point convention is the one thing here that a build settles
        rather than the architecture: the field says which registers a function
        passes and answers floating-point values in, and a program built for a
        base without them passes none.
        """
        if self.isa.has("d"):
            return IMAGE_DEFAULTS
        return replace(IMAGE_DEFAULTS,
                       header_flags=(FLOAT_ABI_SINGLE if self.isa.has("f")
                                     else FLOAT_ABI_SOFT))

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
            if not self.isa.floats and _touches_floats(func):
                # Not a limit of this compiler but of what it was asked to
                # build for.  Emitting the instructions anyway is a program
                # that does not run, and doing it in software is a different
                # calling convention and so a different ABI.
                diags.emit(D.IMPL_BACKEND_NEEDS_AN_EXTENSION, func.span,
                           extension="F and D", level=self.isa.named)
                return
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
        # What the image was built for, said in the file for whatever reads the
        # file.  It has to be said: this architecture's base is small and
        # everything else is an extension, so "a RISC-V binary" says almost
        # nothing about what a processor must have to run it -- and unlike
        # x86-64, there is no instruction a program in user mode can ask.
        asm.section(attributes.SECTION, alloc=False, alignment=1,
                    sh_type=attributes.SHT_RISCV_ATTRIBUTES)
        asm.bytes(attributes.build(self.isa.normalized()))

    @property
    def entry_symbol(self) -> str:
        """The symbol the image's entry point uses."""
        return ENTRY_SYMBOL


def _touches_floats(func: Function) -> bool:
    """Whether anything about a function is a floating-point value.

    Its own type and every value it computes, since a function may pass one
    through without an instruction of its own doing arithmetic on it -- and
    passing one through is exactly what needs the floating-point registers the
    convention names.
    """
    seen: list[Type] = [*func.ty.params, func.ty.ret]
    for block in func.blocks:
        seen.extend(param.ty for param in block.params)
        seen.extend(inst.ty for inst in block.insts)
    return any(_is_floating(ty) for ty in seen)


def _is_floating(ty: Type) -> bool:
    """Whether a type is a floating-point one or is built out of them."""
    if isinstance(ty, FloatType):
        return True
    if isinstance(ty, (ArrayType, ListType, VecType)):
        return _is_floating(ty.element)
    if isinstance(ty, PtrType):
        return _is_floating(ty.pointee)
    if isinstance(ty, ResultType):
        return _is_floating(ty.ok)
    if isinstance(ty, TupleType):
        return any(_is_floating(member) for member in ty.members)
    return False
