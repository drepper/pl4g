PL4G (Programming Language for Generators)
======================================

The compiler predominently has to be fast while at the same time always perform all conformance checks and generating small code.
Well performing generated code is desired as well but is of secondary importance and can be tackled in later phases.

The compiler must use an intermediate representation and have the possibility for backends for different architectures. Initially
the target is x86-64, aarch64, and RISC-V 64bit but that will change. The output files are in the ELF format, specifically for
Linux.  The backends should use a symbolic version to generate the assembler code to allow, in future, inline assembler code.
The compiler structure is therefore quite tranditional: frontend → IR -> optimization -> assembler representation -> ELF generation.
No outside assembler or linker is used.

The compiler must work incrementally. It must be able to modify an existing ELF files with just the changed bits, leaving the rest
undisturbed. To faciliate this the compiler should allow for function growth by padding. It must also use hooks into the system
controlling the binary creation to enable changes to the binary while it is being used.

The compiler generates binaries which do not depend on anything from the system's default runtime by default. I.e., neither the
dynamic linker nor the C runtime or any other is used. Binaries are statically linked. Any common runtime needed for the language
is also developed as part of this project.

If any design decision made by the user contradicts what the specification calls for, explain the contradiction and ask the user
to resolve it.  Create a log file of every single decision made with appropriate timestamp and indication what type of decision this is:
language, implementation, etc.

The compiler must also be usable as a language server using the LSP protocol.  To improve the usability in editors, a
tree-sitter specification for the language is developed in parallel.  Whenever a language feature is finished, add LSP
and tree-sitter support.


Implementation
--------------

There will be two implementations of the compiler. The first is a bootstrap compiler written in Python. If this turns out to be
too slow the bootstrap compiler might be rewritten in C++. The bootstrap compiler is then used to compile the actual compiler.
Until the language is specified sufficiently the bootstrap compiler is the only compiler.  It does not have to be able to handle
the entire language as per the specification as long as it can compile the code of the actual compiler.

At some point the actual compiler is developed and the bootstrap compiler only has to be touched to implement features the actual
compiler's source code uses or to fix bugs.

The internal representation in the compiler does not have to account for different language frontend, PL4G is the only language.
The only exception is that interfaces (not implementations) of functions in other programming languages need to be represented so
that foreign function calls can be performed. To be able to utilize commonly available knowledge about compiler construction an
internal representation at least comparable to SSA (Single Static Assignment) is desirable. The backend and especially the
register allocator and code generator can be improved over time and at different speeds for the different architectures. A first,
not highly optimized implementation for all supported architectures must be the first goal. The end goal definitely is good
optimization capabilities and especially also the utilization of all available new CPU features and eventially the use of
accelerators like GPUs.

At least for the time being there is only whole-program compilation. There is no equivalent of object files. To speed up
compilation it might be necessary to preprocess source files and create a files on disk with data structures that can be quickly
read to construct the internal representation of the source code files.

Create and maintain a JSON file with the known and implemented error and warnings.  Assign unique numbers which are distributed
in blocks which keep errors/warnings with similar meaning and/or original together (numerically) while distributing unrelated
ones across the four digit number space.  Create a schema for the file.  Assign symbolic names which are derived from the location
of the violated requirement in the specification and add more complete descriptions of the (potential) sources of the problem.

The Python-based compiler should be implemented in a module name pypl4g, using `__main__.py` for the name of the file with the
startup function to allow using `python3 -m pypl4g` as the compiler command.  Create in a `bin` directory an appropriate script
with the name `pypl4g`.  The actual compiler binary will also be created in this directory with the name `pl4gc`.  The Python
implementation will accept the same parameters as the actual, full compiler.  Initially, the compiler will require a parameter
`-o` or `--output` with a mandatory argument to specify the output file name (just like existing compilers like gcc).  Additional
parameters are the names of the source files, ending with the extension `.pl4g`.

`--print-targets` writes the target triples the compiler can generate for, one per line.  A build system asks for that list rather
than carrying its own copy, so that nothing has to be edited when a target is added.  Only canonical triples are listed: the
abbreviations a triple may also be spelled with are accepted on the command line but not reported, so that building one binary per
line does not build the same binary several times.

Examples live in `examples`, one directory each, holding the program and a `Makefile`.  The shared rules in `examples/common.mk`
take the list of architectures from `--print-targets` and build into `build/<triple>/`, and `make run` runs each binary, through
the emulator for its architecture where that is not the host's.


The Intermediate Representation
-------------------------------

The representation is in static single assignment form.  Merge points are expressed with **block parameters** rather than with phi
instructions: a block declares typed parameters and every branch carries the arguments for its destination.

```
fn @absdiff(i32, i32) → i32 internal cconv(pl4g) {
block0(%0: i32, %1: i32):
  %2 = icmp.slt.i32 %0, %1
  condbr %2, block1(%1, %0), block1(%0, %1)
block1(%3: i32, %4: i32):
  %5 = sub.i32 %3, %4
  ret.i32 %5
}
```

Block parameters are the form of Swift's SIL, of MLIR and of Cranelift, and the form the standard construction algorithm produces
directly; phi instructions are the form of LLVM and of GCC's GIMPLE, and the one the literature is mostly written in.  Block
parameters were chosen because they remove three rules that exist only to keep phis working: that phis come first in a block, that
a critical edge must be split before a value can be placed on it, and the question of where a register allocator puts its parallel
copies -- the branch argument lists are that place.

Two properties of the representation are decided now because they are free now and expensive later.

**No type carries a layout.**  A type never holds a size, an alignment or a field offset, and a field is selected by index and
never by byte offset.  The specification lets the compiler reorder the fields of a product type for efficiency, and baking an
offset into the representation would throw that freedom away.  Layout is computed by a late pass and held beside the type.

**Memory ordering is explicit in the dataflow graph.**  A load takes a memory token and an address; a store takes a token, an
address and a value, and produces a new token.  Two operations whose tokens are unrelated are thereby *provably* independent,
which is what turns the requirement that no implicit dependency such as memory aliasing may force an order into something a pass
can check rather than merely assume.

Whether an instruction may be removed when nothing uses it is answered by the instruction rather than by the pass that asks: each
shape says whether it has an effect, so a shape added later cannot be overlooked, and the answer "none" has to be given
deliberately.  A write, a call and a terminator have one.  A read does not: every place the language can name is the program's own,
so a read nobody looks at is one nobody can tell happened, and a place where reading is itself an action would have to say so on
the instruction.

A function nothing can reach is left out of the image altogether.  Compilation covers the whole program, so what nothing in the
program reaches is what nothing can ever reach: such a function is not merely unused but unusable, and that is not a judgement that
waits for an optimization level to be asked for.  Reachability is computed forwards from roots rather than by asking of each
function whether it has a caller, since a caller that is itself unreachable is no caller.  The roots are the ways into the program
from outside it: the startup function and the constructors and destructors, which the entry point the compiler writes calls; the
tests, which the testing machinery will call once there is any; and whatever the image offers to the outside, which by definition
can be called from somewhere this compilation cannot see.  Being exported from a module is not enough: within one program, what a
module lends and nothing imports is something nothing reaches.  From a root it follows what each instruction says it names -- today only the callee of
a call, and tomorrow whatever new shape can hold a function.

