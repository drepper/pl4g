"""The layout model of a generated image.

Every offset, address and size is computed before a single byte is written.  That
is what lets the headers be packed without back-patching, and it is the only
structure from which incremental recompilation can work: the layout is the map of
where each function lives and how much slack follows it, so a rebuilt function
that still fits can be patched in place without moving anything else.
"""

from dataclasses import dataclass, field
from enum import Enum


class ImageKind(Enum):
    """The kind of image to produce.

    Only the fixed-address executable is implemented.  A position-independent
    executable differs in the type field, a load bias of zero, a dynamic section
    and a relocation list, and a self-relocation prologue -- all of which this
    model already parameterizes through ``base_vaddr``.
    """

    EXECUTABLE = "exec"
    PIE = "pie"


@dataclass(slots=True)
class Chunk:
    """One contiguous run of bytes in the image."""

    name: str
    size: int
    align: int = 1
    #: Growth slack reserved after the chunk, for later in-place patching.
    padding: int = 0
    data: bytes = b""
    file_offset: int = 0
    vaddr: int | None = None

    @property
    def total_size(self) -> int:
        """The bytes the chunk occupies, including its reserved slack."""
        return self.size + self.padding


@dataclass(slots=True)
class SectionPlan:
    """One section of the image."""

    name: str
    sh_type: int
    sh_flags: int = 0
    sh_addralign: int = 1
    sh_entsize: int = 0
    sh_link: int = 0
    sh_info: int = 0
    chunks: list[Chunk] = field(default_factory=list)
    index: int = 0
    addr: int = 0
    offset: int = 0
    size: int = 0
    #: A section that occupies memory when the program runs.
    alloc: bool = False


@dataclass(slots=True)
class SegmentPlan:
    """One program header."""

    p_type: int
    p_flags: int
    p_align: int = 1
    p_offset: int = 0
    p_vaddr: int = 0
    p_filesz: int = 0
    p_memsz: int = 0


@dataclass(slots=True)
class SymbolPlan:
    """One entry of the symbol table."""

    name: str
    value: int = 0
    size: int = 0
    binding: int = 0
    kind: int = 0
    shndx: int = 0
    visibility: int = 0


@dataclass(slots=True)
class FunctionExtent:
    """Where one function lives in the image, and how much room it has to grow."""

    name: str
    file_offset: int
    vaddr: int
    size: int
    padding: int


@dataclass(slots=True)
class ImageLayout:
    """Everything about the image, computed before any byte is written."""

    kind: ImageKind
    machine: int
    base_vaddr: int
    page_size: int
    entry_symbol: str
    entry_vaddr: int = 0
    chunks: list[Chunk] = field(default_factory=list)
    sections: list[SectionPlan] = field(default_factory=list)
    segments: list[SegmentPlan] = field(default_factory=list)
    symbols: list[SymbolPlan] = field(default_factory=list)
    functions: list[FunctionExtent] = field(default_factory=list)
    by_name: dict[str, Chunk] = field(default_factory=dict)
    total_size: int = 0

    def chunk(self, name: str) -> Chunk:
        """The chunk called *name*."""
        return self.by_name[name]


def align_up(value: int, alignment: int) -> int:
    """Round *value* up to the next multiple of *alignment*."""
    if alignment <= 1:
        return value
    remainder = value % alignment
    return value if remainder == 0 else value + alignment - remainder
