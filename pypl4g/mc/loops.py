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