Variables follow the functions.  A variable at the top level is reached when a function that is itself reached names it, so dropping
a function can be exactly what leaves a variable unreachable; the two are therefore settled in one pass and in that order, rather
than by two that would have to be run until they agreed.  A variable the image offers is a root of its own, for the reason such a
function is.  Being written counts as naming it even where nothing reads what was written -- a write is an effect that
outlives the function -- so a variable the program only writes is reported rather than quietly deleted, which is the next paragraph.

Whether anything reads a variable at the top level is a question about the whole program, since any function may name one.  It is
asked once every function has been checked, and it is asked of the same hook the reachability walk uses: each instruction says
which places it reads and which it writes, so neither the analysis nor the pass matches on instruction shapes, and a shape that can
name a variable in some new way says so in one place.  What the definition said it raises has to stay in force until then; the
expectation is therefore carried on the variable rather than settled where the definition was read, which is what a local already
does for the same reason.

There is a canonical textual form, written by `--emit=ir` and read back by the compiler's own reader.  Values are numbered by
position, so a module always prints the same text; printing, reading and printing again is a fixed point, and that property is
asserted for every stored example.  The form is a testing facility rather than a serialization format: a persistent form, if one
is ever wanted for the pre-digested source files mentioned above, should be a packed binary one instead.

Expressions are parsed by precedence climbing: one function for all the levels rather than one function per level.  Adding an
operator is a row in a table -- the token, what it means, how tightly it binds, which way it associates -- and nothing else, which
is what keeps the grammar from growing a layer every time the language gains a symbol.  The numbers in the table are spaced so that
a level can be put between two without renumbering.

The syntax tree names an *operator* and the representation names an *operation*, and they are separate enumerations with a mapping
between them in the semantic analysis.  That is not duplication for its own sake: the front end is deliberately free of any
knowledge of the representation -- a literal's type is carried as the text of its suffix for the same reason -- and several
spellings may come to mean one operation later.

Both sides of an operator have one type, so whichever side says what that type is says it for the whole expression.  A hint is read
off the syntax first -- a literal that names its type, a name already bound, either side of an operator -- and used as the context
both sides are checked against.  Without it a rule that only looked leftwards would accept `count & 3` and refuse `3 & count`,
which would be a difference with nothing behind it.

Modules
-------

A module is found, read and checked while the file importing it is being checked, by a checker of its own sharing the module being
built.  That is what makes the top-level namespace a file's rather than the compilation's: two files may each define a `counter`,
and the representation holds both, so what a definition is filed under says which file it came from while the name stays what the
source wrote.

A module's name is settled after all the reading rather than during it, because neither question can be answered earlier.  The
shortest of a module's names is not known until the last route to it has been found, and whether two modules share a base name is
not known until both have been read.  So the loading records every name a module could go by, and a pass at the end chooses.

The whole-program questions -- whether there is a startup function, and whether anything reads a variable -- are asked once, by the
outermost checker.  A module read first would answer both wrongly: the startup function is in a file not read yet, and a variable
it exports is read by one.

`--module-path` gives the directories a build wants searched, colon-separated.  A relative entry is tried against the importing
file's directory first and against where the compiler was run second.

The Symbolic Assembler
----------------------

The assembler has **no text syntax**.  The representation is built by calling functions in order, and what they build stays
internal:

```
asm.begin_function("main")
asm.label(name)
asm.loadreg(dst, src)             ※ src: a register, an immediate, memory, or a symbol
asm.op(PLUS, dst, a, b)           ※ destination first, three-address form
asm.call(target)
asm.ret()
asm.end_function()
```

Inline assembly, when it is added, will be a parser that drives these same calls.  That is what guarantees it can never express
something the encoder is unable to emit, and it is why choosing a notation -- Intel's or AT&T's -- is not a decision the language
has to make at all.

Operations are written in three-address form with the destination first.  An architecture whose instructions take two operands
lowers that itself, so the same calls serve x86-64, aarch64 and RISC-V.

The descriptor is split in two.  What an instruction *is* -- its name, the
operands it accepts, the registers it touches besides those operands -- is shared;
how it is spelled in memory is not.  x86-64 describes an encoding as a byte stream
with prefixes, an opcode map and a ModRM byte.  A fixed-width architecture
describes it as one 32-bit template plus the bits each operand occupies, and that
second shape is itself shared, because more than one architecture has it: AArch64
and RISC-V use the same encoder, differing only in their tables and in their
relocations.  Each target extends the shared descriptor with the fields its own
encoder reads, and nothing outside that encoder looks at them.

Three parts of the interface are built to be extended:

- **Operations** are values, not members of a closed enumeration.  The architecture-neutral ones are defined once; a target
  contributes its own the same way (`x86.syscall`), and the builder accepts either.  Adding an operation is a value plus a row in
  the encoding table.
- **Registers** use a unit and view split.  A *unit* is physical storage; a *register* is a view onto a unit at a given width and
  byte offset.  That one mechanism describes `al`/`ah`/`ax`/`eax`/`rax` and `xmm0`/`ymm0`/`zmm0` alike, and it makes the rule for
  whether two registers interfere -- same unit, overlapping byte range -- correct for both.  Register classes are a registry:
  general-purpose, vector, mask, flags and segment registers are entries in it, and a new kind is another entry.  The
  general-purpose class holds thirty-two units from the start, so the extended registers are already addressable and only the
  prefix the encoder emits has to be added.  Virtual registers are in the operand model from the beginning; the encoder refuses to
  encode one, and that refusal is the contract the register allocator satisfies.  A register operand may also state a width that is
  not the register's own, which is how a byte store names the byte view of a register whose identity is not yet known.
- **Encodings** are declarative table rows plus one generic emitter that walks a fixed sequence of phases: legacy prefixes, the
  prefix carrying the register extensions, the opcode map escape and the opcode, the ModRM byte with its SIB byte and
  displacement, and finally the immediate or the branch displacement.  A new prefix family is one more emitter in the second phase;
  every other phase is unchanged, because the fields those encodings need -- the opcode map number and the mandatory prefix -- are
  already the fields a legacy encoding uses.

Where several rows accept the same operands, the assembler selects the **shortest encoding**.  That follows from the requirement
to generate small code, and it means nothing that builds an instruction has to know that one form of an instruction is shorter
than another.

Emission never waits for a symbol to be defined.  A reference to an undefined symbol writes placeholder bytes and records a fixup;
offsets are assigned when the section is laid out, instructions whose encoding can grow are re-encoded until nothing changes, and
only then are the fixups patched.  The rule for turning a symbol's address into the number to store is stated in one place, and the
encoder never computes a displacement itself.

*Storing* that number is a separate question, and one only the target can answer.  A displacement on x86-64 occupies a field of its
own and is simply overwritten.  A branch offset on AArch64 shares its word with the opcode and the destination register, is measured
in units of four bytes, and in one case is split across two runs of bits.  A jump offset on RISC-V is scattered across four separate
runs of bits of its word, with the sign bit at the top and the rest out of order.  A fixup kind therefore says how its value is computed --
absolutely, from the end of the field, or from the start of it -- and each target says how its own kinds are stored.  Kinds are
values rather than members of one enumeration, for the same reason operations are: a target registers the ones it needs.

