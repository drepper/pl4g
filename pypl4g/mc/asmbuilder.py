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
from .desc import InstFlags
from .machine import MachineBasicBlock, MachineFunction
from .operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef, RelocKind, SymExpr
from .ops import Condition, Op
from .reg import Reg, RegisterInfo, RegUnit
from .regalloc import Assignment, allocate
from .streamer import MCStreamer
from .symbol import MCSection, MCSymbol, SymBinding, SymKind, SymVisibility


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

    def select_store(self, address: MCMem, value: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that write *value* into the memory *address* names."""
        ...

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        ...

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        ...

    def select_jump(self, target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that transfer control to *target*."""
        ...

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*."""
        ...

    def select_reload(self, destination: Reg, slot: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that read the frame slot at *slot* into *destination*."""
        ...

    def select_frame(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that make room for *size* bytes on the stack."""
        ...

    def select_unframe(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that give that room back."""
        ...

    def select_branch(self, cond: Condition, lhs: MCOperand, rhs: MCOperand,
                      target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *lhs* and *rhs* stand in *cond*.

        The comparison and the branch are one call because on one architecture
        they are one instruction, and on the two where they are not, which
        instruction does the comparing depends on what is being compared.  A
        selector that were handed them separately would have to remember the
        first to encode the second.
        """
        ...

    def select_address(self, dst: Reg, symbol: MCSymRef,
                       span: Span) -> Sequence[MCInst]:
        """Instructions that put the address of *symbol* into *dst*.

        Always computed from the program counter and never written into the
        image, which is the rule that keeps the image free of anything a loader
        would have to patch.
        """
        ...

    def select_widen(self, dst: Reg, src: MCOperand, bits: int, signed: bool,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that put a *bits*-wide value into the whole of *dst*,
        filling what is above it with zeroes or with its sign.

        What it costs differs: an architecture with only one register width has
        the value there already, and one with narrow views has an instruction
        for it or, for the unsigned case, a move that clears the rest.
        """
        ...

    def select_clamp(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                     bound: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that put *bound* into *dst* where *lhs* and *rhs* stand
        in *cond*, and leave what *dst* holds where they do not.

        One call, like the two above, and for the same reason: two of these
        architectures test into flags and read them back, and the third has no
        flags and has to build the answer and use it as a mask.
        """
        ...

    def select_set(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                   span: Span) -> Sequence[MCInst]:
        """Instructions that put one into *dst* when *lhs* and *rhs* stand in
        *cond*, and zero into it when they do not.

        One call for the same reason `select_branch` is one: two of these
        architectures compare into flags and read them back with a second
        instruction, and the third has no flags at all and computes the answer
        directly, so which instruction does the comparing is the target's
        business and not the caller's.
        """
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
            " unassigned registers after allocation")))
        self.function_name = name
        self.count = count


class Assembler:
    """Builds the symbolic representation of one image."""

    def __init__(self, selector: InstructionSelector, streamer: MCStreamer,
                 function_alignment: int = 16, pad_byte: int = 0xCC,
                 machine_passes: Sequence["MachinePass"] = (),
                 registers: "RegisterInfo | None" = None,
                 allocation_order: Sequence["RegUnit"] = ()) -> None:
        self._selector = selector
        self._streamer = streamer
        self._alignment = function_alignment
        #: What the allocator needs: the register file, to turn a unit and a
        #: width into the register that names them, and which units it may give
        #: out.  Both come from the target, since neither is the builder's to
        #: decide.
        self._registers = registers
        self._allocation_order = tuple(allocation_order)
        #: What the allocator decided, for each function, in the order the
        #: functions were built.  The debugging dump reads it; nothing else does.
        self.assignments: list["Assignment"] = []
        #: What padding is filled with.  It must trap rather than fall through,
        #: so each architecture names a byte of its own: a breakpoint on one, a
        #: permanently undefined word on another.
        self._pad_byte = pad_byte
        #: Rewrites applied to each function before its registers are assigned.
        #: This is where the register allocator and the scheduler will also run.
        self._machine_passes = list(machine_passes)
        self._function: MachineFunction | None = None
        self._block: MachineBasicBlock | None = None
        #: How many labels have been reserved inside functions, so that two
        #: of them are never the same.
        self._reserved: int = 0
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
        self._streamer.emit_align(alignment, self._pad_byte)

    def bytes(self, data: bytes) -> None:
        """Emit literal bytes, as the initial contents of a variable."""
        self._streamer.emit_bytes(data)

    def label(self, name: str, *, binding: SymBinding = SymBinding.LOCAL,
              kind: SymKind = SymKind.NOTYPE,
              visibility: SymVisibility = SymVisibility.DEFAULT) -> MCSymbol:
        """Define a symbol at the current position of the current section."""
        return self._streamer.define_symbol(name, binding, kind, visibility=visibility)

    def end_label(self, symbol: MCSymbol) -> None:
        """Record how far the definition of *symbol* extends."""
        self._streamer.set_symbol_size(symbol)

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

    def reserve_label(self, hint: str) -> str:
        """A label no other block in this function has.

        For code generated inside a function rather than for a block of the
        representation -- the place an arithmetic check carries on from, and
        whatever else wants somewhere to land.
        """
        assert self._function is not None
        self._reserved += 1
        return "".join((".L", self._function.name, ".", hint, ".",
                        str(self._reserved)))

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
        self._make_frame(function)
        self._streamer.emit_align(self._alignment, self._pad_byte)
        # What is not exported is kept in twice over: bound locally, so nothing
        # outside this image can name it, and marked hidden, which is the part
        # that still says so if it is ever made global by something later.
        binding = SymBinding.GLOBAL if function.exported else SymBinding.LOCAL
        visibility = (SymVisibility.DEFAULT if function.exported
                      else SymVisibility.HIDDEN)
        symbol = self._streamer.define_symbol(function.name, binding, SymKind.FUNC,
                                              visibility=visibility)
        for block in function.blocks:
            if block.label in self._streamer.symbols or self._is_branch_target(function, block):
                self._streamer.define_symbol(block.label, SymBinding.LOCAL,
                                             SymKind.NOTYPE, temporary=True)
            self._streamer.emit_insts(block.insts)
        self._streamer.set_symbol_size(symbol)
        if function.padding > 0:
            self._streamer.emit_padding(function.padding, self._pad_byte)
        self._function = None
        self._block = None
        return symbol

    def _is_branch_target(self, function: MachineFunction,
                          block: MachineBasicBlock) -> bool:
        """Whether any block of *function* names *block* as a successor."""
        return any(block.label in other.successors for other in function.blocks)

    def _assign_registers(self, function: MachineFunction) -> None:
        """Turn every virtual register into a physical one.

        The check afterwards is not belt and braces: the encoders refuse a
        virtual register, and this says which function still has one rather than
        leaving the encoder to fail somewhere further down with less to say.
        """
        if function.virtual_registers():
            if self._registers is None or not self._allocation_order:
                raise RegisterAssignmentError(function.name,
                                              len(function.virtual_registers()))
            self.assignments.append(
                allocate(function, self._registers, self._allocation_order,
                         self._selector))
        pending = function.virtual_registers()
        if pending:
            raise RegisterAssignmentError(function.name, len(pending))

    def _make_frame(self, function: MachineFunction) -> None:
        """Put the stack the allocator asked for around the function.

        Only the allocator puts anything on the stack, so this runs after it and
        only where it took a slot: a function that needed none has no frame and
        no instruction saying so.  The room is given back before every return
        rather than at one place, because there is no one place -- a function
        may leave from more than one, and control that left without giving it
        back would return to a caller whose stack had moved.
        """
        size = function.frame.size
        if size == 0:
            return
        entry = function.blocks[0]
        entry.insts = [*self._selector.select_frame(size, INVALID_SPAN),
                       *entry.insts]
        for block in function.blocks:
            out: list[MCInst] = []
            for inst in block.insts:
                if InstFlags.RETURN in inst.desc.flags:
                    out.extend(self._selector.select_unframe(size, inst.span))
                out.append(inst)
            block.insts = out

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

    def store(self, address: MCMem, value: MCOperand | int,
              span: Span = INVALID_SPAN) -> None:
        """Write *value* into the memory *address* names."""
        operand = self.imm(value) if isinstance(value, int) else value
        self._emit(self._selector.select_store(address, operand, span))

    def op(self, op: Op, dst: Reg | None = None, *sources: MCOperand | int,
           span: Span = INVALID_SPAN) -> None:
        """Compute *op* over *sources*, placing the result in *dst*."""
        operands = tuple(self.imm(s) if isinstance(s, int) else s for s in sources)
        self._emit(self._selector.select_op(op, dst, operands, span))

    def call(self, target: MCOperand | str, span: Span = INVALID_SPAN) -> None:
        """Call *target*, named either directly or by symbol name."""
        operand = self.symref(target) if isinstance(target, str) else target
        self._emit(self._selector.select_call(operand, span))

    def jump(self, target: str, span: Span = INVALID_SPAN) -> None:
        """Transfer control to the block called *target*."""
        assert self._block is not None
        self._block.successors.append(target)
        self._emit(self._selector.select_jump(self._symref(target), span))

    def branch(self, cond: Condition, lhs: MCOperand, rhs: MCOperand, target: str,
               span: Span = INVALID_SPAN) -> None:
        """Go to the block called *target* when *lhs* and *rhs* stand in *cond*."""
        assert self._block is not None
        self._block.successors.append(target)
        self._emit(self._selector.select_branch(cond, lhs, rhs,
                                                self._symref(target), span))

    def setcond(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                span: Span = INVALID_SPAN) -> None:
        """Put into *dst* whether *lhs* and *rhs* stand in *cond*.

        The truth value that comes out is one or zero, and nothing else: every
        instruction that produces one on these three architectures produces
        exactly that, so the representation is not a choice being made here.
        """
        self._emit(self._selector.select_set(cond, dst, lhs, rhs, span))

    def address(self, dst: Reg, name: str, span: Span = INVALID_SPAN) -> None:
        """Put the address of the symbol *name* into *dst*."""
        self._emit(self._selector.select_address(dst, self.symref(name), span))

    def widen(self, dst: Reg, src: MCOperand, bits: int, signed: bool,
              span: Span = INVALID_SPAN) -> None:
        """Put a *bits*-wide value into the whole of *dst*."""
        self._emit(self._selector.select_widen(dst, src, bits, signed, span))

    def clamp(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
              bound: MCOperand, span: Span = INVALID_SPAN) -> None:
        """Put *bound* into *dst* where *lhs* and *rhs* stand in *cond*.

        The one thing every saturating operation is made of: a value has been
        computed and a bound replaces it where it went past.
        """
        self._emit(self._selector.select_clamp(cond, dst, lhs, rhs, bound, span))

    def falls_through(self, target: str) -> None:
        """Record an edge control takes by simply going on to the next block.

        Nothing is emitted: that is the point.  The edge is recorded anyway,
        because what makes a block's label a symbol is that something names it,
        and a later pass reading the control-flow graph needs the edge whether
        or not an instruction stands for it.
        """
        assert self._block is not None
        self._block.successors.append(target)

    def _symref(self, label: str) -> MCSymRef:
        """A reference to the block called *label*."""
        return MCSymRef(SymExpr(self._streamer.symbol(label)), RelocKind.PCREL)

    def ret(self, span: Span = INVALID_SPAN) -> None:
        """Return from the current function."""
        self._emit(self._selector.select_return(span))
