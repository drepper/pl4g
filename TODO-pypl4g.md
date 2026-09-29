To Do List for the pypl4g compiler
==================================

`[ ]` open, `[x]` done, `[?]` needs a decision before it can be started -- such an entry carries a
`Question:` paragraph saying what is undecided and what the choices are.

[x] a system call from the language: `⎕syscall(NUMBER, ARG...)`, answering the kernel's own `i64`.  Done, on all three
    targets: an IR instruction outside the token chain, `asm.kernel` to say what the entering instruction destroys and reads,
    and one selection arm each.  What is still the standard library's is naming the numbers, which differ per architecture.

[x] give a program a way to name a system call whatever it is built for.  Done as `⎕sc@NAME`, a name the compiler provides
    carrying a key: the compiler holds the numbers per architecture and answers the one for the target.  What is still open is
    a module asking *which* architecture it is, for anything other than a call number -- `comptime if` compares types and
    refuses a question about a value (4502), so the shape that would answer it is not there.

[x] acquire and release ordering in the IR.  Done on 2026-09-17: a load may acquire and a store may release, said on the
    instruction.  x86-64 emits what it would have emitted, AArch64 `ldar`/`stlr`, RISC-V the two fences.  An ordered access to a
    value of several parts, or to a floating-point value, is refused (8501).

[x] a way to write an ordering in the language.  Done on 2026-09-17, at the user's direction: `⎕acquire(REF)` and
    `⎕release(REF, VALUE)`, said at the access rather than by the type of the place.

[x] render a reference as the program wrote it.  Done on 2026-09-29: `Type.written` beside `Type.render`, the second method
    rather than a change to the first, because the IR's form is read back as well as written and `ir/reader.py` parses what the
    printer produced.  Every message, every note the language server shows and every line of the report log asks for the new
    one; a reference is `&mut T`, a result `T ? E`, and a type that holds another writes what it holds the same way.

[ ] sequential consistency.  Acquire and release are what driving a ring needs; the ordering neither of them gives -- a write
    followed by a read of another place, seen by everyone in one order -- is the one that costs x86-64 an instruction, and
    nothing asks for it yet.

[x] report every test that failed rather than stopping at the first.  Done on 2026-09-18: `__pl4g_report` writes the message and
    comes back, the count is kept in a register a call leaves alone, and the binary exits with 66 -- the runtime's own status --
    after every test has run.

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

[x] let a bracketed value span more than one line in the grammar.  Mostly done the day after this was written, by making an end
    of line an extra; what was left was a *block* written on one line inside brackets -- `f(if c: 1u8 else: 2u8)` -- where the
    scanner did not know that a closing bracket or a comma ends such a block.  Done on 2026-09-29, with an `_error_sentinel` so
    that the scanner says nothing while the parse is recovering: manufacturing tokens of no width there multiplied the stacks
    the recovery was exploring and cost gigabytes on a file with a stray `()` in it.

[x] hold a value of a product or a sum type.  The product half was done on 2026-09-17; the sum half on 2026-09-29.  A value of a
    sum is room in the frame holding its largest part and a tag, and what travels is where that room is -- an address, as an
    array's is.  Answering with one goes through the caller's room, by the convention an answer of more values than the registers
    hold already took: `ReturnStyle.in_registers` says no for anything held in memory, and the rewrite copies the bytes in.

[ ] hold a record or a tuple in memory, so that one may hold a sum.  Refused today (9902): both travel as the values they are
    made of, so one holding a sum would carry the address of the room the sum was made in, which dangles the moment it is answered
    with.  An array in a record has the same hazard and is not refused -- the one such record in the language is `@[abi]` and is
    only ever handed over by reference -- so the fix is one thing for both: a type holding a value held in memory is itself held in
    memory, passed as an address and answered through the caller's room.

[ ] a sum at the top level.  Refused today by the rule that a variable there is bytes in the image (9902).  What it needs is what
    a collection there needed: the bytes are known while compiling -- a part and a tag -- so this one is a constant to lay out
    rather than a constructor to generate.