An operand a table row cannot carry is put in a register, and which operands a row can carry is asked of the table rather than
written down twice.  x86-64 has a form of `and` that takes an immediate and AArch64's equivalent needs the bitmask encoding, which
is not generated; the selector tries the operands as they are, and materializes what was refused.  Adding a row that carries an
immediate is then all it takes for one to be used.

A branch is selected together with the comparison that feeds it, in one call, because that is the shape all three architectures
have: one puts the comparison inside the branch, and the other two set flags in the instruction immediately before it.  Handing a
selector the two separately would mean it had to remember the first to encode the second, and on the architecture with no condition
codes there would be nothing to remember.  A condition arriving as a value rather than as a comparison -- a variable holding a
truth value, one day the result of a call -- is branched on by testing it against zero, which two of the three have an instruction
for that needs no flags at all.

A comparison whose answer is wanted as a *value* rather than as a place to go is the other half of that question, and it is
selected in one call for the same reason.  The three architectures diverge further here than they do on branches.  x86-64 reads
its flags into a byte with `setcc`, which leaves the rest of the register as it was, so the byte is widened into the register
afterwards -- clearing the register first instead is the usual trick and is not open here, because clearing it writes the flags the
comparison has just set.  AArch64 reads them into a word with `cset`, which clears the rest of the register itself, so two
instructions do.  RISC-V has no flags: it has one comparison, "set if less than", in a signed and an unsigned form, and the other
six orderings are that one with the operands exchanged, its answer inverted with an exclusive or, or both.  Equality has no
ordering in it at all and is a subtraction followed by a question about the difference.

**A truth value is one or zero.**  That is not a representation chosen for the language; it is what every instruction on all three
architectures that produces one produces, and what the byte in the image already holds.  It is a byte in memory, which is what the
layout says of `bool`, so it is read and written a byte at a time -- reading or writing one any wider would reach past it into
whatever is laid out beside it.  In a register it occupies a whole one, like every other value narrower than a word.

A comparison is folded into a branch only where the branch is its *sole* reader and reads it as its condition.  Read once by
anything else, or read twice, it is a value that something wants, and a value something wants has to be in a register.  Two
branches cannot each absorb one comparison, so that case computes it once and both branches test the result against zero.

**A fault is reported by writing a string and trapping, and nothing else.**  The
compiler knows which operation could not be done, in which function and at which
line, so the whole message is built while the program is compiled and put in the
image beside its constants.  What runs at the moment of the fault is a raw
`write` and a trap: no formatting, no number to turn into text, no allocation,
no second thing that could fail.  That is worth more here than anywhere else in
the compiler, this being the code that runs when something has already gone
wrong.

Standard error is where it goes, that being the only place a program depending
on nothing from the system can write to, and whether the descriptor is open is
not asked -- a program started with it closed has the message go wherever that
number now points, which is still better than nowhere.  The trap is the same
signal on every target, and is told apart in a disassembly from the padding
between functions, which on one target is otherwise the same instruction.
Nothing of this is emitted for a program in which nothing can fault.

**A value narrower than a register carries its own zeroes or its own sign above
itself.**  Every integer value of width *w* is held in a register with the bits
above *w* equal to the zero- or sign-extension of the value, according to the
type's signedness.  That is the invariant everything else rests on: a
comparison, a store and an arithmetic operation each read the whole register, so
a value whose upper bits say something other than what the type says is a value
one of them will read wrongly.

Nearly everything maintains it without being asked.  A load says in its own
instruction whether it widens by zero or by sign.  A constant is built as the
whole pattern.  A comparison answers with one or zero.  `and`, `or` and
`exclusive or` of two values that satisfy it satisfy it again, the bits above
the width being then the same bit on both sides.  The complement of an
*unsigned* value does not, which is the one place the bits have to be put back:
`~15` as a `u8` is 240, and a register holding the complement of 15 holds
neither 240 nor anything that compares equal to it.

Arithmetic answers the question differently.  An operation that saturates or
that faults on overflow never produces a value outside its type, so computing it
at the full width of the register and clamping leaves the invariant holding with
nothing further to do -- which is also why such an operation can be computed
*exactly* for any type narrower than a register, and needs a rule of its own only
at the width of the register itself.

**A constant wider than an instruction can carry is built rather than loaded.**  No constant pool is emitted and none is planned:
a pool costs a relocation, a cache line and a section, where a sequence costs two to four instructions that no other value has to
wait for.  AArch64 sets a quarter of a word at a time, and turns every bit round first where the value has more quarters of ones
than of zeroes, which makes a small negative number two instructions rather than four.  RISC-V builds the upper part the same way,
shifts it as far left as its own trailing zeroes allow so that a power of two costs two instructions, and adds the last twelve
bits.  x86-64 has a move that takes the whole eight bytes, so only the places whose immediate is narrower -- a store and a
comparison -- have to put the value in a register first.

An immediate narrower than the operation is sign-extended, which is what decides whether one may be used at all: `0xFFFFFFFF` in an
eight-byte operation is not four bytes of immediate, it is minus one.  At the operation's own width nothing is extended and any
pattern will do, which is what lets a one-byte store carry two hundred.

**A block parameter is a value like any other, and lives in a register.**  Every parameter of every block is given one before any
block is walked, since a branch writes the parameters of a block that may come later in the layout than the branch does; what a
branch carries is then the instruction to put something there, emitted immediately before the jump.  The register allocator's hint
usually makes the move disappear, by giving the parameter and the value that reaches it the same register.

That works because only an *unconditional* branch may carry arguments.  An edge of a conditional branch has nowhere to put the
moves -- they belong on that edge and not before the test -- so splitting the edge is what carrying arguments there would need.
Nothing generates that shape: the front end's short-circuit lowering sends the conditional branch to two blocks that carry nothing
and lets each of them hand the answer over with a branch of its own, and a loop's test likewise carries nothing on either edge.  A
conditional branch with arguments is refused rather than got wrong.  What is counted is the *values* it carries and not its
arguments: an edge carrying only a memory token costs no instruction and needs no block, since a memory token is nowhere.

The moves a branch does carry are a *parallel* copy and not a sequence of moves.  A branch handing a block its own parameters back
rearranged -- which is what a loop carrying two values does on every turn -- would have the second move read a register the first
had already written.  So every move is built before any is emitted, and then they are put in an order in which none reads what
another has written: a move is ready when nothing still to be made reads the register it writes, and where nothing is ready every
move left is in a cycle, so one register is copied into a spare and the move reading it is pointed at the spare instead.  The spare
is a fresh virtual register, which is always available because this runs before anything has been given a physical one, and which
is why no architecture needs an instruction that exchanges two registers.

The hazard is between registers and not between the values of the representation.  Taking one value out of another leaves both in
one register, so a branch can read a parameter's register without any parameter appearing among its arguments; asking the registers
is what makes that case come out right without anything having to know about it.

Which way round a branch is written is decided where the order of the blocks is known, and not by a backend.  A two-way branch is a
conditional branch and a jump; the jump is not needed when the block it would go to is the next one in the image, so the condition
is inverted when that is what makes it so.  For a branch whose two blocks both follow it -- which is every branch a conditional
expression produces -- that costs one instruction rather than two.

