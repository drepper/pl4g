Design and Implementation Decisions
===================================

Every decision made about the language, the compiler or the runtime is recorded here, newest last, with the time it was made and
what kind of decision it is.

This is not the log the compiler itself produces.  That one is written per compilation, in JSON, and records the decisions the
compiler made while translating a particular program; it is requested with `--decision-log`.  This file records decisions made
about the project by people.

Each entry states the decision, what else was considered and where it has been done before, and why this was chosen.

---

## 2026-09-12T19:40+02:00 — language

**Attribute notation: `@[name]`, `@[name, name(arguments)]`**

Considered: `#[...]` (Rust), `[[...]]` with namespaces (C++11; aspect clauses in Ada), `{. .}` pragmas (Nim), `@(...)` (Odin), a
bare `@name` decorator (Python, Java, D), `pragma` statements (Ada), magic comments (Go's `//go:...`).

Chosen because it is one construct that is visibly a list and visibly parametrizable, and because `@` is used by nothing else, so
a parser can commit on the first character and the grammar stays context-free.  `#` was rejected as the introducer for the same
reason it was later reserved for comments: it is a single character worth keeping free.  `[` alone was rejected because indexing
will want it in an array-influenced language.

Attributes apply to any kind of object, not only to functions.

## 2026-09-12T19:40+02:00 — language

**Attribute parameters: positional first, then named**

Considered: named arguments only; variants encoded in dotted names (`test.build`, after Go and D).

Chosen after Python, C# and Odin.  It keeps the common one-argument case terse (`align(16)`) while letting an attribute with
several parameters describe itself (`abi("sysv64", variadic=false)`), and it composes where a dotted name does not: an attribute
can have both a variant and a value.  Each attribute declares its own signature, so parsing an attribute list needs no knowledge
of which attribute it is.

## 2026-09-12T19:40+02:00 — language

**Primitive type names: `i8`…`i64`, `u8`…`u64`, `f32`, `f64`, `bool`, `void`**

Considered: `int32`/`uint32`/`float64` (Go, C#, D, Java), `s32`/`u32` (Linux kernel, embedded practice), unsized `int`/`long` (C).

Chosen after Rust, Zig, Odin, WebAssembly and LLVM IR: the shortest unambiguous form, with the width always explicit.  No type's
size depends on the target, which follows from the requirement that a program contain no surprising interpretation of its inputs.
The names are short enough to appear in every diagnostic and every dump of the intermediate representation without cost.

## 2026-09-12T19:40+02:00 — language

**Comments are introduced by `※` (U+203B REFERENCE MARK); `※※` is a documentation comment**

Considered: `#` (Python, Ruby, shell, Nim, Julia), `//` with `/* */` (C, C++, D, Go, Rust, Zig, Odin), `--` (Haskell, Ada, Lua),
`;` (Lisp, assemblers).

Chosen on the user's decision.  A glyph costs nothing in a language that is generated rather than typed, and it keeps `#` free for
a future feature.  Non-nesting block comments were rejected: a generator emits line by line, and a block comment that does not
nest breaks silently when the region it wraps already contains one.

## 2026-09-12T19:40+02:00 — language

**An ASCII substitute for a glyph is allowed only when it is more than one character**

Considered: glyph only, with no substitutes at all (Fortress, Agda); both spellings accepted everywhere (Haskell, Idris).

Chosen on the user's decision, as a rule rather than a blanket policy, so that each glyph is decided on its own.  `->` is accepted
for `→` because a two-character sequence claims no character.  `#` is *not* accepted for `※`, because a single character would
then be unavailable to every future feature.  The glyph stays canonical: it is what the compiler emits.  Using a substitute is
reported by a warning that is off by default, since it is accepted usage and not a defect.

## 2026-09-12T19:40+02:00 — language

**The startup function takes no parameters and its result is the exit status**

Considered: C's two accepted signatures for `main` with an implicit `return 0`; Go's `main`, which returns nothing and sets the
status through a library call; Rust's `main`, which may return any type implementing a trait that decides the status.

Chosen as the most explicit of these.  Control is transferred to the startup function without arguments, so parameters would be
undefined, and the status is the returned value and nothing else.

The return type was `i32` in the first version; see the entry of 2026-09-13, which replaced it.

**Open:** the specification makes every operation that can fail return a sum type.  Whether the startup function's return type
eventually becomes such a sum, with the error variant deciding a non-zero status, is not settled.

## 2026-09-12T19:40+02:00 — language

**Reading of the rule on a trailing `return`**

The specification says the keyword "can and should be skipped" on the last statement, and that a warning is issued.  Read
literally: writing `return` as the last statement is accepted but warned about, and the canonical form omits it.  The other
reading -- warning when it is omitted -- would contradict "can and should be skipped".

## 2026-09-12T19:40+02:00 — implementation

**The bootstrap compiler's module is `pypl4g`, invoked as `python3 -m pypl4g`**

`spec/details.md` said the module is named `pypl4g` but that this allows `python3 -m pl4g`.  Those cannot both hold.  Resolved on
the user's decision in favour of `pypl4g`, and the document has been corrected.  The name `pl4g` stays free for the language's own
package namespace.

## 2026-09-12T19:40+02:00 — implementation

**The intermediate representation uses block parameters, not phi instructions**

Considered: phi instructions (LLVM, GCC's GIMPLE, most of the literature); block parameters (Swift SIL, MLIR, Cranelift, and the
output of the standard construction algorithm).

Chosen because block parameters remove three rules that exist only to keep phis working: that phis come first in a block, that a
critical edge must be split before a value can be placed on it, and the question of where the register allocator inserts its
parallel copies.  The literature being written mostly in terms of phi is a real cost, accepted because the translation between the
two is mechanical.

## 2026-09-12T19:40+02:00 — implementation

**No type in the intermediate representation carries a size, an alignment or a field offset**

The specification lets the compiler reorder the fields of a product type for efficiency.  An offset stored in the representation
would throw that freedom away, and a field is therefore selected by index.  Layout is a side table computed by a late pass.

## 2026-09-12T19:40+02:00 — implementation

**Memory ordering is explicit in the dataflow graph, through tokens**

Decided now although the first version has no memory operations, because retrofitting it would mean rewriting every memory
instruction and every pass that touches one.  A load takes a memory token; a store produces a new one.  Operations whose tokens
are unrelated are provably independent, which is what makes the requirement that no implicit dependency force an order something a
pass can check rather than assume.

## 2026-09-12T19:40+02:00 — implementation

**The symbolic assembler has no text syntax; it is a builder API of function calls**

Considered: printing and parsing Intel syntax, which would have made comparison against a disassembler nearly textual; printing and
parsing AT&T syntax, which is what every existing tool on the system speaks.

Decided on the user's direction that no syntax is needed at all: `asm.loadreg(dst, src)`, `asm.op(PLUS, dst, a, b)` and the rest
build the representation by being called in order, and what they build stays internal.  Inline assembly will be a parser that
drives these same calls, which guarantees it can never express something the encoder cannot emit.  A textual dump exists for
debugging and for stored test files; it is a dump, nothing parses it, and it carries no promise of stability.

Operations are three-address with the destination first; a two-operand architecture lowers that itself, so the same calls will
serve aarch64 and RISC-V.

## 2026-09-12T19:40+02:00 — implementation

**Registers are modelled as views onto storage units**

A unit is physical storage; a register is a view onto a unit at a width and a byte offset.  One mechanism describes
`al`/`ah`/`ax`/`eax`/`rax` and `xmm0`/`ymm0`/`zmm0`, and makes the interference rule -- same unit, overlapping byte range --
correct for both.  Classes are a registry, so general-purpose, vector, mask, flags and segment registers are entries and a new kind
is another entry.  The general-purpose class holds thirty-two units from the start, so the extended registers are addressable and
only the prefix the encoder emits remains to be added.  Virtual registers are in the operand model from the beginning and the
encoder refuses them, which is the contract the future register allocator must satisfy.

## 2026-09-12T19:40+02:00 — implementation

**Encodings are declarative table rows walked by one generic emitter**

The emitter runs a fixed sequence of phases.  A new prefix family is one more emitter in the phase that carries the register
extensions; every other phase is unchanged, because the fields those encodings need are already the fields a legacy encoding uses.
Where several rows accept the same operands the shortest is selected, which follows from the requirement to generate small code and
keeps the choice away from the place that builds the instruction.

## 2026-09-12T19:40+02:00 — implementation

**The first image is a fixed-address `ET_EXEC` executable with section headers and a symbol table**

Considered: a position-independent executable from the start, which is where the compiler has to end up; a minimal image with
program headers only, which would be some three hundred bytes smaller.

Chosen because it needs no self-relocation and because the symbol table is not merely a convenience: with accurate addresses and
sizes it *is* the map of where every function lives, which is what an incremental rebuild needs, readable back out of the
compiler's own previous output.  A position-independent image stays reachable through one binding rule adopted now: the backend
materializes the address of a symbol only through a single helper that emits a program-counter-relative computation, and nothing
may put a symbol into an immediate.

## 2026-09-12T19:40+02:00 — implementation

**The image writer plans and then materializes**

Every offset, address and size is computed before a byte is written; the bytes then go into one buffer of the final size at the
recorded positions.  Nothing is appended and nothing is back-patched.  This is what makes the layout reusable for an incremental
rebuild, and it makes writing only the changed pages a matter of comparing two buffers.

## 2026-09-12T19:40+02:00 — process

**The design log is this file, in Markdown**

Considered: a JSON file with a schema, matching the treatment the diagnostics list already has; JSON as the source with generated
Markdown beside it.

Chosen on the user's decision.  Markdown is readable in the repository and in every diff, and being unlike the compiler's own JSON
decision log keeps the two from being confused.

## 2026-09-12T22:10+02:00 — implementation

**The instruction descriptor is split: what an instruction is, and how it is encoded**

Adding the AArch64 backend showed that the descriptor written for x86-64 was partly x86-64's own: opcode maps, prefixes and a ModRM
byte mean nothing on a fixed-width architecture, where an encoding is one template with operand fields written into it.

The shared descriptor now holds only what an instruction *is* -- its name, the operands it accepts, the registers it touches
besides those operands, its flags and its encoded size.  Each target subclasses it with the fields its own encoder reads, and
nothing outside that encoder looks at them.  Selection, the table and the operand model are unchanged and are genuinely shared.

Considered instead: one descriptor with every architecture's fields present and unused where they do not apply, as some
retargetable assemblers do.  Rejected because the unused fields are not merely wasteful, they are misleading: a row would claim to
have a ModRM byte on an architecture that has none.

## 2026-09-12T22:10+02:00 — implementation

**A fixup kind says how its value is computed; each target says how it is stored**

On x86-64 a displacement occupies a field of its own and is overwritten.  On AArch64 a branch offset shares its word with the
opcode and the registers, is measured in units of four bytes, and for an address computation is split across two runs of bits.
There is no single way to store a fixup, so a kind now states only what its value is measured from -- the address itself, the end
of the field, or the start of it -- and the target supplies the code that puts it into the bytes.

Kinds became values rather than members of one enumeration, matching the treatment operations already had, so that a target
registers the ones it needs without the shared module knowing about them.

## 2026-09-12T22:10+02:00 — implementation

**An immediate operand is matched by declared width on x86-64 and by value on AArch64**

On x86-64 the declared width is what chooses between two encodings of one instruction, so a 64-bit constant must not silently pick
the 32-bit form.  On AArch64 there is no shorter form to fall back to, so what matters is only whether the value fits the field.
The operand specification supports both, and each table states which it means.

## 2026-09-12T22:10+02:00 — implementation

**The zero register and the stack pointer are two units that share a number**

AArch64 spells both as register 31, and which one is meant depends on the instruction.  They are modelled as separate storage
units with the same encoding, because that is what they are: writing the stack pointer changes something and writing the zero
register does not.  It also keeps the interference rule honest -- the two do not interfere, which a single unit would have claimed
they do.

## 2026-09-12T22:10+02:00 — implementation

**`--print-targets`, and the examples take their architecture list from it**

A build system that carries its own list of architectures is a list that rots.  The compiler reports the targets it has, and the
examples build one binary per line of that output; adding this backend needed no edit anywhere in `examples`.

Only canonical triples are reported.  The abbreviations a triple may also be spelled with are still accepted on the command line,
but listing them would make a build system produce the same binary several times.

## 2026-09-12T23:30+02:00 — implementation

**Fixed-width encoding is shared between the architectures that have it**

Adding RISC-V made it plain that describing an encoding as "a template plus the bits each operand occupies" is not AArch64's own
idea: it is what a fixed-width architecture is.  The description, the field model and the encoding loop moved into the shared
layer, and the AArch64 backend was moved onto them with no change in what it produces.

What stays with each target is its table, its relocations and the code that stores a relocated value -- which is exactly the part
the two do differently, since neither's scattered fields resemble the other's.

This does not undo the earlier split.  There are two encoding shapes, not one: a byte stream with prefixes, and a fixed-width word.
The descriptor a target extends still says only what an instruction *is*.

## 2026-09-12T23:30+02:00 — implementation

**The compressed instructions are not used**

RISC-V has sixteen-bit forms of its most common instructions.  They are not emitted, so every instruction is one word.

A fixed width keeps the image layout exact and keeps the padding between functions a whole number of instructions, and the
specification asks for small code rather than for the smallest possible code.  Enabling them later needs no new mechanism: rows
whose encoded size differs are what the table's shortest-encoding rule already handles, and it is the rule x86-64 relies on
throughout.

## 2026-09-12T23:30+02:00 — implementation

**A RISC-V register has two names and one identity**

The architecture gives each register a number and the calling convention gives it a role name; `a0` and `x10` are the same
register.  Both names resolve to the same object, so two spellings of one register compare as the same register rather than as two
equal ones -- which matters, because instruction selection asks whether a move's source is already its destination.

## 2026-09-12T23:30+02:00 — implementation

**RISC-V has no condition-code register, and the model does not invent one**

A comparison and the branch that acts on it are one instruction, so nothing writes flags.  The other two backends declare a flags
register and record which instructions write it, precisely so a pass can ask; here there is no such class at all.  The register
model does not require one, which is the point of classes being a registry rather than a fixed list.

Nothing narrower than a full register exists either: an `i32` lives in a sixty-four bit register, sign extended, and it is the
instruction that says how wide the operation is.  The unit and view model accommodates a unit with exactly one view without any
special case.

## 2026-09-13T00:20+02:00 — language

**The startup function returns `u8`**

Decided on the user's direction, replacing the `i32` the first version used.

An exit status is eight bits wide.  What a program hands to the system is truncated to eight bits before anything can observe it,
so a wider return type lets a program name a status that cannot arrive: written with `i32`, `return 256` compiles and the process
exits with 0.  That is exactly the surprising interpretation of a value the specification rules out, and it is silent.  With `u8`
the literal is out of range and the compiler says so, at the point where it is written.

Considered: `i32` as in C, where the truncation is silent; a dedicated `status` type, which would need a conversion from every
integer and buys nothing over the type that already means "eight bits, unsigned"; and a sum type whose error variant decides the
status, which remains open and is not what a first version needs.

Nothing changed in code generation.  A value of eight bits is returned in the same register the wider type used, because that is
where the calling convention puts a narrow value on all three architectures; only the type the compiler checks against changed.

The compiler enforces it: a startup function that returns anything else is diagnostic 4404, whose message now names the required
type rather than spelling it in prose, so that the catalog and the compiler cannot come to disagree about what it is.

## 2026-09-13T01:30+02:00 — language

**A function's symbol is its signature written out, with nothing encoded**

Decided on the user's direction.  The name a function carries in the generated program is its name, the parameter types in
parentheses separated by commas, and then the result type: `main()u8`, `absdiff(i32,i32)i32`, `take(ptr<u8>,u64)void`.  A module
name prefixes it, separated by a full stop.

Considered: the encoding C++ uses, which substitutes repeated components and prefixes lengths, and which Rust and Swift follow in
spirit; Go's, which is readable but carries no types and so cannot distinguish two functions of one name; and C's, which is the
bare name and distinguishes nothing.

Chosen because the readable form costs only length, and length in a symbol table is not where a program's size lies.  What it buys
is that every tool that shows a symbol -- a disassembler, a symbol table listing, a profiler, a debugger's backtrace -- shows the
signature with nothing in between, and that the compiler needs no demangler, because there is nothing to undo.  The encoded forms
exist to keep names short for linkers that once cared; that is not a constraint here, where the compiler writes the image itself.

The result type is part of the name, so two functions differing only in what they return are different symbols.  Nothing depends on
that yet.  It is the piece that would let a result take part in choosing between functions of one name, and it costs nothing to
have now rather than to retrofit into every symbol later.

A function that declares a foreign calling convention keeps its bare name: declaring one is declaring how a world that has never
heard of this language already knows the function.

Two functions cannot share a symbol.  Today that cannot be stated in source -- differing signatures mangle differently, and
identical ones are already refused as one name defined twice -- so the check is in the verifier, where a failure reports a defect
in the compiler rather than in the program.  It becomes the real check if functions of one name are ever allowed to differ.

The symbol is computed from the intermediate representation alone and is stored nowhere, so every stage that needs it arrives at
the same string without anything being passed along.

## 2026-09-13T03:00+02:00 — language

**A variable is defined with `NAME: TYPE = VALUE` after a keyword, and the colon is always written**

*The keyword was `var` when this was written; see the entry of 2026-09-13T11:00, which replaced it with `let`.*

Decided on the user's direction.  The type after the colon may be left out, in which case it is the type of the value; written
without a space the two characters read as `:=`, but they are the same two tokens and `var x : = 3u8` is the same definition.

Considered: `x: u8 = 3u8` with `x := 3u8`, after Go and Odin, which is terse but leaves a statement beginning with an identifier
ambiguous between a definition, an assignment and an expression until the parser has looked past the name; and `let`, after Rust,
Swift and ML, which parses as easily but in every language that has it binds something that does not change.

A keyword was kept for the reason `@[` was chosen for attributes: a parser commits on the first token, which is what keeps the
grammar context-free and the compilation parallelizable.  Keeping the colon in both forms is what makes them one construct with a
part left out, rather than two constructs that happen to resemble each other.

## 2026-09-13T03:00+02:00 — language

**A variable is always given a value where it is defined**

There is no form that leaves one uninitialized.  C leaves the contents undefined and Go and Java fill them with zeroes; both are
answers a reader has to know rather than read, and the first is exactly the unstated meaning this language rules out.  Nothing is
lost: a value has to come from somewhere, and saying where is no longer than not saying.

## 2026-09-13T03:00+02:00 — language

**An integer literal names its type with a suffix: `3u8`**

Decided on the user's direction.  The suffix is the type's own name, so there is nothing to learn and nothing to look up, and no
ambiguity arises: no integer type's name begins with a digit, and none of the letters a hexadecimal literal uses begins one either.

Considered: `3_u8`, which Rust also accepts and which makes the boundary explicit at the cost of a character on every typed literal
and of two spellings for one thing; and `3:u8`, which reads as "of type" and generalizes beyond literals, but spends the colon that
already ends a function header and opens a block.

A literal takes its type from its suffix, or from the context where it has none.  Where it has both they must agree.

## 2026-09-13T03:00+02:00 — language

**A literal with neither a suffix nor a context is an untyped value, which is not implemented**

Decided on the user's direction.  Such a literal is a number of no particular width that takes the type of wherever it ends up, as
in Odin, and the same will hold for floating-point literals.  That is not implemented, and a literal that would be one is reported
as a feature the compiler lacks.

Considered and rejected: defaulting to `i32`, as Rust, Go and C# do.  It is convenient, and it makes the width of a value depend on
a rule the reader has to know -- `var c := 3000000000` would mean something other than it appears to, silently.  Saying the
compiler does not implement this yet is true; picking a width would not be.

## 2026-09-13T03:00+02:00 — implementation

**A local variable is a value; a variable at the top level is an address**

A local is bound to whatever its initializer produced, with nothing reserved in memory, because nothing can take its address.  When
assignment arrives, a block parameter carries the new value across a branch, which is what block parameters were chosen for.

One at the top level is a value of pointer type, so naming it yields its address and reading it is a load.  Every access to memory
is therefore an instruction in the graph rather than something a name implies, which is what the memory tokens are there to order.
The chain starts at a `mem.start` instruction rather than at a parameter of the function, so that a function's type says nothing
about memory.

## 2026-09-13T03:00+02:00 — implementation

**Layout is computed from a type and never held in one**

The size of a value, and the boundary it must start on, are computed by a pass of their own against a particular target.  A size
stored in a type would throw away the freedom the specification gives the compiler to reorder the fields of a product type, and the
width of a pointer is the target's business rather than the type's.  Product types are laid out in declaration order for now;
choosing a better one belongs in that pass, where no type has to change for it.

## 2026-09-13T03:00+02:00 — implementation

**Every value goes to one register, and a function needing two at once is refused**

There is no register allocator.  Every value a function computes is put in the register a result is returned in, which is correct
exactly while no two values are live at the same time.  The backend checks that: a value must be read by the instruction directly
after it, or by nothing at all.

A function that would need two at once is reported as beyond what this compiler generates.  Refusing is the only honest thing to
do; the alternative is code that is wrong in a way nothing would catch.

## 2026-09-13T03:00+02:00 — implementation

**Two relocation defects, found by checking against the linker rather than by reading the manual**

Implementing addresses turned up a defect that had been in the AArch64 backend since it was written.  The instruction that computes
an address a page at a time needs the difference of the two *pages*; the code computed the difference of the two addresses and
shifted it down.  Those agree only when the instruction is itself page-aligned, which it happened to be in every test until now.
Fixup kinds gained a base that says "the page this lies in", so the computation is stated once, where every other one is.

The second was a gap that would have become a defect: the pair of instructions RISC-V uses to compute an address is not two
independent halves, because the second is measured from the first.  A fixup can now say what it is measured from.

Both are checked against words taken from the GNU linker, with its relaxation switched off so that the pair survives to be read.

## 2026-09-13T11:00+02:00 — language

**The definition keyword is `let`, not `var`**

Decided on the user's direction, replacing the `var` chosen earlier the same day.

The argument made against `let` at the time was that in Rust, Swift and ML it binds something that does not change, so using it for
an assignable variable would make the word say the opposite of what it does.  That argument was answered by the language itself.
The specification calls for a predominantly functional style with pure functions, curried functions and combinators; assignment is
not part of the language and may never be the ordinary way to write it.  A word whose whole history is in that tradition -- ML,
Haskell, Rust, Swift, and mathematical writing before any of them -- is the right one for a language of that shape, and the
objection only has force in a language where reassignment is the default, which this is not.

What `let` gives up is the pairing with `var` that Kotlin and Scala use to distinguish the two.  If the language later wants a form
that can be reassigned, the pair is available: `let` for the definition and something else for the other, which is the direction
Rust took with `let mut` rather than the one Kotlin took.  That choice belongs with the rules on purity and effects it interacts
with, which are not specified.

Nothing else changed.  The construct, the colon that is always written, and the type that may be left out are all as they were; the
intermediate representation uses the same word, since it denotes the same thing and two spellings would only invite a reader to
look for a difference.

## 2026-09-13T11:00+02:00 — language

**A value too large for its type is an error, at every point where a value meets a type**

Confirmed and extended on the user's direction.  A program that stored 300 in a `u8` and read back 44 would not be behaving as it
reads, and no rule about which bits survive would make it so.  C and Go narrow silently here; C++ does unless the initializer is
braced; Rust rejects a literal that does not fit but wraps a conversion unless it is asked not to.

The semantic analysis already refused every such value a program can write, in a literal or in the value a variable is defined
with, and tests now cover each way of writing one -- with the type declared, with it derived from a suffix, with a literal that has
no suffix, and at the boundary of every integer type.

What was missing was underneath.  Three places turned a value into bytes by masking it to a width: the initial contents of a
variable, an immediate in an instruction, and a patched displacement.  None of them can be reached by a program that compiled, so
each is now an error reporting a defect in the compiler rather than a silent wrap -- which is the same rule, applied where it is
least likely to be noticed if it is broken.  The displacement case had a further consequence: a branch too far to reach was
wrapping rather than being reported, on the one architecture whose relocations had no range check.

---

Open questions
--------------

These are recorded so they are not lost.  None of them blocks the current version.

- **Patching a binary while it is in use.**  `spec/details.md` asks for hooks into the system that controls binary creation so that
  a binary can be changed while it is being used.  Linux refuses to write to a running executable's file.  Doing this needs a
  concrete mechanism -- writing to the process's memory, a `memfd`-backed scheme, or a supervisor built into the generated runtime
  -- and none has been chosen.

- **The error path before `io_uring` exists.**  The specification requires a message when a required CPU or operating system
  feature is missing, forbids depending on any system runtime, and routes all input and output through `io_uring` -- whose own
  setup can fail before any object exists to report through.  The bootstrap path for that first message needs specifying.

- **A garbled sentence in the specification.**  "the grammar has to be context-free, there is no process definitions in order" is
  read as "there is no *need to* process definitions in order", so a forward reference at the top level is legal.  The semantic
  analysis collects every top-level definition before checking any body, which is what makes that reading true.

- **Assignment.**  A variable is given a value where it is defined and cannot be assigned to afterwards.  Nothing needs it yet,
  and it is what will first make a store necessary.

- **A unary minus.**  A negative number cannot be written: `-3i8` is a negation of a literal rather than a literal, and the
  expression syntax has no unary operators.

- **Naming a symbol outright.**  A function following a foreign convention keeps its bare name, which covers calling into another
  world.  There is no way to say what a function should be called without also saying how it is called -- an attribute naming the
  symbol directly.  Nothing needs it yet.

- **Materializing the address of a symbol on RISC-V.**  The rule adopted for position-independent code is that an address is only
  ever produced by one helper that emits a program-counter-relative computation.  On x86-64 that is one instruction and on AArch64
  a pair whose two halves are independent.  On RISC-V the pair is not independent: the second instruction's relocation refers to
  the label of the first rather than to its own address.  Nothing emits an address yet, so rather than implement half of it the
  backend has neither relocation, and the two instructions are present only in the form that takes a plain immediate.

- **The RISC-V header flags.**  The ELF header of a RISC-V image carries flags saying which extensions the code uses and which
  floating-point convention it follows.  Zero is correct while only the base integer set is emitted; emitting floating point will
  mean setting them, and the image writer has no field for them yet.
