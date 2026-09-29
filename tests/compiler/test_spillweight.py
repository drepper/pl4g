"""Which blocks are in a loop, and what that makes the allocator give up.

Two things, and the second is the whole reason for the first: a block's loop
depth says how often its instructions run, and what the allocator sends to the
frame is what costs least to read back -- weighed by that.  Linear scan on its
own gives up the range that reaches furthest, which in a loop is the value the
loop carries, whose reload then runs every turn.
"""

from __future__ import annotations

from conftest import describe, run_compiler
from pypl4g.mc.loops import back_edges, dominators, loop_depths, natural_loop
from pypl4g.mc.machine import MachineBasicBlock, MachineFunction
from pypl4g.mc.operand import MCImm, MCMem, MCReg
from pypl4g.mc.regalloc import allocate
from test_regalloc import INFO, ORDER, SELECTOR, inst, virtual


def graph(*edges: tuple[int, ...]) -> MachineFunction:
    """A function of as many empty blocks as *edges* has rows, joined by them."""
    built = MachineFunction(name="t")
    for at in range(len(edges)):
        built.add_block("".join(("b", str(at))))
    for block, going in zip(built.blocks, edges):
        block.successors = ["".join(("b", str(at))) for at in going]
    return built


# -- the graph ------------------------------------------------------------------

def test_a_line_of_blocks_is_at_no_depth() -> None:
    """Nothing runs twice, so nothing is worth more than anything else."""
    assert loop_depths(graph((1,), (2,), ())) == [0, 0, 0]


def test_a_loop_holds_its_header_and_its_body() -> None:
    """And nothing before it or after it."""
    #  b0 -> b1 -> b2 -> b1, and b1 -> b3
    assert loop_depths(graph((1,), (2, 3), (1,), ())) == [0, 1, 1, 0]


def test_a_loop_inside_a_loop_counts_twice() -> None:
    """Which is what makes the weight say a hundred rather than ten."""
    #  b1 is the outer header, b2 the inner one, b3 the inner body.
    depths = loop_depths(graph((1,), (2, 4), (3,), (2, 1), ()))
    assert depths == [0, 1, 2, 2, 0]


def test_a_block_that_reaches_itself_is_a_loop_of_one() -> None:
    """The shortest loop there is, and the definition covers it unchanged."""
    assert loop_depths(graph((1,), (1, 2), ())) == [0, 1, 0]


def test_two_loops_one_after_the_other_are_two_loops() -> None:
    """Neither is inside the other, so neither block is at depth two."""
    assert loop_depths(graph((1,), (1, 2), (3,), (3, 4), ())) == [0, 1, 0, 1, 0]


def test_a_block_nothing_reaches_is_at_no_depth() -> None:
    """It does not run, and weighing code that does not run says nothing.

    A block whose only edge goes to itself would otherwise be a loop, which is
    what makes this worth asserting rather than assuming.
    """
    assert loop_depths(graph((1,), (), (2,))) == [0, 0, 0]


def test_what_dominates_what() -> None:
    """The entry dominates everything, and a block after a fork dominates
    nothing but itself."""
    doms = dominators([(1, 2), (3,), (3,), ()])
    assert doms[0] == {0}
    assert doms[1] == {0, 1} and doms[2] == {0, 2}
    assert doms[3] == {0, 3}


def test_an_edge_back_to_what_dominates_it_closes_the_loop() -> None:
    """Which is the definition, and the only thing that makes a loop."""
    assert back_edges([(1,), (2, 3), (1,), ()]) == [(2, 1)]


def test_a_forward_edge_closes_nothing() -> None:
    """A jump over a block is not a jump back into one."""
    assert back_edges([(1, 2), (2,), ()]) == []


def test_the_loop_is_what_reaches_the_latch_without_leaving_it() -> None:
    """A block that only the way out reaches is not in the loop."""
    #  b1 header, b2 and b3 body, b4 after.
    edges = [(1,), (2, 4), (3,), (1,), ()]
    assert natural_loop(edges, latch=3, header=1) == {1, 2, 3}


# -- and what it makes the allocator do -----------------------------------------

def loop_function(reads_in_the_loop: int, others: int) -> tuple[MachineFunction,
                                                               list]:
    """A function whose loop reads some values and whose tail reads the rest.

    Every value is computed before the loop and read after it, so all of them
    are live across the whole function.  Two things then tell them apart, and
    they point opposite ways: the first *few* are read once per turn of the loop
    as well, and they are also the ones read last of all -- so their ranges are
    the ones that reach furthest, which is what makes them the victim the rule
    linear scan was described with picks first.
    """
    built = MachineFunction(name="t")
    entry = MachineBasicBlock("entry")
    body = MachineBasicBlock("body")
    tail = MachineBasicBlock("tail")
    built.blocks = [entry, body, tail]
    entry.successors = ["body"]
    body.successors = ["body", "tail"]
    tail.successors = []
    values = [virtual() for _ in range(reads_in_the_loop + others)]
    for one in values:
        entry.append(inst("mov", MCReg(one), MCImm(1, 32)))
    for one in values[:reads_in_the_loop]:
        body.append(inst("mov", MCMem(disp=8, size_bits=32), MCReg(one)))
    for one in [*values[reads_in_the_loop:], *values[:reads_in_the_loop]]:
        tail.append(inst("mov", MCMem(disp=16, size_bits=32), MCReg(one)))
    return built, values