Instruction selection produces a machine-level function -- basic blocks holding instructions, with virtual registers allowed --
rather than a finished stream of bytes.  That is where the register allocator, the peephole passes and the scheduler run.  A
peephole rewrite states the condition under which it is valid and checks it: replacing a register-clearing move by an exclusive-or
is three bytes shorter but writes the flags, so the rewrite fires only where a liveness scan has shown the flags to be dead.

The Decision Log
----------------

A diagnostic says something is wrong.  A *decision* says the compiler chose something the program did not state -- above all, what
it left out.  The two are kept apart deliberately: nothing is wrong when a function nothing can reach is dropped, and putting
"this function is not in your binary" among the warnings would make it either noise or something missed.  A language meant to be
generated will have plenty of them, since a generator emitting from a template routinely produces more than one instantiation uses.

`--decision-log=FILE` writes them as JSON beside the binary.  Each entry has a `kind`, which is the stable part that something
reading the log matches on; a `subject`, named as the program names it; a `reason` in prose, which is for a person and may be
reworded; and, where the subject is written in a source file, a `where` giving the file, line and column -- being told that a
function went without being told which one would leave the reader to find it.

What is recorded today is what is left out: `drop-function` and `drop-variable` from the reachability pass, and `drop-local` from
the dead-code sweep.  The first two happen at every optimization level, because a function nothing can reach is code the program
cannot run; the third happens from `-O1`, because an unoptimized build keeps what the program wrote and so decides nothing about
it.  Recording happens whether or not the log was asked for, because a decision recorded only when someone is watching is one a
test cannot check.

A dropped local is named by the name the program gave it.  That is what the name hint on a value is for: the semantic analysis
writes the name of a local on the instruction its definition produced, so that the textual form can be read against the source and
a pass that removes a value can say which local went with it.  Only an instruction is named and only if it has none already -- a
constant is interned and shared with every other use of the same number, and where two locals stand for one value the first name
is the one that stays.  A value with no name is an intermediate of an expression, not something the program can ask about, and its
going is not recorded.

A local bound to a constant is not recorded either, because nothing drops it: a local is a value, so one whose initializer is a
constant never becomes an instruction at all.  Saying where such a local went is the business of the debug information, which is
where a constant expression standing for a name belongs.

The warning that nothing reads a value (4006) and the record that it is therefore not in the binary are different facts and both
are kept.  One is a possible mistake and is reported at every optimization level; the other is what became of it and only happens
where something was optimized.  They also come apart: a local that *is* read can still go, when the thing that read it went.

The log travels on the module, since every stage has the module and any of them may decide something.  It is not the design log in
[decisions.md](decisions.md), which records what was decided about the *language* and is written by hand; this one is about one
program and is written by the compiler.

Reading the Log
---------------

`bin/pl4g-decisions` shows a program's source with each record of the log standing just above the line it is about -- above rather
than below, so that the line is read already knowing what became of it instead of being read, understood, and then corrected.  A list of names
and line numbers is not something anyone reads; the question a decision answers is "I wrote that, where did it go?", and it is
answered by looking at the place it was written.  A pattern on the command line chooses which source files to show.

Where the output is a terminal the source is highlighted, and where it is a pipe it is not, unless `--color=always` says otherwise.
The highlighting is done with the tree-sitter grammar in `tree-sitter-pl4g` and its own highlight queries, so what this colours and
what an editor colours are the same thing and cannot drift apart.  Everything the highlighting needs may be absent on a machine
that only wants to read a log, and none of it being there is not an error: the source is shown without colour, which is what a pipe
gets anyway.

The Grammar
-----------

`tree-sitter-pl4g` is a grammar for the language in the form editors and highlighters read.  It is a second statement of what a
program is, and two statements of one thing can disagree, so a test requires the grammar and the compiler to agree on whether each
program in the language test suite parses -- in both directions, since a grammar that is too loose is as wrong as one that is too
strict and shows up as an editor offering what cannot be written.

A change to the syntax changes the grammar in the same commit.  Nothing could make that automatic; it is a rule, and the test is
what notices when it was not followed.

The layout rules cannot be expressed in a context-free grammar, so the tokens that stand for them come from an external scanner,
which is the arrangement tree-sitter's Python grammar uses for the same reason.  The generated parser is committed, so that reading
the grammar needs no tools, and a test checks that it is the one the grammar produces.

Timing
------

The compiler must be fast, so how long it takes is measured rather than assumed.  `bin/pl4g-timing` compiles a set of sample
programs drawn from the language tests and appends a row to [the timing tables](timings.md), one row per commit and one column per
program.  That way round because the commits go on for ever and the programs do not: a column for every commit would be unreadable
within a month.  A sample is added when a feature lands that could plausibly cost time and is never removed, so a row recorded
earlier stays meaningful, and a sample that did not exist yet simply has no figure.

The programs are split into groups, each its own table, so that no one table is wide.  They are grouped by *what would move them*
rather than by what they look like, because a group is useful exactly when a change to one part of the compiler moves its numbers
together: the smallest programs, variables and memory, register pressure, what is left out, and expressions.

Two numbers are kept.  *Work* is the sum of the compiler's own stages, which is what a change to the compiler moves, and is what
the group tables show.  *Process* is the whole run, which is what someone waiting for the compiler waits for; for the bootstrap
compiler it is dominated by starting the interpreter and importing the package, so what one sample costs and what the next costs is
noise, and only the range across all of them is tabulated.  Every figure is kept in the JSON beside the document.  The best of five
runs is recorded rather than the mean, because the best is the one least disturbed by whatever else the machine was doing.

The figures are from one machine and are worth comparing with each other and with nobody else's.

The Generated Image
-------------------

The image is a statically linked `ET_EXEC` executable at a fixed address, with no interpreter, no dynamic section and nothing from
the system's runtime.  Its program headers are a `PT_GNU_STACK` without the executable bit, whose absence would give the program an
executable stack, and one loadable segment for each set of permissions the program actually needs: read-only, read and execute, and
read and write.  A segment carries one set of permissions for everything mapped through it, so what needs different permissions
cannot share one, and no two of them may share a page either -- which permissions a shared page ended up with would depend on the
order the segments were mapped in.  A group with nothing in it gets no segment, since an empty one would still cost a page.

The load address is the same on every target so far, but the alignment of the loadable segment is not: it is the largest page size
a kernel for that architecture may be configured with, so that one image loads whatever the running kernel chose.  That is 4 KiB on
x86-64 and RISC-V, and 64 KiB on AArch64.  Padding is filled with a byte that traps rather than falls through, which is also
target-specific: a breakpoint on x86-64, and on both fixed-width architectures a zero word, which neither of them leaves defined.

A symbol carries a binding and a visibility, and what a program marks visible decides both.  What is marked visible is bound globally and
left visible; what is not is bound locally and marked hidden.  Being exported from a module is a different question and does not
reach the symbol table at all.  Within a single linked image the binding alone would do, since a local
symbol cannot be named from outside it -- the visibility is what still says so if the symbol is ever made global by something
later, and it is what a relocatable or shared object would need.  The entry point is the compiler's own rather than something the
program declared, and stays visible: every tool that inspects a binary expects to find it.

