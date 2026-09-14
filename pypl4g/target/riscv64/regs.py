"""The RISC-V 64-bit register file.

Two things distinguish this architecture from the other two.

There is no register that is only part of another: an integer register is
sixty-four bits and that is all it is.  Narrower arithmetic is a different
instruction, not a different view, so a unit here has exactly one view -- which
the unit and view model accommodates without doing anything special.

There is no condition-code register either.  A comparison and the branch that
acts on it are one instruction, so nothing writes flags and no class of them
exists.  The other two backends record which instructions write theirs precisely
so that a pass can ask; here the question does not arise.

Registers have two names apiece: the number the encoding uses, and the name the
calling convention gives the role.  Both name the same object, so two spellings
of one register are the same register and not merely equal ones.
"""

from __future__ import annotations

from typing import Final

from ...mc.reg import PhysReg, RegClass, RegisterInfo

INFO: Final[RegisterInfo] = RegisterInfo()

GPR: Final[RegClass] = INFO.add_class("gpr", 64)
#: The floating-point registers of the F and D extensions.
FPR: Final[RegClass] = INFO.add_class("fpr", 64)
#: The vector registers of the V extension.  Their width is set by the
#: implementation; the smallest one the extension allows is used here.
VEC: Final[RegClass] = INFO.add_class("vec", 128)

#: The name the calling convention gives each register, by number.
ABI_NAMES: Final[tuple[str, ...]] = (
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
    "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
    "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7",
    "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6",
)

for _enc, _abi in enumerate(ABI_NAMES):
    _unit = INFO.add_unit(GPR, _enc, _abi)
    _reg = INFO.add_view(_unit, _abi, 64)
    INFO.add_alias("".join(("x", str(_enc))), _reg)

#: The frame pointer has a second conventional name.
INFO.add_alias("fp", INFO.registers["s0"])

for _enc in range(32):
    _fp_unit = INFO.add_unit(FPR, _enc, "".join(("f", str(_enc))))
    INFO.add_view(_fp_unit, "".join(("f", str(_enc))), 64)
    INFO.add_view(_fp_unit, "".join(("fs", str(_enc))), 32)

for _enc in range(32):
    _vec_unit = INFO.add_unit(VEC, _enc, "".join(("v", str(_enc))))
    INFO.add_view(_vec_unit, "".join(("v", str(_enc))), 128)


def reg(name: str) -> PhysReg:
    """Return the register called *name*, by either of its names."""
    return INFO.registers[name]


#: Reads as zero; writes to it are discarded.  Several pseudo-instructions are
#: an ordinary instruction with this register in one operand.
ZERO: Final[PhysReg] = reg("zero")
#: The return address, which a call writes.
RA: Final[PhysReg] = reg("ra")
SP: Final[PhysReg] = reg("sp")
#: The frame pointer.
FP: Final[PhysReg] = reg("s0")
A0: Final[PhysReg] = reg("a0")
A1: Final[PhysReg] = reg("a1")
#: The register a system call number is passed in.
A7: Final[PhysReg] = reg("a7")
S1: Final[PhysReg] = reg("s1")

#: Registers a called function must leave as it found them.
CALLEE_SAVED_NAMES: Final[tuple[str, ...]] = (
    "sp", "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11")
#: Registers a call is free to change, the return address among them.
CALLER_SAVED_NAMES: Final[tuple[str, ...]] = (
    "ra", "t0", "t1", "t2", "t3", "t4", "t5", "t6",
    "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7")


#: The registers a call destroys.  See the note beside the same name in the
#: x86-64 backend.
CALLER_SAVED: Final[tuple[PhysReg, ...]] = tuple(
    reg(name) for name in
    ("ra", "t0", "t1", "t2", "t3", "t4", "t5", "t6",
     "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7"))
