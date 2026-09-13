"""Instruction selection for RISC-V.

The builder speaks in three-address form with the destination first, which is
what this architecture's instructions already are, so nothing has to be lowered.

There is only one register width, so a narrow value is not a narrow register: an
``i32`` lives in a full register, sign extended, and it is the *instruction* that
says how wide the operation is.  That is why the return value here is placed in
the whole register rather than in a view of it, as on the other two backends.
"""

from typing import TYPE_CHECKING, Final, Sequence

from ...mc import ops
from ...mc.asmbuilder import InstructionSelector
from ...mc.desc import InstrTable, SelectionError
from ...mc.inst import MCInst
from ...mc.operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef
from ...mc.ops import Op
from ...mc.reg import Reg, VirtReg
from ...mc.operand import SymExpr
from ...source.location import Span
from . import ops as rvops
from .opcodes import IMM12_MAX, IMM12_MIN, RISCV_INSTRS
from .regs import GPR, INFO

if TYPE_CHECKING:
    from ...mc.reg import PhysReg
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo
    from ...ir.types import Type
    from ..callconv import CallConvDesc

#: The mnemonic that implements each architecture-neutral binary operation.
_BINARY: Final[dict[str, str]] = {
    ops.PLUS.name: "add",
    ops.MINUS.name: "sub",
    ops.XOR.name: "xor",
}

#: Operations that are one instruction with no operands.
_NULLARY: Final[dict[str, str]] = {
    rvops.ENVIRONMENT_CALL.name: "ecall",
    ops.TRAP.name: "unimp",
}