The section headers and the symbol table are kept.  They are what lets a disassembler and a debugger show the generated code, and
the disassembler is the independent check on the encoder.  More importantly, a symbol table with accurate addresses and sizes *is*
the map of where every function lives -- which is exactly what incremental recompilation needs, readable back out of the
compiler's own previous output without a file beside it.

**Every image the tests produce is checked against the format by `eu-elflint --strict`.**  No assembler and no linker stands
between the compiler and the file, so nothing else would notice a field filled in wrongly: a header that disagrees with itself
produces a file the kernel may still load and a tool may still read, and the mistake surfaces much later and somewhere else.  The
strict form also reports what common practice allows but the standard does not, which is the level to hold a compiler to when it
writes the bytes itself.

The check runs on every binary any test builds, not on a chosen few, so it holds for whatever is added later.  A gate that cannot
fail guards nothing, so one test damages an image deliberately and requires the check to reject it.

The writer plans and then materializes.  Planning computes every offset, address and size without emitting a byte; materializing
allocates one buffer of the final size and writes each piece at the offset the plan recorded.  Nothing is appended and nothing is
back-patched.  That is what makes the layout reusable for an incremental rebuild, and it makes "write only the pages that changed"
a matter of comparing two buffers.  Each function is a piece of its own with a field for reserved growth slack, which is the
padding that lets a rebuilt function be patched in place.

A position-independent executable is one flag away and is deliberately not implemented yet: it needs the type field changed, a load
bias of zero, a dynamic section, a relocation list and a self-relocation prologue.  One rule is adopted now to keep it reachable:
**the backend materializes the address of a symbol only through one helper, which emits a program-counter-relative computation.**
Nothing anywhere may put a symbol into an immediate.

Diagnostics
-----------

`share/diagnostics.json` is the catalog, with `share/diagnostics.schema.json` as its schema.  The numbers are four digits;
`0000`-`0999` is left unused so that every number prints as four digits.  The space is divided into wide, sparsely filled blocks, so
that diagnostics of one family stay numerically adjacent as they grow while unrelated families stay far apart:

| Range | Family |
|---|---|
| 1000-1099 | driver and command line |
| 1100-1199 | input and output files |
| 2000-2099 | lexical structure |
| 2100-2199 | layout and block style |
| 3000-3199 | syntax |
| 3200-3299 | attributes |
| 4000-4199 | names and modules |
| 4200-4399 | types |
| 4400-4599 | special functions |
| 5000-5299 | control flow and returns |
| 6000-6999 | purity, effects, aliasing and parallelization |
| 7000-7499 | compile-time evaluation and reflection |
| 7500-7999 | testing |
| 8000-8499 | intermediate representation and optimization |
| 8500-8999 | code generation and inline assembly |
| 9000-9499 | image generation and incremental compilation |
| 9900-9999 | internal compiler errors |

The gaps between the blocks are wide on purpose, and a number that lands in one is refused rather than written out: the generated
identifier module groups the names under the heading of the block they are in, so a number belonging to no block would be printed
under whatever heading came before it and would read as a member of a family it is not in.  The generator says so instead.  A test
checks that the table above and the catalog agree, since two places stating the same thing are two places to change.

A number is the stable identity of a diagnostic and never changes -- not even when a warning is later promoted to an error, since
a program may be reacting to that number.  The symbolic name is derived from the heading chain of the section that states the
violated requirement, prefixed `LANG_` for the language specification and `IMPL_` for this document; each entry quotes that
requirement verbatim, so that drift between the specification and the implementation is visible.  Each entry also carries the
longer description of the likely causes.

Severity, message text and controllability come from the catalog and never from the place that reports the diagnostic, which is
what keeps two implementations from drifting apart.  `--diag-format=json` writes the same content as objects, which is the
substrate for the requirement that a program can handle the errors and warnings the compiler emits.


Targets
-------

Three architectures have backends: x86-64, AArch64 and RISC-V (64-bit).

The backend for an architecture is `pypl4g/target/<arch>/`, holding its register
file, its operations, its encoding table, its encoder, its relocations, its
calling conventions, its instruction selection and its entry point.  Nothing
outside that directory knows about the architecture, and `--print-targets` is how
anything else -- a build system, the examples -- learns which ones exist.

What the two backends do differently is worth stating, because it is what the
shared layers had to be able to express:

| | x86-64 | AArch64 | RISC-V 64 |
|---|---|---|---|
| Instruction length | one to fifteen bytes | always four | always four, with the compressed forms unused |
| Encoding described as | prefixes, opcode map, opcode, ModRM, SIB, immediate | a template plus the bits each operand occupies | the same |
| Choosing between rows | by encoded size; several forms of one instruction differ in length | by table order; every row is the same size | the same |
| Immediate matching | by the declared width, which is what selects the shorter form | by whether the value fits, since there is no shorter form | the same |
| Relocation storage | overwrite a field of its own | insert into a word, split in two for an address | insert into a word, scattered over four runs of bits |
| Three-address operations | lowered to two operands with a move | native | native |
| Narrow values | a narrower view of the same register | a narrower view of the same register | a full register, sign extended, with a different instruction |
| Condition flags | a register, written as a side effect | a register, written as a side effect | none; a comparison and its branch are one instruction |
| Register naming | one name each | one name each | a number and a role name, both the same register |
| The zero register | none | shares its number with the stack pointer | a register of its own |
| Exit system call | number 231 in `eax`, arguments from `edi` | number 94 in `x8`, arguments from `x0` | number 94 in `a7`, arguments from `a0` |
| Padding | `int3` | a zero word | a zero word |
| Segment alignment | 4 KiB | 64 KiB | 4 KiB |


Variables
---------

A variable inside a function is a *value*, not a place.  The name is bound to whatever its initializer produced and nothing is
reserved in memory, because nothing can take its address; when the language lets a name be assigned, a block parameter is what
carries the new value across a branch, which is why the representation has them and why a local will not need memory then either.

Because a local is a value, a local nothing refers to is an instruction nothing uses, and an optimized build keeps nothing of it:
the ordinary dead-code sweep removes it, together with whatever it alone used.  Asking the question that way -- about uses rather
than about variables -- is what makes the rule still hold once the language can keep a reference to a local, since a reference will
be a use like any other.  An unoptimized build keeps it, so that stepping through one matches the source; the warning that nothing
reads it is issued either way, long before any pass runs.

A variable at the top level is a value of *pointer* type: naming one yields its address, and reading it is a load.  That is what
keeps every access to memory visible in the dataflow graph instead of implied by a name.  Every load takes a memory token and every
store produces one, so the chain has to start somewhere; a `mem.start` instruction is where.  It is an instruction rather than a
parameter of the function, so that the function's type says nothing about memory.

How much room a value takes and where it must start is computed from a type, never held in one: a size stored in a type would throw
away the freedom the specification gives the compiler to reorder the fields of a product type.  It is computed against a target,
because the width of a pointer is the target's business.

Where a variable goes follows from its type.  One defined `mut` can be assigned to, so it goes in `.data`, which is mapped writable.
One that is not cannot change while the program runs, so it goes in `.rodata`, which is mapped neither writable nor executable: the
guarantee then holds against the program rather than only against the type checker, and a write that got past the front end faults
instead of taking effect.  The two need different permissions, so they are in different sections and in different segments.

