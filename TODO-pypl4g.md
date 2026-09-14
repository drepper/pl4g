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

[x] split a live range instead of spilling a value for its whole life.  Done as far as it pays: a value read again by the next
    instruction is not read from the frame twice, and one read straight after it was computed is not read back at all.  Measured on
    a program built to show it, that is two instructions fewer on each of the fixed-width targets and one more on x86-64, whose
    two-address form puts a move between the two reads and so blunts it.

[x] follow the graph rather than the layout when saying what is live.  Done: per-block live-in and live-out settled by the
    ordinary backward fixpoint over the machine control-flow graph, with a range then the hull of the points at which a register
    is live.  On a graph whose every edge goes forward the ranges are exactly what the old rule gave, so nothing that compiled
    before changed; with a branch backwards the old rule was unsound, and a value computed before a loop and last read in the
    middle of its body had its register handed to something later in the same body.  The edges are recorded as the blocks are
    built and checked on every function.

[x] pass a branch's arguments as a parallel copy.  Done: every move is built before any is emitted, and they are put in an order
    in which none reads what another has written; a cycle -- which is what a loop carrying two values makes on every turn -- is
    broken by holding one register in a fresh virtual one.  The hazard is asked of the registers and not of the values, which is
    what makes a branch reading a parameter's register through an `extract` come out right.

[ ] weigh a loop when choosing what to spill.  The victim is the range that reaches furthest, which in a loop is systematically
    the value the loop carries -- the worst possible choice, since its reload runs every turn.  The standard answer is a weight by
    loop depth, and the block flow the liveness already builds is what it needs.

[ ] keep a spilled value in a register across instructions that do not read it, where that is cheaper than reloading.  The step
    beyond the entry above, and the one that needs a cost model: the register held is one another value cannot have, so holding it
    causes a spill somewhere else, which is exactly what the x86-64 measurement above shows in miniature.  Now that there are
    loops, the count of times a load runs has stopped being the count of times it is written, so there is something to base a
    model on.

[ ] a frame larger than the immediate a stack adjustment can carry is reported rather than built in steps.  RISC-V reaches this
    first, at about two hundred and fifty slots, and AArch64 at about five hundred; x86-64 does not.  Now that a constant of any
    width can be built, the answer is to build the size in a register and add it -- and the register has to be one the frame does
    not yet exist to spill.

[x] emit conditional branches.  Done for `BrInst`, `CondBrInst` and `UnreachableInst`: a branch is selected together with the
    comparison that feeds it, since that is the shape all three architectures have, and the condition is inverted where that lets
    the branch fall through instead of jumping.  Every ordering is compiled and run on all three targets.

[ ] merge a block into its only predecessor, and replace a block parameter that has one incoming argument by that argument.
    `match` makes the first of these worth having: an arm that assigns nothing still ends in a branch to the block the arms join
    at, and a join with one incoming edge is a block that need not exist.
    What is left after a branch on a settled condition is replaced by its jump: a chain of blocks that fall through, which costs
    nothing in the generated code and is why this was not done with the logical operators.  `if` is the right occasion for it,
    since an `if` whose condition is known should leave no trace at all.

[ ] split a critical edge so that a conditional branch can carry arguments.  Not needed by anything yet -- the short-circuit
    lowering is shaped to avoid it, and `if` will be too -- and refused (8501) rather than got wrong until something asks.  The
    same entry covers a branch handing a block its own parameters rearranged, which needs a temporary the way any parallel copy
    does.

[ ] let the instruction table stop naming the registers a call destroys.  They are a convention's business and the table cannot
    reach a convention, so each backend names them beside its registers and the call row uses them.  It costs nothing today,
    every convention each target has naming the same set; the day one does not, the call will have to carry them per instance.

[x] teach the register allocator about register classes.  Done: the allocator takes an allocation order per class rather than
    one order, a virtual register says which class it belongs to, and `_choose` hands out from the order for that register's
    class.  The calling convention names the floating-point argument, result and allocation registers beside the integer ones and
    builds the mapping the allocator wants.  A frame slot is still eight bytes whatever the class, which is right until there is a
    value wider than that.

[ ] let an operand require a particular register, so that an instruction with a fixed register pair can be used.  Division turned
    out not to need it -- an instruction that declares it writes a register is already enough to keep other values out of it, so
    the divisor cannot land in the pair -- but the one-operand multiply does, since its *input* has to be in a particular register
    and nothing can say so.  That is the only way to see the upper half of a product on x86-64, and until then a saturating
    multiplication of the widest type is refused on every target rather than on the one that cannot do it.

