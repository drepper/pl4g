The C++ implementation: what is open
====================================

`[ ]` open, `[x]` done, `[?]` needs a decision before it can be started.  What the C++ implementation is and how it is held to
pypl4g is [spec/cpp-implementation.md](spec/cpp-implementation.md).

[ ] make what `-fpie` relocates read-only again once it is relocated.  The words the startup code patches stay writable for the
    life of the process, the sections holding them -- a `.rodata` among them made `.data.rel.ro` -- being in the writable
    segment; what `PT_GNU_RELRO` and the dynamic linker's `mprotect` do for a shared object is not done.  One `mprotect` of the
    pages after the walk, from a range the writer gives the startup code, would do it.

[ ] find out whether the register allocator follows a value along a conditional branch in the middle of a block.  Writing the
    relocation walk of `-fpie` with branches inside blocks, the allocator gave the register of a value still live to a constant;
    the walk now ends every block at its branch, and what else branches inside a block does so with physical registers only.
    It is pypl4g's allocator ported, so the question is pypl4g's as much: a property of the machine functions both make, or a
    bug in both.
