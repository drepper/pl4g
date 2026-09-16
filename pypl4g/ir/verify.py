"""The IR verifier.

It runs after lowering and after every pass, unconditionally: the compiler must
always perform all conformance checks.  A verifier failure reports a defect in
the compiler, never in the program being compiled, so it raises rather than
emitting a user diagnostic.
"""

from __future__ import annotations

from typing import Iterable

from ..diag.engine import InternalError
from .function import BasicBlock, Function, SpecialKind
from .mangle import symbol_name
from .inst import (AddressInst, SyscallInst, AnyLaneInst, AssertInst, BinaryInst, BinOp,
                   BlockTarget, CastInst, FrameInst, SplatInst,
                   CastKind, CmpInst,
                   ErrorInst, ExtractInst, FailedInst,
                   Instruction, TupleInst,
                   LoadInst, RetInst, StoreInst, Terminator, UnaryInst,
                   UnwrapInst, WrapInst)
from .module import GlobalVar, Module
from .types import (ArrayType, BOOL, BoolType, CharType, DictType, EnumType,
                    without_units,
                    FuncType, IntType,
                    MEM, PtrType, ResultType, SetType, TupleType, Type,
                    U64,
                    VecType, VOID, parts_of)
from .value import Const, IntConst, Value


class Verifier:
    """Checks the structural invariants of one module."""

    def __init__(self, module: Module) -> None:
        self._module = module
        self._problems: list[str] = []

    def _fail(self, where: str, detail: str) -> None:
        """Record one violated invariant."""
        self._problems.append("".join((where, ": ", detail)))

    def run(self) -> None:
        """Verify the module, raising on the first set of problems found."""
        self._check_module()
        for func in self._module.functions.values():
            if not func.is_declaration:
                self._check_function(func)
        if self._problems:
            raise InternalError("; ".join(self._problems))

    # -- module level ----------------------------------------------------------

    def _check_module(self) -> None:
        """Check the properties the whole module must have."""
        self._check_symbols_are_distinct()
        self._check_special_caches()

    def _check_symbols_are_distinct(self) -> None:
        """Check that no two functions end up under one symbol.

        Two functions whose signatures differ mangle to different names, and two
        that agree are already refused as one name defined twice.  So this can
        only fail if the mangling itself is wrong, which is a defect in the
        compiler and not in the program.
        """
        seen: dict[str, str] = {}
        for func in self._module.functions.values():
            symbol = symbol_name(func)
            previous = seen.get(symbol)
            if previous is not None:
                self._fail("module", "".join((
                    "'", previous, "' and '", func.name,
                    "' are both known by the symbol '", symbol, "'")))
                continue
            seen[symbol] = func.name

    def _check_special_caches(self) -> None:
        """Check that the special-function caches agree with the attributes."""
        startups = [f for f in self._module.functions.values()
                    if f.attrs.special is SpecialKind.STARTUP]
        if len(startups) > 1:
            self._fail("module", "more than one function is marked startup")
        if self._module.startup is not None and self._module.startup not in startups:
            self._fail("module", "the cached startup function is not marked startup")
        if startups and self._module.startup is None:
            self._fail("module", "a startup function exists but was not cached")
        expected_ctors = [f for f in self._module.functions.values()
                          if f.attrs.special is SpecialKind.CONSTRUCTOR]
        if set(map(id, expected_ctors)) != set(map(id, self._module.ctors)):
            self._fail("module", "the constructor cache disagrees with the attributes")
        expected_dtors = [f for f in self._module.functions.values()
                          if f.attrs.special is SpecialKind.DESTRUCTOR]
        if set(map(id, expected_dtors)) != set(map(id, self._module.dtors)):
            self._fail("module", "the destructor cache disagrees with the attributes")

    # -- function level --------------------------------------------------------

    def _check_function(self, func: Function) -> None:
        """Check one function's blocks, types and dominance."""
        where = "".join(("function @", func.name))
        entry = func.entry
        if entry is None:
            self._fail(where, "a definition with no entry block")
            return
        param_types = tuple(p.ty for p in entry.params)
        if param_types != func.ty.params:
            self._fail(where, "entry block parameters do not match the function type")
        labels: set[str] = set()
        for block in func.blocks:
            if block.label in labels:
                self._fail(where, "".join(("duplicate block label '", block.label, "'")))
            labels.add(block.label)
            self._check_block(func, block)
        self._check_dominance(func)

    def _check_block(self, func: Function, block: BasicBlock) -> None:
        """Check that a block is terminated exactly once and typed correctly."""
        where = "".join(("function @", func.name, ", block ", block.label))
        if not block.insts:
            self._fail(where, "empty block")
            return
        for inst in block.insts[:-1]:
            if isinstance(inst, Terminator):
                self._fail(where, "".join(("terminator '", inst.opcode,
                                           "' is not the last instruction")))
        last = block.insts[-1]
        if not isinstance(last, Terminator):
            self._fail(where, "".join(("block ends with '", last.opcode,
                                       "', which is not a terminator")))
        for inst in block.insts:
            self._check_inst(where, func, inst)

    def _check_inst(self, where: str, func: Function, inst: Instruction) -> None:
        """Check the types of one instruction."""
        for operand in inst.operands:
            if isinstance(operand, IntConst) and isinstance(operand.ty, IntType):
                if not operand.ty.holds(operand.value):
                    self._fail(where, "".join((
                        "constant ", str(operand.value), " does not fit in ",
                        operand.ty.render())))
            if operand.ty is VOID:
                # A call to a function that answers with nothing is the only
                # thing that has this type, and its answer is not a value: there
                # is nothing to store, nothing to compare and nothing to give
                # back.  A front end that let one be named has to report that
                # itself, with the place the name was written; this is the net
                # underneath, and it catches a pass that builds one by mistake.
                self._fail(where, "".join((
                    "'", inst.opcode, "' is given something that has no value")))
        match inst:
            case RetInst():
                expected = func.ty.ret
                if expected is VOID:
                    if inst.operands:
                        self._fail(where, "returning a value from a void function")
                elif not inst.operands:
                    self._fail(where, "returning no value from a non-void function")
                elif inst.operands[0].ty != expected:
                    self._fail(where, "".join((
                        "returning ", inst.operands[0].ty.render(), " from a function returning ",
                        expected.render())))
            case BinaryInst() if isinstance(inst.ty, PtrType):
                # Arithmetic on an address: the place so many bytes along from
                # the one the left names.  The two operands are deliberately
                # not of one type -- an address is not a number, and what is
                # added to it is not an address -- so the rule below does not
                # apply and this one does.
                if inst.op not in (BinOp.ADD, BinOp.SUB):
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' applied to an address")))
                elif inst.operands[0].ty != inst.ty:
                    self._fail(where, "".join((
                        "'", inst.opcode, "' answering with ", inst.ty.render(),
                        " from ", inst.operands[0].ty.render())))
                elif not isinstance(inst.operands[1].ty, IntType):
                    self._fail(where, "".join((
                        "moving an address by ", inst.operands[1].ty.render())))
            case CastInst() if inst.kind in (CastKind.ZEXT, CastKind.SEXT,
                                             CastKind.TRUNC):
                # Between things a register holds as a whole number, which is
                # what "wider" and "narrower" are about.  A truth value and a
                # value of an enumeration are among them: both are a number in
                # a register, whatever the language says they mean.
                if not (_counts(inst.ty) and _counts(inst.operands[0].ty)):
                    self._fail(where, "".join((
                        "'", inst.opcode, "' between ",
                        inst.operands[0].ty.render(), " and ", inst.ty.render())))
            case CastInst() if inst.kind is CastKind.BITCAST:
                # The same bits read as another type.  An address -- and a
                # collection is one, being where its table is -- or a code point
                # read as the number it is held as, which is the same bits in
                # the same register bank and the same width.  An integer read as
                # a floating-point number is not among them: that is the same
                # question asked of two different register banks, and answering
                # it wants a rule about where the bits are and not only that
                # they are the same ones.
                if not (_is_an_address(inst.ty)
                        and _is_an_address(inst.operands[0].ty)) \
                        and not _held_as(inst.ty, inst.operands[0].ty) \
                        and without_units(inst.ty) \
                        is not without_units(inst.operands[0].ty):
                    self._fail(where, "".join((
                        "reading ", inst.operands[0].ty.render(), " as ",
                        inst.ty.render(), ", which is not the same kind of thing")))
            case FrameInst():
                if inst.ty != PtrType(inst.held, mutable=True):
                    self._fail(where, "".join((
                        "room for a ", inst.held.render(), " read as ",
                        inst.ty.render())))
            case AssertInst():
                if inst.operands[0].ty is not BOOL:
                    self._fail(where, "".join((
                        "a check of ", inst.operands[0].ty.render(),
                        ", which is not a truth value")))
            case SyscallInst():
                # Everything the kernel is given is a machine word: a number, a
                # descriptor, a length, or the address of something.  Nothing
                # else fits in a register the kernel reads, and a floating-point
                # value in one would be a program that meant something else.
                if not isinstance(inst.ty, IntType):
                    self._fail(where, "".join((
                        "a request to the kernel answering '",
                        inst.ty.render(), "'")))
                for one in inst.operands:
                    if not isinstance(one.ty, (IntType, PtrType)):
                        self._fail(where, "".join((
                            "a request to the kernel given '", one.ty.render(),
                            "'")))
            case AddressInst():
                if not isinstance(inst.operands[0].ty, PtrType):
                    self._fail(where, "the address of something that is not a place")
                elif inst.ty != inst.operands[0].ty:
                    self._fail(where, "".join((
                        "the address of a ", inst.operands[0].ty.render(),
                        " read as ", inst.ty.render())))
            case BinaryInst():
                # Up to units, which the bits know nothing about: multiplying a
                # length by a time answers an area-per-time and is one
                # multiplication, the same one it would have been with no units
                # written anywhere.
                if without_units(inst.operands[0].ty) \
                        != without_units(inst.operands[1].ty):
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' applied to operands of different types")))
                # An operation that may have no answer says so in its type: the
                # answer type is the operands' and the whole is a result.
                answered = (inst.ty.ok if isinstance(inst.ty, ResultType)
                            else inst.ty)
                if without_units(answered) != without_units(inst.operands[0].ty):
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' result type differs from its operands")))
            case TupleInst():
                # Anything of several parts, and not only a tuple: what the
                # parts of a type are is one question with one answer, and a
                # tuple is merely the shape that has as many as it was written
                # with.  An array whose type does not say how long it is has
                # two, and is made the same way.
                pieces = parts_of(inst.ty)
                if len(pieces) < 2:
                    self._fail(where, "".join((
                        "making a ", inst.ty.render(),
                        ", which is one value and not several")))
                elif len(inst.operands) != len(pieces):
                    self._fail(where, "".join((
                        "making a ", inst.ty.render(), " out of ",
                        str(len(inst.operands)), " values")))
                else:
                    for index, (value, member) in enumerate(
                            zip(inst.operands, pieces)):
                        if value.ty != member:
                            self._fail(where, "".join((
                                "the value at ", str(index), " of a ",
                                inst.ty.render(), " is ", value.ty.render())))
            case ExtractInst():
                inner = parts_of(inst.operands[0].ty)
                if len(inner) < 2:
                    self._fail(where, "".join((
                        "taking a value out of ", inst.operands[0].ty.render(),
                        ", which is one value and not several")))
                elif not 0 <= inst.index < len(inner):
                    self._fail(where, "".join((
                        inst.operands[0].ty.render(), " has no value at ",
                        str(inst.index))))
                elif inst.ty != inner[inst.index]:
                    self._fail(where, "".join((
                        "taking ", inst.ty.render(), " out of ",
                        inst.operands[0].ty.render())))
            case WrapInst():
                if not isinstance(inst.ty, ResultType):
                    self._fail(where, "making something that is not a result")
                elif inst.operands[0].ty != inst.ty.ok:
                    self._fail(where, "".join((
                        "making a ", inst.ty.render(), " out of ",
                        inst.operands[0].ty.render())))
                elif inst.operands[1].ty is not BOOL:
                    self._fail(where, "whether a result failed is not a truth value")
                elif (len(inst.operands) > 2) != (inst.ty.err is not None):
                    self._fail(where, "".join((
                        "making a ", inst.ty.render(),
                        " with an error value" if len(inst.operands) > 2
                        else " without the value its error carries")))
                elif len(inst.operands) > 2 and inst.operands[2].ty != inst.ty.err:
                    self._fail(where, "".join((
                        "making a ", inst.ty.render(), " whose error carries ",
                        inst.operands[2].ty.render())))
            case UnwrapInst() | FailedInst() | ErrorInst():
                inner = inst.operands[0].ty
                if not isinstance(inner, ResultType):
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' applied to something that is not a result")))
                elif isinstance(inst, UnwrapInst) and inst.ty != inner.ok:
                    self._fail(where, "".join((
                        "reading ", inst.ty.render(), " out of ", inner.render())))
                elif isinstance(inst, ErrorInst) and inst.ty != inner.err:
                    self._fail(where, "".join((
                        "reading the error of ", inner.render(), " as ",
                        inst.ty.render())))
            case UnaryInst():
                if inst.ty != inst.operands[0].ty:
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' result type differs from its operand")))
            case CmpInst():
                if inst.operands[0].ty != inst.operands[1].ty:
                    self._fail(where, "comparison of operands of different types")
                elif isinstance(inst.ty, VecType):
                    # A comparison of two vectors answers one truth value per
                    # lane, which is a vector of as many lanes as it was asked
                    # about and of truth values whatever they were.
                    compared = inst.operands[0].ty
                    if not (isinstance(compared, VecType)
                            and compared.lanes == inst.ty.lanes
                            and inst.ty.element is BOOL):
                        self._fail(where, "".join((
                            "comparing ", compared.render(), " answering ",
                            inst.ty.render())))
                elif inst.ty is not BOOL:
                    self._fail(where, "".join((
                        "a comparison answering ", inst.ty.render())))
            case SplatInst():
                if not isinstance(inst.ty, VecType):
                    self._fail(where, "".join((
                        "spreading a value over ", inst.ty.render(),
                        ", which has no lanes")))
                elif inst.operands[0].ty != inst.ty.element:
                    self._fail(where, "".join((
                        "spreading ", inst.operands[0].ty.render(), " over ",
                        inst.ty.render())))
            case AnyLaneInst():
                asked = inst.operands[0].ty
                if not (isinstance(asked, VecType) and asked.element is BOOL):
                    self._fail(where, "".join((
                        "asking whether any lane of ", asked.render(),
                        " is true, which holds no truth values")))
                elif inst.ty is not BOOL:
                    self._fail(where, "".join((
                        "whether any lane is true read as ", inst.ty.render())))
            case StoreInst():
                if len(inst.operands) != 3:
                    self._fail(where, "a store takes a token, an address and a value")
                elif inst.operands[0].ty is not MEM:
                    self._fail(where, "a store's first operand is not a memory token")
                else:
                    target = inst.operands[1]
                    stored = inst.operands[2].ty
                    if not isinstance(target.ty, PtrType):
                        self._fail(where, "a store's address is not a pointer")
                    elif target.ty.pointee != stored:
                        self._fail(where, "".join((
                            "storing ", stored.render(), " through a pointer to ",
                            target.ty.pointee.render())))
                    if isinstance(target.ty, PtrType) and not target.ty.mutable:
                        named = target.name if isinstance(target, GlobalVar) else "a place"
                        self._fail(where, "".join((
                            "storing into '", named,
                            "', through a pointer that does not allow it")))
            case LoadInst():
                if len(inst.operands) != 2:
                    self._fail(where, "a load takes a memory token and an address")
                elif inst.operands[0].ty is not MEM:
                    self._fail(where, "a load's first operand is not a memory token")
                else:
                    address = inst.operands[1].ty
                    if not isinstance(address, PtrType):
                        self._fail(where, "a load's address is not a pointer")
                    elif address.pointee != inst.ty:
                        self._fail(where, "".join((
                            "loading ", inst.ty.render(), " through a pointer to ",
                            address.pointee.render())))
            case _:
                pass
        if isinstance(inst, Terminator):
            for target in inst.successors():
                self._check_target(where, func, target)

    def _check_target(self, where: str, func: Function, target: BlockTarget) -> None:
        """Check that a branch supplies the arguments its destination declares."""
        block = target.block
        if not isinstance(block, BasicBlock) or block.parent is not func:
            self._fail(where, "branch to a block of another function")
            return
        if len(target.args) != len(block.params):
            self._fail(where, "".join((
                "branch to ", block.label, " supplies ", str(len(target.args)),
                " arguments but the block declares ", str(len(block.params)))))
            return
        for arg, param in zip(target.args, block.params):
            if arg.ty != param.ty:
                self._fail(where, "".join((
                    "branch to ", block.label, " supplies ", arg.ty.render(),
                    " where ", param.ty.render(), " is declared")))

    # -- dominance -------------------------------------------------------------

    def _predecessors(self, func: Function) -> dict[int, list[BasicBlock]]:
        """Map each block to the blocks that can branch to it."""
        preds: dict[int, list[BasicBlock]] = {id(b): [] for b in func.blocks}
        for block in func.blocks:
            terminator = block.terminator
            if terminator is None:
                continue
            for target in terminator.successors():
                if isinstance(target.block, BasicBlock):
                    preds.setdefault(id(target.block), []).append(block)
        return preds

    def _dominators(self, func: Function) -> dict[int, set[int]]:
        """Compute, for each block, the set of blocks that dominate it."""
        blocks = func.blocks
        all_ids = {id(b) for b in blocks}
        preds = self._predecessors(func)
        dominators: dict[int, set[int]] = {id(b): set(all_ids) for b in blocks}
        dominators[id(blocks[0])] = {id(blocks[0])}
        changed = True
        while changed:
            changed = False
            for block in blocks[1:]:
                incoming = preds.get(id(block), ())
                if not incoming:
                    new = {id(block)}
                else:
                    new = set(all_ids)
                    for pred in incoming:
                        new &= dominators[id(pred)]
                    new |= {id(block)}
                if new != dominators[id(block)]:
                    dominators[id(block)] = new
                    changed = True
        return dominators

    def _check_dominance(self, func: Function) -> None:
        """Check that every use of a value is dominated by its definition."""
        where = "".join(("function @", func.name))
        dominators = self._dominators(func)
        defining_block: dict[int, BasicBlock] = {}
        position: dict[int, int] = {}
        for block in func.blocks:
            for param in block.params:
                defining_block[id(param)] = block
                position[id(param)] = -1
            for index, inst in enumerate(block.insts):
                defining_block[id(inst)] = block
                position[id(inst)] = index

        def check_use(user_block: BasicBlock, user_index: int, value: Value) -> None:
            """Check one use of *value*."""
            # A constant and a global are available everywhere: neither is
            # produced by an instruction, so neither has a definition that
            # could fail to dominate a use.
            if isinstance(value, (Const, GlobalVar)):
                return
            home = defining_block.get(id(value))
            if home is None:
                self._fail(where, "use of a value that no block defines")
                return
            if home is user_block:
                if position[id(value)] >= user_index >= 0:
                    self._fail(where, "use of a value before its definition")
                return
            if id(home) not in dominators[id(user_block)]:
                self._fail(where, "use of a value that does not dominate the use")

        for block in func.blocks:
            for index, inst in enumerate(block.insts):
                for operand in inst.operands:
                    check_use(block, index, operand)
                if isinstance(inst, Terminator):
                    for target in inst.successors():
                        for arg in target.args:
                            check_use(block, index, arg)