Reading a variable is one instruction on x86-64, which can name a place in memory relative to the program counter and widen a
narrow value as it reads it.  On the two fixed-width architectures no instruction can name an address outright, so one is built in
two steps and then read through.  The two differ in how: one computes the page the address lies in and then adds the offset within
it, and the other adds an upper and a lower half, with the second instruction measuring from the first rather than from itself.

Whether a place may be written is part of the pointer's type: the address of a variable defined with `mut` is a `ptr<mut T>` and
the address of one without is a `ptr<T>`.  The verifier asks the pointer rather than the variable, so the rule holds for any place
a pointer can reach and not only for a name the source wrote down.  A local has no pointer and no place; its mutability is a
property of the binding and appears nowhere in the representation.

A place is not always a name.  A load or a store whose address is a variable names its symbol and reaches it relative to the
instruction, as above; one whose address is anything else takes it from a register, and what place it names is settled while the
program runs rather than while it is compiled.  Both are the same two instructions in the representation, and which of the two
forms a backend emits follows from what the address operand *is*, so nothing above the backend has to know about the difference.

Three things make an address that is not a name.  `address` puts a variable's own address in a register, which is what a place
computed from it starts out as; adding a number of bytes to an address moves it, and is deliberately not the checked addition the
same operator means on two numbers, because what a number overflows into is another number and what an address past its place
names is not a place at all; and `bitcast` reads the same bits as a pointer to something else, which is how storage handed back as
untyped bytes becomes a place of a type.  There is no instruction that reserves storage: what a program allocates comes from an
allocator, and an allocator answers with an address like any other.


A parameter is a local like any other, and `mut` on one says what it says on any other: the body may bind the name to something
else.  It costs nothing at all -- a parameter arrives in a register, a name bound to a new value is a register, and the allocator
gives the two the same one wherever their lives allow -- and it is no part of the function's type, so it changes neither the symbol
nor any caller.  What a parameter's `mut` is *not* is a pointer's: a `ptr<mut T>` says something about a place both sides reach, and
a parameter's says something about a name, which is not shared.

Assigning to a local writes nothing: a local is a value, so the name is bound to a new one and the function that results is the
same as if the final value had been written in the first place.  Where control flow arrives, a block parameter will carry the new
value across a branch, which is what block parameters were chosen for; a local will not need memory even then.

Assigning to a variable at the top level is a store, which takes the memory token and produces a new one, so a read that follows it
is ordered after it and a read that does not provably is not.

An assignment stands for the variable it changed, so where its result is wanted a load follows the store and takes the new token --
which is what makes what comes back be what was written, by the ordinary rule rather than by a special one.  Where the result is
not wanted no load is emitted, since reading a place nothing looks at would be an instruction the program never asked for.  For a
local there is nothing to read back: the result is the value the name was just bound to.

A store needs two things at once -- the address and the value -- which the one register the compiler has is not enough for.  Each
fixed-width backend therefore sets aside two registers for it, chosen from the ones the calling convention leaves to the caller to
preserve, since nothing of the compiler's holds a value across the few instructions a store takes.  x86-64 needs neither: it writes
to a place in memory directly, and takes the value as an immediate where there is one.

The width an immediate is *encoded* at is not the width of the access it belongs to.  An eight-byte store on x86-64 carries a
four-byte immediate that the instruction widens, so what an operand states is how large the number is, and the row that accepts it
states how large a number that form can carry.  Where a constant is larger than one instruction can carry -- twelve signed bits on
one architecture, sixteen on another -- it is reported rather than assembled from a sequence, which this compiler does not generate
yet.

Nothing narrows a value without saying so.  The semantic analysis refuses every one a program can write that does not fit its
type, and the three places further down that turn a value into bytes -- the initial contents of a variable, an immediate in an
instruction, and a patched displacement -- refuse one too rather than storing it with its upper bits dropped.  Those cannot be
reached from a program that compiled, so reaching one reports a defect in the compiler; a wrapped value would be the same quiet
reinterpretation there as anywhere, and it would be harder to notice.

Instruction selection names each value it computes with a register of its own and says nothing about where that register is; the
allocator decides.  The method is linear scan: every instruction is given a position, each register gets the range between the
first position that writes it and the last that reads it, and the ranges are walked in order of their start, a unit being held for
as long as a range needs one and released as soon as it ends.  That is much less than a colouring allocator does and it is the
right amount for straight-line code, which is all the language can express -- with no branches there is no interference graph, only
an interval on a line.  The entry that adds a branch backwards has to replace the liveness with an analysis over the control-flow
graph, and the pass says so where it computes the ranges.

One instruction is two positions, a read and then a write.  That is not a detail: it is what lets a value be moved into the
register it is read from, so that a value hinted towards the register a result is returned in gets that register, the move becomes
one of a register to itself, and it goes.  Removing such a move is safe even where the instruction widens what it writes, because
on the architectures that widen, a narrow write has already cleared the rest of the unit and the value being moved got there by
such a write.

What an instruction does with each operand is stated by the table row, since only the allocator asks and the answer is a property
of the instruction.  A row that says nothing writes its first operand and reads the rest, which is the convention the builder
already speaks in; a store and a comparison write none, and the two-operand arithmetic of x86-64 reads the operand it writes.  A
register inside a memory operand is read wherever the operand stands, because computing an address reads it whatever the
instruction then does with the place it names.

Which registers may be given out is stated by the calling convention rather than worked out by the allocator, since that is what a
convention is about.  The order puts the registers a call would destroy first: using one of those costs a leaf function nothing,
while using a callee-saved one would cost it a save and a restore.  The stack and frame pointers are left out, and so is the
register holding a return address where there is one, because a frame and the unwinder that will walk it need somewhere to stand.

Which positions a register is live at is settled over the control-flow graph and not read off the layout: what is live leaving a
block is what is live entering any block it reaches, and what is live entering it is what it reads before writing together with
what it does not write and something after it wants, iterated until it stops changing.  A range is then the hull of the positions
at which the register is live.

That distinction does nothing while control only falls through, and it is the whole of what makes a loop safe.  The span between
the first write and the last read *in layout order* stops being a superset of the live set as soon as control can come back: a
value computed before a loop and last read in the middle of its body would have its range end there, and everything the body
computes after that point would be free to take its register -- which the next turn then reads instead of the value.  A hull of
live points contains every live point whichever way control reaches it, so correctness asks nothing of the block order.  Tightness
does ask something: where a loop's blocks lie next to each other the hull is the span of the loop and nothing outside it pays, and
where they do not the code is still right and merely spills more.

Where there are not enough registers a value is spilled: given a slot in the function's frame, written there when it is computed
and read back before each use.  The value is in memory for its whole life, and what occupies a register is a fresh one that lives
for the single instruction reading or writing it.  Splitting a range so that a value is in a register where it is busy and in
memory where it is not would generate better code and is a great deal more machinery; this is the version that is obviously right.

