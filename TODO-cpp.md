The C++ implementation: what is open
====================================

`[ ]` open, `[x]` done, `[?]` needs a decision before it can be started.  What the C++ implementation is and how it is held to
pypl4g is [spec/cpp-implementation.md](spec/cpp-implementation.md).

[x] make what `-fpie` relocates read-only again once it is relocated.  The words the startup code patches stay writable for the
    life of the process, the sections holding them -- a `.rodata` among them made `.data.rel.ro` -- being in the writable
    segment; what `PT_GNU_RELRO` and the dynamic linker's `mprotect` do for a shared object is not done.  One `mprotect` of the
    pages after the walk, from a range the writer gives the startup code, would do it.
    Done on 2026-10-20: the relocated read-only sections are a writable segment of their own, page-disjoint from the data,
    named by a `PT_GNU_RELRO`, and `__pl4g_relocate` makes them read-only once the walk is done (`mprotect(…, PROT_READ)`).
    pie_agrees.py checks the segment on every image.

[x] find out whether the register allocator follows a value along a conditional branch in the middle of a block.  Writing the
    relocation walk of `-fpie` with branches inside blocks, the allocator gave the register of a value still live to a constant;
    the walk now ends every block at its branch, and what else branches inside a block does so with physical registers only.
    It is pypl4g's allocator ported, so the question is pypl4g's as much: a property of the machine functions both make, or a
    bug in both.
    Done on 2026-10-20: a bug in both.  Liveness was worked out per block, and a branch before the end of a block read
    nothing, so a value its target reads and the block writes again after it was not live up to the branch -- and was not
    live into the block either.  pypl4g's lowering branches inside blocks 31044 times over the test suite, 1110 of them in
    that shape, and wrote nothing between such a value's last use and the branch in any of them, which is all that kept the
    code right.  Now a branch before the end reads what its targets want, in pypl4g and in the C++ implementation alike;
    13 x86-64 images changed, in both.  test_regalloc.py holds it, and mc_agrees.py makes up branches inside blocks.
