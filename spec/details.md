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
fn @absdiff(i32, i32) → i32 internal cconv(pl4g.v0) {
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

There is a canonical textual form, written by `--emit=ir` and read back by the compiler's own reader.  Values are numbered by
position, so a module always prints the same text; printing, reading and printing again is a fixed point, and that property is
asserted for every stored example.  The form is a testing facility rather than a serialization format: a persistent form, if one
is ever wanted for the pre-digested source files mentioned above, should be a packed binary one instead.

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
  encode one, and that refusal is the contract the future register allocator has to satisfy.
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

Instruction selection produces a machine-level function -- basic blocks holding instructions, with virtual registers allowed --
rather than a finished stream of bytes.  That is where the register allocator, the peephole passes and the scheduler run.  A
peephole rewrite states the condition under which it is valid and checks it: replacing a register-clearing move by an exclusive-or
is three bytes shorter but writes the flags, so the rewrite fires only where a liveness scan has shown the flags to be dead.

The Generated Image
-------------------

The image is a statically linked `ET_EXEC` executable at a fixed address, with no interpreter, no dynamic section and nothing from
the system's runtime.  It has two program headers: one loadable segment covering the headers and the code, and a `PT_GNU_STACK`
without the executable bit, whose absence would give the program an executable stack.

The load address is the same on every target so far, but the alignment of the loadable segment is not: it is the largest page size
a kernel for that architecture may be configured with, so that one image loads whatever the running kernel chose.  That is 4 KiB on
x86-64 and RISC-V, and 64 KiB on AArch64.  Padding is filled with a byte that traps rather than falls through, which is also
target-specific: a breakpoint on x86-64, and on both fixed-width architectures a zero word, which neither of them leaves defined.

A symbol carries a binding and a visibility, and what a program exports decides both.  What is exported is bound globally and left
visible; what is not is bound locally and marked hidden.  Within a single linked image the binding alone would do, since a local
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

A variable at the top level is a value of *pointer* type: naming one yields its address, and reading it is a load.  That is what
keeps every access to memory visible in the dataflow graph instead of implied by a name.  Every load takes a memory token and every
store produces one, so the chain has to start somewhere; a `mem.start` instruction is where.  It is an instruction rather than a
parameter of the function, so that the function's type says nothing about memory.

How much room a value takes and where it must start is computed from a type, never held in one: a size stored in a type would throw
away the freedom the specification gives the compiler to reorder the fields of a product type.  It is computed against a target,
because the width of a pointer is the target's business.

Variables go in a writable section, which the image maps with a second loadable segment.  That segment begins on a page of its own:
two segments sharing a page would have to be mapped with one set of permissions, and which they got would depend on the order they
were mapped in.

Reading a variable is one instruction on x86-64, which can name a place in memory relative to the program counter and widen a
narrow value as it reads it.  On the two fixed-width architectures no instruction can name an address outright, so one is built in
two steps and then read through.  The two differ in how: one computes the page the address lies in and then adds the offset within
it, and the other adds an upper and a lower half, with the second instruction measuring from the first rather than from itself.

Whether a place may be written is part of the pointer's type: the address of a variable defined with `mut` is a `ptr<mut T>` and
the address of one without is a `ptr<T>`.  The verifier asks the pointer rather than the variable, so the rule holds for any place
a pointer can reach and not only for a name the source wrote down.  A local has no pointer and no place; its mutability is a
property of the binding and appears nowhere in the representation.

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

There is no register allocator, so every value a function computes goes to the register a result is returned in.  That is correct
exactly while no two values are live at once, and the backend checks it: a function that would need two is reported as beyond what
this compiler generates rather than compiled wrongly.


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
