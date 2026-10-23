"""What a shared object has that an executable has not.

It has no entry point: the program that loads it calls what it offers, which is
its visible functions and variables.  Its constructors are run when it is
loaded and its destructors when it is unloaded, by the loader, from a table of
each -- the destructors' read from its end, so that they run in the order the
program gives them, which is the opposite of its constructors'.  Each was given
the system's convention for that, the loader calling the way the system calls.
And it reaches what the host defines through stubs that ask `dlsym` (`host.py`),
which is what it needs the C library for.
"""

from __future__ import annotations

from typing import Final

from ..elf.const import SHT_FINI_ARRAY, SHT_INIT_ARRAY
from ..ir.function import Linkage
from ..ir.mangle import symbol_name
from ..ir.module import Module
from ..mc.asmbuilder import Assembler
from ..mc.fixup import ABS64, MCFixup
from ..mc.operand import SymExpr
from ..source.manager import SourceManager
from . import host
from .callconv import CallConvDesc
from .faults import Messages
from .globals import symbol_of

#: The library `dlsym` is in, which is the one a shared object depends on.
LIBC: Final[str] = "libc.so.6"

#: The tables of constructors and destructors.
INIT_ARRAY: Final[str] = ".init_array"
FINI_ARRAY: Final[str] = ".fini_array"


def emit(asm: Assembler, module: Module, abi: host.HostABI, cconv: CallConvDesc,
         messages: Messages, sources: SourceManager | None, text_alignment: int,
         abort: str) -> None:
    """Emit the stubs for what the host defines, and the two tables."""
    host.emit(asm, module, abi, cconv, messages, sources, text_alignment, abort)
    # The loader runs the table in order: the `constinit` variables' initializers
    # first, as the entry point of an executable calls them, then the
    # constructors.
    starts = [*(one.func for one in module.initializers), *module.ctors]
    for name, sh_type, funcs in ((INIT_ARRAY, SHT_INIT_ARRAY, starts),
                                 (FINI_ARRAY, SHT_FINI_ARRAY, module.dtors[::-1])):
        if not funcs:
            continue
        asm.section(name, writable=True, alignment=8, sh_type=sh_type, sh_entsize=8)
        for func in funcs:
            asm.bytes(bytes(8), (MCFixup(offset=0, kind=ABS64,
                                         target=SymExpr(asm.symbol_named(
                                             symbol_name(func)))),))
    # And back to the code, which is where what comes after expects to be.
    asm.section(".text", executable=True, alignment=text_alignment)


def exports(module: Module) -> tuple[str, ...]:
    """What a shared object offers: its visible functions, then its variables."""
    return (*(symbol_name(func) for func in module.functions.values()
              if func.linkage is Linkage.VISIBLE and not func.is_declaration),
            *(symbol_of(var) for var in module.globals.values()
              if var.linkage is Linkage.VISIBLE))