A value read again by the instruction straight after the one that read it is not read from the frame twice: the register it is
already in serves both, and the register is held across exactly as many instructions as are reading it and no further.  The same
rule covers a value read straight after it was computed, which is used from the register it was written from rather than read back
at once.  That is as far as splitting a range goes here; keeping a value in a register across instructions that are *not* reading
it would need a cost model, since the register held is one another value cannot have, and there is nothing yet to base one on.

Spilling is done by rewriting and starting again rather than by patching the assignment as it goes.  A spill adds instructions,
which moves every position after it and so changes every range, and recomputing is simpler than repairing.  The value given up is
the one whose range reaches furthest, since that is the one that would hold a register longest.

Only the allocator puts anything on the stack, so the frame is made after it has run and only where it took a slot: a function that
needed none has no frame and no instruction saying so.  The room is given back before every return rather than at one place,
because there is no one place -- a function may leave from more than one.  That is right exactly while nothing branches to the
first block, and a back edge to it would make the stack grow by a frame a turn without anything saying so, which is why it is
refused where the graph is already in hand.  The runtime is the one thing that asks for a frame outright, having no allocator to
ask for a slot.

Where control goes is recorded as the blocks are built and not derived from the instructions afterwards, because a fall-through
edge has no instruction to derive it from.  One invariant holds it together and everything that walks the graph rests on it: a
block whose last instruction neither leaves the function nor is a barrier reaches the block laid out after it by going on, and so
has to name it.  It is checked on every function, which is what turns a missing edge into a compiler error rather than into a
program that runs wrongly.  A successor naming no block of the function is a jump that stands in for a call and is left out of the
graph; control does not come back from one.

A register may say it must not be spilled, and one does: on RISC-V an address is built by two instructions of which the second
measures from the first, so they have to stay next to each other and the register held between them cannot go to the frame.  It is
given a register of its own rather than the destination's, which also shortens the life of the value being loaded; on AArch64 the
two halves are independent and no such rule is needed.


Arrays
------

**What a value of an array type is, is where its elements are.**  A type that says its shape carries everything about the array but
the elements themselves, so there is nothing else for a value of one to hold; the backend treats it as an address, and the
representation says so by letting such a type stand where a pointer does in a `bitcast`.

**A type that does not say its shape is a place and one count per dimension.**  That is the same machinery a result and a tuple
already use -- `parts_of` answers with several types, and everything that places a value asks `parts_of` rather than knowing the
shapes -- so it needed no new mechanism, only the generalization of two rules in the verifier from "a tuple" to "anything of
several parts".  A vector of no stated length is two parts, a table three, and so on.

**The shape is a tuple of dimensions, and the elements are one run.**  Row-major, which is what makes a row of a table a run and
puts the index arithmetic in the ordinary Horner shape: each index is added on after what is already there has been multiplied by
the dimension it steps through.  For a vector that comes to the index itself, so nothing is paid for the generality.

Every dimension says how many or none does.  Mixing them would make the parts of a value depend on which dimensions were told,
which is a second kind of array; the diagnostic says so rather than the compiler inventing a rule.

**Where the elements are depends on the array.**  A variable at the top level holds them itself.  One inside a function holds them
in the function's frame, which is what the `frame` instruction reserves: it answers with where the room is, and the room lasts
exactly as long as the call.

That reverses a decision: the earlier `alloca` was deleted with the note that storage whose address is taken comes from an arena.
That is right for a collection, which outlives the statement that built it and whose lifetime the program manages; it is wrong for
an array whose length is in its type, which lasts exactly as long as the name does and which an arena that never frees would leak
once a turn.  Both kinds of storage exist because there are two questions.

The frame hands out **bytes** rather than slots for this reason.  A spilled register still takes a slot the width of the widest
register; an array takes as much as its elements make it, aligned as its type asks.  Offsets once given out never move, which is
what lets the lowering take room before the allocator knows whether it will need any.

**Every index is checked against its own dimension**, not against how many elements there are in all: `m⟦0,5⟧` of a two-by-three
is outside though it is within the six.  The check is an instruction of its own.  What it tests is not about the read it
guards -- an index is compared against a length, and neither is part of the load -- so it is not a flag on the load the way the
overflow check is a property of an addition.  It lowers to a comparison and a branch that does not come back, through the same
fault path an addition that does not fit uses.

**Where both the index and the length are written down there is no check at all**, and a program whose index is outside is refused
rather than compiled into one that always fails.

**Layout is where the specification's freedom about data first shows.**  A variable marked `@[cdecl]` is laid out the way the
system's own compilers would; every other one is laid out whichever way is better.  What that buys today is one thing: an array
large enough to be worth reading a word at a time is aligned so that a word can be read.  What it deliberately does not touch is
the stride, since that is what an index is multiplied by and the front end works it out without knowing which layout the variable
ended up with.

**A global named only by having its address taken counts as named.**  The pass that drops what nothing reaches used to ask only
which variables are read and written, which is every variable a load or a store names; an array is named by neither, since what a
value of one is, is its address.

What a loop takes its values from
---------------------------------

Four things are iterators and none of them is called: a range, an array, a set and a dictionary.  What they have in common is not
a call but a *shape*, and the loop is written once around that shape:

    before:  br loop(s₁ … sₖ, v₁ … vₙ, mem)
    loop(s₁ … sₖ, p₁ … pₙ, mem):  condbr there is another → body, done
    body:    the names stand for this one; the statements; br loop(…)
    done:    what follows

`s₁ … sₖ` is whatever the thing being iterated carries from one turn to the next.  Three questions settle the rest, and they are
asked in the three places a loop has room for them: **is there another**, asked where the loop tests; **what does this turn
give**, asked in the body; and **what does the next turn start from**, asked at the branch backwards.  A `next` answering a result
is those three said as one value, which is what a user-written iterator will answer with -- so the loop built here is the loop
either kind wants.

| Iterator | carries | is there another | this turn gives | next starts from |
|---|---|---|---|---|
| a range | the number | it has not passed the end | the number | one step on, saturating |
| an array | a place in it | it is short of the length | the element, or the row | one place on |
| a table | a place in its entries | it is short of the capacity | the key, or the key and the value | the next place holding a key |

Everything that does not change from turn to turn -- where an array's elements are, how many there are, where a table's entries
are -- is worked out once before the loop and read from where it was left.  That is what keeps a turn of an array to a comparison,
an addition and a read.

**A table's walk steps past the places holding nothing**, and finding the next one is a function of the runtime rather than a
second loop written into this one.  That is what keeps the shape above the shape of every iterator: a `more` that had to search
would have to hand the body what it found, and there is nowhere in the shape for it to put that.

**A dictionary gives a tuple**, and two names take a tuple apart everywhere a tuple is bound.  So `foreach k, v = d:` needed
nothing of its own: the tuple is made and the binding that already existed takes it apart.

A tuple handed over as several arguments
---------------------------------------

`⁂t` among a call's arguments is expanded in the checker, before the call is counted or typed.  `_handed_over` walks what was
written and answers a list of `_Argument`, one per argument the call actually hands over: an ordinary argument contributes itself
and nothing more, and a spread one is lowered once and contributes one `extract` per member of its type.

Lowering the operand *once* is the point of the `_Argument` record.  An argument is normally lowered where the call needs it, so
that it is lowered exactly once; a spread operand has to be lowered earlier, because how many arguments there are is a question
about its type.  The record therefore carries the expression for an ordinary argument and the already-lowered value for a spread
member, and `_lower_call` lowers what has not been lowered yet.  Writing `⁂f()` calls `f` once.

