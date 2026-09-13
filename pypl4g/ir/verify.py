"""The IR verifier.

It runs after lowering and after every pass, unconditionally: the compiler must
always perform all conformance checks.  A verifier failure reports a defect in
the compiler, never in the program being compiled, so it raises rather than
emitting a user diagnostic.
"""

from typing import Iterable

from ..diag.engine import InternalError
from .function import BasicBlock, Function, SpecialKind
from .mangle import symbol_name
from .inst import (BinaryInst, BlockTarget, CmpInst, Instruction, LoadInst,
                   RetInst, StoreInst, Terminator, UnaryInst)
from .module import GlobalVar, Module
from .types import IntType, MEM, PtrType, VOID
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
            case BinaryInst():
                if inst.operands[0].ty != inst.operands[1].ty:
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' applied to operands of different types")))
                if inst.ty != inst.operands[0].ty:
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' result type differs from its operands")))
            case UnaryInst():
                if inst.ty != inst.operands[0].ty:
                    self._fail(where, "".join(("'", inst.opcode,
                                               "' result type differs from its operand")))
            case CmpInst():
                if inst.operands[0].ty != inst.operands[1].ty:
                    self._fail(where, "comparison of operands of different types")
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


def verify(module: Module) -> None:
    """Verify *module*, raising ``InternalError`` if anything is wrong."""
    Verifier(module).run()


def verify_all(modules: Iterable[Module]) -> None:
    """Verify several modules."""
    for module in modules:
        verify(module)