[x] generate the functions in the order the call graph gives.  Done on 2026-09-29: `ir/callgraph.py` answers the graph and the
    order, a depth-first walk left in the order it finished, and the three backends generate in it.  Round a cycle there is no
    order and the convention's whole caller-saved set is what a call assumes.  It turned up a bug of the kind it was meant to
    make impossible: the register allocator rebuilt instructions without their `clobbers`, so every function looked as though its
    calls destroyed nothing -- harmless only while the answer went unused.

[x] use the same ordering to decide what to inline.  Done on 2026-09-29, as an IR pass walking callees first: what the program
    asked for, what the whole program calls once, and what is small.  What is left behind is dropped by the pass that drops what
    nothing reaches, which is what makes the second rule free.

[ ] thread the memory chain through an inlined body.  A call is not in the chain -- it neither takes a token nor answers with one
    -- so the caller's loads after an inlined body still read the token that stood before it.  Nothing reorders memory today, so
    it is bookkeeping rather than a defect; the day something does, the body's last token has to reach what follows it, which
    means the block after the call taking one and every `ret` handing one over.

[ ] weigh what a copy costs where it is *generated* rather than where it is written.  The inliner measures a callee in IR
    instructions, which is what it can see; how many bytes it comes to, which registers it wants and whether it calls anything are
    the backend's answers, and a cost model over those is what an inliner that inlines the doubtful cases would want.

[ ] a bespoke convention per function rather than one for all of them.  Today `pl4g` is one convention, shaped so that the common
    small function costs nothing; the specification allows a different one per function, which would mean parameters placed where
    the body wants them and the callee-saved set narrowed to what each function actually keeps.  The second half of that is
    already done by another route -- a call destroys what the callee turned out to destroy -- and the first half wants the
    ordering above, since a caller has to know what its callee chose.

[ ] split a virtual register's live range the way a physical one's is split.  A physical register is written wherever a convention
    says it is and holds nothing between a write and the read that takes the value away, so its range is several stretches.  A
    virtual register still gets one hull, which is right for a value with one definition and wrong for one the spill rewrite
    redefines; the hull is conservative, so this is a missed register rather than a defect.

[ ] keep a spilled value in a register across instructions that do not read it, where that is cheaper than reloading.  The step
    beyond the entry above, and the one that needs a cost model: the register held is one another value cannot have, so holding it
    causes a spill somewhere else, which is exactly what the x86-64 measurement above shows in miniature.  The weight by loop
    depth the allocator now chooses a victim with is what such a model would be built on: it already says how often an
    instruction runs, which is the number this has to compare against a reload.

[ ] a frame larger than the immediate a stack adjustment can carry is reported rather than built in steps.  RISC-V reaches this
    first, at about two hundred and fifty slots, and AArch64 at about five hundred; x86-64 does not.  Now that a constant of any
    width can be built, the answer is to build the size in a register and add it -- and the register has to be one the frame does
    not yet exist to spill.

[x] emit conditional branches.  Done for `BrInst`, `CondBrInst` and `UnreachableInst`: a branch is selected together with the
    comparison that feeds it, since that is the shape all three architectures have, and the condition is inverted where that lets
    the branch fall through instead of jumping.  Every ordering is compiled and run on all three targets.

[x] teach the register allocator about register classes.  Done: the allocator takes an allocation order per class rather than
    one order, a virtual register says which class it belongs to, and `_choose` hands out from the order for that register's
    class.  The calling convention names the floating-point argument, result and allocation registers beside the integer ones and
    builds the mapping the allocator wants.  A frame slot is still eight bytes whatever the class, which is right until there is a
    value wider than that.

[ ] let the textual IR be read back for everything it can be written for.  The reader lags the printer: it does not know
    floating-point types or constants, calls, casts, the result type or the three instructions that make and read one.  Nothing
    depends on it today -- the golden round-trip test names the cases it covers -- and what it costs is that a dump of a program
    using any of those cannot be fed back in.  Worth closing in one piece rather than a shape at a time.

[ ] build a jump table for a switch with enough cases.  `SwitchInst` is lowered now, as a chain of comparisons in one block --
    one per case, in the order the instruction holds them, with what falls off the end going the default way -- and a `match` over
    an enumeration builds one.  A table wants a table of addresses in the read-only section, an indirect jump on each of the three
    architectures, and a bounds check, and is worth that only past some number of cases: an enumeration with three values is
    better off with the comparisons.  What the number is wants a way to time generated code, which the timing harness does not
    have; it times compilation.

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