Everything after the expansion sees a list of arguments and does not know how it was written.  The arity check, the per-argument
type check, the conversion of an unsuffixed literal to its parameter's type and the register assignment all run on that list, so a
spread call and the call written out are the same call in the IR, and no diagnostic had to learn about the glyph.  Two are new,
and both are about the glyph itself rather than about the call: the operand is not a tuple (4464), and the glyph stands where no
argument list is (3031).

**The second of those is the parser's and not the checker's.**  `⁂` is admitted only in front of an argument, which is what the
tree-sitter grammar says too -- a `spread_argument` rule that only `call_expression` names.  The first implementation made it an
expression and refused it in `_lower_expr`; that put a rule in the checker the grammar could simply enforce, and it made the two
grammars disagree about what an expression is.  The diagnostic moved to the parser and changed number with it.

Calling conventions
-------------------

A convention is a value attached to a function, not a property of the target.  The specification says the conventions need not
match the system's and may differ between the functions of one compilation, so they do.

**`pl4g` is the language's own and `cdecl` is the system's**, and every backend answers for both under those names.  A function
marked `@[cdecl]` gets the second and keeps its plain name; everything else gets the first.

**A call is placed by the callee's convention.**  Which register an argument goes in, which registers carry the answer back, and
what the call destroys are all the callee's to say, and a caller that asked its own would be right only while every function in the
program agreed.

**What `pl4g` is, today, is a convention whose argument registers begin where the answer comes back.**  On x86-64 that is `rax`
then `rdx`, where the system's begins at `rdi`; a function that answers with what it was given then has the value where it has to
be already.  On the other two the two lists already begin at the same register and nothing had to change.  Whether a bespoke
convention per *function* is worth more than one bespoke convention for all of them is an open question with an entry of its own.

**What a call destroys is asked of the callee rather than of its convention.**  A convention can only say what a function is
*allowed* to destroy; a small function destroys a great deal less, and the difference is a save and a reload at every call.  So
each function is asked, once it is generated, which registers it wrote and did not put back, and a call to it destroys exactly
those.  A function whose code has not been generated yet, and a declaration of one defined elsewhere, fall back to the convention's
whole caller-saved set.

Which functions have been generated by the time a caller is depends on the order they are generated in, which is the order the
module holds them; sorting that order by the call graph is what would make the answer always the exact one, and it is in the to-do
list along with what else that ordering buys.

**Three things carry a call rather than its instruction's row.**  What it destroys, because that is the callee's; what registers
its arguments went into, because a register holding an argument would look dead from the moment it was written and a later value
could be given it; and, for a return, the register the answer went into, for the same reason.  The rows say only what the
instruction itself does -- writing the link register, on the two architectures that have one.

**A physical register's live range is several stretches and not one.**  A virtual register gets a hull over the points where it is
live, which is right for a value: one value, one stretch.  A physical register is written wherever a convention says it is -- an
argument in, an answer out -- and between one such write and the read that takes the value away it holds nothing.  A hull would say
it was busy the whole time and the value that could have had it would be sent elsewhere, which is a move into a register and a move
straight back out of it.  Within a block a stretch runs from a write to the last read before the next write; at the edges of a
block it is what the graph says is live coming in and going out.

That splitting is what turns `fn f(p: u8) → u8: p` into a bare `ret`: without it the register the argument arrives in looks busy
from the entry to the return, so the value is copied out of it and back into it.

The allocator
-------------

Nothing a program builds can outlive the function that built it until something asks the system for memory, and the runtime the
compiler emits is what asks.  It is written once for all three architectures: what differs between them is the number of a system
call, which registers its arguments go in and which instruction enters the kernel, and that much is a small record each backend
fills in.

**The shape is GNU's obstacks: a bump pointer over a list of chunks.**  An arena is three words -- the first byte not yet handed
out, one past the end of the current chunk, and the head of the chunk list -- so an allocation is an addition and a comparison.
Where the current chunk is too small, a path that does not come back here asks the system for another and links it on.  A chunk is
never given back on its own and a whole arena is given back at once, which is the only granularity this allocator has.

That is the cheapest allocator there is and it is the right first one.  A compiler builds a great deal that lives exactly as long
as the compilation, and nothing in the language yet says that one value outlives another, so nothing yet can ask for the finer
answer.  What it is not is a general-purpose allocator: an arena that frees nothing will not do for a program that runs for a long
time, and the to-do list says so.

**There are as many arenas as a program makes.**  An arena is three words and nothing else, so one is a value like any other: an
allocation names the arena it comes out of, and giving that arena back gives back everything that came out of it.  That is the
whole of what makes an arena safe for storage with a known lifetime, and it is why the allocator is a *type* rather than a place
the compiler knows about.

**Allocations start on sixteen bytes**, which is the strictest alignment any value of the language wants.  Asking the question
once, here, is cheaper than carrying an alignment through every allocation and it costs at most fifteen bytes an allocation.

**An allocation that cannot be met stops the program**, through the same `__pl4g_abort` an arithmetic fault goes through.
Answering with a result instead would put a `?` on every value a program builds rather than computes, and there is nothing a
program could usefully do at that point that the system will not do better by refusing to start it.

Three things have to survive the system call that asks for a chunk -- which arena, how much was asked for, and how much was mapped
-- and none of them can stay in a register: the call's own arguments take every register a caller does not expect back, and the
instruction that enters the kernel destroys two more on one of the three architectures.  So they go on the stack, which is the one
place in the compiler where code written here rather than lowered from the representation needs a frame of its own.

The allocator is emitted where something calls it and nowhere else: a module that declares one of its entry points gets the body,
and a program that never allocates carries none of it.


Expectations
------------

A construct that says what it raises puts an *expectation* in force while it is checked.  It holds two sets: the diagnostics it
quiets, and the subset of those it asserts are raised.  `ignore` adds to the first, `expect` to both, and the difference is visible
only where nothing meets them.  The diagnostic engine consults the
expectations in force before it reports anything, innermost first; one that matches absorbs the diagnostic, records that it was
raised, and -- where what it absorbed was an error -- records that too, since a construct that raises an error cannot be compiled
whether or not anyone was told.

Expectations are a property of the engine rather than of any one stage, so a diagnostic raised anywhere while a construct is being
checked is covered, without every stage having to know about them.

A definition hands its expectation to what it defines rather than settling it where the definition ends, because not everything a
definition raises is raised while it is being read: that nothing ever reads the value a variable was given is only known once the
variable is gone.  The expectation is therefore put back in force at that point, and settled there.

A definition that absorbed an error is removed from the module, and from every cache that named it -- the startup function, the
constructors, the destructors, the tests.  Removing it is what keeps the later stages honest: they see a program that does not
contain it, rather than one containing something that could not be checked.

A value nothing reads is found by the scope, not by a pass: a binding records whether anything has read the value it stands for,
and the report is made where the value is replaced or where the scope ends.  That is exact for straight-line code, which is all the
language has; when control flow arrives it becomes a liveness analysis over the graph, and the place it is reported from will move
with it.