[ ] let the textual IR be read back for everything it can be written for.  The reader lags the printer: it does not know
    floating-point types or constants, calls, casts, the result type or the three instructions that make and read one.  Nothing
    depends on it today -- the golden round-trip test names the cases it covers -- and what it costs is that a dump of a program
    using any of those cannot be fed back in.  Worth closing in one piece rather than a shape at a time.

[ ] lower `SwitchInst`.  It exists in the representation and nothing generates one.  A `match` over a result is two ways and is
    lowered as a conditional branch; a `match` over an *enumeration* is a chain of comparisons, one per value an arm names, which
    is correct and is what a first version should do -- this is what would replace that chain.  A jump table wants the relocation
    work that position-independent code needs anyway.  Worth measuring against the chain before it is written: an enumeration with
    three values is better off with the comparisons.

[x] reach a place through an address that is not a name.  Done: a load or a store whose address operand is not a variable takes
    the address from a register, which every backend's move and store selection could already build and only the six guards above
    them refused.  Three instructions make such an address -- `address` puts a variable's own address in a register, adding a
    number of bytes to an address moves it, and `bitcast` reads the same bits as a pointer to something else -- and the unused
    `alloca` stub went, since storage a program allocates comes from an allocator and an allocator answers with an address like
    any other.  Adding to an address deliberately does not take the checked path the same operator takes on two numbers.

[x] emit an allocator.  Done: `pypl4g/target/allocator.py` emits a bump allocator over a list of `mmap`ed chunks -- GNU's
    obstacks -- written once and parameterised by a small per-target record saying the numbers of the two system calls, which
    registers they take and which instruction enters the kernel.  An arena is three words, so a program makes as many as it
    wants; an allocation is an addition and a comparison; a whole arena is given back at once and a chunk never on its own.  An
    allocation that cannot be met goes through `__pl4g_abort` like an arithmetic fault.  It is emitted only where something
    declares one of its entry points.

[x] give an allocator a spelling in the language.  Done: `arena` is a type, `⎕arena` is what a variable of it starts out holding,
    `⎕heap` is the one the compiler provides, and `in` says which arena a collection comes out of.  A program makes as many as it
    wants, and a collection made out of two others comes out of the same arena the first of them did.

[ ] give an arena back from a program.  The runtime has `__pl4g_release` and the language has no way to call it, so nothing a
    program writes can give an arena back -- which is why nothing a program writes can yet be left pointing into one that went.
    What it needs is a way to write the call, and, before that, the rule that makes it safe: a value that lives in an arena has to
    say so in its type, so that one from an arena that has been given back cannot be stored where one from another is expected.
    Today `in` says where a collection went and is no part of its type, and this entry is what would change that.

[ ] hash a value that is not one word.  Done for everything that can be a key today -- an integer, a truth value, an enumeration
    -- by one multiplication, because all of them fit in a word.  A string or a product would want a hash over its bytes, emitted
    per type, which is what the entry about a wider key in TODO-language.md waits on.

[ ] an allocator that gives memory back.  An arena frees nothing until the whole of it goes, which is right for a compiler and
    wrong for a program that runs for a long time.  A size-class allocator is the thing every language ends up with; what it needs
    first is a program that runs long enough for the difference to show.

[x] hash a value.  Done: Fibonacci hashing, one multiplication with the high bits folded down, generated once rather than per key
    type -- every key is one word, so there is nothing per type to generate.

[ ] hold a value whose type is a product or a sum.  Both are refused today (8501): a product is every one of its fields at once
    and a sum is one variant and a tag, and neither is a thing a register holds.  What they want is a place in memory, an address
    to reach it by, and a convention for passing one to a function and answering with one -- which on all three targets means
    small aggregates in registers and large ones behind a pointer.  Nothing in the language writes such a value yet, so this waits
    on the two questions in TODO-language.md rather than the other way round.  The layout is already computed, in
    `pypl4g/ir/layout.py`, including where each field starts, and the result type shows the shape the answer takes: two registers
    inside a function and in the convention, two accesses of one place in memory, and a side map from a value to its second half
    so that the allocator sees ordinary values and nothing aggregate.

