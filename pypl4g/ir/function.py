"""Basic blocks, functions and the attributes a function carries."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Final, Mapping, Sequence

from ..source.location import INVALID_SPAN, Span
from .inst import Instruction, Terminator
from .types import FuncType, Type, parts_of
from .value import BlockParam, Const


class Linkage(Enum):
    """Whether the image offers a definition to anything outside the program.

    This is one of the two questions a definition answers about who may name it,
    and the narrower one: it is about the symbol table of the finished binary.
    The other -- whether a file importing this module may name it -- is
    ``exported`` below, and the two are independent.  A definition may be
    offered to the outside without being part of what its module lets in, and a
    module may let something in that no binary ever names.
    """

    INTERNAL = "internal"
    VISIBLE = "visible"
    IMPORTED = "imported"


class SpecialKind(StrEnum):
    """The ways a function can take part in the life of a program.

    The three test flavours are the three the specification distinguishes: those
    that run after a build and at first start, those that run when a build
    finishes, and those that run when a testsuite run is requested.
    """

    STARTUP = "startup"
    #: The function the compiler runs rather than compiles, to find out what to
    #: build.  It is special in the way the others are -- one per program, a
    #: signature the compiler settles -- and unlike them it never reaches the
    #: image: what it leaves behind is a description, and the description is what
    #: gets compiled.
    BUILD = "build"
    #: The function `std` provides that makes the dictionary the environment
    #: is read through.  The entry point calls it and puts what it answers
    #: with in the record the program is started with: nothing the program
    #: writes calls it, and the table it makes cannot be made by the runtime,
    #: which knows nothing of how a table is laid out.
    ENVIRONMENT = "environment"
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
    #: The symbol a function defined somewhere else lives under, where that is
    #: not the name the program gave it.  What the program calls it and what the
    #: thing it calls is called need not agree: one is a name in a language and
    #: the other a name in an image.
    external: str | None = None
    #: Whether the compiler provides this function itself while it works out a
    #: build.  Such a function has no body and no symbol: what it does is change
    #: something the compiler is holding, so a program that called one would be
    #: calling something that is not there.
    builtin: bool = False
    #: Whether a caller may let what the function answers with go nowhere.  It
    #: may not by default: a function that answers is a function whose answer is
    #: the point of calling it, and the ones that may be called for what they do
    #: instead say so.
    can_ignore: bool = False
    #: Whether the function may change anything that outlives the call.  It may
    #: not by default: a function that only works out an answer is one a caller
    #: may move, repeat or drop, and that is worth having by default rather than
    #: on request.
    impure: bool = False
    #: Whether an array handed where one element is wanted is walked, the
    #: function being called for each and the answers making an array of the
    #: same shape.  A property of the function, since what it means to hand it
    #: an array is the function's own business.
    listable: bool = False
    extra: Mapping[str, AttrValue] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class BasicBlock:
    """A straight-line sequence of instructions ending in one terminator."""

    label: str
    parent: Function | None = None
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


#: What a function follows where it asks for nothing else: the language's own
#: convention, which each backend describes for itself.  It is a name and not a
#: description, because what the name stands for is the target's to say.
DEFAULT_CCONV: Final[str] = "pl4g"

#: And what a function that asks for the system's follows.  It is a name rather
#: than an architecture's own, so that a program saying "call me the way this
#: system does" need not know what this system calls it.
SYSTEM_CCONV: Final[str] = "cdecl"


class ReturnStyle(Enum):
    """How a function hands back an answer of more than one value.

    There is one so far and it is the default; the point of naming it is that
    the choice belongs to the function that answers.  A second would be a second
    member here and nothing else moved: everything that has to know asks the
    style, and the style answers for a type.

    `TWO_IN_REGISTERS` answers up to two values in the registers the convention
    names, and anything larger through storage the caller provides -- a place
    handed over as one argument more, which the callee writes and the caller
    reads.  Two is what the three system ABIs answer in registers as well, so it
    is the first choice rather than an arbitrary one.
    """

    TWO_IN_REGISTERS = "two-in-registers"

    def in_registers(self, ty: Type) -> bool:
        """Whether an answer of *ty* travels in registers rather than storage."""
        match self:
            case ReturnStyle.TWO_IN_REGISTERS:
                return len(parts_of(ty)) <= 2
        raise AssertionError(self)


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
    cconv: str = DEFAULT_CCONV
    blocks: list[BasicBlock] = field(default_factory=list)
    span: Span = INVALID_SPAN
    #: Where the name is written, which is what a message about the function
    #: itself points at: the whole definition begins at its first attribute or
    #: at the keyword, neither of which is what a reader is looking for.
    name_span: Span = INVALID_SPAN
    source_path: str = ""
    #: What the definition's documentation comment said, where it had one.  Kept
    #: for the same reason the spans are: nothing the compiler does reads it, and
    #: what asks for it is something telling a reader about this function --
    #: which, for a function another file imported, has nothing else to ask.
    doc: str = ""
    #: The module the function belongs to, which prefixes its symbol name.
    module: str = ""
    #: Whether a file importing this module may name it.  Not the same question
    #: as the linkage: this one is about the language, that one about the image.
    exported: bool = False
    #: What the parameters are called, in order.  A call may say which
    #: parameter an argument is for, and the name it says is this one.  The
    #: entry block's parameters carry a name hint each, but a declaration has no
    #: entry block and a caller in another file has to be able to name them all
    #: the same.
    param_names: tuple[str, ...] = ()
    #: What each parameter is given where a call gives it nothing, and nothing
    #: where it must be given something.  Settled where the function is defined
    #: rather than at the call, so that every call of it -- in this file or any
    #: other -- hands over the same value.
    defaults: tuple[Const | None, ...] = ()

    #: Which parameters the answer names what was named by: every one carrying
    #: the lifetime name the answer carries, so `fn f(v: &⧖a u8) → &⧖a u8` gives
    #: one and `⧖x` on two parameters gives two.  Several of them means the
    #: shorter of what they named, which is the only promise that holds
    #: whichever one the body picked.
    borrows_from: tuple[int, ...] = ()

    #: How this function hands back an answer that is more than one value.  A
    #: property of the function that answers, as the convention it is called by
    #: is, and for the same reason: what a caller has to do to receive the
    #: answer is settled by the callee and by nothing else.
    answering: ReturnStyle = ReturnStyle.TWO_IN_REGISTERS

    @property
    def is_declaration(self) -> bool:
        """Whether this names a function defined elsewhere."""
        return not self.blocks

    @property
    def entry(self) -> BasicBlock | None:
        """The block control enters at."""
        return self.blocks[0] if self.blocks else None

    def add_block(self, label: str | None = None) -> BasicBlock:
        """Append a new block and return it.

        A label that is already taken gets a number after it.  What a caller
        asks for is a name for the reader, not a name it will look the block up
        by -- a block is found by the object, never by its label -- so making
        the label unique here is better than making every caller count.
        """
        block = BasicBlock(self._unique(label))
        block.parent = self
        self.blocks.append(block)
        return block

    def _unique(self, label: str | None) -> str:
        """*label*, made unlike every label already used in this function."""
        if label is None:
            return "".join(("block", str(len(self.blocks))))
        taken = {block.label for block in self.blocks}
        if label not in taken:
            return label
        count = 1
        while "".join((label, str(count))) in taken:
            count += 1
        return "".join((label, str(count)))

    def params(self) -> Sequence[BlockParam]:
        """The function's parameters, which are the entry block's parameters."""
        entry = self.entry
        return entry.params if entry is not None else ()
