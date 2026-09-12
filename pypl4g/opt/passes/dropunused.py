"""Dropping the functions nothing can reach.

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
"""

from ...ir.function import Function, Linkage
from ...ir.module import Module


class DropUnusedFunctions:
    """Removes functions no root reaches."""

    name = "dropunused"

    def run(self, module: Module) -> bool:
        """Drop what nothing reaches; report whether anything changed."""
        reachable = self._reachable(module)
        kept = {name: func for name, func in module.functions.items()
                if id(func) in reachable}
        if len(kept) == len(module.functions):
            return False
        module.functions = kept
        return True

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
