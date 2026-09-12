"""Symbols and sections of the symbolic assembler."""

from dataclasses import dataclass, field
from enum import Enum


class SymBinding(Enum):
    """How widely a symbol is visible."""

    LOCAL = "local"
    GLOBAL = "global"
    WEAK = "weak"


class SymVisibility(Enum):
    """How far a symbol is visible outside the image it is defined in.

    It is a separate question from the binding.  A binding says whether the
    symbol is one name among many in this image or a name the whole program
    shares; visibility says whether anything outside may reach it, and it is the
    one that survives a symbol being made global by something later on.
    """

    DEFAULT = "default"
    INTERNAL = "internal"
    HIDDEN = "hidden"
    PROTECTED = "protected"


class SymKind(Enum):
    """What a symbol names."""

    NOTYPE = "notype"
    FUNC = "func"
    OBJECT = "object"
    SECTION = "section"
    FILE = "file"


@dataclass(slots=True, eq=False)
class MCSymbol:
    """A named place in the generated image."""

    name: str
    section: "MCSection | None" = None
    #: Index of the fragment the symbol sits in front of.  The byte offset is
    #: not known until the section is laid out, because alignment padding has no
    #: size until then.
    fragment_index: int | None = None
    #: Index of the fragment the symbol's definition ends in front of.
    end_fragment_index: int | None = None
    #: Offset within the section, filled in when fragments are laid out.
    offset: int = 0
    size: int = 0
    defined: bool = False
    binding: SymBinding = SymBinding.LOCAL
    visibility: SymVisibility = SymVisibility.DEFAULT
    kind: SymKind = SymKind.NOTYPE
    #: Temporary labels are used for control flow and are not put in the symbol
    #: table of the image.
    temporary: bool = False
    #: The address the symbol ends up at, filled in by the image layout.
    vaddr: int | None = None

    def __repr__(self) -> str:
        return "".join(("MCSymbol(", self.name, ")"))


@dataclass(slots=True, eq=False)
class MCSection:
    """A run of fragments that end up contiguous in the image."""

    name: str
    #: Whether the section occupies memory when the program runs.
    alloc: bool = True
    readable: bool = True
    writable: bool = False
    executable: bool = False
    alignment: int = 1
    fragments: list[object] = field(default_factory=list)
    #: Offset within the section of the end of the last fragment laid out.
    size: int = 0
    vaddr: int = 0

    def __repr__(self) -> str:
        return "".join(("MCSection(", self.name, ")"))
