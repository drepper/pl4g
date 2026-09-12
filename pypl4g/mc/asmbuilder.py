"""The assembler builder API.

This is the interface code generation uses.  There is no assembler text syntax:
the representation is built by calling these functions in order, and what they
build stays internal.  Inline assembly will be a parser that drives exactly these
calls, which is what guarantees it can never express something the encoder is
unable to emit.

    asm.begin_function("main")
    asm.loadreg(EAX, asm.imm(0))
    asm.op(PLUS, EAX, asm.reg(EAX), asm.reg(ECX))
    asm.call("other")
    asm.ret()
    asm.end_function()

Operations are written with the destination first and in three-address form.  A
target whose instructions take two operands lowers that itself, so the same calls
serve every architecture.

Calls append to a ``MachineFunction``, not straight to the image.  That is where
the register allocator, peephole passes and scheduling will run: ``end_function``
assigns registers -- today a check that none are left to assign -- and only then
hands the instructions to the streamer.
"""

from typing import Protocol, Sequence

from ..source.location import INVALID_SPAN, Span
from .inst import MCInst
from .machine import MachineBasicBlock, MachineFunction
from .operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef, RelocKind, SymExpr
from .ops import Op
from .reg import Reg
from .streamer import MCStreamer
from .symbol import MCSection, MCSymbol, SymBinding, SymKind


class InstructionSelector(Protocol):
    """What a target must provide for the builder to work.

    Every method turns one builder call into the instructions of that target.
    Adding an operation to the builder means adding a case here and a row to the
    encoding table; nothing in the builder itself changes.
    """

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
        ...

    def select_op(self, op: Op, dst: Reg | None, sources: Sequence[MCOperand],
                  span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over *sources* into *dst*."""
        ...

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        ...

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        ...


class MachinePass(Protocol):
    """A rewrite applied to a function before it is streamed."""

    name: str

    def run(self, function: MachineFunction) -> bool:
        """Rewrite *function*, returning whether anything changed."""
        ...


class RegisterAssignmentError(Exception):
    """A function still mentions registers the allocator has not assigned."""

    def __init__(self, name: str, count: int) -> None:
        super().__init__("".join((
            "function '", name, "' still has ", str(count),
            " unassigned registers; no register allocator has run")))
        self.function_name = name
        self.count = count


class Assembler:
    """Builds the symbolic representation of one image."""

    def __init__(self, selector: InstructionSelector, streamer: MCStreamer,
                 function_alignment: int = 16,
                 machine_passes: Sequence["MachinePass"] = ()) -> None:
        self._selector = selector
        self._streamer = streamer
        self._alignment = function_alignment
        #: Rewrites applied to each function before its registers are assigned.
        #: This is where the register allocator and the scheduler will also run.
        self._machine_passes = list(machine_passes)
        self._function: MachineFunction | None = None
        self._block: MachineBasicBlock | None = None
        self.functions: list[MachineFunction] = []

    @property
    def streamer(self) -> MCStreamer:
        """The streamer the built representation lands in."""
        return self._streamer

    # -- placement -------------------------------------------------------------

    def section(self, name: str, *, executable: bool = False, writable: bool = False,
                alignment: int = 1) -> MCSection:
        """Select the section subsequent emission goes into."""
        section = self._streamer.get_section(name, executable=executable,
                                             writable=writable, alignment=alignment)
        self._streamer.switch_section(section)
        return section

    def align(self, alignment: int) -> None:
        """Pad to the next multiple of *alignment*."""
        self._streamer.emit_align(alignment)

    # -- functions -------------------------------------------------------------

    def begin_function(self, name: str, *, exported: bool = False,
                       padding: int = 0) -> MachineFunction:
        """Start building the function *name*."""
        assert self._function is None
        function = MachineFunction(name=name, exported=exported, padding=padding)
        self._function = function
        self._block = function.add_block("".join((".L", name, "_entry")))
        self.functions.append(function)
        return function

    def block(self, label: str | None = None) -> MachineBasicBlock:
        """Start a new block within the current function."""
        assert self._function is not None
        self._block = self._function.add_block(label)
        return self._block

    def end_function(self) -> MCSymbol:
        """Finish the current function and stream it into the image."""
        assert self._function is not None
        function = self._function
        for machine_pass in self._machine_passes:
            machine_pass.run(function)
        self._assign_registers(function)
        self._streamer.emit_align(self._alignment)
        binding = SymBinding.GLOBAL if function.exported else SymBinding.LOCAL
        symbol = self._streamer.define_symbol(function.name, binding, SymKind.FUNC)
        for block in function.blocks:
            if block.label in self._streamer.symbols or self._is_branch_target(function, block):
                self._streamer.define_symbol(block.label, SymBinding.LOCAL,
                                             SymKind.NOTYPE, temporary=True)
            self._streamer.emit_insts(block.insts)
        self._streamer.set_symbol_size(symbol)
        if function.padding > 0:
            self._streamer.emit_padding(function.padding)
        self._function = None
        self._block = None
        return symbol

    def _is_branch_target(self, function: MachineFunction,
                          block: MachineBasicBlock) -> bool:
        """Whether any block of *function* names *block* as a successor."""
        return any(block.label in other.successors for other in function.blocks)

    def _assign_registers(self, function: MachineFunction) -> None:
        """Turn every virtual register into a physical one.

        There is no register allocator yet, so this checks that instruction
        selection produced none.  When the allocator arrives it replaces the body
        of this method and nothing else changes.
        """
        pending = function.virtual_registers()
        if pending:
            raise RegisterAssignmentError(function.name, len(pending))

    # -- operands --------------------------------------------------------------

    def reg(self, register: Reg) -> MCReg:
        """A register operand."""
        return MCReg(register)

    def imm(self, value: int, bits: int = 32, signed: bool = True) -> MCImm:
        """An immediate operand."""
        return MCImm(value=value, bits=bits, signed=signed)

    def mem(self, **kwargs: object) -> MCMem:
        """A memory operand."""
        return MCMem(**kwargs)  # type: ignore[arg-type]

    def symref(self, name: str, kind: RelocKind = RelocKind.PCREL,
               addend: int = 0) -> MCSymRef:
        """A reference to a symbol by name."""
        return MCSymRef(SymExpr(self._streamer.symbol(name)), kind, addend)

    # -- the operations --------------------------------------------------------

    def _emit(self, insts: Sequence[MCInst]) -> None:
        """Append selected instructions to the current block."""
        assert self._block is not None
        for inst in insts:
            self._block.append(inst)

    def loadreg(self, dst: Reg, src: MCOperand | int, span: Span = INVALID_SPAN) -> None:
        """Place *src* into the register *dst*."""
        operand = self.imm(src) if isinstance(src, int) else src
        self._emit(self._selector.select_move(dst, operand, span))

    def op(self, op: Op, dst: Reg | None = None, *sources: MCOperand | int,
           span: Span = INVALID_SPAN) -> None:
        """Compute *op* over *sources*, placing the result in *dst*."""
        operands = tuple(self.imm(s) if isinstance(s, int) else s for s in sources)
        self._emit(self._selector.select_op(op, dst, operands, span))

    def call(self, target: MCOperand | str, span: Span = INVALID_SPAN) -> None:
        """Call *target*, named either directly or by symbol name."""
        operand = self.symref(target) if isinstance(target, str) else target
        self._emit(self._selector.select_call(operand, span))

    def ret(self, span: Span = INVALID_SPAN) -> None:
        """Return from the current function."""
        self._emit(self._selector.select_return(span))