[x] produce a truth value in a register.  Done: `setcc` and a widening move on x86-64, `cset` on AArch64, and `slt`/`sltu` with
    the operands exchanged or the answer inverted on RISC-V, equality there being a subtraction and then a question about the
    difference.  A comparison is folded into a branch only where that branch is its sole reader and reads it as its condition;
    anything else computes it.  A truth value is one or zero, and a byte in memory -- which also fixed a `bool` in memory being
    read and written eight bytes wide.  The comparison operators in TODO-language.md are now unblocked.

[x] parse expressions with precedence, and lower `BinaryInst`.  Done: precedence climbing with a table, so an operator is a row and
    not a new level of the grammar; `Binary` and `Unary` nodes in the syntax tree; `BinaryInst` and `UnaryInst` lowered on all
    three targets, with an operand no table row can carry put in a register.  `CmpInst` as a value is the entry above.

[x] emit the path a fault leaves the program through.  Done: the message is built whole at compile time and put in the image, and
    `__pl4g_abort` writes it to standard error through a raw system call and traps.  The program dies by the same signal on every
    target, at the point of the fault, with its stack still standing.  Nothing is emitted for a program in which nothing can
    fault.  This is also the pre-`io_uring` error path the entry in TODO-language.md asks about, and it answers that question: it
    assumes nothing about the descriptor, allocates nothing, and formats nothing.

[ ] walk the stack, so that a fault reports where it was called from and not only where it happened.  Deferred deliberately: the
    language has no way to call a function, so every stack is one frame deep and an unwinder could not be tested against the thing
    it exists for.  The analysis, so that it is not done twice:
    Two ways to walk.  A **frame pointer chain** is what `_start` already prepares for -- it sets the frame pointer and the return
    address to zero so that a walk knows where to stop -- and needs no table at all, at the cost of a register and two
    instructions in every function.  **Frame information**, a table from a code address to the frame size and to where the return
    address was put, costs nothing at run time and is what a debugger and a profiler want anyway; it is more to emit and needs an
    absolute relocation in data, which nothing generates yet.  The second is the better answer for a language that cares about
    what it emits, and the first is what to reach for if the second proves slow to write.
    Names are a third question: `.symtab` is in the image but is not mapped, so a walk that prints names needs a table of its own
    that is.  The same table can carry both, which is an argument for the second way.

[x] implement module system.  Done: `let name := import("somename")`, found in the importing file's directory, then the
    directories `--module-path` gives, then the installation's; read once however many routes reach it; named by the shortest of
    those routes, with the hash of the path where two files share a base name; a ring refused.  The top-level namespace is a
    file's rather than the compilation's, which is what makes two modules able to define one name.

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

[x] materialize a constant wider than one instruction can carry.  Done: AArch64 sets a quarter of a word at a time with
    `movz`/`movk`, and turns every bit round with `movn` first where that costs fewer instructions; RISC-V uses the sequence LLVM
    generates, an upper part built the same way, shifted as far as its own trailing zeroes allow, and the last twelve bits added;
    x86-64 already had a move that takes eight bytes and needed only a register for the places -- a store and a comparison -- whose
    immediate is narrower.  Checked by compiling a program that compares a constant it built against the same constant as the image
    writer wrote it, which is two separate paths from one number.
    Two real defects came out with it.  x86-64 chose the width of an immediate by what the number needs, ignoring that an
    instruction sign-extends one narrower than the operation, so `0xFFFFFFFF` in an eight-byte comparison was minus one.  And
    RISC-V was handed the unsigned reading of a pattern with its top bit set and tried to shift by sixty-four.

[x] materialize the address of a symbol on RISC-V.  Already done, by the work that made loads and stores reach a variable: the
    `auipc.hi20` and `addi.lo12` rows carry the two relocations the pair needs, the second measured from the label of the first,
    and the register holding the address between them is marked as one the allocator must not send to the frame.  The entry was
    written before that landed and was stale.

[x] set the RISC-V header flags.  Done: the target states its flag word and the image writer carries it into the header, with the
    three values named in `target.py` so that choosing between them is a constant rather than a change to the writer.  It is zero
    today, which is not "unset" -- it says the base integer set and the soft-float convention, which is what is emitted.


Optimizations
-------------

[ ] Implement value range propagation.  Needs the arithmetic entry in TODO-language.md, which is what produces the checks this
    would remove.  The result is obviously usable in many situations, including:
    [ ] skip overflow/underflow checking of arithmetic operations
