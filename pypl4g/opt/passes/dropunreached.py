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
* whatever the program exports, which is by definition callable from outside
  and so cannot be known to be unreachable here.

From there it follows what each instruction says it names.  A call names its
callee; nothing else names a function yet, and when something does -- a pointer
to one, a table of them -- it says so on the instruction and this pass needs no
change.

Variables follow the functions.  A variable is reached when a function that is
itself reached names it, so dropping a function can be what makes a variable
unreachable -- which is why the two are answered here together and in that
order rather than by two passes that would have to be run until they agreed.
A variable the program exports is a root of its own, for the reason an exported
function is: something outside this compilation may name it.
"""

from collections.abc import Iterable

from ...ir.decisions import DecisionKind
from ...ir.function import Function, Linkage
from ...ir.module import GlobalVar, Module


class DropUnreached:
    """Removes the functions and variables no root reaches."""

    name = "dropunreached"

    def run(self, module: Module) -> bool:
        """Drop what nothing reaches; report whether anything changed.

        Each thing dropped is recorded.  Leaving something out of a binary is a
        decision about the program, and one a reader is entitled to ask about:
        "I wrote that function, where is it?" has an answer, and this is where
        the answer is kept.
        """
        reachable = self._reachable(module)
        functions = {name: func for name, func in module.functions.items()
                     if id(func) in reachable}
        changed = len(functions) != len(module.functions)
        for name, func in module.functions.items():
            if name not in functions:
                module.decisions.record(
                    DecisionKind.DROP_FUNCTION, name,
                    "nothing the program can run reaches it, and it is not "
                    "exported, so nothing outside can reach it either",
                    func.span)
        module.functions = functions

        named = self._variables_named_by(module, functions.values())
        variables = {name: var for name, var in module.globals.items()
                     if id(var) in named or var.linkage is Linkage.EXPORTED}
        if len(variables) != len(module.globals):
            changed = True
            for name, var in module.globals.items():
                if name not in variables:
                    module.decisions.record(
                        DecisionKind.DROP_VARIABLE, name,
                        "no function that is itself reached names it, and it is "
                        "not exported",
                        var.span)
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
        roots.extend(module.ctors)
        roots.extend(module.dtors)
        roots.extend(module.tests)
        roots.extend(func for func in module.functions.values()
                     if func.linkage is Linkage.EXPORTED)
        return roots

    def _reachable(self, module: Module) -> set[int]:
        """The identities of the functions a root reaches, directly or not."""
        seen: set[int] = set()
        queue = list(self._roots(module))
        while queue:
            func = queue.pop()
            if id(func) in seen:
                continue
            seen.add(id(func))
            queue.extend(self._called_by(func))
        return seen

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

        A function whose body is elsewhere may name anything, so one of those
        keeps every variable.  None exists yet, which is why this is a guard
        rather than a mechanism.
        """
        named: set[int] = set()
        for func in functions:
            if func.is_declaration:
                return {id(var) for var in module.globals.values()}
            for block in func.blocks:
                for inst in block.insts:
                    for place in (*inst.reads(), *inst.writes()):
                        if isinstance(place, GlobalVar):
                            named.add(id(place))
        return named