[x] stop `let d: u8⟦⟧ = ⟦1u8, 2u8⟧` inside a function from crashing the compiler.  Done, and it was two crashes rather than one,
    both of them `_array_written` lowering elements with no builder to lower them into.  Where a type is wanted the elements are
    not lowered there at all -- their type is the wanted type's element type, and `_fill` lowers them into it once.  Where nothing
    is wanted, as in `⟦1u8, 2u8⟧⟦0⟧`, they say their own type and what they came to is carried to `_fill` rather than worked out
    twice, which is what keeps a call written as an element from being made twice.  The array decays as it always did.

[ ] count the bits set on AArch64 with the instruction it has.  `CNT` is in the vector unit, so a count is a move into a register
    of the other kind, the count, a horizontal add and a move back -- four instructions against the dozen the sequence is, and a
    trip between the two register files that may cost more than it saves.  Worth measuring before it is written.  Leading zeroes
    already use `CLZ`, which is an ordinary instruction there.

[ ] check on RISC-V that the processor has what the ISA string promised.  What may be emitted is settled when the program is
    built and what is not promised is refused, so a program is never built with an instruction its own `--mclevel` did not allow
    -- but a program built for `rv64gc` and started on a machine without `M` dies of an illegal instruction with nothing said.
    There is no instruction to ask with, the architecture having none; the answer is in `AT_HWCAP` of the aux vector the kernel
    hands the program, which the entry point already walks past on its way to the environment.  AArch64 needs none of this: its
    vector unit is not optional in the profile this compiler targets.

[ ] give the kinds of fault numbers of their own, out of the reserved range.  Everything leaves through 64 today, which is the
    general one; an answer that will not fit, a division by zero, an index outside its array and an allocation that failed are
    four different things a caller might want to act on differently.  What argues against it is that the message already names
    the operation, the function and the line, so a number would say less than what is there -- it is worth doing when something
    is reading the status rather than the message.

[ ] count registers per part recursively.  `parts_of` gives a tuple its members and stops, so a member that is itself several
    values -- a result among them, `〈u8?, u8〉` -- is given one register where it needs two, and the program is refused with a
    message about an encoding of `mov`.  It has never worked; what it wants is for everything that counts parts to flatten, which
    is `parts_of` answering the leaves rather than the members, and for the places that read a tuple apart to follow.  The
    `largeanswers` pass leaves such an answer alone rather than moving where it goes wrong.

[ ] put a call in the memory chain.  A call has `has_effects` and so is kept, and nothing orders memory operations around it: a
    load written after a call takes the token that was in force before it.  Nothing reorders memory operations today, so nothing
    is wrong yet; the loads the `largeanswers` pass writes after a call are the first code that depends on it, and a scheduler
    would be the thing that breaks them.  A callee can write a variable at the top level, so this is wanted for every call and not
    only for those.

[x] compute a narrow sum or difference at its own width and read the flags, rather than widening and comparing.  Done: the
    backend states which widths its arithmetic writes flags about -- all four on x86-64, thirty-two and sixty-four on AArch64,
    none at all on RISC-V, which has no flags -- and a trapping addition or subtraction at one of them is one instruction and one
    branch on the carry or overflow flag.  A `u8` sum was four instructions and a constant; it is now `add %cl,%al` and `jae`.  A
    product is left out on every target: whether one went past is in the upper half, which is a second instruction and on x86-64 a
    fixed pair of registers.
    A real defect came out with it.  The shared code asked which operation it was by comparing against the *saturating* opcode,
    which is false for the trapping one, so a trapping addition took the branch written for a subtraction -- and the widest signed
    and unsigned sums did not notice they had gone past.  Every such test now names the ordinary operation the two are built from.

[ ] let the grammar read an array literal that goes over more than one line.  The compiler reads one -- a bracket is not a
    place a line ends, so the layout rule lets it continue -- and `tree-sitter-pl4g` does not, which the agreement test catches
    the moment a program in the suite is written that way.  Found while writing one; the program was put on one line instead.

