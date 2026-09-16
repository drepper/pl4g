"""What a program is started with, and where the entry point puts it.

The startup function may take the record the `std` module calls `Init`, whose
one field holds the three devices a process inherits.  Which registers those
arrive in is the calling convention's answer and not a thing written down three
times, so the entry points ask here and each of them only has to know how to
write a number into a register.
"""

from __future__ import annotations

from typing import Final, Sequence

from ..ir.module import Module
from ..mc.reg import PhysReg
from .callconv import CallConvDesc, argument_places

#: The descriptors every process starts with open, in the order `std.Io` writes
#: them down: what it reads from, what it writes to, and where it reports.  The
#: numbers are the system's and are the same on every one of these targets.
INHERITED: Final[tuple[int, ...]] = (0, 1, 2)


def handed_over(module: Module,
                cconv: CallConvDesc) -> Sequence[tuple[PhysReg, int]]:
    """Each register the startup function's one argument arrives in, and what
    goes in it.

    Nothing where the program wrote no parameter, which is most of them: a
    program that wants neither the devices it inherited nor anything else that
    came with it is started with the registers left as the kernel had them.
    """
    startup = module.startup
    if startup is None or not startup.ty.params:
        return ()
    places = argument_places(cconv, startup.ty.params)[0]
    assert len(places) == len(INHERITED), (len(places), len(INHERITED))
    return tuple(zip(places, INHERITED))