class UnsupportedOperation(Exception):
    """The backend has no selection rule for this operation yet."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


class RVSelector(InstructionSelector):
    """Turns builder calls into RISC-V instructions."""

    def __init__(self, table: InstrTable | None = None) -> None:
        self.table = table if table is not None else InstrTable(RISCV_INSTRS)

    def _inst(self, mnemonic: str, operands: Sequence[MCOperand], span: Span) -> MCInst:
        """Select the encoding of *mnemonic* for *operands*."""
        try:
            desc = self.table.select(mnemonic, operands)
        except SelectionError as exc:
            raise UnsupportedOperation(str(exc), span if span.is_valid else None) from exc
        return MCInst(desc=desc, operands=tuple(operands), span=span)

    #: Which load reads a value of a given width, and how it widens it.  There
    #: being no narrower register, the instruction is what says which happens.
    _LOADS: Final[dict[tuple[int, bool], str]] = {
        (8, False): "lbu", (8, True): "lb",
        (16, False): "lhu", (16, True): "lh",
        (32, False): "lwu", (32, True): "lw",
        (64, False): "ld", (64, True): "ld",
    }

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
        if isinstance(src, MCMem):
            return self._select_load(dst, src, span)
        if isinstance(src, MCReg) and src.reg is dst:
            return ()
        if isinstance(src, MCReg):
            return (self._inst("mv", (MCReg(dst), src), span),)
        if isinstance(src, MCImm):
            if src.value < IMM12_MIN or src.value > IMM12_MAX:
                # A wider constant is an upper-immediate load followed by an
                # add, or a load from a constant pool.  Neither is generated yet.
                raise UnsupportedOperation("".join((
                    "a constant of ", str(src.value),
                    ", which does not fit one instruction")), span)
            return (self._inst("li", (MCReg(dst), src), span),)
        raise UnsupportedOperation("moving from this kind of operand", span)

    def _select_load(self, dst: Reg, src: MCMem, span: Span) -> Sequence[MCInst]:
        """Instructions that read memory into a register.

        As on the other fixed-width architecture an address is built in two
        steps, and the destination holds it in between.  The second step is
        measured from the first rather than from itself, which is what the
        encoding table records.
        """
        width = src.size_bits if src.size_bits is not None else 64
        mnemonic = self._LOADS.get((width, src.signed))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "reading ", str(width), " bits from memory")), span)
        if src.disp_sym is None:
            base = src.base if src.base is not None else dst
            return (self._inst(mnemonic, (MCReg(dst), MCReg(base),
                                          MCImm(src.disp, 12)), span),)
        symbol = MCSymRef(src.disp_sym)
        return (
            self._inst("auipc.hi20", (MCReg(dst), symbol), span),
            self._inst("addi.lo12", (MCReg(dst), MCReg(dst), symbol), span),
            self._inst(mnemonic, (MCReg(dst), MCReg(dst), MCImm(src.disp, 12)), span),
        )

    def select_op(self, op: Op, dst: Reg | None, sources: Sequence[MCOperand],
                  span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over *sources* into *dst*."""
        nullary = _NULLARY.get(op.name)
        if nullary is not None:
            return (self._inst(nullary, (), span),)
        if op is ops.MOVE:
            if dst is None:
                raise UnsupportedOperation("a move needs a destination", span)
            return self.select_move(dst, sources[0], span)
        mnemonic = _BINARY.get(op.name)
        if mnemonic is not None:
            if dst is None or len(sources) != 2:
                raise UnsupportedOperation("".join((
                    "'", op.name, "' needs a destination and two sources")), span)
            return (self._inst(mnemonic, (MCReg(dst), *sources), span),)
        raise UnsupportedOperation("".join((
            "no RISC-V selection rule for '", op.name, "'")), span)

    #: Which store writes a value of a given width.
    _STORES: Final[dict[int, str]] = {8: "sb", 16: "sh", 32: "sw", 64: "sd"}

    def select_store(self, address: MCMem, value: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that write *value* into the memory *address* names.

        As on the other fixed-width architecture the address has to be built in
        a register.  It is asked for as a value like any other, so the allocator
        places it and no register has to be set aside that nothing else may use.
        """
        width = address.size_bits if address.size_bits is not None else 64
        mnemonic = self._STORES.get(width)
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "writing ", str(width), " bits to memory")), span)
        held: Sequence[MCInst] = ()
        if isinstance(value, MCReg):
            source = value
        else:
            carried = INFO.new_virtual(GPR, 64)
            held = self.select_move(carried, value, span)
            source = MCReg(carried)
        place = MCReg(INFO.new_virtual(GPR, 64))
        if address.disp_sym is None:
            base = MCReg(address.base) if address.base is not None else place
            return (*held, self._inst(mnemonic, (source, base,
                                                 MCImm(address.disp, 12)), span))
        symbol = MCSymRef(address.disp_sym)
        return (
            *held,
            self._inst("auipc.hi20", (place, symbol), span),
            self._inst("addi.lo12", (place, place, symbol), span),
            self._inst(mnemonic, (source, place, MCImm(address.disp, 12)),
                       span),
        )

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        if not isinstance(target, MCSymRef):
            raise UnsupportedOperation("only direct calls are generated yet", span)
        return (self._inst("jal", (target,), span),)

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        return (self._inst("ret", (), span),)


def lower_function(asm: "Assembler", func: "Function", cconv: "CallConvDesc",
                   registers: "RegisterInfo") -> None:
    """Build the machine form of one IR function."""
    from ...ir.inst import LoadInst, MemStartInst, RetInst, StoreInst
    from ...ir.mangle import symbol_name
    from ...ir.module import GlobalVar
    from ...ir.types import IntType
    from ...ir.value import IntConst
    from ..globals import symbol_of

    asm.begin_function(symbol_name(func),
                       exported=func.linkage.value == "exported")
    #: Where each value the function computes is held.  A value gets a register
    #: of its own and the allocator decides which; nothing here knows or cares.
    held: dict[int, VirtReg] = {}
    returned = _returned_value(func)
    for block in func.blocks:
        for inst in block.insts:
            span = inst.span if inst.span.is_valid else None
            match inst:
                case MemStartInst():
                    # Memory is not held in a register; the token exists to
                    # order the operations that touch it, and there is nothing
                    # yet for it to order.
                    pass
                case LoadInst():
                    address = inst.operands[1]
                    if not isinstance(address, GlobalVar):
                        raise UnsupportedOperation(
                            "reading through an address that is not a variable", span)
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.loadreg(
                        destination,
                        asm.mem(disp_sym=SymExpr(
                                    asm.streamer.symbol(symbol_of(address))),
                                rip_relative=True, size_bits=_width_of(inst.ty),
                                signed=_is_signed(inst.ty)),
                        inst.span)
                case StoreInst():
                    address = inst.operands[1]
                    if not isinstance(address, GlobalVar):
                        raise UnsupportedOperation(
                            "writing through an address that is not a variable", span)
                    written = inst.operands[2]
                    place = asm.mem(
                        disp_sym=SymExpr(asm.streamer.symbol(symbol_of(address))),
                        rip_relative=True, size_bits=_width_of(written.ty),
                        signed=_is_signed(written.ty))
                    if isinstance(written, IntConst):
                        asm.store(place, MCImm(
                            written.value,
                            _immediate_width(written.value, _is_signed(written.ty)),
                            signed=_is_signed(written.ty)), inst.span)
                    else:
                        # A store writes as much of the register as the width
                        # names and reads the rest not at all, so no narrower
                        # view of it has to be asked for here.
                        asm.store(place, MCReg(_value_of(written, held, span)),
                                  inst.span)
                case RetInst() if not inst.operands:
                    asm.ret(inst.span)
                case RetInst():
                    value = inst.operands[0]
                    ty = value.ty
                    if not isinstance(ty, IntType):
                        raise UnsupportedOperation("".join((
                            "returning a value of type '", ty.render(), "'")), span)
                    result = _result_register(ty, cconv, registers)
                    if isinstance(value, IntConst):
                        asm.loadreg(result, MCImm(value.value, 12,
                                                  signed=ty.signed), inst.span)
                    else:
                        asm.loadreg(result, MCReg(_value_of(value, held, span)),
                                    inst.span)
                    asm.ret(inst.span)
                case _:
                    raise UnsupportedOperation("".join((
                        "the instruction '", inst.opcode, "'")), span)
    asm.end_function()

def _result_register(ty: "Type", cconv: "CallConvDesc",
                     registers: "RegisterInfo") -> "PhysReg":
    """The register an instruction's result is put in.

    There is only one register width here, so a narrow value simply sits in a
    whole register; the instruction that produced it is what decided whether the
    bits above it are copies of its sign or zeroes.
    """
    del ty, registers
    return cconv.int_ret_regs[0]


def _new_value(ty: "Type", registers: "RegisterInfo",
               hint: "PhysReg | None" = None) -> VirtReg:
    """A register for a value the function computes.

    Every register here is the full width and a narrow value simply occupies
    one, so unlike the other two backends there is no view to choose.

    The hint says where the value is wanted anyway.  Taking it turns the move
    that would put it there into a move of a register to itself, which then goes.
    """
    del ty
    return registers.new_virtual(GPR, 64, hint=hint)


def _value_of(value: object, held: "dict[int, VirtReg]",
              span: "Span | None") -> VirtReg:
    """The register a value the function computed is in."""
    found = held.get(id(value))
    if found is None:
        raise UnsupportedOperation("a value this backend did not compute", span)
    return found


def _returned_value(func: "Function") -> object:
    """The value the function returns, where it computes one.

    It is worth knowing before the value is computed, because that is when the
    register it will be wanted in can be asked for.
    """
    from ...ir.inst import RetInst

    for block in func.blocks:
        for inst in block.insts:
            if isinstance(inst, RetInst) and inst.operands:
                return inst.operands[0]
    return None

def _width_of(ty: "Type") -> int:
    """How many bits a value of *ty* occupies in memory."""
    from ...ir.types import IntType

    return ty.bits if isinstance(ty, IntType) else 64


def _is_signed(ty: "Type") -> bool:
    """Whether a narrow value of *ty* is widened by its sign when it is read."""
    from ...ir.types import IntType

    return isinstance(ty, IntType) and ty.signed


def _immediate_width(value: int, signed: bool) -> int:
    """The narrowest standard width that holds *value*.

    It is the width the *encoding* uses, which is not the width of the access:
    an eight-byte store carries a four-byte immediate that the instruction
    widens, so what the operand has to say is how large the number is.
    """
    for bits in (8, 16, 32, 64):
        if signed:
            if -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
                return bits
        elif 0 <= value < (1 << bits):
            return bits
    return 64
