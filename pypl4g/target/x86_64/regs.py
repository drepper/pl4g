"""The x86-64 register file.

Every register is a view onto a unit, so the width variants of a general-purpose
register and the width variants of a vector register are described by the same
mechanism.  The general-purpose class holds thirty-two units from the start: the
sixteen the base architecture has and the sixteen more that the extended
general-purpose registers add, so nothing here changes when they are encoded --
only the prefix the encoder emits does.
"""

from typing import Final

from ...mc.reg import PhysReg, RegClass, RegisterInfo, RegUnit

INFO: Final[RegisterInfo] = RegisterInfo()

GPR: Final[RegClass] = INFO.add_class("gpr", 64)
VEC: Final[RegClass] = INFO.add_class("vec", 512)
KMASK: Final[RegClass] = INFO.add_class("kmask", 64)
FLAGS: Final[RegClass] = INFO.add_class("flags", 64, allocatable=False)
SEG: Final[RegClass] = INFO.add_class("seg", 16, allocatable=False)

#: The sixteen registers of the base architecture, in encoding order.
_BASE_NAMES: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("rax", "eax", "ax", "al"),
    ("rcx", "ecx", "cx", "cl"),
    ("rdx", "edx", "dx", "dl"),
    ("rbx", "ebx", "bx", "bl"),
    ("rsp", "esp", "sp", "spl"),
    ("rbp", "ebp", "bp", "bpl"),
    ("rsi", "esi", "si", "sil"),
    ("rdi", "edi", "di", "dil"),
)

#: The high-byte views, which exist only for the first four units and which no
#: instruction carrying a REX prefix may name.
_HIGH_BYTE: Final[dict[int, str]] = {0: "ah", 1: "ch", 2: "dh", 3: "bh"}

#: The number of general-purpose units.  Sixteen of them need an encoding the
#: base architecture does not have.
GPR_UNIT_COUNT: Final[int] = 32

#: Units at or above this encoding need one of the extended prefixes.
GPR_EXTENDED_FIRST: Final[int] = 16

_units: dict[int, RegUnit] = {}


def _add_gpr(enc: int) -> None:
    """Register one general-purpose unit and all of its views."""
    if enc < len(_BASE_NAMES):
        wide, dword, word, byte = _BASE_NAMES[enc]
    else:
        wide = "".join(("r", str(enc)))
        dword, word, byte = "".join((wide, "d")), "".join((wide, "w")), "".join((wide, "b"))
    unit = INFO.add_unit(GPR, enc, wide)
    _units[enc] = unit
    INFO.add_view(unit, wide, 64)
    INFO.add_view(unit, dword, 32)
    INFO.add_view(unit, word, 16)
    INFO.add_view(unit, byte, 8)
    high = _HIGH_BYTE.get(enc)
    if high is not None:
        INFO.add_view(unit, high, 8, byte_off=1)


for _enc in range(GPR_UNIT_COUNT):
    _add_gpr(_enc)

for _enc in range(32):
    _vec_unit = INFO.add_unit(VEC, _enc, "".join(("zmm", str(_enc))))
    INFO.add_view(_vec_unit, "".join(("xmm", str(_enc))), 128)
    INFO.add_view(_vec_unit, "".join(("ymm", str(_enc))), 256)
    INFO.add_view(_vec_unit, "".join(("zmm", str(_enc))), 512)

for _enc in range(8):
    _mask_unit = INFO.add_unit(KMASK, _enc, "".join(("k", str(_enc))))
    INFO.add_view(_mask_unit, "".join(("k", str(_enc))), 64)

_flags_unit = INFO.add_unit(FLAGS, 0, "eflags")
EFLAGS: Final[PhysReg] = INFO.add_view(_flags_unit, "eflags", 64)

for _enc, _name in enumerate(("es", "cs", "ss", "ds", "fs", "gs")):
    _seg_unit = INFO.add_unit(SEG, _enc, _name)
    INFO.add_view(_seg_unit, _name, 16)


def reg(name: str) -> PhysReg:
    """Return the register called *name*."""
    return INFO.registers[name]


RAX: Final[PhysReg] = reg("rax")
RCX: Final[PhysReg] = reg("rcx")
RDX: Final[PhysReg] = reg("rdx")
RBX: Final[PhysReg] = reg("rbx")
RSP: Final[PhysReg] = reg("rsp")
RBP: Final[PhysReg] = reg("rbp")
RSI: Final[PhysReg] = reg("rsi")
RDI: Final[PhysReg] = reg("rdi")
R11: Final[PhysReg] = reg("r11")

EAX: Final[PhysReg] = reg("eax")
ECX: Final[PhysReg] = reg("ecx")
EDX: Final[PhysReg] = reg("edx")
EBP: Final[PhysReg] = reg("ebp")
EDI: Final[PhysReg] = reg("edi")
ESI: Final[PhysReg] = reg("esi")

#: The byte registers that require an empty REX prefix to be nameable at all.
REX_REQUIRED_BYTE_REGS: Final[frozenset[str]] = frozenset(("spl", "bpl", "sil", "dil"))

#: The byte registers that no instruction with a REX prefix may name.
HIGH_BYTE_REGS: Final[frozenset[str]] = frozenset(_HIGH_BYTE.values())

#: Offsets of the segment override prefixes.
SEGMENT_PREFIX: Final[dict[str, int]] = {
    "es": 0x26, "cs": 0x2E, "ss": 0x36, "ds": 0x3E, "fs": 0x64, "gs": 0x65,
}


#: The registers a call destroys, which is what the language's convention and
#: the system's both say.  They are named here rather than taken from a
#: convention because the instruction table cannot reach one; the two
#: conventions this target has name the same set, so nothing is lost by it yet.
CALLER_SAVED: Final[tuple[PhysReg, ...]] = tuple(
    reg(name) for name in
    ("rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11"))
