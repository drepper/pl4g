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

from __future__ import annotations

from typing import Protocol, Sequence

from ..source.location import INVALID_SPAN, Span
from .inst import MCInst
from .desc import InstFlags
from .machine import MachineBasicBlock, MachineFunction, MalformedGraph
from .operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef, RelocKind, SymExpr
from .ops import Condition, Op
from collections.abc import Mapping

from .reg import PhysReg, Reg, RegisterInfo, RegUnit
from .regalloc import Assignment, allocate, registers_of
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

    def select_float_abs(self, dst: Reg, src: MCOperand, bits: int,
                         span: Span) -> Sequence[MCInst]:
        """Instructions that put the magnitude of *src* into *dst*."""
        ...

    def select_float_extend(self, dst: Reg, src: MCOperand, from_bits: int,
                            to_bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put *src* into *dst* in a wider floating-point
        format, which holds every value of the narrower one exactly."""
        ...

    def select_branch_if_finite(self, value: Reg, bits: int, target: MCSymRef,
                                span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *value* is a finite number.

        Subtracting a value from itself answers zero where it is finite and
        not-a-number where it is an infinity or already a not-a-number, so one
        subtraction and one question about whether the answer is a number
        settles both cases at once.
        """
        ...

    #: The widths at which this target's ordinary arithmetic both exists as an
    #: instruction and leaves behind something that says whether the answer went
    #: past the end of that width.  Empty where it has no such thing, which is
    #: what says the answer has to be found by comparing it instead.
    flagged_widths: frozenset[int] = frozenset()

    def select_op_at(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                     bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions computing *op* into *dst*, naming everything at *bits*.

        Asked where the width matters to what the instruction leaves behind and
        not only to the answer: arithmetic on a narrow type is done at that
        type's width so that the flags say whether *it* went past.
        """
        ...

    def select_branch_if_in_range(self, op: Op, signed: bool, target: MCSymRef,
                                  span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when the last arithmetic did not go past.

        Asked only at a width in `flagged_widths`, and only straight after the
        instruction it is about: what it reads was written by that instruction
        and by nothing since.  It is told which operation, because an
        architecture may say "carried out" and "borrowed into" with one flag and
        opposite senses.
        """
        ...

    def select_float_op(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                        bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over two floating-point values."""
        ...

    def select_float_compare(self, cond: Condition, dst: Reg, lhs: MCOperand,
                             rhs: MCOperand, bits: int,
                             span: Span) -> Sequence[MCInst]:
        """Instructions that put whether two floating-point values stand in
        *cond* into *dst*, as one or zero."""
        ...

    def select_shift(self, op: Op, dst: Reg, value: MCOperand, amount: MCOperand,
                     bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that move the bits of *value* by *amount* into *dst*.

        One call rather than an ordinary two-operand operation because one of
        these architectures takes the count in a fixed register, which is a
        thing the caller should not have to know.
        """
        ...

    def select_divide(self, dst: Reg, left: MCOperand, right: MCOperand,
                      signed: bool, remainder: bool, bits: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that divide *left* by *right* into *dst*, giving the
        quotient or, where *remainder*, what is left over.

        One call, because the shapes are nothing alike: one architecture takes
        its dividend in a fixed pair of registers and writes both answers there,
        one has a division and gets the remainder by multiplying back, and one
        has an instruction for each.  What they agree on is that the division
        truncates toward zero, which is what the language says it does.
        """
        ...

    def link_slot_size(self) -> int:
        """How much room a function that calls has to set aside for its own
        return address, which is none where a call has already put it somewhere
        a further call cannot reach."""
        ...

    def select_save_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put the return address into the frame at *offset*."""
        ...

    def select_restore_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Instructions that take it back out again."""
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
                 machine_passes: Sequence[MachinePass] = (),
                 registers: RegisterInfo | None = None,
                 allocation_order: (Sequence[RegUnit]
                                    | Mapping[str, Sequence[RegUnit]]) = (),
                 callee_saved: frozenset[RegUnit] | None = None) -> None:
        self._selector = selector
        self._streamer = streamer
        self._alignment = function_alignment
        #: What the allocator needs: the register file, to turn a unit and a
        #: width into the register that names them, and which units it may give
        #: out.  Both come from the target, since neither is the builder's to
        #: decide.
        self._registers = registers
        #: What the allocator may give out.  Either the units of the one class
        #: a target has values in, or a mapping from the name of a class to its
        #: units where a target has more than one.
        #: What a function that says nothing of its own gets, and what the one
        #: being built now uses.  A function states its own where it begins,
        #: since the convention is the function's and not the image's.
        self._default_order: tuple[RegUnit, ...] | dict[str, tuple[RegUnit, ...]] = (
            {name: tuple(units) for name, units in allocation_order.items()}
            if isinstance(allocation_order, Mapping) else tuple(allocation_order))
        self._order = self._default_order
        #: The units a function must hand back as it found them.  The
        #: convention says which, since that is what a convention is about.
        self._default_kept = frozenset(callee_saved or ())
        self._kept = self._default_kept
        #: What the allocator decided, for each function, in the order the
        #: functions were built.  The debugging dump reads it; nothing else does.
        self.assignments: list[Assignment] = []
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
                       padding: int = 0,
                       allocation_order: (Sequence[RegUnit]
                                          | Mapping[str, Sequence[RegUnit]]
                                          | None) = None,
                       callee_saved: frozenset[RegUnit] | None = None,
                       ) -> MachineFunction:
        """Start building the function *name*.

        Which registers may be given out and which have to be handed back as
        they were found are the *function's* and not the image's: the
        specification says a convention may differ between the functions of one
        compilation, so a function that follows another one says so here.  What
        was given when the builder was made is what a function that says nothing
        gets.
        """
        assert self._function is None
        function = MachineFunction(name=name, exported=exported, padding=padding)
        self._function = function
        self._order = (self._settled_order(allocation_order)
                       if allocation_order is not None else self._default_order)
        self._kept = (frozenset(callee_saved) if callee_saved is not None
                      else self._default_kept)
        self._block = function.add_block("".join((".L", name, "_entry")))
        self.functions.append(function)
        return function

    @staticmethod
    def _settled_order(
            given: Sequence[RegUnit] | Mapping[str, Sequence[RegUnit]]
    ) -> tuple[RegUnit, ...] | dict[str, tuple[RegUnit, ...]]:
        """An allocation order in the form the allocator wants it."""
        if isinstance(given, Mapping):
            return {name: tuple(units) for name, units in given.items()}
        return tuple(given)

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
        wrong = function.edges_are_complete()
        if wrong is not None:
            raise MalformedGraph(wrong)
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
            if self._registers is None or not self._order:
                raise RegisterAssignmentError(function.name,
                                              len(function.virtual_registers()))
            self.assignments.append(
                allocate(function, self._registers, self._order,
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
        # A function that calls has to keep its own return address somewhere a
        # call cannot reach.  On the architecture whose call instruction pushes
        # it, that is already true and this costs nothing; on the two that leave
        # it in a register, the register is the first thing the next call writes.
        calls = any(InstFlags.CALL in inst.desc.flags
                    for block in function.blocks for inst in block.insts)
        # And only where it returns.  A function that never comes back -- the
        # entry point is the one there is -- has no caller to return to, so
        # keeping its return address would be keeping something nothing reads.
        returns = any(InstFlags.RETURN in inst.desc.flags
                      for block in function.blocks for inst in block.insts)
        link = self._selector.link_slot_size() if calls and returns else 0
        # And every register the convention says a function hands back as it
        # found it.  The allocator gives those out -- they are the ones worth
        # having when a call would destroy the others -- so a function that took
        # one has to put it back, or its caller loses whatever it held.
        kept = self._kept_registers(function) if returns else []
        total = size + link + 8 * len(kept)
        if total == 0:
            return
        # The prologue goes at the top of the first block and the undo before
        # every return, which is right exactly while nothing branches to the
        # first block.  A back edge to it would make the stack grow by a frame
        # a turn, silently, so the one thing that would make this wrong is
        # refused rather than left to be found as a program that slowly dies.
        if self._is_branch_target(function, function.blocks[0]):
            raise MalformedGraph("".join((
                function.name, ": control comes back to the block the frame is "
                "made in")))
        opening = list(self._selector.select_frame(total, INVALID_SPAN))
        if link:
            opening.extend(self._selector.select_save_link(size, INVALID_SPAN))
        for index, register in enumerate(kept):
            opening.extend(self._selector.select_spill(
                size + link + 8 * index, register, INVALID_SPAN))
        function.preserved = frozenset(reg.unit for reg in kept
                                       if isinstance(reg, PhysReg))
        entry = function.blocks[0]
        entry.insts = [*opening, *entry.insts]
        for block in function.blocks:
            out: list[MCInst] = []
            for inst in block.insts:
                if InstFlags.RETURN in inst.desc.flags:
                    for index, register in enumerate(kept):
                        out.extend(self._selector.select_reload(
                            register, size + link + 8 * index, inst.span))
                    if link:
                        out.extend(self._selector.select_restore_link(size, inst.span))
                    out.extend(self._selector.select_unframe(total, inst.span))
                out.append(inst)
            block.insts = out

    def _kept_registers(self, function: MachineFunction) -> list[Reg]:
        """The callee-saved registers this function turned out to use.

        Asked of the finished code rather than of the allocator, so that a
        register put there by anything else -- a fixed operand, a helper -- is
        counted too.
        """
        if not self._kept or self._registers is None:
            return []
        used: set[RegUnit] = set()
        for block in function.blocks:
            for inst in block.insts:
                for operand in inst.operands:
                    for register, _ in registers_of(operand):
                        if isinstance(register, PhysReg):
                            used.add(register.unit)
                for register in (*inst.desc.implicit_defs, *inst.desc.implicit_uses):
                    if isinstance(register, PhysReg):
                        used.add(register.unit)
        # Kept in the order the convention prefers them, so that a function
        # saving two of them saves them in a settled order rather than in
        # whatever order a set came out in.
        preferred: list[RegUnit] = []
        if isinstance(self._order, dict):
            for units in self._order.values():
                preferred.extend(units)
        else:
            preferred.extend(self._order)
        order = {unit: index for index, unit in enumerate(preferred)}
        wanted = sorted(used & self._kept,
                        key=lambda unit: order.get(unit, len(order)))
        return [self._registers.view(unit, 64) for unit in wanted]

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

    def call(self, target: MCOperand | str, span: Span = INVALID_SPAN,
             clobbers: Sequence[Reg] = (),
             reads: Sequence[Reg] = ()) -> None:
        """Call *target*, named either directly or by symbol name.

        *clobbers* is what the callee destroys and *reads* the registers the
        arguments were put in.  Both are given here rather than stated in the
        instruction's row because both are the callee's to say: two functions of
        one compilation may follow different conventions, a function that
        destroys little is one a caller has to save little around, and a
        register holding an argument would look dead from the moment it was
        written if nothing said the call wanted it.
        """
        operand = self.symref(target) if isinstance(target, str) else target
        selected = list(self._selector.select_call(operand, span))
        for inst in selected:
            if InstFlags.CALL in inst.desc.flags:
                inst.clobbers = tuple(clobbers)
                inst.reads = tuple(reads)
        self._emit(selected)

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

    def float_abs(self, dst: Reg, src: MCOperand, bits: int,
                  span: Span = INVALID_SPAN) -> None:
        """Put the magnitude of *src* into *dst*."""
        self._emit(self._selector.select_float_abs(dst, src, bits, span))

    def float_extend(self, dst: Reg, src: MCOperand, from_bits: int, to_bits: int,
                     span: Span = INVALID_SPAN) -> None:
        """Put *src* into *dst* in the wider floating-point format *to_bits*."""
        self._emit(self._selector.select_float_extend(dst, src, from_bits,
                                                      to_bits, span))

    def branch_if_finite(self, value: Reg, bits: int, target: str,
                         span: Span = INVALID_SPAN) -> None:
        """Go to the block called *target* when *value* is a finite number."""
        assert self._block is not None
        self._block.successors.append(target)
        self._emit(self._selector.select_branch_if_finite(
            value, bits, self._symref(target), span))

    @property
    def flagged_widths(self) -> frozenset[int]:
        """The widths whose arithmetic says for itself whether it went past."""
        return getattr(self._selector, "flagged_widths", frozenset())

    def op_at(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
              bits: int, span: Span = INVALID_SPAN) -> None:
        """Compute *op* into *dst* with everything named at *bits*."""
        self._emit(self._selector.select_op_at(op, dst, left, right, bits, span))

    def branch_if_in_range(self, op: Op, signed: bool, target: str,
                           span: Span = INVALID_SPAN) -> None:
        """Go to *target* where the arithmetic just emitted stayed in its width."""
        assert self._block is not None
        self._block.successors.append(target)
        self._emit(self._selector.select_branch_if_in_range(
            op, signed, self._symref(target), span))

    def float_op(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                 bits: int, span: Span = INVALID_SPAN) -> None:
        """Compute *op* over two floating-point values into *dst*."""
        self._emit(self._selector.select_float_op(op, dst, left, right, bits, span))

    def float_compare(self, cond: Condition, dst: Reg, lhs: MCOperand,
                      rhs: MCOperand, bits: int, span: Span = INVALID_SPAN) -> None:
        """Put whether two floating-point values stand in *cond* into *dst*."""
        self._emit(self._selector.select_float_compare(cond, dst, lhs, rhs, bits,
                                                       span))

    def shift(self, op: Op, dst: Reg, value: MCOperand, amount: MCOperand,
              bits: int, span: Span = INVALID_SPAN) -> None:
        """Move the bits of *value* by *amount* into *dst*."""
        self._emit(self._selector.select_shift(op, dst, value, amount, bits, span))

    def divide(self, dst: Reg, left: MCOperand, right: MCOperand, signed: bool,
               remainder: bool, bits: int, span: Span = INVALID_SPAN) -> None:
        """Divide *left* by *right* into *dst*, truncating toward zero."""
        self._emit(self._selector.select_divide(dst, left, right, signed,
                                                remainder, bits, span))

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


    def frame_slot(self, size: int, alignment: int) -> int:
        """Take *size* bytes of this function's frame and answer where they are.

        For storage a function has of its own -- an array whose type says how
        many elements it has, to begin with.  It lasts exactly as long as the
        call, which is what makes the frame the right place for it and an arena
        that never frees the wrong one.

        The offset is from the stack pointer as the function leaves it, which is
        where the register allocator's own slots are measured from too.
        """
        assert self._function is not None
        return self._function.frame.reserve(size, alignment)

    def temporary(self, like: Reg) -> Reg:
        """A register of the same kind and width as *like*, holding nothing yet.

        For a value with no name of its own and a life of two instructions:
        what a parallel copy needs to break a cycle.  It is virtual, which is
        what makes it always available -- this runs before anything has been
        given a physical register -- and so no architecture needs an
        instruction that exchanges two registers.
        """
        if self._registers is None:
            raise RegisterAssignmentError("a function with no register file", 1)
        return self._registers.new_virtual(like.cls, like.bits)

    def tail_jump(self, target: str, span: Span = INVALID_SPAN) -> None:
        """Go to another function, which returns in this one's place.

        Unlike `jump` this records no successor: what it names is not a block of
        this function, and the graph this function's own passes walk would be
        wrong to have it in.  Control does not come back here.
        """
        self._emit(self._selector.select_jump(self.symref(target), span))

    def frame(self, size: int, span: Span = INVALID_SPAN) -> None:
        """Make room for *size* bytes on the stack, and give it back with
        `unframe`.

        For code written here rather than lowered from the representation: the
        runtime, which works in physical registers and so has no allocator to
        ask for a slot.  A function that uses this must give the room back on
        every path it leaves by.
        """
        self._emit(self._selector.select_frame(size, span))

    def unframe(self, size: int, span: Span = INVALID_SPAN) -> None:
        """Give back the room `frame` made."""
        self._emit(self._selector.select_unframe(size, span))

    def put_aside(self, slot: int, source: Reg, span: Span = INVALID_SPAN) -> None:
        """Write *source* into the room `frame` made, at *slot* bytes into it."""
        self._emit(self._selector.select_spill(slot, source, span))

    def take_back(self, destination: Reg, slot: int,
                  span: Span = INVALID_SPAN) -> None:
        """Read what `put_aside` wrote at *slot* back into *destination*."""
        self._emit(self._selector.select_reload(destination, slot, span))

    def ret(self, span: Span = INVALID_SPAN,
            reads: Sequence[Reg] = ()) -> None:
        """Return from the current function.

        *reads* is the registers the answer was put in, for the same reason a
        call names the ones its arguments were put in: what says a register
        written just before this is still wanted is this instruction.
        """
        selected = list(self._selector.select_return(span))
        for inst in selected:
            if InstFlags.RETURN in inst.desc.flags:
                inst.reads = tuple(reads)
        self._emit(selected)