[ ] use the widest registers the newest level has for a run of elements.  `v4` promises AVX-512, whose registers are
    sixty-four bytes; what is emitted for it today is AVX2's thirty-two, which `v3` already promises.  Two things are in the
    way and both are real work rather than more rows: the EVEX prefix, which the encoder refuses where it emits VEX, and the
    mask registers -- there is no `vpmovmskb` for the widest registers, so "did any lane go past" is asked there with
    `vptestmb` into a mask register and `kortestq`, which is a third way of asking a question this already asks two ways.

[ ] emit `vzeroupper` where a function that used the wider registers calls one that may not have.  Mixing the wider forms with
    the narrower ones costs a state transition on several processors; nothing is wrong without it, which is why this is a
    to-do and not a defect.  The splat is where the two meet today: a value goes into the low half with the narrow move and is
    then spread with the wide one.

[ ] do a run of floating-point numbers at once.  The instructions are there and the check is not: what says a floating-point
    answer went past is not a comparison but a question about the number itself, and asking that of a lane apiece is its own
    piece of work.  The front end refuses to make a run of them until it is done.

[ ] multiply a run at once.  Seeing that a product went past wants the upper half of it, which none of these machines gives at
    every lane width -- the same reason the narrow scalar arithmetic leaves multiplication to the widening path.

[ ] shift a whole run at once.  A shift by one value in every lane is one instruction on both machines that have runs, and a
    shift by a lane apiece is another on the newer x86-64 levels; neither is emitted, so a shift over an array is still an
    element at a time whether or not it is inside a wrap.  What holds it up is the check: outside a wrap the distance has to be
    compared against the width in every lane, which is the same question as the arithmetic's and wants the same machinery.

[ ] fold a shift of two constants.  None of the five folds today, wrapping or not, so `1u8 « 2u8` reaches the code generator as
    an instruction.  Adding the wrapping five alone would have made a program fold inside a wrap and not outside it, which is
    why neither is there.

[ ] copy a string's bytes a register at a time rather than one at a time.  Joining two arrays is one instruction per register's
    worth, because the lengths are written down and the run-at-a-time machinery can cut them up; a string's are not, so the join
    is a byte loop.  What it wants is a run whose length is a value rather than a number, which is the same thing a loop over a
    dynamically sized array would want.

[ ] fill a shape from an array a run at a time where the counts are equal.  `⍴` with one value is a splat and one store,
    which the run machinery cuts up; with an array it is a store per element, each reading the source at a place known while
    compiling.  Where the two counts are equal that is a plain copy and is the same thing joining two arrays does, so it wants
    the same treatment; where they are not, it is a copy of one run repeated, which is a different question.

[ ] fold `⌈` and `⌊` over a whole run.  The written-out form -- a tuple's members, a fixed array's elements --
    is a chain of two-operand comparisons, one per element, where every one of these machines has a lane-at-a-time maximum
    (`pmaxub` and friends, `umax`, and the vector extension) that would do a register's worth at a time.  It wants the same
    treatment joining two arrays got: the elements are already a run whose length is written down, so what is missing is the
    entry in each target's `Vectors` table and a reduction over the lanes at the end.

[ ] raise a whole run at once, and fold a power of a value written down.  `v²` over an array is one element at a time, where
    each multiplication could be one instruction per register's worth; and `2u32³` reaches the code generator as two
    multiplications, where the same arithmetic written out is folded.

[ ] use what the RISC-V extensions offer beyond the two the code generator asks about today.  `b` brings Zbb, whose `min`,
    `max`, `minu` and `maxu` are one instruction where `⌈` and `⌊` are a comparison and a conditional move, and whose
    `rol` and `ror` are the rotations; `v` brings the vector extension, which is what `Vectors` is empty for on this target.
    Both are mandatory in the profile that is now the default, so the strings are already saying they are there.

[ ] know the older RISC-V profiles.  `rva20u64` and `rva22u64` are published sets like `rva23u64` and are each one row in the
    profile table; they are left out because what this compiler would learn from them is what `rv64gc` already says, and a row
    written from memory rather than from the document is a row that is wrong.

