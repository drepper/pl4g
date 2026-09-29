"""The AArch64 register file.

The same unit and view split the x86-64 backend uses describes this
architecture too: a unit is storage, a register is a view onto it at a width.
Here the views are simply the 64-bit and 32-bit forms of each general register,
and the byte through quadword forms of each vector register.

One wrinkle is worth stating.  The register number 31 means two different things
depending on the instruction: the zero register in most encodings, and the stack
pointer in a few.  They are modelled as two units that happen to share an
encoding, because that is what they are -- writing the stack pointer changes
something, writing the zero register does not -- and because it keeps the
interference rule honest: the stack pointer does not interfere with the zero
register.
"""

from __future__ import annotations

from typing import Final

from ...mc.reg import PhysReg, RegClass, RegisterInfo, RegUnit

INFO: Final[RegisterInfo] = RegisterInfo()

GPR: Final[RegClass] = INFO.add_class("gpr", 64)
#: The SIMD and floating-point registers.  The width is the one the base
#: architecture has; the scalable vectors widen the same units rather than
#: introducing new ones, so nothing here changes when they are added.
VEC: Final[RegClass] = INFO.add_class("vec", 128)
#: The scalable-vector predicate registers.
PRED: Final[RegClass] = INFO.add_class("pred", 16)
FLAGS: Final[RegClass] = INFO.add_class("flags", 32, allocatable=False)

#: The number that names the zero register or the stack pointer.
SP_ENC: Final[int] = 31

_units: dict[str, RegUnit] = {}

for _enc in range(31):
    _unit = INFO.add_unit(GPR, _enc, "".join(("x", str(_enc))))
    _units["".join(("x", str(_enc)))] = _unit
    INFO.add_view(_unit, "".join(("x", str(_enc))), 64)
    INFO.add_view(_unit, "".join(("w", str(_enc))), 32)

#: The zero register: reads as zero, writes are discarded.
_zero_unit = INFO.add_unit(GPR, SP_ENC, "xzr")
XZR: Final[PhysReg] = INFO.add_view(_zero_unit, "xzr", 64)
WZR: Final[PhysReg] = INFO.add_view(_zero_unit, "wzr", 32)

#: The stack pointer, which shares the zero register's number but not its
#: meaning.  Not allocatable, so it is a unit of its own outside the pool.
_sp_unit = INFO.add_unit(GPR, SP_ENC, "sp")
SP: Final[PhysReg] = INFO.add_view(_sp_unit, "sp", 64)
WSP: Final[PhysReg] = INFO.add_view(_sp_unit, "wsp", 32)

for _enc in range(32):
    _vec_unit = INFO.add_unit(VEC, _enc, "".join(("v", str(_enc))))
    for _prefix, _bits in (("b", 8), ("h", 16), ("s", 32), ("d", 64), ("q", 128)):
        INFO.add_view(_vec_unit, "".join((_prefix, str(_enc))), _bits)

for _enc in range(16):
    _pred_unit = INFO.add_unit(PRED, _enc, "".join(("p", str(_enc))))
    INFO.add_view(_pred_unit, "".join(("p", str(_enc))), 16)

_flags_unit = INFO.add_unit(FLAGS, 0, "nzcv")
#: The condition flags.  Named so that an instruction which writes them can say
#: so, which is what a later peephole or scheduler needs to know.
NZCV: Final[PhysReg] = INFO.add_view(_flags_unit, "nzcv", 32)


def reg(name: str) -> PhysReg:
    """Return the register called *name*."""
    return INFO.registers[name]


X0: Final[PhysReg] = reg("x0")
X1: Final[PhysReg] = reg("x1")
X8: Final[PhysReg] = reg("x8")
X19: Final[PhysReg] = reg("x19")
#: The frame pointer.
X29: Final[PhysReg] = reg("x29")
#: The link register, which a call writes.
X30: Final[PhysReg] = reg("x30")

W0: Final[PhysReg] = reg("w0")
W8: Final[PhysReg] = reg("w8")
W19: Final[PhysReg] = reg("w19")

#: Registers the caller must preserve across a call, by the standard procedure
#: call standard.
CALLEE_SAVED_NAMES: Final[tuple[str, ...]] = (
    "x19", "x20", "x21", "x22", "x23", "x24", "x25", "x26", "x27", "x28", "x29")
CALLER_SAVED_NAMES: Final[tuple[str, ...]] = tuple(
    "".join(("x", str(n))) for n in range(19))
