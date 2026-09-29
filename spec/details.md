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
width of the type rather than compared against it -- one `and`, every width being a power of two, in place of a comparison and a
branch that does not come back.

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

**A list is two words and no new runtime.**  The value is where the elements are and how many there are, which `parts_of`
answers as it does for a string and for an array of unstated length -- so a list travels in two registers and needed nothing of
the calling convention's own.  Its elements come from the arena, one allocation per literal, and a join uses the very function a
string's join uses: a join of two runs of bytes is a join of two runs of bytes whatever the bytes mean, and what a list adds is
one multiplication apiece to turn a count of elements into a count of bytes.

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
| `answer-in-storage` | a function answering with more than the registers hold |
| `name-lambda` | the name a lambda's code was given |
| `capture` | a name a capture list saying "all of them" brought in, and how |
| `place-local` | a variable put in storage of its own rather than a register |
| `instantiate` | the types one instance of a generic function was compiled for |
| `lifetime` | how long the reference one call answers with turned out to live |
| `fatal`, `error`, `warning`, `note` | what the compiler said, with its number |

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
answer through the caller's storage is impure from then on**: it writes through a pointer it was handed, whether or not the
program was allowed to write anything.  Without that its calls answer with nothing, are used by nothing, and are swept away with
the answer still unwritten -- which is a wrong-code bug the suite did not catch, the test for that shape having run only at `-O0`.
It now runs at both.

**The textual form prints `impure`**, and the reader takes it.  A form that left it out would read back as a module where
everything is pure, which is the one mistake about this that writes wrong code rather than slow code.

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


Exit statuses the runtime reserves
----------------------------------

`pypl4g/target/statuses.py` is where the reserved range is written down, and every place that ends a program the runtime stopped
reads it from there: the fault helper on each of the three targets, and the x86-64 entry point's check of the processor.  There is
one such number per kind of stop and they are all in one file, so that what a status means is a thing to look up rather than a
thing to remember.

**A fault exits rather than traps**, which is a change from what this compiler first did.  The argument for the trap was that a
signal hands a debugger the stack as it stood; the argument against is that a signal is not a status, a shell reports it as 128
plus the number, and it therefore collides with whatever the program might have chosen to exit with.  What settles it is the
reservation: 64 through 127 being nobody else's is what makes a status able to say "the runtime stopped this" without also
claiming the program meant it.

**The message is still written first**, so nothing is lost either way; what the trap gave up was never the message.

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

What a function may change
--------------------------

`FuncAttrs.impure` says whether a function may change what outlives the call, and everything that would make such a change goes
through `_an_effect`, which reports where the function being lowered did not say it may.  One list rather than a rule each place
remembers: a variable at the top level written, memory the function did not make written, a collection made, and a call to a
function that may do any of those.

**What counts as "memory it did not make" is asked of the value, not of the syntax.**  `_made_here` walks a place back through
the casts and the arithmetic that named it: a `frame` is storage this call made and will lose, and anything worked out from one
still is.  Anything else -- a variable at the top level, a parameter the caller handed over, something read out of memory -- came
from somewhere that outlives the call.  Asking it this way means a row of a local array, an element of one and a slice of one all
answer correctly without any of them being listed.

**`CallInst.has_effects` asks the callee**, by attribute rather than by type, so a callee that is not a function this module knows
says nothing and what it does not say is assumed.  That one line is the whole of the optimization: dead-code elimination already
drops an instruction that nothing uses and that has no effects, so a pure call whose answer nothing reads goes with no pass
knowing what purity is.

**The runtime is impure**, both the hand-written symbols and the generated table functions: they allocate and they write tables,
all of which outlives the call.  They are reached from `_put_key` and its neighbours rather than from `_lower_call`, so what
reports a program using a collection is the collection rule and not the call rule.

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

**A lifetime name never reaches the IR.**  `ast.RefTypeRef.lifetime` holds it, `_lifetimes_of` walks a type reference over its
fields to collect every name written anywhere in it, and `_borrowed_from` turns the names on the return type into the tuple of
parameter positions carrying them.  That tuple is the whole of what the rest of the compiler sees, so the provenance walk, the
call site and the report log say nothing about names at all.  `PtrType` gains nothing either: inside the body a `&u32 ⧖x`
parameter is an ordinary `&u32`.

**A block's parameter is where the walk has to branch.**  A body that answers differently in two arms hands back what the join
came to, which is a block parameter and not either arm's value.  `_answers_from` builds the map from a block to the branches that
reach it -- there being no predecessor list in the IR -- and `_reached_from` requires *every* one of them to reach a source, a
promise being about what the function does and not about what one path through it does.  A parameter already being walked answers
yes, which is what stops a loop going round for ever and is right: what reaches itself round a loop is whatever it was on the way
in, and that is the branch the walk is already looking at.

**Which parameters are carried is on the definition, not in the type.**  `Function.borrows_from` holds their positions, worked
out once by `_borrowed_from` while the signature is collected.  It is deliberately not in `FuncType`: two functions differing
only in which parameters carry the name have the same signature as far as an indirect call is concerned, and an indirect call is
where the promise stops being checkable anyway.

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


A function written where a value is wanted
------------------------------------------

**A lambda's value is two addresses**: where its code is and where what it brought in is.  `parts_of(FuncType)` answers those two
pointers, which is what makes a function value travel the way every other value of several parts already travels -- two registers
as an argument, two words in memory, and the large-answer pass needs no telling.  One type covers the lambda that brought
something in and the one that brought nothing, which is what lets either stand where a `fn(...)` is wanted; the one with nothing
to carry carries the address of a byte nobody reads.

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

