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

**A sum or a difference that faults is computed at the type's own width where
the architecture has an instruction for it**, and what says whether it went past
is the flags that instruction wrote.  An eight-bit addition is an eight-bit
addition: `add %cl,%al` and then a branch on the carry flag for an unsigned type
or the overflow flag for a signed one.  One instruction and one branch, against
the other way of asking -- widen both operands, add at the register's width, and
compare the answer against one end of the type or both -- which is an extra
instruction for each operand and one or two comparisons after.  And the answer
needs no bringing back into its type afterwards, because it never left it.

Which widths that covers is a property of the architecture and each one says so
for itself.  x86-64 has arithmetic at all four widths and uses this at all four.
AArch64 has it at thirty-two and sixty-four, so a byte or a halfword there keeps
the widen-and-compare path.  RISC-V has no flags at all and keeps it at every
width: the question is asked of the answer instead, which is what the rest of
this section describes.

A **product** is not among them on any of the three.  Seeing that a
multiplication went past wants the upper half of the product, which is a second
instruction and on x86-64 a form with a fixed pair of registers -- where widening
and comparing is one instruction and no constraint on which registers are used.
The one flag x86-64 does write for a product says only whether a *signed* one
went past, and the unsigned question would still want the other instruction, so
taking it would leave two paths rather than one.

**Which operation it is, is asked of the ordinary operation it is built from.**
The saturating form and the trapping form of an addition are two opcodes and both
are an addition; a test written against one of the two spellings answers `false`
for the other and quietly picks the code for a subtraction.  That was a real
defect -- the widest signed and unsigned sums did not notice they had gone
past -- and it is why every such test names the ordinary operation.

**`⎕wrap` is a flag on the lowering and not a node in the graph.**  The checker carries one bit saying whether what is being
lowered stands inside one, and every operator asks it before choosing which instruction it is: `+` becomes `wrap.add` rather than
`add`, and `«` becomes `wrap.shl` rather than `shl`.  A saturating operator asks it too and reports rather than choosing,
which is the one place the bit is read for something other than an opcode.  Nothing survives into the IR
saying that a wrap was written: what survives is which operations it chose, which is all anything after the checker needs.  That
is what makes it lexical for free, and what makes it stop at a call -- a callee's body is lowered with the bit as its own
definition left it.

The wrapping opcodes were already there.  The compiler generates them for itself, in the hash of a key and the arithmetic on a
table's indices, where a value is a bit pattern and there is nothing about an overflow to report to anyone; what `⎕wrap` adds is a
way for a program to write one.  The five moving ones are new, and are the five checked ones with the distance taken modulo the
width of the type rather than compared against it -- one `and` for a width that is a power of two, in place of a comparison and a
branch that does not come back.  **Any other width is a remainder** (`_modulo_the_width` in `target/saturate.py`): the distance
masked to its type's bits where the type is signed, so that it is read without a sign, and divided by the width with the
machine's unsigned division into a register of its own -- or, for a distance written down, worked out there and loaded as a
number.  The checked rotation builds its other distance as the width less this one, and masks it only for a power of two: for
any other width the type is narrower than the register, and a shift right by the whole width brings in the noughts above the
value.  The IR interpreter takes `extend(b, bits, false) % bits`, and the macro interpreter does the moves of a wrap and the
rotations over the type's own bits (`_moved`).

**A wrapping operation is the only arithmetic that has to be brought back into its type.**  The checked and the saturating forms
never produce a value outside it, so the bits above a narrow value already say what the type says; this one deliberately does
not, and a sum of two bytes that wrapped has a ninth bit.  So it is followed by a mask for an unsigned type and a sign extension
for a signed one -- and by nothing at all where the type is as wide as the register it was computed in, which is where most of
them are.

**Over a run there is nothing to bring back and nothing to check**, which is where a wrap costs the least and saves the most.  A
checked addition of sixteen bytes is one instruction and then the eight that ask whether any lane went past; a wrapping one is
the one instruction.  It is also the only way a run is multiplied at all: seeing that a product went past wants the upper half of
it, which none of these machines gives at every lane width, while a product that may wrap wants only the low half, which they do
give -- `pmullw` and `pmulld` on one, `mul` at three arrangements on the other.

Which lane widths a machine has an operation at is stated as a set and not as a width to stay under, because of that last one:
x86-64 multiplies halfwords at every level and words from the second on and bytes not at all, which is not a range.

**`⍴` costs nothing on the left and one store per element on the right.**  The shape is known while compiling, so what it
answers written before one thing is a constant or a read of the counts a dynamic array carries; the two-sided form takes room for
the answer where it stands and fills it.

Filling has two shapes.  **One value everywhere** is a run: a splat of that value over as many lanes as the new object holds, and
one store -- which the step that cuts runs into pieces then makes one instruction per register's worth.  **Values from an array**
are a store apiece, each reading the source at `at % count`, which is a number known while compiling since both are.  Where the
two counts are equal that is a plain copy and could be a run as well; a to-do line records it.

**`#` is four reads and one walk.**  A tuple's count and a fixed array's are numbers the type holds, so they are constants; a
dynamic array's is the second of the parts it travels in; a table's is a field of the table block, kept by the two operations
that put things in rather than counted when asked.  A string's is the one that needs code, and it is its own function rather than
the walk `foreach` uses: what counting needs of each character is only how long it was, so it reads the leading byte and nothing
else -- every byte that is not a continuation byte begins a character, which is one `and` and one comparison per byte and no
decoding at all.

**`⌈` and `⌊` are four operations in the representation and three ways of using them.**  The four are the larger and the
smaller at signed and at unsigned, which is what the two operators come to once the type says how its values are ordered; a
`char` takes the unsigned pair, being the number it is stored as.

The three ways are decided by what is known while compiling.  **A tuple's members and a fixed array's elements are known one by
one**, so the comparisons are written out and nothing is counted while the program runs; for an array of more than one dimension
the answer goes into a frame and comes back as an array of the row's shape, `answer[j]` being the largest of `a[i][j]` over every
row -- which is one loop the compiler walks and no loop in the program, and which row-major makes plain arithmetic on one index
rather than a walk of the shape.  **Everything whose count is not in its type is a loop**: a list, a string, a set, a dictionary
and an array of unstated length all already have an iterator for `foreach`, and this is that iterator with one value carried
beside the state.

What the loop starts from is **the first thing there is, walked again**.  Comparing something with itself answers itself, so the
first turn costs one instruction and saves needing a value of the type to start from -- and that value would have to be the end
of the type, which every integer has, which a floating-point type has only as an infinity and which a character type has only by
knowing what a code point may be.  A thing with nothing in it is what taking the first thing cannot do, so the count is asked
once before the walk and the program stops where it is zero.  For a string the byte count is what is asked, not the character
count: no bytes is no characters, and asking the other question would be a walk to find out whether to walk.

**On integers it is a comparison and a conditional move**, which is the one thing every saturating operation is already made of
-- `clamp` in the assembler, asked here of the other operand instead of a bound.  So no backend needed an instruction it did not
have.  What it did need is a width: a register with no name of its own is a whole word wide, and moving a whole word into a byte
is not an instruction any of these machines has, so an operand carried into one names the width of the *value* rather than the
width of the register holding it.

**On floating-point values it is one instruction** -- `maxsd` and `minsd`, `fmax` and `fmin`, `fmax.d` and `fmin.d` -- and it is
the only floating-point operation the backends do not ask about afterwards.  Every other one can answer with an infinity or with
something that is not a number and stops the program where it does; these two answer with one of the two they were given, which
the program had already, so the comparison and branch that follow every other float operation are left out.  What the three
machines answer where one side is not a number differs between them, and it cannot arise: the operation that would have made such
a value stopped the program where it stood.

The type of what the one-sided form answers is worked out twice, once off the syntax before anything is lowered and once from
what was lowered.  That is not duplication for its own sake: a comparison lowers its left side with what the right side is wanted
to be, so `⌈v ≠ 9u8` needs the answer's type before the answer exists.

**Dividing without a remainder is the remainder with the remainder thrown away**, and it needed no test for zero of its own: the
remainder already answers a result and already fails on exactly the divisor this one has no answer for.  So where the divisor is
worked out, the whole of it is the remainder, a comparison of the answer with nothing, and a `wrap` putting that beside the
failure the remainder gave -- with the number the question was asked about carried as what the error holds.  Where the divisor is
written down neither the result nor the test is built at all.

**Writing a failure out is the `wrap` every other failure already is**, with a truth value of one and an answer half nothing may
read.  The one thing it needed of its own is where its type comes from: it is what is wanted where it stands, and that is the one
place in the checker that must *not* ask `_aiming_at` -- which unwraps a result, because a value of the answer type written where
a result is wanted is the successful one.  A failure is the result and not its answer, so it asks for what is wanted as it
stands.

In the grammar it is an expression and never an operand.  `⊥ a ⊼ b` carries the whole of what follows, so there is no reading in
which `⊥ a` is the left side of anything -- which is what `return` does and is why it sits at the top of the expression
hierarchy rather than among the operands.  Written among them it was an unresolved conflict: whether the operator after it
belonged to what it carried could not be decided until the operand after *that* had been read.

**A result whose error carries a value is three parts**, and putting the truth value *between* the two is what made that a small
change rather than a large one: everything that reads a result asks for the truth value by its place, and a part added after it
changes nothing.  `parts_of` says so, which is the one place that had to; the register allocation, the calling convention and the
pass that moves a large answer into the caller's storage all ask it rather than knowing the shape.  What had to be written was
one instruction to read the third part, one operand on the one that makes a result, and three lines in the pass that takes an
answer apart -- which now uses `unwrap`, `failed` and `error` for a result where it uses `extract` for a tuple, the parts of a
result being of three types rather than indexed by number.

**In registers a result's answer is spread out**, which `leaves_of` says where `parts_of` does not: an answer that is several
values -- a string, a record of two fields -- is that many registers, then the truth value, then the error's.  The representation
still reads a result by its three members; what gives a value registers, a place among the arguments or the answers, or a run of
offsets in memory (`leaf_offsets_of`) asks for the leaves.  The selectors already counted an answer's registers from the leaves when
reading one apart, so what was missing was the places that made the registers -- a block's parameters, a call's answer, a load --
and the count `ReturnStyle.in_registers` makes: `Reader ? i32` is four registers, more than an answer travels in, so it goes
through the caller's storage like any other large answer.

**Counting the turns is one iterator wrapping another.**  `_Iteration` is the three things a loop asks -- whether there is
another turn, what this turn gives, what the next turn starts from -- and `⎕enumerate` answers all three by asking the iterator
underneath and carrying one number beside its state.  So it works over everything a loop works over without any of them knowing
about it, and the whole of it is twenty lines in the checker: nothing reached the representation, the optimizer or any backend.

Which is also why a dictionary cannot be counted.  Its turn is already a pair, so the tuple this makes would hold a tuple, and a
value of one of those has nowhere to go: what a tuple travels in is one register per part, and a part that is itself several is
not one register.  It is reported where the count is built rather than left to fail in a backend.

**Lifting is read twice and settled once.**  What stands between `⌜` and `⌝` is parsed as a *type* first and kept where that
reading ends at the closing bracket; anything else is re-read as an expression.  Reading it twice rather than deciding is what
makes the brackets worth having: a bare name is both readings and the parser is not the thing that knows which, so it carries
whichever it could build and the checker looks the name up.  A name that a local has is a value; anything else is resolved as a
type.

`⌈⌜T⌝` and `⌊⌜T⌝` are constants and no instruction at all: the ends of a type are numbers the compiler has, and the answer is
the constant it already knows how to put in an operand.  That is the whole of what they cost, and it is why they are in the
checker beside the walk that answers the largest of an array rather than anywhere further down.

**Nothing settled while compiling is lowered, and that is the whole implementation.**  A condition after `comptime` is answered
by walking the syntax -- `⎕typeof` of something is that thing's type, read off it without lowering it; a type's name is that
type; and the two are compared and joined.  There is no value of a type at any point, which is why the representation of what
`⎕typeof` answers is unspecified: there is nothing to specify.  An arm the compiler settled as false is dropped from the chain
before anything is built, so what it holds is never checked against anything -- which is what lets the arms of one `if` be of
types that would not otherwise agree.

A `comptime foreach` is the loop lowering with the loop taken out.  There is no header block, no counter and no branch backwards:
the members are walked here, and for each one the name is bound to an `extract` of that member and the body is lowered.  Each
body is lowered separately, so each sees the name at that member's type -- which is the whole of how one loop binds a name of
several types.

**A list is three words and its room says how large it is.**  The value is where the elements are, how many there are, and the
allocator (below, beside strings).  The room it was given has a header of sixteen bytes before the elements holding how many there
is room for, which keeps them as aligned as the allocators' room is; a list with no allocator -- in the image, or the zeroed one a
record starts with -- has none, and nor does one that points nowhere, which is what an empty list made while running is.  Both
count as full.  `sema/lists.py` is the only place the header is written or read: `__pl4g_list_room` takes room and writes it,
`__pl4g_list_capacity` reads it, `__pl4g_list_free` gives the room back by the size it says -- which after an element was taken
out is more than the elements, and was given back as the elements' size before -- `__pl4g_list_join` makes the room a join answers,
and `__pl4g_list_grow` and `__pl4g_list_release` are a join onto one where it is (below).  `owned` copies and gives back a list
through them, and a string as it always did.

The element type lives in the type and not beside the value.  That is the whole of what "store the common element type" needs
today, and it is the shape the boxed case will be told apart from: a list whose type says what it holds is the one that needs no
tag per element, and one whose type does not would carry the tags.

**A string is two words and two generated functions.**  The value is where the bytes are and how many there are, which
`parts_of` answers exactly as it answers for an array whose type does not say its length -- so a string travels in two registers,
is passed and answered with in two, and needed nothing of the calling convention's own.

The bytes of a literal go in the image as an ordinary array of bytes with a name of the compiler's, one per distinct text: two
literals that say the same thing are the one run of bytes, which costs a dictionary lookup while compiling and nothing at run
time.  The encoding is done by the compiler, which is what makes "well-formed UTF-8" true by construction.

The two functions are generated in the representation rather than written as assembly per target, for the reason the table
runtime gives -- both have loops, several live values and arithmetic that wants a register allocator.  **Taking one character**
reads the leading byte, works out from it how many bytes the sequence has and which of its own bits belong to the code point, and
folds in six bits per continuation byte; it answers the code point *and* where the next one begins, because a walk wants both and
reading the leading byte twice would be the same work done again.  It checks nothing, the bytes being well-formed by
construction, and it is pure: it reads and changes nothing.  **Joining two** takes room for the two lengths together from the
arena and copies each side in, which is a change that outlives the call and is therefore impure.

A walk calls the first of them once per turn and uses the answer twice -- the character in the body, the place at the branch
backwards.  That is what the loop's `take` and `step` are, and they share one call rather than making two.

**A code point is held the way an enumeration's value is held, and is described the same way.**  `char` carries a *holder* --
the integer type its values are stored and read as, which is `u32` -- and everything that has to know how wide one is, whether it
is read back with its sign or with zeroes, and how much room it takes asks the holder rather than knowing about code points.
That is the whole of what the backends were told: three places per target, all of which already had the same question to ask
about an enumeration.

`⎕ord` and `⎕chr` are one instruction apiece and one of them is none.  The value is the same bits in the same register bank at
the same width, so the conversion is a bitcast -- which the verifier now allows for exactly this one pair, a code point and the
type it is held as, beside the addresses it already allowed.  `⎕chr` puts a check in front of it and `⎕ord` does not, and a code
point written down is settled while compiling either way.

**Raising to a power is squaring and multiplying, and there are two of it.**  Where the exponent is written down there is no
loop at all: the bits of it are known, so the multiplications are written out -- one per bit and one more per bit that is set,
which is five for a fourteenth power where multiplying it out would be thirteen.  Where it is not, the same algorithm runs with
the exponent in a register, at most sixty-four turns and as many as it has bits.

**The squaring cannot report an overflow the answer does not have.**  Every square the written-down form computes is a power the
answer itself contains, and for an integer of magnitude at least two a smaller power is a smaller number; for the three integers
where that is not so, and for a floating-point value of magnitude below one, every power is at most one.  So the check each
multiplication already carries is the whole of the checking, and nothing had to be written to keep a step towards the answer from
stopping a program whose answer was fine.

The loop needs one thing of its own for the same reason: **it does not square on the last turn**.  Squaring once more than the
answer needs is exactly the case the paragraph above does not cover, so the test for another turn comes between the two
multiplications rather than at the top of the loop -- which is why the loop is nine blocks of one instruction rather than four.
Two of those nine are there only because a branch that asks a question carries nothing with it: what the loop starts from is
handed over by a block after the test, not by the test.

The exponent is not walked where what is raised is an array.  One count serves every element, which is what being listable means
for the one operator here whose two sides are of two different types.

**The sign of the exponent is looked at twice and the loop walks a magnitude.**  Once before, to take that magnitude, and once
after, to divide one by what came out.  The bits are walked with a *logical* shift whatever the type, because what is in the
register after the first of those is a magnitude and not a signed number -- which is also what makes the most negative exponent
there is come out right, its magnitude being one more than the largest the type holds and its bit pattern being that magnitude
already.

**What the loop hands over at the end is the two halves of a result and not the result.**  A block parameter is a value of the
kinds a block parameter is, and a result travels in two: so the branch that chooses between the division and the plain answer
hands over the number and the truth value beside it, and the whole is put back together after the merge.

Where the exponent is written down none of that is built.  A raised number that is not negative is the multiplications and
nothing else; one that is negative is those multiplications and the division; the operator with a written-down exponent is
whichever of the two, with the answer wrapped up as a result because the *operator's* type says so and not this one exponent's.

**Rounding is one instruction on two of the three machines and eight on the other.**  x86-64 has `roundss`/`roundsd`, whose
immediate names the direction and whose value four says to ask `MXCSR` instead -- one instruction for all four operators.
AArch64 has `frintm`, `frintp`, `frintn` and `frinti`, one apiece.  RISC-V has none: rounding a floating-point number where it
stands is in the Zfa extension and not in the base D, so it goes out to an integer and back, the conversion carrying the rounding
because the architecture put the mode in the instruction.

That round trip is right only below the point where the format has room for a fraction -- 2²³ for `f32` and 2⁵² for `f64` -- so a
value at or above it is answered with itself.  That is not an approximation: such a value *is* a whole number already, and the
same limit is where the integer would stop holding it, so one comparison settles both questions.  The branch is over five
instructions and is not taken for the values a program that rounds is usually rounding.

The conversion loses a sign that the rounding keeps: a value between minus one and zero rounds up to minus zero and comes back as
zero.  So the answer is put back together with `fsgnj`, which costs nothing and is the instruction RISC-V writes a move with
anyway.

**x86-64 refuses to round at the oldest level.**  `roundsd` is SSE4.1, which the architecture's second level promises and the
first does not, and what the first would need instead is the same round trip through an integer plus a correction for each
direction.  The level is asked for and the default is the newest, so this is a refusal a program has to go out of its way to
meet; a to-do line records it.

**The three roundings that name a direction are pure and the fourth is not.**  What `⇕` reads is a register of the processor's
that nothing in the language writes, so two of them with the same value answer alike within one run and need not between two --
which is exactly the line `@[impure]` draws.  Nothing in the representation records it: purity is the front end's question, asked
where the operator is written, and what reaches the backends is an ordinary instruction.

**An integer type narrower than a machine width is held in the width that contains it**, with every bit above its own equal to
the zero- or sign-extension of the value.  That is the invariant `narrow.py` already stated for the four machine widths, and
arbitrary widths are the same invariant with one more thing to say about it: a machine's flags answer whether the width *it*
worked at overflowed, so for a type that does not fill that width they answer the wrong question.

The code that decides was already written for the case where a type is narrower than the register holding it -- it computes at
the register's width, where the exact answer always fits, and then compares against the type's own two ends.  A `u3` takes that
path for the same reason a `u8` in a 32-bit register takes it, so checked and saturating arithmetic needed nothing at all: a sum
of two `u3` values is computed in thirty-two bits, which cannot lose anything, and compared against seven.

Two things did need saying.  **Putting a signed value back into a narrow type** has no instruction: a machine widens from eight,
sixteen and thirty-two bits and not from five, so it is a shift up until the type's top bit is the register's top bit and an
arithmetic shift back down, which brings the sign in behind it.  And **a run of narrow elements is not a run**: the lane
operations check the width they work at, so a run of `u3` lanes would answer for the byte and let a sum of nine go unreported.
Such an array comes apart into elements, where the check is the one the type asks for.

What is not here is any conversion between widths, which the language has never had for the machine widths either.  It is what a
program answering a `u6` status feels first, since the status then has to be worked out in `u6` from the start.

**A comparison is absorbed by the branch that reads it, except a floating-point one.**  Read once, and read by a branch as its
condition, an ordinary comparison emits nothing of its own: the machine compares and the branch tests what the comparison wrote,
so there is no truth value anywhere and no register holding one.  Read any other number of times, or read by anything else, its
answer is a value like any other and goes in a register.

A floating-point comparison is never absorbed.  What these machines write for one is a different set of flags, on which a value
that is not a number is a fourth answer and takes more than one reading to tell from the other three -- so it is computed into a
register and the branch tests that register.  All three selectors said so and `folded_into_branch`, which is the one function all
three ask, did not: it answered that the branch would absorb the comparison, the selector materialized it anyway, and the branch
then emitted an integer compare of two floating-point registers, which no encoder has.  The effect was that `if a < b:` on two
`f64` was refused by the code generator while `f(a < b)` was not, and every approximate comparison with it.  The knowledge now
lives in the one place, which is what makes the three of them right.

**Comparing two strings is a third generated function, and it is `memcmp`.**  UTF-8 orders by bytes exactly as it orders by
code points, so the comparison neither decodes nor needs to know where a character begins: it walks to the shorter of the two
lengths comparing bytes, answers with the difference at the first that differs, and answers by the lengths where it runs out.
All six operators go through it, each asked of which of the two came first rather than of the strings, so there is one loop in
the image however many ways a program compares.

What it answers is negative, zero or positive, and how far from zero means nothing.  The byte arm answers with the difference of
the two bytes, which cannot go past a signed word, the bytes being in one already.  The length arm cannot do that -- two lengths
can differ by more than a signed word holds -- so it answers with one comparison subtracted from the other, which is branch-free
and says exactly as much.

The equal pair could skip the walk where the lengths differ, and does not.  The walk answers on the first byte that differs, and
two strings meant to be different nearly always differ early; the case a length check would save is the one where one string is a
prefix of the other, which is the case the walk has to do anyway to find out that it is.

A `char` needed nothing: it is held as a `u32` and its value is the code point, so each of the six is the instruction the same
comparison of two unsigned numbers is.

**Joining two arrays is two reads and two writes, and both are runs.**  `a ⧺ b` takes room for the answer where it stands and
copies each side into its part of it -- and it asks for each copy as one value of as many lanes as that side has elements, which
is the same shape an operator over a whole run asks for.  So the step in each backend that cuts a run into pieces cuts these too,
and a copy comes out as one instruction per register's worth on a machine that has them and an element at a time on one that does
not, with nothing written here to say either.

That needed one rule added to the cutting: a run that reaches no operation is one that is only being moved, so there is no
operation to ask which lane widths it exists at, and none is needed -- a register holds a run of any lane width, so what cuts it
up is only how wide the register is.  A join of two sixteen-byte arrays is two `movdqu` pairs where it was thirty-two loads and
thirty-two stores.

**An operator over an array is one operation over a whole run of elements.**  The front end asks the question of the whole
innermost run -- the last dimension, which is the one whose elements are next to each other -- as a single value of as many
*lanes* as the run is long, whatever the machine it is being built for can actually do.  `u8⟦16⟧ + u8⟦16⟧` is one
addition of sixteen lanes in the IR, on every target.  The side of the operator that is not an array becomes one value in every
lane; every dimension outside the innermost is walked as it always was.

A step in each backend then brings that down to what that machine has, and it has three answers.  **As it stands**, where there
is a register holding the whole run and an instruction that does the operation to all of it.  **In pieces**, where the run is
longer than a register: as many whole registers’ worth as fit, each piece being itself a run because the elements are laid
out one after another with nothing between them.  **One element at a time**, for whatever is left over and for every operation a
machine has no instruction for -- which is the floor under all of it, and is why a target that says it can do nothing still
compiles every program.

Each piece is a power of two elements long, so that the bytes it covers are a size one instruction reads and writes: sixteen
bytes, or eight, or four.  Anything shorter goes an element apiece, a partial read being its own instruction on every target and
a different one on each.  A read of fewer bytes than the register holds leaves the rest of it clear, which is what lets a run
shorter than a register be worked on in one.

**There are no flags over a run.**  A machine that adds sixteen bytes in one instruction does not write sixteen carry flags, so
what says an answer went past the end of its type is asked of the answer, of every lane at the same time, by operations that are
themselves one instruction over the whole run.  Four questions, one per operation and signedness, each the same formula in `and`,
`or` and `exclusive or`: a signed sum has gone past when both operands had one sign and the answer the other, `(a ^ sum) & (b ^
sum)`; a signed difference when the operands differed in sign and the answer differs from the left one, `(a ^ b) & (a ^ diff)`;
an unsigned sum when the addition carried out of the top, `(a & b) | ((a | b) & ~sum)`; an unsigned difference when it borrowed
into it, `(~a & b) | (~(a ^ b) & diff)`.  Every one leaves a value whose top bit in each lane says whether that lane went past.

**The lanes beyond the run are not asked about.**  A run shorter than a register leaves the rest of it holding zero on one side
and, where the other side is one value in every lane, that value -- so those lanes may well go past while none of the program’s
elements does.  Restricting the question to the lanes the run covers is what makes reading a short run into a whole register safe
rather than merely convenient, and it is asked of the target rather than worked out here: one architecture gathers a bit per byte
and masks it, another turns each lane into every bit of itself and looks at the bytes the run covers.

**Multiplying is not done to a run**, on any of them.  Seeing that a product went past wants the upper half of it, which none of
these machines gives at every lane width -- the same reason the narrow scalar arithmetic leaves multiplication to the widening
path.  Neither is a run of floating-point numbers: what says one of those went past is not a comparison but a question about the
number itself.

**How wide a register holding a run is, is the microarchitecture level’s to say on x86-64** and nobody’s on the other two.
`v1` and `v2` hold sixteen bytes, which is what the SSE2 integer instructions give and what "x86-64" means; `v3` and `v4` hold
thirty-two, AVX2 being part of what those levels promise and the program having already said at its own entry point that the
processor has it.  AArch64 holds sixteen, the Advanced SIMD instructions being in the base this compiler builds for.  RISC-V holds
none: its vector extension is not in that base and there is no level to ask for it with, so a run there is an element at a time
and the program means the same thing.

Which operations a machine has is stated per operation and per lane width, because it differs between them: one architecture
saturates a byte and a halfword and not a word, the other saturates every width, and neither multiplies.

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

The Report Log
--------------

Everything the compiler has to say about one compilation goes in one log.  Two kinds of thing are in it.  What the compiler
**said** is the diagnostics -- an error, a warning, a note -- which say something is wrong, or worth saying, about what the
program wrote.  What it **chose** is what the program did not state: what it left out, where it put something, how long it worked
out that a reference lives.

The two are different things and the `kind` field keeps them apart, but they belong in one log and in one order, because the
question a reader has -- what happened to my program? -- is not a question about only one of them.  Nothing is wrong when a
function nothing can reach is dropped, so it is not a warning; but "this function is not in your binary" and "you never read what
you gave this variable" are answers to the same question, asked of one program, and a reader should not have to look in two
places for them.  A language meant to be generated will have plenty of both, a generator emitting from a template routinely
producing more than one instantiation uses.

`--report-log=FILE` writes them as JSON beside the binary.  Each entry has a `kind`, which is the stable part that something
reading the log matches on; a `subject` -- the name the program gives it for a choice, and the diagnostic's own symbolic name for
something the compiler said; a `reason` in prose, which for a diagnostic is the message as it was printed and not a second
sentence about it; and, where the subject is written in a source file, a `where` giving the file, line and column.  Something the
compiler said carries a `number` as well, which is what the catalog gives it and what a reader looks it up by; a choice has none,
nothing being wrong with any of it, and that absence is what tells the two apart without matching on the kinds one by one.

**Every entry says its `class`** -- `error`, `warning` or `note` -- since format version 4.  A fatal and an error are errors and a
warning is a warning; a note the compiler said and every choice it made are notes, a choice being nothing wrong and most of what a
log holds.  The kind settles the class, so a reader could work it out, but only by knowing which kinds are which -- and a reader
asking for the errors should not have to.  `bin/pl4g-reports` draws each class in its own colour -- an error red, a warning yellow,
a note bright blue, which nothing in the highlighted source is drawn in -- and `--class CLASS` shows that class and the more
serious ones: `note` everything, `warning` warnings and errors, `error` errors alone.  It reads the versions before, working the
class out of the kind.

**`where` points at the subject's own name**, not at the construct that holds it.  A definition begins at its first attribute or
at its keyword, which at the top level is column one on every one of them; a local's value is produced somewhere else on the line
and sometimes on another line; and the call in `_ ← f()` begins four characters in.  A column that answered "where the line
begins" would say nothing a reader could not have worked out, so every named thing carries the span of its name: `Value` has a
`name_span` beside its `name_hint`, and a `Function` and a variable at the top level have one of their own.  A call needs none,
its instruction already beginning at the callee.

What is recorded today is what is left out, what is put somewhere the program did not write it, and what the program left for the
compiler to work out:

| kind | what it says |
| --- | --- |
| `drop-function`, `drop-variable` | what the reachability pass left out |
| `drop-local`, `drop-call` | what the dead-code sweep left out |
| `answer-in-storage` | a function answering with more than the registers hold, or a record laid out for C through its caller's room |
| `answer-as-c` | a function of the system's convention answering a record laid out for C in registers, and which |
| `name-lambda` | the name a lambda's code was given |
| `capture` | a name a capture list saying "all of them" brought in, and how |
| `place-local` | a variable put in storage of its own rather than a register |
| `instantiate` | the types one instance of a generic function was compiled for |
| `lifetime` | how long the reference one call answers with turned out to live |
| `type-inferred` | a variable whose type was not written in full, and the type it was given |
| `scope-ended` | where a variable's scope ends -- after its last use -- and why there |
| `fatal`, `error`, `warning`, `note` | what the compiler said, with its number |

**`scope-ended`** is worked out by `sema/lastuse.py`, a walk of each function's tree made once after the function is first
lowered, however many times it is lowered.  It keeps the variables of each scope and the end of the statement each use is in; a
use in a loop the variable was bound outside of is moved to the end of the loop's last statement -- a block's span runs on to the
next statement, so `end_of` finds where its text ends -- and one in a deferred statement to the end of its block.  Which names keep
a reference to which is the checker's to know from the types: `_kept_by_a_name` notes the pair in `_lends` as it promotes the
references a statement made, and the walk lets the one referred to last as long as the one referring.  A macro's expansion carries
the places of the macro's own text, in another file, so the walk takes the end of the invoking statement for a use in one and
ignores what one binds.  The entry goes on the first character of the line after, which `line_after` finds.  Each variable keeps
one end for each way through that has come so far: a use on a straight line replaces them with itself, and an `if` or a `match`
walks each arm from the state its conditions leave, then joins what the arms that do not leave come to.  An end carried into an
arm unchanged is moved to the arm's first statement where the arms do not agree or a condition of the choice made it -- but only
into an arm that starts on a later line, a one-line `if` having nowhere of its own to say it -- and the way past an `if` with no
`else` ends after it, at its column.  Where a variable's
last use moves its value out -- `_moved` keeps the places in `_moved_from` -- no entry is made: the move was logged, and is the end.

**A borrow a name keeps ends at the name's last use.**  `sema/liveness.py` works out, backwards over each function's tree and once,
which bindings are live as each statement begins (`live_in`): an `if` and a `match` join their arms, a loop goes to a fixed point,
`break`, `continue` and `return` leave with what is live where they go, a deferred statement's uses are live until its block is
left and what a `foreach` walks for the whole loop.  `_Borrow.keeper` is the binding of the name that keeps it, set where
`_kept_by_a_name` promotes it; `_lower_block` keeps the `live_in` of the statement being lowered in `_current_live`, and
`_still_out` counts a kept borrow only while its keeper is in it -- the keeper being a *value*, the binding and the statement that
gave it (`_current_stmt`), which the forward walk tells reaching from not: `reach_in` says which values reach a statement, a loop
taken to a fixed point, and a borrow counts only while its value reaches and its name is live.  `_kept_by_a_name` records a borrow
at the depth of its keeper's scope, not the one being lowered -- a value given in a block inside the name's own lives as long as
the name -- and a borrow of a name holding a reference carries what that name's value keeps over to the new keeper, dropping the
borrow of a name from its own new value.  Then a forward walk over the same answers, carrying per way
through which names are live and joining a choice's arms, notes the statement each stops being live at (`ends`) -- which is
where `_still_out` stops counting it -- and `_borrows_ended_at` writes a `borrow-ended` entry there; `ends_after`, `leave_ends`
and `exit_ends` are the values given another value within a statement, left by a `return`, `break` or `continue`, and still
counted where their block ends, which `_borrows_ended_after` and `_borrows_ended_with` log with the cause.  The log is the checker's
range because both read the one `live_in`.

**A `&mut` copied is lent** in `_lower_name`: a local holding a reference, read anywhere but directly under `⌖` -- the names
`_read_through` collects before a body is lowered -- calls `_lend` on its own name, shared where a `&` is wanted and writable
otherwise, and checks `_lent_out` first as a place's name is checked.  The lend is the statement's until something keeps it:
`_kept_by_a_name` promotes it with the reference a name was bound to, as it promotes `&mut x`.  Writing through `r⌖` and
assigning `r` ask `_lent_out` as writes.

**Two parameters living alike** are the pairs `_kept_pairs` reads off a signature: a parameter whose reference refers to a
reference carrying a lifetime name, and another whose own reference carries the same.  The body may write the second where the
first refers (`_said_alike`, asked where 4571 would refuse it), the place reached from the one and the value from the other by
the provenance walk; `Function.keeps` carries the pairs to every call, where `_kept_alike` holds what is handed for the second to
the place handed for the first by `_outlives_it` -- or, where the caller did not make that place, by `_said_alike` of its own.

**What a function type promises a caller** is three fields of `FuncType`: `listable`, `impure` and `can_ignore`, read off what is
written before `fn` or a lambda by `_promised`, and held to one-way subtyping in `_promises_as_much`.  A named function's value takes the
last two from its definition's attributes in `_function_as_a_value`, its own `func.ty` staying as it was.  A lambda's body is
lowered pure with `_observing` set: `_an_effect`, through which everything that changes what outlives a call comes, notes the
change there instead of reporting it; what was noted becomes the
lambda function's `impure` and its type's, which is therefore made after the body rather than before it.  The context a mismatch
is reported in -- what is being initialized, assigned, handed over -- is kept across lowering the body, which sets its own.  A call
through a value is an effect only where its type says `impure`, and `_answer_is_taken` asks the type's `can_ignore`.

**The context a mismatch is reported in is the innermost construct's**, so a construct whose parts have types of their own sets
the others aside while it lowers them.  `_report_mismatch` asks the contexts in a fixed order -- a loop's value, an operator's
operands, an argument, a field, an assignment, an initializer, and an answer last -- and each is a field of the checker set around
what it is about: `_filling`, the record or sum and the field being given a value, is set by `_lower_field_value` for every field
of a record written out (`_lower_record`, `_build_record`) and the part of a sum, with the others cleared, and reported as 4340;
`⎕chr` and `⎕ord` set `_handing_over` to their own name and clear the rest, so a number of the wrong type is the conversion's
argument, reported once -- the conversion's own check reports only where nothing was reported while lowering it.  Every place that
sets the contexts aside and puts them back -- a body lowered in the middle of another (`_BODY_STATE`), an instance, a lambda, an
index, a loop's value -- carries `_filling` with the rest.  **A division where a plain number is wanted** is asked by the
operator itself: `÷` and `%` answer a result, and the result is measured against what is wanted (`_accepts`) before the
instruction is made, the operands having been measured as numbers.

**A definition named as the image's own code** is asked where it is collected (`_not_the_images`, from `_collect_function` for a
function with a body that is not a type's member, and from `_collect_global`): `_start`, or a name beginning `__pl4g` or `pl4g_`
(`_names_the_images`), 4341 at the name.  The C++ port's interactive session writes its own definitions under the compiler's prefix
and is let through where a session is in force.

**What the error of a result carries may be a path**, read by `_parse_error_type` after the mark of a name's, an array's and a
list's result: a name, and `.NAME` while a name follows the dot, kept in `TypeRef.error` (and the array's and list's) as the text
written, dots and all, so that the tree's shape is what it was.  `_result_of` and `_resolve_type` split it at the last dot: a path
is looked up through its module, a name alone as before.  The grammar has the same as `_error_type`.

**An arm written on one line may be followed by the next on the next line**: `_parse_if` asks `_chain_goes_on` after every arm,
which reads past the end of the line where the next token is `elif`, `else` or `comptime elif` -- never a statement's first word,
and on the next line at the same indentation, an indented one being an `INDENT` first.

**A reference in a product or a tuple answered with** is asked about in two places.  `_holds_a_short_reference` says whether a type
holds one that does not last as long as the program, which is what `_borrows_written` refuses in any answer (4531) and
`_collect_global` at the top level (4532).  A tuple written out in the answer is asked member by member instead
(`_borrows_written` again), each reference's lifetime name giving the parameters it was borrowed from, kept on the function as
`leaf_borrows`, by where the part stands among the tuple's leaves.  `_members_answered_from` walks each `return` back to where the
tuple was made -- through a block's parameter, a cast, and a call that promised the same of that part -- and from there as a single
reference is walked; `_reached_from` goes through a part taken out of a call's tuple in the same way.  At a call,
`_members_as_long_as_given` makes a part lasting where every argument for it lasts.  The walk carries which leaf of a tuple it
took out (`leaf`), through an extract and into a call or a tuple made here, and a source whose name is on some of its leaves only
is in `only`, so `p⟦1⟧` does not reach a parameter whose name is on `p⟦0⟧`.  `_named_place_of` goes through a call the same way:
what a call answers names the shortest-lived of what the arguments carrying its name named (`_borrowed_parts`), which is what
4570 asks of a name bound further out.  An arena check had counted a reference inside
a tuple as something pointing into an arena; references have lifetimes of their own inside something else too, which
`_points_somewhere(references=False)` says.

**A `foreach` name marked `&` or `&mut`** is in `ForEach.marks`, one entry for each name, the parser reading the mark before a
name of `foreach` only.  `_Iteration.place` says where the element a turn gives is, for the walks over something that keeps its
elements (an array of one dimension, a list, a pick); `members` says, for `⎕enumerate`, what each member of a turn is and where,
so that `k, &p` takes the count and refers to the element without making the tuple holding both -- which, for a value never
copied, would be the copy.  `_bind_by_reference` binds a marked name to the place cast to `&T` or `&mut T`, and lends what is
walked (`_walked_name`, looking through `⎕enumerate`) with the depth of the loop's body, so `_lent_out` refuses writing it while
the loop runs; element assignment asks `_lent_out` too now, which it did not before.  A turn of several values is made with
`_made_tuple`, which takes a record apart into its leaves -- the gap that had refused counting the turns of one.

**`⟦⟧` at the top level** is read off the value before it is laid out: `_variable_type` hands a definition whose type is an array
of no element to `_array_of_its_value`, which asks `_type_of` -- taught an array written down (`_array_written_type`, the shape
from `_shape_written` and the element from `_said_by` or the first entry), `… in ⎕static`, a record or a sum written out, and a
value of an enumeration -- and holds the dimensions written to it.  The value is then laid out by `_constant_value` against that
type as any other, which is where an entry of another type is reported.  The same cases make `let g := …` at the top level work
for those values.

**`type-inferred`** is recorded where a name is bound -- a definition in a body or at the top level, the names a taking apart
binds, a loop's name, each turn of a loop written out -- by `_report_inferred`, which asks the name for the type it was given.  No
type written is reported as such; a type written is resolved again and compared with what the name holds, so `⟦⟧` alone and anything
else the value completed are reported with what was written, and a type the name holds as it was written is not.

The first two happen at every optimization level, because a function nothing can reach is code the program cannot run; the
sweep's two happen from `-O1`, because an unoptimized build keeps what the program wrote and so decides nothing about it.  The
rest of the choices are the checker's and happen always, there being no level at which a lambda does not need a name.  Recording
happens whether or not the log was asked for, because a choice recorded only when someone is watching is one a test cannot check.

**A warning that a `-W` setting quieted is not in the log**, and neither is one an `ignore` attribute absorbed: what goes down is
what was reported, and the severity that goes down is the one after `-Werror` has had its say.  The log is the record of what the
run said, not of what it might have said under other settings.

**How long a call's answer lives is recorded every time, and not only when it comes out lasting.**  It is the one thing about
such a call that neither the signature nor the call site says.  Where a lifetime name stands on two parameters the signature says
only that the two are equal, and which of the arguments the answer actually took its lifetime from is a fact about that call and
about no other:

```
lifetime: either — answers with a reference that lives as long as 'q',
                   the shortest-lived of what the arguments for 'b' and 'c' named
lifetime: either — answers with a reference that lasts as long as the program,
                   because every argument for 'b' and 'c' does
```

Where two of them live equally long both are named, since naming one would read as though the other had been looked at and
turned down.

**A capture list is recorded where it said "all of them" and not where it named names.**  `[=]` and `[&]` leave which variables
to the compiler, and that is the one thing about such a lambda a reader cannot get from the source -- so there is one entry per
name, with the lambda's own name in the reason, and "which variables were brought in" is answered by asking for the `capture`
entries rather than by reading prose.  A list that wrote the names decided nothing and is recorded as nothing.

**A lambda's name is recorded because the program left it unwritten.**  It is what ties a symbol in the finished binary back to
the line the lambda was written on, and it is what every `capture` entry names to say which lambda it belongs to.  The name
carries the module's own name in front of it, as every definition does, so two files each writing a lambda do not produce one
symbol twice.

**A call the program said it did not want goes at every optimization level**, which the `dropignored` pass does before anything
else runs.  That is the rule the reachability pass already follows and for the same reason: what it acts on is not something the
compiler noticed about the program but something the program said, and `_ ← f()` means the same thing however the compiler
was asked to build.  The rest of what is dead still waits for `-O1`, a local nothing reads being the compiler noticing.

It runs before `largeanswers` because that is what asks the question about the program as written.  **A function rewritten to
answer through the caller's storage writes memory that outlives the call, and is not thereby impure**: what it writes is the
place it was handed and nothing else.  It said `impure` for a while, because saying nothing left its calls answering with
nothing, used by nothing, and swept away with the answer still unwritten -- a wrong-code bug the suite did not catch, the test for
that shape having run only at `-O0`.  Saying `impure` fixed that and cost the other half: a call to such a function was made
whether or not anybody read what it wrote, and a pure function whose answer nobody wants is exactly what a caller is entitled to
drop.

So the attribute says which of the two it is.  `answer_in_storage` means "writes the place it was handed, and otherwise pure", and
a call to such a function has an effect by default -- the safe answer where nobody has asked a better question.  **The sweep asks
the better question**, and it is a question about the place rather than about the callee: where the place is room the caller made
and the call is the only thing left that names it, what the call changes is something nothing can read, so the call goes and the
room goes with it.  The loads that read the answer die first in the ordinary way, then the address arithmetic that fed them, and
the frame's last user is then the call -- which is why the sweep running to a fixpoint is what makes this work rather than a rule
that has to recognise the whole shape at once.

**The textual form prints both**, and the reader takes both.  A form that left `answer-in-storage` out would read back as a module
where such a call may be moved and repeated, which is the one mistake about this that writes wrong code rather than slow code.

**A call that is not made is the largest thing the sweep does**, which is why it is recorded even though the value it produced had
no name: what the program asked for was a function to run, and it does not run.  The entry names the callee and says why -- nothing
reads what it answers with, and it changes nothing that outlives the call -- because the why is a property of a function the
reader wrote and can change.  A reader who cannot find their call in the output, and a reader wondering whether `@[impure]` is
missing from something, are the two this is for.  A call to an impure function is never among them: it is made whoever wants its
answer, so there is nothing to tell.

Everything else the sweep removes is an intermediate of an expression, which nothing in the program names and nobody can ask
about.

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

Whether a `mut` was needed (4931) is asked when a local's scope is popped, against a set of names read off the function's body
before it is lowered (`_changed_names` in `sema/check.py`): what the syntax writes, and two things only the checker knows -- `&`
given where a `&mut` is wanted (`_lend`) and an array written by a function it was handed (`writes_arrays`) -- added while the
body is lowered.  A lambda's body adds its own names to those of the function it is in.  The local that a capture by reference
makes inside the lambda is never reported; the variable it names is, where it is defined.
A second set, `_used_whole`, holds the names a body uses whole -- anything but the base of a lookup, the operand of `#` or of a
binary operator, and what a `foreach` walks -- and a writable table among them keeps its `mut`.

Whether a table is the function's own, for 4479, is `_table_made_here`: the handle traced through casts to the call of
`__pl4g_table_new`, which every literal and set operator lowers to.  A handle that is a block parameter is not answered there: `_written_table` keeps the write in
`_unsettled_tables`, one list per body -- a function's, an instance's, a lambda's -- and `_settle_tables` answers it when that body
is lowered, following every edge into the block (`_incoming_of`), a parameter already being followed counting as made.  A call
hands every argument for a writable table parameter (`_fills`) to `_written_table` as well, with the callee's name, so the same
question is asked of it and a refusal says 4934.  Where the table settles to a writable table parameter of the body's own
(`_through_a_parameter`), it is the caller's to answer for: nothing is reported, and the body's function is set `opaque` so that a
call to it is kept.

The log travels on the module, since every stage has the module and any of them may decide something.  It is not the design log in
[decisions.md](decisions.md), which records what was decided about the *language* and is written by hand; this one is about one
program and is written by the compiler.

Reading the Log
---------------

`bin/pl4g-decisions` shows a program's source with each record of the log standing just above the line it is about -- above rather
than below, so that the line is read already knowing what became of it instead of being read, understood, and then corrected.  A list of names
and line numbers is not something anyone reads; the question a report answers is "I wrote that, what happened to it?", and it is
answered by looking at the place it was written.  A pattern on the command line chooses which source files to show.

**The mark stands over the column the record gives**, which is the name the report is about -- worth finding in a line that
holds several names, and in `_ ← f()` there are three things it could otherwise have been taken to mean.  What it puts before the
mark is built out of the line itself, one blank per character and a tab for a tab, so a glyph the terminal draws two columns wide
is stood in for twice without this program having to agree with the terminal about which glyphs those are.  The language's
brackets for a tuple are two such.

Where the output is a terminal the source is highlighted, and where it is a pipe it is not, unless `--color=always` says otherwise.
The highlighting is done with the tree-sitter grammar in `tree-sitter-pl4g` and its own highlight queries, so what this colours and
what an editor colours are the same thing and cannot drift apart.  Everything the highlighting needs may be absent on a machine
that only wants to read a log, and none of it being there is not an error: the source is shown without colour, which is what a pipe
gets anyway.

Running the tests
-----------------

`python -m pytest tests` runs everything, over every core: about two and a half minutes becomes about seven seconds on a machine
with sixty-four of them, which is the difference between running the suite after each change and running it when it occurs to you.
That is the default rather than something to remember -- `addopts` in `pyproject.toml` says `-n auto` -- and `-n0` on the command
line puts it back in one process, which is what a debugger wants and what a test printing something wants.  `pytest-xdist` is
therefore a dependency and is named as one: without it `pytest` stops at the option rather than quietly running serially, which is
the right way round for a thing the suite depends on.

Nothing in the suite depends on order or shares a file between tests -- each language test compiles to a path made of its own
name, its optimization level and its target -- so what parallelism needed was not a change to the tests.

What it needed was two races outside them, both of the same shape: a shared library written where it is read from.

- **`tree-sitter` builds the grammar into a shared library** on first use, and again whenever the generated parser is newer --
  which it is every time the grammar changes.  Dozens of processes arriving at once find it half written.  A session fixture in
  `tests/conftest.py` has one process do it under a file lock while the rest wait; the lock is a file because the processes that
  have to agree are separate interpreters that know nothing about one another.  The file it warms with is a program of *this*
  language, since which grammar gets built is decided by what the file is.
- **`pl4g-decisions` builds its own copy** for highlighting, and compiled it straight to the path it loads from.  It now compiles
  beside it and renames, a rename being the one operation that cannot be seen half done.  That is worth having whatever the tests
  do: two people running it at once is not a strange thing.

And one thing in a test: `grammar_parses` read a non-zero exit from `tree-sitter` as "the grammar refuses this program", which is
also what it returns when it could not load the grammar at all.  Those are different answers and only one of them is about the
program, so the second is now told apart, asked again, and reported as itself if it persists -- a reader sent to look at a
perfectly good file is worse than a slow test.

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

A Shared Object, and What the Host Defines
------------------------------------------

`-shared` writes an `ET_DYN` image linked at nought, which a program loads with `dlopen`.  **The code is the code an executable
has**: every back end reaches what it reaches relative to where it is, so only data that holds an address is relocated.  The
writer finds those words as the C++ compiler's `-fpie` does -- an absolute eight-byte fixup in data; one in code, or one of another
width, is refused -- and what was read-only but for being relocated is made writable, renamed `.data.rel.ro`, put in a segment of
its own and said with `PT_GNU_RELRO`, which the loader acts on once it has relocated it.

**The tables are last, in two segments of their own**, so that nothing before them moves with their size: `.hash`, `.dynsym`,
`.dynstr` and `.rela.dyn`, read-only, and then `.dynamic`, which is writable because glibc adds the load address to the addresses
in it where it stands.  The symbols are the null one, `dlsym` where the object needs it, and then what it offers -- its visible
functions and then its visible variables, in the order the module holds them.  The hash table is a System V `DT_HASH` with a bucket
per symbol, each symbol put at the head of its chain in order.  The relocations are an `R_*_RELATIVE` per word in the order of
their places, whose addend is what the word holds as linked, then a `GLOB_DAT` -- `R_RISCV_64` on RISC-V, which has none -- for
the slot `dlsym` is put in.  `RELA` rather than `RELR`, which glibc accepts only from an object that also asks for the symbol
version `GLIBC_ABI_DT_RELR`: one table of one kind is less than two tables and a version requirement.  `DT_NEEDED` names
`libc.so.6` where something needs `dlsym`, and nothing otherwise.

**There is no entry point and no startup code.**  The constructors are in `.init_array` and the destructors in `.fini_array`,
reversed, since the loader reads that one from its end and the program's order is what has to come out; each was given the
system's convention, the loader being what calls it.  A startup function the program has is not called.  The stack is the caller's,
so the program makes none of its own and there is no handler for running off it.

**A call to a function the host defines reaches a stub**, `__pl4g_host.SYMBOL`, local to the image so that `dlsym` can never find
it in place of the host's.  It reads `__pl4g_host_slot.SYMBOL` and goes where it says, the arguments and the return address as
the caller left them; while the slot holds nothing it calls `__pl4g_host_find` with the slot and the name in two registers no
argument is in, and then goes where that answered -- or, where `dlsym` answered nothing, stops the program with status 92 and a
message naming the symbol.  The finder is written once for the three architectures, from what each convention says its argument
registers are: it keeps every one of them, sixteen bytes a register, and the slot, calls `dlsym(RTLD_DEFAULT, name)` through the
slot the loader filled, and stores what came back.  The registers each architecture uses are `r11`, `r10` and `rax` on x86-64; the
two AArch64 keeps for code between a call and what it calls, `x16` and `x17`, and `x9`; and `t1`, `t2` and `t0` on RISC-V, which,
like AArch64, has the stub keep its return address across its call to the finder.

**A `str` is made C's text by the checker.**  A host function taking one is split in two: a declaration taking an address in its
place (`NAME.host`), and the function the program calls, whose body copies each `str` into the heap with a nul after it
(`__pl4g_str_c`), calls the declaration and gives the copies back.  It is done in the representation so that the interpreter's call
into the host and the compiled stub both see only what C sees.

**A stop in a shared object ends the process it is in**, as it ends a program: the message is written to standard error and the
process exits with the status, through the system call and not through the C library's `exit`.  What the host had buffered and not
yet written is lost with it.  Whether a stop there should leave through `exit`, so that the host's own handlers run, is open
(TODO-pypl4g.md).

The Embedded Interpreter, and Its Calls into the Host
-----------------------------------------------------

The C++ interpreter can be embedded in a C++ program, through `pl4g::embed::Interpreter` (`src/lib/embed/embed.hh`): what a
program it runs is started with -- arguments, environment, the descriptors of its three devices -- the warnings, the colour of
the diagnostics, where modules are looked for, and a run of a program given as a file or as text in memory.  A run is
`repl::run_file`'s: the program is compiled from its source and interpreted, and how it ended is answered as a process would
answer it, with status 1 where it did not compile and the diagnostics on the error descriptor.  Text in memory is handed to the
source manager under the path the caller names, which is read as if it were that file.

**A program the interpreter runs has a host**: the program the interpreter is in.  The compilation says it makes something
interpreted (`ir::Output::interpreted`), wherever the interpreter is what runs it -- a file, the interactive session, an embedding
program -- and so a function the host defines is declared there without 5300, as in a shared object, while the compiler still
refuses one in an executable.

**A function the host defines is found the first time it is called**, with `dlsym(RTLD_DEFAULT, SYMBOL)`, and called through
libffi.  The embedding program has to export its symbols for `dlsym` to see them (`-rdynamic`, CMake's `ENABLE_EXPORTS`) and to
define them `extern "C"`.  What is kept is kept for the run: where each symbol was found, asked once however many declarations
name it, and for each declaration the call's description (`ffi_cif`), prepared from its IR signature.  Nothing is kept from one
run to the next, as nothing is from one process to the next.

**A value crosses as the x86-64 back end leaves it in a register.**  The checker has let only C's types through and made each
`str` an address of a nul-terminated copy, so every argument is one cell: an integer at the width it is held at (`u6` as a byte,
`i24` as four), signed or not as its type is; a truth value as a byte; a character as its `u32`; an `f32` or `f64` as its bits; a
reference as the address of the place it names, which for an `@[abi]` record is the record C expects.  C reads the low bytes of
the cell its type has.  An answer comes back widened to a word by libffi and is made a cell as a load from memory makes one: its
held width extended as its type's signedness says, whatever C left above that width not looked at.

**A symbol nobody defines stops the run** as a shared object's stub stops it: `pl4g: the host defines no 'SYMBOL' in 'NAME'`, at
the declaration, on the error device, and status 92.  `NAME` is the function the program wrote, not the `NAME.host` the checker made
of one taking a `str`.  The embedding program is not ended with it: the stop unwinds the interpreter, as every stop of a run does,
and `run_source` or `run_file` answers 92.  **The interpreter is an archive too.**  The objects of `libpl4g-embed.so` are compiled once and are also `libpl4g-embed.a`,
the same C entry and the same C++ interface for a program that carries the interpreter rather than loading it; the archive
passes on what it is linked with to what links it, and `embed-host` is linked with it.  **Every object of the build is
`-fPIC`**, a program's own included, set once with `add_compile_options`: CMake's `POSITION_INDEPENDENT_CODE` says `-fPIE` of a
program's objects after it, which would win.  **What the host hands over by name is asked first.**  `interp::Options::defined`, a map from a symbol to the address of a
function and a context, is consulted when a host function is first called, and `dlsym(RTLD_DEFAULT, ...)` only where it holds nothing of
that name; what was found is kept for the run as before.  `embed::Interpreter::define(symbol, function)` fills it -- a template
takes a function pointer of any type, the table holding addresses -- and so do `pl4g_embed_define` of the C entry and
`LoadedInterpreter::define`.  Each takes a context, a `void*` that is null unless given: a function handed over with one is
called with it first, as a pointer, and then with what the program's declaration says, so that one function of the host serves
several of its objects; the call's description for libffi is made at the first call, once the function and its context are
known, with a pointer put in front where there is a context.  A null context is no context.  The table is the interpreter's state, so it holds for every run after, and the interactive session
reads it too.  `src/examples/embed/defined.cc` is a host linked without `-rdynamic` handing over functions of internal linkage,
one of them under the name of a C library function, which its program reaches in place of the library's; ctest's
`embed-defined` runs it, and `embed-host` holds the C++ interface.  `src/examples/embed/members.cc` hands over the member
functions of objects: what is defined is a static member function of the class, the object is its context, and the static
member turns the context back into the object and calls the member on it -- two accounts through one pair of statics, each
its own context; ctest's `embed-members` runs it.  **No way a program ends ends the program embedding it.**  Returning from the startup function, a
stop -- an answer that does not fit, an index past the end, a host symbol not found -- and the system's own `exit` and
`exit_group` asked for with `⎕syscall` all end the run alone: the interpreter takes those two calls before they reach the kernel,
and each of the others unwinds it, and the call that ran the program answers with the status.  `embed-host` holds each of them.

**The interpreter is also a shared object**, `libpl4g-embed.so`, for a program that loads it with `dlopen` rather than linking
with the library.  It offers three functions with C's linkage (`src/lib/embed/pl4g-embed.h`): `pl4g_embed_state_new`, which makes a
state of the interpreter, `pl4g_embed_state_free`, which gives one back, and `pl4g_embed_run_source(state, text, length, name)`,
which runs a program held in memory in a state, with descriptors 0, 1 and 2, and answers its status -- 128 plus a signal's number
where it would have died of one, and -1 where there is no state or the interpreter itself failed, nothing being allowed to leave it
as an exception.  A state is opaque: the C side names a type it never sees inside, which is a struct around a
`pl4g::embed::Interpreter`.  Everything else in the object is hidden, by the visibility it is compiled with and by a version
script, which also hides what is visible by its own say-so: the grammar's entry point, and the instantiations of the standard
library's templates.  The functions the program calls with `@[host]` are still found with `dlsym(RTLD_DEFAULT, ...)`, so the
loading program is linked with `-rdynamic`.  `src/examples/embed/host.c` is such a program, in C, and ctest's `embed-example` runs
it: in each of two states made at once, it hands the interpreter a program that reads its input through two functions of the host,
works out a sum, the extremes and a sum of squares, and delivers them through a third that only stores them, which the host then
checks against what it works out itself.  **A C++ program has an object for it** (`src/lib/embed/pl4g-embed.hh`, header
only): `pl4g::embed::LoadedInterpreter` loads the library, finds the three functions and makes a state when it is made -- throwing,
with what went wrong, where any of that fails -- and gives the state and the library back when it is destroyed; it can be moved
and not copied, and `run(text, name)` runs a program in its state.  What it asks and promises is said as contracts, through `PL4G_PRE` and `PL4G_POST`, which
are `pre` and `post` where the compiler has contracts (`__cpp_contracts`) and nothing where it has not.  GCC 16 has them in C++26
without `-fcontracts` but links what reports a violation only with it, which no macro shows: a program using the header with
GCC is compiled and linked with `-fcontracts`, or defines `PL4G_NO_CONTRACTS`.  `src/examples/embed/host.cc` is the example again through it,
which ctest's `embed-example-cc` runs.  `src/examples/embed/expressions.cc` is one that hands its work over as JSON: arithmetic
expressions written as trees -- a number a leaf, an operation an object of `op` and `args` -- made with nlohmann::json, some written out
and the rest grown at random to depths of one to fourteen, read by the PL4G program through a callback as a view of the host's
own bytes (`⎕span`), decoded with the `json` module and evaluated, and answered as JSON through another callback, which only
stores the reply.  Each reply is read with nlohmann::json, held to a JSON Schema with json-schema-validator and compared with the value the
host worked out while growing the expression, folding from the left as the program does; ctest's `embed-expressions` runs it where
both libraries are installed.

**What a run leaves behind is its state's.**  The interpreter had no state of its own between runs, but the runtime it calls did,
in variables of the C file: the heap's lists and the slab it cuts from, and the base every table's seed is worked out from.  Built
into the library (`PL4G_RUNTIME_STATE`), the runtime keeps them in a `struct pl4g_runtime_state` (`runtime/state.h`) instead -- the
one the thread has selected in `pl4g_runtime_current`, which `embed::Interpreter` selects for the length of a run and which is
otherwise one of the process's own -- and keeps account of what it takes from the system: each slab and each object too large for
a class carries a link in front of it, and the ring with its mappings and the runs of arguments and of the environment, which
nothing gives back while a program runs, are noted in a ledger.  Releasing a state unmaps and closes all of it.  The packaged
runtime is built without the definition and is what it was, byte for byte, which `bin/pl4g-runtime --check` holds it to.  What the
compiler keeps for the life of the process -- the types, made once and shared because they are compared by address, the registers
and operations of the targets, and the trees the checker makes of its own -- is not a run's and not a state's: it never changes
what a program does, and it is shared by every state as it is by every thread of the language server.

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
| 4600-4699 | documentation comments |
| 4700-4799 | collections, and what is taken out of them |
| 4800-4899 | products and sums |
| 4900-4999 | conditions on functions, and bundles |
| 5000-5299 | control flow and returns |
| 5300-5399 | functions the host defines, and shared objects |
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


Exit statuses the runtime reserves
----------------------------------

`pypl4g/target/statuses.py` is where the reserved range is written down, and every place that ends a program the runtime stopped
reads it from there: the fault helper on each of the three targets, and the x86-64 entry point's check of the processor.  There is
one such number per kind of stop and they are all in one file, so that what a status means is a thing to look up rather than a
thing to remember.

**A fault exits rather than traps**, which is a change from what this compiler first did.  The argument for the trap was that a
signal hands a debugger the stack as it stood; the argument against is that a signal is not a status, a shell reports it as 128
plus the number, and it therefore collides with whatever the program might have chosen to exit with.  What settles it is the
reservation: 79 through 127 being nobody else's is what makes a status able to say "the runtime stopped this" without also
claiming the program meant it.

**The message is still written first**, so nothing is lost either way; what the trap gave up was never the message.

**The range begins at 79 and not at 64**, which is where `<sysexits.h>` ends.  It began at 64 and the argument was that the
convention is advisory where this is the compiler's own -- but "advisory" cuts the other way: a great deal of software reads those
numbers, and a runtime that stopped a program with 64 was telling all of it that the command line was wrong.  `EX__MAX` is 78 and
the runtime starts after it.

**Each kind of stop has a number**, and the status travels to the helper rather than being a constant inside it.  `__pl4g_abort`
takes it as a third argument, which means it has to survive the write that reports the message -- the write's own third argument
arrives in the same register -- so the first thing the helper does is put it somewhere the write will not touch and the walk of
the stack keeps nothing in.  The alternative was a helper per status, which is the same code as many times as there are kinds.

Where a kind comes from differs by where the check is.  A fault the *backend* emits knows its kind from the operation: the three
selectors build one `_Fault` per kind and hand it the number.  A check the *front end* emits carries it in the instruction --
`AssertInst` says what went wrong and which number that is, because an index outside its array and a walk that has ended are two
different things and only the front end knows which it built.

The stack a program runs on
---------------------------

**A program does not run on the stack the kernel gave it.**  Before anything of
it runs, its entry point asks the system for one of the size the program was
built with, with a guard below that nothing may touch, and moves the stack
pointer there.  Running off the bottom is then a fault in the guard, which a
handler recognizes and reports as a status of its own rather than as a signal.
`--stack-size=SIZE` says how much, 1 MiB by default, and `--guard-size=SIZE` how
much unreachable space sits below it, 64 KiB by default.  Both take a number of
bytes, optionally with `K`, `M` or `G` after it, and a size that is not one is
refused (1016) rather than quietly replaced by the default.

**None of it is carried in the image.**  `target/stack.py` is written once and
emitted for every architecture, the way `target/allocator.py` beside it is:
what differs between them is the number of a system call, which registers its
arguments go in, and which instruction enters the kernel, and that much is a
small record each backend fills in.  It was compiled from C and packaged with
the compiler at first, which put two and a half kilobytes of object code into
every program that wanted a stack -- which is every program.  Emitting it costs
about nine hundred bytes, all of it code the compiler selected, and it leaves
the packaged runtime to the programs that actually reach it.

**One mapping, then part of it taken away.**  The whole of `guard + size +
ALT_STACK` is asked for readable and writable and `MAP_NORESERVE`, and the guard
is then turned to no access at all with `mprotect`.  The guard is what is left
of the mapping once the rest of it has been kept, so there is no second mapping
to place and nothing to go wrong between the two calls.  `MAP_NORESERVE`
because a stack is reserved and not used: a megabyte of address space costs
nothing until it is written to, and a system that counts commitments should not
be asked to count this.

**Three regions in a known order**: the guard at the bottom, the program's stack
above it, and above that the little stack the handler runs on.  One mapping
rather than three is one call rather than three, and the handler's stack has to
exist for the reason the whole thing does -- there is no room on the stack that
just ran out.

**It is asked for below the stack the kernel made**, at an address worked out
from the stack pointer as the entry point found it, brought down to a two-
megabyte grain and two megabytes clear of the kernel's stack -- Linux leaves a
gap below a stack that nothing may be mapped into, so a hint any closer would
simply be ignored.  It is a hint and not a demand: a kernel that would rather
put the mapping elsewhere does, and the program is no worse off.  What the hint
is worth is that the program's stack stays in the part of the address space a
stack lives in rather than in the middle of where mappings are handed out.

**The guard is rounded up to the largest page the architecture may use.**  What
`mprotect` takes away is a whole number of pages of whatever the running kernel
chose, and the program is built once for all of them; a guard of 8 KiB on an
AArch64 kernel with 64 KiB pages would otherwise take 64 KiB away and leave the
handler comparing against bounds that are not the ones in force.  So the number
is brought up where the program is built: 4 KiB on x86-64 and RISC-V, 64 KiB on
AArch64, which is the same table the segment alignment above comes from.

**Running off the bottom is caught rather than fatal.**  The handler is
installed with `SA_SIGINFO`, so that it is told the address that faulted, and
`SA_ONSTACK` with the alternate stack above.  It compares the address against
the guard it recorded: inside it, the program ran off the bottom, so it writes
`pl4g: the stack ran out` and exits with the status reserved for that (67).
Outside it, this is some other bad address and not the runtime's business, so
the handler puts the default back and returns, and the instruction runs again
and the program dies of the signal it really got -- core file and all.

**Nothing of it is required to succeed.**  Where the mapping is refused the
entry point leaves the stack pointer where the kernel put it; a program that
could not have the stack it asked for still runs, on the one it would have had
before.  A stack that could not be *guarded* is given back rather than used: a
stack with no guard below it is the thing this exists to avoid, and having one
silently would be worse than having none.  `--stack-size=0` asks for the
kernel's stack on purpose, and is what the tests that measure the smallest image
build with.

**`PT_GNU_STACK` carries the size.**  It is not what makes any of this work, the
program having mapped its own; it is filled in because that is where the format
keeps the number, so a reader of the image finds it there, and because a program
whose own mapping failed is then left on a stack of the size it asked for.

Two things about it are worth writing down because each cost a debugging
session:

- **x86-64 needs an `SA_RESTORER`.**  That architecture's kernel does not return
  from a handler by itself; what returns is a few instructions the program
  supplies, whose address goes in the action.  So the backend emits
  `__pl4g_stack_return`, which is `rt_sigreturn` and nothing else, and the
  field and the flag are filled in only there -- the other two kernels put
  their own trampoline in the return address register and have neither in the
  structure at all.
- **On AArch64 `mov x22, sp` is not a move.**  The stack pointer and the zero
  register share encoding 31 and which one is meant is decided by the
  instruction; the move form reads the zero register, so reading or writing SP
  is `add x22, sp, #0`.  Written as a move it assembled cleanly and set the
  register to nought.

What a build does about a condition
----------------------------------

`--conditions=check|ignore|observe`, which is g++'s `-fcontract-evaluation-semantic` and is where the implementation of it is
worth writing down.  The three are decided in the **checker** and not in the backend: `ignore` emits no `AssertInst`, `observe`
emits one with a flag on it, and `check` emits the same one every other check of the language uses.  So nothing downstream has to
know what the command line said -- a module reaching a backend from anywhere carries what it needs, and `observes(module)` asks the
instructions rather than the options.

**The observing path reuses the helper a failing test says so through.**  `__pl4g_report` writes and returns, which is what
`observe` needs and is why nothing new had to be written for it.  Two things had to change around it:

- **It is emitted where anything observes** and not only where a test runs, which is one condition in three `target.py` files.
- **The call is given a clobber list.**  The stopping path calls `__pl4g_abort` with none, which is correct because that call never
  comes back: what it destroys can never be read.  An observing call *does* come back, so it is given everything the convention
  lets a call destroy -- through the same `destroyed_by` the ordinary call path uses -- and the register allocator keeps nothing
  live across it.  A build that observes therefore spills around every observed check.  That is the right way round: it is the
  semantic a reader asked for and not the one a program ships with.  Getting it wrong would be a silent miscompile, which is what
  one of the language tests exists to catch -- a value live across an observed check, read twice after it, on all three targets.

**`⎕entry` needed nothing at the machine level.**  A parameter's value at entry is the entry block's parameter, which SSA already
keeps: a body that binds the name to something else does not disturb it, and a body that takes a reference to the parameter gets
storage for the *name* while the value that arrived stays what it was.  So the checker records what arrived, `⎕entry(a)` answers
that value, and what it costs is one live range the allocator prices like any other.  Nothing is copied and nothing is stored,
which is what restricting it to a parameter's name buys.

**The purity note had to learn a family.**  A condition is lowered with the purity rules in force whatever the function may do,
and the note saying why was hung on every error raised while that lasted -- so a misspelled `⎕entry` in a clause was told that it
was a condition and might be compiled out, which answers a question nobody asked.  `DiagEngine.because` now takes the errors the
note belongs to, and the note is hung on those four and no others.

What a program is built for
---------------------------

One option, `--mclevel=NAME`, and three architectures that answer the question in three different ways.  **The name is
interpreted by the target and by nothing else**: what may be asked for is that architecture's own business, so the driver hands
the name over and is told what is wrong with it (1011).  A target that can be built for only one thing has no such method at all,
and asking it for a level is reported as asking for something with no meaning (1012) rather than for the only thing there is.
AArch64 is that target today.

**x86-64 has four names.**  "x86-64" has meant several quite different machines over twenty-five years, and the architecture's own
documentation names four sets of features -- `x86-64-v1` through `x86-64-v4` -- for saying which one a program was built for.
`v4` is what a program gets unless it says otherwise, because a program that will not run says so the moment it is started while
one built for the oldest machine quietly leaves everything on the table.  Two things depend on it: how wide a register holding a
run of elements is, and whether the instruction that rounds a floating-point number is there at all.

**RISC-V has a list.**  Its base is deliberately small and everything else is an extension a particular implementation may or may
not have, so there is no list of four to choose from -- what a program is built for is the set of extensions it may use, and the
architecture gives that set a spelling: `rv64gc`, `rv64imafd_zicsr`, `rv64gc_zba_zbb_zbs`.  `--mclevel` takes one of those, and
`isa.py` reads it: the base and its width, a version after anything that wants one, single-letter extensions in any order with
underscores between them meaning nothing, multi-letter ones separated by underscores because `zbazbb` would otherwise be a name,
and `g` standing for the general-purpose seven.

Writing the list out is precise and nobody wants to do it, so the architecture also publishes **profiles**: `rva23u64` is "what a
64-bit application processor of 2023 has", under a name a person can hold in their head.  Those are taken too, with the short
`rva23` meaning the user-mode one -- which is the one a program is built for.  **`rva23` is the default**, and that is what
settles that floating point is there: this architecture's Linux ABI has required the F and D extensions since the beginning, and
a default of the bare base would have had the compiler refuse the arithmetic every program on it actually uses.

**What the string may say that this target cannot be is refused** (1011), and there are two such things.  The **width**:
`rv32gc` names a machine whose addresses are half as wide, and that is a different target rather than a different level of this
one.  The comparison is against what the target says its own addresses are, so the one line refuses `rv64` on a thirty-two bit
target of the same family the day there is one, without anything being written twice.  The **reduced base**: `rv64e` has sixteen
registers and a calling convention of its own, and this compiler's register file and convention are the full ones.  The parser
understands both, because reading a name and being able to build for it are different questions and a compiler that confused them
would report the wrong one.

**An extension the compiler does not know is refused.**  That is what every other compiler's `-march` does and the reason is a
typo: `zfaa` is not an extension, and a compiler that shrugged at it would silently build the slower program.  The table holds
the single letters, everything the profiles name, and everything the code generator asks about; a new extension is one row.

**What the code generator asks it is a much shorter list than what it reads**, and that is deliberate -- a program naming an
extension for the sake of a later compiler is not refused by this one.  Today it asks two things.  **`d`**, without which
floating point is refused outright (8503): emitting the instructions anyway is a program that does not run, and doing it in
software is a different calling convention and so a different ABI.  The header's flag word follows the same answer, saying the
double convention where the extension is there, the single one where only `f` is, and the soft one where neither is -- which is
what that field is for.  And **`zfa`**, which has the instruction that rounds a floating-point number where it stands; without it
the same answer costs a round trip through an integer, a comparison and a branch, which is eight instructions where the profile
gets one.

**A program says at its own entry point whether the processor can run it.**  Before the constructors, before the startup function,
a program built for anything but the oldest level asks the processor what it has; where it has not got it, the program writes one
line to standard error and exits with status 1.  That is the difference between a sentence and an illegal instruction in the
middle of somebody else's afternoon.

- **`CPUID` is what asks**, which is the instruction the architecture provides for the question.  It is answered the same way on
  every operating system, it needs nothing to be mounted, and it cannot be out of date about the processor the program is
  actually running on -- which a file the kernel wrote can be.
- **One `CPUID` per leaf**, its answer masked down to the bits the level wants and compared against that mask: all of them or
  none of it.  The leaves and the bits are in `levels.py`, written out with the names the architecture's tables use, and nothing
  about which bit means what is decided anywhere else.
- **A leaf is asked to exist before it is asked anything.**  Leaf zero answers with the highest ordinary leaf and leaf
  `0x80000000` with the highest extended one, so each is asked once first; a processor old enough not to have leaf seven is old
  enough not to have what leaf seven would have reported.
- **It exits rather than trapping.**  Nothing has gone wrong inside the program -- it is the machine that is wrong for it -- and a
  signal would say that something had.

**v1 emits nothing**, every x86-64 processor having v1 by being one.  What the others cost is a few hundred bytes run once, which
`test_asking_the_processor_is_what_a_level_costs` states as a number so that a change to it is something somebody chose.  The
tests that are about how small an image can be, and the golden assembly dumps, are built at v1 for the same reason: what they are
about is the program, and the same forty lines repeated in each of them would bury it.

**A RISC-V image says what it was built for, in a section of its own.**  It has to: "a RISC-V binary" says almost nothing about
what a processor must have to run it, and unlike x86-64 there is no instruction a program in user mode can ask the processor
with.  So the answer has to be in the file, for a debugger working out which instructions to expect, a linker checking that two
objects were built for the same machine, a packager checking that what it ships will run.

The section is `.riscv.attributes`, of the architecture's own section type, and it is not mapped -- it is for whatever reads the
file and takes no room when the program runs.  The format is the one ARM invented for the same job: a byte naming the format,
then a sub-section per vendor, then a sub-sub-section per scope, with **every length counting itself**, which is what lets a
reader step over a vendor or a tag it does not know rather than give up.  That is why it is built from the inside out, each length
written once what it measures is there.

One tag is written, `Tag_RISCV_arch`, and its value is the **normalized** string: every extension spelled out, in the
architecture's order, each carrying the version it is at -- `rv64i2p1_m2p0_a2p1_f2p2_d2p2_c2p0_zicsr2p0_…`.  Normalized rather
than canonical, and the difference is the point: nothing is left implicit, so an extension another brings with it is written
beside it, and nothing is left to a reader's idea of which version was current.  A reader comparing two images only works if the
two agree exactly, so **the string is checked against what the GNU assembler writes for the same request**, over a corpus of
thirty strings including the default profile.  That is the same differential test the instruction encodings get and for the same
reason: the rules are spread over a specification, a profile document and forty years of extension names, and the only way to be
sure of them is to ask something that already implements them.

The other tags are not written.  The stack alignment and whether unaligned access is fast are things this compiler does not vary;
which privileged specification the system follows is a thing about the system.  Writing a value nobody chose would be stating
something nobody said.

**The check is what makes a level mean something.**  It was written before anything generated depended on a level, so that when
something did -- the width of a run register, and now the rounding instruction -- there was one place for it to go rather than a
new question to answer.  RISC-V has no such check and can have none: there is no instruction a program in user mode can ask what
the processor has, which is why what a RISC-V program is built for has to be told to the compiler and cannot be found out by the
program.


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
allocator decides.  The method is linear scan: every instruction is given a position, each register gets the hull of the positions
at which it is live, and the ranges are walked in order of their start, a unit being held for as long as a range needs one and
released as soon as it ends.  That is much less than a colouring allocator does, and it is the right amount here: what it gives up
is the ability to say that two ranges which overlap are never live at the same moment, which costs a register now and again and
never costs correctness.

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

**One split is made, around a call.**  A value live across a call needs a register the call leaves alone, and a convention has five
to a dozen of those against twice as many it does not; so in a function that calls something on one way of a branch inside a loop,
the values the loop reads are the ones that run out of registers, and spilled whole, each of them is read from the frame on every
turn -- the turns that do not call paying for the ones that do.  Where the scan gives up a value that lives across calls, and the
calls are made less than half as often as the value is read and written, the value is put in its slot just before each such call
and read back just after, and is in an ordinary register -- one a call destroys will do -- everywhere else.  How often is the one
question the depth of the loops cannot answer, a block behind a test in a loop being as deep as the test, so it is asked of
`block_frequencies` (`mc/loops.py`): ten turns of every loop as before, and either way of every branch alike, the ways that stay in
a loop sharing a turn and a way out of one taken once whichever turn takes it -- the uniform guess LLVM's block frequencies begin
with where there is no profile.  The guess decides that and nothing else: which value is given up is still the price above.  A
value split so and still in the way in the next round goes to the frame everywhere, in the slot it has, what put it there across
the calls taken out again; a call that destroys nothing -- the one to the fault path, which does not come back -- is no reason to
move anything.

**A value moved to or from another is given the other's register**, where that is free at every point the value is live, after the
hint and before the order.  The moves the builder writes are mostly between values whose lives touch end to end: a block's
parameter and what each branch hands it, the operand an instruction writes over and what it writes.  So the copies a loop's back
edge makes into the values it carries become moves of a register to itself and go, and `w ← w × 10 + d` is a multiplication and
an addition into the register `w` is in rather than both into a temporary and a move back.

A value read again by the instruction straight after the one that read it is not read from the frame twice: the register it is
already in serves both, and the register is held across exactly as many instructions as are reading it and no further.  The same
rule covers a value read straight after it was computed, which is used from the register it was written from rather than read back
at once.  That is as far as splitting a range goes here; keeping a value in a register across instructions that are *not* reading
it would need a cost model, since the register held is one another value cannot have, and the weight below is what such a model
would be built on.

Spilling is done by rewriting and starting again rather than by patching the assignment as it goes.  A spill adds instructions,
which moves every position after it and so changes every range, and recomputing is simpler than repairing.

**The value given up is the one with the lowest price**, which is what spilling it would cost divided by the register time it
gives back.  The cost is every read and every write of it, each weighed by ten to the depth of the loops the instruction stands in;
what it gives back is how many positions the range covers.  Linear scan was described with "the range that reaches furthest",
which is that measure with the cost left out -- and left out, it names the value a loop carries, since a value read every turn and
read again after the loop is precisely the one that reaches furthest.  The reload the spill puts in then runs on every turn.  The
incoming range is a candidate like any other, so where it is the cheapest it is the one that goes and the unit stays where it is;
where two are worth the same, the one reaching furthest goes, which is the old rule kept as the tie-break it always was.

**What a loop is** is asked of the machine graph and not of the front end: an edge to a block that dominates the one it leaves is a
back edge, the loop it closes is that block together with everything reaching the latch without passing through it, and a block's
depth is how many such loops hold it.  By the time the machine function exists there is no `while` left, only blocks and edges, so
a loop lowered from something else counts as much as one written down.  A block the entry cannot reach is at depth 0: nothing runs
there, and weighing code that does not run says nothing.  Ten to the depth is the weight priority-based colouring was first
described with; it is a guess at how often an instruction runs and deliberately a crude one, since nothing here knows how many
turns a loop takes and the only thing the weight has to get right is that inside is worth more than outside.

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

**An array written down is lowered once, and where its type comes from decides how.**  Where something says what type is wanted
-- a name of an array type, a parameter, an element of an array already settled -- the elements are that type's element type, and
`_fill` lowers each of them into it as it writes them into the run.  That is what lets a literal with no suffix stand as an
element.  Where nothing says, as in `⟦1u8, 2u8⟧⟦0⟧`, the elements are the only thing that can say it: `_array_written` lowers
them to ask, and hands what they came to on to `_fill`, which writes those values rather than lowering the same expressions
again.  `_shape_written` gives them in row-major order, which is the order `_fill` walks, so the place a value has in the list is
the place its element has in the run -- and a call written as an element is made once.

An array of no stated length wanted of a written array is told to the writing rather than worked around it: `_spread` hands the
wanted type down when what it is lowering is a written array, so the elements take their type from it, and lets go of the length
afterwards exactly as it does for a name.  Both of these were crashes before -- `_array_written` settles a type before there is
anywhere to put code, and asked the elements anyway.

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
| a pick, `a⟦m⟧` | a place in the array | it is short of the places the mask covers | the element, or the row | one place on |

**A pick asks a fourth question**: whether the place the walk is at is one to give a turn for.  It is asked once there is another,
in a block of its own between the test and the body, and a place not picked branches straight back to the test with every carried
local as the test left it and the walk one place on.  `_Iteration.wanted` is that question and `passed` what the walk moves to
when a place is stepped past -- the step, except under `⎕enumerate`, where a place stepped past is not a turn and so is not
counted.  Telling a pick in a loop from one anywhere else needs the expression's own lowering, since what says it is a mask is the
index's type: `_iteration_of` names the expression it is lowering in `_pick_in`, and `_lower_element` leaves the array and the mask
in `_pick_walked` rather than building the array when the expression it is lowering is that one.

**A mask asked a place at a time** is what `_pick_per_place` makes of a mask written as operators over arrays, values marked with
`¨`, and calls handed one (`_walks_per_place`).  It walks the mask's syntax once, before the loop: every array an operator is
given and every value marked is lowered and kept under a name `¨` makes up (`_each_name`, a name no program can write, since
`¨` is no letter), everything else is lowered and kept the same way, and two expressions come out -- the mask for one place,
where those names stand for what is at the place, and the mask for the whole, where they stand for the arrays.  Where everything
walked is one dimension, `_pick_walked` holds a function that binds the names to what is at a place and lowers the first
expression; `_Iteration.wanted` calls it.  Otherwise the second expression is lowered once, as a `_Ready` index, and the loop
goes on as before -- which is also what happens where nothing is walked at all and the "mask" was an index all along.  A record at
a place is bound as the place (`_bind_element`), so that a field of it is read from where it is rather than the record copied
into the frame to have one field read out of it.

`¨` everywhere else is three lowerings over one idea: what is done to each element is written out as syntax, with a made-up name
standing for the element, and lowered once per element by the code that lowers it anywhere.  `_marked` takes a value apart into
what is walked, how deep, and a function writing the chain after the mark around any expression.  A read (`_lower_each`) and a
call (`_lower_each_call`) both end in `_each_built`, which unrolls the walk over the stated shape as `@[listable]` does, lowering
the first element alone so that a mistake is reported once, and storing each answer into frame room taken once the first says
what the answers are.  A write (`_assign_each`) is a `foreach` over a range of `u64 ¤idx`, built as syntax and lowered by the
loop's own code: the place written is named again each turn, `B⟦i⟧`, with an `if` around it where a mask picks, which is why
the place has to be something naming does nothing to.

**An iterator bound to a name** is an `_Iterable` on the `_Local` rather than a value: the element type, what was written, the
array or value walked, its type, the mask (a value, or `_pick_per_place`'s function), and the names lent.  `let` with no type and
`let q: ⇉…` lower the value with `_pick_in` set to it, so a pick leaves its pieces in `_pick_walked` instead of building the array,
and a name bound to an iterator does the same when it is named where a loop, or another definition, takes it (`_iterator_named`).
A walk of it is `_walk_of`, built afresh each time from what was kept -- which is what makes it a description.  Where an array is
asked for, `_built_from` builds it with `_lower_picked`, building the mask first by asking it a place at a time where that is how
it was described (`_mask_built`, which needs the array's shape stated).  The names it reads are kept in `_iterator_loans` with the
depth of its scope, dropped by `_pop_scope`, and asked by `_lent_to_an_iterator` from `_record_may_change` (every element and field
write), an assignment, and an array handed to a `mut` parameter.

**`let a: ⟦⟧ = …`** is an `ArrayTypeRef` whose element is nothing, which the parser makes only where `_parse_variable` reads a
definition's type (`_parse_type_ref(definition=True)`) and refuses anywhere else (3053) -- a lift tries a type first, and the bare
brackets there are an array literal.  `_lower_array_definition` lowers the value with `_pick_in` set, builds what a walk gives with
`_built_from`, and holds the array's rank and stated dimensions to the ones written.

**`⇉T` as a value** (`IterType`, three words, laid out as a function value is) is made by `_handed_on` where `_lower_into` is asked
for one -- an argument of a `⇉T` parameter, and of a generic `⇉T'` one, which `_lower_generic` asks for `⇉` with the element left
open and `_reading` reads `T'` off.  What is walked is written word by word (`_words_of`: an array of a stated shape as where its
elements are, anything else as its parts) into a record in the frame; the value is the code of a `⎕nextN` function, that record,
and the walk's first state, which `_Iteration.start` must be one `u64` of.  `⎕nextN` (`_next_function`, one per shape of what is
walked) takes the record, where the walk's state is and where to write the element, reads the record back, builds the same
`_Iteration` the loop would have -- a pick's without the shape checks, made when the iterator was -- and runs one turn: past the
places not picked, then the element written word by word, the state moved on, and `true`; `false` where there are no more.  The
element comes back through memory rather than as a result because a result of a string is four words, more than an indirect call
answers in registers.  `_over_an_iterator` walks a `⇉T` value: the state and the element each in the frame, and a call per turn.

Everything that does not change from turn to turn -- where an array's elements are, how many there are, where a table's entries
are -- is worked out once before the loop and read from where it was left.  That is what keeps a turn of an array to a comparison,
an addition and a read.

**A table's walk steps past the places holding nothing**, and finding the next one is a function of the runtime rather than a
second loop written into this one.  That is what keeps the shape above the shape of every iterator: a `more` that had to search
would have to hand the body what it found, and there is nowhere in the shape for it to put that.

**A dictionary gives a tuple**, and two names take a tuple apart everywhere a tuple is bound.  So `foreach k, v := d:` needed
nothing of its own: the tuple is made and the binding that already existed takes it apart.

A tuple handed over as several arguments
---------------------------------------

`⁂x` is expanded in the checker, before anything is counted or typed.  `_pieces_of` walks a written list and answers a list of
`_Piece`, one per value the list actually comes to: an ordinary entry contributes itself and nothing more, and a spread one is
lowered once by `_taken_one_each` and contributes one value per thing its type counts.

**Two lists admit it**, and the same code serves both: a call's arguments, and a tuple's members.  That is the whole of the
tuple case -- `_lower_tuple` calls `_pieces_of` and then does what it always did -- and it is why the parser has one
`_parse_spreadable` that both `_parse_call` and `_parse_tuple` use, and the grammar one `_spreadable` rule that
`call_expression` and `tuple_literal` both name.

**Two types are several values the compiler can count.**  A tuple's members are already held separately, so it is taken apart
with one `extract` each.  A fixed array's elements are one run in memory, so they are read: `_shape_of` gives where they start,
and each element is a `load` through `_element_place` -- the instructions writing the indices out would have produced.  An array
of rank greater than one gives its outermost dimension through `_row_at`, which is arithmetic on the place and no copy, and
matches the rule `foreach` follows.  An array whose type does not say its length is refused (4465): the expansion is written into
the program being compiled and the length is not known until it runs.

Lowering the operand *once* is the point of the `_Piece` record.  An entry is normally lowered where it is wanted, so that it is
lowered exactly once; a spread operand has to be lowered earlier, because how many pieces there are is a question about its type.
The record therefore carries the expression for an ordinary entry and the already-lowered value for a spread piece, and the
caller lowers what has not been lowered yet.  Writing `⁂f()` calls `f` once.

Everything after the expansion sees a plain list and does not know how it was written.  The arity check, the per-argument type
check, the conversion of an unsuffixed literal to its parameter's type and the register assignment all run on that list, so a
spread call and the call written out are the same call in the IR; a tuple made with a spread has the type it would have had
written out.  No existing diagnostic had to learn about the glyph.  The four that are new are about the glyph itself: what
follows it is one value (4464), an array whose length is not in its type (4465), a tuple left with no members (4466), and the
glyph standing where no list is (3031).

**The expansion happens in the same pass that lowers.**  `_one_by_one` walks a written list once, left to right, lowering each
entry where it stands: a spread is taken apart in its turn, and an ordinary entry is lowered into whatever the place it has
landed on wants.  Which place that is, is known because everything to its left has already been counted, which is what lets an
unsuffixed literal still take its parameter's type.

An earlier version ran two passes -- every spread first, then everything else -- which made `f(g(), ⁂h())` call `h` before `g`.
One pass is what the specification's left-to-right rule requires, and it needs nothing the two-pass version had: the count is not
known before the arguments are lowered, so the arity check simply moves after them.  What that costs is that a call with the
wrong number of arguments now lowers the ones that have a parameter, so a mismatch in one of those is reported beside the count;
both complaints are true of the call.

**The last of those is the parser's and not the checker's.**  `⁂` is admitted only in front of an entry of one of the two lists,
which is what the tree-sitter grammar says too.  The first implementation made it an expression and refused it in `_lower_expr`;
that put a rule in the checker the grammar could simply enforce, and it made the two grammars disagree about what an expression
is.  The diagnostic moved to the parser and changed number with it.

What is wanted, travelling down
-------------------------------

`_lower_expr` takes what is wanted of an expression, and where that is a result type two rules decide what each expression does
with it:

- **`_aiming_at`** answers the result's answer type, and is what something that can only ever be an answer asks: a literal,
  an operator's two sides, the members of a tuple or an array or a collection, what a `break` hands over.  None of those can be a
  result of its own, so where one stands and a result is wanted, what is wanted of *it* is the answer.
- **`_accepts`** is what every check asks instead of comparing types directly.  A value of the result type stands, and so does
  one of its answer type -- `_lower_into` is what makes the second into the first.

Before this, `_lower_into` handed *nothing* down when a result was wanted, so that a plain value could come back and be wrapped.
That worked for everything with a type of its own and failed for the one thing that has none: a literal with no suffix, in every
position a result is wanted -- a definition, an assignment, an argument, what a function answers with, an `if`'s arms, what a
loop comes to.  Handing the whole type down and splitting the question in two is what fixed all of them at once.

**`mem.start` goes at the top of the entry block.**  It was appended wherever memory was first asked for, which could be inside
an arm of an `if`; every later use of memory then read a value that arm does not reach, and the branch into a loop written after
such an `if` failed the verifier's dominance check.  It says nothing and depends on nothing -- it is where the chain begins,
which is where the function begins.

**A block expression takes no postfix.**  An `if`, a `match` and a loop end where their body ends, and in the layout notation
that is a place with no line ending after it, so the `(` beginning the next statement was read as a call on what the block came
to.  `_parse_primary` stops at such an expression instead.

Leaving a loop and repeating it
-------------------------------

A labelled loop is pushed on `_loops` for the length of its body, carrying the two blocks a jump goes to -- the header and the
block after -- together with the names the loop carries and, for a `foreach`, the state its iterator walks and the step that
advances it.  `break` branches to the block after; `continue` branches to the header with the step applied, which is what the end
of the body does, so the two produce the same instructions from the same pieces.

**The block after a loop takes parameters once the loop has a name.**  Before there was a jump it needed none: the test was the
only way out, the header dominated the block after, and what followed the loop simply read the header's parameters.  A `break`
gives it a second predecessor whose names are the ones at the jump, so the values now differ by which way was taken and have to
be handed over.

**A conditional branch carries nothing**, which is what the back ends require, so the way out of the test is given a block of its
own -- `leave` -- holding one unconditional branch that hands the header's parameters to the block after.  That is the one place
the loop's own parameters are what the exit reads.

**What the loop comes to is one more thing the exit takes**, added to that block after the body has been read rather than
before it.  Whether there is such a value, and what type it has, is not known until the breaks have been seen -- the loop's own
type says only where something already wanted one -- so the parameter cannot exist when the first `break` branches.  It does not
have to: a branch records what it hands over and a block records what it takes, and the two are matched when the function is
done.  The parameter therefore stands last, after the memory, which is the order the branches written before it hand things over
in.

**A loop with no `else` arm answers with a result**, and the two halves of that are made in the two places they belong: the
`break` wraps what it hands over into the result, and the way out of the test hands over the failure.  Nothing is done on the path
round the loop, and nothing is done in the block after it.

**The `else` arm is what the way out of the test runs**, so it is lowered into the `leave` block -- which is why that block is
filled after the body rather than immediately after the conditional branch.  The memory the arm starts from is therefore captured
before the body is lowered, the builder's own having moved on by then.  The arm is outside the loop, so it is lowered after the
loop's name has been taken down: a `break` in it would name a loop that has ended.  A name it assigns is one of the names the loop
carries, for the reason the body's are -- what follows the loop is reached through the arm as well as through a `break`, and the
two ways have to agree.

**A mismatch in what a loop comes to is said as one.**  `_as_the_loops_value` puts every other context aside while a `break`'s
value or the `else` arm is lowered, so a loop in a definition's initializer reports a `break` of the wrong type as a mistake about
the loop rather than about the definition.  That is the same mechanism `_operand_of` and `_handing_over` already are.

**Whether a body holds a `break` is not asked.**  A labelled loop gets the exit parameters and the `leave` block whether or not
anything jumps, because the question is about every place a statement can be written -- inside an `if` used as a value, inside a
`match` arm, inside a nested loop -- and getting it wrong means branching to a block with no parameters, which the verifier would
catch and a reader would not understand.  What it costs is one jump in a loop nothing leaves, and such a loop has a label nothing
names, which is reported (4469) rather than optimized.

**A label that is already taken is dropped rather than refused.**  `_label_of` reports 4468 and answers nothing, so the loop is
lowered without a name and its body is checked like any other; refusing the loop outright would hide everything else wrong with
the body behind one message.

The order things are worked out in
----------------------------------

The specification says left to right, everywhere several things are written in a row.  What makes that a claim about the compiler
rather than an accident is that lowering happens in one walk in written order, so there is one place per construct that could
break it, and each is written to walk once:

- a call's arguments and a tuple's members, through `_one_by_one`;
- an array's elements, through `_fill`, with what `_array_written` had to lower to learn the type carried forward rather than
  worked out again;
- a collection's entries, through `_entries_written`, which walks key, value, key, value and hands what it lowered to
  `_build_collection`;
- an operator's two sides, which `_lower_binary` lowers in that order, precedence having already decided the shape of the tree
  and not the order the walk visits it in.

Two of those were wrong before the rule was written down, and in the same way: something had to lower an expression to learn its
type, and something else lowered it again to use it.  A collection's entries were each worked out **twice** -- once by
`_one_type` to find what type they share, once by `_build_collection` to put them in the table -- so a call written as an entry
was made twice; and a dictionary's keys were all worked out before any of its values, an order nobody wrote.  Both are fixed by
lowering once and carrying the values, which is the same shape the array literal already needed.

The rule that makes this easy to keep: **a function that answers what type something has must hand back what it lowered.**
`_one_type` takes an `into` list, `_array_written` answers the elements beside the type, and `_entries_written` answers an
`_Entries`.  Anything that asks a type and throws the value away will lower it a second time somewhere.

Picking with a mask
-------------------

An array indexed by an array of truth values is picked from rather than indexed, and which of the two was meant is never a
question about how it was written: an array of numbers indexes and an array of truth values picks.  `_mask_written` lowers the one
index and looks at its type, and the reading and the writing then go to `_lower_picked` and `_assign_picked`.

**The answer's room is the whole array's and is taken as a `frame`.**  No more can be picked than there were, so the size is known
even though the count is not -- which is why the array picked from has to state its shape (4485) and why nothing is allocated.
What comes back is the ordinary dynamic-array value: the place, the count, and the lengths the mask said nothing about.

**Neither half branches**, and that is the same idea twice.  Picking writes each thing at the count and then advances the count by
the mask, widened from a truth value to a number: a thing that was not picked is written where the next one writes over it, which
is sound because the room is this call's own.  Assigning spreads the mask across the whole width of the element -- nought less the
truth value, so all ones or all zeros -- and writes `(old & ~m) | (v & m)` to every element, which is the old value or the new one
and costs the same either way.

**Both are written out, one element at a time**, for the reason the listable walk is: the shape is known, the count is not, and a
loop wants an index worked out while the program runs.  What it costs is code proportional to the array, which is the honest price
of the simplest thing that is correct.

**The spread trick wants an integer**, so an array of floating-point elements is refused for now (9902) rather than written with
branches.  Nothing else in the language needs a select instruction; adding one would make this shorter and would make a `match`
over two constants shorter as well.

**A shape stated in part** is what picking rows produces -- `u8⟦,3⟧` -- and is what the rule against half-told shapes had
forbidden.  The representation already allowed it: `ArrayType.shape` is a tuple of "how many, or nothing", `parts_of` gives a
count per dimension whether the type states it or not, and `_shape_of` reads them all from the value.  What had to change was the
resolution refusing it, and the decay checking that a length let go of is not a length changed: `_lets_go_of` says a dimension the
wanted type states must be the one it states.

Walking an array
----------------

An array handed to a `listable` function where one of its elements is wanted is walked, and the whole of that happens in the
checker: `_walking_shape` works out the shape and `_each_of` writes the calls out.

**The shape is worked out before anything is lowered for it**, because the room the answer needs is the whole of that shape and
is taken once, as a `frame`.  It is found by peeling: at each turn, every argument whose type is not yet its parameter's gives up
its outermost dimension, they must agree about how many that is, and the turn stops when no argument is left over.  What falls out
is the list of dimensions walked, which is the answer's shape, and the answer's elements are what the function answers with.

**`_each_of` then walks it**, writing each answer at the place row-major puts it -- the same arithmetic an array written down
uses, and for the same reason.  An argument already of its parameter's type is handed to every call as it stands; one that is not
gives its element or its row, which is `_row_at` again.

**The arguments are lowered with `_listing` in force**, which makes `_accepts` take an array wherever one stands.  That is what
lets `added(v, 10)` work: the literal still asks `_aiming_at` for the parameter's type, and the array is let through.  What is
let through permissively is then checked once, by `_walking_shape`, which is the only place that can say what is wrong with a
*walk* rather than with an argument -- a dimension the type does not state, or two arguments that disagree.

**An operator walks by being lowered again.**  Each operator's lowering pauses after its operands are lowered and asks
`_walk_operands`; where none of them is an array -- which is every operator on every ordinary value, and the path this must not
slow down -- it answers nothing and the lowering carries on as it was.  Where one is, the operator is lowered once per element
with a `_Ready` node standing where each operand was written: a node no parser makes, holding a value already worked out.  So
every check and every choice the operator makes -- the exact-float warning, the constant folding, the saturating forms, which
comparison predicate a signed type takes -- is made for each element by the code that already makes it, rather than by a second
copy of that code written for the walk.

What that costs is one line in each operator: the operand's context is asked of `_scalar_of`, so that a number written beside an
array takes the array's element type; `_listing` is in force while the operands are lowered, so that an array is let through
where one of its elements is wanted; and `_walk_operands` is asked once afterwards.  The two that answer a truth value had to
have their early check relaxed as well -- what is wanted of them may be an array of truth values, which is not known until the
operands are lowered.

**`and` and `or` do not walk**, and `_boolean` takes a flag saying which kind of operator is asking rather than accepting an
array everywhere: which side is worked out is what they are about, and over an array there is no such thing as which side.

**The calls are written out rather than looped**, one per element of the shape.  A loop would need the answer's storage and the
index to be worked out at run time, which is the same machinery a dynamic array wants; both are the same piece of work and neither
is here.  What it costs today is code proportional to the shape, which is why a shape the type states is required and not merely
convenient.

A mask, and a run of bytes compared at once
-------------------------------------------

`⎕mask(b)` is one more operation of the representation, `mask`, a unary one whose operand is a run of truth values and whose
answer is a whole number -- the one operation that reads a run and does not answer one.  Three places do the work: the checker,
which keeps the run it is handed from ever being an array; `settle_vectors`, which cuts the run as it cuts every other; and each
back end, which gathers a register's lanes its own way.

**The checker lowers what it is handed as a run.**  An operator walked over arrays answers an array in a frame of its own --
`_run_at_a_time` stores each run it worked out into one -- and reading thirty-two truth values back out of room taken to hold
them would be two copies of what is already in a register.  So `_lower_mask` puts the argument in `_as_run`, and `_lower_expr`
passes the mark on to the operands of any operator walked at once; `_walk_operands` then answers the run itself, recording it in
`_runs`, where the shape is one dimension.  A recorded run is walked as an array of that many lanes would be -- `_shape_walked`
counts its lanes, `_run_or_one` hands it on and forgets it -- and the one place it may meet that wants elements, an operator
walked an element at a time, is given an array after all by `_as_array`.  So `(c = 34u8) ∨ (c = 92u8)` is two comparisons, an
`or` and a `mask`, and no frame; samevalues then folds the two reads of `c` into one.

**A family of runs is cut as one.**  `settle_vectors` decided how to cut a run by asking the operation that read it, and gave the
answer to what the operation read; that was enough while every operation's answer went through a frame before the next read it.
A run that goes straight from one operation to the next has to be cut the same way by both, and a comparison of halfwords --
asked a lane apiece, there being no instruction answering a byte per lane -- feeding an `or` of truth values, asked sixteen at a
time, would not be.  So `_plan` joins every run to the runs the instruction making it reads, over the whole function, and cuts each
family by the operation in it that can do the fewest at once; a family with no operation is only moved, and is cut by the
register.  Over the function and not a block, so a run that crosses into another block is cut alike in both.  `_mask` then gathers
each piece -- a piece of one lane being a truth value, which is its own bit once widened -- shifts it to where its first lane is
and ors the pieces together.

**What a run of truth values is in a register is the back end's.**  In memory each is a byte of one or nought, as one truth value
is.  On x86-64 and AArch64 a register holds each as every bit of its lane set or clear, which is what their comparisons answer, so
`and`, `or` and exclusive or of two of them are the instructions a register already has and `¬`, an exclusive or with a run of
`true`, turns every bit; `run_truths` -- nought less each byte, which takes one to every bit set and back -- is what reading,
writing and spreading one value over a run do on the way.  x86-64 gathers with `pmovmskb`, the top bit of every byte, and clears
the bits beyond a run shorter than the register, a comparison having answered for the lanes beyond it too.  AArch64 has no such
instruction: the lanes are anded with their weights, one to a hundred and twenty-eight twice over and nought beyond the run, read
out of the pool of constants, and three pairwise additions leave each eight lanes' bits in a byte.  The unsigned orders on x86-64
are asked through `pminub` and `pmaxub` -- `a` is at least `b` where the larger of the two is `a` -- and turned round with an
exclusive or of every bit, which is three instructions against four for flipping each top bit and asking the signed order.

**On RISC-V a run of truth values is a mask register**, one bit per lane, which is what `vmseq` and the rest answer; `vmv.x.s` at
an element width of sixty-four reads the first sixty-four bits, and the bits past the run, which nothing promised, are cleared.
`and`, `or` and exclusive or of two masks are `vmand.mm` and the rest; a mask is made of bytes with `vmsne.vi` against nought, and
made bytes again by merging one into nought, which reads the mask from `v0` -- the one register a masked instruction reads it
from, never given out for that reason.  Every vector instruction does what `vtype` says, which no instruction carries, so each
group the selector writes begins with a `vsetivli`; within a block, a machine pass, `SaidOnce`, drops one that says what the one
before said, forgetting at a call and at `ecall`, after which the kernel promises nothing about vector state.  None of the
vector registers survives a call or a system call, which is what the convention and the kernel say.

**A RISC-V function that works on runs keeps `t6` to itself.**  A vector register is set aside in the frame by `vs1r.v`, which
takes its address in a register and no offset, so the spill needs a register the allocator has not given anything; and a run done
a lane at a time on `rv64gc` -- sixty-four bytes compared, widened, shifted and ored, all live at once -- makes a frame of a few
kilobytes, past the twelve bits a load or a store carries.  Which functions those are is asked before the runs are settled, since
afterwards a build without the extension has none left; every other function has every register it had.  The frame is then made
and given back with the size built in `t6`, and a slot past two kilobytes is reached through it.

**The registers a function keeps for its caller are saved as the convention keeps them.**  `_kept_registers` takes every unit of
the convention's `callee_saved` the finished code names, and the system's conventions on AArch64 and RISC-V now name the
floating-point ones as well -- `d8` to `d15`, and `f8`, `f9` and `f18` to `f27` -- so a `@[cdecl]` function saves those it used.
Each has eight bytes beside the ordinary ones.  The selector saves one with `select_keep` and puts it back with `select_unkeep`,
which are its spill and its reload unless it says otherwise: AArch64 spills a floating-point register whole, sixteen bytes, and
keeps only the low half, `str d8, [sp, #n]`, which is what the convention asks; RISC-V's `fsd` and `fld` are its spill and reload
already.  `keeps_all_of` says whether the whole register was kept, and only such a unit is in `MachineFunction.preserved`, which is
what a caller here reads (`clobbered_units`): a run of lanes in `v8` would lose its upper half across a call that kept the low one.
A call of the system's convention destroys, by its `caller_saved`, every floating-point register on AArch64 and all but the twelve
on RISC-V.  The language's own convention keeps none.

**A frame of any size works on AArch64 and RISC-V, and costs a register only where it is that large.**  A load or a store carries
twelve bits of offset on both -- unsigned and counted in what is read on AArch64, so 4095 bytes of a byte and 32760 of a word;
signed bytes on RISC-V, 2047 -- and so does what moves the stack pointer.  AArch64 moves it by twelve bits a page at a time and the
rest (`sub.lsl12`, `sub`), sixteen megabytes in two instructions and no register, and past that by a number built in a register and
the extended-register form (`sub.uxtx`), the one that reads register 31 as the stack pointer where the shifted-register form reads
it as nought.  A place in the frame no offset reaches is built in a register: in the destination, for a load into an ordinary
register, which is about to be written anyway; otherwise in the register the selector names, `x16` on AArch64 -- the first of the
two the standard convention leaves to the linker's veneers -- and `t6` on RISC-V.  **Which functions set it aside is found by
allocating:** the size of a frame is known only once the allocator has said what it spilled, and what it spills depends on how many
registers it had.  So every function is allocated with every register, and where a selector, once the frame stands, finds a place
out of reach with no register set aside, it raises `FrameOutOfReach`; the builder puts the function back as it was before
allocation, takes the register out of the order, tells the selector it has one, and allocates again.  A frame that large can
afford the second allocation, and every other function keeps the register.  A place of the lowering's own -- a `frame`'s address,
the stack pointer and a constant -- is a value the lowering computes, built a page at a time on AArch64 and with the number in a
register of its own on RISC-V, and asks for nothing set aside.

**A view given to an array whose type states its shape** goes through `_copy_elements` as an assignment does, which compares the
counts while running.  `_move_elements` asks a view of one dimension whether its stride is one before copying up to
`_AS_ONE_AT_MOST` bytes, and where it is, reads the whole block as one value and writes it as one; the loop is for the view that is
not one run, and for a block larger than that, which is code that does not grow with the count.


What a function may change
--------------------------

`FuncAttrs.impure` says whether a function may change what outlives the call, and everything that would make such a change goes
through `_an_effect`, which reports where the function being lowered did not say it may.  One list rather than a rule each place
remembers: a variable at the top level written, memory the function did not make written, a collection made, and a call to a
function that may do any of those.

There is a second attribute beside it and the two are not the same question.  `answer_in_storage` says the function writes the
place its caller handed it, which is what the pass that moves a large answer out of the registers leaves behind.  That memory does
outlive the call, so a caller may not move the call or make it twice -- and it is all the function writes, so a caller that reads
none of that place may drop it.  Impurity is what a program says about a function; this is what a pass did to one, and saying so
separately is what keeps a program's own promise from being silently taken away from it.

**What counts as "memory it did not make" is asked of the value, not of the syntax.**  `_made_here` walks a place back through
the casts and the arithmetic that named it: a `frame` is storage this call made and will lose, and anything worked out from one
still is.  Anything else -- a variable at the top level, a parameter the caller handed over, something read out of memory -- came
from somewhere that outlives the call.  Asking it this way means a row of a local array, an element of one and a slice of one all
answer correctly without any of them being listed.

**`CallInst.has_effects` asks the callee**, by attribute rather than by type, so a callee that is not a function this module knows
says nothing and what it does not say is assumed.  That one line is the whole of the optimization: dead-code elimination already
drops an instruction that nothing uses and that has no effects, so a pure call whose answer nothing reads goes with no pass
knowing what purity is.

**A string and a list are three words**: where, how many, and the allocator -- a pointer to the allocator's state, nothing for
text in the image or the kernel's.  Every function answering one goes through the caller's storage (`largeanswers`), the style
answering two words in registers.  **`⎕heap` is in the runtime** (`pl4g_heap_alloc`, `pl4g_heap_free`); `__pl4g_allocate` and
`__pl4g_dispose` pick the kind by comparing the allocator with `⎕heap`'s address, and where the compiler knows the allocator it
calls the heap straight away (`__pl4g_heap_new`, `__pl4g_str_join.heap`) or, for a pool, emits nothing.  A heap container gives
back what it replaces through `__pl4g_disown.T`, which asks each element for its allocator.  What a value was made in carries
two kinds of key besides allocators: one naming a heap container, and one naming "read out of that container", which is what an
element replacement kills.

**An arena handed out lives in the caller's frame.**  A function with `→ T in pool` for no parameter (`Function.hands_out`,
`FuncType.hands_out` for a value) takes one parameter more, last: a `ptr<mut arena>`.  `let pool: mut arena = ⎕arena` in the body's
own scope is lowered with that pointer as its place instead of a `frame` slot, and the local is marked `handed_out`, so the body does
not give it back.

**A delegate is handed what the walk is resumed with.**  `⎕resume` writes it where the generator's own body reads it; a delegation
record has a fifth word, where the delegate reads it in its own argument record, and the driver (`_generator_next`) copies the
words there before resuming a delegate.  **What a generator answers when it ends** has room after that in its argument record
(`_finish_place`); `return v` writes it, and `yield ⇉` lowered as a value (`_lower_delegated_value`) reads it out of the record the
delegation was made with, which `_delegated` leaves in `_last_delegation`, once the delegate has nothing more.  A body's last
expression is that `return`.

**A task's read keeps its bytes in the ring.**  `pl4g_io_read_kept` takes room of the heap's laid out as a list's -- the sixteen
bytes `sema/lists.py` writes before the elements, holding how many there is room for -- submits the read into it, and marks the
slot `handed`, which `give_back` leaves alone.  `std.run`, right after reading a slot's answer and before anything can take the
slot again, asks `pl4g_io_taken` for where the bytes are (nought for any other request, and the room given back where the kernel
refused) and hands it to the task in `io.Answer.at`; `std.received` adopts the room as a `[u8]` with `⎕span(at, n, ⌜[u8]⌝)`, which
makes the three words of a list of the heap's.  A RISC-V call the C compiler did not inline is `R_RISCV_CALL_PLT`, patched as one
fixup of two words (`fixups.CALL`).

**`@[opaque]` is a flag of its own**, `FuncAttrs.opaque`, printed and read back as `opaque` in the IR.  `CallInst.has_effects` answers
yes for it, which is the one question every pass that would fold, merge, move or drop a call asks, and the sweep's rule for a call
answering through storage asks it as well; the attribute sets `inline(never)`.  A function making a call to one is set `opaque`
in turn, in `_what_it_changes`, without `inline(never)`.  Nothing in the checker reads it as `impure`.

**A list handed to a parameter the callee changes in place is copied by the caller.**  `Function.takes_owned` lists the `mut`
parameters holding a list that the body changes in place (`_takes_owned`); the body binds those as they arrive, and every direct
call runs its arguments through `_owned_arguments`, which copies into `⎕heap` (`_copy_into_heap`) unless `_movable` says the caller
is done with the variable -- not a parameter, `mut`, not in `_addressed`, named once in the statement (`_mentions`), and either the
name the statement assigns or not live after it.  A function value of such a function always goes through its shim, which copies.
`_answered` hands such a parameter back as it is where its allocator is the heap (`_heaps_or_copied`, a comparison while running)
and copies it otherwise.  A lambda still copies on the way in, being called only through values.

**Whether a mark is needed (4933) is a `_Marks` record per definition**, which `_lower_body` and `_lower_instance` make current in
`_marking`.  `_an_effect` notes every effect there before deciding whether to report it -- as the body's own, or, given `through`,
as a call to a marked callee -- and `_what_it_changes` notes every call to a function that does input or output.  `_check_marks`
runs once every body of the unit is lowered and nothing else was reported: a least fixpoint over the calls, a callee with no
record taken to need what it says.  A function type's slots (4935) join the same fixpoint: `_slot` registers a parameter, a
variable, a field or an answer by a key, `_flow` notes what each is given as a source (`_source_of`: another slot, a named
function, a lambda's record in `_lambda_marks`, or nothing known), and a call through a variable bound to a slot is a call to it
rather than an effect of the caller's own.  A slot is needed where it is in `_slots_outside` or any source may change things.

**An arena a body made is given back by a `defer` the compiler writes.**  `_give_back_on_leaving` appends a `_Deferred` holding
`⎕empty(a)` right after `let a: mut arena = ⎕arena` is lowered -- also for the arena `let v: T in kept = f(…)` makes, and not for one
the body hands out -- so every way out of the block runs it, in the order `defer` already has, with everything `defer` already does
to what was made in the arena along that way.  The call's identity is kept in `_implicit_empties`; a `⎕empty` the program wrote is
not among them and is refused (4932).  With nothing else giving an arena back, the check that a pure function gave back what it
made, and the ones for giving back somebody else's arena, are gone.  `let v: T in kept = f(…)` lowers `let kept: mut arena = ⎕arena` first,
then the call with `kept`'s place waiting in `_receiving`; only the call that is the definition's whole value takes it
(`_arena_for`), and the answer's provenance is `kept`.  `made_in` is `(HANDS_AT,)`, so the answer may be thin with the caller's
place as its allocator.

**A call of a function to itself in tail position is a jump** (`opt/passes/tailcalls.py`, the first pass after `dropignored` at
every level, before `largeanswers` could put the answer in the caller's storage).  Tail position is read off the IR the checker
writes: the call and then the `ret` of its answer, or the call and then a branch to blocks that do nothing but hand on and return
it.  The function gets a new entry block that only branches to the old one, whose parameters -- and a memory token, where the
function touches memory -- become the loop's; each tail call becomes a branch to it with its arguments and the token in force at
the call.  A call handing over anything made from a `frame` instruction is left alone.  The checker keeps the copy an answer may
need into its allocator inside each arm of an `if` or a `match` that is the answer (`_answer_arms`), and answers a call of the
function to itself as it is, so that the call stays last.

**What the checker writes once per mention is worked out once** (`opt/passes/samevalues.py`, at `-O1` after the first folding).
Value numbering down the dominator tree (`opt/flow.py`, Cooper, Harvey and Kennedy's immediate dominators): an instruction with no
effect that computes what a dominating one computed -- the same opcode, type, operands and attributes -- stands for it.  A frame
and the memory chain's start are never shared; a call is left to `purecalls`.  A load is shared only where no call, store or
request to the kernel lies on a way between the two, since the chain cannot be trusted yet: a call is not in it, and a body
inlined in place of a call keeps its own.  A value is shared only from a block laid out before the one reading it, because the
three instruction selectors read the blocks in order and want a value worked out before it is read; a `foreach` over a pick lays
its `pick` block out before its body for that reason.  `constfold` drops what leaves a number unchanged (`x × 1`, `x + 0`, `x | 0`,
`x & ~0`, a bitcast to the type it has) and answers a comparison of a value with itself for integers, truth values, characters and
pointers -- not for floating point.

**What a branch settled, and what a value can be, is used where it holds** (`opt/passes/facts.py`, after the second folding; the
C++ is `opt/facts.cc`).  A function holding a check, a checked or saturating operation or a comparison of whole numbers is walked
in reverse postorder, each block with a `_Known`: its dominator's, shared where the block learns nothing, copied where it does.
**Facts** are learned on the edge into a block that edge is the only way into (`_facts_of` of the branch's condition, the
unit-only casts between `u64 ¤idx`, `u64` and `u64 ¤size` looked through by `_plain`, `∧`, `∨` and `¬` looked into), after a
check that does not only observe, and -- `_joined` -- where the branch is on a truth-value parameter of the block it ends and only
one way in can hand it the value the edge needs: that way's facts at its end and the argument's.  A `_Known` holds the facts as a
set; the values each is known to differ from; and `bounds`, `x - y` at most a number for the values `_offset` finds under each side
-- a value and a constant added by `add`, `sub`, or a wrapping one whose range says it fits -- the tightest kept, indexed both ways.
**Ranges** are `(lo, hi)` in the value's own reading, `_reread` between signed and unsigned (a range whose top bits differ reads as
every value).  `_range_of` works one out per instruction from its operands' ranges at that point (`_at`, which narrows by
`_narrowed`: each bound of the value or of what it is a constant away from, read in the bound's domain, then the values it differs
from moving an end); `_hand_on` widens each parameter of a successor by its argument narrowed on the edge, `_widen` taking a bound
still moving after `_WIDENINGS` (one) growth to the end of the type.  The function is walked again while a parameter of a block
already walked changed, at most `_ROUNDS` times (forty; a function not settled by then is left as it was), and **what the last
walk found is changed**: a check `_answer` says holds goes; `add`, `sub`, `mul`, the saturating three and the checked shifts whose
range fits (`fits`) become the wrapping ones -- a shift where its modulo costs no more than the check, a width that is a power of
two (an `and`) or a distance that is a constant (worked out by the code generator), and not where it would be the machine's
remainder; and a comparison `_answer` settles is replaced by the truth value, `settle` folding what follows.  `_answer` asks: the
same value on both sides; the fact or its negation; the two ranges (`_compared`); and the two offsets, `a - b` at most `below +
at_x - at_y` from `bounds`.  A shift of a narrow value is done in the whole register, so a right shift bringing in the other kind
of bit than the type's reading has a range only where the top bit is clear; a division or remainder of a signed type has none.  A
check whose condition is a comparison only it reads is a `cmp` and a conditional jump, as a branch's is (`branches.condition_of`).

**An answer whose allocator is fixed travels as two words** (`opt/passes/thinanswers.py`, run as the first half of `largeanswers` at
every level, so the module is verified once for both).
The checker writes `Function.answer_from` -- `("heap", -1)` or `("param", i)` -- from `→ T in ⎕heap` or `→ T in a`, or from the body
where every answer was a fresh heap object; it checks bodies callees first (`_callees_first`, a depth-first walk over the names a
body calls) so a caller sees what its callees showed.  The pass changes the answer type to `〈ptr, u64〉`, drops the third part at
every `ret`, and rebuilds the string after every call with the heap's address or the argument for parameter `i`.  A function named
other than as a direct callee, or not internal, is left alone.  "Fresh" is a join, a list written out, `in ⎕heap`, or a call to a
function whose answer is fixed: never what an unannotated call answers, which may be text in the image -- so the compiler gives
back directly to the heap only what it knows is fresh, and asks the value otherwise.

**A value is kept two words wide where its allocator is known** (`opt/passes/leanvalues.py`, after `largeanswers` at every level).
It folds an `extract` of a `tuple`, narrows every block parameter of a string or a list by a lattice -- not yet known, one
allocator, carried -- over what every branch hands it, and makes the parameters with one allocator `〈ptr, u64〉`, rebuilding the
value at the block's head from `⎕heap`'s address, a null, or the function's parameter; then it drops the pure instructions nothing
reads, so a value only compared or joined never has its third word made at all.  A join's parameter takes the name of the local
it is bound to, so the report says which.

**What a container owns is copied by a generated function**, `__pl4g_own.T(arena, v)`, one per element type and built on
first ask in `sema/owned.py` the way the table functions are: text is a join of its bytes with nothing, a list is a join of its
run with nothing followed by a loop copying every element that points somewhere, and a list of lists calls the function for its
element type.  A value held in memory -- a sum, or a record or a tuple holding one -- is copied by `_build_own_in_memory`: its
bytes into a frame slot of the function's own, then every part that points somewhere copied where it stands (`_own_where_it_stands`
loads it, calls the part's own function and stores the answer back), and for a sum only the part its tag names, by a chain of
comparisons over the tag, one per part that points anywhere.  The answer is the slot, which `largeanswers` turns into a write
through the caller's storage as it does for every answer held in memory.  `__pl4g_disown.T` takes the same walk without the
copy: a record travelling in registers part by part (`_disown_leaves`), one held in memory and a sum where their parts stand
(`_disown_in_memory`), the part of a sum chosen by its tag -- and an element of a list held in memory is handed over where it is,
a value of it being its address, rather than loaded into a frame to be given back.  Before this a list of records holding text
reached `__pl4g_disown` with a record and failed an assertion the moment an element was replaced.
Giving back one object is `__pl4g_free(arena, where, size)`, the fourth of the allocator's hand-written symbols;
for an arena its body is a return.  The checker decides where a copy is needed from what it knows of where a value was made,
and a value made in `⎕heap` carries a mark of its own for that, so that "made in `⎕heap`" is told apart from "made in nothing".

**The runtime is impure**, both the hand-written symbols and the generated table functions: they allocate and they write tables,
and two calls to the allocator are two allocations that must not be merged or dropped.  That is a fact about the IR and not about
the program: they are reached from `_put_key`, the join lowering and their neighbours rather than from `_lower_call`, so the call
rule never sees them, and **allocating is not an effect of the function that does it** -- what it makes is new, and nothing anybody
else holds changes.  A pure function that allocates is still pure, and a call to it whose answer nothing reads may still go.

An answer that has to be taken
-----------------------------

`_check_value_is_used` reports a statement that is an expression whose value goes nowhere, and a call has always been the one
thing it let past.  What it asks of a call now is `_answer_is_taken`: a call is fine where the function answers with nothing, and
where `FuncAttrs.can_ignore` says so, and is reported otherwise.

The callee is found by `_callee_named`, which looks the name up and answers nothing where it is not a function.  It reports
nothing of its own: whether the callee is a function at all is the call's business and is said where the call is lowered, and
this runs before that.

**`_` is handled in three places, and each is a refusal but one.**  `_lower_assignment` sends it to `_dropped`, which lowers the
value and keeps nothing -- there is no local to rebind, no mutability to check and no unread-value question to ask.  Resolving a
name reports it (4475) rather than saying it is undefined, which would be answering a different question.  And defining one is
reported (4476) both inside a function and at the top level.

Nothing reaches the IR: dropping a value is lowering the expression and not using what came back, so what is left is whatever the
expression itself does, which dead-code elimination then keeps or drops by the ordinary rule.

Answering with more than the registers hold
------------------------------------------

`Function.answering` is a `ReturnStyle`, and the one member there is answers up to two values in registers and everything larger
through storage the caller provides.  It is a field of the function rather than of the target or of the convention description,
because the specification lets conventions differ between functions and this is part of one; a second style is a second member of
the enum, and everything that has to know asks the style rather than counting parts itself.

The second half is carried out by the `largeanswers` pass, which runs first at every optimization level -- it is how a function of
that shape is called, not an improvement to it, so the passes after it see the calls as they will be:

- the function grows one parameter, a pointer to the answer, and its answer becomes `void`;
- each `ret v` becomes one `extract` and one `store` per part, then a bare `ret`;
- each call to it gets a `frame` of the answer's type, hands that over as one argument more, and reads the parts back out with one
  `load` each before putting them together again with a `tuple`.

**The pointer goes last.**  A hidden *first* argument is what the system ABIs do, and they do it because theirs has to be in one
known register whatever else is passed; this convention is the compiler's own, so putting it last leaves every argument the
program wrote in the register it already had.

**It is a pass rather than three instruction selectors** because nothing about it differs between targets -- a place, some writes
and some reads -- and doing it once is what makes the three of them agree by construction.  It is a pass rather than part of
checking the language because the signature it produces is not the signature the program wrote: the pointer is not an argument any
program can pass, and no rule about arguments should have to know that one of them is not one.

**Two things bit while writing it, and both are the same thing.**  Finding the memory token in force may put the start of the
chain at the top of the entry block, which moves every instruction below it along -- so the index of the instruction being
replaced is taken *after* that, not before.  And a call that has already been rewritten answers with `void`, which is what says so:
without that test the rewritten call is found again and handed a second place to write into.

**What it does not do:** a part that is itself several values, such as a result among a tuple's members, is left alone.  Nothing
anywhere counts registers per part recursively -- `parts_of` gives a tuple its members and stops -- so routing such an answer
through storage would only move where it goes wrong.  Those are refused as they were before.

**The style counts values and the registers are counted per kind**, so every convention has as many floating-point registers to
answer in as values it answers: three for `pl4g`, two for the systems' (`xmm0`/`xmm1`, `d0`/`d1`, `fa0`/`fa1`, where C answers a
pair of `double`s).  With one, as there was, an answer the style kept in registers could still find none of its kind -- a
`〈f64, f64〉`, a record of two -- and was refused (8501).

### A record laid out for C, answered whole

**In the language's own convention it is a large answer whatever its size.**  `through_storage` answers yes for any function of
that convention whose answer is or holds an `@[abi]` record (`holds_abi`: the record itself, or a result, a tuple or a record
holding one by value), and for one of the system's convention whose answer holds one without being one -- a `Timespec ? i32`.
Nothing else changes: the place goes last, the `ret`s become stores, every call makes a frame and reads it back.  **A call through
a value is rewritten the same way** where its answer holds one: whatever the value points at is a function of the language's own
convention -- one of another is reached through a shim of this one -- so it answers through the room, and the call hands one over
after its arguments.  A call through a value answering anything else that does not fit is still refused (8501); this is the one
shape that every callee is known to answer in storage.

**In the system's convention it is answered as C answers it**, by a second half of the pass (`_as_c`) that runs after the first.
`ir/cabi.py` holds the classification and nothing else: `follows_system` says which names a target's table holds its system
convention under (`cdecl` and the architecture's own), and `answered_by_c` says how C answers a record on the module's triple --
a tuple of `Piece`s, each the bytes of the record one register holds and the type it holds them as, or nothing for room the
caller makes.  The members are flattened as C flattens them, an array's elements counted one by one (`scalars_of`):

- **x86-64**: a record of more than sixteen bytes in memory; otherwise eightbyte by eightbyte, one holding only floating-point
  numbers an `f64` -- or the `f32` it holds alone, two `f32`s packed as one `f64` -- and any other a `u64`, or the member itself
  where it is one member that fills it from its start;
- **AArch64**: one to four floating-point members of one width, a member to a register as itself; otherwise by sixteen bytes and
  eightbytes as x86-64, every word an ordinary register;
- **RISC-V**: one floating-point member, two, or one and one whole number, each as itself; otherwise as AArch64's.

**A function answering in registers** answers the pieces: its answer becomes the one piece's type or a tuple of them, which the
convention's registers take a part each by kind -- exactly SysV's assignment of INTEGER and SSE eightbytes to `rax`, `rdx` and
`xmm0`, `xmm1` in order, and the other two's -- and each `ret` puts its pieces together.  **A piece is put together with shifts**:
each member's bits (`fbits` for a floating-point one, a `bitcast` for an address) widened to `u64`, shifted to its offset in the
word and `or`ed in, the `f64` of two `f32`s made with `fbits` at the end; a call takes the pieces apart again with `lshr`, `trunc`
and the same casts the other way.  A word that is one member is that member and no instruction at all, which is the common
case -- `Timespec` is two `i64`s in `rax` and `rdx` -- and what a C compiler emits for the rest is the same shifts.  Where the record
is bytes in a place (it holds an array) or holds something no register is read as a number, the pieces go through a frame of
whole words instead (`_by_memory`): nothing C has needs that, and it is the shape that is certainly right for the rest.

**A function answering through room** gets the place *first* (`_widen_first`), as the systems put it, and the attribute
`answer_first` beside `answer_in_storage`, printed `answer-first`.  On x86-64 and RISC-V the first parameter is the first argument
register, which is what the systems want, and nothing below the pass has to know; **on AArch64 it is `x8`**, which is no argument
register, and `CallConvDesc.answer_reg` says so: `argument_places` asked with `answer_first` puts the first in that register and
the rest where they would have been without it, as `chained` does with the chain register.  **On x86-64 the function answers with
the place**, its answer the pointer, because the psABI says `rax` holds it on return and a C caller may count on that; the other two
leave it unsaid.  The sweep finds the place first where the attribute says so.  The stub of a host function and the finder keep
`x8` with the argument registers, since the finder's own call to `dlsym` may destroy it.  AArch64's system convention answers in
`d0` to `d3`, which only a homogeneous aggregate of three or four members uses.

**Not in a program the interpreter runs.**  Nothing there reads a register: a function it runs answers whatever it answers as one
value of several cells, and a function the host defines is called through libffi, which is handed the record as a `struct`
type and applies the system's rules itself.  So `_as_c` does nothing for `Output.INTERPRETED`, and `through_storage` sends such a
host function's answer through the caller's room, the place last as in the language's own convention: the interpreter calls the
host with every argument but that one, as a `struct`-returning function described to libffi by the record's fields -- a nested
record a nested `struct`, an array a `struct` of its elements, which is how libffi is told of one -- and copies what libffi wrote
into the room.  The record is laid out as C lays it out wherever it is, so the bytes need nothing done to them.

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
call graph gives -- callees first -- so the answer is the exact one everywhere but round a cycle, where there is no such order and
the convention's whole set is what is assumed.

**A convention named in the source is asked of the target.**  `@[abi("name")]` used to take any string, and one no backend
recognised fell through to the language's own convention under a name nothing would mangle -- two surprises at once, and both
silent.  `pl4g` and `cdecl` are answered without asking anyone, which is what lets the front end check the standard library without
importing a backend; anything else is put to `target.registry.conventions_of`, which imports that target's table of conventions and
nothing else of it -- not its code generator, which the front end has no business building to answer a question about a name.  The
table is the same `CONVENTIONS` dictionary the backend looks a convention up in, so a convention added to a target is one the check
knows about without being told, and the refusal lists what the target does know.  What the name is asked of is the module's own
triple, so a program compiled for two targets is checked against each.

`@[abi]` on a *type* takes no name at all: the attribute's meaning there is a layout, which is one thing and the same whoever
compiled the other side.  A name written there was read and thrown away -- `_collect_type` records only that the attribute was
present -- so it is refused rather than ignored.

**Three things carry a call rather than its instruction's row.**  What it destroys, because that is the callee's; what registers
its arguments went into, because a register holding an argument would look dead from the moment it was written and a later value
could be given it; and, for a return, the register the answer went into, for the same reason.  The rows say only what the
instruction itself does -- writing the link register, on the two architectures that have one.

The instruction tables named the caller-saved set on their call rows for a while, because the allocator had to be told something and
the table was the only place that could tell it.  Nothing of a convention is in them now.  What answers instead is `destroyed_by` in
`target/callconv.py`, one function for all three targets rather than a copy each: the units a finished callee actually wrote, where
that has been worked out, and the convention's whole caller-saved set where it has not.  A test says of each target that no call row
names a register the allocator hands out, which is the invariant in the form a table can be checked against.

**A physical register's live range is several stretches and not one.**  A virtual register gets a hull over the points where it is
live, which is right for a value: one value, one stretch.  A physical register is written wherever a convention says it is -- an
argument in, an answer out -- and between one such write and the read that takes the value away it holds nothing.  A hull would say
it was busy the whole time and the value that could have had it would be sent elsewhere, which is a move into a register and a move
straight back out of it.  Within a block a stretch runs from a write to the last read before the next write; at the edges of a
block it is what the graph says is live coming in and going out.

That splitting is what turns `fn f(p: u8) → u8: p` into a bare `ret`: without it the register the argument arrives in looks busy
from the entry to the return, so the value is copied out of it and back into it.

**The stack is aligned at every call as the system's convention has it**, sixteen bytes on all three.  It was not, for a long
time, and nothing noticed: what was called was this compiler's own code, which never asks, and the runtime compiled from C, which
happened not to.  A function the host defines is C that does ask, and the first call to one through a function with no frame
stopped inside `dlsym`.  So a frame's size is the target's to round (`frame_size`): on x86-64, a function that calls and returns
makes one eight past a multiple of sixteen, the return address having taken the other eight -- which is a frame of eight bytes for
a function that would otherwise have none; on AArch64 and RISC-V every frame is a multiple of sixteen, which is what the stack
pointer is held to everywhere there and not only at a call.  AArch64 checks it on real hardware, so an image whose frame was not one
would have stopped there with an alignment fault; QEMU does not check, which is why the tests did not see it.  A function that
does not return -- the entry point, the fault helper, a stub -- was not called as one that comes back and says how its stack stands
for itself.

**What the registers do not hold goes on the stack.**  `argument_places` answers a place per part, and a place is a register or
an `OnStack(offset)`: a part takes a register of its kind while the convention has one left, and an eight-byte slot after that
(`STACK_SLOT`), the slots numbered in the order the parts come in.  The two kinds are counted apart, as they always were, so
running out of one sends nothing of the other to the stack.  `CallConvDesc.floats_in_integers` is the one difference the three
systems have: RISC-V's LP64D passes a floating-point part with no floating-point register left in an ordinary register before
the stack, and its convention says so; the language's own does not, on any target, and is the same rule everywhere.  A record is
its leaves here as in registers, so one may be split between the two.  `TooManyArguments` is left to `result_places` only, an
answer having no second route at code generation -- `largeanswers` has sent everything too large through storage by then.

**The caller makes the room, at the bottom of its frame.**  `calls_placed` asks where every call of a function puts what, before
any code is generated, and the largest stack share of them (`stack_bytes`, rounded to sixteen) is the first slot the frame gives
out -- at offset nought, where the stack pointer is at each call.  A part is written there with a store of the whole register for a
whole number, which is what it would have been in a register, and at its own width for a floating-point one.  The stack pointer
does not move around a call, so `frame_walk`'s "how far above this function's stack pointer its caller's is" is still one number
per function and the walk of the stack needed no change; the stack is aligned at every call by the rounding it already had.

**The callee reads above its frame**, where its caller's stack pointer was -- which is `frame_walk`'s second number again: the
frame's size, and on x86-64 the return address above it.  That is known only once the allocator has said what it spilled, so
isel writes `Assembler.incoming`, a stand-in (`INCOMING`) that defines the parameter's register and reads nothing, and
`_make_frame` turns each into `select_incoming` with the final offset -- a load of the value's own width and sign, read as a `load`
of its type would be.  x86-64 reaches any offset; AArch64 refuses one past its twelve scaled bits rather than building the address,
which only a byte past four kilobytes of frame would need and for which a floating-point register has no register to build it in;
RISC-V builds a larger one in the destination itself, or for a floating-point one in the register a function working on runs keeps.

**Nothing else needed to change.**  A call of a function to itself in tail position is a loop (`tailcalls`), so its arguments are
the loop's values and never on the stack; no other call is made a jump.  A host function's stub jumps to what it found with the
stack as the caller left it.  The interpreter's calls and its host calls through libffi already took any number of arguments.

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


What a call writes and what a function takes
--------------------------------------------

They used to be the same list, and `_lower_call` checked the lengths against each other.  Two things separated them: an argument
may say which parameter it is for, and a parameter may say what it is given where no argument does.

**The default is settled where the function is written.**  `_defaults_of` runs in `_collect_function`, beside the resolution of
the parameter types, and each default it settles goes into `Function.defaults` as an `ir.value.Const` -- not an expression, not a
tree.  `Function.param_names` goes in beside it.  Both are on the function rather than on the definition's AST because a call in
another module has the `Function` and nothing else: modules are checked in the same process and the exports map holds the very
same objects, so a caller across a module boundary reads the value the definition settled without anything being serialized.

Settling reuses `_constant_value`, the same code a top-level variable's initializer goes through, by standing the default in a
throwaway `ast.VarDef`: the question "is this a value of this type the compiler knows" is one question and has one answer.  What
may be written is narrowed first, to a literal or a value of an enumeration, so that what does not settle is reported as a
default that does not settle (4525) rather than as a top-level initializer the compiler has not implemented.  A run of elements
or a collection is excluded on purpose -- what a call hands over is values, and those live in memory.

**The reordering happens after the lowering, which is why it costs nothing.**  `_given_arguments` splits the written list at the
first named argument, hands the prefix to `_one_by_one` unchanged -- so spreads, the per-place type, the unsuffixed literal
taking its parameter's type and the left-to-right order are all exactly what they were -- and then lowers each named argument
into the parameter it names.  The values land in a list indexed by parameter, gaps are filled from `Function.defaults`, and what
comes out is the positional list every later stage already understood.  Nothing after `_lower_call` knows that a call was written
with names.

The count check had to be split in two.  Too many arguments is still the old diagnostic; too few is only the old diagnostic where
neither the call nor the definition says anything but the places, and is otherwise reported per parameter (4530), because with
defaults in play "three where four were wanted" does not say which one is missing.


A line break the layout does not end a statement at
---------------------------------------------------

The compiler's lexer counts open brackets and gives out no `NEWLINE` while any of them is, which is what lets a parameter list or
a call be written down the page.  The tree-sitter grammar could not read those programs at all, and the reason is worth writing
down because it is a property of the two designs and not a bug in either.

The external scanner is the only thing that may consume a line break, since a newline matches nothing in the grammar's own lexer.
But tree-sitter asks an external scanner only where at least one of its tokens is valid in the parse state, and inside a bracket
none of `_newline`, `_indent` and `_dedent` is: the parser is in the middle of a list and would not accept the end of a statement
there.  So the scanner is never asked, and the line break reached the internal lexer, which had no rule for it.  (It reached the
scanner in the end, but only through error recovery, where every token is valid -- and the scanner then answered with an indent,
which is where the error came from.)

The scanner cannot count the brackets itself for the same reason: it is not called between two tokens the parser is confident
about, so it never sees most of them.  Making the brackets external tokens would fix that and would put the lexing of a dozen
characters in the scanner to do it.

**An end of line is therefore an extra.**  The scanner is still asked first at every position it is asked at, so wherever the
parser would end a statement the answer is `_newline`, an indent or a dedent, and the layout rules are decided exactly where they
were.  The extra catches what is left, and inside brackets what is left is precisely the breaks the compiler suppresses.
tree-sitter-python reads Python's identical rule the identical way.

What it costs is that the grammar also admits a line break where the compiler ends the statement and reports an error: after a
binary operator, after the `=` of a definition, after the `fn` keyword.  The grammar accepts a few programs the compiler refuses,
which is the safe direction for a grammar an editor colours with, and the compiler remains what decides whether a program is one.


A name that has to be somewhere
-------------------------------

A local in this compiler is a value: `let n: mut u8 = 0u8` binds a name to an SSA value, and assigning to it binds the name to
another.  Nothing of it is in memory, so there is no address to give -- which is the one thing `&n` needs.

**Whether `&x` may be written through is decided before the place is worked out.**  `_lower_address` reads it off `expected`,
because the aliasing rule has to be told which kind of borrow is being taken and `_lend` runs before `_place_written` -- working
the place out reads the name, and a name being lent is what is being asked about.  So the one flag serves three things: the
borrow recorded, the check that the place allows writing, and the type the value gets.

**A diagnostic that says the compiler has not implemented something must not be a consequence of one that says the program is
wrong.**  It is a claim about the compiler, it is fatal, and a reader has no way to tell it from the real thing.  `_literal_type`
therefore answers nothing, quietly, when what it is measured against is the error type, and `_lower_comparison` hands the error
type down to the right operand where the left turned out to be one -- the left having already been reported.  `_hint_of` learnt
to look through a dereference in the same pass, which is what lets `0 = r⌖` mean what `r⌖ = 0` means.

**A name a reference is taken of is given storage of its own, before the body is walked.**  `_addressed_in` collects every name
written after `&` anywhere below a node, and `_lower_body` runs it over the body before binding a single parameter.  A name in
that set is bound to a `frame` with its value stored into it, and `_Local.placed` says so; reading it is then a `load` and
assigning to it a `store`, exactly as for a variable at the top level, and `_Local.held` carries the type the name has since
`value.ty` is now a pointer.

It has to be settled before the body is lowered rather than at the `&`: a name given storage inside one arm of a branch and not in
another would be two different things where the arms meet, and the arms carry their locals to the join as block parameters.

The walk is over the fields of the tree itself -- `dataclasses.fields` on anything that is an `ast.Node` -- rather than over a list
of the kinds of node there are, so a node kind added later is looked through without this being told about it.  It goes by name and
takes no notice of scope, so a name shadowed somewhere may give storage to a binding that never needed it; what that costs is a
load, and never an answer.

A placed local is left out of what a loop carries round.  The name stands for the same place at every turn, so there is nothing to
carry: what a turn changes is what is *in* the place, which is read where it is read.

**Everything under it was already there.**  `PtrType`, `AddressInst`, `LoadInst` and `StoreInst` are in the IR; `place_of` in each
of the three instruction selectors already reads and writes through an address held in a register as well as through a symbol; and
`parts_of` answers that a pointer is one value, so a reference travels in one register as a parameter and needs nothing of the
calling convention. What was missing was entirely in the front end.

**Where a reference may go is one question asked in two places.**  `_holds_a_reference` looks through tuples, results, products,
sums, arrays, lists, sets and dictionaries -- with a set of what it has seen, because a type may reach itself -- and is asked of a
function's return type and of a variable's type at the top level.  Those are the two places a value escapes to, so both are where
a lifetime has to be said.

**How long a reference lives is one bit in the type.**  `PtrType.lasting` joins `pointee` and `mutable` in the interning key, so
`&static u8` and `&u8` are two types and the ordinary type comparison does the work; `render` and `mangled` both write it, which
is what keeps two instances of a generic apart.  Nothing below the front end sees it: a bitcast is the only instruction either
direction produces, and it selects to nothing.

**A lifetime name never reaches the IR as a name.**  `ast.RefTypeRef.lifetime` holds it, `_lifetimes_of` walks a type reference
over its fields to collect every name written anywhere in it, and `_borrows_written` turns the name on each reference of the
return type -- the outermost one's, a reference it leads to saying nothing about the place it is -- into a `Borrowed`: the
positions of the parameters carrying it, for each the leaves of a tuple parameter the name is on (`_leaves_named`, nothing
where it is on the whole), and for a lambda or a function type the names of a signature around it, each paired with what tells
that signature from another (`_lifetime_key`, where it is written).  That is the whole of what the rest of the compiler sees.
`PtrType` gains nothing: inside the body a `&u32 ⧖x` parameter is an ordinary `&u32`.

**A block's parameter is where the walk has to branch.**  A body that answers differently in two arms hands back what the join
came to, which is a block parameter and not either arm's value.  `_answers_from` builds the map from a block to the branches that
reach it -- there being no predecessor list in the IR -- and `_reached_from` requires *every* one of them to reach a source, a
promise being about what the function does and not about what one path through it does.  A parameter already being walked answers
yes, which is what stops a loop going round for ever and is right: what reaches itself round a loop is whatever it was on the way
in, and that is the branch the walk is already looking at.

**Which parameters are carried is in the type.**  `FuncType.borrows` holds a `Borrowed` for each reference in the answer, by
where it stands among the answer's parts (-1 for an answer that is one reference), worked out once by `_borrows_written` while
the signature is collected -- for a definition, a lambda and a written function type alike -- and part of the interning key and
of the mangled name.  `Function.borrows_from` and `leaf_borrows` keep the positions alone, which is what the definition's own
checks read.  A call through a value has nothing but the value's type, so that is where it reads the promise: `_call_borrows`
answers, for any call, the arguments as the type numbers them -- leaving out what a lambda brought in, which a call through a
value hands over first -- and the table, read off the callee's type or off the value the code was taken out of.  A value stands
where a type is wanted when its answer comes from no more than the type says (`_borrows_as_much`): fewer parameters, fewer parts
of them, fewer names from around.  A lambda's own names and those of the signatures around it are in `_lifetime_scopes`, pushed
while a signature is resolved and while a body is lowered; what each stands for in the body being lowered is in
`_lifetime_sources` (`_enter_lifetimes`): the parameters carrying it as they arrived and where they are kept, and in a lambda
what it brought in of them.  `_reached_from` takes those as `outer`, so a call through a lambda whose answer lives as long as a
name around is reached from what that name stands for.

**Where a parameter is kept is not what it named.**  A parameter a reference is taken of is given storage, which is a source too
-- reading the parameter is reading that storage -- but only through a load: the walk carries whether its last step was one, and
a source in `stored` reached any other way is the storage's own address, a place of this call's (4561).  A capture by reference
is the same inside a lambda: what it brought in is where the variable is.

**Provenance is one walk, used three ways.**  `_reached_from` follows a value back through the instructions that keep a reference
pointing into the same place -- `LoadInst` to its address, `AddressInst` to its variable, `CastInst`, `ExtractInst` and an `ADD`
or `SUB` to their first operand, and a `CallInst` whose callee made the same promise to the arguments it promised about.
`_answers_from`
walks each `RetInst` in the finished body back to the parameter, `_lasting` walks the same path looking for a `GlobalVar`, and
`_as_long_as_given` asks `_lasting` of an argument to decide what the call answers with.  The parameter's block parameter and,
where the parameter was given storage because a reference was taken of it, the place itself, are both accepted as the source.

**The check runs after the body is lowered**, not as each `return` is met, because a `return` is not the only way a body answers:
the last statement of a block is one too, and it is lowered in three places.  Walking the blocks once at the end catches all of
them and needs no hook in any of them; the scope is still open at that point, which is what lets the placed parameter's storage
be found by name.

**A promise is kept by anything that outlives it.**  `_lasting` is asked first in `_answers_from`, so a function saying `from v`
may answer with a variable at the top level, and `_shorter_life` in `_lower_into` bitcasts a lasting reference where a shorter one
is wanted.  The other direction is an ordinary type mismatch and is left to be one.

**How long a place lasts is how deep the scope it was made in is.**  `_Local.depth` records it when the name is bound, and
`_places` maps the value that is a place's address to the name it belongs to -- filled in `_bind_local`, where the frame is made.
`_named_place_of` walks a reference back to one of those the way provenance is walked everywhere else, and `_outlives_it` is the
one comparison: the name being given the reference must not be shallower than the place.  A variable at the top level is nobody's
local and answers nothing, which is right -- it outlives every name.

It is asked at three places: assigning to a name that stands for a value, assigning to one that stands for a place, and writing
through a reference whose pointee holds one.  A definition needs no asking, a name being bound at the depth it is written at, and
so does handing one to a call, a parameter lasting no longer than the call.

**Three kinds of value reach a place**, and `_reaches_a_place` is the one question asked of all of them.  A reference reaches the
one it names.  A tuple reaches the shortest-lived thing in it, so `_named_place_of` walks a `TupleInst`'s operands and answers
with the deepest.  A lambda reaches what it brought in *by reference*, so `_lower_lambda` records the deepest of those against
the value it made -- what a lambda brought in by value is a copy, a name standing for a place handing over what is at the place,
and ties it to nothing.

**A place the call did not make is the one the body cannot measure.**  `_named_place_of` answering nothing there is not "it
outlives everything" but "this body cannot say", and what goes into such a place must therefore outlive any caller -- which is
what `_lasting` already answers.  That is what shuts the `&mut &mut` hole at the definition: a function writing one of its
arguments into another is refused where it is written, since neither end can see the mistake.

**A reference that is out is one row in a list.**  `_Borrow` holds the name, whether it may write, where it was taken, and how
deep the scope is that keeps it.  `_lend` walks the list at `&`, `_lent_out` walks it at a name, `_statement_ended` drops the rows
nothing bound and `_pop_scope` drops the rows that scope kept.  There is no dataflow and no fixpoint: the list is the whole of the
analysis, which is what a lexical rule buys.

**What promotes a row is a name being bound to something holding a reference.**  A row starts at
`_UNTIL_THE_STATEMENT_ENDS`, and `_kept_by_a_name` is called from the four places a name takes a value -- a definition with a
written type, one whose type is read off the value, and the two arms of an assignment -- with the type that name will have.  So
`let r: &mut u8 = &mut n` keeps the row and `bump(&mut n)` does not, and nothing has to match the reference value up with the row
that made it.

**Working the place out reads the name, and that read is the reference.**  `_lower_address` asks `_lend` before
`_place_written`, and sets `_taking_a_reference` to the name while the place is worked out, so that `_lower_name` does not report
the operand of `&` as a read of a name that is lent.  Every local a reference is taken of is `placed`, so that one branch of
`_lower_name` is where every such read arrives.

**A lambda's body gets an empty list**, saved and put back around it, because it binds its own names and a reference out in the
body around it says nothing about a name of the same spelling inside.  `_lower_body` clears it for the same reason.

**`static` is read by looking, not by lexing.**  Making it a keyword would take the name from every program, and a suite that
already had a product type with a field called `from` showed what that costs.  `_reading(word)` matches an identifier by text,
and `static` additionally needs `_begins_a_type(1)`, so a reference type whose pointee is a type called `static` still reads.
The tree-sitter grammar cannot look ahead that way and takes the word wherever a reference type could say it, a difference only a
type of that name would show.

**Purity needed no rule of its own.**  `_made_here` answers whether an address is storage this call made by walking back through
casts and offsets to a `frame`, and `LANG_PURE_WRITES_ELSEWHERE` is what an array written through already reports.  A reference
that came in as a parameter is a block parameter and not a frame, so writing through it is an effect; one taken of this function's
own local is a frame, so it is not.


A type that reaches itself
--------------------------

`_resolved` works a definition out on first ask and sets `resolving` while it does, so a definition that reaches itself is caught
where the second ask arrives.  A reference is the one indirection that makes such a type finite, and making that work needed two
things.

**The type object exists before its parts are known.**  `_shell_for` makes an empty `ProductType` or `SumType` and `_resolved`
publishes it as `defined.shell` before resolving a single field, so a `&Node` among those fields has something to point at.
`_filled_in` puts the resolved parts into that same object once they are all in hand -- one `object.__setattr__` on a frozen type,
at the one moment nothing has read the parts yet, because the only thing that had the object was a `PtrType` and what a pointer
occupies does not depend on what it names.

**A product and a sum are equal only to themselves.**  Both were dataclasses comparing by structure, which for a type that
reaches itself would walk round the circle for ever -- and hashing one would too, which matters because `TypeContext` interns
pointer types in a dictionary keyed by what they point at.  Identity is what nominal already means, so the two now define `__eq__`
and `__hash__` themselves.  The base `Type` has no fields, so inheriting its generated `__eq__` would have made all products
equal; that is why they are written out rather than `eq=False` alone.

**What says a cycle is allowed is `_behind_a_reference`**, a depth raised while a reference's pointee is resolved.  Where the
second ask arrives with the depth at zero the definition is refused as before; where it arrives with the depth above zero a
reference stands somewhere on the way round, and the shell is handed back.  That is exactly the question -- not whether the field
in hand is a reference, but whether one is open anywhere between the type and itself.

**A list and a table's value raise the same depth** (`_behind_an_indirection`), since each is a few words whatever it holds: a
list where its elements are, how many and the allocator, a table a handle.  A set's element does not, needing none -- it is a key,
and nothing that could close a circle is one.  An array whose type says its length is its elements and raises nothing.

**The depth is compared with where the definition began, not with zero.**  `defined.opened_at` is the depth when its resolving
started, and the second ask is allowed only where the depth has risen since: an indirection opened on the way round.  Asking
"above zero" let `type Inner = d : u8 ; again : Inner` through whenever `Inner` was first reached from behind a reference --
`type Far = r : &Near`, `type Near = n : Inner` -- and its size would then have been worked out by walking round it for ever.

**Nothing else had to learn the circle.**  Every walk over a type that goes into a list's element already carried a set of what it
had seen, because a reference had made types circular before; `owned.can_own` is the one that had not, and now takes one, a type
met again being taken to be what the rest of the walk finds.  The generated copy and give-back functions close their circle on a
call: `tables.generated` registers a function before its body is built, so the copy of `[Json]`, asking for the copy of `Json`
which asks for the copy of `[Json]`, finds it.


A unit is part of a type
------------------------

The decision that made the rest of it small: a unit lives **inside** `IntType` and `FloatType` rather than in a wrapper type
around them.  A wrapper would have had to be seen through by every `isinstance(ty, IntType)` in an eight-thousand-line checker;
inside, `ty.bits`, `ty.signed`, `ty.low` and `ty.high` all still answer, every existing check still reads, and the one thing that
changes is identity -- `u64` and `u64 ¤meter` are two interned types.

That identity is the whole of the checking.  `+`, `-` and the comparisons already demanded that both sides be the *same* type and
already reported what did not match; with the unit in the type they demand the same unit and say so in the message, and not one
of them was touched.  `FloatType` had to be interned like `IntType` for it, since `is` is what the compiler asks a type with.

**Two operators work a unit out rather than demand one.**  `_DERIVES` is `×` and `÷`, and while their operands are lowered
`self._deriving` is set, which does two things: `_accepts` lets any unit through provided the bits agree, and `_literal_type` and
`_float_literal_type` give a literal the *bare* type, so `d × 2` is twice a length and not a length times a length.  The unit of
the answer is then worked out from the two operands' -- `Unit.times` and `Unit.over`, which add and subtract exponents -- and
handed to `builder.binary` as an explicit type.  Because the operands were let through unmeasured, what comes out is measured
against the place it is going here and nowhere else; that check is the one thing the derive path has to do for itself.

The unit is derived only where the operand's own type *is* the scalar being worked with.  In the vectorised path `ty` is the
element of a `VecType`, and answering with the element would turn a run into a single value.

**Nothing below the checker knows.**  `mangled()` leaves the unit out, so a symbol name is what it was.  `parts_of`, the layout,
the register allocator and the three instruction selectors all read `bits`, which is unchanged.  The verifier compares types
`without_units` in the two places where a unit legitimately differs across an instruction -- a `bitcast` between the same bits,
and the operands of a product or a quotient -- and nowhere else.  `Module.int_const` and `float_const` intern by the unit as well
as the bits, which was the one thing that had to change for a literal to carry one at all.

**`⎕drop` and `⎕unit` are a `bitcast`**, or nothing where the value is a constant -- the constant is simply made in the other
type.  Neither applies a factor: what the `unit NAME =` form records is that two units measure the same thing, and the scale is
kept in `Unit.scale` so that two units with the same base units and different scales are two types.  A conversion that used the
scale would generate code, and units generate none.

**`#` answers `u64 ¤size`**, which is where the `¤idx` rule earns its keep: an index is `¤idx`, a count is `¤size`, and a program
that wants to index with a count writes `unit ¤size → ¤idx` once.  `_stands_for` follows those declarations transitively and never
backwards.


The bill of materials
---------------------

`pypl4g/sbom.py` holds all of it: the normalization, the hashing and the two sections' bytes.  What it needed from elsewhere was
small.

**Somebody had to know every file that was read.**  The driver reads what the command line named and the checker reads what an
import named, and neither knows about the other; `SourceManager` is the one thing both go through, so it keeps the list --
`record(path, tokens, unit)` from each of the two places.  It holds the tokens and the tree as `object`, because the source layer
is below the front end and does not know what either is.

**A definition's tokens are the ones inside its span**, with the attributes folded in because what a definition says about itself
is part of what it is.  One wrinkle fell out of that and is worth stating: a dedent stands at the first column of the line that
follows the block it closes, which is the line the *next* definition begins on -- so one definition's blocks close inside the
next one's span.  `normalized` therefore drops leading block-ends, since nothing begins by ending a block.

**The sections are emitted from the driver** rather than from each of the three targets, because nothing about them is
target-specific, and after the assembly dump rather than before it: the dump is for reading what the code generator produced, and
this names the sources by the paths they were read from, which are one machine's directories and not a program's.

`MCSection` grew `sh_link_to` and `sh_entsize`, and the writer stopped assuming that every loaded section is `SHT_PROGBITS` --
`.sbomstr` is a loaded `SHT_STRTAB`, which is what `.dynstr` is in any dynamically linked program.  `sh_link` is resolved by name
once the section indices are known, beside where `.symtab` is linked to `.strtab`.

**It costs every image a second loadable segment**, since the two sections are read-only and the code is not, and sections are
grouped by what may be done to them.  That is about two hundred bytes of headers on top of the table and its strings, and the
size test subtracts it: a fixed cost of carrying the thing is not a fact about the code generator.


One entry says what a literal holds
-----------------------------------

An array, a list, a set and a dictionary each hold one type, so an entry that says which says it for every other.  The compiler
used to get that only from the left: entries were lowered in order with nothing expected, and the first one that turned out to
have a type settled it for the rest.  That refused `⟦1, 2u8⟧`, which says its type perfectly well, and -- because an array
literal was lowered with nothing expected whenever the variable had no written type -- it refused `⟦1u8, 2⟧` too.

**`_said_by` reads the type off the writing before any of it is lowered.**  It walks the entries, looking through nested array and
list literals, and answers the first type an entry *says on its own* -- which is a literal carrying a suffix and nothing else.  An
entry whose type is known only once it has been lowered, a name or a call, is left to the lowering, where the first of them
settles it for the rest as it always did.

What it answers becomes the expectation every entry is lowered with, so a literal with no suffix takes it, and `_same_type` still
has the last word about whether they agree.  The one wrinkle is that an entry which says its own type must *not* be lowered into
another one: `_literal_type` reports a suffix that disagrees with its context, and the complaint wanted here is the one about the
literal as a whole -- "this entry is of type u16, and the ones before it are of type u8" -- rather than one about a value being
handed somewhere.  `_taken_from` is that rule: an entry that says what it is takes nothing from the rest.

The same call also settles the case a written type covers.  A collection's entries were deliberately lowered with nothing
expected even where the type was written down, so that a disagreement with the type reads as a disagreement between entries; what
is expected of them now is what an entry said, falling back to what the type says where no entry says anything -- so
`let s: ⸨u8⸩ = ⸨1, 2⸩` works, which it did not.


Narrowing, and the conditions that cannot hold
----------------------------------------------

`_fitted` builds three truth values -- above the top, below the bottom, negative where there are no negative values -- and each
is asked only where the two types make it possible.  `found.high > into.high` decides whether overflow can happen at all,
`into.signed and found.low < into.low` whether underflow can, and `signed and not into.signed` whether the sign case can.  Where
one cannot, the constant `false` goes in its place and `_either` drops it from the disjunction, so nothing about it reaches the
program.  Narrowing to a wider type comes out as a `wrap` of a value with a constant `false` beside it, and `u32` to `u8` as one
comparison.

**Which condition it was is their numbers added up rather than a choice between them.**  There is no select instruction in this
IR, and building blocks for a three-way choice would be a branch where none is needed: the conditions are exclusive by
construction -- `sign` is asked only where the type has no negative values and `underflow` only where it has -- so
`underflow×1 + sign×2` is the answer whenever one of them holds.  `overflow` is nought, which is what makes it what the sum
comes to when neither holds, and it is also what the sum comes to where nothing failed at all; the error of a result that
succeeded is never read, so that costs nothing.

The value itself is read as the narrower type by width of the *holder* and not by the width the type states: a `u5` and a `u8`
are one byte apiece, so there is nothing to cut off between them, and by the time the cast is reached the value is in range and
the bits above its own width are already what they should be.

Two things elsewhere had to give a little.  The verifier's `_held_as` now covers an enumeration and its holder as well as a code
point and its holder -- reading the one as the other is the same idea the widenings already allowed, and that an enumeration
holds only its own values is the checker's to keep rather than anything an instruction can say structurally.  And `_lower_member`
looks in `BUILTIN_TYPES` before the file's own names, so `⎕narrowing.overflow` is written the way every other enumeration's
values are.

**A unit on a result had been unwritable**, which `⎕narrow` found: the mark that makes a type a result was read before the unit,
so `u8 ¤meter?E` did not parse and `u8?E ¤meter` would have put the unit on the result rather than on its answer.  `TypeRef`
now carries the unit and the parser reads it between the name and the mark, which is where it belongs -- a result of a length is
a result whose answer is a length, and there is nothing about a result for a unit to say.


Whole numbers and floating-point numbers
----------------------------------------

Three cast kinds carry the four names: `itof` a whole number into the floating-point number nearest it, `ftoi` a floating-point
number toward zero, and `fbits` the bits of one read as the other, in whichever direction its two types say.  None of them is a
`bitcast`.  That one means "the same bits in the same register, read as something else" and emits nothing; these three move a
value between the two kinds of register, and the verifier says so of each -- an integer of any width and a float of either for
the first two, an integer and a float of one width for the third.  Whether `itof` reads its operand as signed is the operand's
type's business, as it is for every other instruction, so there are not two of each.

**`ftoi` answers nothing in particular for a number its type has no room for.**  The three architectures answer such a number
three ways -- x86-64 the smallest signed number, AArch64 and RISC-V the end it is beyond, and not-a-number nought on one and the
top on another -- and the checker asks the question before anything reads the answer, so there is nothing to agree on.  The
interpreter of the C++ implementation answers nought there, a C++ conversion of such a number being undefined; the constant
folders do not fold one at all.

`⎕trunc` lowers to three comparisons and the conversion: at or above the top of the type plus one, at or below the bottom less
one (or below the bottom, where the format has no number for the bottom less one), and unequal to itself, which only a
not-a-number is.  Each bound is a power of two or one less than one that the format holds, so it is a constant in the image.
For an unsigned type the second comparison is with minus one, and it is `sign` rather than `underflow` -- exclusive with the
other two as the narrowing's are, so `_reason` adds them up the same way, with `absent` as a third term weighted three.

**Every whole number is widened to a word by its own sign before it is converted**, so each back end has one conversion per
format and signedness, from a word.  An unsigned number narrower than a word is a signed word that is not negative, so only a
`u64` needs the unsigned conversion -- which AArch64 (`ucvtf`, `fcvtzu`) and RISC-V (`fcvt.d.lu`, `fcvt.lu.d`) have and x86-64 has
only in AVX-512, which no level here assumes.  There:

- **into a float**, the signed conversion answers every `u64` below 2⁶³, and a branch skips what follows for those.  One with the
  top bit set is halved, the bit shifted out or-ed back into the lowest place so that the halved number still rounds the way the
  whole one would -- it has more bits than either format keeps, so that one bit is enough -- and its conversion is doubled, which
  is exact.  It is the sequence GCC emits.
- **out of a float**, the signed conversion answers every number below 2⁶³ and the smallest signed number for one from 2⁶³ up,
  which has only the top bit set; so the number less 2⁶³ is converted as well, and kept where the first answer was negative by a
  mask made of that answer's sign shifted across the word.  Or-ing the two puts the top bit back.  No branch; it is Clang's.

RISC-V's narrower bit move, `fmv.x.w`, fills the doubleword with copies of the top bit, and a `u32` is held there with zeroes,
so the move is followed by the `and` every narrow unsigned value is normalized with.  The conversions on RISC-V name `rtz`
toward zero and the dynamic field for nearest, which is what `frm` holds at startup and what nothing in the language changes;
`select_int_to_float` now takes a doubleword for both formats (`fcvt.s.l` rather than `fcvt.s.w`), the word the rounding
sequence hands it being in a doubleword already, extended by its sign.

**A constant is converted by the checker**, and the narrower format by hand: Python's `float(int)` rounds to nearest even for
`f64`, but `f32` of an `int` by way of `f64` would round twice, so `_nearest_float` keeps the top twenty-four bits and rounds the
rest itself.  The C++ checker converts with the host's own conversion, which is single-rounding in both formats.  `⎕from_bits` of
a constant is folded unless it is a not-a-number, whose fraction a `FloatConst` -- a number, not bits -- does not promise to keep.

`⎕narrow(n, ⌜char⌝)` is `_narrowed_to_char`: above the top where the number's type reaches past it, negative where it is signed,
and a surrogate where it reaches the first of them -- `(n - 0xD800) <u 0x800`, one subtraction that wraps and one comparison, a
number below the run coming out of the subtraction very large.  `⎕chr`'s check is the same question with the answer turned round
and the top asked as well, and stops with 83.  Both have their constant forms in the checker, as does a literal number where a
`char` is wanted, and the lexer refuses an escape naming a surrogate as it refuses one beyond the last code point.

`⎕wide_mul` is `wide.mul`, a binary instruction whose answer is the tuple of its two halves and so the one whose type is not its
operands': the verifier asks for two 64-bit operands of one type and an answer of the upper half's type and `u64`.  Each back end
lowers it to `Assembler.wide_product`, which saturating and trapping multiplication already used, into two registers that are the
tuple's two members as any value of several parts is held -- the upper one first.


A function written where a value is wanted
------------------------------------------

**A lambda's value is three words**: where its code is, where what it brought in is, and the allocator that room came from.
`parts_of(FuncType)` answers those three pointers, which is what makes a function value travel the way every other value of
several parts already travels -- three registers as an argument, three words in memory, and the large-answer pass needs no
telling.  One type covers the lambda that brought something in and the one that brought nothing, which is what lets either stand
where a `fn(...)` is wanted; the one with nothing to carry carries a null environment and no allocator.

**Where the environment is, `_environment` is told.**  `_lambdas_that_stay` walks a body before it is lowered -- the whole of it,
lambdas inside lambdas included -- and records, by identity, every lambda written as an argument of a call of a function
(`_calls_a_function`: not a record, a variant or a `⎕` name) and every one bound by `let` to a name that is not `mut` and that the
body only calls or hands to such a call.  One walk, `_lambdas_handed_on`, collects both and every name mentioned any other way --
a macro invocation that mentions a name counts as taking it anywhere -- and a name bound twice is judged by every mention of
either: telling the two apart would be a second walk, and the cautious answer costs an allocation.  Those keep the environment in a frame slot and carry no allocator; every other one takes room from `⎕heap` through
`tables.allocate` and carries `⎕heap`'s address; `Lambda.kept_in`, written after the capture list, forces `⎕heap` or names an
arena whose place `_arena_named` finds, and the room comes from there.

**A lasting lambda is a type**, `FuncType.lasting`, which `_promises_as_much` lets stand where a plain one is wanted and
`_shorter_life` carries across by retupling.  `[n] in ⎕heap` makes one after asking `_reached_by` for nothing but `_HEAP`
(4644); a lambda bringing nothing in, and `_function_as_a_value`, take the lasting type when that is what is wanted.  Because
the type is checked wherever such a value is made, what holds one never asks where it came from: `_reached_by` skips captured
values of a lasting type, and `_answered` and `_into_a_place` skip the lambda question for types whose every function lasts
(`_lambdas_last`).  The report log says which (`allocator`).

**What a lambda reaches is provenance, in the keys an arena's are.**  `_lower_lambda` writes into `_made_from` the room's key
(`_HEAP`, the arena's, or a stand-in for the frame) and what `_reached_by` finds: every captured value's own provenance, a
stand-in for every variable brought in by reference, and one for the variable a captured reference names.  A stand-in is a
`_Local` registered in `_arena_by_id` at the depth of the scope it goes with -- so every depth check an arena already gets
(`_made_in`, `_made_in_part`, `_leaves_no_arena_behind`, `_into_a_place`) holds a lambda to it, and `_outlives_arena` reports one in
a reference's words (4570) -- and in `_stand_ins`, which keeps it out of the allocator report.  `_points_somewhere` counts a
`FuncType`, so a parameter of one gets an unknown marker; `owned.points` does not, an environment being neither copied nor given
back.  This replaced `_places` for lambdas: one mechanism, which already followed values through names, joins, loops, records and
lists.

**Where a lambda leaves, `_lambda_may_leave` asks** whether every key is `⎕heap` or one the signature names, and reports 4632
otherwise -- from `_answered`, for anything holding a lambda, and from `_into_a_place`, for a place `_made_here` says is not the
call's own.  `_into_a_place` asks the same of strings and lists written there, copying one into `⎕heap` (`owned.own_function`)
where it can and reporting 4633 where it cannot; through a reference to a place of the call's own it applies the depth check
against the variable `_named_place_of` finds.  A record written out where it goes takes the slow path whenever the place is
asked, `_build_record` not passing through it.

**The body becomes a function of the module**, `⎕lambdaN`, taking the environment as a first parameter nobody wrote.  It is
checked in a scope stack of its own, holding the captures and the parameters and nothing else, with the scopes around it kept in
`_outside` -- so a name it names and did not bring in is reported as one it did not bring in rather than as one nobody has, which
is the difference worth telling a reader about.

**`[=]` and `[&]` are worked out in the checker**, from two walks over the body: every name it writes, in the order it writes
them, less every name it binds for itself and less its own parameters.  A name written as the target of an assignment counts --
it is kept as a string rather than as a name of its own, so it is not found by looking for names and has to be looked for.  A nested lambda's capture list counts as names *this*
body reaches, since that is what they are.

`[&]` needs the names placed, and which names those are is not known until the body is checked -- which is after `_addressed_in`
has decided.  So every name written in such a body is given storage: one that turns out not to be brought in has paid a load for
it, which is the price of a list that says "all of them" rather than saying which.

**Whether a capture was used is the scope's answer and not a second walk.**  `_Local.read` is what the rule about a value nothing
reads is already built on, so the check after the body is lowered is that flag -- asking the same question twice in two ways
would be two answers to one question.  Where it reports, the local is marked read, so that one mistake is reported once rather
than as an error and a warning.

Writing is a use, which needed a flag of its own.  A name brought in by reference may be brought in *to* be written, and the
place it stands for is then never read; `_Local.written` says so, and the rule about a value nothing reads learned it too -- for
a name that stands for a place, the value bound to it is where the place is, and writing through it uses that value as much as
reading through it does.

**A capture by reference is a placed local inside the lambda.**  `_addressed_in` collects the names written after `&` in a
capture list beside the ones written after `&` in an expression, because both need the variable to be somewhere; the environment
then holds a pointer, and the name inside the body is bound with `placed_as`, so reading it loads and assigning to it stores.
That is the same `_Local.placed` a reference already introduced, doing the same job.

A capture of something that is itself several values -- a lambda capturing a lambda -- is kept in the environment **as its
parts**, laid out as a tuple of them would be, because a store writes one part and a load reads one part.

Indirect calls
--------------

**The walk takes a way of making one call, not a function.**  `_walked`, `_walking_shape` and `_each_of` were written against a
`Function`; they now take the `FuncType` and a callable that makes one call, so a direct call passes `builder.call(func, ...)`
and an indirect one passes a closure over the two halves it read out of the value.  Both halves are read once, before any call is
made: a walk makes one call per element and they all go to the same code with the same environment.

**A named function becomes a value as it is.**  Every call through a value -- a lambda's, a walk's turn, a generator's resume --
passes its first argument, the environment or the description, in the convention's chain register, and every function called that
way is `chained`: its first parameter arrives there and the rest where they would without it.  `argument_places(chained=True)`
says so for both ends, a call being chained where it goes through a value or to a chained function.  The register is one no
argument uses and a call destroys -- r11 on x86-64, the language's own convention passing an argument in r10, which is the
system's static chain; x15 on AArch64, the backend leaving x18 alone; t2 on RISC-V, as GCC has it -- so a function of the
program, which has no environment, never reads it, and `_function_as_a_value` points the pair at the function itself.  A
function of another convention still goes through `⎕through<name>`, kept in `_shims`: a call through a value follows the
language's convention and such a function does not.

Compare GCC's nested functions and LLVM's `nest`, the same register for the same reason; Rust tells the two apart in the type
system, `fn` for the bare address and `Fn` for the pair, and C has nothing to carry, which is why a C callback needs a `void *`
written out beside it.  A tag in the value would have cost a branch at every indirect call to save one call at some of them.

**A function's own type says whether it walks.**  `_collect_function` and `_make_instance` build it with `func_attrs.listable`,
so `func.ty.listable` is the one place it is written down and a named function handed over carries it.  The symbol does not
move: `mangle` writes the parameters and the result one by one rather than the whole type, so the word appears in a symbol only
where a *parameter* has such a type.

**A bitcast cannot carry a function from one type to the other**, a function being three words and not one.  `_shorter_life`
takes the three out and puts them back under the type that promises less, which is the same registers and no work; the
verifier's `_held_as` gained the arm that says two function types differing only in the walk are the same bits.

**`@[listable]` before a lambda is an attribute list where an expression is wanted.**  Nothing else begins an expression with
`@[`, so `_parse_atom` reads it and hands it to `_parse_lambda`; `_begins_a_type` gained it too, a function type now being able
to start that way.  The tree-sitter grammar has the one ambiguity the compiler does not: written as a whole statement the list
could be the statement's or the lambda's, and a declared conflict with a lower dynamic precedence on the lambda is what makes the
generalized parse take the statement's, which is what the compiler's statement parser does by reading the list first.

**Purity is asked before the walk and not after.**  It was asked after, and a walked call returned before reaching it -- so a
pure function calling an impure listable one with an array said nothing.  Moving the question above the branch is the whole fix;
a walked call is as much a call as any other, and what the callee does it does once for every element.

**The callee of an indirect call is an operand and not a reference.**  It was a reference at first, beside the `Function` a
direct call names, and the optimizer removed the instruction computing it: everything that asks what an instruction uses asks its
operands, so a callee kept anywhere else is a value nothing counts as used.  `CallInst` now puts a computed callee first among
the operands and answers `target` and `arguments` accordingly.

**The callee goes into the parallel copy with the arguments.**  The register it is in is one an argument may be moved into --
which is likelier than it sounds, the argument registers being the ones the allocator prefers -- and a move that wrote it first
would call whatever the argument happened to be.  That was a segmentation fault the first time the callee and an argument
collided, and it is the same reason a branch's arguments are a parallel copy.

The convention for a call through a value is the language's own and cannot be the callee's: nothing at the call knows which
function it is.  For the same reason such a call is taken to destroy everything its convention allows, and to do anything at all
-- so a pure function may not make one.

Three instructions were added, one per target: `call r/m64`, `blr Xn` and `jalr ra, rs, 0`, each with a sample in the
differential tests against the GNU assemblers.


A function written once and compiled many times
-----------------------------------------------

**A generic function is not a `Function` at all until a call makes one.**  `_collect_function` sees a type parameter among the
written parameter types and keeps a `_Generic` instead: the tree, the attributes, and the parameters in the order they are first
written.  Nothing of it reaches the module, so a generic function nobody calls costs nothing and is never checked.

**A call is what makes one.**  `_lower_generic` walks the arguments left to right, and for each one asks whether the parameter's
written type is settled by what the arguments to its left already said.  Where it is, the argument is lowered *into* it, so a
literal with no suffix takes that type; where it is not, the argument is lowered on its own and `_reading` matches the written
type against what it turned out to be.  That is what makes `largest(9u8, 4)` work, and it is the same left-to-right rule an
ordinary call follows with the parameter's type worked out rather than looked up.

`_reading` is a structural walk: a bare type parameter binds, and an array, a list, a reference, a set, a dictionary, a tuple and
a function type each match their own shape and recurse.  A shape that does not match is where a type parameter stays unknown, and
is reported rather than guessed at.

**Instantiating is the lambda's trick again.**  `_lower_instance` puts aside everything about the function being checked and puts
it back, because a call to a generic function stands in the middle of another body and what is being checked has to be this one
while its body is.  `self._bound` carries what each type parameter is, and `_named_type` reads it -- which is the whole of the
substitution: there is no rewriting of the tree, only a map consulted while the types in it are resolved.

What is put aside includes **the borrows the caller has out**: the body starts with none and the caller's come back when it is
done.  They used to be shared, and a statement ending in the body ended the caller's statement borrows with it, or failed to, by
turns -- which a template with two holes found, its second hole being told the pool was still lent to its first.  And it includes
**the conditions being held to**, `_demanding`: the body's own `pre` is checked at its entry by `_lower_conditions` with
`while_running`, which leaves out a clause comparing types (settled at the call, 4918), and its `post` at each `return`.  Before,
neither was checked, and the return of an instance was held to its caller's.

**A value of a type with an `each` is walked as the call `T.each(v)`**, made in one of two places.  Where what the value is can be
said before it is lowered -- `_said_to_be`: what `_hint_of` says, a field of a record, an element of an array -- `_called_each`
rewrites the tree, `v` and `v in a` alike, into the method call `v.each()` resolved by `_through_a_value`, which is what lends a
place to an `each` taking a reference and refuses a reference read out for one taking the value.  Rewritten before anything else
looks at it, the loop then sees a generator's call, and `_written_in` writes its body into the loop.  Where it can only be said
after -- `_over_a_value`, `_as_an_iterator`, `let r: ⇉ = …`, `⎕start` -- `_each_walk` lowers `T.each(_Ready(v))` with `_pick_in`
on the call, which gives back the walk's description the way a call written out does.  A generator written `T.name` is kept in
`_members` beside the type's functions (`_put_generator`, which holds an `each` to its shape, 4926).  A value of an instance of a
type with type parameters finds them under its definition: `_path_to_member` reads the template off the instance's `family` and
looks for the function under the template's name, holding the module's export of that name to be the template.  And `_generator_named`
finds it through `_member_named`.  The arena is never written by the compiler's rewrite: `T.each(v)` leaves it out, and
`_generator_arguments` fills a trailing `&mut arena` left out with `_out_of` -- the arena in force -- as `_given_arguments` does for
any call.  `_generator_in` puts the `in` arena in force while it lowers the call, so that `g(…) in a` hands `a` both as the place
of the walk's frame and as the argument.

Since a loop calls `each` without writing its name, `_mentioned` counts a loop, a `yield` and a call as mentioning `each`, so that a
module read only as far as it is named still lowers the `each` of its types.  And since a generator may walk another through a
value, which no name in its body says, the ring check behind 4675 is fed by the lowering: `_walks_itself` records each generator a
body walks through calls (`_walks_into`), and `_generator_reaches_itself` follows those edges as well as the calls it reads off the
tree.  A body written into a loop is lowered there too, so the report is made once per place.

**A coroutine frame is a type of its own**, compared by identity.  `CoroutineFrame` was declared `eq=False`, which keeps the
dataclass from writing an equality and leaves the base's: generated for `Type`, which has no fields, it took any two frames for one,
and `tuple_type` interned two walks' descriptions with alike arguments as one -- laid out with the frame of whichever came first.
It now says its equality itself, as the nominal types do.

**Arguments are matched to parameters as in any call**, by place and then by name, by `_generic_arguments`: a parameter left out
takes its default, lowered in the definition's own scope, and an `&mut arena` left out is `None` there and handed the arena in
force -- `_out_of`, the one the innermost `in` around the call names, or `⎕heap`.  An ordinary call does the same in
`_given_arguments`, and both note the call in `_arena_in_force`, so that `_where_made` takes the answer to be made in that arena
(`_arenas_handed`) and `_lower_allocated` lets the call carry an `in` at all; a call with `in` that leaves out no arena is still
refused (4568).

**One instance per set of types**, held on the `_Generic` and keyed by the types.  A second call saying what an earlier one said
gets the same function back, which is what keeps a loop that calls one from emitting a copy each time round.  A function that
calls itself with the types it already has is refused rather than looped over: what it would call is the one being made, which is
not finished.

**Two instances are two symbols on their own.**  A symbol is the signature written out, and two instantiations have two, so
nothing had to be invented for it -- only the module's key, which the name alone no longer tells apart.

**An error in a body says which call asked for it.**  `DiagEngine.because` hangs a note on every error raised until it is given
back, which is how the note reaches diagnostics raised deep inside the body by code that knows nothing about generics.  It is the
one mechanism for it: a note is attached to a diagnostic, and nothing that reports one inside an instantiated body has the
instantiation in hand.

**`T’` cost the lexer one rule**: a quotation mark continues an identifier.  It cannot begin one, so `’a’` is still a character
literal, and the only thing it costs is a name immediately followed by a character literal with nothing between them -- which
nothing readable writes, and which the whole test suite confirmed nothing does.  It is Haskell's rule and ML's.


A range with a computed step
----------------------------

**A written-down step keeps the loop it always had**: the comparison its sign chooses and a saturating step.  One worked out while
running goes to `_over_a_counted_range`, which works out the number of turns before the loop and carries a count beside the value.
Everything is done in 64 unsigned bits -- the ends and the step extended by their own signedness and read unsigned -- where the
difference of the two ends, taken in whichever direction the step points, is exact for every width up to sixty-four.  The direction,
the magnitude and the count are worked out without a branch, there being no select in the representation: a truth becomes a mask
(nought less its widening), and `(d ÷ s) + (d % s ≠ 0)` rounds up without the addition that `d + s - 1` would overflow.  The value
steps with a wrapping addition of the step in the ends' type -- a signed twin's bits being what a wrapping addition of a negative
amount is -- and the step after the last turn, which may go round, is never read.  The check for nought comes before any of it.

A block written on one line
---------------------------

The two parsers decide it in two different places, because the two know
different things.

**The compiler's parser decides it on one token.**  `_parse_layout_block` reads
the colon and looks: an end of line means the indented reading, anything else
means the block is on this line, and `_parse_separated` then reads the
statements the way it reads them anywhere.  What ends the run is what ends any
run -- the end of the line, a dedent, a brace -- and the `else` of the chain,
which is not a statement and so ends it by not continuing it.  `_inline` is
raised while such a block is read, which is what refuses a second one inside it
(3044).  One flag, one lookahead, and nothing else.

**The grammar cannot look**, so it is told.  A fourth external token,
`_inline_open`, stands in `layout_block` where the end of line and the indent
stand in the other reading; the scanner produces one or the other, so the two
readings differ by a token and there is nothing for the parser to decide
between.  Writing it as a grammar rule instead was tried and measured: a rule
holding any statement converges only after **nine** declared conflicts, through
the expression grammar, assignments, units and loops; one holding a single
statement still needs `[$._expression, $.logical_expression]`, because
`if b: x ⊼ y` can read the operator as the arm's or as the whole `if`'s.  With
the scanner producing the token there are none.

**What closes it, the scanner decides by one character.**  It is asked only
where the parser would take the end of a statement, and at such a place the only
things that may follow on the line are a semicolon continuing the block, the
brace of a block it stands in, a comment taking the rest of the line, and the
`else` or `elif` of the same chain.  Of those only the last begins with a
letter, so `e` is enough and no word has to be read.  It is an approximation of
what the compiler's parser knows exactly, and the direction it errs in is the
harmless one: a name beginning with `e` can only stand there after a semicolon,
which is tested first.

**Neither token may be produced where nothing was read.**  Both are empty, so an
open followed at once by a close would leave the lexer where it was and the
parse would not move.  What stops it is the grammar rather than a guard: a block
holds at least one statement, so right after the open neither the end of a line
nor a dedent is a token the parser would take, and the close cannot fire until
something has been read.

What the grammar is made into
-----------------------------

Two things are made from `grammar.js`, by two commands.  `tree-sitter generate`
writes `src/parser.c`, which is committed so that anything reading the grammar
needs no tree-sitter command.  `tree-sitter build` writes `pl4g.so` beside it,
which is what an editor loads; it is not committed, being a binary for one
machine.  `bin/pl4g-grammar` runs both, and is the one thing a grammar change
asks for.

**The suite keeps them current**, so that forgetting costs nothing.  One test
generates into a copy and compares the parser byte for byte; another rebuilds
the library whenever anything it is made from is newer, and fails with the C
compiler's own words when the scanner does not build -- which is the one place
the scanner is compiled on purpose, a scanner that does not build showing up
anywhere else as a grammar that cannot be loaded.

**Neither test may write what the others read.**  `tree-sitter parse` builds a
library of its own in a cache, from `src/parser.c`, whenever that file is the
newer of the two -- so a test that rewrote `src/parser.c` in place while the
suite ran over every core made one of those reads find a parser half written,
and the run then disagreed with the compiler about a program that was perfectly
all right.  Generating into a copy is what settles it.  The built library is
read by nobody here, so building it disturbs nothing.

Where a record lives
--------------------

A record name stands for storage of its own, which is `_Local.placed` doing what
it already did for a name a reference is taken of.  That is what gives a field
an offset to be read at: `offsets_of` says where each one lies, `_field_place`
turns that into an address, and `&p.x` is then an address like any other.  A
field behind a reference is read the same way, which is the only way one can be
read at all -- loading the whole record first would be a load of a multi-part
value, which the back ends do not do.

**Binding one writes the fields.**  `_record_into` walks the layout and stores
each field, `_record_from` walks it and reads each back; a field that is itself a
record recurses.  Two names are two records, which is what a record being a value
rather than a place means, and the copy costs one store per field rather than a
call to something that moves bytes.

**What travels between functions is unchanged**: `parts_of` answers a record with
its field types, so a convention places it as it places a tuple, and anything
larger than two registers goes through storage the caller provides -- which
`largeanswers` already did and is where the value semantics of a call and an
answer come from.

**A record holding a record needs no register at all.**  A record cannot be a
*value* there -- a part that is itself several values has nowhere to be read
into -- so a literal is written into the place that will hold it rather than
made and then copied: `_build_record` walks the fields, stores each one, and for
a field that is itself written out recurses into that field's place.  Nothing is
an economy about it; it is the only way the value can exist.  Reading follows
the same offsets, so `l.to.x` is one load and `&l.to.x` one address.

**What travels is leaves and not fields.**  `parts_of` answers a record with the
values it is made of, and a field that is itself a record is spread out where it
stands rather than counted as one: a part that was several values would be a
part nothing could put in a register.  So `parts_of(Line)` is four `u32`s, and
`extract` on such a value takes the *n*-th of them.  `part_offsets_of` says where
each of those lies in a place holding the record, walking to the same leaves and
adding where the field holding each lies; `largeanswers` reads it, so a record
whose answer goes through storage is written and read back by the same rule the
checker builds one with.

That leaves a value made of a record value, and the two directions are one
helper each: `_leaves_of` takes a field that is itself a record apart into its
own leaves, so a literal builds one flat tuple; `_leaves_from` reads a place the
same way.  Where a record arrived as a value and a field of it is wanted -- what
a call answered with -- it is written into a frame first and the field read out
of that, so one rule reads a field however the record got here and a field of a
field needs no second one.

**One field is not one value.**  Whether a value is several travelling as one is
asked of the type -- `made_of_parts`, which is `parts_of(ty) != (ty,)` -- and not
counted, because a record of one field is made of parts and has one of them while
a `u32` is not made of parts at all.  Every place that chooses between the
one-register path and the part-by-part one asks it: the three instruction
selectors at a return, at a call's arguments and at its answer, the parallel copy
a branch's arguments are, and the verifier's rule about making a value and taking
one apart.  Counting was the question before, which is why a record of one field
reached the back end looking like its field and was refused there.

**An `elif` asks its condition in a block of its own**, and a condition that
writes -- which is what such a frame is -- leaves a memory token only the arms
below it may read.  Each arm therefore begins with the token current where the
branch to it was made, and what falls past every condition begins with the last
one's; before this there was one token for all of them, which was the entry
block's only because nothing asked in an `elif` had ever written.


The I/O runtime, compiled ahead of time
---------------------------------------

The runtime is C, in `runtime/io.c`, and is compiled once for every architecture
the compiler generates for.  What is packaged with the compiler is neither the C
nor an object file but **the code and its relocations**: `bin/pl4g-runtime` runs
clang for the three triples, reads the objects and writes
`pypl4g/runtime/<architecture>.py`, a `Blob` of pieces, the names in them, and
the patches to fill in once the pieces have addresses.  So building a pl4g
program needs no C compiler and the compiler needs no reader for relocatable
objects; only changing the runtime needs either.  A test recompiles and
compares, which is what stops a package falling behind the source it was made
from.

One compiler for all three rather than three cross-compilers: what has to be the
same about the three objects is easier to believe when one thing made them.  It
is built freestanding, with no builtin calls -- a call to `memcpy` would be a
call to something that is not there -- and, on RISC-V, with relaxation off,
those relocations meaning "a linker may shorten this" and there being no linker.

**A value of several parts is several values one after another in memory.**  A
load of one is one load per part and a store is one store per part, each at the
offset `part_offsets_of` gives -- which now answers for every shape and not only
for a record: a result keeps its truth value and its error where the layout puts
them, and a string, a list or an array whose type does not say its length is its
parts in order, each where its alignment puts it.  Before that a record could not
hold a string at all, which is what `args` needed and what surfaced it.

**The two declarations of the shared record are compared.**  `runtime/io.c` and
`modules/std.pl4g` each declare the ring, in two languages, and nothing makes
them one thing -- so the C exports `pl4g_io_shape`, a run of words saying how big
the record is, how it is aligned, and where each field is *and how wide*, and a
test works the same numbers out for the `@[abi]` record the module declares.
Where alone would not do: a field made narrower can leave every offset where it
was, padding taking up what it gave back, and a reader of the wrong width is
exactly the bug this is here to catch.  A field added on one side and not the
other is then a failing test rather than a program reading the wrong word.

**Placing it is the machinery that was already there.**  `target/runtime.py`
puts each piece in a section of its own -- `.pl4grt.text` and whatever else the
object had -- defines the names in it by cutting the bytes where a name falls,
and hands each run of bytes to `asm.bytes` with the patches that lie in it
turned into `MCFixup`s.  From there the runtime is laid out and patched exactly
as the compiler's own code is, and nothing about it is special.  It is emitted
only where a call in the program names something it defines, so a program that
does no I/O carries none of it.

A patch measures **to** the piece it names plus how far into it, and **from**
itself unless the format said otherwise -- which is what RISC-V needs, computing
an address in two instructions of which the second is written against the first.
The extractor resolves that pairing, so what is packaged already says what it
reaches rather than naming another relocation.

**The runtime is built position-independently**, `-fPIE` on two architectures
and `-mcmodel=medany` on the third, because it is placed wherever the compiler
is putting things: anything absolute would be a promise about an address nobody
has made.  That is also what makes the relocations exercised rather than
assumed -- each architecture reaches its own constant table through one.

**A relocation becomes a fixup the target already has.**  `FROM_ELF` in each
target's `fixups.py` says which relocation number is which kind, and `BY_NAME`
says which kind a packaged patch names; a relocation the table has no kind for
stops the extraction rather than being filled in wrongly.  Only one kind had to
be added for the runtime as it stands -- the family of load-and-store offsets
AArch64 scales by the width of the access -- and two architectures need no
relocations at all.

**The clocks go through the vDSO.**  `pl4g_args`, which every program's entry point calls with the stack it was started
with, also records where the vDSO is: `AT_SYSINFO_EHDR` in the auxiliary vector after the environment.  The first call of
`pl4g_clock_gettime` or `pl4g_clock_getres` reads that image as the kernel laid it out -- the program headers for where it was
linked against where it is and for the dynamic section, that for the symbols and their strings and a hash table to count them
by (`DT_HASH`, or `DT_GNU_HASH`, whose chains say where the last symbol is) -- and keeps the two functions it finds:
`__vdso_clock_gettime` and `__vdso_clock_getres` on x86-64 and RISC-V, `__kernel_clock_gettime` and `__kernel_clock_getres` on
AArch64.  Each is then called as C calls it, and answers nought or the error negated, as the system call does; where one was not
found -- no vDSO, as in a shared object, which never sees the stack -- the call is the system call, `clock_gettime` (228 on
x86-64, 113 on the others) or `clock_getres` (229, 114).  `std.time` is the module that reaches them, its two functions
turning the negated error into POSIX's number; the interpreter calls the C library's functions in their place.

Saying which tests failed
-------------------------

Every test a binary runs is run.  A failing one writes its message through
`__pl4g_report`, which is `__pl4g_abort`'s write and then a *return*, and the
count of them is kept in a register a call leaves alone -- `ebx`, `x20`, `s2`,
all callee-saved in the conventions this compiler generates -- so nothing has to
be saved around a call and no storage has to be found for a number that lives for
a few instructions.  Afterwards, a count that is not nought exits with 66.

That is emitted only where a test runs: a write that comes back is of no use to
anything else, so `emit_report` is asked for beside `emit_abort` and only when
some test has a message.

The ring, and where it is written
---------------------------------

It was written in pl4g first, in `modules/std.pl4g`, and that is where the
language learned what driving a ring needs: a record holding a record and handed
to a call, a record of one field, a field that may be assigned to, a record at
the top level, `⎕at` and `⎕span`, `⎕acquire` and `⎕release`,
`⎕widen`, `⎕address`.  Every one of those was asked for and decided on its own
terms, and every one of them stayed when the ring moved.

It is now C, in `runtime/io.c`, at the user's direction.  What it offers is
three things: **submit**, which takes a slot in the ring's own table and writes
the request without telling the kernel; **wait**, which tells the kernel and
reads the answer out of that slot; and **drain**, which waits for every slot
that is outstanding.  A slot is taken either way -- with a ring and without one
-- so the handle a program holds is the same thing whichever it got, and where
there is no ring the answer is put in the slot as the work is done.

**`io_uring_enter` asking for one answer is not being given one.**  It may come
back with nothing ready, and what makes that right is asking again: `wait` loops
until the slot it cares about is answered.  The drain did not, and broke on a
turn that found nothing -- so a write nobody had waited for was abandoned and
the process exited with it still in flight.  To a terminal or a pipe it landed
anyway and to a file it did not, which is what made the bug look like a
difference between devices rather than a race.

**The ring is drained before the process ends**, which is what "exits normally"
means.  It was a destructor in `std` while the ring was a variable at the top
level of `std`; since 2026-10-04 the ring is a field of the record a program is
started with, and the entry point calls `pl4g_io_drain` with where it is, after
the destructors.  The destructor was what first exercised one that *calls*
anything, and it found a second bug -- on x86-64 the exit status was moved into the register a system
call takes its first argument in *before* the destructors ran, and an ordinary
call puts its own first argument there.  The status now waits in a register a
call leaves alone, and only where a destructor or the drain is going to run --
which is every program now.

**qemu-user answers `io_uring_setup` with `ENOSYS`**, so a ring can never run
under the emulators two of the three targets are tested with.  The runtime
therefore tries once and falls back to the plain `read` and `write` calls: every
language test runs on all three targets, the native run exercising the ring and
the emulated ones the fallback.  Whether there is a ring is not something a
program can see.

What another observer sees
--------------------------

A load may **acquire** and a store may **release**, said on the instruction
(`Ordering` in `ir/inst.py`) and written `load.acquire.u32` and
`store.release.u32` in the textual form.  The memory token says that two
accesses of this program happen in an order; this says what a second observer --
a kernel reaping a ring, another thread -- may see, which the token does not.
The verifier refuses a load that releases and a store that acquires, each
promising something about the wrong side of itself, and an acquiring load counts
as having an effect although a plain one does not.

`target/ordering.py` is the one rule about which accesses may carry an ordering:
a value of several parts is several accesses and which of them the ordering
belonged to has no answer, and floating point is refused because the ordered
forms name integer registers.  Both come back as 8501 rather than as a crash.

Below that each machine answers for itself, through `Assembler.acquire` and
`Assembler.release`:

- **x86-64** emits what it would have emitted.  Its reads are already acquiring
  and its writes already releasing; the one ordering that would cost it an
  instruction is a write followed by a read of another place, which nothing asks
  for yet.
- **AArch64** emits `ldar` and `stlr`, in the four widths.  They carry no offset
  -- the address is a register and nothing else -- so `_flat_address` adds the
  offset first, into the destination for a read and into a register of its own
  for a write.
- **RISC-V** has no acquiring form of a plain load, those bits belonging to the
  atomic instructions, so it emits the fences the architecture asks for:
  `fence r, rw` after the read and `fence rw, w` before the write, one table row
  carrying all twelve bits of either.

What a program is started with
------------------------------

The startup function takes exactly one parameter, not `mut`, of exactly one
type: the record the `std` module calls `Init`.  `_check_startup_signature` in
`sema/check.py` refuses each other shape with its own wording of 4404 -- no
parameter, more than one, `mut`, a reference, another type, another answer --
so the message says what to change.  Which type that is is settled by where it
was written down -- `modules/std.pl4g` beside the compiler, found through
`system_modules()`, which is also what a `SearchPath` holds when it is made
without one -- and not by its shape, so a record a program defines for itself
and calls `Init` is a record it defined for itself and is refused.

Every program therefore imports `std`, and **the entry point drains the ring in
every program** -- the ring being a field of the record, `init.fs.ring` -- so the
packaged runtime is in every image.  The runtime is placed whole, so an image
holds all of it -- some four kilobytes of code on x86-64 -- however little of it
the program calls.
`dropunreached` keeps no variable for a function whose body is elsewhere -- what
is compiled elsewhere names only what the image offers, which is a root anyway --
so `std`'s variables nothing reached are not carried, and the ones the compiler
made for itself are dropped without a report, nobody having written them.

`Init` holds an `Io`, which holds three devices, the `std.fs.State` holding the
ring, and the words the program was named with.  **What the image carries is the
record**, an object in the writable data called `__pl4g_init`, laid out by the
program's own declaration of the type.  The fields are found **by name**
(`target/started.py`): `io`, whose every member is a device whose `fd` is known
before anything runs and is written into the image, and whose `ring` is the
address of `fs.ring` inside the same object -- an `ABS64` fixup, settled when the
image is laid out, so nothing is worked out at start; `fs`, all noughts, which is
a ring not yet made and an empty task list; and `args`, whose two words the
runtime fills in when the program starts.
`offsets_of` and `part_offsets_of` say where they go, which is the same place a
field read anywhere else comes from, so a field added to `Io` is one the entry
point reaches without being told where.

**Reading the arguments is three instructions and a call.**  What the kernel
leaves at the stack pointer is the count, then that many pointers, then a null --
and that is the only moment the stack pointer says so, since everything after it
puts something there.  The entry point keeps it in a register a call leaves alone
until the constructors and the level check are done, then hands it to the runtime
along with where in the record the run of strings goes.  The runtime counts each
one and asks the system for room to put them in; it is never given back, lasting
as long as the program does, which is as long as what a program was named with is
worth having.

Two things that bit while writing it.  The call follows **the system's**
convention and not the language's, and on x86-64 those put their first arguments
in different registers -- the callee's is the one that decides.  And on AArch64
the stack pointer and the zero register share an encoding: a move between
registers reads it as the zero, and an addition of nothing is the form that reads
it as the stack pointer.

`target/started.py` is where the rest lives, so each of the three entry points
only has to know how to put an address in a register.

A program that writes no parameter is started with those registers as the kernel
left them, which is most of them and is what every test written before this does.

Asking the kernel
-----------------

`SyscallInst` is one instruction and not a call: what enters the kernel is one
instruction of the architecture, what it takes is named by the kernel rather
than by any calling convention, and nothing about it is the language's to
choose.  It is outside the memory token chain, as a call is and for the same
reason -- what the kernel does to memory is not something the program can write
down, so it is kept by saying the instruction has effects.

**Everything in one is a machine word before a back end sees it.**  The checker
widens a narrower number by its own signedness and reads a reference as the
address it is, so each selector has one shape and no widening of its own: move
the number and the arguments into the registers the kernel names, enter, take
the answer.  The verifier learnt that an address read as a `u64` is the same
bits, which is what that reading is.

**The numbers live in one table and nowhere else.**  `target/syscalls.py` holds
what each call is numbered on each architecture: two of the three share the
numbering the kernel calls generic, so that is written once and x86-64 is
written out.  What is in it is what the runtime and the standard library ask
for, each number checked against the kernel's own table; a call nothing has
asked for is not there, and adding one is a line.  It is somebody else's table
and is kept small and honest for that reason.

**`SyscallABI` already said where everything goes.**  The number register, the
argument registers, the answer register and the entering instruction were
written down per target for the allocator's own `mmap`; what this added is what
the entering instruction destroys, which is nothing on two of the three and is
`rcx` and `r11` on x86-64 -- the instruction's doing rather than the kernel's.

**The assembler gained `kernel`**, which emits the entering instruction and then
says what it destroys and what it reads.  `op` cannot say either, and a register
holding an argument would look dead from the moment it was written if nothing
said the kernel wanted it; `call` is the shape it copies.

Tests
-----

`@[test(ARG)]` marks a function as a test, and `ARG` says which of three kinds
it is.  Written with no argument and no parentheses it is a `suite` test, that
being the kind most tests are.  A test takes nothing and answers whether it
passed (4407): nothing calls one but the runner, so there is nothing to give it,
and a truth value is the whole of what it has to say.

**What differs between the kinds is which binary the test is in**, not what the
test says.  That is why it is settled in one place, `target/tests.py`, which
answers what a binary runs and what it says when one fails; each back end asks
it and emits the same shape of code around the answer.

| kind | which binary it is in | when it runs |
| --- | --- | --- |
| `always` | the program | before the startup function is reached |
| `build` | one the compiler builds to run it | when a build finishes |
| `suite` | one the compiler builds to run it | when `pypl4g test` asks |

**An `always` test rides the constructor path**, which already called things
before the startup function.  What it adds is that the answer is looked at: a
test that answers false leaves through `__pl4g_abort` with its own name, the
same helper a fault leaves through, since a program that has been found to be
wrong is what that helper is for.  The answer is compared at the width the
compiler's own calls compare it at -- a truth value comes back widened to the
whole register.

**A test binary is the same module with a plan.**  `Module.test_plan` names what
its entry runs; where it is set the entry calls those instead of the startup
function and exits zero if it reaches the end.  It is also what the reachability
pass takes as roots, which is how a `suite` test stays out of the program: in
the program it is code nothing can reach, and leaving that out is what the pass
is for.

**The plan is chosen before the passes run**, and a test binary is built from
the sources over again rather than from the module in hand -- the passes have
already left out of that one everything the program does not reach, and a test
the program does not call is exactly what they left out.

**Running one needs a machine that runs it.**  `pypl4g test` and a build with
`build` tests in it run a temporary binary; where the target is not this machine
there is nothing to run it with unless `--test-runner=COMMAND` names one, and
the build finishes with a warning (1014) rather than refusing.  Refusing would
be refusing the ordinary case, which is cross-compiling.

**A run stops at the first test that fails.**  It is the abort helper doing what
it does, and what it costs is the list of everything else that would have
failed.  Running them all and reporting each needs a way to write a message and
carry on, which the runtime has not got: it has one helper, and that helper
exits.

Running a function at compile time
----------------------------------

`pypl4g/comptime/` is an interpreter for the language, over the **syntax tree**
and after the checker has been over it.  What asks for one is the build function,
which the compiler runs rather than compiles; what it answers is the values,
since the checker has already answered everything about the types.

**Why the tree and not the representation.**  The representation is nearer the
compiler's own idea of the program and would have been the other choice: its
instructions are few and their meaning is settled, so an interpreter for it would
implement the representation rather than the language a second time.  What
settles it the other way is memory.  A string joined to another is one Python
string joined to another over the tree; over the representation it is an arena, a
call into the allocator -- which is emitted as assembly per target and has no
representation at all -- and a model of the heap to put the answer in.  The whole
point of a compile-time evaluation is that none of that happens, so the tree is
where it is done.

**What it does.**  Numbers, truth values, characters, strings, arrays and lists,
records, references, `let` and assignment, `if`, `while`, `foreach`, `break`,
`continue`, calls to functions of the same program, and calls to what the
compiler provides.  Control flow is read as an *expression*, because that is what
it is in this language: a loop answers with what a `break` carried, so `_expr`
handles `If`, `While` and `ForEach` and the statement level is the few things
that really are statements.

**What it does not do, it names.**  A match, a lambda, a set, a dictionary, a
`?`, a lifted type: each answers with the words for it (7000) rather than with a
wrong value.  The list is one table, so adding one of them is an entry there and
the code for it.

**Numbers are worked out without a width.**  The checker has settled the types
and the ranges of the literals already; what the evaluator computes with is whole
numbers, so an intermediate that would not have fitted does not stop it.  Where a
value is finally used -- put into a field of the build object -- what it has to
fit is checked there.  `TODO-pypl4g.md` has the entry for doing better.

**It has a ceiling rather than a floor.**  A hundred thousand steps and a hundred
and twenty-eight frames: past either, the evaluation is given up on and reported
(7001).  A compiler that hung on a loop with no end would say nothing at all,
which is the one outcome worse than refusing.

One pool of literals for the whole image
----------------------------------------

**The bytes of a string literal are a global, and the pool they are named out of
belongs to the module and not to whatever is reading a file.**  `Module.texts` holds
one entry per distinct text and the name is its index in that pool.

That is not a detail.  A file this program imports is read by a `Checker` of its own,
and its literals go into **this** image -- the module is shared, the checker is not.
A pool per checker therefore numbered two different texts alike, `__pl4g_text.0`
twice, and the second definition took the first's bytes: a module's literal came out
as whatever the program had written last.  It was found by `std.println`, whose
newline is a literal in `std` and printed as the caller's text instead.

Nothing else in the compiler numbers symbols per file, which is why this was the only
one of its kind; a test compares a module's literals with the program's.

Running a macro at compile time
-------------------------------

**And there is a second interpreter, over the representation.**  `pypl4g/front/expand.py`
is the stage between parsing and checking, and where a macro is written as a function it
hands the work to `pypl4g/front/interpret.py`, which runs the **representation** and not
the tree.  So the compiler has two compile-time machines with two answers to the question
above, which is worth explaining and worth fixing.

**Why the representation here.**  The argument that settled the build function the other
way is memory, and a macro has none: what it computes with is pieces of the program,
whole numbers and truth values, and it never joins one string to another.  What decides
it instead is that a macro *is part of the program* and must be held to every rule the
program is held to.  So `check_macros()` puts the macros and the `comptime fn`s through
the same checker and the same lowering the program goes through -- one `Module` of their
own -- and what runs the result holds no knowledge of the language at all: values are
Python integers and tuples, memory is one growing `bytearray`, and there is one arm per
opcode.  A macro's types are checked because the checker checked them, not because a
second implementation of the language was written to agree with the first.

**What it does.**  Every opcode the front end emits for a function of this shape:
arithmetic and comparisons over integers, casts, calls, branches and block parameters,
records and tuples read and written, results wrapped and unwrapped, and a frame slot for
anything the lowering put in memory.  A piece of the program is a handle, which is what
makes `syntax` one value wide.

**It has a ceiling too**, of a million steps, reported as the macro not coming to an end
(7023).  A macro the checker or the lowering refused is reported as one that cannot be run
(7024), with the reason.

**Hygiene is two walks over a quote, and a hole is filled after both.**  `_renamed` gives
every name a quote binds a name with `#` in it, and `_reached` writes a name the module
defines at the top level as a path through the module.  What fills a hole is the caller's
and is walked by neither, which is all `⎕caller_name` needs: it answers a handle to a
fresh `NameRef`, which reaches the tree only by filling a hole and so is the caller's as
surely as anything the caller wrote.  Its position is looked for in the invocation
(`_written_at`): the first literal handed over holding the name in braces, and the name's
own bytes in it where the literal is exactly as long as what it holds and its two quotation
marks -- a literal with no escape in it, where a place in the text is a place in the source.

**The two should be one.**  Neither is the other's superset today: the build machine has
strings, collections and a heap the macro machine has no need of, and the macro machine
has the property the build machine gave up -- that what it runs was checked and lowered
by the compiler rather than read a second time.  `TODO-pypl4g.md` has the entry.

The build function
------------------

**A file holding one describes a build rather than being a program.**  The
checker records it on the module the way it records the startup function, refuses
a second one (7002) and checks its shape (7004); a module with one needs no
startup function, since nothing of it is ever started.  The driver, having
checked the file, runs the function instead of generating code, and then compiles
what it asked for -- one `Driver` per artifact, sharing this one's diagnostics and
source manager so that a run says what went wrong wherever it was.

**What the function is handed** is a `Record` the evaluator holds, whose fields
are the ones `std.Build` declares.  The defaults are what the command line said,
so a build file says only what it means to decide; the fields are read back when
it returns.  What cannot be a field is the run of things to build -- the language
has no growing list of records yet -- so that is `std.add_executable`, one of the
three functions the compiler provides while a build is worked out.  They are
declared in `modules/std.pl4g` with `@[builtin]`, which means no body and no
symbol: a program that called one would be calling something that is not there,
and the checker says so (7006).

**Which compilation is a build** is settled twice, in the two places the two ways
in are: the command line puts `build.pl4g` in the inputs where nothing else was
named (1018), and the driver looks for the build function in whatever was named.
`Options.from_build` is what keeps the second from running again for each
artifact.

The Command Line
----------------

`argparse` does the parsing; `share/options.json` says what there is to parse.
The two are kept in step by a test rather than by generating one from the other:
the table is the contract between this compiler and any other, and what a
contract needs is both sides checked against it, not one side built from it.  A
test compares the option names `argparse` was given against the names the table
declares, in both directions, and another does the same for the commands.
`--help` is still rendered from the table, so that two implementations print the
same list.

**Every refusal is a numbered diagnostic.**  `argparse` writes prose of its own
and exits; here it does neither.  `ArgumentParser.error` and `.exit` are both
overridden -- the first turns what it found into an entry of the shared catalog,
the second raises -- so that a build reading the errors sees the same numbers
whichever compiler produced them.  One number, 1013, carries `argparse`'s own
words for the things no other number covers, since inventing a sentence for
something that has one already says the same in worse words.

**Two forms are written out before `argparse` sees them.**  A short option whose
value is stuck to it and a long option whose value may be left out are both
places where `argparse` takes the *next* word instead: `--color prog.pl4g` would
colour "prog.pl4g" and compile nothing.  So `-O` becomes `-O1` and `--color`
becomes `--color=yes` first, and after `--` nothing is touched.

**What `argparse` accepts that the table does not describe** is the separated
form of an option the table calls `joined_equals`: `--emit asm` as well as
`--emit=asm`.  `argparse` cannot be told to refuse it.  It is a widening and not
a narrowing -- every command line the table describes still works, and a build
system writing the documented form is right with either compiler -- so it is
recorded here rather than fought.

**A command line names what the compiler is to do**, and one that names nothing
means `build`, which is what every command line meant before there were any.
The word is looked for in the first position and nowhere else: an option's value
may be spelled like a command, and `-o test prog.pl4g` writes a file called
`test`.  `test` is declared and reports that it is not implemented yet, the way
`--incremental` does; what it needs is something to run the functions
`@[test(...)]` marks, which are collected as reachability roots today and
nothing else.

**The output no longer has to be named.**  The sources say what the program is
called, so `pypl4g prog.pl4g` writes `prog`, and `--emit` decides the suffix --
`.s` for assembly, `.ir`, `.ast`, `.tokens`, and nothing at all for an
executable, which is what every compiler on a system without file types does.
Diagnostic 1001, which said the output was missing, is retired; an empty command
line names no source, and that is the thing actually missing.

Colour in a diagnostic
----------------------

Colour is decoration and never information: everything a colour says is said by
the text as well, so a terminal that shows none loses nothing and the suite
compares the two and finds the same characters under the codes.  That is why the
default is to look, and why `NO_COLOR` is honoured whatever `--color` says, a
program reading this output being the one case where the decision has already
been made.

**`--color[=WHEN]`** takes `yes`, `no` or `auto`, and written with nothing after
it means `yes` -- a switch with no value asks for the thing it names.  `auto` is
the default and looks at the **standard output**, although the diagnostics go to
the standard error.  What that answers is "is a person watching this run", which
is a question about the run rather than about one of its streams: a build that
keeps the errors in a file is still a build someone is sitting in front of.

**The eight colours and the two attributes**, and no more.  A palette of 256
would look better where there are 256 and worse where there are not, and what is
gained is a shade.  What is coloured is what a reader looks for: the severity,
the place, the carets, and the pieces of the source line that carry meaning --
keywords, types, numbers, strings, comments.  Operators, brackets and ordinary
names are left as they are, a line in which everything is coloured being one in
which nothing stands out.

**The snippet is highlighted by the grammar**, not by the compiler's own lexer.
A lexer knows a name is a name; the grammar knows that this one is a type and
that one a parameter, and the queries beside it are the ones an editor already
uses -- so a line in a diagnostic is coloured the way the same line is coloured
where it was written, from one description of the language rather than two that
drift apart.

**Of two patterns over the same thing, the one written later wins.**  That is
what tree-sitter's own highlighter does and what Neovim does, so it is what this
does: the sort that puts the runs in order breaks a tie by taking the *later*
pattern of the query file.  It read them the other way round at first, which
made the compiler the only thing in the world that coloured `u64` as a type --
an editor and `tree-sitter highlight` both took the line that calls every name a
variable, because it stood below.  The queries are now written coarse-first, and
that one rule is what all three of them follow.

**The whole file is parsed, not the line.**  One line of a block does not parse
on its own, so what is asked is always about the file, the answer is cut to the
line afterwards, and it is remembered per file: a hundred diagnostics in one
file are one parse.

**Where the two ways of counting meet.**  tree-sitter counts bytes and
everything above counts characters, and this language is written in glyphs of
three bytes -- so a run measured in one and used in the other lands in the
middle of a bracket.  `Highlighter` converts once per file, with a table from
byte to character built in the same pass, rather than per capture.

**Nothing of it is required.**  The grammar is loaded on the first snippet that
wants colour, and where the module or the built library is missing -- or where
the library was built for another version of tree-sitter, or a query names a
node the grammar has not got -- there is simply no highlighting.  A compiler
that said it could not colour something would say it about every line of every
diagnostic, and none of it is about the program.

A table shaped by what it holds
-------------------------------

`sema/tables.py` generates the hash table a set and a dictionary are, and what it
generates now depends on **what the table holds**.  `Shape` is that question
asked once: the key's type and the value's, and from them where the value of an
entry begins, how long an entry is, and what the generated functions are called.
Making one and walking one are the same for every table -- both read the stride
the table carries -- and the probe, the insertion and the set operators are
generated per shape and named for it, so a dump shows `__pl4g_table_slot.str` and
there is no wondering which instantiation it belongs to.

**A key is hashed by the runtime, with the table's seed.**  `pl4g_hash(bytes, count, seed)` is rapidhash in `runtime/io.c`, and
`pl4g_hash_word(word, seed)` the same over a word's eight bytes; `_hashed` calls one or the other, by `key_ir_type`, with what the
block's `SEED_FIELD` holds -- the seventh word, after `used`.  `pl4g_hash_seed()` hands out a seed no table has had: a count of those
handed out mixed with a base `hash_start` works out from the stack `pl4g_args` is handed, the number `PL4G_HASH_SEED` says in the
environment or the sixteen bytes the kernel points `AT_RANDOM` at in the auxiliary vector.  `__pl4g_table_new` writes one,
`__pl4g_table_grow` a fresh one for every rebuild, `put` one where the field says nought -- a table laid out in the image empty, which
`_empty_table_constant` writes so -- and the probe reads it.  A table laid out in the image with its keys has `tables.IMAGE_SEED`,
and the checker places its entries with `sema/rapidhash.py`, which is the C over again; `tests/compiler/test_hash.py` compiles the
hashing part of the C for the machine the suite runs on and holds the two to each other at every length the hash reads differently.
The comparison of text keys is still the function `=` uses, so a key found in a table and a `=` written in a program cannot disagree.
The RISC-V runtime stores to the seed's counter with an `R_RISCV_PCREL_LO12_S` (`PCREL_LO12_S`), which the packaged runtime had not
needed before, and its eight-byte constants are in `.pl4grt.rodata.cst8`.

**A result may now hold a value of several parts.**  `d⸨k⸩` answers `V?`, so a
dictionary whose values are strings makes one -- and a result was two registers
in the backends, the answer and the truth value, wherever the answer was one
value.  It is now the answer's own registers, the truth value after them, and the
error's after that; `_answer_registers` is what counts the first of those, and
everything that reads a result asks it rather than counting from one.  `parts_of`
is unchanged: it says what a result *is* (an answer, a truth value, an error), and
how many registers the answer takes is a question about registers.

That change cost an afternoon to a one-line mistake worth recording: the new arm
walked the answer's parts with a loop variable called `index`, which is the name
the enclosing loop uses for *which block is being lowered*.  The branch at the
end of that block was then told the wrong following block, so it fell through
into the arm it should have jumped over -- a miscompile with nothing wrong in the
instruction that was emitted.  The loop variable is called `at` now, and the
comment beside it says why.

Two spellings for a type, and who reads which
--------------------------------------------

`Type.render` is the IR's: `ptr<mut Pair>`, `u64?i32`.  `Type.written` is the
language's: `&mut Pair`, `u64 ? i32`.  Every message, every note the language
server shows and every line of the report log asks for the second; the printer,
the reader and a mangled symbol name ask for the first.

It is two methods and not one changed method because **the IR's textual form is
read back as well as written**.  `ir/printer.py` and `ir/reader.py` are two
halves of one format, and a printer that wrote `&mut u8` where the reader expects
`ptr<mut u8>` would be a format that no longer round-trips -- so the spelling a
person reads had to be the new one rather than the old one moved.

`written` defaults to `render`, which is right for most types: the IR borrowed
the language's own notation wherever it could, so an array, a tuple, a list, a
set, a dictionary and a string are the same either way.  What differs is a
reference (`&mut T` against `ptr<mut T>`), a result (`T ? E`, with the spaces a
signature has, against `T?E`), and a function type, whose `listable` is written
as the attribute a program writes.  Every type that holds another overrides it
too, so that the element of a list of references is written the language's way
as well -- a default that recursed through `render` would have said `[ptr<mut
u8>]`.

Two types have no spelling at all: a cursor, which lives in a name whose type is
read off its value, and a vector, which is what an operator walked over an array
works on.  A message about either says what it *is* -- `cursor over [u8]` --
rather than a spelling nobody could have written.

Callees first, and what that buys
---------------------------------

`ir/callgraph.py` answers one question -- who calls whom -- and two things rest
on the order it gives.

**The backend generates a callee before its caller.**  What a call destroys is
asked of the *finished* callee: a register not among what it wrote still holds
what it held, so nothing has to be saved around the call.  A caller generated
first had to assume the convention's whole caller-saved set instead, which is a
save and a reload at every call to every function defined later in the file.
The order is a depth-first walk left in the order it finished, which is the
topological order where there is one; round a cycle there is none, and the
functions of it come out in the order the walk met them, each assuming the
convention's set for the callees it has not seen.

That ordering turned up a bug of the kind it was meant to make impossible.  The
register allocator rebuilds an instruction whose registers it changed, and the
rebuild carried neither `clobbers` nor `reads` -- so **every** finished function
looked as though its calls destroyed nothing, and `clobbered_units` answered
with what the function itself wrote.  Nothing noticed while callers were mostly
generated before their callees; with the order reversed, a caller of a function
that calls through a value kept a value in a register the call overwrote.  Both
rebuilds carry them now, and a test in `test_regalloc.py` holds them there.

Branching on the parts of a condition
-------------------------------------

**`if a ∧ b:` is two comparisons and two branches.**  The checker writes a condition joined with `∧` or `∨` in one of two shapes:
where the second operand costs nothing to work out and cannot stop the program, both truth values and an `and.bool` or `or.bool`
of them; where the second waits for the first -- an element read only where the index was said to be inside -- a block working it
out, a block answering the constant without it, and a block where the two meet that takes the answer as a parameter.  The branch
then tests what came out, which on every machine here is a comparison set into a register and the register tested: `setb`,
`movzbl`, `and`, `test`, `je` for what is `cmp`, `jb`.  The pass `conditions` (`opt/passes/conditions.py`, -O1, after the second
folding and before `facts`) rewrites both shapes until neither is left:

- a branch on a truth value made with `∧`, `∨` or `¬`, read by nothing else, becomes a branch on the first operand into a new block,
  laid out after it, that branches on the second -- for `∧`, the first being false goes where the whole being false went; for
  `∨`, the first being true where its being true went; `¬` swaps the two ways.  What works out the second operand moves into the
  new block where nothing else reads it and nothing in it touches memory, a load staying where it stands since what it reads is
  ordered by the token and not by position;
- a block that is nothing but a branch on its own parameter, the parameters read nowhere else, is gone through: a jump into it
  carrying a value becomes a branch on that value with the block's targets, a branch carrying a constant goes where the constant
  goes; and a block that is nothing but a jump, taking nothing, is gone through likewise, except from a branch of two ways where
  what the jump carries would only be given a block again to move it in.

What is left nothing reaches, and is taken out before the pass ends: a block nothing reaches still reads what it read, and what it
read may now be defined on no way to it.  Before `facts` because a part branched on is a fact on the edge it settled: in `while at
< n and bytes⟦at⟧ ≠ 32u8`, `at < n` holds on the way into the block that reads the element, where before it was half of a truth
value.  A truth value that is answered or stored is left as it was made, nothing branching on it.

Dividing by a constant
----------------------

**`x ÷ 10` is the upper word of a product and a shift.**  The pass `divisions` (`opt/passes/divisions.py`, -O1, after simplifying the
graph and before the second folding) rewrites `udiv`, `urem`, `sdiv` and `srem` whose divisor is a constant other than nought --
and, signed, other than minus one, the one divisor with a dividend it has no answer for -- into ordinary arithmetic every back end
already has.  A division the language writes answers a result; one by such a constant never fails, so it is made the answer beside
a `false`, and folding reads through that: `failed` of a result made of its parts is the truth value it was made with and `unwrap`
its answer (not an answer that is `undef`, which stands for nothing).  Everything is done at 64 bits, a narrower dividend widened
first and the answer narrowed after:

- by one, the dividend; by a power of two, unsigned, a shift, signed, a shift after adding `2^k - 1` to a negative dividend, so
  that it rounds toward nought, and negated for a negative divisor;
- otherwise unsigned, Granlund and Montgomery's multiplier: the smallest `s` at which `m = ⌈2^(64+s) ÷ d⌉` fits a word and `(m × d
  - 2^(64+s)) × largest < 2^(64+s)` for the largest dividend the type holds, and the quotient is `⎕wide_mul`'s upper word shifted
  by `s` -- for a type of 32 bits or fewer always `s = 0` and no shift.  Where no `m` fits, an even divisor is tried again on the
  dividend shifted down by its twos, and an odd one takes the multiplier a bit too wide, `t = hi(x × (m - 2^64))` and
  `(t + (x - t) ÷ 2) » (s - 1)`;
- otherwise signed, Hacker's Delight's multiplier and shift, the signed upper word (`⎕wide_mul` of an `i64`), the dividend added
  or taken away where the multiplier's sign is not the divisor's, the arithmetic shift, and the sign bit added.

A remainder is `x - q × d` with wrapping arithmetic, which cannot wrap.  `samevalues` runs again after it, so `x ÷ 10` beside `x %
10` multiply once.  `tests/language/divides-by-a-constant` compares every quotient and remainder by some ninety divisors of ten
types with the machine's division, over the ends of each type and the neighbours of each divisor, on all three targets.  `tenth` in
`std.string` is now what the compiler writes for `÷ 10u64` and stays as it is.

Putting a callee where it was called
------------------------------------

The inliner walks the same order, callees first, so a callee it reaches is one
whose size and whose calls are final -- which is most of what an inliner needs
and all of what a cheap one needs.  Nothing is looked at twice and nothing round
a cycle is looked at at all.

**What it inlines**: what the program said to (`@[inline]`), whatever the size;
what the whole program calls once and nothing outside can reach, since the copy
is then the only one there is; and what is small -- sixteen machine
instructions as `cost_of_body` counts them, a handful more than a call is made
of, and thirty-two for a leaf, a function that calls nothing.  A leaf is worth
more than its size: across a call its caller keeps nothing in a register the
convention lets the callee destroy, and a leaf copied in destroys only what it
uses.  A caller may grow by three hundred in total, so that a function calling
many small ones does not become one enormous one -- and what it grows by is only
what makes the program larger: a copy of a function called once is the only
copy there is, and a copy no larger than the call it replaces costs nothing, so
neither is counted and neither is ever refused for the budget's sake.

That last part was found by measuring `std.string`.  Counted, the copy of
`shortest` that `format_f64` is the one caller of spent the whole budget, and
`tenth`, `decimal_count`, `byte_of` and `to_odd` -- three instructions, a
handful, six, nineteen -- were then called on every number written; reading the
number back was the same story with `parse_f64`.  With nothing counted that does
not grow the program, every one of them is put where it is called.

What it never inlines: a function that can reach
itself, one that follows another convention -- the call *is* the convention --
one the entry point or the testing machinery calls by name, and one the program
marked `@[inline(never)]`.

**What it leaves behind** is a function nothing calls any more, and the pass that
drops what nothing reaches removes it.  That is what makes inlining a
function called once free: the call goes, the body stands where it was, and the
original goes with the next pass.  The log says both, so "where did my function
go" has an answer.

**How a body is put in place**: the block holding the call is cut in two, what
follows the call becomes a block of its own taking the answer as a parameter, and
the first half branches into a copy of the callee with the arguments as the
copy's parameters; every `ret` in the copy becomes a branch to the second half.
Two things about the copy are worth writing down.  The callee's `mem.start` is
the token in force at the call, a chain begun again in the middle of a function
saying nothing about what came before it.  And the copy is made in two passes --
every instruction first, then what each one names -- because a block may pass a
value defined in a block that stands *after* it in the list: the list is a layout
and not an order of definitions.  The blocks go into the caller's list where the
call was, since that list is the order the backend walks.

A sum is where its bytes are
----------------------------

`held_in_memory` is the one question everything asks about a sum: what a value
of one *is*, is where its bytes are, so it is an address in a register and never
a value.  An array whose type says its shape already answered yes to it, which
is what the entry in the to-do list meant by "arrays have shown what the answer
looks like": a place, an address to reach it by, and room in the frame where the
value is a function's own.

**Making one** takes room in the frame, writes the part at its start and the tag
after it -- `tag_offset_of` says where, which is after the largest part, the tag
last so that a tag ahead of a part wanting eight bytes is not seven bytes of
padding -- and the value is that address, bitcast to the sum's type.  A part that
is itself held in memory is *copied* into the room rather than stored, since what
the value in hand says is where those bytes are.

**Matching** is the chain of comparisons an enumeration is taken apart with, over
the tag.  What differs is what an arm binds: the part is at the start of the
room, read as the type that part has -- a record read out field by field, a
number loaded, and a part held in memory answered as the address it already is.
The read is done in the arm's own block, since each arm reads a different type
out of the same place.

**A sum that is somewhere is matched there.**  `_lower_match` puts the subject's
identity in `_match_in_place` while it lowers it, and the four reads that would
copy a sum out of a place -- `_lower_deref`, `_element_of` for a list,
`_lower_element` for an array, `_field_of_place` -- ask `_loaded_or_in_place`,
which answers the place bitcast to the sum, as making one does, and moves the
identity to `_matched_in_place` to say it did.  Everything after that is the
match it was: the tag is read from the place, the part from the place in the
arm's block.  `_payload_from` is told the place is the program's
(`_ArmPlan.in_place`) and copies a part held in memory where it answered the
copy's address before -- the arm may write the place, and the name it bound
must not change with it.  The expression asked about is the one written:
`_lower_element` may replace an index with one it settled, so it keeps the
original to ask with.

**Answering with one** is the caller's room.  `ReturnStyle.in_registers` says no
for anything held in memory whatever its size, so `largeanswers` gives the
function the parameter that says where to put the answer -- and for such a value
the rewrite is shorter than for a record: the answer is copied into the room with
a copy written out in the largest pieces that fit, and at the call there is
nothing to read back, the room being the answer.

**At the top level** a sum is a `SumConst` -- which part, the tag, and the constant the part carries -- that `emit_globals` lays
out as `ResultConst` is: the part at the start, the tag at `tag_offset_of`, zeroes between.  `_read_global` is the one way a
function reads a variable at the top level: a load for a value in registers, and for one held in memory -- a sum, an array of
known shape -- the variable's own address, except that a `mut` sum is copied into the frame first.  Assigning to one copies
the bytes in with `_copy_bytes`.

**A record or a tuple holding one** is held in memory itself: `held_in_memory`
answers yes for a product or a tuple with a member it answers yes for, however
deep, and `parts_of` then answers the type itself -- one part, an address -- so
the convention, `largeanswers` and the coroutine frames treat it as they treat a
sum without knowing the difference.  The checker builds one as room in the frame
with each member stored where `offsets_of` or `member_offsets_of` puts it
(`_lower_record`, `_made_tuple`), reads a member from there (`_member`,
`_field_of_place`), and `_record_from` and `_record_into` become a load and a
store of the whole.

**The builder's `load` and `store` copy a value held in memory.**  Storing one
copies its bytes, in the largest pieces its alignment allows (`IRBuilder.copy`);
loading one copies them into room of its own, which is the value.  Before, a sum
put in a field or behind a reference was stored as the address of its room -- a
pointer where the layout said bytes, which read back only while that room lived.
A `mut` local of such a type, an array excepted, is given storage of its own
(`_bind_local`), so assigning to it copies rather than rebinding the name to the
room the new value was made in: that room belongs to a place in the program and
not to a turn of a loop, so a value kept from one turn to the next would
otherwise read what the next turn made.

An array in a field, written where it is
----------------------------------------

**A field of a record read as a value is loaded, and loading an array whose type says its shape copies it** -- which is right
wherever the array is given to something else, and was wrong for `r.held⟦0⟧ ← 9u8`, which wrote the element into the copy and
lost it.  `_array_in_a_field` answers such a field as where it is, through `_record_at` -- a record local, what a reference names,
a field of a field -- and `_lower_element_assign` and `_lower_element` start from it: a write, a row, a mask, and a read of one
element, which no longer copies the whole array to read it.

A choice without a branch
-------------------------

**`select.T c, a, b` is `a` where the truth value `c` holds and `b` where it does not**, both worked out either way, of a type one
register holds: a whole number, a truth value, a code point, an address, a floating-point number (the verifier holds it to that,
and `c` to being a truth value).  `constfold` takes a choice made by a known truth value, and between a value and itself.  The
masked write (`_assign_picked`) is what makes one, an element at a time, where it used to spread the mask over the element's bits
itself -- which only a whole number has to spread over.

Each selector lowers it (`select_select`): **AArch64** compares the truth value with nought and selects, `csel` for an ordinary
register and `fcsel` for a floating-point one; **x86-64** puts the second in and moves the first over it with `cmovne`, and having
no conditional move of an XMM register, chooses between two floating-point values in ordinary registers, through `movq` and
`movd`, and moves the one chosen back; **RISC-V**, with no select of either kind in the base, makes the truth value a mask --
taken from nought -- and lets the difference of the two through it onto the second, `fmv` carrying a floating-point value's bits
across and back.

A lifetime name at a call
-------------------------

**A name of the callee's is free while its call's arguments are lowered.**  `_given_arguments` puts the callee's
`Function.lifetime_key` in `_calling_key`, and `_borrows_as_much` lets anything stand for a name of that key in a function type
the argument is handed for; `_shorter_life` reads the argument as the parameter's type and keeps the type it had in
`_promised_as`.  The call is then held to it by `_answers_as_long`, run once every argument is lowered: for each such name, what
the parameters carrying it (`Function.lifetimes`, function types left out) were handed is weighed with `_lifetime_of_value` -- a
local of the call with its depth, the names of the signature around it reaches (`_lifetime_outer`), lasting, or nothing -- against
what the function handed answers: the names its type says, or for a lambda written there and saying nothing, each reference it
brought in.

**Such a lambda takes the parameter's borrows** (`_adopted_borrows`, in `_lower_lambda`, in place of 4562), and while its body is
lowered every reference it brought in, and every variable it brought in by reference, is what the parameter's name stands for
(`_adopting`, added to `_lifetime_sources`), so that the body may answer those and nothing else (4561); `_lower_lambda` leaves
them in `_lambda_notes` for the call to weigh.

A lambda answered in an arena a parameter names
-----------------------------------------------

**It is a rewrite of the signature before anything reads it.**  `_kept_in_the_signature`, the first thing `_collect_function`
does, takes `fn(…) in a → T` off the answer's type and off each parameter's where `a` is a parameter, and writes it as the
definition's `made_in` and the parameter's: `→ T in a` and `s: T in a`, which `_made_in_of` checks (4618 where `a` is no arena)
and every rule about where an answer was made then applies -- `_lambda_may_leave` already let a lambda leave kept in an arena the
signature names.  Written anywhere else, `kept_in` reaches `_resolve_type` as it is, and 4642 says only `⎕heap` may stand there.

Where a view's elements are
---------------------------

**A view carries a key for the array it was taken of**, which nothing did before: an array in a frame had the empty provenance an
array in the image has, so a view of one could be given to anything.  `_array_frame_of` answers the key -- `_storage_key`, the
stand-in a lambda's reference to a variable already uses, at the variable's depth, for an array this function keeps in its frame
(`_made_here` of its value, which is no frame for one `in ⎕static`), and the parameter itself, in `_unknown_by_id`, for one a
caller handed over -- and `_collect_arenas` adds it for a name of an array.  The keys are noted in `_array_frames`, so that where
the elements are copied they are taken out again: one element read out (`_a_view_of`), a fixed array given a value, any array
given to an array name or an element, which copies the elements rather than re-pointing anything.  A view given to a field of a
record re-points the field, and keeps them.  What then refuses an escape is what refused one made in an arena: `_made_in` and
`_made_in_part` against the stand-in's depth, said as a reference outliving its place (4570).

**An answer `in a`, `a` an array, is allowed the parameter's key** (`_params_made_in`); `_made_in_of` takes an array parameter
after `in` only for a view's answer.  A caller's answer is made in what the argument was (`_arenas_handed`), its frame key
included, so the escape rule holds there too.  `_answered` copies into an allocator only -- never into an array's key -- and a
view of one dimension is copied by `__pl4g_own.T` into a dense run (`_build_own_view`).  `_return_type` lets a function answer an
array; a fixed one goes through the caller's storage (`largeanswers`), which was there already.

A list joined onto where it is
------------------------------

**`l ⧺← more` is one statement, `ast.JoinAssign`**, and the two marks are one token to the grammar and two adjacent ones to the
compiler's parser, which is what tells it from a join while the left side is read.  `_lower_join_assign` takes the list's place
with `_list_place` -- `†`'s, saying which operator it is checking -- and `_room_made` asks `__pl4g_list_grow` for room for that many
more: the same room where there is enough, otherwise new room twice as large from the list's own allocator, or the arena in force
for a list in the image (`__pl4g_list_allocator`), with the elements copied.  What is joined on is copied in after that, and only
then is the old room given back (`_room_kept`, `__pl4g_list_release`), which is what makes `l ⧺← l` read what it joins before it
goes.  A list written out is lowered straight into the room; one whose elements point somewhere is copied deep into the list's
allocator first, known only while running, and the run that copy was in given back.  `⎕reserve` is the same with nothing copied
in, and a list in the image made writable before `l⟦i⟧ ←` or `†` is the same with room for nought more: it has none, so it is
copied out.

**A list read into another place is copied by noting the value** (`_copying`, by identity) where a binding, an assignment, a field
or an element is lowered, and `_lower_expr` wraps it in `__pl4g_own.T` when it gets there: liveness answers by the identity of the
statement, which a rewritten tree would not have.  `_copied_where_shared` decides -- a list, or a record or tuple holding one, read
from a name, a field, an element or through a reference, where either side may change it -- and `_used_after` asks the new
`live_out` whether a local is used again, which is the hand-over.  **The hand-over asks `_Local.owns_room`** as well, which the
statement binding the name sets from what `_copied_where_shared` answers: room made here (`_room_made_here`: a list written out, a
join, a call of a program function, and a record, tuple, list or `if` made of nothing else, `_made_or_holds_none`), a copy, or the
room of a local done with whose room was its own.  A `mut` variable is always given one of those, so it always owns its room once
bound by a `let` or an assignment; any other binding leaves the flag unset.  Where the place may change, anything else it is
given is noted, not only a name, a field, an element or a reference -- an `if`, a call of the language's own, a table's entry
`d⸨k⸩ ← x` (now asked as a field's value is) -- and `_build_record`, which writes a record written out a field at a time, moves
the note from the record to each field not already room of its own.  `_movable` asks the flag of a variable that is not a
parameter, so a variable bound to a shared name is not moved into a call either.  A `mut` parameter is copied on the way in where
`_changed_in_place` finds the body changing it so, read off what is written.  Whether a change is an effect is `_made_here` of the
list's place, the question an array's element write already asked.

**A list read as a view is `_as_a_view`**, which every place a view is wanted asks: `_spread` (what `_lower_into` does for a `T⟦⟧`),
`_as_wanted` (a generic call's arguments), `⎕text`, and the two joins below.  An array whose type says its shape lets its shape go
there, as before; a list of the element wanted is the view `(elements, count, 1)` of one dimension, and `_borrowed_name` of what was
written is `_lend`-ed as a `&` -- for the statement, and past it where `_kept_by_a_name` keeps it, which now asks `_holds_a_view`
beside `_holds_a_reference`, so the liveness that ends a reference's borrow ends a view's.  `_list_place`, which `⧺←`, `l⟦i⟧ ←`, `†`,
`⎕reserve` and the rest ask, asks `_lent_out` with `writing` before anything changes the list (4566); an assignment of the whole
list already did.  What was written is noted in `_list_views` by identity, and the two places that hand a `mut` view -- a call's
`writes_arrays` and `_owned_array` for a `let mut` -- refuse one found there (4251).  `l⟦i…j⟧` is `_list_element` making that view
and handing it to `_lower_slice`, so the ends, the step and the checks are an array's.  A generic call's argument is converted after
it was lowered, with no tree to lend from, so it lends nothing: during the call nobody else can reach a local list.

**`l ⧺← v` of an array is a branch of `_lower_join_assign`** taken where `_may_be_an_array` says the right side looks like one -- a
name or a call `_said_to_be` an array, a slice, `⎕bytes`, `⎕span`, `⎕one_run` -- so that `[]` and an `if`'s arms are still lowered
into the list's type; anything else that turns out to be an array there is a mismatch, as before.  It lowers the side with nothing
wanted, makes it a view, refuses elements `_not_copied_because` names (4710, `_view_copied`), takes room for the view's count with
`_room_made`, copies with `_joined_on` and writes the list back with `_room_kept` -- so a slice of the list itself is read from the
old room before it is given back.  `_joined_on` compares the view's stride with one while running: one run is a call of
`__pl4g_bytes_move` for count times stride bytes, otherwise `_element_by_element` copies forward.  `l ⧺ v` and `v ⧺ l` are
`_joined_with_a_view`, reached from `_joined_list` where either side is an array (`_lower_concat` wants nothing of a right side
`_may_be_an_array` says is one): room for both counts from `__pl4g_list_room` in the arena in force, each side through `_joined_on`.

**`__pl4g_bytes_move(into, source, count)`** (`strings.move_function`) copies eight bytes at a time with a `u64` load and store at
each offset and the rest a byte at a time; its loops carry the memory token as `copy_bytes`'s does.  The words are wherever the
offsets put them: x86-64 and AArch64 read a misaligned word as one instruction and RISC-V's kernel finishes one where the hardware
does not, so lining them up first is a cost for the uncommon machine.  It is impure, a call that writes memory being one nothing may
remove.  The older byte loops (`copy_bytes`, in a string's join and a list's) are left as they were, since changing them changes
the code of every program that joins anything.

Text from bytes
---------------

**`⎕text(BYTES)` is `_lower_text`.**  The argument is lowered with nothing wanted (a `u8⟦⟧` for an array written down, as
`_spread` does), `_as_a_view` makes it a `u8⟦⟧` or 4250 is said, and the answer is `str ? u64`.  `_made_from` says it was made in
`_out_of_arenas`, and `_takes_room` admits `in` around it.  `_as_one_run` keeps a view whose stride is one and otherwise calls
`__pl4g_one_run.u8⟦⟧`, `⎕one_run`'s function, in the arena in force.  Then `__pl4g_str_valid(bytes, length)` answers how far the
bytes are well formed; equal to the length, the room is `tables.allocate`d from the arena and the bytes moved in by
`__pl4g_bytes_move`; otherwise nothing is allocated and the join block gets a null pointer.  The answer is one `wrap` of the
string made of that pointer, the length and the allocator, failed where the two differ, carrying the offset -- the answer half
read by nothing on the way it failed.

**`__pl4g_str_valid`** (`strings.valid_function`) is generated once per module, pure.  Its `words` block reads the `u64` at the
offset while eight bytes are left and goes round again where `word & 0x8080808080808080` is nought; a word that is not is handed
to the `sequence` block with its end as the bound, and the last few bytes with the length as the bound.  `sequence` decodes one
sequence per turn while the offset is below the bound, then goes back to `words` from wherever it got to: a leading byte below
`80` is one byte; below `C2` is refused; below `E0`, `F0` and `F5` is two, three and four, each first asking that the whole
sequence is before the end, then that each following byte is in its range, as one unsigned comparison of the byte less the low end
against the width.  The first following byte's range is chosen with two `select`s on the leading byte (`_first_following`): from
`A0` after `E0`, to `9F` after `ED`, from `90` after `F0`, to `8F` after `F4`; the rest are `80` to `BF`.  Every refusal returns
the offset the sequence began at, and the end the length.

A cursor is where the list is, and how far along
-----------------------------------------------

Two words: a pointer to the *place* the list is in, and an index.  The place and
not the elements, because a walk may take one out -- and a list is where its
elements are and how many there are, so a shorter list is a different pair of
words that has to go back where the holder will read it.  Every operator on a
cursor therefore begins by loading the list afresh, which is also what makes
`⎕iter` cheap: it is the address and a nought.

That the index and not a pointer is what moves is what makes `†it` answer the
cursor it was given: the elements after the one taken out move down, so the same
index is the next element, and where the last element went it is the count --
which is what a walk that is over is.

`⎕iter(l)` and `†l⟦i⟧` both need the place, so both go through `_place_written`,
the machinery `&` uses -- and a list-typed name is therefore given storage of
its own where either is written of it, which `_addressed_in` now collects
alongside the names `&` is written before.  `_can_be_referred_to` still refuses a
reference *to* a list, that being a second way of writing what a list already
is; what these take is the compiler's own pointer and never the program's.

**Moving the elements down is one generated function.**  `sema/lists.py` emits
`__pl4g_list_erase(at, stride, count, which)`, a byte-at-a-time loop from the
front -- which is safe for the overlap, the bytes moving *down* -- and one for
every list of every element type, since what it is told is how long an element
is.  The count is the caller's to write, the caller being what holds the list.

**A cursor in a condition** is asked whether the walk is over, which
`_asked` turns into `index ≥ count` wherever a condition is lowered.  That
is the polarity `unless` wants, and `unless` is `ast.While` with `until` set: one
flag, one negation where the condition is lowered, and every other thing a loop
has -- labels, `break`, `continue`, an `else` arm -- unchanged.

A field of an element of a list, written
----------------------------------------

`l⟦i⟧.name ← v` is `_list_field_assign`, which `_lower_member_assign` sends a statement to where `_through_a_list` finds an element
of a list on the path -- an `ast.Element` whose base `_said_to_be` a `ListType`, which it now answers for a list's element as it
does for an array's.  The place is `_record_at(…, writing=True)`, whose element case for a list is `_list_element_written`: the
list's place (`_list_place`, which asks `mut` and that no view of the list is out), the index (`_index_into`), `_made_writable`, the
bounds `check` and `_element_place` -- what `_lower_list_element_assign` does before it writes, in the same order -- and the list's
allocator left in `_written_allocator`.  The field is then `_field_place` from there, and what is written goes as an element does:
where the field's type points somewhere and `owned.can_own` it, the old value is loaded, the new one copied with
`owned.own_function` into that allocator, stored, and the old given back; a record without anything to own is `_record_into`;
anything else is a store.  A path through records is `_record_at`'s member case unchanged, and a path through a list held in a
field of an element (`l⟦i⟧.sub⟦j⟧.x`) is the same walk again, `_list_place` asking `_place_written(…, writing=True)`, whose
`_place_of_a_field` asks `_record_at` for writing -- so the outer list is made writable before the inner one's place is read.

Three things changed under it.  `_made_here` steps through an `extract` of the elements of a `load` of a list to the place the list
was loaded from, a list's elements being its own, so writing an element's field of a local list is no effect, as writing the
element is not.  `_addressed_in` gives a place to the name at the root of a path through an element that a statement changes in
place (`_named_through_an_element`), since writing it may copy the list out of the image.  And `_array_in_a_field` answers nothing
straight away for a path through a list whose field is not an array whose type says its shape, so that the element is made ready
once and not twice.

A dense array, an index, and what a table is rebuilt for
--------------------------------------------------------

A table keeps the order its keys were put in (spec.md, "A table is walked in the order its keys were first put in"), and is laid
out as Python's dictionaries are since 3.6 for it.  The block is eight words: `arena`, `mask`, `count`, `stride`, `entries`,
`used`, `seed`, `index`.  `index` and `entries` point into one run, the index first -- `mask + 1` words -- and the dense array of
entries straight after it, with room for `room_for(mask + 1)` of them: three quarters of the index, which is as many as it may
name, and one more.  An entry is what it was, the state word, the key, the value.

**A word of the index** is nought where nothing hashed there, and otherwise two things: in its low `OFFSET_BITS` (forty) bits how
far past the start of the index its entry is -- never nought, the entries being after the index -- and above them the top
twenty-four bits of the hash of that entry's key (`HASH_TOP`).  The probe, `_probe`, walks the index linearly from the hash masked:
a word of nought ends it, the key being nowhere further; a word whose top bits are not the key's is walked past without reading
its entry (`(word ^ hash) < 2^40` is one exclusive or and one comparison); only then is the entry read, walked past where it is
given up, and its key compared.

**The answer for a key that is not there is the entry after the last written**, `entries + used × stride`, which is where `put`
would write it, and whose state word says it is empty.  So `__pl4g_table_slot` answers an entry either way, as it did, and a
lookup reads the entry's first word for whether and the value unasked -- which is then whatever the room held, and the truth value
beside it says nobody may read it: nothing that calls it changed.  The extra entry is what makes that entry exist when the index is
as full as it gets.

**Nothing in a table's room is read before the table has written it.**  The room the arena hands out is the system's, nought; the
heap's is what was given back, as it was, its first word the free list's link.  So the two things a table reads before writing
them are written when the room is taken: **the index is cleared** -- in `__pl4g_table_new` one store of a run of eight zero words
(`_clear_words`), in `__pl4g_table_grow` a loop of such runs over the new index (`_clear_index`), every index being a power of two
of words no smaller than eight -- and **the state of the entry after the last written is marked empty**: by `new` for the first
entry, by `grow` for the one after the moved keys, and by `put` for the one after the key it writes, which the load the table is
rebuilt at leaves inside the room.  A run of a constant stored is what the back ends make wide: two 32-byte stores on x86-64 at
`v3` and `v4`, four of sixteen at `v1` and on AArch64, eight words on RISC-V.  This costs a store per insertion and an index's worth of
stores per rebuild, where clearing whole runs would store every entry's bytes again; a zeroing allocation, which cleared only what
came off a free list, would have cleared the entries as well, and been a second entry into the runtime for one caller.  Nothing
else the compiler generates reads room before writing it: a list's elements past its count are never read, and a record, a
closure's environment and a generator's frame are written whole when they are made.

**`put` writes a new key last**: the probe stops at a word of nought, which becomes the new entry's word, and the entry at `used`
is written with the state and the key; `used` and `count` go up by one.  A key that is there is answered where it is, and its
value replaced by the caller -- so it keeps its place.  Taking a key out (`__pl4g_table_give_up`) marks its entry given up and
leaves the word of the index naming it, so a probe walking past it to what was put in after it still walks past; a key put back
later is a new key and goes last, its old entry a hole.  `__pl4g_table_next` and `__pl4g_table_select` walk the dense array from
nought to `used`, stepping past the holes, which is what makes a walk, and the set operators' answers, in order.

**`=` on two tables is `__pl4g_table_same.<shape>(one, other)`** (`tables.ensure_same`), a truth value: the same address answers
at once; counts that differ answer at once; otherwise the dense array of `one` is walked from nought to `used`, each live key
looked up in `other` with `__pl4g_table_slot`, and the walk stops at the first key `other` does not hold or -- for a dictionary --
holds standing for a value that differs, compared with `=` of the value's type (`_same_value`: text through
`__pl4g_str_compare`, everything else one comparison).  `≠` is the answer with its bit turned round.  `_lower_comparison` lowers
the right operand as the read-only kind of the left's table type and holds the two to it, so `mut ⸨K⸩` and `⸨K⸩` compare;
`tables.not_compared` names a value type `=` is not defined on, which `_compared_tables` refuses with 4280.  A value is never a
table (4445), so the function never calls another of its kind.

**The table carries two counts.**  `count` is how many keys it holds, which is what `#` answers; `used` is how many entries are
written, the keys and the holes, which is also how many words of the index are taken.  The load is measured on `used`: `put` calls
`__pl4g_table_grow.<shape>` *before* its probe where one more would pass three quarters of the index.  That rebuilds the run --
twice the index where `count + 1` is more than half of it, the same size otherwise, when the holes are at least a quarter -- moving
the live entries in order to the front of the new dense array and putting each key's word in the first free word of its walk, with
a fresh seed; no key is compared, every one moved being different.  So the holes disappear in a rebuild, a table crowded by holes is
rebuilt at the size it has, and only a table crowded by keys doubles.  The rebuild is a function of its own so that `put`'s probe is
not laid out around a loop that runs once in many insertions; the per-shape functions use `Shape.stride` as a constant.

`__pl4g_table_give_up` is what marks one, and it is generated once for every
table rather than once per shape: what it reads and writes is the entry's first
word and the count, and those are the same in every table whatever it holds.  It
does nothing where the entry holds no key, so taking out a key that is not there
is not a count going wrong.  The entry is not cleared -- its key and its value
are still there when it answers, which is what lets `†d⸨k⸩` read what was under
the key with one probe rather than two.

Collections at the top level
----------------------------

A collection is a table in an arena, made by running code; a variable at the top
level is bytes in the image.  What bridges them is a constructor, which the
module already had a place for: `_build_the_tables` in the checker generates one
per file that has any -- `__pl4g_globals`, numbered where two files do -- whose
body is lowered the way any other body is, so a top-level definition may say
everything one inside a function may say.

It goes at the *front* of `module.ctors`, so the tables are there before any
constructor the program wrote runs.  The variable itself is writable in the
image whatever the program may do with it, since the constructor writes it;
what the *program* may do is the type's business, and a collection without `mut`
is refused an assignment there by the rule a field already had.

The environment, built by the compiler
--------------------------------------

`⎕environ` is a name the compiler provides, standing for a variable of the image
that holds one table.  Neither half of the compiler can fill that variable
alone, which is why it is filled by both.  The strings are on the stack the
kernel set the process up on, reachable only by the entry point and only before
anything else runs; what they have to become is a hash table, whose layout only
the compiler knows -- the runtime is C and knows nothing of an entry, and
teaching it would be writing the table twice.

So `pl4g_env` in the runtime walks the stack, splits each `NAME=VALUE` where its
bytes already are -- nothing is copied, and a name and a value point into the
kernel's own string -- and answers a flat run of counted strings, each name
followed by what it stands for.  `sema/environ.py` generates
`__pl4g_environ_make`, the way `sema/tables.py` generates the table runtime: a
loop that walks that run two at a time and puts each pair in through the same
`put` a program's own dictionary uses, so a name looked up here and a name
looked up there are one key by one rule.  And `_read_environment` in each
target's `startup.py` calls the one, moves the two words it answered with into
the registers the language's convention passes one argument in, calls the other,
and stores the one word that comes back in `__pl4g_environ`.  Two calling
conventions in a handful of instructions, because there are two callees.

**Before the constructors**, so that a constructor may read the environment.
What it needs is the stack address, which is in hand from the entry point's
first instruction; what it does not need is the program's own stack, which is
made later.

**Flat rather than a run of pairs**, because a walk over pairs would have to take
a pair apart into a key and a value, and the backend cannot yet bind a loop
variable to a value of several words.

**A program that never names it carries none of it**, and nothing has to look for
that: the variable is made on first ask, exactly as `⎕heap` is, so
`Module.environ` being there *is* the question answered.  The entry point asks
it before emitting anything, the pass that drops what nothing reaches keeps the
builder only for a program that has one, and the table runtime for
`⸨str: str⸩` follows the builder.  That is 2 KB of image and, for a program
importing `std`, four milliseconds of compiling -- which is what an earlier
arrangement cost every such program, the builder being written in `std` and
generated, lowered and thrown away again whether or not anything read it.

**Not writable, in a writable section.**  The variable is `mut` in the image
because the entry point fills it, and not writable by the program because its
type is a collection without `mut`: an entry assignment is refused, and so is
putting a different dictionary in the name, which is the rule a field of a
record already had.  So what the table says is what the process started with,
whatever happens to the process's own environment afterwards.

**At build time it is a Python dictionary.**  The driver takes one snapshot of
its own environment and hands it to two places: the evaluator, where `⎕environ`
stands for it, and `Plan.env`, which puts it in the object the build function is
handed as `std.Build.env`.  One dictionary, two names for it.  A lookup answers
what the key stands for or `MISSING`, which `??` reads and everything else
refuses -- so a build file that uses a variable nobody set, without saying what
to do instead, is told where it did.

The auxiliary vector, read by the compiler's own code
-----------------------------------------------------

`⎕auxv(TYPE)` reads `__pl4g_auxv`, a variable of the image holding fifty-two words, `u64⟦52⟧`: word *t* is the value the
kernel gave type *t*, and word 0 -- `AT_NULL`'s, which ends the vector and is never an entry -- has bit *t* set for each type that
was there.  `sema/auxv.py` makes the variable on first ask and generates `__pl4g_auxv_make(at)`, which fills it; `Module.auxv`
being there is what tells the entry point, the pass that drops what nothing reaches (which keeps the reader as a root, as it keeps
the environment's builder), and the interpreter.

**The reader** takes the stack pointer the process started with -- `HELD_STACK`, which `started.holds_stack` now keeps for a
program that asks the vector too -- and is called by `_read_auxv` in each target's `startup.py` under the language's convention,
first of everything the entry point calls: before the environment's builder and the constructors, and after only the processor
check of x86-64 and, in a position-independent image, the relocation.  It loads the count of arguments, steps past it, the
arguments and their null, walks the environment to its null, and then takes the pairs to `AT_NULL`, storing each value whose
type is below 52 and `or`ing its bit into a block parameter; the bits are stored once, at the end.  About forty instructions on
each target.

**A question** is lowered where it is written, with no call: the type as a `u64` (zero- or sign-extended by its own type, or
truncated from an `i64`, which is the same bits), `select`ed to 0 where it is not below 52; the presence word shifted right by
that and masked to its low bit; the word at that index loaded; and the two made into the result, `wrap`.  So a type outside the
table asks `AT_NULL`'s bit, which is never set, and no branch is needed.  For a type written down the shift and the address fold,
and what is left is two loads, a test and the result; a type written down outside the table is warned about (4381) and is the
failed result with no table made.  The variable is `mut` in the image, which keeps the pass that works out pure calls from
reading it while compiling -- it would read nought, the table being empty in the image.

**Where it goes.**  The table is in the image's writable data, `.data`, as nought.  Its entries want to be in the section the entry
point makes read-only after the constructors (`@[relro]`, a change of its own): moving it there is putting the variable
`auxv.variable` makes in that section, and nothing else, the reader running before the constructors.

**A shared object has the table and no reader**: it has no start of its own and is never handed the stack, so the table stays
nought and every question answers nothing.  The reader is made only where `Module.output` is an executable.

**The interpreter** has no reader either: it fills the variable before the constructors from `getauxval`, for every type from 1 to
51, a type present where the answer is not nought or `errno` is not `ENOENT` -- the questions the generated code asks of the
table are then the same.

The compiler as a language server
---------------------------------

`pypl4g lsp` speaks the Language Server Protocol on its standard input and
output, and **what answers every question is the compiler itself**: the same
lexer, the same parser, the same checker, and where the file has been saved the
same code generation, over the text the editor is holding rather than over a file
on disk.  A server that reimplemented any of it would be a second statement of
what the language is -- the project has one of those already in the tree-sitter
grammar, kept honest by a test -- and the one a reader would notice drifting is
the one in the editor.

**A command word and not an option**, beside `build` and `test`: what the
compiler is being asked to do is the thing a command word says, and a server is
not a build with a flag on it.  It takes no source file, and one named beside it
is refused (1017) rather than ignored, since a command line that names one was
written by somebody who expected it to matter.

**How far it compiles depends on what happened.**  Typing gets the front end,
which is where all but a handful of diagnostics are and which costs a few
milliseconds; saving gets the whole compiler, so that a program the back end
refuses (8501, 9901) says so at the moment there is a file to refuse.  Both are
the same `Driver` asked for different amounts of work -- `front_end()` and
`run()` -- and the second writes its output into a temporary directory that is
thrown away, because what is wanted is the diagnostics and not the image.

**The editor's text, not the file's.**  `BufferSources` is a `SourceManager` that
hands out what the editor is holding for a path it has heard of and reads the disk
for anything else.  Every file the compiler opens goes through it, so a module
imported from a buffer with unsaved changes is read as the buffer has it.  That is
the whole of what makes this work on text that has never been saved, and it is
eleven lines.

**Three ways of counting, and the editor picks.**  The compiler counts characters
and numbers lines from one; the protocol numbers lines from nought and counts
along a line in whatever unit the two ends agreed, which is sixteen-bit units
unless they agree otherwise.  This language is written in glyphs of three bytes
and can hold a character outside the basic plane, so the two ends disagree about
every column of every interesting line until something converts.  So the server
offers `utf-32`, `utf-8` and `utf-16` in that order of preference -- the first
being exactly what the compiler already has -- and converts through the text of
the line.  A test puts the same error on a line holding a pound sign, a euro sign
and a Linear B syllable, and requires character 38, byte 44 and unit 39.

**One message at a time, and no threads.**  A request is answered before the next
is read, which is affordable because the compiler is fast and an editor only sends
an analysis request once it has stopped hearing keystrokes.  Nothing can answer out
of order, so a cancellation is something to ignore rather than to race.

**Only the protocol goes to the standard output.**  What the compiler would have
printed about its stages goes to the standard error, which is the log an editor
keeps -- and which is where a reader looks when the server itself is what is
wrong.

**A documentation comment is read into its parts.**  `front/doccomment.py` takes
one apart: the prose it opens with is the summary, and a line beginning with `\`
or `@` and a word is a command that runs until the next one.  It is in the front
end rather than in the server because two things read the answer -- the checker,
which asks whether a `\param` names a parameter, and the server, which shows what
it says.  Nothing in it knows what is being documented: whether a name is a
parameter is a question about a definition, and it is asked where definitions are.

The parser keeps **the span of every line** of a comment beside its text, taken
after the marker and after the spaces following it -- so a column in the comment's
text is a column in the file, and a diagnostic about what a comment *says* points
at the words it says it in.  That is one field on each definition node
(`doc_lines`) and it is what makes the carets in 4600 to 4604 land on the command
rather than on the definition's name.

The IR's `Function` gained a `doc` for the same reason it has spans: nothing the
compiler does reads it, and what asks for it is something telling a reader about
this function.  It is what makes hover work for a function of *another* module --
the server has that module's IR and not its syntax tree, so without this there
would be nothing to show.

**What a name is, and where it was defined**, are answered from a side table the
checker fills in when it is handed one.  `sema/notes.py` is that table: for every
name the checker resolved it records where the name is, what kind of thing it
turned out to be, the type it got rendered as the language writes one, and where
it was defined -- which may be in another file, a module's function being defined
in the module and the compiler having read that file too.  A build hands in
nothing and pays one test against nothing per name resolved; that is why it is a
table handed in rather than something the checker keeps.

**It is about uses, not definitions.**  Where a definition is and what its
documentation comment says is in the syntax tree, which the server already has, so
standing on a definition is answered from the tree and standing on a use from the
table.  That is what makes the same key work at either end, and it is why the
checker needed four hooks and not forty: `_lookup` for a local, a parameter or a
global, `_place_of_a_name` for one a reference is taken of, `_callee` and
`_callee_of_module` for a function, and `_named_type` for a type.

**A test drives it the way an editor does**, over pipes, from the handshake to the
last notification: that is the only way to check a server, what it is being what
it says on those two streams.  And one test runs Neovim with nothing but this
project's package on its runtime path and requires the compiler's diagnostic to
arrive in the buffer at the place the compiler put it.

What the external scanner answers, and when it says nothing
-----------------------------------------------------------

The grammar's scanner produces four tokens -- an end of line, an indent, a
dedent, and the mark that opens a block written on one line -- and tree-sitter
asks it only where the parse could take one of them.  That is what the line
break inside brackets rests on: there the parser could take none, so the scanner
is not asked and the `\n` among the extras swallows the break, which is the rule
the compiler's lexer states by counting brackets.

**A block written on one line closes at a closing bracket.**  The scanner is
asked where a statement could end, which is what makes one character enough to
tell what follows: a semicolon continues the block, a brace closes a block it
stands in, a comment takes the rest of the line, `e` begins the `else` of the
same chain -- and a closing bracket or a comma ends the block, which is what the
compiler's parser does with them.  Without those two, `f(if c: 1u8 else: 2u8)`
left the block open at the `)` and the `else` belonged to nothing.

**And it says nothing at all while the parse is recovering.**  tree-sitter marks
every external token valid there, so the scanner can read nothing from what is
wanted; `_error_sentinel`, an external token no rule ever writes, is how it knows
where it is.  Answering anyway is what made a file with a mistake in it cost
gigabytes: each manufactured end of line and dedent is a token of no width, every
stack the recovery is exploring asks again at the same place, and the stacks
multiply.  The whole test corpus parsed in 7.3 seconds with the scanner answering
during recovery and in 1.2 with it silent, and one file with a stray `()` in an
attribute exhausted two gigabytes and died.

Editors that read the same grammar
----------------------------------

`editors/nvim` is a Neovim package, and it **holds no copy of anything**: the
parser in it and the queries in it are links to `tree-sitter-pl4g`.  So an editor
colours a program the way a diagnostic colours the same line, for the same reason
the diagnostic does it that way -- one description of the language, read by
everything that needs one.

Four files, and what each is for: the suffix says what a `.pl4g` file is, since
the language has no shebang line; the ftplugin sets what editing one is like and
turns the grammar on; `parser/pl4g.so` is a link to what `bin/pl4g-grammar`
builds, and `queries/pl4g/` links to the grammar's own queries under the names
Neovim looks them up by.  Nothing computes a path and nothing is copied on
install, so pulling the project brings the grammar with it.

**What the ftplugin says is what the language settles and nothing else.**
Indentation is four characters and never a tab, because a tab in indentation is
an error the compiler reports (2103) -- so a tab that is there already is shown
rather than left to be found by the compiler.  The comment markers are the two
the grammar has.  A name may hold an apostrophe and one the compiler provides may
hold an `@`, which is said so that a word-wise motion treats such a name as one
word; the `⎕` that begins it needs no saying, every character above 255 being a
word character to that editor already.

**It does not turn folding on** although the fold query is there, and it does not
work out the indent of a new line.  The first is the reader's business and a
package that decided would be deciding for every file.  The second wants a rule
that knows a line ending in `:` opens a block, which is what an indentation query
would be; `TODO-editors.md` has the entry.

**A plugin manager may own the runtime path, and then only it can be told.**
`lazy.nvim` -- which kickstart.nvim and most configurations use -- sets `packpath`
to the Neovim runtime and rebuilds `runtimepath` from its own list, both by
default and both to save a few milliseconds of startup.  So a package installed
under `pack/*/start/` is never loaded and a path added with `--cmd` is thrown away
again, in both cases with nothing said: the file opens, no filetype is set, no
colour appears, and `:checkhealth pl4g` answers that there is no such check.  The
README leads with the spec such a manager reads, and says which single line
(`:echo &packpath`) tells a reader that this is what is happening.

**`:checkhealth pl4g` says why a file is not coloured.**  Five things have to
hold -- the suffix is recognized, the package is on the runtime path, the parser
was built, the queries compile, the buffer has the highlighter -- and each of them
fails looking exactly like the others, so the package carries a health check that
says which.  The fifth answer is the one nothing else would give: everything
works and the colour scheme paints almost nothing.  Neovim's own default scheme
gives `Type`, `Number` and `Operator` the ordinary foreground and makes a keyword
bold, so a file coloured perfectly well looks plain -- which is what "no
highlighting" turned out to mean the first time it was reported.

**Zed is the same two halves said in Zed's shapes.**  `editors/zed` is an
extension: `extension.toml` says which grammar to build and which language server
to start, `languages/pl4g/config.toml` says what a `.pl4g` file is and what
editing one is like, and `highlights.scm` beside it is a link to the same query
Neovim reads -- Zed resolves a capture name against the theme by dropping the last
part until something matches, so `@keyword.conditional` is coloured as a keyword
and the one file serves both.  The queries Zed has that Neovim has not are the
ones Zed asks for in its own captures: `outline.scm` (`@item`, `@name`,
`@context`), `brackets.scm` and `overrides.scm`.

**The one thing the two do not share is where the grammar comes from.**  Neovim
reads the working tree, through a link to the library `bin/pl4g-grammar` builds.
Zed builds the grammar itself, from a **git repository at a revision** -- so what
it reads is a commit, and a change to `grammar.js` reaches it only once that
change is committed, pushed, and the revision in `extension.toml` bumped.
`bin/pl4g-zed-rev` writes that revision, and a test refuses one whose grammar is
not the grammar of the working tree: a second statement of which grammar is
current, kept honest the way this project keeps the others.

**The server is the same server.**  Zed will read a command from an extension and
from nothing else, so `src/lib.rs` is a dozen lines of Rust compiled to
WebAssembly whose whole job is to answer with one: the compiler named in the
settings, or `bin/pypl4g` of the project the file is in, or `pypl4g` on the path.
The second is the one that matters -- a program in a checkout of the language is
then checked by the compiler it is written beside.

**A test opens a program in the editor.**  `tests/compiler/test_editors.py`
checks that the links resolve, that every query of both editors compiles, that
every token of every program in the suite is something the queries colour, that
every name they capture by is one an editor knows, that Zed's manifest and its
language configuration agree with each other and with the compiler's own option
table -- and then runs Neovim over a program with nothing but this package on its
runtime path and asks what each token came out as.  That last is the one test that
reads the type of the buffer, the parser, the queries and the order of the
patterns in one go.  Nothing here runs Zed: what a test can check of an extension
is that it says what it means to say, and Zed builds the rest itself.

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

A block that need not exist
---------------------------

`simplifycfg` is four things now, and each of them is what the one before it
leaves behind.  A branch on a condition the folder settled becomes the jump it
would have taken; a block no branch reaches is removed; a **parameter every
branch arriving supplies the same value for** is replaced by that value and
taken off the block; and a **block with one way in, whose one predecessor has
one way out**, is written into that predecessor and removed.  The four run until
nothing changes rather than once each, because each creates work for the others:
a settled branch leaves a block that one branch reaches, a block one branch
reaches leaves a parameter with one incoming argument, and a parameter replaced
by its argument leaves a block that is nothing but a jump.

**A parameter is a question about where control came from**, and one whose
answer is the same whichever way control came is not a question.  An argument
that is the parameter itself is not counted, which is what lets a loop carrying a
value it never changes lose the parameter as well: on the turn that supplies it,
the parameter already holds what the way in supplied.  Nothing has to be proved
about dominance, either -- the value replacing the parameter is computed before
every branch that arrives, so it is computed before the block.

**It runs twice**, the second time inside `settle` with `constfold` and `knownargs`, the three taken in turn until none changes
anything (at most `SETTLE_ROUNDS`): `facts` and the second folding settle conditions after the first `simplifycfg` has run, a branch
settled then leaves a block whose parameter is a tuple a caller made, and that tuple's parts fold in turn.  An extract of a tuple
folds where the part's type *equals* the extract's -- `parts_of` makes pointer types without interning them, so the same type is
not always the same object.

**What every caller hands alike is written in** (`knownargs`).  A function that is internal, defined, no root (`DropUnreached`'s),
named nowhere but as the callee of a direct call, has every caller in view: where every call hands a parameter the same integer or
truth value -- or the same one at a part of it, an argument made where it is called as a tuple of parts -- reads of it, and extracts
of that part, read the constant instead.  A caller's own parameter counts where what is known of it is known, which is worked out
over every such function until nothing new is learned (`ROUNDS`).  It is what makes a view's stride one inside `std` wherever every
call hands it a run, so `⎕one_run`'s question folds and the copy, `__pl4g_one_run` and the allocator go.  **Addresses fold too**
(`constfold`): a pointer read back as a place and a number of bytes past it (`_place_and_offset`, through bitcasts and constants
added on), the difference of two such words with one place under them is the number, and two compared for equality are settled --
one place being the same value, or the same part extracted twice from one value (`_same_place`).  And **a declaration nothing calls
is no root** of `dropunreached`, being somebody else's definition: the allocator is emitted for a program whose code still names it
(`wanted_by`), and one whose copies folded away no longer does.

**Verifying is asked for** (`--verify`, or `PL4G_VERIFY=1`), and an ordinary compilation does without: it is a check on the
compiler, and it was half of what optimizing took.  Asked for, the module is verified after the checker, after every pass that says
it changed something, and after the last pass whatever it says; and after a pass that names the functions it changed (`touched`, a
list each pass's `run` leaves behind, `settle` the union of its three), only those -- every function where a pass says nothing, which
`coroutines` and `largeanswers`, rewriting callers and callees together, do not.  The module-wide checks run every time.  The test
suite sets the variable for every compilation it makes, in a process of its own or not, so a pass that breaks the IR is found at that
pass; `pl4g-timing` takes it out of what it runs, so a column is the time of an ordinary compilation.

**The blocks are reordered in one case only.**  The order they are laid out in is
the order the backend walks, and a value has to be computed in a block standing
before the one that reads it; merging a block into one standing before it removes
a block and moves nothing, so that stays true without anything having to reason
about it.  What is merged away is also never a loop header: a header has the way
in and the way round, which is two ways in.

The case is a block merged into a predecessor standing *after* it, which `match`
makes: its join is laid out after the first arm, and where every arm but a later
one returned, that later arm is its one way in.  What the join computed then
moves down to the arm, and the blocks it dominated -- which stood after it, and
which the inliner put there when the join made a call -- would read values
computed below them.  `_follow` moves those, and only those, to just after the
predecessor, keeping their order; what the predecessor does not dominate reads
nothing it computed, and stays.  A program that showed it walked a recursive
document in a function called after such a `match`; the backend refused it with
"a value this backend did not compute", the IR having verified.

What it is for is less what the language writes than what the passes leave.  An
`if` whose condition is known should leave no trace at all, and before this it
left a chain of blocks that fell through; `match` leaves a join block that the
one arm assigning nothing still branches to; and the inliner leaves three blocks
per call -- the callee's entry, the block the copy answers into, and whatever the
copy's own joins were.  On the standard library's read-and-write test that is
four blocks and sixteen lines of IR gone and forty-eight bytes of image with
them; over the two hundred and forty-two programs of the language suite, fifty
smaller images, three larger by the sixteen bytes an alignment moves, and two
thousand seven hundred bytes less in all.

A block on an edge
------------------

A branch carries the values the block it goes to takes as parameters, and
putting them where that block will look for them is a move -- an instruction,
which has to be in a block.  A branch with one way out has one: its own, just
before the jump.  A branch with more than one does not, since before the branch
is every way out at once, and moves made on the way that was not taken would
write what the other way's block reads.

So `splitedges` puts a block there.  The edge becomes two: the branch goes to a
new block carrying nothing, and that block goes on to where the branch was
going, carrying what the branch carried.  One way in, one way out, nothing in it
but the moves -- which is the place the moves needed.

**Which edges.**  The ones whose source has more than one way out and which
carry a value.  The classical rule asks for the target to have more than one way
in as well -- a *critical* edge, the only kind worth splitting where the
alternative is to put the moves at the top of the target block.  That
alternative does not exist here: what places the moves is the backend, which
writes them before the branch and never inside the block it goes to.  An edge
carrying only a memory token is not counted either, a token being an ordering
and not a value, so there is nothing to move for it.

**Where the new block goes: straight after the branch.**  The order blocks are
laid out in is the order the backend walks, and a value has to be computed in a
block standing before the one that reads it.  What the new block reads is what
the branch carried, which is computed before the branch -- so just after the
branch is right whatever the rest of the layout looks like.  In front of the
block the edge arrives at would be right for an edge going forwards and wrong
for one going back: a loop that goes round from its conditional branch carries
values its header computed, and a block in front of the header would read two
values nothing had worked out yet.

The pass runs at every optimization level, because what it does is not an
optimization but a shape the machine needs, and it runs last, after everything
that could make such an edge.  **Nothing makes one yet**: over the whole language
suite compiled at both levels, one thousand and twenty-two compilations, it
splits nothing.  Every lowering that could carry values on a conditional branch
is written to carry them on a jump instead, which is the cheaper shape anyway.
What the pass buys is that the first one that does not is compiled rather than
refused -- and the backend keeps the refusal (8501), since such a branch
reaching it now means the pass did not run.

**What it cost, and what that paid for.**  A pass that never fires is still a
pass, and the pass manager verified the whole module after every one of them --
so adding this one added a verification, which on the larger programs was about
five per cent of the compilation.  Verifying after a pass that changed nothing is
verifying what the verifier passed the last time it ran, so it no longer happens:
the module is verified after every pass that reports a change, and after the last
pass whatever it reports, so what reaches the backend has always been through it.
That won back more than the new pass cost -- `collection-string-keys` 74.7 ms
before the pass, 78.5 with it and 68.0 with both -- because several passes change
nothing on most programs.  What it gives up is which pass to blame: one that
mutates the module and reports that it did not would be found by the next pass
that does report a change, and named as the culprit.  That is a defect in a pass
either way, and the timing report would already be lying about it.

Both halves of a product
------------------------

A saturating or trapping operation on the widest type has nowhere wider to be
computed in, so what says it went past the end of that type is a property of the
wrapped answer: a sum that came out below what it was given has carried, and so
on.  A product has no such property -- nothing about the low half says the high
half was not nought -- so the product is computed whole instead, and the upper
half is what says whether the lower one is the answer.

**Unsigned**, the upper half is nought exactly when the product fits, and the
only end such a product can reach is the top.  **Signed**, it fits exactly when
the upper half is what the lower one's sign says it should be: all ones under a
negative answer and nought under a positive one, which is the lower half shifted
right by its whole width less one.  Which end was passed is then the sign of the
upper half, that being the sign of the product itself.

`Assembler.wide_product` asks for both halves at once rather than the upper one
alone, because the three do not agree on how many instructions it is.  AArch64
and RISC-V have one instruction per half and want both factors twice over;
x86-64 has both halves out of one instruction, and asking for the upper one
alone would make it multiply twice.  Each of the two-instruction targets emits
the *upper* half first, since the lower one may be wanted in a register a factor
is in.

**And the fixed pair needed nothing new.**  The to-do entry that asked for this
said an operand would have to be able to require a particular register, because
x86-64's one-operand multiply reads a factor in `rax` and writes both halves to
`rdx:rax`.  It turned out to be the same situation as the division, which reads
a fixed pair and writes one: the selector moves the factor into `rax` itself,
and the instruction declaring what it writes is enough to keep the other factor
out of both -- the allocator already keeps a value out of a register whose life
overlaps its own.  The move in costs nothing in practice either: where the
factor is computed just before, the allocator gives it `rax` and the move
becomes a move of a register to itself, which goes.

The many-way branch
-------------------

`SwitchInst` had been in the representation from the beginning and nothing built
one; a `match` over an enumeration built the answer instead -- a comparison and a
conditional branch per value, each in a block of its own, with the last arm
reached by falling off the end.  That is what the backend still emits, and it is
no longer what the front end says.

**The front end says the question once.**  One `switch`, one case per value an
arm names, and the wildcard -- or the last arm, where there is none -- as the way
taken when no case matches.  What that buys today is smaller: four fewer blocks
on a three-armed `match`, and the same machine code to the byte, measured over
every `match` and `enum` program in the language suite.  What it buys later is
that the decision is in one place: a jump table is a property of how the switch
is lowered, and a chain spelled out in blocks by the front end could not be made
into one without recognising it first.

**The backend makes a chain of it**, in `target/branches.py` beside the other two
branches: the subject in a register, a comparison and a branch per case in the
order the instruction holds them, and what falls off the end goes the default
way.  A jump table is the other way, and wants a table of addresses in the
read-only section, an indirect jump on each of the three architectures, and a
bounds check -- worth its cost only past some number of cases, which is a
measurement nobody has made.  The to-do list carries it.

**The default is laid out first.**  The block the front end makes for the way
taken when no case matches is created before the others, so it is the block laid
out after the switch: what falls off the end of the chain then falls into it
rather than jumping to it.  That is one instruction per `match`, and it is the
reason the machine code came out identical rather than one instruction longer.

**A switch on a value that is already known** is settled by `simplifycfg` the way
a conditional branch on a known condition is: the case whose number the value is,
or the default where no case names it.  Both are one question with the same
shape, and a `match` inside a function that was inlined is where it arises.

**An edge of a switch that carries values** is split by the pass that splits
them, which asks how many ways out a branch has and not which instruction they
belong to -- so a many-way branch was covered by those words before there was one
to cover.

Walking the stack
-----------------

A fault says where it was, which the compiler knew when it emitted the check.
How the program got there is on the stack, and a stack is only bytes until
something says which of them are return addresses.  `target/frames.py` is what
says so: a row per function, giving where its code begins, where its return
address lies once its frame stands, how far above its caller's stack pointer is,
and the line to print for it.

**A table rather than a chain of frame pointers.**  The other way to walk is to
have every function keep a pointer to its caller's frame, which costs a register
and two instructions in every function that calls, in every program, forever.
The table costs nothing in any function; what it costs is bytes in the image,
read only by a program that is already stopping.  It is also what a debugger or
a profiler would want next, where a chain of pointers is of no use to either.

**Two numbers, not a shape.**  A row says where the return address is and how
far up the caller's stack pointer is, and not how the frame is laid out -- which
is the thing that differs.  The architecture whose call instruction pushes a
return address has it above the whole frame and its caller's pointer eight bytes
further still; the two that leave it in a register have it wherever the function
put it and nothing between the frames.  Asked for the two numbers, the walk is
the same instructions on all three; asked about the shape, it would have to know
which machine it was walking.  Each backend answers `frame_walk` with the two.

**Only the functions that call get a row.**  A function that calls nothing keeps
its return address in the register the call left it in, and no walk can ever
meet one: every frame standing when a fault is reported has an outstanding call,
the report itself being one.  So a leaf costs nothing, and the search stays
correct -- a row is found by taking the last one beginning at or before the
address, and every address the walk looks up is inside a function that calls.

**The rows are in address order**, which is the order the functions are emitted
in, and the last is a sentinel: it begins where the last function ends and names
no line.  Reaching it is having looked at every function there is, which is what
an address in the entry point or in the packaged runtime comes to, and the walk
stops rather than following whatever is below.  It is emitted even where there
are no rows at all, since what asks for the table is that the program can stop
with a message.

**The line is built whole at compile time**, indentation and newline and all, so
what runs per frame is one write of bytes that were already there.  The walk is
in `target/backtrace.py`, written once and emitted for every architecture the
way the allocator is: what differs is which registers a system call uses and
which instruction enters the kernel, and that much is a record each backend
fills in.  It may destroy any register it likes -- the helper that calls it is
about to end the program -- which is what lets it keep its state in registers a
system call leaves alone rather than on a stack it is in the middle of reading.
It gives up after sixty-four frames, because a walk that ran away would turn a
program that reported a fault into one that did not.

**What it costs** is the walk, a row, and a line per function that calls, and
only in a program that can fault at all: about forty bytes for one that cannot,
four hundred to six hundred for the ordinary ones in the language suite, and
1160 for the largest of them before the leaves were left out, 968 after.

Three things had to give a little.  `select_widen` on all three targets took a
register or a constant and passed a place in memory to the ordinary move, which
would have read eight bytes where four were wanted -- and on x86-64 matched no
encoding at all.  Each of them now narrows the place and picks the load that
widens it, which is what the walk reads a four-byte field with.  On AArch64
`_select_load` named the destination at the register's own width rather than at
the width the load writes, so reading four bytes into a whole register asked for
the instruction that reads eight and an offset on a grain of eight.  And every
number in a row is kept below the top bit of four bytes, so that the field means
the same on the machine that copies the sign of a narrow load into the rest of
the register and on the two that copy nought.

Generating for the microarchitecture level
------------------------------------------

`--mclevel` was a promise about what the compiler might do and a check at the
entry point that the processor could do it, and nothing the code generator
emitted was above the oldest level.  That had stopped being true for a run of
elements -- a word-wide vector multiply is SSE4.1 and wants the second level,
and a run wider than sixteen bytes wants the third -- and it is now untrue for
scalar code as well.

**`⎕ones` and `⎕lead` are the two the level decides.**  The second level adds
`popcnt` and the third adds `lzcnt`, so the same program is one instruction at
the level that has the instruction and a dozen at the level that does not.  What
those dozen are is in `target/bitcount.py`, written once for all three targets
because what differs between them is only whether the machine answers outright:
the bits folded in pairs, then nibbles, then bytes, and a multiplication to sum
the bytes into the top one.  Twelve instructions and no branch, against a loop
of up to sixty-four turns -- and a loop would answer in a time that depends on
the value, which is the wrong shape for something a program may do in a tight
place.

**Leading zeroes without an instruction** are the bits smeared downwards and
then counted: once every bit below the highest set one is set, the zeroes above
it are the width less the count of ones.  That answers for nought as well, which
is the width -- the one case the instructions themselves leave undefined and the
language does not.

**AArch64 has `clz` at every level**, counting leading zeroes not being a feature
there but the instruction set; it counts bits set as well, in the vector unit,
which is four instructions and a trip through a register of the other kind and
is a to-do line rather than a guess.  RISC-V has all three counts in Zbb --
`cpop`, `clz` and `ctz` -- which the application profile the compiler builds for
by default has; where the program is built for a base without it, it counts
itself.  The RISC-V selector answers `counts_ones`, `counts_leading` and
`counts_trailing` by asking the ISA string, as it does for Zfa's rounding.

**A count is over the type's width, not its holder's.**  The back ends hand
`lower_count` the type's bits; it reads the value at the width of what holds it
(`_holder`, the narrowest of eight, sixteen, thirty-two and sixty-four that
contains the type) and counts at the type's: a signed value masked to its own
bits, the leading count less the register's width beyond the type, the stop bit
of the trailing count at the type's width.  The two interpreters mask to the
type's bits as well, which is what the checker's folding of a number written
down (`_counted`) always did.

**`⎕trail` is the third count**, and the one where the zero case costs nothing to
get right.  A value narrower than the register has the bit just above its own
set first: a value with a set bit of its own counts what it did, and nought
counts up to that bit and stops, which is the width of the type.  Whatever stood
above the value in the register -- a sign, or anything at all on x86-64, where a
narrow value says nothing about the rest of its register -- is above the bit
that stops the count, so a signed value is not masked as the other two counts
mask it.  x86-64 has `tzcnt` from the third level (BMI1 comes with the same level
as `lzcnt`); below it the selector emits `bsf`, which finds the same bit and
leaves its destination undefined for nought, and a `cmove` from a register
holding sixty-four after it -- the zero flag `bsf` sets for nought being exactly
the condition.  AArch64 has no count from the bottom and reverses the bits with
`rbit` before its `clz`.  Without any instruction the zeroes below the lowest set
bit are made ones by `~x & (x - 1)` and counted with the sequence `⎕ones` uses:
for nought that is every bit, which is the width again.

**And the third level's shifts take their count in any register.**  A shift by a
value the program computed is three instructions at the older levels -- the count
moved into one fixed register, the value moved into the destination, the shift --
and that fixed register is held away from every other value for as long as the
shift wants it.  `shlx`, `shrx` and `sarx` are one instruction and no constraint,
and they are what is emitted from the third level up.  On the suite's own
`wrap-arithmetic`, `main` is 152 instructions at the first level and 147 at the
third.

**The levels are checked against each other rather than argued about**: every
program of the language suite that runs was compiled at the third level and run,
and all two hundred and forty-six of them answered what they answer at the
first.


Asking the system as well as the processor
------------------------------------------

`CPUID` answers about the processor, and the processor is half the question.
From the third microarchitecture level up, what a program uses includes registers
whose upper halves a context switch has to save -- and whether the operating
system saves them is not the processor's to say.  `OSXSAVE` says only that the
processor *lets* the system enable them; a kernel built or booted with them off
leaves a program passing its own check and then faulting on the first
instruction that used one, which is the exact failure the check exists to turn
into a sentence.

So `XGETBV` is asked beside it: register nought of the extended control
registers, masked down to the bits the level wants.  The third level wants the
lower half of a vector register and the upper half of a wide one -- the two AVX
needs saved -- and the fourth wants the mask registers, the upper half again and
the sixteen further registers AVX-512 adds.  `levels.state` is where those bits
are written down, beside the `CPUID` questions and in the same shape.

**The sentence is its own**, because what to do about it is its own: a processor
cannot be argued with and a system can.  "this processor does not have it" and
"this system has not enabled the registers it uses" are two messages and one
status, the status being about what happened rather than about whose fault it
was.

**What it cost the instruction table** was a byte.  `xgetbv` is `0F 01 D0`,
where the last byte is a ModRM byte that names no operand -- an opcode the
architecture ran out of room for.  The descriptor gained `modrm_byte` for
writing such a byte down, which is what those instructions need: the byte, and
not a rule for building one.

The other two architectures ask nothing.  AArch64's vector unit is not optional
in the profile this compiler targets, so there is nothing a system could have
turned off; RISC-V's extensions are optional and its check is at compile time
instead -- the ISA string says what may be emitted, and what is not promised is
refused rather than emitted and hoped for.

A member that is itself several values
--------------------------------------

`parts_of` answers what a value *is* where it is more than one value travelling
as one, and everything that has to put a value somewhere asks it: the register
allocation, the calling convention, the pass that moves a large answer into the
caller's storage.  It gave a tuple its members and stopped, which was right
until a member was itself several values.

**A result inside a tuple is three parts and not one.**  `〈u8?, u8〉` is an
answer, whether there is one, what the error carries, and the second member --
four values in four registers.  Counted as two, the first member was given one
register and needed three, and the program was refused with a message about an
encoding of `mov`.  It had never worked.

**So `parts_of` answers the leaves.**  A record already did, a field that is
itself a record having been spread out where it stands since records existed;
this is the same rule said of every shape rather than of one.  `parts_within` is
the other half: for each member of a tuple or a record, where its parts begin
among the whole's and how many there are, which is what reads a member out.

**The front end follows.**  A tuple literal hands over its members' leaves, the
way a record literal already handed over its fields' leaves, and reading a
member out takes that member's leaves and puts them back together.  Two builder
methods do both -- `leaves` and `whole` -- and they know the one thing that is
not uniform: a result is taken apart by `unwrap`, `failed` and `error` and put
together by `wrap`, its parts being of three types rather than indexed by
number, where everything else uses `extract` and `make_tuple`.

**The verifier needed nothing.**  It checks a tuple's operands against
`parts_of` and an extraction's index against it, and both are what the front end
now gives it: the change was the front end catching up with what the
representation already said.

What this buys is a shape that was refused becoming a shape that works, on all
three targets and through the caller's storage: `〈u8?, u8〉` is four parts, which
is more than the convention carries in registers, so a function answering one
goes through the pass that moves a large answer -- and that pass reads
`parts_of` like everything else, so it needed nothing either.


What is in the image
--------------------

**`⎕static` is no allocator at all.**  An answer said to be in it is `STATIC_AT` among the places a signature names, `_answered`
refuses anything with provenance (4635), `_settle_answer` marks it `answer_from = ("static", -1)`, and `thinanswers` puts back a
null allocator.  A body answering only text written down gets the same mark from what `_answer_kinds` shows.

**A container `in ⎕static` is a constant.**  `_lower_allocated` and `_lower_collection` hand it to `_lower_static`, which builds it
with `_constant_value` -- the function that already made a top-level variable's bytes, taught text (`PartsConst` of an
`AddressConst` to the text's variable, the length, a null allocator), lists (a run of their own), sets and dictionaries
(`_table_constant`) -- and then names it: an array or a record by the address of a new read-only variable, a list or a table by
its parts.  `_static_type` reads a type off what is written where nothing around says it, quietly, and `_known_while_compiling`
is the syntactic test 4638 asks.

**The image holds addresses.**  `AddressConst` encodes as nought plus an `ABS64` fixup against the target's symbol
(`target/globals.py`), the way the frame table already relocated its rows; `PartsConst` lays parts out as a tuple of them would be.
The compile-time interpreter places the target and writes its address.

**A table is laid out as `__pl4g_table_put` would have built it** in `_table_laid_out`: the keys in the order written, a key written
twice keeping its first place and its last value; an index starting at eight words and doubling before a put that would pass three
quarters of it -- nothing being taken out, every rebuild doubles -- and each key's word put in the first free word of its linear
walk, in the order of the entries, with `tables.IMAGE_SEED`.  The run is one variable, a record of the index's words and the
entries, with one empty entry after the last for a lookup of a key that is not there; the block's `index` points at it and
`entries` past the index.  The entry is a record of the state word, the key as `key_ir_type` holds it, and the value -- checked
against `Shape.stride` and `value_at`, since a layout that drifted would be a table nobody finds anything in.  A table laid out
empty (`_empty_table_constant`) is a run of noughts as long as `__pl4g_table_new` would have taken.  The block's arena field is null, and
`tables.arena_of` answers `⎕heap` for it through `__pl4g_table_arena`, so a table made out of one in the image has somewhere to
come from.

**Writing an array needs a `mut` name**: `_lower_element_assign` asks `_record_may_change`, `_place_of_an_element` asks the quiet
`_may_write` and `_read_only` (a non-`mut` variable of the image at the root).  `Function.writes_arrays` lists the `mut` array
parameters; `_where_made` refuses handing them anything `_may_write` says no to, and only those parameters are asked where their
elements are kept (4625) -- a body that cannot replace an element cannot give one back.

**An array given to an array is copied** by `_copy_elements`, asked from three places: `_array_assigned` for a local
(`_lower_assignment` sends every name holding an array there, before the placed and the unplaced paths), the variable at the top
level's path, and `_lower_element_assign` where fewer indices than dimensions name a row (`_row_at`).  It holds the element types
and ranks to each other, refuses elements `_not_copied_because` names (4710) and lengths both known that differ (4709), puts a
`check` on each dimension either side knows only while running, and asks `_read_only` and `_made_here` as a write of an element
does.  `_move_elements` copies: a count known while compiling of numbers or truth values is one vector load and one store, as
`_copied` does for a join, which reads every element before writing any; otherwise a loop an element at a time
(`_element_by_element`, the memory token a parameter of its header as `strings._copy` has it), run from the end where the
destination is the later address, which is `memmove`'s rule.  A `let` goes through `_owned_array`: a value written as a place --
a name, an element, a row, a field, what is read through a reference, or an `if` or a `match` that may answer one -- of a type
that says its shape is copied into a frame of its own unless neither the name nor what it names may be written; of a type that
does not, it stays what it was, and a `mut` name of it is refused unless `_may_write` says yes (4708).

**A `T⟦⟧` is where its first element is, a count per dimension and a stride per dimension**, in that order (`parts_of`), the
stride counted in elements.  `_strides_of` answers them for any array -- constants an array whose type says its shape makes one run
of, the parts beside the counts otherwise -- and `_view` and `_dense_view` make one, the second working the strides of a run out
of the counts.  `_offset_of` is each index times its stride, summed, and `_row_at` keeps the strides of the dimensions it leaves;
`_flat_offset` is the *n*-th place of the first few dimensions in the order a run has them -- one multiplication for an array whose
type says its shape and for a vector, and for a view of more dimensions the index along each first, the counts divided out -- which
is what `¨` asks, of a place written down, where the division is folded.  A walk in order does not divide: `_over_a_pick` and the
copy (`_element_by_element`) carry an index along each dimension of a view of several with the loop, `_odometer` moving the last
and carrying into the one before it without a branch -- a comparison, and a multiplication by what it came to -- forward, or back
for a copy that has to run from its last place, and `_offset_at` adds each index by its stride.  The divisions that stay are the
machine's, of two constants where the length of a run with a step is worked out, and `constfold` folds them where the divisor is
not nought and the answer fits.  What makes a view rather than
reading one: `_lower_slice`, an index a number or a run (`_run_along`, ends checked and a step at least one, the count rounded up)
in each position and a dimension nothing was written for kept whole; `_as_wanted` and `_spread`'s conversion, which carry the
strides of what they convert; and a pick, `⎕span` and `⎕bytes`, which are runs.  A dimension a run was taken along has no stated
length in the answer, so the dimensions a view's type states are ones it kept whole, still one run: a row `_row_at` answers as an
array whose type says its shape is a place and nothing else, and has to be.  `_lower_one_run` compares the strides with a run's
and branches: the view itself, or `_room_for` in the arena `in` named (`_takes_room` lets it) copied into by the loop.  The
runtime's `pl4g_args` writes the stride of one beside the place and the count.  What `std` copies for the kernel is given back by
the ring: `kept_at` and `kept_len` per slot, set by `pl4g_io_submit` where its last argument says the bytes are such a copy (through
`io.Writer.start_kept`) and given back by `reap` when the slot's answer is taken in -- or at once, where the request was done
without a ring, or no slot could be had.  A function that waits for its answer itself makes the copy in an arena of its own,
`⎕one_run(v) in scratch`, which goes when the function leaves.


A generic function checked where it is written
----------------------------------------------

**`_check_generic` lowers the body once more, with every type parameter an `OpaqueType`** -- a type equal only to itself, laid
out as a word so that nothing on the way trips -- into a `Function` the module never sees.  What the check makes on the way (a
lambda's function, a string's bytes, an entry in `_owned`, what the report log was told it chose) is taken out again afterwards;
what it said stays.  `DiagEngine.errors_only` keeps its warnings unsaid, an instance saying them for what was compiled.  A function
the check refuses is marked `refused`, and no instance of it is made.

**`_requirement_shapes` turns the requirements into a table** keyed by kind, operator or function name, and operand types, each
naming what it answers: the arrow's type, settling a fresh name as a new `OpaqueType`, or an `OpaqueType` nothing more may be done
to.  A bundle's lines are read with its arguments put in, in its own file.

**`_abstractly` stands in front of `_lower_expr` for every application** -- an operator, a fresh operator, a call of a named
function, an element, an entry.  Operands that can be lowered on their own are lowered first; where none turns out opaque, the
expression goes on to the ordinary path with those as `_Ready` values, so nothing is lowered twice.  Where one is opaque, the table
answers (`UndefConst` of what it says) or 4645 is reported.  A generic operator and a generic call go the ordinary way, and
`_lower_generic`, its callee's requirements met against the caller's table by `_check_clauses` as for any call, answers what the
callee's signature says without making an instance -- 4646 where a requirement does not follow.  `comptime if` and `comptime
foreach` are not lowered at all, and counted for the report log.


Constants worked out over the syntax
------------------------------------

`Checker._constant` is what a number that must be known while compiling is worked out by, and it works over the syntax, not the
code: what it settles is often a type, and a type is settled before anything is lowered.  It answers a `_Folded` -- the number,
its type, or none for a number of no width, and whether it is a division with no answer -- or nothing where the expression is not
a constant, or `_REFUSED` where it is one that has been reported.  The operators are the program's, worked out in Python with the
type's limits applied: `_fits` refuses what does not fit, `_within` reads low bits as the type does for the bitwise operators and
the shifts.  A call runs `comptime.evaluate.Evaluator` over `self._units`, the program as written, kept by `run` for this; the
definition is found in `self._definitions` by name, and its purity and type parameters are read from the syntax as well, so a
call works in a signature written before the function it calls.

Where a constant is only welcome -- an array's index -- `_welcome_constant` asks quietly: `_refuse` answers nothing rather than
reporting, and the expression is lowered as written and says what is wrong where the program would.  An index that comes to a
number of no width is replaced by the literal it comes to (`_index_written_down`), so everything that already treats a literal
specially -- the check against the length while compiling, the exemption from `¤idx` -- treats it the same way.

A constant at the top level named before its definition has been reached is not a `GlobalVar` yet.  `_constant_not_yet_defined`
works it out from the definition as written (`self._top_written`), quietly, with `self._constants_asked` holding the ones being
worked out so that a cycle ends; `_reaches_itself` is what tells the definition, when its turn comes, that it found one.

Generators
----------

A generator (`fn g(…) ⇉ T`) is a `_Generator` in the top-level names, not a `Function`: what a call to it makes is a walk.  Its
body is compiled once, as the function `⇉g` of three pointers -- its frame, where to write what it hands out, and its arguments --
answering whether it handed one out; `_lower_body` binds the parameters from the argument record (`_arguments_from`, the words
`_words_of` makes) and lowers `yield v` as the value written to the out pointer and a `YieldInst`, a terminator going on in a
block of its own.  A `return` is `false`; `?` writes the failure out and stops at a block that answers `false` when resumed.

**A call** (`_lower_generator_call`) works the arguments out and is an `_Iterable` with the generator and them.  **A loop that can
see it** (`_written_in`, asked before the loop's own lowering) is lowered as syntax rewritten: the generator's names renamed with a
prefix no program can write, each `yield v` a loop of one turn binding the loop's names to `v` around the loop's body -- whose
`continue` of the outer loop becomes a `continue` of that one -- and the whole a loop of one turn taking the loop's name, `break`
and `else`, the generator's `return` a `continue` of it.  The arguments are bound to the renamed parameters in a scope of their
own.  **Anything else** (`_generator_described`) keeps, in the caller's frame, two counters, the argument record, and a
`CoroutineFrame` the size the pass measures; the value is `⎕next.g`, that record, and nought.  `⎕next.g` takes the walk's
number: nought is a new walk, which takes the next number, keeps it as the one walking and sets the frame's first word to nought;
anything else must be the one walking, or the program stops.  Then it calls `⇉g`.

**The `coroutines` pass** (`opt/passes/coroutines.py`, after `largeanswers` at every level) gives `⇉g` a new first block taking
the three pointers: it reads the stop number and switches -- nought to the old first block, each stop's number to the block after
it, anything else to `false`.  Every value read outside the block that made it gets a room in the frame, written where it is made
and read back at the head of each block that reads it; every `frame` the body took is a place in the frame; the memory chain
starts afresh after a stop.  A stop writes its number and answers `true`; a return writes all ones and answers `false`.  The
size goes to `layout.measured`, which is what `size_of` asks for a `CoroutineFrame`.

**Finishing a walk through calls.**  A `⇉T` value's `next` takes a null pointer where it would write a turn to mean "finish".  The
loop walking one (`_Iteration.finish`) emits that call after the loop, and puts it off -- an `_Emit` in a `_Deferred` frame of its
own around the loop -- for every way out that leaves more than the loop.  A plain iterator's `next` answers `false` to it at once;
a generator's resumes a walk under way, if its body puts anything off, with the null pointer, and the body -- which after each
stop tests the pointer when it has a `defer` in it -- runs what is pending and answers `false`.

**`g(…) in a`** is caught in `_lower_allocated` (`_generator_in`): the walk carries the arena's state, and a walk through calls
takes its description out of the arena instead of the frame -- in a size no checker knows, so the room is asked for with a
`SizeOfInst`, which the `coroutines` pass replaces with the number once every generator's frame is measured.

**`yield ⇉ g(…)` in one step.**  A generator's body answers nought for no more, one for a turn and two for "handed on": at
`yield ⇉` of a generator's call (`_delegated`) it makes `g`'s description, sets `g`'s frame to start, writes a delegation -- `g`'s
body, its frame, its arguments, and the delegation it came from -- into a `frame` of its own, puts where that is in its frame's
second word, and stops with `yield.on`, which the pass answers two for.  The description keeps the delegation the walk is in;
`⎕next.g` resumes that body, or the generator's own where there is none, pushes on two and pops on nought.  Told to finish, it
finishes every delegation from the innermost out and then its own body.  Every body tests for being told to finish after every
stop, since a driver may tell any of them.

**`⎕start` and `⎕resume`** (`_lower_start`, `_lower_resume`).  A walk under way is a `RunningType`, four words: the next
function, the description, where the state word is, and where what a resume hands in goes -- nothing for a walk that takes nothing.
`⎕start` lowers its argument with `_pick_in` set, so a generator's call, a pick, an iterator or an array comes back as the walk it
describes; a generator's is made with `_generator_described(…, driven=True)`, and the given place is the generator's own slot
inside the description.  The state word is a `frame` of the caller's.  `⎕resume(c, u)` writes `u`'s words to the given place,
calls the next function with the description, the state word and an out record, and wraps what came out as a result failed where
the call answered `false`.

**Kept in the heap.**  `⎕start(g(…) in ⎕heap)` arrives with the walk's arena set by `_generator_in`; where that arena is the heap
(`tables.is_heap`) the state word is allocated there too -- the description already is -- and the type is
`running_type(element, given, lasting=True)`, which renders `running<T, U, lasting>`.  The parser reads `⌖⇉T ⇇ U in ⎕heap` as
`RunningTypeRef`, taking the `in` only where `⎕heap` follows.  `_promises_as_much` lets a lasting walk stand for a plain one, and
`_shorter_life` re-tuples its four words under the plainer type, as it does a lambda's three.  `_short_lived` finds an iterator or
a plain walk inside a type -- through tuples, results, arrays and lists -- for the field, answer, top-level and capture checks
(4670).  The arguments are checked against `_kept_anywhere` of the parameter's type and, failing that, `_plainly_lasting` of what
is written (4680): a reach analysis like a lambda's (`_reached_by`) would admit more and is in `TODO-pypl4g.md`.

**`⎕await(v)`** never reaches the checker as a call: `_parse_call` makes it the `YieldValue` that `yield v` in an expression is,
so `_count`, `_contains` and the strategy choosing how to lower a generator see it as the stop it is.  `_check_value_is_used`
exempts a `YieldValue`, which is what lets it stand as a statement.

**`std.run`** is written in the language (`modules/std.pl4g`).  Its tasks are a list in the `std.fs.State` it is handed -- at the
top level of `std` until 2026-10-04 -- each a record of the lasting walk and the `Pending` it waits on (a slot of −1 before its
first turn); a round moves the list aside, resumes the tasks that may go
on and puts the rest, and the ones that handed out another request, back.  Two runtime functions are new in `runtime/io.c`:
`pl4g_io_ready`, which reaps what the completion ring holds and says whether a slot is still submitted, and `pl4g_io_step`, which
enters the ring waiting for one completion -- that being also what hands the kernel the requests written since the last enter.
`pl4g_io_wait` of a slot that is ready returns at once.

**A record of one field** is one word, its field's, and `_words_of` and `_from_words` step into it: `_word_types` already said the
word is the field's type, and an argument record of a generator with such a parameter read the field back as the record.

**Values that are closed** (`@[closed_by(f)]`).  The attribute is carried from `_NamedType.closed_by` to `ProductType.closed_by`;
`_check_closers` checks `T.f` in `_members` before any body is lowered (4686).  A `_Local` holding such a value has `closer`, and
two pieces of state that differ from one way through a body to another: `owes` and `ended_at`/`ended_how`.  Both ride in the
provenance tuple beside an arena's `gone_at`, so `_provenance`, `_put_back` and `_joined` carry them through arms, loops and ways
out with nothing new: a join owes where any way owes, and has ended where any way ended.

- `_bind_local` begins the lifetime (`_lifetime_begins`), both for a value and for a name that already stands for a place; the
  parameter of the closing function itself (`func.name == "T.f"`) owes nothing and says so in the log.
- `_lower_name` is the one place a name becomes a value, so a move is recorded there (`_moved`): `owes` cleared, `ended_at` set,
  `move` and `lifetime-ended` logged with what `_handing_over` says it went to.  `_record_at`, which every field read and
  reference goes through, refuses a variable whose lifetime ended (4682, said once per place).
- Assigning to such a variable (`_lower_assignment`, placed path) refuses one that still owes (4684) and owes again after.
- `_all_closed(scopes, span)` asks every variable in the scopes being left: from `_returning` after the defers ran, from
  `break`/`continue` over `scopes[loop.scope_at:]`, from `_pop_scope(left_at)` where a block fell through, and after the body of an
  instance and of a lambda.  Each variable is told once (`_told_unclosed`).
- `_goes_round` compares a turn's head with its end (`_after_the_turns`) and with each `continue` (`_Loop.turn_began`): a variable
  from outside that ended in the turn is 4685.  A loop over a range from a literal to the one after it takes one turn
  (`_one_turn`), which is the loop a generator written into its walk is wrapped in, and a `while true` is left only by its
  `break`s, so its head is no way out.
- `_lower_expr` marks what an aggregate puts inside itself (`_mark_stored`: a tuple's members, an array's and a list's elements, a
  record's fields; `_build_record` marks the fields it writes in place) and what a statement stores into a field, an element, a
  dereference or a variable at the top level.  `_not_moved_out` refuses a marked value of such a type unless it is written out where
  it stands (`_made_here`), and a member, element or dereference read as a value (4683, 4687).

**`kind: ⌜T'⌝`** (`ast.LiftedTypeRef`).  `_collect_generic` keeps the definition without such parameters as `_Generic.node`, so
that checking it where it is written and making its instances never see them, and keeps the parameters as written in
`written_params`.  `_lower_generic` reads each against that: a lifted type is resolved where the call is and read into the bound
types with `_reading`, and nothing is handed.  `_conditions_can_hold` then settles every `pre` condition the compiler can answer with
the types bound (4918, with the call as a note) -- before the instance, where a type the body was not written for would otherwise
fail first.  A generic function of a type is registered in `_members`, and `_generic_named` asks `_member_named` for a path whose
base is a type.  `_levels_in` takes `⌜T'⌝` for a type without resolving it, which is what let a condition over a type parameter be
classified where the function is written.

**A result of an array** is `ArrayTypeRef.result`/`error`, resolved first in `_resolve_type` by wrapping the array type.  Two places
that took a result apart by position now take it apart by what it is made of: `_words_of` with `unwrap`, `failed` and `error`, and
`_from_words` with `wrap` -- an `extract` of a result reads a register by index and is right only where the answer is one word.
A generator written into its walk binds what each `yield` gives with the generator's element type (`_types_renamed`), so a `yield`
of an answer and one of a failure are both the result.

**`std.fs.Dir.open` and the closes** are `pl4g_io_open` and `pl4g_io_close` in `runtime/io.c`: the name copied to a buffer on the
stack with a nul added, `openat(dir, …)` with the directory's descriptor -- `AT_FDCWD` for what `std.fs.cwd()` answers --
`O_CLOEXEC` and the flags of the kind; a close enters the ring until nothing is
outstanding and then calls `close`.  Every request the ring is handed now carries the offset −1, the file's own position: a nought
there is the absolute offset nought, which a pipe ignores and a file does not.

**`std.read_lines`** keeps a 1024-byte buffer and two indices in its frame; it is walked through calls from another file.
`target/vectors.py` stores a splat of a constant a word at a time where the target holds no run in a register
(`_store_repeated`), which is what keeps `1024 ⍴ 0u8` to 128 stores on RISC-V rather than a thousand.

**RISC-V branch relaxation** (`Target.relax`, a no-op on the other two).  After code generation and before the image or the
assembly dump is laid out, the sections are laid out and every conditional branch whose target in the same section is out of its
±4 KiB reach becomes the opposite branch over eight bytes and `jal zero, target` with a `riscv_jal` fixup; repeated until a round
grows nothing.

**A borrow of a field** (`_Borrow.path`).  `_borrowed_name` answers the name and the fields down to the part `&` reaches; an element
whose indices `_index_known` folds -- `_constant` asked with a diagnostics engine that says nothing, the index being lowered and
complained about later, once -- adds one step `⟦i⟧` per index, and any other element cuts the path back to the array.  `_lend` and
`_lent_out` refuse only where the two paths overlap -- one is the other or begins it (`_overlap`).  `_record_at` and `_field_of`
carry the fields being read down to the name (`below`), so reading one field while another is lent is not reading the name;
`_lower_element` checks the element's own path, so reading `v⟦1⟧` while `&mut v⟦1⟧` is out is refused (4565) and reading `v⟦0⟧` is
not, and an element written is checked with its path in the same way.

**`⎕fields` of a type.**  `_fields_of_a_type` reads a lifted argument: an enumeration gives `_members_of`, a tuple of
`〈str, E〉` pairs built from `enum_const` and the names' text; a record type is taken by `_written_out` before anything lowers it
(`_written_out_per_field`): each turn binds the name to a one-member tuple holding the field's name and records the field's type in
`_field_types`, by the local's identity.  `_type_stood_for` answers `f⟦1⟧` from there, which is what `⎕typename` and `comptime if`
ask; `_lower_expr` refuses it as a value (4691).  Anywhere else `⎕fields(⌜T⌝)` of a record type is 4692.

**`⎕kindof` and `⎕alternatives`.**  `_settled` asks `_settled_kind` of an `=` or `≠` before it compares two types: one side a
call of `⎕kindof` with one argument, the other a string literal, the literal held to `_KINDS` (4282) and the type `_type_stood_for`
answers named by `_kind_of` -- a `match` over the IR's type classes.  A `⎕kindof` lowered as a value is 4281, as `⎕isrecord` is
4605.  `_lower_alternatives` reads the tag once (`_tag_place`) and builds, for each variant in order, the pair of the name's text
and a one-dimensional view: the start the sum's own place bitcast to the part's pointer -- or, for a `void` part, a `frame` of a
`bool` holding `true`, made once per call -- the count the tag compared with the variant's index and zero-extended, the stride one;
the pairs and the whole go through `_made_tuple`.  `std.text` uses them for the four new arms; `list_text`, `set_text` and
`dictionary_text` are generic over `[E']`, `⸨K'⸩` and `⸨K': V'⸩`, which `_collection_type` now accepts with an `OpaqueType` as the
key, and a call reading the types out of a writable table hands it on bitcast to the read-only parameter type it settled
(`_lower_generic_call`, after `_reading`).  `tests/compiler/test_std_whole.py` lets a `comptime foreach` over `⎕alternatives` go
untyped, as one over `⎕fields` does.

**An empty table at the top level** (`_empty_in_the_heap`, `_empty_table_constant`): the block and a run of `FIRST_CAPACITY` empty
entries are image variables made writable (`_image_variable(…, mutable=True)`), the block's arena word the address of
`__pl4g_heap`; the variable's initializer is the block's address, and it is not given to `_build_the_tables`.  Growing allocates a
new run from the heap and leaves the old one; nothing hands a table's runs back.

**`std.error_name`** looks up `as_error(code)` in `error_names: ⸨Error: str⸩ … in ⎕static`, a table in the image written out in
`std` (since 2026-10-04; it was filled on first use with `comptime foreach` over `⎕fields(⌜Error⌝)`, which made every caller
impure).  `tests/language/error-name` walks `⎕fields(⌜std.Error⌝)` and asks `error_name_of` of each value, so the two cannot drift.

**`dropunreached` runs second**, right after `dropignored`, at every level, and again at the end.  A module brings every function it
defines into the program's module, and the passes before it used to work on all of them -- `largeanswers`, `coroutines` and
`leanvalues` over the whole of `std` for a program that called one function of it.  After `dropignored` so that an ignored answer in
a function nothing calls is still reported.

**A module is lowered as far as it is named.**  A module read by something else (`run(…, whole_program=False)`) checks every
definition's signature, as before, and lowers only its roots -- the constructors, destructors and tests, what the image offers
outside, and whatever the initializers of its variables name -- keeping every other `_Collected` in `_waiting` and every generic in
`_waiting_generics`.  `LoadedModule.want` is the module checker's `want(names)`: it takes every waiting function and generic whose
simple name is one of `names`, and every operator, adds what their definitions mention (`_mentioned`: bare names, the last part of
a path, operator glyphs), repeats until nothing more is taken, asks the modules it reads in turn, and lowers what it took callees
first.  A checker that lowers everything -- the program's own, one for the macros, one feeding the language server, or any when
`whole_modules` is asked for -- hands every name its units mention to the modules it reads before lowering a body of its own, so
that callees are lowered before callers across files as within one.  The names are an over-approximation: every `close` of every
type is lowered for one `close` written anywhere.  Nothing reaches a function without its name being written, so nothing is
missed; `tests/compiler/test_std_whole.py` checks `std` whole, so that a body no program reaches is still checked.  **What lowers
everything is the checker started on the files, never a module it reads** (since 2026-10-22): `Checker(imported=True)` for every
module `_read_module` reads, and `run` takes the branch that lowers everything for the macros' checker only where it is not
`imported`.  `for_macros` is handed on to a module's checker, and that alone used to make it lower every body -- every
number-reading body of `std.string` for a program whose one macro called nothing.  The macros of a module are its roots where the
macros are checked (`entry.node.is_macro`), the expander running them by a name nothing in a body writes.  The C++ keeps
`Checker::imported`, set on the module's checker before `run`.

**A function's body is a job** (`parser.BodyJob`, `ast.DeferredBlock`).  The parser reads every definition's signature -- the
parameters, the answer, the clauses, which a generic function's requirements are -- and, where the body is a braced block or an
indented one, finds where it ends from the tokens the lexer already counted (a brace and the one closing it; an indent and the
dedent matching it) and sets it aside: a `DeferredBlock` holding a job of the file's tokens, the body's first and one-past-last
index, the path, and whether the marks quote there.  A body written on one line is parsed where it stands.  The job parses with a
`Parser` of its own over its slice and an engine of its own; what it said is replayed into the compilation's engine
(`DiagEngine.replay`) when its block takes the statements, so that bodies parsed in any order, or at once, say what they say in
the order of the source.  A body that cannot be read leaves the block marked `failed`, and the checker drops the function, as the
parser did with a definition it could not read.

`parse(…, defer=False, workers=N)` reads a program's files: signatures, then every job (`run_jobs`), on `--jobs` threads -- one by
default, the interpreter running one at a time; the structure is the point.  `parse(…, defer=True)` reads a module: a body is
parsed the first time anything reads its `stmts`, which collecting a definition never does, so a function nothing names is never
parsed.  The expander's read of a module and the checker's are one read (`SourceManager.remember`/`parsed`).

**A module's bodies are lexed when read** (`tokenize(…, lazy=True)`, `tokenize_body`).  Lexing a module, the lexer notes whether
the definition at the margin is a function's (`_top_is_a_function`); where such a header ends in a colon and the next line is
indented, `_set_aside` finds the end of the body a line at a time -- the first line at the margin that is not blank or a comment,
comments at the margin just before it being the next definition's -- counting brackets on the way with text, characters and comments
taken out (`_depth_change`), and emits one `BODY` token over that text.  A body whose count does not come out even, which would be a
line at the margin inside a pair, is lexed where it stands.  The parser takes `: NEWLINE BODY` as a deferred block whose job lexes
the text (a `Lexer` from the body's first line to its end, so that it makes the indent and dedent the whole file would) and then
parses it; the tokens it made are kept on the job (`lexed`), and the bill of materials hashes a used function of a module by its
signature's tokens and those.  A source is hashed by its bytes (`ReadUnit.text`); a module's definition rows are its functions
whose bodies were read.  `std` is lexed in 4.3 ms rather than 7.6, its signatures being about half its tokens.

**A `mut` field** (`ast.Field.mutable`, `ProductType.mutable_fields`).  The parser takes `mut` before a field's name; the checker
records the names on the record type (`_mutable_fields`) and refuses one on a sum's variant (4693).  `_lifted(base, name, record)`
answers whether a field is writable whatever its record is: it, or a field on the path to it, is `mut`, and no `&` that only reads
lies on the path (`_behind_a_reader`), the types along the path read off the definitions with nothing lowered
(`_record_type_of`).  `&mut` of a field asks `_lifted` or the record's own writability (`_may_write`), and assigning a field asks
`_lifted` before `_record_may_change`.  A variable at the top level whose type holds a `mut` field is given a writable pointer type
(`_has_mutable_field`), which is what places it in writable data.  `std.Io`'s three streams are `mut`, and `std` has no `io`
variable any more.


What `std.io` and `std.fs` are built from
-----------------------------------------

**`modules/io.pl4g` is the ring and the devices; `modules/fs.pl4g` is directories and the `State`**, and `std` imports both with
`@[export]` so a program reaches them as `std.io` and `std.fs`.  `fs` imports `io` -- `Dir.open` makes `io`'s devices and `State`
holds an `io.Ring` -- and `io` imports nothing, which is what keeps the two from being a ring of imports: everything a device's
own functions need, the `Ring` record, `Pending` and the runtime's submit, wait, ready and close, is `io`'s, and `fs` declares
only the runtime functions it calls itself (open, drain, step).  Every device record -- `io`'s `Reader`, `Writer`,
`ReadWriter` and `Pending`, and `fs`'s `Dir` -- is its number and `ring : u64`, the address of the `Ring` record inside the
program's `init.fs`.  A function of either hands the runtime `⎕at(x.ring, ⌜&mut Ring⌝)`, so the C sees the same `struct pl4g_ring *` it always did and nothing in the language names a variable at the top level.
`State` is `@[device]`, so `std.run`, `std.spawn` and `std.drain`, which are handed one, need no `@[io]`; they still say
`@[impure]` where they write the task list through the reference.

**Several values in a result.**  `Dir.open` answers `Reader ? i32`, four registers, which the code generator did not do before:
`leaves_of` spreads a result's answer into registers (see above), and a coroutine holding such a result in its frame needed
`extract` of a result to count the answer's registers and `tuple` to give a member of several values that many.


Singletons
----------

**`singleton NAME : TYPE = VALUE`** is parsed among a type's fields and kept apart from them (`TypeDef.singletons`), so nothing
that walks the fields -- the layout, construction, `⎕fields` -- sees one.  `_collect_singletons` turns each into a definition at
the top level filed as `T.name`, which no program can write, with its container value treated as `in ⎕static`, and registers it
as the member `(T, name)`: the paths a function inside a type is found by (`_member_named`) find it, `_lower_member` reads it and
`_exported_variable` makes it a place for `&`.  It is exported with its type.  Writing one is refused where the assignment is
lowered and, for a field of one, by `_record_may_change`, which now asks whether a path names a variable at the top level before
walking to its root -- which also refuses writing a field of another module's variable that is not `mut`, before the check
missed it.

**`std.fs.State.cwd` is the one written without a value** (`FILLED_SINGLETON`): a `Dir` of `AT_FDCWD` whose `ring` is an
`AddressConst` to `__pl4g_init` -- the record `target/started.py` lays out, which no module defines, so the constant points at a
stand-in variable of that name.  The constant is kept in `Module.ring_addresses`, and `emit_globals` sets how far into the record
it points from `started.ring_at` once the layout is known.  The singleton is read-only, so it is in `.rodata`, with an `R_*_64`
relocation the linker settles.

**A slice may begin at the length** (`_checked_index(…, one_past=True)`): `a⟦n…n⟧` is the empty run at the end.  The start was
checked as an element index, which refused it, and `cat` with no arguments stopped on `init.args⟦1…#init.args⟧`.


A recursive document, and what it found
---------------------------------------

`type Json = … | items : [Json] | fields : [Member]` beside `type Member = key : str ; value : Json` is the shape the JSON module
reads into, and making it work found four things that were wrong for every program and not only for it.

**A heap container held what a name still held.**  `_stored` let in as it was anything made in an allocator lasting as long as the
container, and the heap lasts as long as itself -- so `[w, "x"]`, `w` a string a call made, held `w`'s bytes, and `l⟦0⟧ ← …` gave
them back while `w` still read them.  The specification already said otherwise -- "anything else of the heap's is copied" -- and
now the code does: into a heap container, only a temporary of the heap's goes in as it is.

**`EXPR ?? DEFAULT` lost the memory of its default.**  `_lower_or_else` joined the two ways with the value alone, so a default that
wrote memory -- a sum, made in a frame slot of its own -- left what followed reading a token from the way not taken, which
`--verify` reports as a use the definition does not dominate.  Where the default wrote memory, the join now takes the token too,
as the join of an `if` does; where it did not, nothing about the IR changes.  `IRBuilder.memory_so_far` is what tells the two
apart without starting a chain by asking.

**A field of an element of a list was refused** (4449): `_record_at` asks `_place_of_an_element` where the element is, which knew
arrays only.  A list's element is where its run says, so reading `l⟦i⟧.name` asks for that, checked against the length as any
index of a list is; a reference into a list and a field written through one are still not taken this way, the run moving when the
list grows and the element perhaps shared until written.

**Giving back a list of records holding text failed an assertion**, `__pl4g_disown` having been written for text and lists only;
that and the merge above are in their own sections.


A list handed on, units where they stand, and the image worked out when named
-----------------------------------------------------------------------------

**A parameter threaded through a call is taken as the body's own.**  `_takes_owned` asks `_threaded(name, body)` beside
`_changed_in_place`: an assignment `name ← CALL(…)` with `name` bare among the call's arguments (or a `Named` one's value), a
`return CALL(…)` likewise, or such a call as the body's last statement.  Read off what is written, as `_changed_in_place` is.
`_movable(written, current)` then lets a parameter be handed on where `_owned_parameter(written, current)` says the function being
lowered takes it as its own -- which asks `_Local.named_parameter`, set where the name is bound as a parameter and, unlike
`is_parameter`, not cleared when the body gives it a value -- and a name in `_addressed` is refused only where `_referenced(body,
name)` finds `&` in front of it, a capture by reference, a `[&]` lambda naming it or `⎕iter` of it in the function's body
(`_node_of_func`; a function with no node there, a lambda's, is refused as before).  A join, an element written or one taken out
gives a name storage of its own for the write and makes no second way to it.  `_answered` keeps answering such a parameter through
`_heaps_or_copied`, the room being the heap's from the call that answered it.  The C++ is `threaded`, `referenced`,
`Local::named_parameter` and `movable(written, current)`.

**A unit that may stand is a cast.**  `_shorter_life`, which every non-result `_lower_into` goes through, casts a number whose type
differs from the one wanted only in a unit `_stands_for` lets stand -- `bitcast`, which no back end emits anything for.  `_accepts`
already let it through; `_as_declared` then found the value's type not the name's and bound `UndefConst`.  `_Generic` keeps the
defining file's `_stands` and its top-level unit scope, and the call that makes an instance (`_lower_generic_call`) swaps them in
beside `_top` and `_bundles`, as `written.scope` already was; the C++ `Generic` copies the two when it is collected, the units of a
file being settled before any function is.

**A result's error is resolved with no type arguments** (`replace(ref, …, args=())`, `other->args = {}`).  **`_report_unused` skips
`_`.**

**The nought of an error's answer reaches a floating-point register.**  `_select_float_move` on each machine took a register or
memory; an `MCImm` -- what `undefined()` answers for an `UndefConst` -- now goes into a fresh ordinary register by `select_move` and
across by `select_move_bits`: `mov`/`movq` on x86-64, `movz`/`fmov` on AArch64, `li`/`fmv.d.x` on RISC-V.

**A variable in the image whose value is a call waits to be named.**  `_collect_global` leaves `initializer` nothing and puts the
variable in `_waiting_images`, by name, where it is `in ⎕static`, its value an `Allocated` call, nothing `@[expect]`s of it, and the
checker is not `_images_now` -- which `run` sets for a whole program, `whole_modules`, the macros' checker and the language server's,
everything lowered there being lowered at once.  `want(names)` works out every waiting variable among `names` (`_image_now`, which
is `_constant_value` with `_for_the_image` set) before it lowers anything, so a body reading the variable is lowered after it is
known; and `_constant` of a waiting name works it out on the spot, for a number read where it may fold.  A variable nothing names
keeps no initializer and `dropunreached` drops it, being named by nothing -- the one reader of an initializer before that is
`dropunreached` itself, of what is named.  The C++ keeps `waiting_images` as a vector in the order written, so the two compilers
work them out, and lay out what they hold, in the same order.  **An array may be what such a call answers**: `_constant_value`'s
`Call` arm takes an `ArrayType` of one dimension whose type says its length, and `_as_literal` makes an `ArrayLit` of a list where
one is wanted and a `FloatLit` of a float.


What `std.string` is built from
------------------------------

**`modules/string.pl4g` is PL4G and nothing else**: `std` imports it with `@[export]`, and it imports nothing, its tables being its
own.  It says `unit ¤size → ¤idx` once, so that a count of bytes is a place among them -- and since a generic function made for
another file reads its own file's units, `parse_int` and `format_int` mean the same in a program that says nothing of the kind.

**Reading a decimal number** (`parse_f64`) is three loops and three ways to the answer.  The whole part and the fraction each go
through their bytes with one subtraction, one comparison and a multiplication by ten per digit, every digit into one `u64` that is
not asked whether it fits (`⎕wrap`): where at most nineteen digits count, it is right, and where more do, `leading` reads the first
nineteen again, the noughts in front counted, and the exponent takes the rest.  A `_` is looked at only where a byte is not a digit,
and only where `Allow.underscores` says so (`separator`).  Then: nought, which has its sign; **Clinger's**, `⎕float` of the
significand times or divided by `EXACT_TENS⟦k⟧`, where it is below 2⁵³ and the power is 10²² at most, both exact and one rounding;
and **`eisel_lemire(q, w)`**, fast_float's `compute_float` -- the significand shifted to its top bit, one `⎕wide_mul` by the power's
upper word and a second by its lower one where the 55 bits that matter might take a carry, the power of two worked out as
`(217706 × q) » 16`, subnormals by shifting away what does not fit, and the exact-tie test for the powers of five that are exact.  It
answers an infinity's bits where the number is too large, which `parse_f64` refuses.  **Where digits were left out**, `w` and
`w + 1` are both tried and, where they round apart, `closest` walks from the first up or down: `against_half(text, bits)` compares
the number with the point half way between `bits` and the next `f64`, `(2m + 1) × 2^(e - 1)`, as two big integers -- the digits
(800 at most, and whether any after them was not nought) times `5^power` on one side or the other and `2^…` on the other, every
multiplication the one loop over `big⟦row, i⟧`, `u64⟦2, 48⟧` in the frame.  The steps are the digits nineteen at a time
(`TENS⟦19⟧` and what is gathered), then `FIVES⟦27⟧` at a time; the shift is in place from the top word down.  2731 bits is the most
any case reaches (a number of 800 nines at every exponent there is), so 48 words is room to spare.  **Hexadecimal** (`hex_float`)
keeps the first sixteen digits that count and `binary(m, e, more)` rounds on bits: the fraction and the exponent side by side are one
number, and rounding up is adding one, a carry out of the fraction going into the exponent and, past the largest, into an infinity.

**Reading a whole number** (`parse_int`) is one loop whatever the type: `digit_value` of each byte compared with the base, one
`⎕wide_mul` by the base asking whether the magnitude still fits, and `narrowed` at the end, which is `comptime if` the type is
`u64` and a narrowing of the signed number otherwise -- `-(m - 1) - 1`, so that the smallest `i64`'s magnitude never has to be an
`i64`.  `comptime if` is also what lets the narrowing to `T'` be checked where the call says what `T'` is.

**Writing an `f64`** (`format_f64`) is `shortest(bits)`, Schubfach as Giulietti's paper and Bolz's C++ have it: the significand `c`
and the binary exponent `q`; `k = ⌊q × log10(2)⌋` as `(q × 1262611) » 22` -- of three quarters of 2^q, `524031` less before the
shift, where the neighbour below is nearer; `h = q + ⌊-k × log2(10)⌋ + 1` as `(-k × 1741647) » 19`; the three products `to_odd(g, cb « h)` of the
number and its neighbours' half way points, each the upper 64 bits of 64 × 128 with the last bit set where anything below was
not nought; then one digit fewer tried first, and the nearer of two otherwise.  The trailing noughts come off by `tenth`, ÷ 10 as a
multiplication by ⌈2^67 ÷ 10⌉ and a shift; `decimal_count` is `⌊bits × 1233 ÷ 4096⌋` and one comparison with `TENS`; the digits
are written two at a time from `PAIRS` into a `u8⟦32⟧` of the frame, ÷ 100 being the multiplication by ⌈2^68 ÷ 25⌉ of the
quarter, at the place JavaScript's form puts them -- one to the right where the point comes in among them or after the first, the
digits before it moved back after -- with the noughts the buffer was made of, and joined onto the list in one move.  **Writing a
whole number** (`format_int`) is the same pairs into a `u8⟦24⟧` in base ten and a division a digit in any other.  The digits are
written in each writer and not by a function: one writing a buffer it is handed is impure, and one answering a record of digits
was a third of the time, its copies into the caller and back costing more than the digits.

**The tables are functions' answers** -- `exact_tens`, `tens`, `fives`, `pairs`, `powers_of_five`, `powers_of_ten` -- each a literal in
a body, worked out and laid out in the image the first time a function that reads it is lowered.
`tests/compiler/test_std_string_tables.py` makes every one of them again from its definition and compares: fast_float's script for
the powers of five, ⌊10^k × 2^-r⌋ + 1 for Schubfach's.

**What was checked.**  Beyond the language tests, a Python model of every function, word for word in 64-bit arithmetic, was compared
with Python's `float` and `repr` over some two million numbers, and the compiled module with the model over 1.9 million cases on
x86-64 at `-O1` and some 190 000 each on AArch64 and RISC-V under QEMU and at `-O0`: every bit pattern's text and back, the half way
point between random neighbours written out exactly and a digit past it, random long digit strings, every power of ten and of two,
hexadecimal numbers of up to 120 bits, the flags.  What `format_f64` writes was compared with `node`'s `String(x)` for 200 000
numbers.  None differed.


Checks a program cannot fail, and a list a body made answered as it is
-----------------------------------------------------------------------

**The ranges and facts are `facts`'s** -- the paragraph on that pass in the optimizer's section says what is kept and how it is
asked.  What it leaves in `examples/numbers` is what neither can say: an index a call answered (`place`, not inlined), a slice whose
ends a loop moved (`bytes⟦begin…mantissa_end⟧`, which wants `begin ≤ at ≤ n` carried round the loop, an invariant and not a
range), and a difference of two counters.  The language tests `checks-settled-by-ranges` and `checks-unsettled-*` run at `-O0` and
`-O1` on all three machines, the second set stopping where the program can fail; `tests/compiler/test_ranges.py` reads the IR.

**A local answered as it is.**  `_answered`'s case for a parameter taken as the body's own (`_owned_parameter`) takes
`_owned_local(written, func)` beside it: a `NameRef` to a `_Local` that is `mutable` and not `named_parameter`, in a function with a
node in `_node_of_func` (so not a lambda's), whose body `_given_its_own(name, body)` passes -- one `VarDef` of the name, `mut`, not
taking a tuple apart; every `AssignStmt` to it, likewise; each value a `ListLit`, a `⧺`, or a `Call` whose callee is not a builtin
(`Allocated` looked through); no `ForEach`, `Param`, `Pattern` or `Capture` of the name and none in a `more`; no `Defer` that
`_mentions` it; and not `_referenced`.  The answer then goes through `_heaps_or_copied`, as the parameter's does: the allocator the
value carries compared with `⎕heap`'s address, the room answered where they are one and `__pl4g_own` of it where not.  The C++ is
`given_its_own` (`sema/check-helpers.cc`) and `Checker::owned_local` (`sema/check-places.cc`).
What `json` is built from
-------------------------

**`modules/json.pl4g` is PL4G and imports only `std`**, for `std.string`'s numbers.  It says `unit ¤size → ¤idx` once, as
`std.string` does.  Decoding is two stages; encoding is one recursion.

**The first stage, `quickly`**, takes sixty-four bytes at a time -- the last block a copy padded with spaces -- and `block_starts`,
inlined into it, answers a `u64` with a bit for every byte the second stage begins something at, and what is carried into the next
block, a `Carry` of four words.  It is simdjson's arithmetic, written out: `⎕mask` of the block compared with `\` gives the
backslashes; the bytes a run of them escapes are `((potential « 1) | ODD) - potential ^ ODD` on the bits of the runs, the run
carried over a block's end in `escaped`; the quotes that are not escaped, run through a prefix exclusive or -- six shifts and
exclusive ors, `x ^ (x « 1)`, `« 2`, ... `« 32`, what was inside at the last block's end carried in `inside` -- are the bits inside
a string; the six structural characters and the white space are two more masks, and a scalar's first byte is one that is neither
and follows one of them or a quote that closes a string.  The answer is the structural characters and the opening quotes outside
strings, and the scalars' first bytes.  The bits are walked with `⎕trail` and `bits & (bits - 1)` into `places: u32⟦64⟧` in the
frame, and joined onto the index once a block: one join a block rather than one a place, which with three runtime calls a join
(below) is most of the first stage's time.  The index is `[u32]` in an arena of `decode`'s own, given back when it returns, and
ends with the document's length, so that the second stage reads one place past the last without asking.  **`slowly`** answers the
same index a byte at a time, for `comments`, `hash_comments`, `single_quotes` and `whitespace`, which change where a string or
white space is; `lexing(allow)` picks it.

**The second stage, `tree_at`**, walks the index with a stack of its own: `open: [Open] in a`, each an array or an object begun and
where its values and keys begin in `pending: [Value] in a` and `keys: [str] in a`, which hold the values read so far of every open
container one after another.  A value read is written at `pending⟦live⟧`, the room reused once a container has closed; a container
closing makes its list, exactly as long as it is (`elements_of`, `members_of`), of the values above its mark, and that becomes the
value in its parent's place.  So nothing recurses, and the depth limit is a comparison with `#open`.  An object's keys' places are
kept in `spots`, so that `settled` can say, when the object closes, where a duplicate was and what `duplicates` makes of it.
Scalars are `leaf_at`, never inlined: it holds the number reader's frame, and inlined it put `tree_at`'s past the 2047 bytes a
RISC-V offset reaches.  **A string** (`string_at`) is read from after its quote with `stop_at`, thirty-two bytes at a time to the
first quote, backslash or control character -- the last block a copy padded with the quote -- and where that is the closing quote
the bytes between are checked as UTF-8 by `⎕text` and made a `str` in the arena in one move.  Where it is a backslash, `escaped`
takes over, building a `[u8] in a` of the runs between escapes, each found by `stop_at` and joined in one move, and each escape
decoded (`unicode_escape` pairs the halves of a surrogate pair; `json5_escape` the rest), and makes the text once it is all there.
**A number** (`number_at`) of up to nineteen digits and no point is read into a `u64` in one loop, which nearly every whole number
in a document is; anything else is `std.string.parse_int` for a `u64` beyond `i64`, or `parse_f64`, with `number_allow` handing on
the flags the extensions are.  `ends_here` asks that what follows a scalar is a structural character, white space or the end, which
the first stage does not.

**An error** is a `Fault`, its problem and offset, inside the module, and `failure(input, fault)` makes the `Error` a caller sees,
counting lines and characters up to the offset only then.

**Encoding** (`written`) recurses through arrays and objects only and writes everything else with `leaf_written`, never inlined,
so that the numeral's seventy-two bytes and the string writer's blocks are in one frame and not in one a level.  Every writer takes
`into: mut [u8] in a` and answers it, `→ [u8] in a`, the list moved in and out without a copy.  **A string** (`quoted`) is written
thirty-two bytes at a time to the first byte that wants an escape, by `wanted_at`: `⎕mask` of the block below 32, equal to `"` or
equal to `\`, or'ed with the mask of the bytes from DEL up `and`ed with `high` -- all ones with `ascii`, nought without, so that
the loop has no branch on it -- and the run before it is joined in one move.  `escape` writes the five named escapes by name and the
other control characters as `\u00XX`, and with `ascii` the character it decodes there (`unicode`) as one `\u` escape or a surrogate
pair's two, `four` writing four small hexadecimal digits.  **A number** is `std.string.numeral_f64(x, Form.python)` or
`numeral_int(n)`, joined from the `Numeral` in one move; `format_int` keeps its own body rather than joining a `Numeral`, the record's
copy into its frame and out costing more than its digits.

**What was checked.**  The language tests -- `json-round-trip`, `json-errors`, `json-extensions`, `json-depth`, `json-strings`,
`json-numbers`, `json-duplicates` and `json-big`, on all three targets at `-O0` and `-O1` -- and `tests/compiler/test_json_module.py`,
which runs a driver over documents made at random -- nested arrays and objects, text of every length of UTF-8 and every escape,
whole numbers of every size, floating-point numbers of every exponent -- and holds what is written, compact, pretty and ASCII, to
`json.dumps`'s bytes on every target, and what is refused of documents broken a byte at a time to what Python refuses, natively.
The three benchmark documents are read and written back byte for byte as `json.dumps` writes them, at every `x86-64` level.

### Measured

`examples/documents`: `make data` writes three documents of four megabytes with `generate.py` -- `twitter`, the mix of a social
network's API; `numbers`, arrays of numbers of every form; `strings`, long strings, escapes and characters beyond ASCII -- and the
program decodes (`d`), encodes (`e`) or pretty-prints (`p`) one `ROUNDS` times.  x86-64, Zen 5, `-O1`, cycles a byte with the
reading of the file taken out, the best of three runs of ten rounds:

| level | twitter decode | encode | pretty | numbers decode | encode | pretty | strings decode | encode | pretty |
|---|---|---|---|---|---|---|---|---|---|
| `v4` (the default here) | 16.5 | 12.5 | 16.1 | 23.9 | 21.0 | 23.6 | 18.8 | 10.5 | 10.1 |
| `v3` | 16.7 | 12.7 | 16.2 | 23.8 | 21.0 | 23.8 | 18.8 | 10.4 | 9.8 |
| `v1` | 17.5 | 12.6 | 17.3 | 24.1 | 22.0 | 24.6 | 18.9 | 11.2 | 10.3 |

In MB/s at `v4`: decoding 240, 195 and 240; writing compact 330, 240 and 400; pretty 240, 210 and 475.  On the same machine, built
with `g++ -O2 -march=native`: **simdjson** 3.12's DOM parser reads them at 3 010, 1 040 and 2 810 MB/s and its `minify` writes them
at 640, 420 and 600; **yyjson** 0.12 reads them at 1 240, 940 and 800 and writes them at 2 110, 1 200 and 680 (pretty 1 900, 1 160,
675); **Python** 3.14's `json`, its C accelerator, reads them at 166, 140 and 229 and writes them at 234, 94 and 275.

**Where the time goes**, `perf record`: decoding `twitter` is `tree_at` 16 %, `string_at` 14 %, `quickly` 12 %, `leaf_at` 11 %,
`__pl4g_str_valid` 8 % (the UTF-8 check, a byte at a time in the runtime), `escaped` 7 %, and `__pl4g_list_grow` and
`__pl4g_bytes_move` 5 % each; decoding `numbers`, `leaf_at` 33 %, `parse_f64` 20 % and `__pl4g_list_grow` 13 %; encoding `twitter`,
`quoted` 36 %, `__pl4g_list_grow` 17 %, `written` 9 % and `leaf_written` 7 %.  `v3` against `v1` is thirty-two bytes a comparison
against sixteen, and is worth five per cent at most: the blocks are not where the time is.

**What the code generator does that it should not**, each seen in `perf annotate` of the program above:

- **`l ⧺← x` is three calls into the runtime** -- `__pl4g_list_allocator`, `__pl4g_list_grow` and `__pl4g_list_release` -- even
  where the list has room for what is joined, which is nearly always.  It should be a comparison of the length and what is joined
  with the capacity, a store or a `memcpy` and an add, inline, and a call only where the list grows.  This is the 13 to 17 % of
  `__pl4g_list_grow`, and the reason `quickly` gathers a block's places in the frame first.
- **An index is multiplied by its stride as a run-time product**: `places⟦count⟧ ← …` in `quickly` is `mov $4, %rdx; mov %r9,
  %rsi; imul %rdx, %rsi; mov 0x320(%rsp), %rdx; add %rsi, %rdx; mov %r10d, (%rdx)`, where `mov %r10d, (%rdx,%r9,4)`, the base in a
  register, is the whole of it.
- **Checks the loop has already answered**: in the same loop `count` is compared with 64 every iteration (`cmp $0x40, %r9; jb`)
  though a word has no more than 64 bits to walk; `base + ⎕trail(bits)` asks whether a `u32` sum overflowed (`add %edx, %r10d;
  jae`) where `base` is below the input's length and `⎕trail` below 64; `count + 1` asks too.  Each is a compare, a branch and a call
  of `__pl4g_abort` laid out inline, in the hottest loop of the first stage.
- **Constants made again in every iteration**: `wanted_at`'s comparisons with 32, 34, 92 and 127 are each `mov $imm, %edx; movd
  %edx, %xmm2; vpbroadcastb %xmm2, %ymm2` inside the loop, and the all-ones vector `¬` needs is `vpcmpeqd` stored to and loaded
  from `0xc0(%rsp)` every block.  Four splats and a constant belong before the loop, in registers that are free.
- **A comparison spelled the long way**: `block < 32u8` is `vpmaxub` with 32, `vpcmpeqb` and `vpxor` with all ones; it is one
  `vpminub` with 31 and `vpcmpeqb`, or on AVX-512 one `vpcmpub` into a mask register -- which `⎕mask` then wants anyway, where today
  every comparison goes through `vpmovmskb`.
- **The last block of a short string is a store that cannot be forwarded**: `last⟦0…n - at⟧ ← text⟦at…n⟧` is a loop of byte loads
  and stores into the frame, and the 32-byte load of `last` right after it waits for all of them: 58 % of the samples in `quoted`
  fall on that load.  Most strings in a document are shorter than a block, so this is most of writing a string.  A masked load
  (`vmovdqu8` with a mask from `bzhi`), or the block ending at `n` loaded and shifted into place, reads the same bytes without the
  frame.
- **Values in the frame across iterations**: `quickly` reloads the index's base (`0x320(%rsp)`) and the word of bits through a
  pointer (`0x308(%rsp)`, then `(%rcx)`) in every pass of the bit walk, and `quoted` reloads its `high` mask from `0x128(%rsp)`
  every block; the loops have registers to spare for all three.
- **A truth value made to be tested**: a condition joined with `and` is `setcc`, `movzx` and `test` before the branch, as
  `std.string`'s measurements showed; `while at < n and input⟦at⟧ ≠ q` should be two compares and two branches.
- **A sum built and copied**: `Value(.text ← s)` made in a frame slot stores its tag byte and its payload separately and is then
  copied out with eight-byte loads, the load of the tag's word waiting for the byte store; and every sum a function holds gets a
  frame slot of its own, never shared with one that is dead, which is why `tree_at`'s frame is some 1 300 bytes.
- **Small functions not inlined**: `byte_at`, `place_of`, `is_space` and `allows` are a handful of instructions each and were called
  in every iteration until the module said `@[inline]`; a function shorter than its call should not need saying so.
- **The prefix exclusive or is six shifts and exclusive ors**, the language having no carry-less multiplication: simdjson's is one
  `pclmulqdq` of the quote bits by all ones (`pmull` on AArch64, `clmul` in RISC-V's Zbc).  A `⎕clmul`, or the compiler knowing the
  ladder for what it is, would make it one instruction.


What compiling cost after `json`, and where it went
---------------------------------------------------

`bin/pl4g-timing` put pypl4g at 1.57 times 28f579f over every sample after branch `json` (geometric mean; macro-comptime-fn 3.7
times).  Every change below leaves the IR, the code, the diagnostics and the images as they were, with one exception said in its
place: what the bill of materials of a program with macros lists.  `tests/language` compiled by the tree before and after --
IR at `-O0` and `-O1`, images for the three targets with the bill of materials left empty -- and `examples/` at `-O1` are byte
for byte the same.

**The macros' checker lowered every module whole.**  `for_macros` is handed on to the checker of a module the macros read, and
`run` took the branch that lowers everything wherever it was set; since `std` reads `std.string`, that was every number-reading
and number-writing body of the library for a program whose one macro calls nothing of it -- 300 ms of macro-comptime-fn's 392.
What lowers everything is now the checker the compilation started, never one it made for a module (`Checker(imported=True)`, and
`Checker::imported` in the C++), and a module the macros read is lowered as `want` asks, its macros among the roots.  **The bill
of materials of a program with macros lists fewer of its modules' functions**: one row per function whose body was read, which had
been every function of every module, and is now the ones the program or its macros used -- what the specification says it is.

**A body set aside is found with one search.**  `_set_aside` went through the rest of the file a line at a time in Python and
counted each line's brackets with a pattern taking out text, characters and comments; over `std.string`'s 2 600 lines, at every
import.  `_AT_THE_MARGIN` finds the line that ends the body, the comments at the margin before it are found walking back over the
lines between, and `_depth_change` counts over the whole body once -- what it takes out never reaches past the end of a line, so
the count over the text is the sum over its lines.  The pattern's three ways each begin with their own character now (the mark
before a character's quote looked at once the quote is taken), which the engine looks for before trying anything.  A blank run is
skipped with one match (`_BLANKS`), what goes on with a name is one match of `_is_ident_continue` as a pattern (`\w` is
`isalnum` and the underscore), the ASCII spellings are asked about only where a character begins one, and a name beginning with
an ASCII letter is tried first.  Every `.pl4g` file of the tree lexes to the same tokens -- whole, lazily, and body by body.

**`Span`, `Token` and the nodes of the tree are not frozen.**  A frozen dataclass sets each field through `object.__setattr__`,
which was half of what making a span cost; they hash by their fields as before (`unsafe_hash=True`), and nothing changed one once
it was made.  `DeferredBlock` sets its own slots through `object.__setattr__` still, which works either way.

**The walks over a tree ask once for its nodes.**  `ast.fields_of` is `dataclasses.fields` kept per class.  `_below(node)` is the
node and every node under it, each before what it holds and in the order of its fields -- the order the recursive walks met them
-- kept by the node's identity with the node beside it.  `_mentioned`, `_addressed_in`, `_read_through`, `_changed_names`,
`_used_whole`, `_named_in`, `_parameters_in` and `_type_names_in` are loops over it.  `_lifetimes_of` is not: it goes into a node's
own fields and tuples and not into a list or a tuple inside a tuple, which `_below` does.

**The parser** reads the kind straight off the token list in `_check` and `_accept`, which are asked several times a token.

**`conditions` keeps what it asks as it changes it** (`_Ways`).  For every block it might go through it worked out how often each
value is read and every branch arriving at every block -- two walks of the function per block, 764 ms of examples/numbers at
`-O1`.  A walk works both out once: an edge moved counts its arguments off and on (`moved`), a branch replaced counts the jump's
off and its own on and arrives where it goes (`arrives`), and the ways into a block are those still pointing there from the
branch their block ends with now (`into`).  What a block's rewrite does to the next one's question is the same either way, so the
order the ways are taken in does not matter.  33 ms; the C++ `Ways`, 163 ms to 5.

**`simplifycfg` joins in one sweep.**  `_merge_blocks` started again from the top with the edges worked out afresh after every
join.  Joining a block into its one way in changes, for no block that stays, how many ways arrive -- the branches it held go
where they went, from the block it is written into -- and makes no block before it worth joining that was not; so the counts are
worked out once and the sweep goes on from the joined block, and the hundred joins a call may make are kept, the rest left to the
next round as before.  `_settle_params` writes the parameters it drops into the function once at the end, reading each argument
through what was dropped so far (`now`), which is what one replacement after another left; `_absorb` replaces a block's
parameters all at once where none is handed another of them, and one at a time where one is.  `stands_for` makes no list for an
instruction reading nothing it replaces, and `Value` has no `__eq__`: equality was identity already, and a list searched for a
value -- `in`, `index`, `remove` -- compares in C rather than calling Python for every element.  236 ms to 21; the C++, 20 to 3.

**`BinOp`, `UnOp`, `CmpPred` and `CastKind` hash as objects** (`__hash__ = object.__hash__`).  `Enum` hashes the member's name
through Python; equality is identity, and the name's hash differs from run to run, so no answer depended on the value.

**The register allocator walks the physical ranges in step** (`_Held`; the C++ `Held`).  The virtual ranges are assigned in the
order they start; a physical range is looked at from when it starts until one that starts after it has ended -- one that ended
before a range starts meets none after it.  Code generation of std-print-macro, 168 ms to 142.  And `covers`, asked of every pair
of ranges an allocation weighs, walks the stretches with `_parts` and the lengths written out.

**What a function's reads are is asked once** (`branches._Reads`): `condition_used_once` counted the reads of a comparison over
the whole function for every comparison a selector met.  The counts, and the first instruction reading each value as an operand,
are worked out on the first question about the function being lowered and forgotten when the next is (`lowering(func)`, at the
top of each selector's `lower_function`); nothing changes the IR of a function while it is lowered.

**Which edges close a loop is asked of the dominator tree** (`mc.loops._Dominance`, the C++ `Dominance`).  `back_edges` built
every block's dominators as a set, which for a function of a few thousand blocks holds most of it per block, twice for every
round of the allocator -- in the C++ three quarters of compiling examples/numbers.  The tree of immediate dominators, found as
Cooper, Harvey and Kennedy find it, and numbered as it is walked, answers whether one block dominates another in constant time; a
block the entry does not reach is dominated by nothing and dominates nothing, as with the sets.  `dominators` stays, and
`test_spillweight.py` holds `back_edges` to it over three thousand graphs made at random.  Code generation of examples/numbers at `-O1`: pypl4g 1022 ms to
530, the C++ 372 to 67.

**A pipeline imports its own passes** (`pass_._made`): `build_manager` imported all of them whatever the level, and the import was
timed as optimization -- 3 of macro-comptime-fn's 4.4 ms at `-O0`.

### What was left, and why

**`std.string` is read by every program that imports `std`**, which hands it on: lexed and parsed (4 ms) and its signatures
checked.  Reading it only where a program names `std.string` would read it later than where `std` imports it, and what a module
adds to the program -- functions, variables, texts -- is added in the order it is read: the IR of every program that uses it, or
`json`, which imports it after `std`, would come out in another order.

**`⍕` and what it names are checked against their requirements for every program that imports `std`**, an operator being asked for
by no name: `⍕`, `text`, `list_text`, `set_text` and `dictionary_text`, and every function of every module that a name in them names
-- `place` of `std.string` among them -- about 8 ms of exit0's 31, where 28f579f checked `⍕` and `text` in 4.  Asking a generic
operator for by its glyph, as a generic function is by its name, takes exit0 from 1.30 times 28f579f to 1.09; it was tried, and it
moves output, because the check leaves things behind.  The heap's variable is made where `⍕`'s body names `⎕heap` and kept (it is in
`_top`), so it stands earlier among the variables than where a program first names it; a static's name is the number of variables
there were (`__pl4g_static.N`), so every one after it is renumbered; and `leanvalues._fold` takes a part out of a tuple only where
the part's type is the extract's type *as an object* -- and which `ptr<mut arena>` a tuple's operand has depends on what was made
before.  Six tests of generators and tasks came out different, IR and code.  Both are in the to-do list.

### Measured

`python -m pypl4g --time-report`, the best of five, the sum of the stages as `bin/pl4g-timing` sums them, in ms: 28f579f, the tip
of `json` (ebf4d33) and now.  Over the 221 samples the geometric mean is 1.157 times 28f579f, where it was 1.57.

| sample | 28f579f | `json` | now | where it is |
|---|---|---|---|---|
| macro-comptime-fn | 112.7 | 421.6 | 107.3 | macro expansion 93.9, 392.5, 85.5 |
| std-print-macro | 301.1 | 578.9 | 280.5 | macro expansion 96.5, 364.7, 87.6; code generation 141.4, 145.9, 137.5 |
| std-format | 233.9 | 563.3 | 225.2 | semantic analysis 33.5, 47.9, 29.5 |
| exit0 | 28.3 | 50.1 | 36.8 | semantic analysis 20.8, 42.7, 31.4 |
| std-read-write | 108.7 | 138.3 | 112.9 | semantic analysis 43.7, 65.4, 47.1 |
| collection-grows | 51.7 | 85.0 | 68.0 | code generation 23.1, 34.3, 31.6 |
| references | 65.4 | 107.4 | 84.6 | code generation 22.7, 42.3, 37.5 |

What is above 28f579f is work it did not do.  Every program importing `std` reads `std.string` and checks `⍕`'s family -- the 10 ms
exit0 is above it (above).  The collections' code generation is the table runtime the branch grew -- collection-grows's text
3 828 bytes where it was 3 172 -- and the allocator's splitting around calls, which takes more rounds (references: thirteen
allocations for eight functions).  At `-O1`
the passes the branch added cost what they buy: std-read-write's optimization is 32 ms where it was 22, of which `facts` is 4.2,
`conditions` 1.7 and the second `samevalues` 1.9, and they are what took a million numbers' writing from 1 871 instructions a number
to 1 522; examples/numbers spends 120 ms of 508 in `facts`.

The examples at `-O1`, pypl4g, the stages summed: `numbers` 1.3 s where the tip of `json` took 3.0, `documents` 3.5 s where it
took 7.9.  The C++,
the tip of `json` and now: macro-comptime-fn 30.1 ms and 12.9 (macro expansion 26.2 and 9.0), std-print-macro 45.3 and 27.4,
`numbers` at `-O1` 650 and 165 (optimization 251 and 77, code generation 379 and 68), `documents` 1 173 and 434.  The whole of
`python -m pytest tests`, every core: 120 s, now 81.


The manual
----------

The manual is what a program asking for help is shown: a directed graph of short texts, each with a name, every one reached from
`help` by the names the texts mention, none more than four kilobytes.  Four pieces make and keep it.

**The nodes.**  What the language is and how it is used is written by hand, a file per node in `share/manual/`: `NAME.txt`, a
first line `title: ...`, a blank line and the body, a link to another node written `[[name]]`.  Names are lower case and dotted, a
group first and its part after -- `types`, `types.arrays`, `types.arrays.views` -- and a group's node says what its section covers
and links every part, each of which links back.  What the library offers is not written by hand: every module (`std`, `std.io`,
`std.fs`, `std.string`, `json`) is a node, and so is every definition it exports with a `※※` comment, named as a program names it,
`std.string.parse_int`.  A hand-written node named after a module stands in for the module's own introduction.  **A complete
program in a node** is the block indented four spaces under a line `Program:`, and what it prints the block under a line `Output:`
after it; any other indented block is a fragment.

**The generator and its checks.**  `bin/pl4g-manual` makes every node each time it runs.  A library node is the definition's
header as the module writes it -- every line of it: a type's fields with their remarks, a function's `pre` and `post` lines, each
definition of a glyph, read with one operand and with two -- then what the `※※` comment says, its summary, its `\details`, the parameters as an aligned
list, and what it answers and fails with, the prose filled again to 78 columns with a code span never broken.  Only `※※` lines
are documentation: a `※` line among them is a remark about how the thing is made and stays out.  A node ends with the nodes its
comment quotes by name (`See also`) and the module it is part of; a module's node lists its exports by kind and splits over
`NAME.more`, `NAME.more2` and on where the list would pass the limit.  What is checked: every link names a node, every node is
reached from `help`, none is more than 4096 bytes of UTF-8, every `Program:` line has a program under it, and every builtin of
`pypl4g/front/token.py`, every attribute of `pypl4g/sema/attributes.py` and every keyword is mentioned in some node -- so that one
added to the language and not to the manual fails the next build rather than surprising the next reader.  `--check` writes
nothing; `--out DIR` writes the nodes and `manual.pack`, name, nul, text, nul, in the order of the names.

**The library.**  CMake runs the generator whenever a node, a module or what the script reads changes, and `libpl4g-manual.so`
(`src/manual/manual.cc`) embeds the pack with `#embed`: nothing is read at run time.  The same objects, compiled once and
position-independent, are also the archive `libpl4g-manual.a`, for a program that carries the manual rather than loading it; it
leaves fstrcmp to what links it.  `manual-graph` walks the graph through the archive and `pl4g-help` reads the shared object.  It also includes `manual.stamp.hh`, which
the generator writes beside the pack with the pack's SHA-256 in it: a compiler cache hashes what a source includes and not what it
embeds, and ccache handed back the object of the old manual until it had something to see change.  `pl4g::manual::lookup(name)` answers a
node's text; a name that is no node is answered with a sentence saying so and naming up to three nodes whose lower-cased names
are close to it by `fmemcmp`, at fstrcmp's own threshold (`FSTRCMP_THRESHOLD`), so that `JSON.Decode` finds `json.decode`.
`exists` and `names` answer the rest.  `pl4g-help [NAME]` prints a node, exiting with one where there is none.  It needs nothing
of the compiler.

**The tests.**  ctest's `manual-graph` walks the graph through the library as served -- every link a node, every node reached, none
too large, a near miss answered with the close name and a far one with none.  `tests/compiler/test_manual.py` runs the generator's
check and then every program the manual shows: each is compiled by pypl4g for x86-64 and run through `bin/pl4g-run`, and has to
compile without a diagnostic, exit with nought and print what its `Output:` block says.  So an example the language moves away from
fails the suite, naming its node.