def test_what_a_loop_reads_is_not_what_goes_to_the_frame() -> None:
    """Though it is what reaches furthest, which used to be the whole measure.

    The values the loop reads are read again last of all, so of all the ranges
    here theirs are the ones that end latest and the furthest-reaching rule
    gives them up first -- and every reload it puts in then runs on every turn
    of the loop.  Weighing what a spill costs is what turns that around.
    """
    built, values = loop_function(reads_in_the_loop=3, others=len(ORDER) + 2)
    result = allocate(built, INFO, ORDER, SELECTOR)
    assert result.spilled, "nothing was spilled although there were too many"
    inside = {one.ident for one in values[:3]}
    assert not inside & set(result.spilled), \
        "a value the loop reads every turn was sent to the frame"


def test_and_a_value_read_more_often_is_kept_where_no_loop_is_involved() -> None:
    """The other half of the same measure, which is what makes it a price.

    With no loop anywhere, a value read five times still costs five reloads and
    one read once costs one.  Here the one read five times is also the one read
    last, so it reaches furthest and the old rule gave it up; the four extra
    reloads that buys are what the price counts and the length alone does not.
    """
    values = [virtual() for _ in range(len(ORDER) + 2)]
    built = MachineFunction(name="t")
    block = built.add_block("entry")
    for one in values:
        block.append(inst("mov", MCReg(one), MCImm(1, 32)))
    # Every value but the first is read once, and then the first is read five
    # times over, which leaves it the one whose range reaches furthest.
    for one in values[1:]:
        block.append(inst("mov", MCMem(disp=16, size_bits=32), MCReg(one)))
    for _ in range(5):
        block.append(inst("mov", MCMem(disp=8, size_bits=32), MCReg(values[0])))
    result = allocate(built, INFO, ORDER, SELECTOR)
    assert result.spilled
    assert values[0].ident not in result.spilled, \
        "the value read five times was the one sent to the frame"


# -- through the whole compiler -------------------------------------------------

#: A loop under more pressure than any of the three architectures has registers
#: for.  The four values the loop reads are read again last of all, so their
#: ranges are the ones that reach furthest and they are exactly what the old
#: rule gave up first.
PRESSED = """\
{globals}
@[expect(4007)]
let w: mut u6 = 0u6

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let n: mut u6 = 3u6
    let total: mut u6 = 0u6
{loads}
    while n > 0u6:
{inside}
        n \N{LEFTWARDS ARROW} n - 1u6
{after}
    w \N{LEFTWARDS ARROW} total
    total
"""


def pressed_source(wide: int = 26, carried: int = 4) -> str:
    """The program above, written out."""
    names = ["".join(("a", str(at))) for at in range(wide)]
    names += ["".join(("b", str(at))) for at in range(carried)]
    return PRESSED.format(
        globals="\n".join("".join(("let ", name, ": u6 = ", str(at % 9 + 1), "u6"))
                          for at, name in enumerate(names)),
        loads="\n".join("".join(("    let v", name, ": u6 = ", name))
                        for name in names),
        inside="\n".join("".join(("        total \N{LEFTWARDS ARROW} total ^ v", name))
                         for name in names[wide:]),
        after="\n".join("".join(("    total \N{LEFTWARDS ARROW} total ^ v", name))
                        for name in names))


def test_nothing_is_read_back_from_the_frame_inside_the_loop(tmp_path) -> None:  # noqa: ANN001
    """The whole of what the weight buys, read off the code that comes out."""
    source = tmp_path / "t.pl4g"
    source.write_text(pressed_source(), encoding="utf-8")
    out = tmp_path / "t.asm"
    proc = run_compiler(["--emit=asm", "-o", str(out),
                         "--target=x86_64-linux-none", str(source)])
    assert proc.returncode == 0, describe(proc)
    text = out.read_text(encoding="utf-8")
    body = text.split("_loop:")[1].split("_done:")[0]
    assert "rsp" not in body, body
    assert "rsp" in text, "nothing was spilled at all, so this tests nothing"


# The same program run on every target is `tests/language/spill-around-a-loop`,
# which is where a program that has to answer what it said belongs.