[ ] round a floating-point number at the oldest x86-64 level.  Refused today: `roundsd` is SSE4.1, which the second level
    promises and the first does not.  What the first needs is the round trip through an integer the RISC-V backend already does,
    plus a correction per direction -- `cvttsd2si` truncates towards zero, so the floor is one less where the answer came out
    above the value and the ceiling one more where it came out below.  The nearest and the current mode are `cvtsd2si`, which
    rounds by `MXCSR`, so the first of those needs the mode set and put back or a different sequence again.

[ ] fold the four roundings of a value written down.  `↓2.5f64` reaches the code generator as an instruction, where the
    arithmetic on two written-down floating-point values is folded away.  Three of the four are settled while compiling; the
    fourth is not, which is the whole of why it is impure.

[ ] round a whole run at once.  All three machines round a register of lanes in one instruction, and the operators are listable,
    so rounding an array is an element at a time where it could be one instruction per register's worth.  It wants a `unary`
    entry in each target's `Vectors` table, which today holds only the complement.

[ ] answer `⌈` and `⌊` over strings.  Strings are ordered now, so the largest of a list of them is a
    question with an answer, and it is refused (4506): the fold is one of four instructions chosen by the type, and a string's
    comparison is a call.  What it wants is the walk carrying which of the two it has seen rather than the larger of them, which
    is the same shape and a different body.

[ ] fold a comparison of two strings written down.  `"a" < "b"` reaches the code generator as a call, where the same comparison
    of two numbers is folded away.  Both sides are bytes the compiler put in the image, so there is nothing to wait for.

[ ] answer `⌈` and `⌊` for an array of more than one dimension whose type does not say its shape.  Refused
    today (8501): the answer is a row, and how long a row is, is not known until the program runs, so the room for it has to be
    taken while it runs and the walk has to be two loops rather than one written out.  It is the same piece of work walking such
    an array wants (4482).

[ ] leave out the comparison where one side of `⌈` is a constant at the end of the type.  `x ⌈ 0u8` is `x` and
    `x ⌊ 255u8` is `x`, and both are what a generator writes when the bound comes from somewhere else.  Neither folds
    today.

[ ] hold a tuple whose member is itself several values.  A tuple holding a tuple reaches the code generator and fails there
    ("no encoding of 'mov' accepts ..."), because what a tuple travels in is one register per part and a part that is itself
    several has nowhere to go.  It is what stops `⎕enumerate` counting the turns of a dictionary, whose turn is already a
    pair, and it is the same gap as the array below.

[ ] make the bill of materials the same in two directories.  A source's row names the path it was read from, so the same program
    built from two places gives two images.  What should be recorded instead -- a path relative to something, or only the name --
    is a question about what a consumer of the table wants to match on, and nothing consumes it yet.

[ ] leave the definitions that were dropped out of the bill of materials, or say in the table that they were dropped.  Every
    definition of every source read gets a row, including one the image did not need and the reachability pass removed, so the
    table names things the binary does not contain.  Which of the two a reader wants is the question.

[ ] carry the memory token out of an `elif` condition that writes.  A store in the condition of an `elif` that is neither the
    first arm nor the last, inside a nested `if`, leaves a token the join block reads without its being passed as a block
    argument -- "use of a value that does not dominate the use".  Written small:

        if n = 2u8: 1u6
        else:
            n ← 5u8
            if n = 3u8: 3u6
            elif n = 4u8: 4u6
            elif #[1u16, 2u16] ≠ 2: 5u6      ※ the list literal writes
            elif n = 6u8: 6u6
            else: 0u6

    It predates lambdas -- the same program fails at the commit before them -- and was found writing the lambda test, whose
    conditions happened to have the same shape.

[ ] narrow what the tree-sitter grammar admits where the compiler ends a statement.  An end of line is an extra there, which is
    what lets a line break inside brackets be read; it also admits one after a binary operator, after the `=` of a definition and
    after `fn`, which the compiler refuses.  Closing it means the scanner counting open brackets, which means the brackets
    becoming external tokens it lexes itself.  The direction is the safe one -- the grammar takes a few programs the compiler
    does not -- so this is fidelity and not a defect.

