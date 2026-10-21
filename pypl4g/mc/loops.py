"""Which blocks are in a loop, and how deeply.

One number per block, and what wants it is the register allocator: an
instruction inside a loop runs once for every turn of it, so a value read there
costs a reload every turn if it is sent to the frame.  Weighing a block by how
deeply it is nested is the oldest answer there is to that, and it is the one
here: depth 0 outside every loop, one more for each loop a block is inside.

**What a loop is** is the standard definition and not a shape the front end
described.  By the time the machine function exists there is no `while` left --
only blocks and edges, and a loop lowered from something else would be as much a
loop as one written down.  So: an edge from *u* to *v* where *v* dominates *u*
is a **back edge**, and the loop it closes is *v* together with every block that
reaches *u* without passing through *v*.  A block's depth is how many such loops
hold it.

**A block the entry cannot reach is at depth 0** and belongs to no loop.  Nothing
dominates it in the sense the definition wants, and nothing runs there; giving it
a weight would be weighing code that is never executed.
"""

from __future__ import annotations

from typing import Sequence

from .machine import MachineFunction

#: The edges of a graph, as for each block the blocks it reaches.
Edges = Sequence[tuple[int, ...]]


def _reachable(edges: Edges) -> set[int]:
    """Every block the entry reaches, by its place in the layout."""
    if not edges:
        return set()
    found = {0}
    stack = [0]
    while stack:
        at = stack.pop()
        for reached in edges[at]:
            if reached not in found:
                found.add(reached)
                stack.append(reached)
    return found


def _predecessors(edges: Edges) -> list[list[int]]:
    """For each block, the blocks that reach it in one step."""
    found: list[list[int]] = [[] for _ in edges]
    for at, going in enumerate(edges):
        for reached in going:
            found[reached].append(at)
    return found


def dominators(edges: Edges) -> list[set[int]]:
    """Which blocks every path from the entry to each block passes through.

    The ordinary fixpoint: the entry is dominated by itself alone, everything
    else starts dominated by everything and loses whatever a predecessor does
    not have.  Initialising to *all* blocks rather than to none is what makes
    the answer right where there is a cycle -- a block of a loop would otherwise
    be found to dominate nothing at all on the first pass round.

    A block the entry does not reach is given the empty set, which says of it
    the only true thing: no path arrives, so no path passes through anything.
    """
    reached = _reachable(edges)
    predecessors = _predecessors(edges)
    doms = [set(reached) if at in reached else set() for at in range(len(edges))]
    if reached:
        doms[0] = {0}
    changed = True
    while changed:
        changed = False
        for at in range(1, len(edges)):
            if at not in reached:
                continue
            common: set[int] | None = None
            for before in predecessors[at]:
                if before not in reached:
                    continue
                common = (set(doms[before]) if common is None
                          else common & doms[before])
            found = {at} if common is None else common | {at}
            if found != doms[at]:
                doms[at] = found
                changed = True
    return doms


def back_edges(edges: Edges) -> list[tuple[int, int]]:
    """Every edge that goes back to a block it is inside, as (latch, header).

    Which is what closes a loop, and the only thing that does: an edge to a
    block that dominates the one it leaves cannot be taken without coming round
    again, since every path that reached here came through there.
    """
    doms = dominators(edges)
    return [(at, reached) for at, going in enumerate(edges) for reached in going
            if reached in doms[at]]


def natural_loop(edges: Edges, latch: int, header: int) -> set[int]:
    """The blocks of the loop the back edge from *latch* to *header* closes.

    The header, and everything that reaches the latch without going through the
    header -- found by walking backwards from the latch and stopping there,
    which is the definition read as an algorithm.
    """
    predecessors = _predecessors(edges)
    body = {header}
    stack: list[int] = []
    if latch != header:
        body.add(latch)
        stack.append(latch)
    while stack:
        at = stack.pop()
        for before in predecessors[at]:
            if before not in body:
                body.add(before)
                stack.append(before)
    return body


