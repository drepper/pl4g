"""Finding what the host defines, the first time it is called.

A function the host defines is called through a stub of this image's own, one
per symbol.  The stub reads a slot that holds where the function is; where the
slot holds something it goes there, the arguments and the return address being
just as the caller left them.  The first time the slot holds nothing, and the
stub asks the finder, which asks `dlsym` and fills the slot in.

**`dlsym` is reached through a slot the loader fills**, the one relocation of a
shared object that names a symbol rather than adding where the object was put.
It is asked with `RTLD_DEFAULT`, which is nought, so what is searched is what
the program loaded globally: the program itself and the libraries it needs.

**The finder is written once for the three architectures**, from what each
one's convention says its arguments are passed in.  It keeps every one of those
across the call, the floating-point ones whole, and the slot it was handed:
what it hands back is in the register the name came in.  A stub whose symbol
the host does not define stops the program, saying which, with a status of its
own; nothing is left in the slot, there being nothing to leave.

Each stub is three registers' business, which are none of the arguments': the
slot, the name and what was found, and -- where a call writes the return address
to a register rather than the stack -- the return address the stub keeps across
its call to the finder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Sequence

from ..ir.function import Function
from ..ir.mangle import HOST_PREFIX, symbol_name
from ..ir.module import Module
from ..mc import ops
from ..mc.asmbuilder import Assembler
from ..mc.operand import MCImm, MCReg
from ..mc.ops import Condition
from ..mc.reg import Reg
from ..mc.symbol import SymBinding, SymKind, SymVisibility
from ..source.manager import SourceManager
from . import statuses
from .callconv import CallConvDesc
from .faults import Messages, describe

#: The finder every stub calls.
FIND_SYMBOL: Final[str] = "__pl4g_host_find"

#: The slot the loader puts `dlsym`'s address in, and what it is asked for.
DLSYM_SLOT: Final[str] = "__pl4g_dlsym"
DLSYM: Final[str] = "dlsym"

#: What the slot a stub reads is called, and the symbol's name it asks for,
#: before the host's symbol.
SLOT_PREFIX: Final[str] = "__pl4g_host_slot."
NAME_PREFIX: Final[str] = "__pl4g_host_name."

#: What the declaration that takes C's text in place of a `str` is called, after
#: the name the program gave the function: the checker makes it, and a message
#: about it is about the function the program wrote.
CROSSING_SUFFIX: Final[str] = ".host"

#: Where the loader's slots go, which is where every linker puts them.
GOT_SECTION: Final[str] = ".got"


@dataclass(frozen=True, slots=True)
class HostABI:
    """The registers a stub and the finder work in, which no argument is in."""

    #: Where a stub hands the finder its slot.
    slot: Reg
    #: Where it hands the name, and where the finder answers.
    name: Reg
    #: What the finder calls `dlsym` through.
    through: Reg
    #: What a stub keeps across its call to the finder, where a call writes the
    #: return address to a register: nothing where it pushes it.
    link: Reg | None = None
    #: How much room a stub makes around its call to the finder: enough to keep
    #: the link register in, and to keep the stack as aligned at the call as
    #: the convention has it.
    room: int = 16


def hosted(module: Module) -> list[Function]:
    """The functions the host defines that the program calls, one per symbol.

    In the order the module holds them, which is the order the program declared
    them in.  A declaration nothing calls has no stub: it would be a stub for
    nothing.
    """
    called: set[str] = set()
    for func in module.functions.values():
        for block in func.blocks:
            for inst in block.insts:
                for named in inst.references():
                    if isinstance(named, Function) and named.attrs.host is not None:
                        called.add(named.attrs.host)
    found: dict[str, Function] = {}
    for func in module.functions.values():
        if func.attrs.host is not None and func.attrs.host in called:
            found.setdefault(func.attrs.host, func)
    return list(found.values())


def emit(asm: Assembler, module: Module, abi: HostABI, cconv: CallConvDesc,
         messages: Messages, sources: SourceManager | None, text_alignment: int,
         abort: str) -> bool:
    """Emit the slots, the names, a stub per symbol and the finder.

    *cconv* is the system's convention, which is the one a host function is
    called with and so the one whose arguments the finder keeps; *abort* is the
    helper a stub stops the program through.  Answers whether there was anything
    to emit, which is whether the image needs `dlsym`.
    """
    found = hosted(module)
    if not found:
        return False
    asm.section(GOT_SECTION, writable=True, alignment=8)
    _object(asm, DLSYM_SLOT, bytes(8))
    asm.section(".data", writable=True, alignment=8)
    # After whatever the program's own variables left, which need not end on
    # a word.
    asm.align(8)
    for func in found:
        _object(asm, "".join((SLOT_PREFIX, func.attrs.host or "")), bytes(8))
    asm.section(".rodata", writable=False, alignment=1)
    for func in found:
        _object(asm, "".join((NAME_PREFIX, func.attrs.host or "")),
                (func.attrs.host or "").encode("utf-8") + b"\0")
    asm.section(".text", executable=True, alignment=text_alignment)
    for func in found:
        _emit_stub(asm, func, abi, cconv, messages, sources, abort)
    _emit_find(asm, abi, cconv)
    return True


def _object(asm: Assembler, name: str, data: bytes) -> None:
    """Put *data* into the current section under the local symbol *name*."""
    symbol = asm.label(name, binding=SymBinding.LOCAL, kind=SymKind.OBJECT,
                       visibility=SymVisibility.HIDDEN)
    asm.bytes(data)
    asm.end_label(symbol)


def _emit_stub(asm: Assembler, func: Function, abi: HostABI, cconv: CallConvDesc,
               messages: Messages, sources: SourceManager | None,
               abort: str) -> None:
    """Emit the stub a call to *func* reaches."""
    symbol = func.attrs.host or ""
    name = func.name.removesuffix(CROSSING_SUFFIX)
    text = describe("".join(("the host defines no '", symbol, "'")), name, func.span,
                    sources)
    message = messages.symbol(text)
    arguments = (*cconv.int_arg_regs, *cconv.float_arg_regs)
    asm.begin_function(symbol_name(func), exported=False)
    find = asm.reserve_label("".join((HOST_PREFIX, symbol, ".find")))
    missing = asm.reserve_label("".join((HOST_PREFIX, symbol, ".missing")))
    asm.address(abi.slot, "".join((SLOT_PREFIX, symbol)))
    asm.loadreg(abi.name, asm.mem(base=abi.slot, disp=0, size_bits=64))
    asm.branch(Condition.EQ, MCReg(abi.name), MCImm(0, 32, signed=False), find)
    asm.tail_jump(MCReg(abi.name), reads=arguments)
    asm.block(find)
    asm.address(abi.name, "".join((NAME_PREFIX, symbol)))
    # The call below writes the return address where this stub's own is, on
    # an architecture that keeps it in a register, which the function it goes
    # to will want; and it is made with the stack aligned.
    asm.frame(abi.room)
    if abi.link is not None:
        asm.put_aside(0, abi.link)
    asm.call(FIND_SYMBOL, reads=(*arguments, abi.slot, abi.name))
    if abi.link is not None:
        asm.take_back(abi.link, 0)
    asm.unframe(abi.room)
    asm.branch(Condition.EQ, MCReg(abi.name), MCImm(0, 32, signed=False), missing)
    asm.tail_jump(MCReg(abi.name), reads=arguments)
    asm.block(missing)
    first, second, third = cconv.int_arg_regs[:3]
    asm.address(first, message)
    asm.loadreg(second, asm.imm(len(text.encode("utf-8")), 32, signed=False))
    asm.loadreg(third, asm.imm(statuses.HOST_SYMBOL_MISSING, 32, signed=False))
    asm.call(abort)
    asm.op(ops.TRAP)
    asm.end_function()


def _emit_find(asm: Assembler, abi: HostABI, cconv: CallConvDesc) -> None:
    """Emit the finder: `dlsym(RTLD_DEFAULT, name)`, kept in the slot.

    Every argument register is kept across the call, and the slot; each has a
    room of sixteen bytes, which holds the whole of a floating-point register
    on every architecture and keeps the stack as aligned as it came.
    """
    kept: Sequence[Reg] = (*cconv.int_arg_regs, *cconv.float_arg_regs, abi.slot)
    room = 16 * len(kept)
    first, second = cconv.int_arg_regs[:2]
    asm.begin_function(FIND_SYMBOL, exported=False)
    asm.frame(room)
    for at, reg in enumerate(kept):
        asm.put_aside(16 * at, reg)
    asm.loadreg(second, MCReg(abi.name))
    asm.loadreg(first, asm.imm(0, 32, signed=False))
    asm.address(abi.through, DLSYM_SLOT)
    asm.loadreg(abi.through, asm.mem(base=abi.through, disp=0, size_bits=64))
    asm.call(MCReg(abi.through), reads=(first, second))
    asm.loadreg(abi.name, MCReg(cconv.int_ret_regs[0]))
    asm.take_back(abi.slot, 16 * (len(kept) - 1))
    asm.store(asm.mem(base=abi.slot, disp=0, size_bits=64), MCReg(abi.name))
    for at, reg in enumerate(kept[:-1]):
        asm.take_back(reg, 16 * at)
    asm.unframe(room)
    asm.ret(reads=(abi.name,))
    asm.end_function()
