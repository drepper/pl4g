"""`std.print` and `std.println` build their text in a pool of the call's own.

What is checked is that nothing on the way -- the joins the template comes to,
`⍕` of each argument, `std.text` and what it calls, and a hole written as a call
of `std.padded` or `std.text` that names no arena -- takes room from `⎕heap`: the
only place the heap is named is the dispatch every allocator goes through, in the
branch a caller handing it the heap would take, and no caller does.  A program's
output looks the same either way, so the code is what is asked.

The one other way in is a view that is not one run being copied into one for the
kernel to read (`⎕one_run`), which is the heap's and given back once the kernel
has it; that branch is left out.
"""

from __future__ import annotations

import re

from conftest import describe

PROGRAM = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
type Point = x: u8 ; y: i16

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let p: Point = Point(.x \N{LEFTWARDS ARROW} 1u8, .y \N{LEFTWARDS ARROW} \N{SUPERSCRIPT MINUS}2i16)
    _ \N{LEFTWARDS ARROW} std.println\N{TOP LEFT CORNER}&mut init.io.output, "{} {} {} {} {}", 42u16, true, 'c', "text", p\N{TOP RIGHT CORNER}
    _ \N{LEFTWARDS ARROW} std.print\N{TOP LEFT CORNER}&mut init.io.output, "{}", \N{SUPERSCRIPT MINUS}7i32\N{TOP RIGHT CORNER}
    _ \N{LEFTWARDS ARROW} std.println\N{TOP LEFT CORNER}&mut init.io.output, "[{}] [{}]", std.padded(p.x, .width \N{LEFTWARDS ARROW} 4u64), std.text(255u8, .base \N{LEFTWARDS ARROW} 16u8)\N{TOP RIGHT CORNER}
    0
"""


def test_printing_never_takes_room_from_the_heap(compile_source) -> None:  # noqa: ANN001
    """The heap is named by the dispatch alone, and called by nothing else."""
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    naming: set[str] = set()
    function = ""
    block = ""
    for line in text.splitlines():
        if line.startswith("let @"):
            # The variable itself, which the dispatch takes the address of.
            continue
        found = re.match(r"fn @([^(]+)\(", line)
        if found:
            function = found.group(1)
            continue
        label = re.match(r"([\w.]+)(\(.*\))?:$", line)
        if label:
            block = label.group(1)
            continue
        if block.startswith("one_run.copy"):
            continue
        if re.search(r"@__pl4g_heap\b|@__pl4g_heap_new|join\.heap", line):
            naming.add(function)
    assert naming <= {"__pl4g_allocate"}, naming