def loop_depths(function: MachineFunction) -> list[int]:
    """How many loops hold each block of *function*, by its place in the layout.

    Nested loops add up: a block inside a loop inside a loop is at depth two,
    which is what makes a weight of ten to the depth say that its code runs a
    hundred times for the once a block outside both runs.
    """
    edges = function.successor_indices()
    depths = [0] * len(edges)
    for latch, header in back_edges(edges):
        for at in natural_loop(edges, latch, header):
            depths[at] += 1
    return depths


#: How many times a loop is taken to turn for each time it is entered.
TURNS = 10


def block_frequencies(function: MachineFunction) -> list[float]:
    """How often each block of *function* is taken to run for each time the
    function is called, by its place in the layout.

    Ten turns of every loop, as the depths say, and beside that a guess at which
    way each branch goes: either way alike.  So a block behind one test in a
    loop runs half as often as the loop's header, and the call behind two tests
    a quarter as often -- which is what the depth alone cannot say, a block
    being as deep in a loop whether every turn runs it or almost none.

    **A way out of a loop is taken once, whichever turn takes it.**  What the
    branches inside a loop share between them is a turn, and only the ways
    that stay in the loop share it: the header of a `while` goes on into the
    body every turn but the last.  Where the loop is left is reached as often as
    the loop is entered -- its header's share at the level outside -- divided
    among the ways out there are.  That keeps what follows a loop as frequent as
    what came before it, which a halving at every test of the loop's condition
    would not.

    The guess is the uniform one LLVM's block frequencies start from without a
    profile, with the loop scale this allocator already used; Ball and Larus's
    heuristics for which way a branch goes would refine it and are not here.
    """
    edges = function.successor_indices()
    count = len(edges)
    if count == 0:
        return []
    reached = _reachable(edges)
    latches = back_edges(edges)
    backward = set(latches)
    # Every loop, its blocks, by its header: two back edges to one header are
    # one loop.
    bodies: dict[int, set[int]] = {}
    for latch, header in latches:
        bodies.setdefault(header, set()).update(natural_loop(edges, latch, header))
    holding: list[list[int]] = [[] for _ in range(count)]
    for header, body in bodies.items():
        for at in body:
            holding[at].append(header)
    depths = [len(found) for found in holding]
    exits: dict[int, int] = {header: sum(1 for at in body for reached_ in edges[at]
                                         if reached_ not in body)
                             for header, body in bodies.items()}
    # The forward edges in the order the layout and each block's successors
    # give them, for each block the ones arriving.
    arriving: list[list[int]] = [[] for _ in range(count)]
    for at, going in enumerate(edges):
        for target in going:
            if (at, target) not in backward:
                arriving[target].append(at)
    share = [0.0] * count
    for at in _forward_order(edges, backward, reached):
        if at == 0:
            share[at] = 1.0
            continue
        total = 0.0
        for source in arriving[at]:
            if source not in reached:
                continue
            left = [header for header in holding[source]
                    if at not in bodies[header]]
            if left:
                # Out of every loop in *left*: the outermost of them is entered
                # as often as its header's share says.
                outer = max(left, key=lambda header: len(bodies[header]))
                total += share[outer] / exits[outer]
                continue
            staying = sum(1 for target in edges[source]
                          if all(target in bodies[header]
                                 for header in holding[source]))
            total += share[source] / staying
        share[at] = total
    return [share[at] * TURNS ** depths[at] for at in range(count)]


def _forward_order(edges: Edges, backward: set[tuple[int, int]],
                   reached: set[int]) -> list[int]:
    """The blocks the entry reaches, each after every block with a forward edge
    to it: the reverse of the order a walk along forward edges leaves them in."""
    if not edges:
        return []
    order: list[int] = []
    seen = {0}
    stack: list[tuple[int, int]] = [(0, 0)]
    while stack:
        at, next_ = stack[-1]
        going = [target for target in edges[at] if (at, target) not in backward]
        if next_ < len(going):
            stack[-1] = (at, next_ + 1)
            target = going[next_]
            if target not in seen:
                seen.add(target)
                stack.append((target, 0))
            continue
        stack.pop()
        order.append(at)
    order.reverse()
    return [at for at in order if at in reached]
