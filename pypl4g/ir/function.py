"""Basic blocks, functions and the attributes a function carries."""

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Mapping, Sequence

from ..source.location import INVALID_SPAN, Span
from .inst import Instruction, Terminator
from .types import FuncType, Type
from .value import BlockParam


class Linkage(Enum):
    """How visible a definition is outside the program being compiled."""

    INTERNAL = "internal"
    EXPORTED = "exported"
    IMPORTED = "imported"


class SpecialKind(StrEnum):
    """The ways a function can take part in the life of a program.

    The three test flavours are the three the specification distinguishes: those
    that run after a build and at first start, those that run when a build
    finishes, and those that run when a testsuite run is requested.
    """

    STARTUP = "startup"
    CONSTRUCTOR = "constructor"
    DESTRUCTOR = "destructor"
    TEST_ALWAYS = "test_always"
    TEST_BUILD = "test_build"
    TEST_SUITE = "test_suite"


class InlineHint(Enum):
    """What the source asked for regarding inlining."""

    DEFAULT = "default"
    ALWAYS = "always"
    NEVER = "never"


type AttrValue = int | str | bool


@dataclass(frozen=True, slots=True)
class FuncAttrs:
    """The attributes attached to a function.

    Carried verbatim from the syntax tree and never re-derived downstream, so
    that adding a new kind of special function is a change in the semantic
    analysis alone.
    """

    special: SpecialKind | None = None
    priority: int | None = None
    inline: InlineHint = InlineHint.DEFAULT
    abi: str | None = None
    extra: Mapping[str, AttrValue] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class BasicBlock:
    """A straight-line sequence of instructions ending in one terminator."""

    label: str
    parent: "Function | None" = None
    params: list[BlockParam] = field(default_factory=list)
    insts: list[Instruction] = field(default_factory=list)

    @property
    def terminator(self) -> Terminator | None:
        """The block's terminator, if the block is well formed."""
        if self.insts and isinstance(self.insts[-1], Terminator):
            return self.insts[-1]
        return None

    def add_param(self, ty: Type, name_hint: str | None = None) -> BlockParam:
        """Append a parameter to this block and return it."""
        param = BlockParam(ty, self, len(self.params), name_hint)
        self.params.append(param)
        return param

    def append(self, inst: Instruction) -> Instruction:
        """Append an instruction to this block."""
        inst.parent = self
        self.insts.append(inst)
        return inst


@dataclass(slots=True, eq=False)
class Function:
    """A function, or -- with no blocks -- the declaration of a foreign one."""

    name: str
    ty: FuncType
    attrs: FuncAttrs = field(default_factory=FuncAttrs)
    linkage: Linkage = Linkage.INTERNAL
    #: Name of the calling convention, resolved through the target.  Per
    #: function, because the specification lets conventions differ between
    #: functions of one compilation.
    cconv: str = "pl4g.v0"
    blocks: list[BasicBlock] = field(default_factory=list)
    span: Span = INVALID_SPAN
    source_path: str = ""

    @property
    def is_declaration(self) -> bool:
        """Whether this names a function defined elsewhere."""
        return not self.blocks

    @property
    def entry(self) -> BasicBlock | None:
        """The block control enters at."""
        return self.blocks[0] if self.blocks else None

    def add_block(self, label: str | None = None) -> BasicBlock:
        """Append a new block and return it."""
        block = BasicBlock(label if label is not None else "".join(("block", str(len(self.blocks)))))
        block.parent = self
        self.blocks.append(block)
        return block

    def params(self) -> Sequence[BlockParam]:
        """The function's parameters, which are the entry block's parameters."""
        entry = self.entry
        return entry.params if entry is not None else ()
