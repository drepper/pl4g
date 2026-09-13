To Do List for the pypl4g compiler
==================================

`[ ]` open, `[x]` done, `[?]` needs a decision before it can be started -- such an entry carries a
`Question:` paragraph saying what is undecided and what the choices are.

[x] By default, all functions and variables are not visible to the outside, including when used as a module.  The `@[export]` attribute
    can be attached to a function or variable.  This also determines ELF symbol visibility.

[x] implement the `@[ignore(NUMBER)]` attribute which is defined as current `@[expect(NUMBER)]` is implemented.  The latter is similarly
    defined except that it is an error if the error/warning is not present

[x] global variables not defined `mut` are truly constants and should be defined in the `.rodata` section in the ELF file.  Done: the
    image writer groups the mapped sections by the permissions they need rather than by a writable flag, so a read-only section that
    is neither writable nor executable gets a loadable segment and a page of its own.

[x] non-`mut` local variables for which no reference is kept can be entirely dropped.  Done: a dead-code sweep removes any
    instruction nothing uses and that has no effect, which is the same question and stays right once a reference to a local can be
    kept, since a reference will be a use.  Each instruction shape says for itself whether it has an effect.

[?] a local that was dropped should be defined as a constant expression in the debug information, so that a debugger can still
    show it.  Waits on there being any debug information at all.
    Question: the compiler emits no debug information and neither specification document mentions any.  Should it emit DWARF, and
    if so which version -- 4, which every tool reads, or 5, which is smaller and what current toolchains default to?  The
    alternative is a format of the compiler's own, which would be smaller still and which the incremental rebuild could update in
    place, but which no debugger reads.  Until this is answered the entry above cannot start.

[x] functions not called, not exported, and not referenced can be dropped and should not appear in the binary.  Done: reachability
    is computed forwards from roots -- the startup function, the constructors, the destructors, the tests and everything exported --
    following what each instruction says it names, and runs at every optimization level.

[x] a variable at the top level that nothing reaches should be dropped as well.  Done: each instruction says which places it reads
    and which it writes, the reachability walk carries variables after the functions in the same pass, and an exported variable is
    a root of its own.  The pass is now `dropunreached`, since it no longer drops only functions.

[x] implement a register allocator.  Done: linear scan in `pypl4g/mc/regalloc.py`, with each table row saying what the instruction
    does with each operand, the calling convention saying which registers may be given out, and a value hinted towards the register
    a result is returned in so that the move into it disappears.  `_check_single_use` is gone from all three backends and the two
    fixed-width ones no longer set registers aside for a store's address and value.

[x] spill to a stack frame when more values are wanted at once than there are registers.  Done: a value that cannot have a register
    gets a slot in the frame and is read back before each use, and the function is rewritten and allocated again rather than
    patched.  The frame is made only where a slot was taken, and given back before every return.  A register may say it must not be
    spilled, which RISC-V needs for the pair of instructions that build an address.

[ ] split a live range instead of spilling a value for its whole life.  A spilled value is in memory everywhere, so every use
    costs a load; splitting it would keep it in a register where it is busy.  Linear scan makes this easy and the loop that rewrites
    and reallocates is where it would go.

[ ] a frame larger than the immediate a stack adjustment can carry is reported rather than built in steps.  RISC-V reaches this
    first, at about two hundred and fifty slots, and AArch64 at about five hundred; x86-64 does not.  The same question as
    materializing a wide constant, and it wants the same answer.

[x] emit conditional branches.  Done for `BrInst`, `CondBrInst` and `UnreachableInst`: a branch is selected together with the
    comparison that feeds it, since that is the shape all three architectures have, and the condition is inverted where that lets
    the branch fall through instead of jumping.  Every ordering is compiled and run on all three targets.

[ ] lower `SwitchInst`.  It exists in the representation and nothing generates one, since the language has no construct that would.
    A chain of comparisons is correct and is what a first version should do; a jump table wants the relocation work that
    position-independent code needs anyway.

[ ] produce a truth value in a register.  A comparison feeding one branch is folded into it; one whose result is wanted as a value
    needs `setcc` on x86-64, `cset` on AArch64 and `slt`/`sltu` with a fixup-up for equality on RISC-V.  Needed by the comparison
    operators in TODO-language.md, and reported (8501) until then.

[x] parse expressions with precedence, and lower `BinaryInst`.  Done: precedence climbing with a table, so an operator is a row and
    not a new level of the grammar; `Binary` and `Unary` nodes in the syntax tree; `BinaryInst` and `UnaryInst` lowered on all
    three targets, with an operand no table row can carry put in a register.  `CmpInst` as a value is the entry below.

