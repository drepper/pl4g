"""Calling conventions.

The specification says explicitly that the calling conventions need not match the
system's, and that they may differ between the functions of a single
compilation.  A convention is therefore a value attached to a function, not a
property of the target, and a pass that later synthesizes a bespoke convention
for one function has somewhere to put the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Final, Mapping, Sequence, TYPE_CHECKING

from ..ir.types import FloatType, Type, leaves_of
from ..mc.reg import PhysReg, RegisterInfo, RegUnit

if TYPE_CHECKING:
    from ..ir.function import Function


@dataclass(frozen=True, slots=True)
class CallConvDesc:
    """One calling convention."""

    name: str
    int_arg_regs: tuple[PhysReg, ...] = ()
    int_ret_regs: tuple[PhysReg, ...] = ()
    callee_saved: frozenset[RegUnit] = field(default_factory=frozenset)
    caller_saved: frozenset[RegUnit] = field(default_factory=frozenset)
    #: The same three things for values that are not integers.  A convention
    #: says where every kind of value goes, and a target with more than one kind
    #: of register has more than one set of answers.
    float_arg_regs: tuple[PhysReg, ...] = ()
    float_ret_regs: tuple[PhysReg, ...] = ()
    float_allocation_order: tuple[RegUnit, ...] = ()
    #: The units the allocator may give out, in the order it prefers them.
    #: Which registers are free to use is what a convention is about, so the
    #: convention states it rather than the allocator working it out: the order
    #: puts the ones a call would destroy first, since using one of those costs
    #: a leaf function nothing and using a callee-saved one would cost it a save.
    allocation_order: tuple[RegUnit, ...] = ()
    stack_align: int = 16
    #: Bytes below the stack pointer a leaf function may use without adjusting it.
    red_zone: int = 0
    variadic: bool = False
    #: Where a call through a value passes what the code it calls was made
    #: with -- a lambda's environment -- and where a function called that way
    #: finds it: a register no argument uses and a call destroys, so that a
    #: function with no such thing never reads it.  The static chain of GCC's
    #: nested functions and LLVM's `nest`.  Nothing for a convention nothing
    #: calls through a value.
    chain_reg: PhysReg | None = None
    #: Whether a floating-point argument that finds no register of its own kind
    #: left takes an ordinary one before it takes the stack, as RISC-V's
    #: convention has it.  The other two systems and the language's own send it
    #: straight to the stack.
    floats_in_integers: bool = False

    def orders(self, integers: str, floats: str) -> dict[str, tuple[RegUnit, ...]]:
        """The allocation orders by the name of the class each belongs to.

        A target with nothing to say about floating point says nothing here
        either: the mapping then holds one entry and the allocator behaves as it
        did before there was a second kind of register.
        """
        found = {integers: self.allocation_order}
        if self.float_allocation_order:
            found[floats] = self.float_allocation_order
        return found


def destroyed_by(callee: Function | None, cconv: CallConvDesc,
                 registers: RegisterInfo,
                 known: Mapping[str, frozenset[RegUnit]] | None
                 ) -> list[PhysReg]:
    """Which registers a call to *callee* destroys.

    What the callee turned out to destroy where that has been worked out, and
    everything its convention allows it to destroy where it has not -- a
    declaration of a function defined elsewhere, or one this compilation has
    not reached yet.  The difference is a save and a reload at every call, which
    is why it is worth asking rather than assuming.

    It is asked here, of the convention, rather than stated in an instruction
    table beside the call: two functions of one compilation may follow different
    conventions, so what a call destroys is not a property of the instruction
    that makes it.  The answer travels on the call.
    """
    from ..ir.mangle import symbol_name

    units = (None if known is None or callee is None
             else known.get(symbol_name(callee)))
    if units is None:
        # Everything the convention allows, which is what a call through a
        # value has to assume: nothing here knows which function it is.
        units = cconv.caller_saved
    return [registers.widest(unit) for unit in units]


class TooManyArguments(Exception):
    """More parts in an answer than a convention answers in registers.

    An argument never runs out of places, the stack taking what the registers
    do not; an answer has no such second route here, `largeanswers` having
    sent every answer too large for the registers through storage before code
    is generated, so this is a defect of the compiler and not of a program.
    """


#: How many bytes one part takes on the stack.  Every part is one register, and
#: none is wider than eight bytes; the three systems' conventions all give a
#: smaller one a slot this wide as well, so one size is the whole rule.
STACK_SLOT: Final[int] = 8


@dataclass(frozen=True, slots=True)
class OnStack:
    """A part passed on the stack rather than in a register.

    *offset* is how far above the stack pointer at the call its slot begins:
    the caller writes it there before the call, in room at the bottom of its
    own frame, and the callee reads it from there, which is above its own frame
    and -- where the call pushed the return address -- above that too.
    """

    offset: int


#: Where one part of an argument travels.
Place = PhysReg | OnStack


def argument_places(cconv: CallConvDesc, types: Sequence[Type],
                    chained: bool = False) -> list[list[Place]]:
    """Where each argument is passed, in order, one place per part.

    A register is taken from the list its *kind* comes out of, so a
    floating-point argument does not use up an integer register and the other
    way round -- which is what every one of these conventions says and what
    counting by position alone would get wrong the moment the two are mixed.

    A value of several parts takes one place per part: a result its answer and
    the truth value beside it, a tuple its members.  That is what the ABIs
    already do with a two-word aggregate.

    **A part that finds no register of its kind left goes on the stack**, in an
    eight-byte slot of its own, the slots in the order the parts come in -- the
    order in which the system's conventions lay out what they pass in memory,
    each counting its two kinds of register separately as this does.  A part
    that finds none does not stop the parts after it from taking a register
    of the other kind.  Where the convention says so (`floats_in_integers`), a
    floating-point part tries an ordinary register before the stack.

    *chained* says the first is passed in the convention's chain register
    rather than as an argument, the rest taking the places they would have had
    without it: a function called through a value finds its environment there,
    and one that has none leaves it unread.
    """
    if not chained or not types:
        return _places(cconv, cconv.int_arg_regs, cconv.float_arg_regs, types,
                       stack=True)
    assert cconv.chain_reg is not None, "a chained call in a convention with no chain"
    assert len(leaves_of(types[0])) == 1, "an environment is one word"
    return [[cconv.chain_reg],
            *_places(cconv, cconv.int_arg_regs, cconv.float_arg_regs, types[1:],
                     stack=True)]


def stack_bytes(places: Sequence[Sequence[Place]]) -> int:
    """How much room the parts *places* puts on the stack take, rounded to a
    multiple of sixteen -- the alignment the stack pointer keeps at a call --
    so that room made for them at the bottom of a frame leaves it aligned."""
    end = 0
    for parts in places:
        for place in parts:
            if isinstance(place, OnStack):
                end = max(end, place.offset + STACK_SLOT)
    return (end + 15) & ~15


def result_places(cconv: CallConvDesc, ty: Type) -> list[PhysReg]:
    """Which registers a value of *ty* is answered with, one per part."""
    found: list[PhysReg] = []
    for place in _places(cconv, cconv.int_ret_regs, cconv.float_ret_regs, (ty,),
                         stack=False)[0]:
        assert isinstance(place, PhysReg)
        found.append(place)
    return found


def _places(cconv: CallConvDesc, integers: Sequence[PhysReg],
            floats: Sequence[PhysReg], types: Sequence[Type],
            stack: bool) -> list[list[Place]]:
    """The place each part of each of *types* occupies, registers counted per
    kind and the stack, where *stack* allows it, taking what they do not."""
    found: list[list[Place]] = []
    used = {True: 0, False: 0}
    slots = 0

    def _take(part: Type) -> Place:
        nonlocal slots
        floating = isinstance(part, FloatType)
        bank = floats if floating else integers
        if used[floating] < len(bank):
            used[floating] += 1
            return bank[used[floating] - 1]
        if floating and cconv.floats_in_integers and used[False] < len(integers):
            used[False] += 1
            return integers[used[False] - 1]
        if not stack:
            raise TooManyArguments()
        slots += 1
        return OnStack(STACK_SLOT * (slots - 1))

    for ty in types:
        found.append([_take(part) for part in leaves_of(ty)])
    return found


def calls_placed(func: Function, conventions: Callable[[str], CallConvDesc]
                 ) -> dict[int, tuple[CallConvDesc, list[list[Place]]]]:
    """Every call of *func*, by its identity, with the convention it is placed
    by and where each part of each of its arguments goes.

    The convention is the callee's, *conventions* being how a target looks one
    up by name, and for a call through a value -- where nothing here knows
    which callee it is -- the one the call says the value's type named: the one
    every function of this language follows, the environment travelling in its
    chain register, or the system's, with no environment at all.  Asked once, before any
    code is generated, because the room the stack's share takes is at the
    bottom of the caller's frame and has to be taken before anything else is.
    """
    from ..ir.function import DEFAULT_CCONV, Function as _Function
    from ..ir.inst import CallInst

    found: dict[int, tuple[CallConvDesc, list[list[Place]]]] = {}
    for block in func.blocks:
        for inst in block.insts:
            if not isinstance(inst, CallInst):
                continue
            through = inst.target is not None
            callee = inst.callee
            named = isinstance(callee, _Function)
            if not through and not named:
                # Refused where the call is lowered, which says why.
                continue
            theirs = conventions(inst.cconv if through
                                 else DEFAULT_CCONV if not named
                                 else callee.cconv)
            chained = (through and inst.cconv == DEFAULT_CCONV) \
                or (named and callee.attrs.chained)
            found[id(inst)] = (theirs, argument_places(
                theirs, [a.ty for a in inst.arguments], chained=chained))
    return found
