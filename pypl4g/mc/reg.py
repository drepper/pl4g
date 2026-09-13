"""The register model.

A ``RegUnit`` is physical storage; a ``PhysReg`` is a view onto a unit at a given
width and byte offset.  That one mechanism describes ``al``/``ah``/``ax``/``eax``
/``rax`` today and ``xmm0``/``ymm0``/``zmm0`` later without any new concept, and
it makes the rule for whether two registers interfere -- same unit, overlapping
byte range -- correct for both.

Register classes are a registry rather than a fixed enumeration, so a target adds
general-purpose, extended general-purpose, vector, mask or any other kind of
register without changing anything here.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RegClass:
    """A family of registers that an allocator can treat alike."""

    name: str
    index: int
    #: The width of the widest view of a register of this class.
    full_bits: int
    allocatable: bool = True


@dataclass(frozen=True, slots=True)
class RegUnit:
    """One physical storage location."""

    cls: RegClass
    #: The number the hardware encoding uses for this register.
    enc: int
    canonical: str

    def __repr__(self) -> str:
        return "".join(("RegUnit(", self.canonical, ")"))


@dataclass(frozen=True, slots=True)
class PhysReg:
    """A view onto a register unit at a given width and offset."""

    unit: RegUnit
    name: str
    bits: int
    #: Offset in bytes of this view within the unit; 1 for ah, ch, dh and bh.
    byte_off: int = 0

    @property
    def cls(self) -> RegClass:
        """The class of the underlying unit."""
        return self.unit.cls

    @property
    def enc(self) -> int:
        """The hardware encoding of the underlying unit."""
        return self.unit.enc

    @property
    def byte_range(self) -> tuple[int, int]:
        """The half-open range of bytes of the unit this view covers."""
        return (self.byte_off, self.byte_off + self.bits // 8)

    def __repr__(self) -> str:
        return "".join(("PhysReg(", self.name, ")"))


@dataclass(frozen=True, slots=True)
class VirtReg:
    """A register the allocator has yet to assign.

    Virtual registers are in the operand model from the start.  The encoder
    rejects them, and that rejection is exactly the contract the future register
    allocator has to satisfy.
    """

    ident: int
    cls: RegClass
    bits: int
    hint: PhysReg | None = None
    #: Whether the allocator may put this in the frame instead of a register.
    #: A register held across two instructions whose relocations refer to each
    #: other may not be: spilling it would put instructions between them, and
    #: the second measures from the first.
    spillable: bool = True

    def __repr__(self) -> str:
        return "".join(("VirtReg(%v", str(self.ident), ")"))


type Reg = PhysReg | VirtReg


def interferes(left: Reg, right: Reg) -> bool:
    """Whether two registers name storage that overlaps."""
    if isinstance(left, VirtReg) or isinstance(right, VirtReg):
        return left is right
    if left.unit is not right.unit:
        return False
    left_lo, left_hi = left.byte_range
    right_lo, right_hi = right.byte_range
    return left_lo < right_hi and right_lo < left_hi


@dataclass(slots=True)
class RegisterInfo:
    """The register file of one target."""

    classes: dict[str, RegClass] = field(default_factory=dict)
    units: dict[str, RegUnit] = field(default_factory=dict)
    registers: dict[str, PhysReg] = field(default_factory=dict)
    _next_class_index: int = 0
    _next_virtual: int = 0

    def add_class(self, name: str, full_bits: int, allocatable: bool = True) -> RegClass:
        """Register a new class of registers."""
        cls = RegClass(name=name, index=self._next_class_index, full_bits=full_bits,
                       allocatable=allocatable)
        self._next_class_index += 1
        self.classes[name] = cls
        return cls

    def add_unit(self, cls: RegClass, enc: int, canonical: str) -> RegUnit:
        """Register a new storage location."""
        unit = RegUnit(cls=cls, enc=enc, canonical=canonical)
        self.units[canonical] = unit
        return unit

    def add_view(self, unit: RegUnit, name: str, bits: int, byte_off: int = 0) -> PhysReg:
        """Register a view onto *unit* and return it."""
        reg = PhysReg(unit=unit, name=name, bits=bits, byte_off=byte_off)
        self.registers[name] = reg
        return reg

    def new_virtual(self, cls: RegClass, bits: int, hint: PhysReg | None = None,
                    spillable: bool = True) -> VirtReg:
        """Allocate a fresh virtual register."""
        reg = VirtReg(ident=self._next_virtual, cls=cls, bits=bits, hint=hint,
                      spillable=spillable)
        self._next_virtual += 1
        return reg

    def add_alias(self, name: str, reg: PhysReg) -> PhysReg:
        """Register another name for a register that already exists.

        The same object is stored under both names, so that two spellings of one
        register compare as the same register rather than merely as equal ones.
        """
        self.registers[name] = reg
        return reg

    def view(self, unit: RegUnit, bits: int, byte_off: int = 0) -> PhysReg:
        """The view onto *unit* of the given width, at the given byte offset."""
        for reg in self.registers.values():
            if reg.unit is unit and reg.bits == bits and reg.byte_off == byte_off:
                return reg
        raise KeyError("".join((unit.canonical, " has no ", str(bits), "-bit view")))

    def members_of(self, cls: RegClass) -> list[RegUnit]:
        """Every storage location of the class *cls*."""
        return [u for u in self.units.values() if u.cls is cls]