[ ] emit frame information and an unwinder.  Decided: a fault -- an arithmetic overflow to begin with -- aborts with a real
    multi-frame backtrace, so this is frame information plus an unwinder in the generated code rather than a bare trap.  Needs the
    register allocator's frame layout first.  One question inside it to settle when it is written: `.symtab` is in the image but is
    not mapped, so a backtrace carrying names needs either a table that is loaded or addresses only.  The message goes out through
    a raw system call, which is also what the pre-`io_uring` error path in TODO-language.md needs.

[ ] implement module system.  A module is loaded at compile-time.  The syntax is `let modname := import("somename")` where `modname`
    is the name the module is known as in the compilation unit and `somename` is the name of the module.  There will be built-in
    modules in future, at some point.  For now modules are PL4G source files which are loaded.  They are searched for by a path
    and depending on whether the `somename` string (implement strings) contains a `/`.
    - modules which names staring with `/` are naming absolute files and only the addressed location is searched
    - the directory of the file is searched with the file name appended (even if the name contains a `/` somewhere)
    - a path defined by build rules (for now just a command line parameter) is searched, one of the colon-separated directory
      names at a time.  Absolute path names are used as is, others are searched relative to the files source or the current
      working directory, in that order
    - if still not found, look in system directories which are used by the installation.  This does not happen if the name
      contains a slash.
    The file name has the extension `.pl4g` appended if it does not already have it.
    Once a file is located the file name is remembered.  Before any future import is about to look for a file, a check is
    performed to see whether it is already known and if yes, the loaded data is shared.
    Symbols in a module are known within the source as `modname.NAME` where `NAME` is the name of the exported object in the imported
    file and `modname` is the variable from the assignment.  When generating a symbol name for the ELF symbol name the name of the
    importing module is prepended to the name of the imported module and then the object name is appended.  A module's name can
    consist of multiple concatenated module names.  The modules name is the basename part of the file name, without the `.pl4g`
    extension.  In case of a conflict with another module with the same basename append the hash sum of the respective full path.
    In case a module is imported more than once only one instance is used and the name which is used is the shortest and in case
    of a tie in length, the one sorting first.

[?] patching a binary while it is in use.  spec/details.md asks for hooks into the system that controls binary creation so that a
    binary can be changed while it is being used.  Linux refuses to write to a running executable's file, so this needs a concrete
    mechanism -- writing to the process's memory, a `memfd`-backed scheme, or a supervisor built into the generated runtime -- and
    none has been chosen.
    Question: which of the three?  Writing to the process's memory with `process_vm_writev` or `ptrace` needs no cooperation from
    the program but needs privilege and cannot change the file on disk, so the patch is lost at the next start.  A `memfd`-backed
    scheme -- the program runs from an anonymous file it can rewrite -- keeps the change but needs the program to be started
    through something that sets it up.  A supervisor inside the generated runtime is the only one that needs neither privilege nor
    a special launcher, but it puts a thread and a channel into every program, which the requirement to depend on no system runtime
    makes a heavy thing to add by default.  This also decides who initiates: the compiler pushing a patch, or the program pulling
    one.

[x] report a value written to a variable at the top level that nothing reads.  Done: diagnostic 4007, controllable as
    `unread-variable`, answered in the whole-program phase once every function has been checked.  What the definition says it
    raises is carried on the variable so that `@[expect(4007)]` still stands where a reader would write it.

[ ] materialize a constant wider than one instruction can carry.  It needs a sequence -- two move-wide instructions on one
    architecture, an upper-immediate load and an add on another -- and none is generated, so such a constant is reported instead.

[ ] materialize the address of a symbol on RISC-V.  The rule adopted for position-independent code is that an address is only ever
    produced by one helper emitting a program-counter-relative computation.  On x86-64 that is one instruction and on AArch64 a
    pair whose halves are independent; on RISC-V the pair is not, since the second instruction's relocation refers to the label of
    the first rather than to its own address.  The backend has neither relocation rather than half of the pair, and the two
    instructions are present only in the form taking a plain immediate.

[ ] set the RISC-V header flags.  The ELF header of a RISC-V image carries flags saying which extensions the code uses and which
    floating-point convention it follows.  Zero is correct while only the base integer set is emitted; emitting floating point
    will mean setting them, and the image writer has no field for them yet.


Optimizations
-------------

[ ] Implement value range propagation.  Needs the arithmetic entry in TODO-language.md, which is what produces the checks this
    would remove.  The result is obviously usable in many situations, including:
    [ ] skip overflow/underflow checking of arithmetic operations