[x] let a record with exactly one field be a value.  Done on 2026-09-17: whether a value is several travelling as one is now
    asked of the type (`made_of_parts`) rather than counted, so a record of one field is made of parts and has one.  Found while
    flattening `parts_of`; it predated that.

[ ] render a global's initializer in the textual IR.  An array's and now a record's both come out as `undef`, so a module
    printed and read back loses what its globals started with.  Nothing depends on it today -- the round trip is a test of the
    form and the driver never reads a module back -- but a written IR that does not say what a program holds is a written IR that
    cannot be used for anything else.

[ ] narrow to an enumeration without a comparison per value.  What is emitted is one comparison per value folded with "or",
    which for the eighteen `std.Error` names is thirty-five instructions and about three milliseconds of compiling.  Sorting the
    values and testing ranges would be fewer of both, and is the compiler's to improve without any program changing.

[ ] work out a compile-time value in the width it has.  The evaluator computes in whole numbers: the checker has settled the
    types and the ranges of the literals, so an intermediate that would not have fitted in the type it is written in does not stop
    it, and a value that is finally used somewhere is checked where it is used.  Doing better wants the type of every expression,
    which sema knows and does not write down -- the same table the language server reads would answer it.

[ ] what the compile-time evaluator cannot do yet: a match, a lambda, a set, a dictionary, a `?`, a lifted type, taking a value
    apart into several names.  Each is named where it is refused (7000), so the list is `_DESCRIBED` in `comptime/evaluate.py` and
    adding one is that entry and the code for it.

[ ] refuse, or probe, a frame larger than the guard below the stack.  The guard is 64 KiB by default and a single frame wider
    than that steps clean over it: the fault is then past the guard, the handler does not recognize the address, and the program
    dies of the signal rather than reporting that its stack ran out.  Nothing in the language can ask for such a frame yet, there
    being no local arrays of that size, so this is a gap to close before there is.  The two answers are a check against the guard
    size at the point a frame is laid out, or a touch of each page as the frame is made, which is what a stack probe is.

[ ] build the entry point's small constants in the narrow half of a register.  Every number the stack is made with is put in a
    sixty-four bit register, which on x86-64 is a seven-byte instruction where writing the same number to the thirty-two bit view
    is five and zero-extends anyway.  About thirty bytes per program, and the reason it is written this way is that the registers
    come from a record the shared emitter reads and a narrower view of one is the architecture's own idea.

[ ] say what a program that ran out of stack was doing.  The handler writes one line and nothing else -- there is no unwinder,
    and the entry beside this one says why.  When there is one, the recursion that did not end is exactly what a reader needs to
    be shown, and it is the one case where the stack to walk is the one that just overflowed.

[ ] report a tuple holding an array.  `〈⟦1u8, 2u8⟧〉` reaches the code generator and fails there (9901, "making a 〈u8⟦2⟧〉, which
    is one value and not several"), so what a reader is told is an internal error about a program the front end accepted.  Either
    an array is a thing a tuple may hold, in which case `parts_of` has to say what its parts are, or it is not and the checker
    says so.  Found beside the crash above; nothing the language offers today needs it.


[ ] a unit conversion breaks what reads it.  With `unit ¤size → ¤idx` in scope, `let many: u64 ¤idx = #raw` compiles, and both
    of these are wrong afterwards: `foreach i := first…many…2` runs one turn where the same loop with `many = 4` written out runs
    two, and `⎕drop(many)` reaches the code generator and fails there (9901, "empty block").  Two symptoms of one thing -- the
    value a `¤size → ¤idx` conversion produces -- and both predate the environment work, which is where they were found.  A
    program that hits them is told an internal error or is silently miscompiled, which is the worse of the two.

[ ] an error inside a loop over a converted range is reported as an internal error.  Reduced from the same program: the
    diagnostic that should be reported (4441, a step that is not written down) is replaced by 9901, so the reader is told the
    compiler broke rather than what to fix.  Error recovery leaves the arm's block without a terminator.

Optimizations
-------------

[ ] Implement value range propagation.  Needs the arithmetic entry in TODO-language.md, which is what produces the checks this
    would remove.  The result is obviously usable in many situations, including:
    [ ] skip overflow/underflow checking of arithmetic operations
