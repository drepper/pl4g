"""Turning a function into the name its symbol carries.

The mangled name is the function's signature written out: the name, the
parameter types in parentheses separated by commas, and then the result type.

    main()u8
    absdiff(i32,i32)i32
    take(ptr<u8>,u64)void

Nothing is encoded.  C++ compresses a mangled name into a form that has to be
decoded before anyone can read it, and then every tool that wants to show a
symbol needs a demangler.  Here the symbol *is* the signature, so a disassembly,
a symbol table listing, a profile and a backtrace all show what the reader wants
without any tool in between, and the compiler needs no demangler because there
is nothing to undo.  The names are longer; a symbol table is not where a program
spends its size.

A function that declares a foreign calling convention keeps its plain name.  The
point of declaring one is to be callable from a world that has never heard of
this language, and that world knows the function by the name it was given.
"""

from __future__ import annotations

from .function import Function
from .types import FuncType

#: What separates a module name from the function name within it.
MODULE_SEPARATOR = "."


def mangle(name: str, ty: FuncType, module: str = "") -> str:
    """Build the symbol name for a function of the given name and type."""
    parts: list[str] = []
    if module:
        parts.append(module)
        parts.append(MODULE_SEPARATOR)
    parts.append(name)
    parts.append("(")
    parts.append(",".join(p.mangled() for p in ty.params))
    parts.append(")")
    parts.append(ty.ret.mangled())
    return "".join(parts)


def symbol_name(func: Function) -> str:
    """The name *func* is known by in the generated image.

    This is a function of the IR alone, so every stage that needs the symbol
    computes the same one without anything having to be stored or passed along.
    """
    if func.attrs.abi is not None:
        return func.name
    return mangle(func.name, func.ty, func.module)
