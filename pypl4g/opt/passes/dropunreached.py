"""Dropping what nothing in the program can reach.

Compilation always covers the whole program -- there is no equivalent of an
object file -- so what nothing in the program reaches is what nothing can ever
reach.  Such a function is not merely unused; it is unusable, and bytes no
program can run have no business in the image.

Reachability is computed from a set of roots rather than by asking of each
function whether it is used, because being used by something that is itself
unreachable is not being used.  The roots are the ways into the program from
outside it:

* the startup function, and the constructors and destructors, which the entry
  point the compiler writes calls itself;
* the tests, which the testing machinery will call when there is any;
* whatever the image offers to the outside, which by definition may be called
  from somewhere this compilation cannot see.  Being exported from a module is
  not enough: within one program, a definition a module offers and nothing
  imports is a definition nothing reaches.

From there it follows what each instruction says it names.  A call names its
callee; nothing else names a function yet, and when something does -- a pointer
to one, a table of them -- it says so on the instruction and this pass needs no
change.

Variables follow the functions.  A variable is reached when a function that is
itself reached names it, so dropping a function can be what makes a variable
unreachable -- which is why the two are answered here together and in that
order rather than by two passes that would have to be run until they agreed.
A variable the image offers to the outside is a root of its own, for the reason
such a function is: something this compilation cannot see may name it.

**A constructor that only fills in variables nothing reads is no root.**  One
whose body, and everything it calls, does nothing but work out values, read,
and write its own frame and variables it names -- no request to the kernel, no
call of code whose body is not here or through a value, no check that could
stop the program -- changes nothing anybody can see except those variables.
Where nothing else the program keeps names any of them, running it or not is
the same program, so it goes with them: `std.time`'s, which finds the vDSO's
clocks, is in every program that imports `std` and in the image only of one
that reads a clock.  Kept constructors can name what another writes, so the
kept ones are found until no more are.  Leaving one out also leaves out a read
of memory that would have faulted and a loop that would not have ended, which
is what C and C++ assume of a loop with no effect too.  And where the reader of
the auxiliary vector is left the only function naming its table, both go,
along with the entry point's call of it.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...ir.reports import ReportKind
from ...ir.function import Function, SpecialKind, Linkage
from typing import Final

from ...ir.inst import (AddressInst, BinaryInst, CallInst, CastInst, CastKind,
                        FrameInst, Instruction, StoreInst, Terminator,
                        UnreachableInst)
from ...ir.module import GlobalVar, Module
from ...ir.types import PtrType
from ...ir.value import (AddressConst, ArrayConst, PartsConst, RecordConst,
                         ResultConst, SumConst, Value)
from ...source.location import Span


#: What the function that fills the environment is called.  Named here rather
#: than imported from the semantic analysis, which this pass runs after and
#: does not depend on.
ENVIRON_MAKE: Final[str] = "__pl4g_environ_make"

#: And what the function that reads the auxiliary vector is called, for the same
#: reason.
AUXV_MAKE: Final[str] = "__pl4g_auxv_make"

#: How every name the compiler makes for itself begins.
COMPILER_MADE: Final[str] = "__pl4g_"


class DropUnreached:
    """Removes the functions and variables no root reaches."""

    name = "dropunreached"

    def run(self, module: Module) -> bool:
        """Drop what nothing reaches; report whether anything changed.

        Each thing dropped is recorded.  Leaving something out of a binary is a
        report about the program, and one a reader is entitled to ask about:
        "I wrote that function, where is it?" has an answer, and this is where
        the answer is kept.  Not of a system module's, which the log leaves out:
        the reader did not write it, and a library offers more than any one
        program uses.
        """
        # Nothing is changed in what is left, so there is no function to check
        # again -- only the module, which is always checked.
        self.touched: list[Function] = []
        reachable, unread, table_read = self._reachable(module)
        functions = {name: func for name, func in module.functions.items()
                     if id(func) in reachable}
        changed = len(functions) != len(module.functions)
        if unread:
            module.ctors = [one for one in module.ctors if id(one) not in unread]
        if module.auxv is not None and not table_read:
            # Nothing reads the table, so nothing fills it: not the reader, nor
            # the interpreter, which fills it itself.
            module.auxv = None
        for key, func in module.functions.items():
            # One the compiler made, the reader of the vector, is nothing
            # anybody wrote, so nobody asks where it went.
            if key in functions or func.is_declaration \
                    or func.name.startswith(COMPILER_MADE):
                continue
            if id(func) in unread:
                module.reports.record(
                    ReportKind.DROP_FUNCTION, func.name,
                    "a constructor that only writes variables nothing the "
                    "program keeps reads, so running it changes nothing",
                    _where(func.name_span, func.span))
            else:
                # The name the source wrote, not what the module files it
                # under: the key tells two files' definitions apart and is
                # nothing a reader of the log should have to know about.
                module.reports.record(
                    ReportKind.DROP_FUNCTION, func.name,
                    "nothing the program can run reaches it, and it is not "
                    "exported, so nothing outside can reach it either",
                    _where(func.name_span, func.span))
        module.functions = functions

        named = self._variables_named_by(module, functions.values())
        variables = {name: var for name, var in module.globals.items()
                     if id(var) in named or var.linkage is Linkage.VISIBLE}
        if len(variables) != len(module.globals):
            changed = True
            for key, var in module.globals.items():
                # One the compiler made -- a piece of text, the default arena --
                # is nothing anybody wrote, so nobody asks where it went.
                if key not in variables and not var.name.startswith(COMPILER_MADE):
                    module.reports.record(
                        ReportKind.DROP_VARIABLE, var.name,
                        "no function that is itself reached names it, and it is "
                        "not exported",
                        _where(var.name_span, var.span))
            module.globals = variables
        return changed

    # -- functions -------------------------------------------------------------

    def _roots(self, module: Module) -> list[Function]:
        """The functions that are reachable whatever the program does.

        A test is a root even though nothing calls one yet: what calls it is the
        testing machinery, which is specified and not written.  Dropping one
        because the caller does not exist yet would be dropping it for the wrong
        reason.
        """
        roots: list[Function] = []
        if module.startup is not None:
            roots.append(module.startup)
        if module.environ is not None:
            # Nothing the program writes calls the builder: the entry point
            # does, and only where the program named the environment at all.
            found = module.functions.get(ENVIRON_MAKE)
            if found is not None:
                roots.append(found)
        roots.extend(module.ctors)
        roots.extend(module.dtors)
        # A test the binary runs is a root and one it does not is not: a suite
        # test kept in the program it tests would be code nothing can reach,
        # which is the very thing this pass is here to leave out.
        roots.extend(module.test_plan if module.test_plan
                     else [one for one in module.tests
                           if one.attrs.special is SpecialKind.TEST_ALWAYS])
        # A definition the image offers outside is a root; a declaration is
        # somebody else's definition, which is wanted only where something here
        # calls it -- the allocator above all, which the back end supplies for a
        # program whose code still names it and for no other.
        roots.extend(func for func in module.functions.values()
                     if func.linkage is Linkage.VISIBLE and not func.is_declaration)
        return roots

    def _reachable(self, module: Module) -> tuple[set[int], set[int], bool]:
        """The identities of the functions a root reaches, directly or not, and
        of the constructors left out for writing only what nothing reads, and
        whether what is reached reads the table of the auxiliary vector."""
        writes = {id(one): found for one in module.ctors
                  if (found := self._only_writes(one)) is not None}
        left = [one for one in self._roots(module) if id(one) not in writes]
        seen: set[int] = set()
        unread = {id(one) for one in module.ctors if id(one) in writes}
        while True:
            self._walk(left, seen)
            named = self._variables_named_by(
                module, [func for func in module.functions.values()
                         if id(func) in seen])
            wanted = [one for one in module.ctors if id(one) in unread
                      and any(id(var) in named for var in writes[id(one)])]
            if not wanted:
                break
            unread.difference_update(id(one) for one in wanted)
            left = wanted
        table_read = module.auxv is not None and id(module.auxv) in named
        if table_read:
            # And the reader of the vector, which the entry point calls, where
            # what is kept reads the table it fills.
            found = module.functions.get(AUXV_MAKE)
            if found is not None:
                self._walk([found], seen)
        return seen, unread, table_read

    def _walk(self, queue: list[Function], seen: set[int]) -> None:
        """Add to *seen* every function *queue* reaches."""
        while queue:
            func = queue.pop()
            if id(func) in seen:
                continue
            seen.add(id(func))
            queue.extend(self._called_by(func))

    def _only_writes(self, ctor: Function) -> list[GlobalVar] | None:
        """The variables *ctor* writes, where writing them, its own frame and
        nothing else is all it and what it calls do; nothing otherwise."""
        written: list[GlobalVar] = []
        seen: set[int] = set()
        queue = [ctor]
        while queue:
            func = queue.pop()
            if id(func) in seen:
                continue
            seen.add(id(func))
            if func.is_declaration:
                return None
            for block in func.blocks:
                for inst in block.insts:
                    if not _harmless(inst, written):
                        return None
            queue.extend(self._called_by(func))
        return written

    def _called_by(self, func: Function) -> list[Function]:
        """The functions *func* names."""
        found: list[Function] = []
        for block in func.blocks:
            for inst in block.insts:
                found.extend(named for named in inst.references()
                             if isinstance(named, Function))
        return found

    # -- variables -------------------------------------------------------------

    def _variables_named_by(self, module: Module,
                            functions: Iterable[Function]) -> set[int]:
        """The identities of the variables *functions* name.

        Being written counts as being named even where nothing reads what was
        written.  A write is an effect that outlives the function, and whether
        anyone should have written it is a question for the semantic analysis to
        report, not one to settle by quietly deleting the variable.

        So does being named at all, which is what asking the operands is for: an
        address taken of a variable is a use of it even where nothing has yet
        read through that address, and an array is named exactly that way --
        what a value of one is, is where its elements are.

        A function whose body is elsewhere -- the runtime's, one the program
        declares external -- names nothing here: what is compiled elsewhere can
        name only what the image offers, and a variable it offers is a root of
        its own.  What such a function is handed, it is handed by the call,
        which is an operand of a function that is reached.
        """
        named: set[int] = set()
        for func in functions:
            for block in func.blocks:
                for inst in block.insts:
                    for place in (*inst.operands, *inst.reads(), *inst.writes()):
                        if isinstance(place, GlobalVar):
                            named.add(id(place))
        # What a kept variable holds the address of is kept as well: a string's
        # bytes, a table's entries, a variable another one refers to.
        waiting = [var for var in module.globals.values() if id(var) in named
                   or var.linkage is Linkage.VISIBLE]
        while waiting:
            for target in _addresses_in(waiting.pop().initializer):
                if id(target) not in named:
                    named.add(id(target))
                    waiting.append(target)
        return named


def _harmless(inst: Instruction, written: list[GlobalVar]) -> bool:
    """Whether *inst* does nothing but what a constructor nothing reads may do:
    work out a value, read, call a function whose body is asked the same, or
    write its frame or a variable it names, which goes to *written*."""
    if isinstance(inst, UnreachableInst):
        return False
    if isinstance(inst, Terminator) or not inst.has_effects:
        return True
    if isinstance(inst, CallInst):
        # What the callee's body does is asked of it in its turn -- but of one
        # that asked to be opaque, which is a call kept whatever it does.
        return isinstance(inst.callee, Function) and not inst.callee.attrs.opaque
    if isinstance(inst, StoreInst) and inst.ordering.value == "plain":
        place = _place_of(inst.operands[1])
        if isinstance(place, GlobalVar):
            written.append(place)
            return True
        return isinstance(place, FrameInst)
    return False


def _place_of(address: Value) -> object:
    """What *address* points into, seen through the casts and offsets that
    keep a pointer one: a variable, a frame slot, or nothing known."""
    while True:
        if isinstance(address, (GlobalVar, FrameInst)):
            return address
        if isinstance(address, AddressInst):
            address = address.operands[0]
        elif isinstance(address, CastInst) and address.kind is CastKind.BITCAST \
                and isinstance(address.operands[0].ty, PtrType):
            address = address.operands[0]
        elif isinstance(address, BinaryInst) and isinstance(address.ty, PtrType) \
                and isinstance(address.operands[0].ty, PtrType):
            address = address.operands[0]
        else:
            return None


def _addresses_in(held: object) -> list[GlobalVar]:
    """Every variable a constant holds the address of, however deep."""
    match held:
        case AddressConst():
            return [held.target] if isinstance(held.target, GlobalVar) else []
        case PartsConst():
            return [one for part in held.parts for one in _addresses_in(part)]
        case ArrayConst():
            return [one for part in held.elements for one in _addresses_in(part)]
        case RecordConst():
            return [one for part in held.fields for one in _addresses_in(part)]
        case ResultConst():
            return _addresses_in(held.answer)
        case SumConst() if held.part is not None:
            return _addresses_in(held.part)
    return []


def _where(name: Span, whole: Span) -> Span:
    """Where a report about a definition points: at its name, where there is one.

    The whole definition begins at its first attribute or at the keyword, so a
    reader following the log gets the line right and the column wrong -- and for
    a definition at the top level, always column one.
    """
    return name if name.is_valid else whole
