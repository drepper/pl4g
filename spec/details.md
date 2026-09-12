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
how it is spelled in memory is not, because the two architectures have nothing in
common there.  x86-64 describes an encoding as a byte stream with prefixes, an
opcode map and a ModRM byte; AArch64 describes it as one 32-bit template plus the
bits each operand occupies.  Each target extends the shared descriptor with the
fields its own encoder reads, and nothing outside that encoder looks at them.

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
own and is simply overwritten; a branch offset on AArch64 shares its word with the opcode and the destination register, is measured
in units of four bytes, and in one case is split across two runs of bits.  A fixup kind therefore says how its value is computed --
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
x86-64 and 64 KiB on AArch64.  Padding is filled with a byte that traps rather than falls through, which is also target-specific: a
breakpoint on x86-64, and the word the architecture reserves as permanently undefined on AArch64.

The section headers and the symbol table are kept.  They are what lets a disassembler and a debugger show the generated code, and
the disassembler is the independent check on the encoder.  More importantly, a symbol table with accurate addresses and sizes *is*
the map of where every function lives -- which is exactly what incremental recompilation needs, readable back out of the
compiler's own previous output without a file beside it.

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

Two architectures have backends: x86-64 and AArch64.  RISC-V is next.

The backend for an architecture is `pypl4g/target/<arch>/`, holding its register
file, its operations, its encoding table, its encoder, its relocations, its
calling conventions, its instruction selection and its entry point.  Nothing
outside that directory knows about the architecture, and `--print-targets` is how
anything else -- a build system, the examples -- learns which ones exist.

What the two backends do differently is worth stating, because it is what the
shared layers had to be able to express:

| | x86-64 | AArch64 |
|---|---|---|
| Instruction length | one to fifteen bytes | always four |
| Encoding described as | prefixes, opcode map, opcode, ModRM, SIB, immediate | a template plus the bits each operand occupies |
| Choosing between rows | by encoded size; several forms of one instruction differ in length | by table order; every row is the same size |
| Immediate matching | by the declared width, which is what selects the shorter form | by whether the value fits, since there is no shorter form |
| Relocation storage | overwrite a field of its own | insert into a word that already holds the opcode |
| Three-address operations | lowered to two operands with a move | native |
| Register 31 | nothing special | the zero register or the stack pointer, depending on the instruction |
| Exit system call | number 231 in `eax`, arguments from `edi` | number 94 in `x8`, arguments from `x0` |
| Padding | `int3` | the permanently undefined word |
| Segment alignment | 4 KiB | 64 KiB |