def _is_an_address(ty: Type) -> bool:
    """Whether a value of *ty* is an address as far as a register is concerned.

    A collection is one: what a program passes around is where its table is, and
    nothing else.  So is an array whose type says how many elements it has: what
    a value of one *is*, is where the elements are, since how many there are is
    in the type and there is nothing else to carry.
    """
    return isinstance(ty, (PtrType, SetType, DictType)) or (
        isinstance(ty, ArrayType) and ty.fixed)


def _held_as(one: Type, other: Type) -> bool:
    """Whether one of these is held as the other, which is where the bits are
    the same bits.

    A code point and the number it is held as, and a value of an enumeration
    and the number it is held as.  Both are a number in a register whatever the
    language says they mean -- which is what the widenings above already say of
    an enumeration, asked here of reading one as the other rather than of making
    one wider.  That an enumeration holds only its own values is the checker's
    to keep; nothing structural about an instruction can say it.

    And an address read as the whole number a register holds it as, which is
    what a request to the kernel is given: the kernel takes registers, and an
    address is one of the things that goes in one.

    And two function types differing only in whether what they name walks an
    array it is given.  That is a promise to a caller and nothing a value
    carries: both are the same two addresses in the same two registers, and
    which of the two a name holds is what decides whether the caller walks.
    """
    if isinstance(one, FuncType) and isinstance(other, FuncType):
        return one.params == other.params and one.ret is other.ret
    if isinstance(one, PtrType) and other is U64:
        return True
    if isinstance(other, PtrType) and one is U64:
        return True
    return (isinstance(one, (CharType, EnumType)) and other is one.holder) \
        or (isinstance(other, (CharType, EnumType)) and one is other.holder)


def _counts(ty: Type) -> bool:
    """Whether a value of *ty* is a whole number as far as a register is
    concerned, which is what a widening or a narrowing is between."""
    return isinstance(ty, (IntType, BoolType, CharType, EnumType))


def verify(module: Module) -> None:
    """Verify *module*, raising ``InternalError`` if anything is wrong."""
    Verifier(module).run()


def verify_all(modules: Iterable[Module]) -> None:
    """Verify several modules."""
    for module in modules:
        verify(module)