**A named function becomes a value through a shim.**  Everything called through a name of function type is called with the
environment first, and a definition has no such parameter -- so `_function_as_a_value` points the pair at `⎕through<name>`, which
takes the environment, drops it, and hands the rest on.  One per function and not one per mention, kept in `_shims` by identity,
and recorded in the report log beside the lambdas: it is code the program did not write.  The other half is the address of a
frame byte, which is what `_environment` already hands a lambda that brought nothing in.

The cost is one call.  Rust avoids it by telling the two apart in the type system, `fn` for the bare address and `Fn` for the
pair; C avoids it by having nothing to carry, which is why a C callback needs a `void *` written out beside it; C++'s
`std::function` pays exactly what is paid here.  A second representation, tagged so that a call could tell a bare address from a
pair, would cost a branch at every indirect call to save one call at some of them.

**A function's own type says whether it walks.**  `_collect_function` and `_make_instance` build it with `func_attrs.listable`,
so `func.ty.listable` is the one place it is written down and a named function handed over carries it.  The symbol does not
move: `mangle` writes the parameters and the result one by one rather than the whole type, so the word appears in a symbol only
where a *parameter* has such a type.

**A bitcast cannot carry a function from one type to the other**, a function being two addresses and not one.  `_shorter_life`
takes the two out and puts them back under the type that promises less, which is the same pair of registers and no work; the
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

**A destructor in `std` drains before the process ends**, which is what "exits
normally" means: a destructor runs when the startup function returns.  That is
also what first exercised a destructor that *calls* anything, and it found a
second bug -- on x86-64 the exit status was moved into the register a system
call takes its first argument in *before* the destructors ran, and an ordinary
call puts its own first argument there.  The status now waits in a register a
call leaves alone, and only where a destructor is going to run.

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

The startup function may take one parameter, and the compiler knows exactly one
type it may be: the record the `std` module calls `Init`.  Which type that is is
settled by where it was written down -- `modules/std.pl4g` beside the compiler,
found through `system_modules()` -- and not by its shape, so a record a program
defines for itself and calls `Init` is a record it defined for itself and is
refused (4404).

`Init` holds an `Io`, which holds three descriptors, and the words the program
was named with.  **What the image carries is the record**, an object in the
writable data called `__pl4g_init`, laid out by the program's own declaration of
the type.  The fields are found **by name**: `io`, whose every part is a
descriptor whose number is known before anything runs and is written into the
image; and `args`, whose two words the runtime fills in when the program starts.
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

**A key that is text is hashed over its bytes** with FNV-1a, in a function
generated beside the one that compares two strings -- and the comparison is that
same function, so a key found in a table and a `=` written in a program cannot
disagree.  A key that fits a word keeps the multiplication it had.  Which of the
two a shape wants is `key_ir_type`, and it is the only place that asks.

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

Putting a callee where it was called
------------------------------------

The inliner walks the same order, callees first, so a callee it reaches is one
whose size and whose calls are final -- which is most of what an inliner needs
and all of what a cheap one needs.  Nothing is looked at twice and nothing round
a cycle is looked at at all.

**What it inlines**: what the program said to (`@[inline]`), whatever the size;
what the whole program calls once and nothing outside can reach, since the copy
is then the only one there is; and what is small -- twelve instructions, a
handful more than a call is made of.  A caller may grow by two hundred
instructions in total, so that a function calling many small ones does not
become one enormous one.  What it never inlines: a function that can reach
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

**Answering with one** is the caller's room.  `ReturnStyle.in_registers` says no
for anything held in memory whatever its size, so `largeanswers` gives the
function the parameter that says where to put the answer -- and for such a value
the rewrite is shorter than for a record: the answer is copied into the room with
a copy written out in the largest pieces that fit, and at the call there is
nothing to read back, the room being the answer.

**What is refused** is a record or a tuple holding a sum (9902).  Both travel as
the values they are made of, so one holding a sum would carry the address of the
room the sum was made in -- which is the caller's frame going down and nothing at
all coming back.  Holding either in memory is the step that fixes both, and the
to-do list carries it.

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

Three states, and what a table is rebuilt for
--------------------------------------------

An entry's first word says which of three it is: empty, holding a key, or given
up.  The third is what taking a key out costs.  A probe walks a path until it
reaches an empty entry, so emptying the entry a key left would end a probe that
has to walk past it to reach what was put there after it; every key that
collided with the one taken out would be lost.  So the entry is marked given up:
`__pl4g_table_slot` walks past it, remembers the first one it walked past, and
answers *that* entry when the probe reaches an empty one -- which is how an
insertion takes back the room a removal left.

The key of a given-up entry is still compared, which matters for one case: a key
put back after being taken out is put back in the very entry it left, so a key
is never in two places along one path.

**The table carries two counts.**  `count` is how many keys it holds, which is
what `#` answers; `used` is how many entries a probe may have to walk past,
which is those and the ones given up.  The load is measured on `used`, since
that is what lengthens a probe; how big the new array is, is decided by `count`,
since that is what will be in it.  So a table crowded by entries given up is
rebuilt at the size it has, and only a table crowded by keys is doubled -- which
is what keeps a table that loses as many keys as it gains from doubling for
ever.  Both counts are written afresh by the rehash, which is where the entries
given up disappear.

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

**The blocks are never reordered.**  The order they are laid out in is the order
the backend walks, and a value has to be computed in a block standing before the
one that reads it; merging removes a block and moves nothing, so that stays true
without anything having to reason about it.  What is merged away is also never
a loop header: a header has the way in and the way round, which is two ways in.

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
