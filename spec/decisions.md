Design and Implementation Decisions
===================================

Every decision made about the language, the compiler or the runtime is recorded here, newest last, with the time it was made and
what kind of decision it is.

This is not the log the compiler itself produces.  That one is written per compilation, in JSON, and records everything the
compiler said about a particular program and chose about it; it is requested with `--report-log`.  This file records decisions
made about the project by people, and keeps the word *decision* for them; what the compiler writes down is a **report**.

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

## 2026-09-13T14:00+02:00 — language

**A variable can be changed only if `mut` says so**

*`mut` stood before the name when this was written; see the entry of 2026-09-13T16:00, which moved it into the type.*

Decided on the user's direction, taking the word from Rust: one keyword for a definition, with `mut` where change is admitted.
Unchanging is the default.

Considered: Kotlin's and Scala's two keywords, `val` and `var`, which make the two look like different constructs when they differ
in one property; C's and Go's arrangement, where everything may change and the exception is marked; and ML's and Haskell's, where
nothing may.

The default is the one worth having by default.  A name that keeps its value can be reasoned about wherever it appears, which is
what the specification's functional style calls for, and a name that does not is worth marking where it is introduced rather than
where it is changed.  This also settles the question left open when `let` replaced `var`: the pair is `let` and `let mut`, not
`let` and `var`.

## 2026-09-13T14:00+02:00 — language

**Assignment is written `←`; `=` is reserved for comparison**

Decided on the user's direction.

The arrow is the older notation: Algol, Smalltalk and APL all wrote assignment with a left-pointing arrow, and what replaced it
with `=` in C was the ASCII character set rather than any argument about language design.  Having spent `=` on assignment, those
languages needed something else for comparison and chose `==`, which is why `if (x = 0)` is a mistake C makes easy and why several
later languages spend a compiler warning on it.  Pascal, Ada and Go avoid the collision by spelling assignment `:=`; this language
avoids it by leaving `=` to mean what it means everywhere outside programming.

A language written with glyphs has no reason to inherit that particular accident, and this one already has the arrow's mirror
image in a function's return type.

`←` has no ASCII substitute.  The rule adopted earlier allowed one only where it is more than one character; this
adds the other half of it, that a substitute must not be ambiguous.  `<-` is two characters, but `x <- y` and `x < -y` would be
told apart only by the spaces around them -- a distinction the language makes nowhere else, and one R lives with.

Writing `=` where an assignment belongs is reported and names the arrow, rather than being left to become a comparison whose result
is discarded.  That is the habit every other language teaches, and answering it with the rule costs one diagnostic.

## 2026-09-13T14:00+02:00 — implementation

**Assigning to a local writes nothing; assigning to a variable at the top level is a store**

A local is a value, so an assignment binds the name to a new one and the function that results is the same as if the final value
had been written in the first place.  When control flow arrives a block parameter will carry the new value across a branch, which
is what block parameters were chosen for, so a local will not need memory even then.

A variable at the top level is an address, so an assignment is a store.  It takes the memory token and produces a new one, which is
what orders a read after it -- and what will let a read that takes the older token be shown not to depend on it.

## 2026-09-13T14:00+02:00 — implementation

**Each fixed-width backend sets aside two registers for a store**

A store needs the address and the value at once, which the single register this compiler has is not enough for.  Two registers are
set aside per target, chosen from those the calling convention leaves to the caller to preserve, since nothing of the compiler's
holds a value across the few instructions a store takes.  x86-64 needs neither, writing to a place in memory directly and taking
the value as an immediate where there is one.

This is not a register allocator and does not pretend to be one.  It is enough for a store and no more, and it will be the first
thing an allocator replaces.

## 2026-09-13T14:00+02:00 — implementation

**The width an immediate is encoded at is not the width of the access**

An eight-byte store on x86-64 carries a four-byte immediate that the instruction widens.  An operand therefore states how large the
*number* is, and the table row states how large a number that form can carry; the memory operand states separately how much of
memory is written.  Conflating the two made a perfectly ordinary store of a small constant into a wide variable fail to select.

A constant larger than one instruction can carry -- twelve signed bits on one architecture, sixteen on another -- is reported
rather than assembled from a sequence, which this compiler does not generate yet.

## 2026-09-13T16:00+02:00 — language

**`mut` qualifies the type, not the name**

Decided on the user's direction, replacing the placement chosen earlier the same day.  `let count: mut u8 = 0u8`, with the
qualifier where the type stands; either part after the colon may be left out, so `let count: mut = 0u8` is the form with the type
derived from the value.

Whether a thing may be changed is a property of the thing, not of the name it is reached by.  That is not merely tidiness, and the
implementation is what shows it: a value is a value, and it is the *place* that is writable or not, so when a variable is an
address -- as one at the top level is -- what carries the qualifier is the pointer.  `ptr<mut u8>` and `ptr<u8>` are now different
types, the verifier asks the pointer rather than the variable, and the rule therefore holds for any place a pointer can reach
rather than only for a name the source wrote down.  With the qualifier on the name there would have been two rules to keep in step.

Rust, whose word this is, places it the other way -- `let mut x: u8` qualifies the binding, and the type-level form `&mut u8`
exists only for references.  C and C++ place `const` in the type, with the default the other way round.

A local keeps no pointer and no place, so its mutability is a property of the binding and appears nowhere in the representation.

## 2026-09-13T16:00+02:00 — language

**An assignment stands for the variable it changed**

Decided on the user's direction.  An assignment refers to the variable rather than to the value written, so reading it gives what
the variable now holds; as the last statement of a function it is the function's result, the way any other last statement is.  For
the startup function that means an assignment to a `u8` variable ends the program with that variable's value.

Referring to the variable rather than to the written value is what makes this the ordinary rule rather than a special case: the
last statement of a block is its result, and this statement's result is the variable.  In the representation the read is a load
that takes the token the store produced, so what comes back is what was written by the same rule that governs every other read --
nothing had to be said about assignment in particular.

Compare C and C++, where an assignment is an expression yielding the value assigned, which allows `a = b = 0` and equally
`if (x = 0)`; Python and Go, where an assignment is a statement with no value; and Algol 68, where it yields the variable, as here.
An assignment remains a statement and cannot appear inside an expression, so the hazard C has does not arise; what it has is a
result, which only the last statement of a block is in a position to use.

Where the result is not wanted, no load is emitted: reading a place nothing looks at would be an instruction the program never
asked for.

## 2026-09-13T18:00+02:00 — language

**A value that nothing reads is reported**

Decided on the user's direction.  Where nothing reads what a variable was given, between the point it is given and the point it is
replaced or the variable goes out of reach, giving it cannot have affected what the program does.

It is a warning rather than an error: the program means something, and what it means is simply less than it appears to.  What it
usually means is that something reads the wrong thing.

Only variables inside a function are examined.  One at the top level can be read by any function of the program, and whether
anything does is a question for a pass over the whole program rather than for the scope that defines it.

## 2026-09-13T18:00+02:00 — language

**`@[expect(NUMBER)]` says what a construct raises, and the diagnostic is not reported**

*This was the only such attribute when it was written; see the entry of 2026-09-13T20:00, which added `ignore` beside it and made
a stale `expect` an error rather than a warning.*

Decided on the user's direction, with the syntax and the number the user proposed.

The word is `expect`, not `allow` or `suppress`, and it behaves as it reads: an expectation nothing meets is itself reported.  That
is the distinction Rust draws between `#[allow]` and `#[expect]`, and it is what keeps a suppression from outliving the thing it
suppressed -- a stale one hides nothing while saying something untrue.  This was not asked for; it follows from the word.

In the layout notation the attribute stands on its own line, indented with the statement it belongs to, which is what says which
statement that is.  It may be repeated, which no other attribute may: one expectation names one diagnostic and a construct may
raise several.  The number must be one the compiler can emit, since a number nothing can raise could only ever be a mistake.

## 2026-09-13T18:00+02:00 — language

**An expected error discards the construct it is attached to**

Decided on the user's direction.  A definition that raises an error cannot be compiled, and half of one would be worse than none,
so the function or the variable is left out of the program entirely.

What follows from leaving it out is not hidden in turn: discarding the only startup function is still reported as there being none.
That is the honest behaviour -- the expectation said what *that* definition raises, and said nothing about the program that remains.

## 2026-09-13T18:00+02:00 — implementation

**An expectation belongs to the diagnostic engine, and a definition hands its own to what it defines**

Putting expectations in the engine rather than in any one stage means a diagnostic raised anywhere while a construct is checked is
covered, without every stage having to know they exist.

A definition hands its expectation to the variable it defines rather than settling it where the definition ends, because not
everything a definition raises is raised while it is being read.  That nothing ever reads the value a variable was given is only
known once the variable is gone, so the expectation is put back in force there and settled there.  Written the other way -- purely
lexically -- an attribute on a definition could not cover the very diagnostic it most obviously should.

A value nothing reads is found by the scope rather than by a pass: a binding records whether anything has read what it stands for.
That is exact for straight-line code, which is all the language has.  When control flow arrives it becomes a liveness analysis over
the graph, and where it is reported from will move with it.

## 2026-09-13T20:00+02:00 — language

**`@[ignore(NUMBER)]` quiets a diagnostic; `@[expect(NUMBER)]` asserts it, and a stale assertion is an error**

Decided on the user's direction.  The two attributes differ in one thing: what happens where the diagnostic does not arise.
`ignore` says nothing.  `expect` reports it, and reports it as an error rather than as the warning it was.

The severity is the point of having two.  An attribute that merely permits can outlive what it permitted with nothing to notice,
which is the state every C and C++ codebase with a long-lived `#pragma GCC diagnostic ignored` is in.  An attribute that asserts is
worth relying on only if the compiler checks it, and a claim about a program is either so or it is not -- there is no degree of
staleness that makes a false statement a warning.  Where the intent is only quiet, `ignore` says exactly that and nothing further.

Rust has the same pair, `#[allow]` and `#[expect]`, and reports the stale case as a warning.  The difference here follows from what
the two words are for: `allow` grants permission, which cannot be wrong; `expect` makes a claim, which can be.

The number of the stale-assertion diagnostic did not change when its severity did.  That is the rule the catalog is built on, and
it is what lets a program react to a number without having to know how seriously the compiler currently takes it.

## 2026-09-13T21:00+02:00 — language

**Nothing is visible outside a program unless `@[export]` says so**

Decided on the user's direction.  The attribute applies to a function and to a variable alike, and nothing else changes what is
visible.

The default is the one worth having by default: what a program exports is its interface and is worth stating, and what it does not
export it can change freely with nothing outside able to have depended on it.  C has the opposite default, with `static` as the
exception, which is why a name never meant to be part of an interface so often becomes one by accident.  Rust, Go and Java all keep
things in by default; C++20 modules were added to give C++ the same.

The attribute already existed and already worked for a function.  For a variable it was parsed, checked and then dropped -- the
result of binding the attributes was discarded -- so `@[export]` on a variable did nothing at all.  Both now go through one place
that answers the question.

## 2026-09-13T21:00+02:00 — implementation

**Exporting decides both the binding and the visibility of a symbol**

They are different questions.  The binding says whether a name is one among many in this image or one the whole program shares; the
visibility says whether anything outside may reach it.  What is exported is bound globally and left visible, and what is not is
bound locally *and* marked hidden.

Within a single linked image the binding alone would be enough, since a local symbol cannot be named from outside it, and the
format itself says visibility is not meaningful for one.  Marking it anyway is not redundant for long: the visibility is the part
that still says so if the symbol is ever made global by something later, and it is what a relocatable or a shared object would
need.  Saying it once, where the program says it, is cheaper than working it out again when those exist.

The entry point stays visible.  It is the compiler's own rather than something the program declared, so the rule about what a
program exports does not reach it, and every tool that inspects a binary expects to find it.

## 2026-09-13T22:30+02:00 — process

**Every generated image is checked against the format by `eu-elflint --strict`**

Decided on the user's direction, and applying wherever and whenever a binary is produced rather than at one place in the tests.

The compiler writes the image itself, with no assembler and no linker between it and the file.  That is the point of the design and
it is also what removes the only other thing that would have noticed a field filled in wrongly: a header that disagrees with itself
produces a file the kernel may still load and a tool may still read, and the mistake then surfaces much later and somewhere else.
The strict form additionally reports what common practice allows but the standard does not, which is the level worth holding a
compiler to when it is the one writing the bytes.

It runs on every binary any test builds rather than on a chosen few, so it holds for whatever is added later without anyone having
to remember.  One test damages an image deliberately and requires the check to reject it, because a gate that cannot fail guards
nothing.

All hundred and two binaries the tests currently produce, across the three architectures, pass it as they stand; nothing had to be
fixed.

## 2026-09-13T23:30+02:00 — implementation

**A constant goes in a read-only section, which the image maps with a third segment**

A variable not defined `mut` cannot be assigned to, and until now that was a promise only the front end kept: the value sat in
`.data` beside the ones that can change, mapped writable, so anything that reached it another way -- a pointer the type checker had
been argued out of, a bug in the compiler itself, a write from another thread of the program's own making -- would simply have
worked.  Putting it in `.rodata` makes the fault happen where the mistake is.

The cost is a third loadable segment.  A segment carries one set of permissions for everything mapped through it, so read-only,
read-and-execute and read-and-write cannot share one, and two of them may not share a page either: which permissions the shared
page ended up with would depend on the order the segments were mapped in.  The grouping in the image writer is therefore by the
permissions a section asks for rather than by a writable flag alone, and a group with nothing in it gets no segment, since an empty
one would still cost a page.  A program with no constants and no variables still has exactly one loadable segment.

What other languages do.  C and C++ reach the same place from the other direction: `const` at file scope is a promise about the
type, and whether the object lands in `.rodata` is the implementation's choice, one that every serious implementation makes when it
can prove the initializer is constant.  Rust's `static` is read-only and `static mut` is not, which is the same split under
different names.  Go puts its constants nowhere at all -- they exist only at compile time.  Zig's `const` at container scope may be
elided entirely.  PL4G takes the C++ position for now, since a constant still has an address and a name, and the second half of the
same question -- dropping a constant nothing refers to -- waits on a notion of a reference the language does not yet have.

The headers now fall in the read-only segment rather than the executable one, which is what modern linkers also do and means the
executable segment covers the code and nothing else.

## 2026-09-14T00:30+02:00 — implementation

**A local nothing refers to is dropped by asking about uses, not about variables**

The other half of the constant question: a local that is not `mut` and that nothing refers to can be removed entirely.  A local is
already a value rather than a place, so one whose initializer is a constant never reaches the representation at all; what was left
was the case where the initializer computes something -- a read of a variable at the top level, say -- and the instruction stayed
in the image although nothing used its result.

It is removed by a dead-code sweep over the representation rather than by a rule about variables in the front end.  The two would
drop the same things today, and the difference is what happens when the language can keep a reference to a local, which is the
"new concept" the task itself names as coming.  A reference is a use like any other, so a local that one points at simply stops
being dead, and the pass needs to learn nothing.  A front-end rule would have to be told.

Whether an instruction may go when nothing uses it is answered by the instruction.  A pass holding the list would be a list to
forget to update; a property on the shape makes "this has no effect" something each new shape has to state.  A read states it: the
only memory the language can name is the program's own, so a read nobody looks at is one nobody can tell happened.  A device
register, where reading is itself an action, would have to say so on the instruction, and the language has none.

The sweep runs from `-O1` and last, since constant folding and control-flow simplification both leave dead code behind.  An
unoptimized build keeps what the program wrote, which is what makes stepping through one match the source.  Dropping it is
permitted, not required; what is *required* is the warning that nothing reads the value, and that is issued at both levels, long
before any pass runs.

What other languages do.  C and C++ leave this to the optimizer entirely and warn separately, which is the arrangement here.  Rust
warns about an unused binding and expects the back end to remove it.  Go refuses to compile an unused local at all -- a stronger
rule, and one worth considering for PL4G later, but a change to the language rather than to the compiler, so it belongs in the
language list and not here.

The remaining piece is the one the task called eventual: a local that has been dropped should still appear in the debug
information, defined as the constant expression it was.  There is no debug information yet, so there is nothing to put it in; it
stays on the list.

## 2026-09-14T01:30+02:00 — implementation

**A function nothing can reach is left out, and reachability is computed forwards from roots**

A function that is not exported and that nothing calls cannot be called at all.  That is not a guess: compilation covers the whole
program and there is no equivalent of an object file, so there is no later stage at which something else could refer to it.  Bytes
no program can run do not belong in the image.

It is computed forwards from a set of roots, not by asking of each function whether anything calls it.  The two differ exactly
where it matters: a function called only from another function that is itself unreachable has a caller and is still unreachable,
and only the forward computation sees that.  The roots are the ways into the program from outside it -- the startup function, the
constructors and the destructors, which the entry point calls; the tests; and everything exported.

A test is a root although nothing calls one today.  What calls a test is the testing machinery, which the specification describes
and nobody has written; dropping a test because its caller does not exist yet would be dropping it for a reason that has nothing to
do with the program.

What counts as naming a function is answered by the instruction, as with effects.  A call names its callee.  Nothing else can name
one yet, because a function is not a value in the representation; when it becomes one -- a pointer to a function, a table of them
-- the shape that holds it says so and the reachability computation does not change.

This runs at every optimization level, unlike the dead-code sweep decided earlier.  The distinction is that dropping a local is
about code the program can still run and merely does not use, where keeping it at `-O0` buys debuggability; a function nothing can
reach is code the program cannot run at all, and keeping it buys nothing.  Generating small code is a stated requirement, and this
is the cheapest kind of small there is.

What other languages do.  C and C++ leave it to the linker, with `--gc-sections` and one section per function, because a
translation unit is compiled without knowing what else will be linked; the information this compiler has at the point of decision
is what the linker has to be handed separately there.  Rust does the same through LLVM and the linker.  Go's linker drops unreached
functions by the same forward reachability, and is able to be more aggressive precisely because it, too, sees the whole program.

Left for later, and on the list: a variable nothing reads is not dropped, even when the only thing that read it was a function that
has just been dropped.

## 2026-09-14T10:30+02:00 — implementation

**A variable follows the functions: dropped when nothing names it, reported when only writes name it**

Two entries, one question — which functions read and which write each variable at the top level — so they are answered together.

*Dropping.* A variable is reached when a function that is itself reached names it.  Dropping a function can therefore be exactly
what leaves a variable unreachable, which is why both are settled in one pass and in that order; two passes would have to be run
until they agreed, and the order they agreed in would be the whole of the answer.  An exported variable is a root of its own, for
the reason an exported function is: something outside this compilation may name it.  A function whose body is elsewhere could name
anything, so one of those keeps every variable — a guard rather than a mechanism, since none exists yet.

*Reporting.* A write that nothing ever reads back cannot affect what the program does, which is the rule that already catches an
unread value inside a function (4006), asked of a variable the whole program can name.  The difference is where it can be asked.
Inside a function the question is settled when the name goes out of reach; at the top level any function may name the variable, so
it is settled only once every function has been checked — in the whole-program phase that already existed for reporting a missing
startup function.  Diagnostic 4007, controllable as `unread-variable`.

Being written counts as naming a variable, so the two rules do not overlap: a variable the program only writes is kept and
reported, and one nothing names at all is dropped without a word.  Reporting the second would be reporting that something the
program cannot observe is not observed.

The part that needed care is the expectation.  The diagnostic engine keeps a *dynamic* stack, so whether `@[expect(4007)]`
suppresses anything depends on whether that expectation is in force at the moment of emission, not on where in the source the
diagnostic points.  The definition's expectation was being settled and discarded where the definition was read, which would have
made the attribute useless here and, worse, reported it as a stale assertion.  It is now carried on the variable and resumed around
the late emission — exactly what a local already does, and for exactly the same reason.  Both directions are tested: the attribute
must suppress the warning, and an assertion nothing meets must still be reported.

What other languages do.  C and C++ leave both to the optimizer and the linker and warn about neither at file scope; a `static`
nothing reads is at most a `-Wunused-variable` from some compilers.  Rust warns about an unused `static` and relies on the back end
to remove it.  Go refuses an unused *local* outright but says nothing about a package-level variable, since another file of the
package may use it — the reason PL4G can be stricter is the reason it can drop functions: compilation covers the whole program.

Deliberately not done: a variable nothing writes and nothing reads that is nevertheless kept because it is exported, and a variable
whose only writes come from a function that is later dropped.  The second is handled, since the analysis runs before the pass and
the pass then removes both; the first is correct as it stands.

## 2026-09-14T12:00+02:00 — language

**Truth values, digit separators, and the sign of a negative literal**

Three entries from the language list, two of which turned out to be already true of the compiler and to be missing only their tests
and their statement here.

*Truth values.* A `bool` has exactly two values, `true` and `false`, and nothing else is one.  A number is not a truth value spelled
differently: `let flag: bool = 1u8` does not compile, and neither does the reverse.  That is narrower than C, where any scalar is a
condition and `bool` is an integer type that holds 0 or 1, and narrower than Python, where everything has a truth value; it is the
rule of Go, Rust, Zig, Odin and Haskell.  It is the same rule that refuses to truncate a number, applied to a different type — a
program that wrote `1` and meant "true" reads as a program about a number.

Auditing this found a real defect.  A top-level literal the declared type had no use for fell through to "not implemented", so
`let n: u8 = true` reported a fatal internal error rather than the mismatch a variable inside a function reports for the same
mistake.  It said the compiler was unfinished where the program was simply wrong.  It is now 4203 either way, naming the literal's
own type where a suffix gives one and "integer" where the literal is untyped.  A type that was already reported says nothing more
about the value it was given, so one mistake is still one message.

*Digit separators.* Underscores may separate the digits anywhere, in every base, and are ignored rather than checked against any
grouping rule.  The specification already said so and the lexer already did it; what was missing was a test, which now covers the
four bases, repeated and trailing underscores, and the fact that the suffix still splits where it looks like it does.  Not
enforcing a rule is deliberate: the language is meant to be generated, and a generator emitting separators by whatever rule it
likes should always be writing the same number.  C++14, Rust, Ada, Java and Python all take this position; only Ada is strict about
placement.

*The sign of a negative literal.* A leading `⁻` (U+207B SUPERSCRIPT MINUS), with nothing between it and the digits, makes a literal
negative.  A space after the sign is an error (2008) rather than some other reading.

The reason for a glyph of its own is the reason APL has one.  Where `-` is both a sign and subtraction, `a -b`, `a - b` and `a-b`
have to be told apart by spacing or by a precedence rule the reader has to know, and a language meant to be generated should not
make its parser depend on whitespace.  Here `-` will only ever be subtraction and `⁻` only ever a sign, and neither question
arises.  APL writes `¯3`; the superscript minus is the same idea in a character that says "minus" outright.  C, C++, Rust, Go and
Zig all take the other road, which is why each of them has to explain that the most negative literal of a type is not a literal at
all but a negation — a wrinkle this arrangement does not have: `⁻128i8` is simply the smallest `i8`.

This also closes the separate entry observing that there was no way to write a negative number.  It and the sign were one question.

## 2026-09-14T13:00+02:00 — process

**The to-do lists gain a third state, and the four things nothing had written down**

Working through both lists in order made two things necessary.

*A third state.* An entry that cannot be started until something is decided is neither open nor done, and leaving it `[ ]`
alongside work that can simply be picked up hides which is which.  Entries are now `[ ]`, `[x]` or `[?]`, and a `[?]` carries a
`Question:` paragraph naming what is undecided, the choices, and what each costs.  Twelve language entries and two compiler entries
are marked, which is most of what is left of the language: the product, sum and member-function syntaxes; calls and control flow;
strings that can be resized; `@[required]`; the error path before `io_uring`; naming a symbol; whether an unread variable is an
error; patching a live binary; debug information; and the streams entry, which ends mid-sentence.

*Four things on neither list.* The operator half of the language list is unreachable without them, and none needs new syntax, so
none of them is a language question:

- a register allocator, without which `a + b` cannot be compiled at all, since every value goes to the one register a result is
  returned in and anything more is refused outright;
- conditional branches, of which there is not one row in any of the three instruction tables, although the representation has had
  branches and block parameters from the start;
- an expression parser with precedence, the current one being a four-arm match that consumes a single token;
- lowering `BinaryInst`, which no backend matches, so arithmetic in the representation reaches only the constant folder.

They are now entries, in that order, because that is the order they unblock each other in.  The allocator comes first for a second
reason: it introduces the first stack frame the compiler emits, and the unwinder needs one.

*The fault path.* Decided: an overflow aborts with a real multi-frame backtrace, so the compiler emits frame information and an
unwinder rather than a bare trap instruction.  All three backends already have a trap and could have used it in an afternoon; the
reason not to is that a trap tells the program's author where nothing and leaves them to reconstruct it from a core dump, which is
the sort of thing a language that refuses to truncate a number should not ask of anyone.  The same decision settles half of the
"error path before `io_uring`" entry, since the backtrace has to be written somewhere and the only thing available is a raw system
call; what that path may assume is what is left, and it is marked.

What other languages do about the ordering rather than the features: C and C++ push the whole question to the linker and the
debugger, and their unwinders are a separate library.  Go's runtime carries its own unwinder and prints a goroutine backtrace on a
fault, which is the behaviour this decision chooses.  Rust's panic path also unwinds, and its `-C panic=abort` is the trap-only
option being declined here.

## 2026-09-14T15:00+02:00 — implementation

**A register allocator, by linear scan, which does not spill**

Until now every value a function computed went into the register a result is returned in, and a function wanting two at once was
refused outright.  That made `a + b` impossible, so it blocked every operator the language list still asks for.

*Linear scan rather than colouring.*  Straight-line code has no interference graph worth building: a value is an interval on a
line, and two values conflict exactly when their intervals overlap.  Graph colouring pays for what control flow makes necessary,
and there is no control flow.  The comment where liveness is computed says what has to change when a branch backwards exists,
because that is the point at which the linear order stops being the order control takes.

*One instruction is two positions.*  It reads at the first and writes at the second.  Treating an instruction as a single point
would mean a value could never be moved into the register it is read from, and that case is not an edge case -- it is every
return.  With the two positions, a value hinted towards the register its result is returned in gets that register, the move becomes
a move of a register to itself, and it goes.  The generated code for the programs that already existed is byte-for-byte what it
was, which is the evidence that the hint is granted wherever it used to be taken for granted.

*The table row says what an instruction does with each operand.*  Only the allocator asks, but the answer is a property of the
instruction and not of the pass, so a row that says nothing writes its first operand and reads the rest -- the convention the
builder already spoke in -- and the sixteen rows that deviate say so.  A pass holding that list would be a list to forget.

*Which registers may be given out is the convention's to say.*  That is what a calling convention is about, and it also puts the
question in the one place a function that wants a bespoke convention could answer differently.  The order prefers the registers a
call would destroy, which costs a leaf function nothing.

*It does not spill.*  A function wanting more values at once than the target has registers is reported -- fourteen on x86-64,
twenty-eight on AArch64, twenty-six on RISC-V -- rather than compiled wrongly.  Spilling needs a stack frame, and the frame wants
designing once, together with the unwinder that also needs one and that the fault path has already been decided to use.  Writing
the frame twice to have spilling a little earlier would be the wrong trade.

What other languages do.  LLVM's fast allocator is what a build at no optimization gets and is close to this; its greedy allocator
splits live ranges and is what the rest gets.  Go's compiler also uses a linear-scan-shaped allocator rather than colouring, on
the grounds that compilation speed matters more than the last few percent, which is the same argument the requirement to be fast
makes here.  GCC uses integrated register allocation, a colouring allocator, and pays for it in compile time.

A consequence worth recording: the two fixed-width backends had each set aside two registers for the addresses and values a store
needs, because with no allocator there was nowhere else to put them.  Those are ordinary values now, which is why the generated
code for a store uses whatever is free rather than always the same two registers.

## 2026-09-14T17:00+02:00 — implementation

**A branch is selected together with its comparison, and turned round to fall through**

The representation has had branches and block parameters since the first version and no backend lowered one; there was not a
conditional branch in any of the three instruction tables.  That blocked short-circuit operators, saturated operations, overflow
traps and every form of control flow.

*The comparison and the branch are one call.*  That is the shape the hardware has.  RISC-V puts the comparison inside the branch
and has no condition codes at all; x86-64 and AArch64 set flags in the instruction before.  A selector handed the two separately
would have to remember the first in order to encode the second, and on RISC-V there would be nothing to remember -- so the call
that asks for a branch hands over both things being compared.  A condition that is a value rather than a comparison is branched on
by testing it against zero, which two of the three do in one instruction that needs no flags.

*Which way round is decided once, where the block order is known.*  A two-way branch is a conditional branch plus a jump, and the
jump is unnecessary when its target is the next block.  Inverting the condition is what makes that the case, so a branch with both
of its blocks after it costs one instruction instead of two.  That is the common shape and it belongs in the shared lowering, not
in three backends.

*Four of the ten orderings are missing on RISC-V.*  There is no "branch if less or equal": it is "branch if greater or equal" with
the operands the other way round.  So a condition knows how to be swapped as well as inverted, and the backend that needs it asks.
Both operations are involutions and both are tested as such, since a branch turned round wrongly is a program that quietly does the
opposite.

*A comparison feeding exactly one branch is folded into it.*  One whose result is wanted anywhere else would have to be computed
into a register -- `setcc`, `cset`, `slt` -- which is what producing a truth value means and is the business of the comparison
operators the language list still asks for.  Until then it is reported rather than got wrong.

What other languages do about the last point: LLVM keeps comparison and branch as separate instructions and relies on the backend
to fuse them, which needs a pattern matcher; Cranelift has `brif` taking the comparison directly, which is this arrangement.  For a
compiler whose stated priority is to be fast, having the shape be right at selection rather than recovered by matching is the
cheaper of the two.

The tests build the representation directly and take it through code generation and the image writer, because the language still
has no way to write a condition.  Every one of the ten orderings is compiled, run and checked on all three architectures, both ways
round -- forty programs a target -- along with a branch backwards, which is the case a fixup that only looked forwards would get
wrong.

## 2026-09-14T19:00+02:00 — implementation

**Expressions, by precedence climbing, with the bitwise operators as the first use**

The expression grammar was a four-arm match that consumed one token, and the syntax tree had no node with a child.  Nothing in the
language could be written that had a shape.

*Precedence climbing rather than a function per level.*  One function reads a level given as a number, so adding an operator is a
row in a table and not a new layer of the grammar.  The language list names four more families of operator; with a function per
level each would add two or three functions, and the last one added would be reading operands through a dozen calls.  The numbers
in the table are spaced by ten so a level can be inserted without renumbering.

*The tree names an operator, the representation names an operation.*  Two enumerations with a mapping between them, which looks
like duplication and is not: the front end is deliberately free of any knowledge of the representation -- it already carries a
literal's type as the text of its suffix for the same reason -- and several spellings may come to mean one operation later.

*The bitwise operators are the first use.*  They were chosen over arithmetic because arithmetic cannot be added yet: the language
requires an overflow check on every arithmetic operation and the fault path that check needs is the unwinder, which is not written.
Shipping `+` without the check would be shipping the wrong language.  Bitwise operations cannot overflow, so there is nothing
deferred about them.

*Both sides of an operator have one type, so either side may say what it is.*  A type hint is read off the syntax before anything
is lowered and used as the context for both sides.  Without it `count & 3` would compile and `3 & count` would not, which is a
difference with nothing behind it.  C avoids the question by promoting everything to `int`; this language refuses to widen
anything, so the only other answer would have been to require a suffix on every literal in an expression.

*A truth value is not a one-bit integer.*  `bool` has two values and no representation the language promises.  Go and Rust draw the
same line; C, where `&` on two conditions is legal and usually a mistake, is the example not followed.

*The relative binding is C's.*  `&` tighter than `^` tighter than `|`, which Rust, Go and Zig all kept, so a reader coming from any
of them reads these the same way.  What is *not* inherited is C's famous defect of making them bind looser than comparison:
comparison will bind looser than these when it arrives, so `a & b = c` will read the way it looks.

Two things fell out of the work.  Constant folding ran one walk over a function and so collapsed one level of an expression tree;
it now runs until nothing more folds, which nested expressions made visible immediately.  And a truth value used where a number was
wanted reported twice, because the literal reported the mismatch and then handed back a truth value for whatever read it to report
again; it now hands back the poison type, which is what everything else does.

An operand no row of a table can carry is put in a register, and which rows can carry what is asked of the table rather than
written down a second time.  x86-64 has a form of `and` that takes an immediate; AArch64's equivalent needs the bitmask encoding,
which is not generated.  The selector tries the operands as they stand and materializes whatever was refused, so adding a row that
carries an immediate is all it takes for one to be used.

## 2026-09-14T20:30+02:00 — implementation

**The generated identifier module refuses a number no block covers**

The blocks of diagnostic numbers are far apart on purpose, and the generated `pypl4g/diag/ids.py` puts the names under a heading
for each.  A number falling in a gap got no heading *and* left the previous one standing, so it was written out under the heading
of the block before it -- reading as a member of a family it is not in.  Nothing in the generator noticed; the only thing
preventing it was a test in the catalog's own test module, which is the wrong place for it: the generator is what produces the
misleading file, so the generator is what should refuse.

It now names the diagnostic and says what to do about it.  The test stays, as a second line rather than the only one.

The same shape of problem elsewhere: the table of blocks in this document is written out by hand and nothing compared it with the
catalog, so a block added to one and not the other would have left the document quietly wrong.  A test now compares them.

Both are the same rule -- a fact stated twice is a fact that can disagree with itself -- and the answer in both cases is to check
rather than to remember.

## 2026-09-14T21:30+02:00 — implementation

**What the compiler leaves out is recorded in the decision log, which until now recorded nothing**

`--decision-log` has existed since the first version, is documented as an option, and wrote `"decisions": []` every time; its own
docstring said the compiler made no recorded decisions yet.  Meanwhile two passes had been added that silently leave things out of
the binary.  A reader asking "I wrote that function, where is it?" had nowhere to look.

A decision is not a diagnostic, and the two are kept apart on purpose.  Nothing is *wrong* when a function nothing can reach is
dropped, so reporting it as a warning would be reporting a mistake that was not made.  A language meant to be generated will have
these by the dozen -- a generator emitting from a template routinely produces more than any one instantiation uses -- so as
warnings they would be noise, and as noise they would be turned off, and then the one that mattered would be missed too.

Each entry has a `kind` that is stable and a `reason` that is not.  Something reading the log asks which functions were dropped by
matching `drop-function`, never by matching prose; the prose is for a person and can be reworded without breaking anything.  Where
the subject is written in a file the entry says which file and line, since being told that a function went without being told which
one leaves the reader to find it.

Recording happens whether or not the log was asked for.  The alternative -- record only when `--decision-log` is given -- saves a
list append per dropped definition and makes the recording untestable, because a test would have to run the compiler as a process
to see anything.

The log travels on the module, since every stage has the module and any stage may decide something.  Instruction selection choosing
the shortest encoding, the allocator choosing a register, a later pass choosing to inline: all of them have the module, and none of
them needs a new channel.

What other compilers do: GCC's `-fopt-info` and Clang's `-Rpass` report optimizations as *remarks*, which is the arrangement being
avoided here -- they are diagnostics, they are off by default, and they are prose.  Clang's `-fsave-optimization-record` writes
structured YAML, which is this idea; the difference is that it is a special mode rather than the way the compiler says what it did.

A local that the dead-code sweep removes is recorded too, which needed the name of the local to survive into the representation.
It did not: a value carried a name hint from the first version, the printer rendered it, and nothing ever set one.  The semantic
analysis now writes the name of a local on the instruction its definition produced -- only on an instruction, and only where there
is no name already, since a constant is interned and naming one would put that name on every other use of the same number.  The
textual form became readable against its source as a side effect, which is what the hint was for in the first place.

A value with no name is an intermediate of an expression and its going is not recorded: a log that reported every folded
subexpression would be a log nobody reads.  A local bound to a constant is not recorded either, because nothing drops it -- a local
is a value, so one whose initializer is a constant never becomes an instruction.  Where such a local went is a question for the
debug information, which is where a constant expression standing for a name belongs, and which is still an open entry.

The warning that nothing reads a value and the record that it is therefore not in the binary are different facts, and keeping both
is deliberate.  One is a possible mistake, reported at every optimization level; the other is what became of it, and only where
something was optimized.  They come apart in both directions: at `-O0` there is a warning and no decision, and a local that *is*
read can still go when the thing that read it went, which is a decision with no warning.

## 2026-09-14T23:00+02:00 — implementation

**The log says where it was made, a grammar says what a program looks like, and a program shows the two together**

*Where the log was made.*  A decision names a file and a line, and a file named by a path that is relative is only findable from
the directory the compiler ran in.  The log now records that directory, and every path in it is relative to that -- which is
exactly what DWARF does, where a compilation unit carries the directory it was compiled in beside the name it was compiled from.
A project directory was considered instead and rejected: there is no marker that says where a project begins, so it would have had
to be guessed, and a guess in a machine-readable record is worse than a fact.  The format version went to 2.

*A grammar in a second language.*  `tree-sitter-pl4g` is what an editor reads.  It is a second statement of what a program is, and
the risk with any second statement is that it drifts from the first.  So a test requires the two to agree on every program in the
language test suite -- both ways round, since a grammar that is too loose is as wrong as one that is too strict and shows up as an
editor offering to complete what cannot be written.  All ninety-one agree, and the deliberately malformed programs are refused by
both.

Writing it turned up one thing worth recording: the accepted ASCII substitute `->` had to be in the grammar, because a grammar that
refused what the compiler accepts would disagree about what a program is.  The literal suffix went the other way -- the compiler's
lexer reads any identifier there and then reports one that is not a type, but it reports it as an *error*, so a literal with a wrong
suffix is not a program either way and the grammar lists only the real type names.  That list grows as the language gains types.

The layout rules cannot be expressed in a context-free grammar, so an external scanner gives out the newline, indent and dedent.
The one subtlety was that these are markers with no text: the whitespace before them is skipped rather than consumed, and the
column is read from the lexer rather than counted, so that the newline ending a statement and the indent opening a block can both
stand at one place.  Counting it by hand meant the indent had nothing left to measure, which is the bug that took the longest to
see.

*Showing them together.*  `bin/pl4g-decisions` puts each record back under the line it is about.  The log on its own is a list of
names and line numbers, which is not something anyone reads; the question a decision answers is "I wrote that, where did it go?",
and that is answered by looking at the place it was written.

It highlights with the grammar's own queries rather than with a set of its own, so that what it colours and what an editor colours
cannot come apart.  It colours a terminal and not a pipe, which is the convention every tool that does this has settled on, and
`--color` overrides either way.  Everything the highlighting needs may be missing on a machine that only wants to read a log, and
none of it being there is not an error -- the source is shown plain, which is what a pipe gets anyway.

## 2026-09-15T01:00+02:00 — implementation

**Spilling, by rewriting and starting again, and the first stack frame**

The allocator refused a function wanting more values at once than the target had registers -- fourteen on x86-64.  Honest, and a
hard limit on any program with a real expression in it.

*Spill the whole life, not part of it.*  A spilled value is in memory from the moment it is computed; what occupies a register is a
fresh one per instruction, live for the single instruction that reads or writes it, so there is always somewhere to put it.
Splitting a range -- in a register where the value is busy, in memory where it is not -- generates better code and is a great deal
more machinery.  This is the version that is obviously right, and the loop it is written as is where the better version goes.

*Rewrite and start again, rather than patch.*  A spill adds instructions, which moves every position after it and so changes every
range.  Recomputing all of it is simpler than repairing it, and a function is small.  Each round spills at least one value and a
value once spilled needs no register across its life, so the rounds run out; the cap is the number of registers.

*The value given up is the one whose range reaches furthest*, since that is the one that would hold a register longest.  That is
the heuristic linear scan was first described with, and it is still right for straight-line code.

*The frame is made after allocation and only where a slot was taken.*  Only the allocator puts anything on the stack, so nothing
else has an opinion, and a function that needed none has no frame and no instruction saying so.  The room is given back before
every return rather than at one place, because there is no one place -- a function may leave from more than one, and control that
left without giving it back would return to a caller whose stack had moved.

The x86-64 red zone was considered for this and not used.  A leaf function there may write 128 bytes below the stack pointer with
no prologue at all, which the recorded convention already says; it would have made the common case free on one architecture and
changed nothing on the other two, and it stops being available the moment a function calls anything.  Emitting the frame everywhere
is the same code on all three and stays right when calls arrive.

*A register may say it must not be spilled.*  RISC-V builds an address with two instructions of which the second measures from the
first, so they must stay adjacent -- and spilling the register held between them puts a load and a store between them.  That was a
real miscompilation, found by running a program with forty live values and getting sixteen back instead of one.  The fix is two
things: the address gets a register of its own rather than the destination's, so the long-lived value is not what is being held
across the pair; and that register says it may not be spilled.  Using the destination was a deliberate economy from before there
was an allocator, and it also made the loaded value live three instructions earlier than it needed to.  On AArch64 the two halves
of the pair are independent and no such rule is needed.

What other compilers do: LLVM's fast allocator spills whole values like this; its greedy allocator splits.  GCC's is a colouring
allocator with its own spill heuristics.  The rewrite-and-retry loop is how LLVM's linear scan worked and how Cranelift's
backtracking allocator still works, for the same reason -- a spill changes the problem, so solving the new problem beats fixing up
the old answer.

Verified by running programs with twenty, forty, eighty and a hundred and fifty values live at once on all three architectures,
each returning the first value read, which can only be right if it came back from the frame unchanged.

## 2026-09-15T03:00+02:00 — implementation

**A spilled value is read from the frame once for as many instructions running as read it, and no further**

The entry asked to split a live range rather than spill a value for its whole life.  Before writing anything, the question was how
much there was to gain, and the answer decided the shape of the change.

Counting reloads per slot in the code the compiler generates: on a program with thirty-two values live at once, every slot is read
exactly once -- there is nothing to merge, because every value is defined once and used once.  On a program written to make a value
be read several times over, one slot was read five times.  So the gain is real but it is exactly the gain from *repeated* reads,
and nothing else.

Two measurements then decided where to stop.  Merging the reads of a value that is read by consecutive instructions: two
instructions fewer on AArch64, two fewer on RISC-V, one *more* on x86-64.  The one more is the whole argument in miniature -- the
register held across the reads is a register some other value cannot have, so holding it caused a spill elsewhere; x86-64 feels it
because its two-address form puts a move between the two reads, so only one merge fires and the cost is not paid back.

Going further -- keeping a value in a register across instructions that do not read it -- is where a cost model is needed, and
there is nothing to base one on.  The question is whether a load that runs once is worth a register held for ten instructions, and
the answer depends on how often the code runs, which is not knowable until there are loops.  So that is a separate entry, waiting
for the thing that would make it answerable, rather than a guess written down as a constant.

A third measurement fell out of it and is worth recording: the values with clustered reads are, in the programs written so far,
precisely the ones *not* spilled, because a value read several times close together has a short range and the allocator gives up
the value whose range reaches furthest.  A first attempt at measuring this change showed no effect at all for that reason, and the
programme had to be rewritten so that the value read repeatedly was also the one read last.

What other compilers do: LLVM's greedy allocator splits ranges properly and pays for it with a cost model built on block
frequencies, which come from the loop structure.  Its fast allocator does what this now does -- reuse within a run, nothing beyond.
That the two sit side by side in one compiler is the evidence that the second is not worth having until the information the first
needs exists.

## 2026-09-15T06:00+02:00 — language and implementation

**Modules: read while compiling, read once, and named by the shortest way of reaching them**

A module is a source file, brought in by `let name := ⎕import("somename")` and named through afterwards as `name.thing`.  Several
decisions are worth stating.

*A module is not a value.*  It is read while the program is being compiled, and nothing of it survives into the program but the
definitions it holds, so there is nothing to give a type to, nothing to change, and nothing to compute with.  The syntax is a
definition because binding a name is what it does, but the node is its own rather than a variable whose value happens to be a
module -- writing it as a variable would have meant explaining ever afterwards why that one variable cannot be used as one.  It
cannot stand inside a function for the same reason: what it holds belongs to the program, not to one call.

*The top-level namespace is a file's, not the compilation's.*  This was the largest change underneath, and it is what makes
modules mean anything: two files may each define a `counter`, and neither sees the other's.  The representation holds both, so
what a definition is filed under says which file it came from while the name stays what the source wrote.  Nothing outside the
semantic analysis had to learn about that, because everything else walks the definitions rather than looking one up by name.

*A module's name is settled after all the reading, not during it.*  Neither question can be answered earlier.  The shortest of a
module's names is not known until the last route to it is found, and whether two modules share a base name is not known until both
have been read.  So loading records every name a module could go by and a pass at the end chooses: the shortest, and the one
sorting first where two are the same length, because a program should not be made to carry the longest way of reaching something.

*Two files of one name both get a hash.*  Not the first, not the second: neither has a better claim to the name, and giving it to
whichever was read first would make the symbols in a binary depend on the order the imports were written in.  The hash is of the
path and not the contents, because two files with the same contents are still two modules and a file that changes is still the
same module.

*A ring is an error rather than something to resolve.*  A module is read while the file importing it is being read, so a ring has
no beginning -- neither module can be finished before the other.  The message names the ring, since the first question anyone asks
of a cycle is which files are in it.  Python resolves this with partially-initialized modules and Go forbids it outright; forbidden
is the answer here, because the alternative is a module that can see half of another depending on where the reading got to.

*Where to look, and in what order.*  The directory of the importing file first, which is what makes a directory of sources work
with nothing configured; then what the build says; then what the installation provides -- and the last only for a name with no
slash, since a name with a slash is a path and a path is not something to go looking for somewhere the program knows nothing
about.  Compare Go, where the build system resolves an import path and the file's own directory means nothing, and C, where the
including file's directory is first for `"..."` and not for `<...>`.  This is C's arrangement for a language that has only one
kind of name.

One thing the work uncovered rather than decided: the whole-program questions -- whether there is a startup function, whether
anything reads a variable -- were being asked by whichever checker finished first.  A module read before the main file answered
both wrongly.  They are asked once now, by the outermost checker, over every file.

Left as it was: `@[export]` still means both "visible to a file that imports this" and "visible outside the program", so a module's
exported definition is a root of the reachability pass and is kept whether or not anything imports it.  For a library module that
keeps more than it needs.  Whether the two should be separate attributes is a language question and is on the list.

## 2026-09-15T08:00+02:00 — language

**`export` and `visible` are two attributes, because they were always two questions**

Decided on the user's direction, answering the question the module system left open.  `export` says what a file importing this
module may name; `visible` says whether the finished image offers the symbol.  Neither implies the other and either may be said
alone.

They were one attribute because until modules existed there was only one way out of a program, so one word covered it.  Modules
made the second way, and the two then pulled against each other: a definition a module lends is reachable from outside the
compilation, so it has to be a root of the reachability pass, so it cannot be left out of the image -- and a library module
therefore carried everything it defined whether the program used it or not.  Splitting them removes that entirely: a definition a
module lends and nothing imports is a definition nothing reaches, and it goes.  That is visible in the test that used to keep an
unused exported function and now does not.

The linkage in the representation was renamed with the meaning it now has: `internal`, `visible`, `imported`.  Calling it
`exported` while `export` meant something else would have been a word doing two jobs in the one place where the difference is
about to matter most.  What a module lends is a flag of its own on the definition, since it is a fact about the definition and not
about the image.

Either attribute applies to a function and to a variable alike, and they are not in a group: saying both is saying both.

What other languages do.  Rust draws this same line -- `pub` for what a module lends, and something else again for what a binary
offers.  Go has one rule for the first, the initial capital, and leaves the second to the linker, which is why a Go program cannot
say it wants a symbol in its own binary's table.  C has `static` for neither and nothing at all for the difference between the
other two, which is how a name never meant for an interface becomes one.  The arrangement here is Rust's, said as two attributes
rather than as a keyword and an attribute.

One thing worth noticing rather than deciding: in the file named on the command line, `export` now does nothing observable --
nothing imports the main file.  It is not an error, because the main file is a module like any other and may be imported by
something later; but a warning for an attribute that cannot have an effect where it stands may be worth having, and that is a
question for the list rather than for here.

## 2026-09-13T11:20+02:00 — language

**One meaning, one spelling: one attribute list per definition, and no empty argument parentheses**

Decided on the user's direction.  Two rules, and one principle underneath them that the specification now states outright: where
two ways of writing something would mean the same thing in every respect, the language admits one of them.

The first rule.  Everything said about one definition is written in one list.  `@[export]` on one line and `@[visible]` on the
next is refused (3208) and `@[export, visible]` is how it is written.  A blank line or a comment between the two lists changes
nothing, because it changes nothing about what they attach to — both still attach to the definition that follows, so both are
still two lists before one definition.  The check is in the parser, which counts the lists it reads before one thing; the second
and every later one is reported, and the report points at the list that has to go rather than at the definition.

The second rule.  The parentheses are how an attribute carries arguments, so an attribute carrying none is written without them:
`@[export]`, not `@[export()]` (3209).  This matters most for an attribute that *could* have taken arguments and was given none
— `@[inline()]` parses today and means `@[inline]` — since that is exactly where both spellings would otherwise be available.
For the same reason a list holds at least one attribute; `@[]` says what writing nothing says, and the parser already refused it.

Why, rather than merely tidiness.  A second spelling is paid for by everyone downstream of the language and by nobody in it: a
person reading a program has to know both shapes mean one thing, an editor and a formatter have to match both, a tool that
compares two sources has to normalize before it can compare, and the machine generating this language — which is what it is for
— has to be told which of the two to emit, for no reason it could derive.  The generator is the argument that makes this
different from a human-facing language: choice in the notation is a burden on a program, where for a person it is sometimes a
convenience.

What was deliberately left alone.  The rule is only about shapes that mean the same thing in every respect, so it does not touch:
the ASCII substitutes `->` for `→`, which exist for input methods and already draw a diagnostic of their own (2005); the brace
notation beside the layout notation, which exists because a generator should not have to count spaces and which cannot be mixed
within one block (2102); a type written out where it could have been taken from the value, which says the type whatever the value
later becomes; an attribute parameter given by name where it could have been given by position, which is the same generalization
a call has; and `ignore` and `expect` being repeatable, since each names one diagnostic and a construct may raise several — they
repeat within the one list, which is where the first rule puts them.

What other languages do.  Rust allows any number of `#[...]` attributes in a row and `#[a] #[b]` is identical to `#[a, b]`; both
are written in practice and `rustfmt` does not unify them, so the two shapes are permanently in the language.  C++ allows
`[[a]] [[b]]` beside `[[a, b]]`, and additionally `__attribute__` and `_Pragma` as further spellings of neighbouring things.  Go
takes the opposite position throughout — one way to write a thing, enforced by `gofmt` being the formatting rather than a
formatting — and this is the same position taken in the grammar rather than in a tool, because a grammar that admits only one
shape needs no tool to normalize it.  Python's decorators stack by design and each is an application, so the question does not
arise there in the same form.

The tree-sitter grammar was tightened in step, and the test that checks the grammar and the compiler agree about every language
test is what holds the two together: a shape one of them refuses and the other accepts fails there.

---

## 2026-09-13T14:05+02:00 — compiler

**A truth value in a register is one or zero, and a byte in memory**

Decided on the user's direction, and mostly not a decision at all: it is what the instructions produce.  A comparison whose answer
is wanted as a value -- rather than as a place to go, which is the case that already worked -- is now computed, which is what every
comparison operator and every binary logic operator the language is about to gain will use.

The three architectures diverge further here than they do on branches, which is why the shared part is only *which* condition and
*what* stands in it, and the rest is per target.  x86-64 reads the flags into a byte with `setcc` and leaves the rest of the
register alone, so a widening move follows; the usual trick of clearing the register first is not open, because clearing it writes
the flags the comparison just set.  AArch64 reads them into a word with `cset`, which clears the rest of the register itself.
RISC-V has no flags at all: it has one comparison, "set if less than", signed and unsigned, and the other six orderings are that
one with the operands exchanged, its answer inverted with an exclusive or, or both -- equality having no ordering in it is a
subtraction and then a question about the difference.

The representation follows from that rather than being chosen: every one of those instructions writes one or zero.  Nothing was
free to decide, which is the best kind of answer to this sort of question.  C says any nonzero value is true and `_Bool` normalizes
to 0/1 on assignment, which is two rules where one would do; Rust, Go and Zig all make the value 0/1 and nothing else, and this is
that.

A comparison is folded into a branch only where that branch is its **sole** reader and reads it as its condition.  Read once by
anything else -- a return, a store, one day a call -- it is a value something wants, and a value something wants has to be
somewhere.  Two branches cannot each absorb one comparison, so that case computes it once and both branches test the result
against zero, which two of the three architectures have a flagless instruction for.  The old rule was "read exactly once", which
was right only because a branch was the only thing that could read one.

**A latent miscompilation came out with it.**  Every backend answered "how wide is a value of this type" with the integer's width
or, failing that, a word -- and a `bool` fell into "failing that".  A one-byte variable was therefore read and written eight bytes
at a time, reaching seven bytes past itself into whatever was laid out next.  Nothing had caught it because nothing had yet been
laid out next to a `bool`.  The width now comes from the layout, which already said one byte, and the language test that shows it
puts a guard variable on each side.

What other languages do about the folding.  LLVM keeps `icmp` as a value and lets the backend fold it into a branch when it has a
single use in the same block; GCC keeps the comparison in the branch and splits it out when something else wants it.  The rule
here is LLVM's, said in the one place that can see both readers.

---

## 2026-09-13T16:30+02:00 — language

**The six comparisons: `=` `≠` `<` `>` `≤` `≥`, one precedence level, no chaining**

Implemented as the to-do list specifies them.  Four decisions were taken inside that, each with what else was possible.

**They bind looser than everything else.**  `a & b = c` is `(a & b) = c`.  C binds comparison looser than `&&` but *tighter* than
`&`, which is why `flags & MASK == MASK` is a famous bug there; Go, Rust, Zig and Python all corrected it, and this is that
correction.  Nothing was really open here -- the bitwise section of the specification had already promised this outcome in so many
words, before there were comparisons to give it to.

**All six share one level.**  C, C++ and Java put the orderings tighter than the equalities; Go and Rust put all six together.
Splitting them decides exactly one thing -- what `a < b = c` means -- and that expression is refused here, so the split would buy
nothing and would be one more table row for a reader to hold.

**They do not chain.**  `a < b < c` is an error (3014).  Three positions exist.  C and Go give it a meaning, `(a < b) < c`, which
compares a truth value with a number and is a mistake in every program that has ever written it.  Python gives it a third meaning,
the mathematical one, which reads beautifully and which no other language in this family has adopted.  Rust refuses it.  Refusing
is taken here for two reasons: a language meant to be generated gains nothing from a notation that saves a human keystrokes, and
refusing is the only one of the three that can be changed later without changing the meaning of a program that already exists.
Where the chain was meant, `(a < b) = ready` says the first and `∧` will say the third.

This is said in the tree-sitter grammar by the shape of the rules rather than by precedence numbers: a comparison's operands are
"any expression that is not a comparison", which is also what makes the comparisons bind loosest, since there is nowhere for a
bare comparison to appear except at the top of an expression or inside parentheses.  A shape cannot be read two ways, and the test
that the grammar and the compiler agree about every language test is what holds the two readings together.

**Equality on truth values, ordering not.**  Two truth values can be the same or different; neither comes before the other.
Rust and Haskell order `bool` (`false < true`), C orders it by way of the integer it secretly is, Go refuses.  Go's answer is
taken, and for Go's reason: the specification says outright that `bool` has no representation the language promises, so an
ordering would have to invent one.

**`=` compares, and a discarded comparison is refused.**  Assignment is `←` and has no other spelling, so `=` was free.
The diagnostic that used to catch `count = 1u8` (3008, a syntax error, because `=` had no meaning in an expression) is retired and
replaced by 5005, which reports a comparison whose answer is not used.  The move is not cosmetic: the program now *parses*, and
what is wrong with it is what it means, so the phase that knows whether a statement's value is wanted is the phase that has to
say.  It also catches `a < b` written as a statement, which the old check did not.  The last statement of a body is the body's
result, and a comparison there is the point of the line.

Retiring a number rather than reusing it leaves 3008 unused.  That is the right way round: a number is how a program names a
diagnostic in `@[expect]`, so a number that meant one thing must not come to mean another.

**What was left for later.**  Strings, once there are strings.  Floats, once there are floats -- with them comes the warning the
to-do list asks for, that `=` on floating point is an unsafe question, and the rule that an integer literal compared with a float
must be exactly representable in it.  Nothing here forecloses any of that.

A comparison of constants folds, and its answer needs no range check: there is no truth value that does not fit in a `bool`.

---

## 2026-09-13T18:15+02:00 — language

**A statement that is an expression must have its value used, and `≠` gets no ASCII substitute**

Two things the user settled, one of which generalized a diagnostic that had just been written.

**No ASCII substitute for `≠`.**  Decided by the user.  `!=`, `/=` (Haskell, Ada) and `<>` (Pascal, SQL, BASIC) all pass the two
rules a substitute has to pass -- more than one character, ambiguous with nothing -- so the question was which of three to bless,
and the answer is none.  What `≤` and `≥` have that these do not is that `<=` and `>=` are already what every keyboard and
every reader produces for those two; there is no such agreed spelling for `≠`, so a substitute would be a second way to write it
rather than the way it is written.

**A statement whose value is not used is an error.**  5005 was written as a rule about comparisons -- `count = 1u8`, the mistake
someone makes who has spent years in a language where `=` assigns -- and the user generalized it to every expression: a bare
literal, a bare name, anything computed.  It is right, and for a reason stronger than tidiness.  Every expression the language has
computes a value and does nothing else, so a statement that is only an expression does nothing unless something takes the value,
and something takes it in exactly one place: the last statement of a body is the body's result.

An error rather than a warning, because this language is emitted by a program.  A human writing a line with no effect has made a
slip; a generator emitting one has a defect, and a warning is how a defect gets into a program nobody reads the warnings of.  Go
refuses a discarded expression outright for the weaker version of this reason -- that such a line is almost always a mistake -- and
admits a handful of statement forms instead.  C, C++ and Java allow it and warn about parts of it, which is `-Wunused-value`.
Rust warns and has `#[must_use]` for what it cannot warn about in general.  Go's position, with Go's list of exceptions left for
when there is anything to put on it: a call will be the first expression that does something besides produce a value.

The comparison case keeps what it had, as a note (5006) attached to the general error rather than a diagnostic of its own.  What
is wrong with the line is that the value goes nowhere; that `=` compares and `←` assigns is why the line was probably written,
which is exactly what a note is for.

**A new kind of error, and a field in the catalog to say so.**  The rule collided with an existing one: quieting an error with
`@[ignore]` discards the construct it is attached to, because an error usually means what was read could not be built and half of
something is worse than none.  But the user's requirement is that `@[ignore(5005)]` *accepts* the line -- and the construct here is
whole; the compiler could compile it and is refusing on a rule about how code is written.

So diagnostics now say which kind they are: `well_formed` in the shared catalog marks an error the construct is still well formed
despite.  Quieting one of those leaves it in the program; quieting any other error still discards it.  Putting it in the catalog
rather than in the compiler is what makes it a fact about the language: every implementation draws the line in the same place, and
a new diagnostic has to answer the question rather than inherit an answer.  Without the distinction the attribute would be a way to
delete code, which is the opposite of what someone writing `@[ignore]` on a line they mean to keep is asking for.

Rust has the same split without naming it -- `#[allow(unused_must_use)]` keeps the statement, while an error about a type does not
become ignorable at all -- because there the two are different mechanisms.  Here there is one mechanism, so the catalog has to
carry the difference.

---

## 2026-09-13T20:45+02:00 — language and compiler

**The logical operators, and the block parameters `and` and `or` brought with them**

Implemented as the to-do list specifies them: `∧` `∨` `⊕` `⊼` `⊽` `¬`, which always compute both operands, and `and` and
`or`, which do not.  Four decisions inside that, and one piece of compiler machinery that had to be built to keep a promise.

**Where they bind.**  Looser than everything, because what they join is whole questions: `a < b ∧ c < d` reads as it looks.  Among
themselves they take the order of the three bitwise operators they mirror -- `∧` tighter than `⊕` tighter than `∨` -- so that one
set of habits serves for both.  That is not what C does, where `&&` and `||` are two levels with nothing between and there is no
logical exclusive or at all; taking the bitwise shape gives the third operator a place to go.

**`⊼` and `⊽` do not associate.**  Neither is an associative operation, so `a ⊼ b ⊼ c` is two different questions depending on how
it is grouped.  The same answer as for the comparisons, for the same reason, and now sharing one diagnostic: 3014 was named for
comparisons and is now `LANG_SYNTAX_NOT_ASSOCIATIVE`, which is the rule both cases are instances of.  APL has these glyphs and
gives them its uniform right-to-left grouping; no language in this family has them at all.

**No ASCII substitutes.**  The candidates would be `&&`, `||` and `!`, and spelling the logical operators with the characters the
bitwise ones use is the single confusion this language exists not to have.  `&` and `∧` being different operators on different
types is the whole point, and `&&` would put them one character apart.

**Words for the two that short-circuit.**  `and` and `or` differ from `∧` and `∨` in *when* they evaluate, which is not a thing a
glyph can show.  Worth saying plainly: today the difference is unobservable, since no expression in the language has an effect, can
fail, or can fail to finish.  They are separate now so that a program written today says which it meant, and so that the day an
expression can have an effect is not the day every program has to be read again.  Python and Ada use words for these; C, Go and
Rust use punctuation and have no non-short-circuiting form at all, which is the thing that cannot be said in those languages.

**How they are built.**  A truth value is one or zero, so "both are true" is the two anded together -- the same instruction the
bitwise operators use, asked of values that have only one bit's worth of meaning.  That is what LLVM does with `i1` and it needs no
new instruction.  `⊼` and `⊽` are those two with the answer turned round, and turning a truth value round is an exclusive or with
one and *not* a complement: complementing one sets every bit above the lowest and gives something that is neither true nor false.

**The machinery: block parameters reach the backends.**  `and` and `or` are the first thing in the language that makes a branch,
and the value they produce arrives from two different blocks -- which is a block parameter, which no backend could lower.  So:
every block parameter is given a register before any block is walked, and a branch's arguments become moves immediately before the
jump.  The register allocator's existing hint usually makes those moves vanish by giving the parameter and the value that reaches
it the same register, which is visible in the generated code -- the short-circuit shape compiles to a compare, a branch, and no
moves at all on any of the three targets.

Only an *unconditional* branch may carry arguments, and that is a real restriction rather than an oversight: an edge of a
conditional branch has nowhere to put the moves, and splitting the edge is what carrying them there would need.  The lowering is
shaped so the question does not arise -- the conditional branch goes to two blocks that carry nothing, and each hands the answer
over with a branch of its own.  The unsupported shapes are refused rather than got wrong.  When `if` arrives it will produce the
same shape; when something produces the other one, edge splitting is what it needs and the refusal says so.

**One optimizer addition, which the shape asked for.**  A branch on a condition the folder has already settled is now replaced by
the jump it would have taken, and the block it would not have gone to is then pruned as unreachable.  Without it `true and false`
compiled to a branch on a constant.  With it, the machine code for a short circuit whose answer is known is the answer and nothing
else.  What is *not* done is merging the blocks that remain: the code is already what it should be, since the blocks fall through,
and merging is a larger change that `if` is the right occasion for.

**A small correctness fix underneath.**  `Function.add_block` now makes a label unique if something already has it.  Two `and`
expressions in one function asked for two blocks called `rest`, and the verifier caught it -- which is the verifier doing its job,
but the fix belongs where the label is made, since a block is found by the object and never by its label.

---

## 2026-09-13T22:30+02:00 — compiler

**A wide constant is built, not loaded from a pool; and two miscompilations that came out with it**

Done first of the four the user asked for, because the two after it need it: the bounds a saturating operation clamps to are the
ends of the type, and for a sixty-four bit type those are constants no instruction can carry.

**Built rather than pooled.**  A constant pool costs a relocation, a section and a cache line that a program pays for whether it
reads the constant or not; a sequence costs two to four instructions that nothing else waits for.  Every compiler for these
architectures makes the same choice for the same reason, and the sequences here are theirs: AArch64 sets a quarter of a word at a
time with `movz` and `movk`, with `movn` first where the value has more quarters of ones than of zeroes -- which makes -1 one
instruction rather than four.  RISC-V follows LLVM's sequence exactly, and deliberately: it is what the disassembly of every other
RISC-V program looks like, so a reader comparing the two is comparing like with like.  x86-64 already had a move that takes eight
bytes; only a store and a comparison, whose immediates are narrower, needed a register.

**Two real defects came out of testing it.**  Both were silent wrong answers, and both had gone unnoticed because nothing had yet
used a wide constant.

The first: x86-64 chose an immediate's width by what the number needs, ignoring that an instruction sign-extends one narrower than
the operation.  `0xFFFFFFFF` compared against a sixty-four bit value was encoded as four bytes and read as minus one.  The rule is
now stated where the width is chosen -- narrower than the operation means sign-extended, so only a value that reads the same as a
signed number of that width may go there; at the operation's own width nothing is extended and any pattern will do.

The second: RISC-V was handed the unsigned reading of a pattern with its top bit set -- 2^64-1 rather than -1 -- and computed a
shift of sixty-four.  The two readings are now made one where the sequence begins.

**What made the test worth trusting.**  The program compares a constant it *built* against the same constant as the image writer
*wrote* into memory, and exits with whether they agree.  Two separate paths from one number, with nowhere for both to be wrong in
the same way.  Sixteen values, three architectures, run under emulation.

**The header flag word** now comes from the target rather than being zero by omission.  Only RISC-V has anything to say in it, and
zero there is not "unset": it says the base integer set and the soft-float convention, which is what is emitted.  The three
floating-point values are named beside it so that choosing one later is a constant and not a change to the image writer.

**One entry closed as already done.**  Materializing the address of a symbol on RISC-V was written up as missing; the work that
made loads and stores reach a variable had done it, and the entry had gone stale.  Worth noticing as a process point: an entry
that describes what is missing decays, where one that describes what is wanted does not.

---

## 2026-09-14T02:10+02:00 — language and compiler

**Saturating arithmetic: `⊞` `⊟` `⊠`, and the three shapes a saturating operation takes**

The first arithmetic the language has.  These are the operations for which going past the end is the intended answer, which is
why they come before `+`, `-` and `×`: those fault, and faulting needs an unwinder that does not exist yet.

**The notation.**  Each is the sign of the operation it is built from, in a box, the box saying that the answer stays inside
something.  Zig has `+|`, `-|` and `*|`; Rust has `saturating_add` and its relatives as methods and no operator at all; C and Go
have neither.  Zig's position is taken -- that these are common enough to deserve a notation -- with glyphs rather than
punctuation pairs, for the reason every glyph here is a glyph.  Multiplication binds tighter than addition and all three bind
tighter than the bitwise operators, which is C's order and the one place C's order of operations was not a mistake.

**How it is done, which turned on a question the compiler had not had to answer before.**  Saturating means computing the answer
and bringing it back to the ends of the type, and computing it means knowing what an operation on a value narrower than its
register produces.  That question -- what a narrow value looks like in a wide register -- had never had to be settled, because
nothing had ever read one at a different width from the one it was computed at.

The answer taken is the one the compiler already half had: **a value is correct in the register it is held in, and says nothing
about what is above that.**  A `u8` lives in a thirty-two bit register on two of the targets and a sixty-four bit one on the
third, correct to that width.  It is not "always sign-extended to sixty-four bits", which was the other candidate: that one would
make every narrow operation cost an extension, where this one costs nothing and needs a widening only where an operation is
computed at a width other than its own.

From that the three shapes follow.  A type **narrower than its register** is computed as it stands and the answer is exact, two
values under 2^31 being unable to make a sum, a difference or a product a register cannot hold; so saturating it is two
comparisons.  A type **as wide as its register but not as the widest** -- a thirty-two bit type on x86-64 and AArch64 -- has its
operands widened into whole registers first, one instruction each and none at all on RISC-V, and is then the first case.  A type
**as wide as the widest register** has nowhere wider to go, so it is allowed to wrap and the wrapped answer is asked what
happened: a sum below what it was given has carried, a difference asked of too small a number had the smaller on the left, and a
sum of two numbers of one sign that comes out with the other has gone past that end.

**No branch anywhere.**  The clamp is a conditional move on x86-64 and AArch64 and, on RISC-V which has neither, the answer built
as a number and used as a mask: one or zero, taken from zero to give all ones or none, and the difference between the two values
let through it.  Five instructions rather than one, and no branch, which is worth more than the four instructions.

**One case refused rather than guessed.**  A saturating multiplication of `u64` or `i64` needs the upper half of the product.
AArch64 has `umulh`/`smulh` and RISC-V `mulhu`/`mulh`, each one instruction; x86-64 has it only in the form that writes a fixed
pair of registers, which the allocator cannot yet be told about.  It is refused on **every** target rather than on the one that
cannot do it, so that a program means the same thing wherever it is compiled -- and the to-do entry names what it waits on, which
division will want as well.

**Three defects found by the tests.**  All three were silent wrong answers, and all three needed a value to be read at a width
other than the one it was written at, which nothing had done before.  An immediate narrower than the register it was moved into
was sign-extended, so `0xFFFFFFFF` became minus one.  A constant put in a register to stand beside an operation's other operands
was given thirty-two bits whatever they had.  And a comparison whose constant had to be carried in a register compared at
sixty-four bits, which is wrong for a value that is only correct at thirty-two.

Every case in the table is compiled and run: the program computes the operation, compares the answer with the one written down,
and exits with whether they agree.  Fifty-one cases, three architectures.

---

## 2026-09-14T05:30+02:00 — language and compiler

**Arithmetic that checks: `+`, `-`, `×`, and the path a fault leaves the program through**

The two remaining steps of the four asked for, and they turned out to be one thing: arithmetic that faults is only as good as what
happens when it does.

**An answer that will not fit stops the program.**  `200u8 + 100u8` does not continue with 44 and does not continue with 255.
C leaves signed overflow undefined and wraps unsigned, which is why `-ftrapv` and the sanitizers exist; Rust panics in a debug
build and wraps in a release one, so a program means two things depending on how it was built; Zig and Swift fault in both and
offer `+%` and `&+` for wrapping.  Zig's and Swift's position is taken, with `⊞` and its relatives in place of `+%` -- and with
no wrapping operator at all, since wrapping is a thing to ask for by writing the wrap rather than by writing an operator that
hides it.

The checks are the same three shapes the saturating operations use.  That is not a coincidence worth congratulating: finding that
an answer went past the end of its type is one question, and whether the end is then used in its place or the program stops is the
only difference.  One module answers both.

**`×` and `÷` are glyphs; `+` and `-` are not.**  A single ASCII character spent on an operator is one no future feature can
have, and these two are what the operations are written with outside programming.  `+` and `-` keep their characters because
nothing else could reasonably want them -- and `-` is unambiguous only because the sign of a negative literal is `⁻`, which is what
that glyph was for.  The decision made months ago pays here.

**The fault path.**  Everything that could be worked out beforehand was: the message is built whole at compile time and put in the
image, so what runs at the moment of the fault is a raw `write` and a trap.  No formatting, no number to turn into text, nothing
that could itself fail -- which matters more here than anywhere, this being the code that runs when something has already gone
wrong.  It answers the open question about the pre-`io_uring` error path in the same breath: it assumes nothing about the
descriptor, allocates nothing, and formats nothing.

It ends by **trapping rather than exiting**.  The program dies by a signal at the point of the fault with its stack still
standing, which is what a debugger wants to be handed; a status would say less and could not be told from a program that meant to
exit with it.  The signal is the same on all three targets, which took changing AArch64's trap from `brk` to `udf`: `brk` raises a
different signal from the other two, and a program should die the same way wherever it was compiled.  The trap carries a one where
the padding between functions carries a zero, so a disassembly tells a deliberate trap from a fall into padding.

**The unwinder is deliberately not built.**  The recorded decision was a fault aborting with a real multi-frame backtrace, and
that is still the intent -- but the language has no way to call a function, so every stack is one frame deep, and an unwinder
written now could not be tested against the thing it exists for.  What the message carries instead is what the compiler knew:
which operation, in which function, at which line.  The to-do entry now carries the analysis rather than the intention, so that
the work is a decision already made when there is something to unwind: a frame pointer chain costs a register and two
instructions in every function and needs no table; frame information costs nothing at run time and is what a debugger and a
profiler want anyway, at the price of an absolute relocation in data that nothing generates yet.  The second is the better answer
and the same table can carry the names, which `.symtab` cannot because it is not mapped.

**Division is refused on every target** (8501), not on the one that cannot do it.  x86-64 writes a quotient and a remainder to a
fixed pair of registers, which the allocator cannot yet be told about; AArch64 and RISC-V have one instruction each.  Refusing it
everywhere is what keeps a program meaning the same thing wherever it is compiled, and it is the same call made for the widest
saturating multiplication, which waits on the same machinery.  **Exponentiation is not begun** and needs a decision first: with a
constant exponent it is a few checked multiplications, and with an exponent known only at run time it is a loop the language
cannot write and the compiler would have to emit.

---

## 2026-09-14T09:15+02:00 — language

**A function with no arrow answers with nothing; a semicolon separates and never terminates**

Two rules the user asked for, and they turn out to be one rule seen twice: the last statement of a body is the body's result, so
anything that changes what the last statement *is* changes what the body answers with.

**No arrow means nothing answered with.**  `fn prepare():` rather than `fn prepare() → void:`, and the second is refused (4209).
Refusing it is the one-meaning-one-spelling rule applied, which this language now states outright; leaving both would be the
first place the rule is broken, and in the definition syntax at that.  `void` is also not a type any value can have, so naming it
where a type belongs says less than leaving the place empty -- Rust and Haskell make the same choice for the same reason, where C,
C++ and Java write the word out.

Three things follow, and it is worth saying that all three are the ordinary rules applied and not rules of their own.  A `return`
in such a function carries nothing (5004, which already existed and now has somewhere to fire).  An expression as its last
statement is a value that goes nowhere (5005, the rule that landed earlier), because a body with no result has nowhere to put one
-- what belongs at the end of such a body is an assignment, which is a statement that does something.  And a trailing semicolon
asks for nothing, which is what was wanted.

**A semicolon separates and never terminates.**  It is now accepted in the layout notation as well as between braces, so that two
short statements may share a line and so that what a statement is does not depend on which notation it is written in.  The
important half is the second word: what follows a semicolon is another statement, and where nothing is written there, that
statement is the empty one.  `a;` is two statements, `a;;` is three.

That makes `7u8;` in a function declared to answer with a number a program that does not answer -- and being able to write that
deliberately, and to be told when it happens by accident, is the whole reason the rule is worth having.  Rust draws exactly this
distinction with exactly this mark, and for exactly this reason.  C, C++, Go and Java have no such distinction to draw, a
semicolon there terminating one statement rather than separating two, which is why a stray one costs nothing in those languages
and is worth noticing here.

**3002 is retired.**  It asked for an arrow, and nothing can ask for one now.  A header followed by a bare type is no longer a
header missing its arrow -- it is a header that has ended, followed by something that is not the beginning of a body -- so what is
reported is 3003, which says that.  The number is retired rather than reused, for the reason 3008 was: a number is how a program
names a diagnostic in `@[expect]`.

**One diagnostic narrowed.**  The advice that a `return` at the end of a body could be left off (5002) is no longer given where
the statement is one the function could not have wanted.  `return 1u8` in a function answering with nothing is already reported
for what it is, and telling the author to drop the keyword would leave an expression whose value goes nowhere -- advice that makes
the program worse.

In the tree-sitter grammar the empty statement has no node: there is nothing in the text to give one to, and what matters about it
is only that it is a statement, which is a thing the compiler says and the grammar need not.  The grammar says the shape -- what
follows a semicolon may be written or left out -- and the test that the two agree about every language test is what holds them
together.

---

## 2026-09-14T11:00+02:00 — language

**A call that answers with nothing has nothing to use, and `return f()` is an abbreviation and not an exception**

Decided on the user's direction, and recorded before there is a way to write it: the language still has no way to call a function,
so none of the three uses this is about can appear in a program.  What could be done was done, and the rest is written down so
that implementing the call is a matter of following a decision rather than making one.

**The rule.**  A call to a function that answers with nothing has no answer, so naming one where a value is wanted is an error --
it cannot start a variable, be assigned, or be an operand.  Nothing here was really open; it is what "answers with nothing" means.

**The exception, which on inspection is not one.**  In a function that itself answers with nothing, `return f()` is allowed where
`f` also answers with nothing.  It looks like a hole in the rule and is not: it lowers to the call and then a return carrying
nothing, and no value is named anywhere in it.  That is why it is well formed where every other use is not, and it is the same
shape the language already admits in `return x` beside a bare `x` as the last statement.

C and C++ allow exactly this, and for a reason this language does not have -- a template returning `T` where `T` may be `void`.
It is worth keeping anyway: a function ending in a call to another can then say "and that is the last thing I do" in the place a
reader looks for it.

**Where it is enforced.**  In the representation, which is a level below the syntax and does not wait for it: no instruction may
be given an operand whose type is `void`, and a call that answers with nothing is the only thing that has one.  Five tests build
that representation by hand -- the call on its own, then its answer stored, added, compared and returned, and then the
abbreviation written out -- and the abbreviation is the one that passes.  A front end that lets such a name be written will still
have to report it itself, with the place the name appears; this is the net underneath, and it catches a pass that builds one by
mistake.

**What is left.**  The surface rule needs the surface, and the call entry in the to-do list now carries what was settled here
along with the one part of its question still open: whether arguments may be named.  It also names what the compiler needs beyond
the syntax -- a rule for `CallInst` in the backends, and a register allocator that knows a call destroys the registers a
convention calls caller-saved.  The second is the one that matters, a value held across a call being silently lost without it;
the machinery is already there, the call having only to declare those registers as ones it writes, which is how the flags
register is already handled.

## 2026-09-14T14:00+02:00 — language and compiler

**Floating point is the hardware's, four operators are defined on it, and an exact comparison is diagnosed**

Decided on the user's direction that the hardware's floating-point instructions are assumed on all three targets and that the
requirement is recorded in the binary.  What that comes to per target: on RISC-V the header's flag word now says the
double-precision convention (`EF_RISCV_FLOAT_ABI_DOUBLE`) instead of soft-float, which is what a loader and a linker read to
refuse a program built for one convention against a library built for the other; on x86-64 and on AArch64 there is nothing to
record, SSE2 and the scalar floating-point instructions being in the base of both ABIs, so that saying so in the specification is
the whole of it.  The flag says DOUBLE whether or not a particular program uses floating point, because what it states is the
convention its functions follow and they follow that one either way.

Considered and rejected: a soft-float path, as GCC and LLVM both keep for targets without the hardware, and as the RISC-V flag
word has a value for.  It would double every rule here and would make the same program a hundred times slower on one machine than
on another without saying so, which is the surprise this language exists to avoid.  A program that needs a machine without a
floating-point unit is a program that should not use the type.

**Four operators are defined on a floating-point value: `+`, `-`, `×` and `÷`.**  The bitwise operators, the shifts, the
rotations, the saturating operators and `%` are refused (4205), each for a reason the type states: a floating-point type says the
value is a number and not the bits it is kept in, so a question about bits has nothing to ask; saturating is the nearest end of a
range of whole numbers; and `%` is what is left of a division that stopped at a whole number.

This is narrower than C, where `%` is refused but `fmod` is a call away and the bitwise operators are refused only because the
operand is not an integer; it is the rule of Go, Rust, Zig and Odin, all of which refuse the same set.  APL and BQN go the other
way and apply nearly everything to nearly everything, which suits an array language whose values are numbers first.

`÷` on floating point lowers to a third instruction, `FDIV`, and neither of the two an integer has: the answer is not truncated
towards anything and there is no pair of operands it has no answer for.

**`=` and `≠` on a floating-point value warn (4217), controllable as `exact-float-comparison`.**  Two floating-point values
arrived at by different routes are rarely the one value even where the numbers they stand for are equal, so the exact question is
nearly always the wrong one.  It is a warning rather than an error because it is sometimes right -- a value compared against one
it was assigned from, or against a number every format holds exactly -- and `@[ignore(4217)]` is how a program says it meant it.

Considered: leaving it silent, which is what every one of C, C++, Go, Rust, Zig and Odin does, with the warning available only
from a lint tool nobody turns on (`clang-tidy`'s `clang-diagnostic-float-equal`, `gcc -Wfloat-equal`, `staticcheck`); and making
it an error, which no language does and which would be wrong for the cases above.  Turning it on by default follows from what the
diagnostic catalog is for here: a numbered rule is how this language states a rule the reader may not know, and a generator that
emitted an exact comparison has almost certainly emitted the wrong one.

**How a floating-point answer is checked in a test.**  Nothing can turn a floating-point value into a number a program hands
back, so the language tests assert with the only means the language has: `stop_unless(ok)` computes `ok or (one ÷ zero = one)`,
and `or` does not compute its right side unless the left one leaves the answer open.  A wrong answer therefore divides by zero and
the program stops; the test runs to the end exactly when every answer was right.  It is worth writing down because it is the
shape every test of a value that cannot be handed back will use until there are conversions.

## 2026-09-14T15:00+02:00 — language and compiler

**An answer that is not a finite number stops the program**

The second of the two rules that were decided when floating point was sized, now implemented.  Every one of the four operations
is followed by a check; an answer that is an infinity or a not-a-number reports where it happened and stops, the way an integer
sum that will not fit does.  Where both operands are constants the compiler sees it while compiling and reports it there (4214,
4215), because a program that must stop whenever it is started need not be built -- which is the rule the integers already got.

This is the language's rule applied to the type rather than an exception carved out of it.  Everywhere else a value a program
holds is one its type can represent; an infinity is what the format says when it cannot say the number, and a program that carried
one would compute every answer after it from a value standing for no number.  That is how a mistake in a floating-point program
usually travels a long way from where it was made, and stopping at the first one is what makes the distance zero.

Considered: carrying the infinities, which is what Java, Go, Rust, Zig, Odin, C and C++ all do, and what IEEE 754 is designed to
allow -- the infinities and the not-a-numbers exist precisely so that a computation need not stop.  It was rejected for the same
reason the integer overflow check was: a value a program cannot have is a value it does not get, and a language whose types say
what a value is cannot hold one that says "no number" and still mean what it reads.  Also considered was raising the IEEE
exception and leaving the program to read the flags, which is C's arrangement and which almost no program does.

**How the check is written.**  Subtracting a value from itself answers zero where it is finite and not-a-number where it is an
infinity or already a not-a-number, so one subtraction and one comparison settle both cases at once, and the branch that carries
on is the one not taken.  On x86-64 that is `subsd`, `ucomisd` of the difference against itself and `jnp`, the parity flag being
what says the two were unordered; on AArch64 `fsub`, `fcmp` and `b.eq`, unordered not being equal; on RISC-V `fsub`, `feq` and
`bne`, there being no flags and the answer being a value.  Three instructions on every target, against the alternative of masking
off the sign and comparing with the largest finite value, which needs a constant in the image and one more instruction to reach
it.

## 2026-09-14T16:00+02:00 — language and compiler

**The approximate comparisons, and `⎕tolerance`, which is the first name the compiler provides**

Decided on the user's direction that the approximate comparisons come with the floating-point types and that the tolerance is a
global variable for now.  Six operators, `≅ ≇ ⪅ ⪆ ⪉ ⪊`, each of the exact comparisons with the question asked of the tolerance
rather than of the values; the table in TODO-language.md is what they mean and is now in the specification.  None has an ASCII
substitute, there being no ASCII spelling of "approximate" that is not read as something else.

Each of the six lowers to a subtraction, a read of the tolerance and one ordinary comparison, with the magnitude in between for
the two that ask about likeness in either direction.  The two strict ones are the loose ones with the operands exchanged and the
answer turned round, which is why there are three shapes and not six.  Where the operands are `f32` the difference is computed in
`f32` and widened to `f64` to be measured -- one instruction, and the same answer as widening both operands first, which would be
two.

**`⎕tolerance` is the first name the compiler provides**, and the glyph is the decision worth recording.  A name beginning with
`⎕` is the compiler's; a program may read and assign the ones that exist and may not define one (4219).  So the compiler can add
another later without taking a name away from a program written before it existed -- the problem every language has that puts its
own names in a program's namespace, and which C answers with a reserved-identifier rule that nothing enforces, Rust with `std::`
and a prelude that can still be shadowed, and Go with predeclared identifiers that a program *may* shadow, quietly.

Considered: a plain name like `tolerance`, which collides; a reserved word, which is a name taken away from every program that had
it; a builtin module, which needs an import for one number and makes the tolerance a thing that might not be there.  The quad is
APL's own arrangement, and `⎕CT` is the variable this one is modelled on.

**The tolerance is absolute and not relative.**  APL's `⎕CT` is relative: two values are alike when they differ by less than `⎕CT`
times the larger of them, which is the better rule for values of widely differing size and costs a multiplication and a magnitude
more.  Absolute is what was decided when floating point was sized -- "a load, a subtraction, an absolute value and one ordinary
comparison" -- and the question of whether to change it is in the to-do list rather than settled here.  The default, 10⁻¹³, is
APL's.

**Three things it needed underneath.**  A `FABS` in the representation, which is one instruction on two targets and an `and` with
a mask out of the constant pool on the third, nothing there clearing one bit of a vector register.  A `CastKind.FEXT`, the first
conversion the compiler emits, which every wider floating-point format holds exactly so there is nothing to check.  And a fix in
the lexer, which had been stepping over the first character of a name only because every character that could begin one could also
continue one.

## 2026-09-14T19:00+02:00 — language and compiler

**The result type `TYPE?`, its two operators, and the division that answers with one**

Decided on the user's direction: the language gets the builtin result type the to-do list specified, and the division operator
answers with one, giving the error where the division has none.  What the to-do list already settled -- the spelling `TYPE1?TYPE2`
with the second name omissible, `?` for "the answer, or leave the function with the error", `??` for "the answer, or this instead"
-- is implemented as written.  What follows is what had to be decided beyond it.

**The error carries nothing, so `TYPE?` is what works and `TYPE?ERROR` is refused.**  Nothing in the language constructs an error
value, so a result whose error had a payload would be a type no program could put anything in.  The syntax is parsed and the
compiler says it lacks the feature (9902), which is the honest answer and leaves the spelling in the specification where it
belongs.  This is what waits on the sum type, which is where an error type with variants will come from.

**A value of the answer type, written where a result is wanted, is the successful result.**  There is no syntax for writing one,
and inventing a constructor for a type with no others would be a piece of syntax existing for one purpose.  Zig does exactly this
for its error unions (`return 5;` in a function returning `!u32`) and C++'s `std::expected` converts implicitly from `T`; Rust
writes `Ok(x)` and can, because `Ok` is an ordinary constructor of an ordinary sum type, which this is not yet.  The reverse is
not admitted: a result where a plain value is wanted is refused, since accepting it would drop the error in silence -- which is
the whole thing the type exists to prevent.

**`%` follows `÷`, and floating point follows both.**  Every operation that has no answer for some operands answers with a
result; anything else would be a rule with an exception in it.  For floating point the pair with no answer is a zero divisor,
which is also what keeps `1f64 ÷ 0f64` from being an infinity that the finiteness rule would then stop the program over.  So a
zero divisor is an error value on every type, and an overflowing *answer* is still a fault -- the distinction being that one is a
division the operands did not define and the other is a type that was too narrow.

**Two compile-time reports become warnings.**  `1u8 ÷ 0u8` was an error (4215) because the program could only fault; it is now a
well formed expression whose value is the error, so it is a warning, as is the overflowing signed pair (4223, new).  Both are
reported because the error is the only thing such a division will ever produce.

**`?` requires the enclosing function to answer with a result** whose error type is the same (4221), which is what "leaves the
function with the error" means.  Its answer type need not agree, since what travels is the error and an error carries nothing.

**Precedence.**  `?` binds as tightly as a call, to whatever stands immediately before it, so `a ÷ b?` is `a ÷ (b?)` and the whole
division is `(a ÷ b)?` -- Rust's arrangement.  `??` binds tighter than the comparisons and looser than everything that computes a
number, and is right associative so that `a ?? b ?? c` reads as "a, or else b, or else c"; C# puts its `??` lower still, just
above assignment, which this language has no place for since assignment is a statement.

**How a result is represented.**  Two registers, never one: the answer in the register an answer goes in, and a truth value beside
it in the second register the convention returns a two-word answer in, which is what every one of the three ABIs already does with
a two-word aggregate.  The register allocator therefore sees two ordinary values and nothing aggregate at all, which is what made
this a change to the lowering rather than to the allocator.  Three instructions carry it in the representation -- `wrap`,
`unwrap`, `failed` -- and `unwrap` emits nothing, being the statement that the answer half is what is wanted from here on.

**What is left, and is written in the to-do lists.**  A variable at the top level cannot hold a result (9902): that needs a layout
in memory, which is a question the compiler has not answered.  Neither can a parameter, which needs the positional mapping to
account for a value that takes two registers.  A local holds one today, because a local is a value and needs no layout.

## 2026-09-14T21:00+02:00 — language and compiler

**Product and sum types: one construct, and the separator says which**

Decided on the user's direction.  A definition is `type NAME = ` and then a sequence of `NAME : TYPE` pairs; the pairs are
separated by `;` for a product, which holds all of its parts at once, or by `|` for a sum, which holds exactly one.  The sequence
may be written over several lines inside braces or indented under the definition.

That is one construct where nearly every language has two.  Rust has `struct` and `enum`, Go has `struct` and nothing, Zig has
`struct` and `union(enum)`.  Haskell has one `data` declaration and separates a sum's alternatives with `|`, which is where the
glyph here comes from; the `;` beside it is the language's own, and the two characters already mean "and also" and "or else"
everywhere else in it.  A product and a sum are dual, and writing them with one construct that differs in one character says so.

Considered and rejected: `type Point = (x: i32, y: i32)`, which reuses the parameter-list shape but offers no obvious spelling for
a sum; two keywords after Rust; and an untagged `type Result = i32 | Error` naming only the types, which leaves a sum's parts
unnamed and therefore unreachable.

**What had to be decided beyond the syntax given.**

*One pair with no separator is a product.*  There is nothing to go by, and a record of one field is a useful thing where a choice
between one alternative is not.

*A line may be broken after a separator and not before one.*  Both readings are writable, and allowing either made the grammar
ambiguous: after a pair and an end of line, nothing says yet whether the definition ended.  Ending the line with the separator
keeps that decidable where it is written.

*A defined type is nominal.*  Two definitions with the same parts are two types, and two files each defining `Point` define two.
A definition is what says what a value *is*; two things laid out alike are not one thing.  The type in the representation
therefore carries the name it was given and the file that gave it, and renders as its name, which is what a diagnostic about one
should say.

*A variant of a sum may be `void` and a field of a product may not.*  A `void` variant says the value is this alternative and
carries nothing further, which is how an enumeration is written; a `void` field leaves the product meaning what it would have
meant without it, which is a second spelling of one thing.

*A type may not reach itself* (4408), directly or through a chain: a value of such a type would have to hold a value of itself,
and the indirection that makes that finite elsewhere is not something this language can write.  Resolution is therefore on first
ask with a mark saying "being worked out", which catches the chain and reports the type the chain came back to.

*A type name lives in the same namespace as everything else at the top level.*  A name in this language stands for one thing, and
which kind of thing should not have to be worked out from where it is written.

*Types are read before functions and variables*, and imports before types, so that a signature may name a type defined below it
and a type may be one another module exports.  `@[export]` now applies to a type, and `m.Point` names one.

*What a value occupies.*  A product is its fields, each where its own alignment allows, in declaration order for now -- the
specification lets a later pass choose better, and `offsets_of` is the one place that would change.  A sum is its largest variant
with a one-byte tag *after* it: a tag ahead of a payload wanting eight bytes is seven bytes of padding and behind it is often
none.  A result is laid out the same way, which settles what it looks like in memory even though nothing puts one there yet.

**What is deliberately not in this change.**  Nothing writes a value of a product or a sum, nothing reads a field, and nothing
asks which variant a sum holds -- the last needs control flow the language does not have.  No syntax was given for any of the
three, so all three are questions in the to-do list with proposals rather than decisions made here.  A function that takes or
answers with one compiles as far as the code generator, which says it cannot generate for it (8501) rather than putting a value
in a register it does not fit in.

## 2026-09-14T23:00+02:00 — compiler

**A result is a value like any other: in a variable, in a parameter, and in the image**

The two things the result type still could not do are done.  A variable at the top level may hold one, and a function may take one
and answer with one.  Nothing about the language changed; what changed is that the compiler now knows where the two halves of a
result go in each of the two places a value can be.

**In memory**: the answer where an answer goes and one byte beside it saying whether there is one, rounded up to the answer's
alignment.  The layout was already computed -- it was written when the product and the sum types got theirs -- so what was left
was the reading and the writing, which are two accesses of one place rather than one.  On x86-64 that turned up a real defect: a
symbolic rip-relative memory operand dropped its displacement, the fixup aiming at the symbol and nothing adding the offset.  The
whole of such an address goes in one field, so there is nowhere else for a displacement to be, and it now joins the expression the
fixup aims at.

**In registers**: two, which is what the type already used inside a function.  Passing one needed the argument mapping to stop
counting by position: a register is taken from the list its *kind* comes out of, so a floating-point argument does not use up an
integer register, and a result takes one of the answer's kind plus one ordinary one for the truth value beside it.  Counting by
position was already wrong the moment a floating-point argument stood beside an integer one -- the hint for it named an integer
register -- and this is the fix for both.  The counting is written once, in `target/callconv.py`, and the three backends only turn
a place into the view of it their instructions name.

**The only constant of a result type a program can write is the successful one.**  That follows from the rule already decided --
a value of the answer type written where a result is wanted *is* the successful result -- and needs no new syntax.  So
`let kept: mut u8? = 1u8` initializes a variable, and `ResultConst` is what carries it to the image.

**One thing came out of the front end**: the hint that gives an untyped literal a type looked through `??` and `?` and offered the
*result* type rather than the answer type, so `(q ?? 0u8) + n` reported the two operands as differing.  A hint is what a value
would be, and what those two operators answer with is the answer.

**What is still open, and why.**  `TYPE1?TYPE2`, a result whose error carries a value, is refused (9902) as it was.  It is not a
question about the result type any more: the sum type now exists, so what is missing is a way to *write* a value of one, which is
an open question of its own.  Until something can construct an error, such a type is one no program could put anything in.

## 2026-09-15T03:00+02:00 — language and compiler

**`match`, and an alternative named by its type**

Decided on the user's direction: a `match` after Rust's, with arms written `TYPE(x)` naming the alternative by its *type*, and
`⊥` for the error arm of a result -- `⊥(x)` where the error carries something and `⊥` alone where it does not.

**Naming an alternative by its type is the decision everything else follows from.**  It means no constructor has to exist before a
value can be taken apart, which matters here because nothing yet writes a value of a sum: the patterns are complete while the
values are not.  It also means **no two alternatives of a sum may have the same type** (4410), which the user stated as the
consequence it is.

That rule has a cost worth recording: an enumeration -- a sum every one of whose alternatives carries nothing -- cannot be
written, all of them being `void`.  The specification said last week that an all-`void` sum was how one is written; it no longer
does.  The to-do list carries the question, with the two ways out: let a pattern name the *variant* instead of the type where the
two differ, which is Rust's arrangement and would make the uniqueness rule unnecessary; or give the language an enumeration of its
own.

**`⊥` rather than a type for the error arm.**  A result's two alternatives may name one type -- `u8?u8` is a perfectly good
type -- so naming a type cannot say which arm is which.  The glyph is logic's bottom, the proposition that never holds.

**Exhaustive, with no catch-all.**  Every alternative must be taken and none twice.  A catch-all would let an alternative added
later fall silently into a branch written before it existed, which is the mistake exhaustiveness is checked to prevent; Rust has
`_` and Zig has `else`, and this has neither on purpose.

**A statement, not an expression.**  The user asked for a statement.  Whether it should yield a value is the same question `if`
raises, and the two should be answered together rather than one of them settled by whichever was implemented first.

**What it needed underneath, which `if` will want too.**  A name bound outside the match and assigned inside one arm stands for
two values afterwards, one per arm, so the block the arms join at takes it as a parameter and each arm hands its own value over.
The memory token is merged the same way and for the same reason: two arms that both touch memory arrive with two tokens.  A
memory-typed block parameter is not a value in a register -- what it says is which path's ordering holds from here -- so the
backends skip it, which is one line in each and one in the shared branch lowering.

The unread-value rule says nothing about a name a match may carry past its arms.  Whether an earlier value survives is a question
about paths; that rule is a statement about a straight line of code, and answering it inside an arm would be a guess.

**On a sum it is checked and not generated.**  Every rule about the arms is applied, and then the compiler says it cannot generate
for one (9902).  What is missing is not the match: it is a value of a sum, which nothing writes, and the way one is held, which is
not a register.  A match over a *result* runs today.

## 2026-09-15T05:00+02:00 — language and compiler

**Enumerations, and a wildcard in `match`**

Decided on the user's direction.  `enum NAME [: TYPE]` and then the names of its values, in braces or indented under a colon,
separated by `;`.  A `match` over one names a value in each arm, and `_` takes every alternative no earlier arm took -- on every
kind of value a `match` takes apart, not only on an enumeration.

**The type is the representation and nothing else.**  The user said so outright and it is the decision that matters: writing
`: i32` does not make a value of the enumeration an `i32`, does not let one stand where an `i32` is wanted, and does not let an
`i32` stand where the enumeration is.  It says how much room a value takes and how it is aligned.  That is C++'s `enum class` and
the opposite of C, where an enumerator *is* an `int` and converts both ways silently.

**Where no type is given the compiler chooses the smallest unsigned type that holds every value** -- `u8` up to 256 of them, then
`u16`, `u32`, `u64` -- and the values are numbered from zero in the order they are written.  Both are promises about size and
alignment and about nothing else: with no conversion in the language there is nothing a program can use to observe the numbering,
so a later implementation may number them differently and a program that reads as it behaves cannot tell.  Considered and
rejected: always `u32`, which wastes three bytes in the common case and is what several ABIs do for compatibility with C; and the
*signed* smallest type, which would waste a bit for nothing since no value is negative.

**Two colons where a type is named and the values are indented.**  The type is introduced by a colon and so is the block, which
follows from the two rules the user gave and matches a function that answers with something and has an indented body.

**A value is written `TYPE.NAME`.**  The user gave no syntax for writing one, and without one an enumeration could be declared and
matched but no value of it could exist -- the feature would be as unreachable as a sum is.  `Colour.red` uses syntax the grammar
already parses for a name reached through a module, so nothing is invented but the meaning, and it keeps two enumerations able to
each have a `red`.  It is what Rust, Swift and C++'s `enum class` all write.  An arm of a `match` writes the name alone, the type
being known there; Swift and Zig do the same.

**The wildcard reverses a decision made two days ago.**  `match` was written with no catch-all, deliberately, so that an
alternative added later could not fall silently into a branch written before it existed.  The user has asked for one and it is in.
What is kept of the old reasoning: an arm that takes nothing an earlier arm left is reported (4417), so a wildcard written where
everything is already taken is a mistake and not a habit, and exhaustiveness is still checked -- the wildcard satisfies it rather
than switching it off.

**What an enumeration is at run time**: a number of the type that holds it, so it needs no new machinery in the backends -- a
width, a signedness and a constant, which are three small answers in each.  A `match` over one is a chain of comparisons; a jump
table is what `SwitchInst` is for and stays in the to-do list.

## 2026-09-15T09:00+02:00 — language and compiler

**Enumerations gain numbers and flags, `match` gains a value, and `=` works on two values of one enumeration**

Four things the user asked for, and what had to be decided around them.

**`=` and `≠` on an enumeration.**  Two values are one value or they are not.  Ordering is deliberately still refused: the order
of the values is the order the definition wrote them in, and the language promises nothing about that -- promising it would make
the declaration order part of the meaning, which is the sort of thing a generator changes without meaning to.

**`match` produces a value where one is wanted of it.**  It is now an expression, and a statement in the one place an expression
is worth writing for what it does rather than for what it comes to.  Where a value is wanted every arm ends in a statement that
has one and they are all of one type (4426); the block the arms join at already carried names and the memory token across, so the
value is one more parameter of it.  The statements that have a value are the ones that may be the last of a function's body -- an
expression, and an assignment -- and an arm ending in `return` owes none, leaving the function rather than reaching the join.

Considered: requiring the type to be written on the match.  Rejected: the first arm that produces a value says what the type is
and the rest are checked against it, which is what the operands of an operator already do for each other.

**Values may be given numbers, and two written down may not be alike** (4423).  Two values a program cannot tell apart, written as
though they were two things, is a mistake.  Taking another value's *name* says outright that the two are one and is how an alias
is written (4424 where the name is not an earlier value).  The alternatives of a `match` are therefore the distinct *numbers* and
not the names: two names for one number are one alternative, and an arm naming the second after one naming the first is reported
as an arm that can never run.

**`@[flag]`.**  The values the compiler chooses become powers of two, and the bitwise operators are defined on two values of the
type.  Which operators: `&`, `|`, `^` and `~`, which are the bitwise operators the language has.  `⊼` and `⊽` were considered and
left out -- they are the *logical* nand and nor, they sit at the logical precedence level, and giving them a bitwise meaning would
make `a ⊼ b` bind looser than `a & b`, which is wrong for an operator on bits.  A "neither" is `~(a | b)`.  Arithmetic is left out
for a different reason: it would be a question about the number a value is stored as, which is the one thing the type does not
say.

A flag enumeration's values combine, so a value of one may be a combination no single name stands for.  Naming every name
therefore does not account for every value, and a `match` over one needs an arm taking the rest (4425).  That is the rule that
keeps exhaustiveness honest for a type whose values outnumber its names.

**One thing the grammar had to be told carefully.**  A `match` is an expression now, so a line may end with one in four shapes --
a variable definition, an assignment, a `return`, or the match alone -- and such a line has already taken its own end.  Making the
line end optional was tried and is wrong: it lets two statements share a line with nothing between them, which showed up at once
as `3bool` parsing as `3` followed by `bool`.  The four shapes are written out instead, and a line ending with one has a node of
its own.

## 2026-09-15T13:00+02:00 — language and compiler

**`if`, `elif` and `else`, and the last of the control flow that was open**

Decided on the user's direction, and it closes the entry that has been open since the language had no control flow at all.  `if`
takes a condition with no parentheses around it, then a body in either notation, then zero or more `elif` with the same shape,
then an optional `else`.

**No parentheses, because nothing needs them.**  What ends the condition is the body, which begins with a colon or a brace, and
neither can be part of an expression.  That is Python's and Rust's and Go's arrangement; C needs them because its body may be a
bare statement with nothing marking where it starts.

**The condition is a `bool` and a number is not one** (4427).  C treats any scalar as a condition, which is what makes `if (x = 0)`
compile there; this language has nothing for a number to mean in that place, and `n = 0u8` is how the question is asked.

**It produces a value, and then needs an `else`** (4428).  `match` produces one, so an `if` that did not would have been the odd
one out -- which the to-do list said two days ago.  The `else` is required for the reason Rust requires it: without one there is a
way through that runs no arm, and that way would owe a value it has nowhere to get.  An arm ending in `return` owes none.

**It needed no new machinery.**  The block the arms join at, the names it carries across, the memory token it merges and the value
it hands out were all built for `match`; what `if` added was a chain of conditional branches in front of them and one more shape
of arm.  The part that was already there was generalized rather than copied: an arm is now a body and a block rather than a
`match` arm, so the two constructs share the whole of what runs them.  An `if` with no `else` needed one thing more -- a way
through that runs no arm at all and still reaches the join, carrying what was true before the `if`.

**Two spellings that mean one thing, kept.**  `match` over a `bool` and an `if` are the same two branches.  The one-spelling rule
is about constructs that say the same thing; these two are about different questions -- which alternative a value holds, and
whether something is so -- and a language that made you write `match ready: true: ... false: ...` would be worse for it.

**What the grammar needed.**  A line may now end with an `if` as well as a `match`, in the same four shapes, so the three
statements a block expression can end are written as rules of their own and aliased.  An alias over an inline sequence flattens
the fields inside it, which showed up as a variable definition whose name, type and value all came out as siblings called
`variable_statement`; aliasing a named rule keeps the shape.

## 2026-09-15T17:00+02:00 — language and compiler

**Sets and dictionaries: written, typed and checked, and not yet built**

Decided on the user's direction: sets and dictionaries with Python's semantics and a lookup that does not grow with what is in
the collection, written between `⸨` and `⸩`.  What is here is the whole of the front end -- the syntax, the types, the rules
about keys, the operators, the lookups and the assignment -- and code generation says the compiler lacks the feature (9902).

**Why it stops there, and this is the part worth recording.**  A hash table needs a heap for the table to be in and a loop for a
lookup to walk.  The compiler emits no allocator, and which one it should emit is an open question in TODO-language.md with three
answers written out and none chosen; the language has no loop, and whether it should have a general one is the other open
question.  Both are the user's to answer, and choosing either to get a hash table would be deciding a larger thing in order to
reach a smaller one.  So everything that does not depend on them is done, and what does is written down in the to-do lists in
enough detail that the next step is execution rather than design.

**A type is written the way a value of one is.**  `⸨T⸩` and `⸨K: V⸩`, as `⸨a, b⸩` and `⸨k: v⸩` are.  A parameter list and a
call already have that arrangement; a language emitted by a generator wants the shape written down rather than constructed by a
call, which is why these have syntax at all where Rust has library types.

**Which of the two a collection is is decided by its first entry**, and an empty one by the type it is wanted as.  Python needs
`set()` for the empty set because `{}` was already the empty dictionary; one pair of brackets and a rule about the first entry
avoids that, at the cost of an empty collection needing a context -- which it has everywhere one can be written.

**A dictionary lookup answers with a result.**  `d⸨k⸩` is `V?`, not `V`.  Python raises `KeyError` and Go answers with a second
value nothing makes you read; this language has a type that says "or not" in the signature, so a key that is not there cannot be
read past by accident, and `d⸨k⸩ ?? 0u8` is `d.get(k, 0)` written with an operator that was already there.  That is the decision
this feature most turns on, and it fell out of the result type rather than being invented for it.

**What can be a key.**  A type `=` is defined on and answers exactly: integers, `bool`, enumerations.  Floating point is refused
and the reason is three-fold -- a not-a-number is equal to nothing including itself, so a key put in could never be found again;
the two zeroes are equal and have different bits; and two values arrived at by different routes rarely are one value, which is
what the approximate comparisons are for and what a table cannot use.  Python allows floats as keys and has all three problems.

**The four set operators are Python's**, with Python's spellings -- `|`, `&`, `^`, `-` -- which are the characters the language
already gives a number's bits, asking the same question of a different kind of collection.  Equality compares two collections;
ordering does not, Python's reading of `<=` as "is part of" being a different question from which of two comes first.

**A value of one is a handle**: where the table is and how many entries are in it, two words, with the table elsewhere.  That is
what lets a collection be passed and answered with like anything else, and it is provisional in the way a layout is -- nothing a
program can observe depends on it.

**What the runtime will need**, written here so it is not designed twice: open addressing with linear probing, a power-of-two
capacity, growth at about seven eighths full, a tombstone for a key taken out, and a hash the compiler emits per key type -- a
multiply-and-shift for an integer or an enumeration, and the value itself for a truth value.  The table is a block of memory
holding the capacity, the count, and the entries; the handle points at it.  Nothing of that is written yet.

## 2026-09-15T21:00+02:00 — language and compiler

**Tuples, and one register per part for everything that has more than one**

Decided on the user's direction: tuples between `〈` and `〉`, a comma-separated list, and names written next to each other taking
one apart in a definition or an assignment -- what `auto [a, b]` and `std::tie` do in C++, without the second pair of brackets.

**Angle brackets rather than parentheses**, so that a tuple of one thing is still a tuple and not the thing with brackets round
it; parentheses already group an expression and cannot also make one.  **No brackets around the names** that take one apart: the
comma is enough, which is what the user asked for and what Python and Go do.  Rust keeps them so that a pattern looks like the
value it matches, which matters where a pattern may be nested; here one may not.

**A tuple is a product with no names**, and that is what it is for: naming the parts of an answer that is taken apart on the spot
would be naming something that does not outlive the line it is written on.  Where the parts mean something beyond their position,
the product type says so.

**The representation generalizes what the result type already had.**  A result is two registers; a tuple is as many as it has
members, of whatever kind each wants.  So the second register a result kept in a map of its own became a list of the registers
after the first, and everything that places a value -- an argument, an answer, a parameter, a block parameter, a branch that hands
one over -- now asks `parts_of` what the parts are rather than knowing the shapes.  That also lifted the refusal on a block
parameter of a result type, which had been refused because nothing could generate one; a tuple carried out of an `if` generates
exactly that.

A tuple answered with has to fit the registers the convention answers in, which is two per kind here, and one that does not is
refused rather than put somewhere else.  A tuple of three integers is therefore not yet returnable, and the entry in the to-do
list says what that waits on.

**Two defects came out of it.**  A loop variable named `index` inside the instruction walk shadowed the block index the branch
lowering reads, so a conditional branch after a call with a multi-part argument chose its fall-through from the wrong block -- it
emitted a jump to the instruction after itself and left the other arm unreachable.  It was caught by an existing language test
rather than by the new ones, which is the argument for keeping every one of them running.  And `〉` was not in the lexer's list of
closing brackets, so a line ending in a tuple type never ended: the newline was swallowed as though it were inside brackets.

**A variable with no type written now takes it from the value.**  `let a, b := f()` needs it, a call being the usual thing to take
a tuple from, and the rule reads better than the one it replaces: what is written nowhere is what the value turned out to be.

---

## 2026-09-14T09:00+02:00 — compiler

**A place reached through an address in a register**

The first of four pieces the heap, the loops and the collections runtime all rest on.  Nothing in the compiler could read or write
memory that a *name* did not point at: six guards in the three backends, one per form of access, refused any address operand that
was not a variable.  Everything below them was already right -- the verifier asks only for a pointer type and its message already
says "a place", and every backend's move and store selection already had a base-register path -- so the change is that the
backends now choose between a symbol reached relative to the instruction and a register holding the address, and nothing above
them has to know which.

**Three instructions make an address that is not a name.**  `address` puts a variable's own address in a register.  Adding a
number of bytes to an address moves it.  `bitcast` reads the same bits as a pointer to something else.

**Adding to an address is not the checked addition the same operator means on two numbers.**  C and C++ make pointer arithmetic
undefined past one element beyond the end and leave it unchecked; Rust makes it `unsafe` and unchecked; Zig checks a great deal
else but not this.  The reason to leave it unchecked here is not performance: what a number overflows into is another number, and
so there is a nearest value to answer with and a bound to answer against, while what an address past its place names is not a
place at all.  There is nothing to answer with.  Where a bound is known it belongs on the *type* -- a collection knows its own
length -- and that is where the check will go.

**`bitcast` is restricted to addresses.**  The enumerator had been declared and never used.  Reading an integer as a floating-point
number is the same bits in another register bank, which wants a rule about where the bits are and not only that they are the same
ones; that is a separate question and it is not asked yet.

**The `alloca` stub went.**  It reserved storage whose address is taken, which is the stack's answer to the question; the answer
this language is taking is an allocator, and an allocator answers with an address like any other.  Storage that outlives the
function that made it cannot come from a frame, and everything waiting on this -- a `mut str`, a set, a dictionary -- outlives it.

**One defect came out of it.**  The textual form of the representation splits an instruction's opcode from its operands at the
first space; a type may have a space in it, and `ptr<mut u8>` is the first one that does, so a round trip of any instruction whose
type is a writable pointer failed.  The split now stops at the first space outside brackets, which is what the operand split
already did.

---

## 2026-09-14T11:00+02:00 — compiler and runtime

**A bump allocator over mapped chunks, emitted once for three architectures**

The second of the four pieces.  Two questions in `TODO-language.md` are answered by it.

**Which allocator: a bump pointer over a list of chunks, which is GNU's obstacks.**  An arena is three words -- the first byte not
yet handed out, one past the end of the current chunk, and the head of the list -- so an allocation is an addition and a
comparison.  The alternatives were a size-class allocator, which is what C's `malloc`, Rust's default and Go's runtime all are and
is what a long-running program wants, and the system's own `malloc`, which the specification's no-runtime rule rules out.  An
arena is what Zig calls an `ArenaAllocator` and Odin a `virtual.Arena`, and both offer it as one allocator among several rather
than as the only one.  It is the right *first* one here because a compiler builds a great deal that lives exactly as long as the
compilation, and because nothing in the language yet says that one value outlives another -- so nothing yet can ask for the finer
answer.  The finer answer is in the compiler's list.

**An allocation that cannot be met stops the program**, through the same `__pl4g_abort` an arithmetic fault goes through.  Rust
aborts, Zig answers with an error, C answers with a null pointer and Go stops.  Answering with a result would put a `?` on every
value a program builds rather than computes, which is the cost Zig pays and pays deliberately; here there is nothing a program
could usefully do at that point that the system will not do better by refusing to start it, and the language already has one fault
path that says what went wrong before it stops.

**There are as many arenas as a program makes.**  An arena is three words and nothing else, so one is a value like any other, and
giving an arena back gives back everything that came out of it.  That is what the instruction asked for by "multiple
instantiations can exist", and it is what makes an arena safe for storage with a known lifetime.  The interface the second
allocator will implement is stated in the specification as three entry points: allocate from it, give it back, and make another.

**It is written once and parameterised by a small record per target.**  What differs between the three is the number of a system
call, which registers its arguments go in, and which instruction enters the kernel.  Everything else -- the arithmetic, the
comparison, the branch, the stores -- goes through the same builder every lowered function goes through, which is what makes the
runtime a test of the assembler as well as a part of the image.

**The runtime needed a stack of its own.**  Three things must survive the call that asks for a chunk, and none can stay in a
register: the call's own arguments take every register a caller does not expect back, and the instruction that enters the kernel
destroys two more on x86-64.  The assembler grew `frame`, `unframe`, `put_aside` and `take_back` for code written there rather
than lowered from the representation -- the four the register allocator already used, said out loud.

**What is not done, and why.**  The allocator has no spelling in the language.  A type for an arena, a compiler-provided default
one, and a way to make another are all useless until something allocates, and the things that would -- a set, a dictionary, a
`mut str` -- are blocked on the loops rather than on this.  The entry in the compiler's list says what that spelling is to be, so
that the decision is not made twice.

---

## 2026-09-14T14:00+02:00 — language and compiler

**`while`, and what a loop costs a compiler that never had one**

Decided on the user's direction: the language gets both a general loop and an iteration over something.  This is the first half.

**A loop is a statement and not an expression**, which is where it parts from `if` and `match`.  Those produce a value because
every way through them produces one; a loop has a way through that runs the body no times at all, and there is nothing for that
way to produce.  Rust's `loop` *is* an expression, producing what a `break` hands it -- which is exactly the construct this does
not have, and the entry in the list says so.

**The condition has to be a truth value** (4437), like `if`'s.  C treats any scalar as a condition, which is why `while (n)`
counts down there; that is the same mistake `if (x = 0)` is, and the language already refuses the one.

**A name a turn changes is the loop's own parameter.**  The value one turn leaves is the value the next turn reads, and the value
the last turn leaves is what follows the loop reads.  That is the rule `if` already follows for a name its arms assign, said of a
body that runs more than once instead of one of several bodies that run once -- so the representation needed nothing new for it.

Which names those are is asked of the *syntax* before anything is lowered, which is the one thing a loop cannot do the way `if`
does it.  `if` looks at what the arms turned out to change; a loop's parameters have to exist before the condition is lowered,
because the condition reads them.  Over-counting costs a parameter the allocator coalesces away; under-counting would be wrong, so
what is collected is every assignment anywhere in the body including nested ones.

**The branch that starts the next turn reads every value it carries.**  That is what keeps the unread-value rule from reporting
every counter a loop counts down: the next turn is what reads it, and the branch is the reading.

**What it cost was in the backend, not in the syntax.**  Three things assumed control only falls through, and two of them were
latent defects already.  Liveness read off the layout rather than the graph; a branch's arguments were emitted as a sequence of
moves and a block handed its own parameters back rearranged was refused; the peephole that rewrites a move of zero read "the flags
are dead" as "this is the last block laid out"; and the prologue goes at the top of the first block, which a back edge to that
block would run once a turn.  All four are in the entry before this one.

**Grammar: a loop appears in two places.**  One written with a colon takes its own line ending with it, as `match` and `if` do;
one written with braces ends where the brace does and the line ends after it like any other.  The compiler's parser has one rule
and asks what the statement is; the grammar has to say both, because a line ending is a token there.

---

## 2026-09-14T16:00+02:00 — language

**`foreach`, iterators and ranges**

The second half of what the language gets for repetition, decided on the user's direction.

**An iterator is a value with a `next` answering the next value or a failure**, and the failure is what ends the loop.  The result
type is how the failure is said, and it does not surface: a `foreach` binds its names to what there was, and a loop over something
with nothing in it runs no turns.  That is the same shape `?` already has -- ask, then take what the asking gave -- so nothing new
was needed in the representation.

Rust's `Iterator` answers `Option<T>`, which is the same thing with a different name; Python's raises `StopIteration`, which is an
exception used as a value and is the thing this language has no mechanism for; Go has no protocol at all until its range-over-func.
Answering a result is the choice that costs nothing, because the language already routes every "there may be no answer" through
one.

**A range is the only iterator so far, and its `next` is lowered where it is asked rather than called.**  What it comes to is a
comparison against the end and an addition, which is what every language with ranges emits.  The protocol is still the design: a
user-written iterator drops into the same loop, and the entry in the list says what it needs.

**`A…B` and `A…B…C` mean what Python's `range` means.**  Half-open, because the count of values is then the difference between the
ends and two ranges that meet at a number cover everything between their outer ends exactly once.  Rust writes `a..b`, which is the
same meaning; what is taken from Python rather than Rust is the third part, which Rust spells `.step_by(c)` -- a method, and so a
thing that needs iterators to be values first.

**One glyph and not three dots.**  `...` passes the substitute rules on length, but it is three copies of the character a member
access is written with, and telling `a...b` from `a . ..b` would be a question of how far the lexer can look ahead.

**The step is written down** (4441) **and is not zero** (4442).  Its sign says which way the range runs, and that decides which
comparison ends the loop; a computed step needs both comparisons and a choice between them on every turn.  The sign is read off the
step and what is left is the distance, which is what lets a range count down over an unsigned type.

**The step saturates rather than checking.**  A range whose last turn would step past the end of its own type ends instead of
faulting: what a turn past the end would be is not a value, and the comparison is what says there is no turn.  That is the one
place in the language where saturation is chosen for a reason other than a program asking for it, and it is chosen because the
alternative is a program that faults where Python's would stop.

**`_` binds nothing**, as it does in a `match` arm.  Without it, a loop that runs a fixed number of turns would report a value
nothing reads on every one of them.

**Two spellings for one statement**, which the instruction asked for: `foreach` says what the loop is, and `while` written with a
binding says that the two kinds of loop are one construct with two ways of deciding when to stop.  It is the one place the "one
meaning, one spelling" principle is set aside, and it is set aside deliberately -- what `while` buys is that a reader looking for
the loops finds both under one word.  After `while` the colon has to be written, because a name on its own followed by a colon is
a condition with a body after it; the compiler's parser looks one token further and the grammar declares the conflict.

**A range stands where a loop takes its values from and nowhere else** (4443).  Giving one a name would make it a value with a
type and a place in memory; Rust needs that because its ranges are iterators like any other, and nothing here yet does.

---

## 2026-09-14T19:00+02:00 — language, compiler and runtime

**Sets and dictionaries, and the arena they live in**

The last of the four pieces.  Everything it needed -- a place to put a table, and a loop for a probe to walk -- landed in the three
entries above, and what is left here is the table itself and the spelling for where it goes.

**Open addressing with linear probing**, a power-of-two capacity, and growth at three quarters full.  Every entry is in the block
rather than in a chain hanging off it, so a lookup touches one cache line where a chain would touch one per link.  Python, Rust,
Go and Swift all do this; separate chaining is what the older implementations did and what they moved away from.

**Fibonacci hashing**: the key multiplied by the closest odd number to two to the sixty-fourth over the golden ratio, with the high
bits folded down.  One multiplication, no table, and it is what Knuth describes.  The multiplication wraps, which no program of the
language may write -- the specification says arithmetic is checked -- and which the compiler's own code may: a hash is defined on
the bits, and there is nothing about an overflow here to report to anyone.  Three wrapping operations were added to the
representation for it and are reachable from nowhere else.

**The three operations are generated as functions of the representation**, which nothing in the compiler did before: the entry
point and the allocator are written as instructions for each target.  The reason to generate these is that they have loops,
several live values and arithmetic that wants a register allocator -- exactly what the compiler already does for a program.  It is
also a test of the compiler on its own output, and it found nothing, which is the answer worth having.

**Every key and every value is one word.**  That is what lets one table serve every instantiation: there is no code per key type at
all.  For a key it is also the rule -- what may be a key is an integer, a truth value or an enumeration, and all of them fit.  For
a value it is a restriction (4445) rather than a decision, and the to-do list says what lifting it needs.

**Two states for an entry and not three**, because nothing takes a key out of a collection yet: a probe therefore stops at the
first empty entry and there is no given-up state to walk past.  The entry in the list says what removal would need, and notes that
the language has no statement that removes anything from anything.

**The table block never moves and the entries are a separate allocation.**  A collection is where its block is, so a table that
grew under a name would otherwise leave every other name for it pointing at the old entries.

**A collection is one word and not two.**  How much it holds and how much room it has are in the table rather than beside it, so
that two names for one collection see one answer.  The two-word handle the layout used to say was a design from before there was a
table.

**The four set operators answer with a table of their own** and change neither operand, which is what an operator does everywhere
else in the language.  All four are two walks at most of one generated function that takes the keys of one table that the other
does or does not have: four copies of a probe loop would be four places for one defect to live.

**An arena is a type, and `in` says which one a collection comes out of.**  Zig makes every allocator a value and every allocation
name one, which is where this comes from; Odin puts it in an implicit `context`, so that a line that makes a collection does not
say where it goes; Rust parameterises the collection's type by its allocator.  This sits with Zig and Rust, and stops short of
Rust: the arena is no part of the collection's *type*, so a value from an arena that has been given back is not yet refused where
one from another is expected.  Nothing can give an arena back from a program yet, so nothing can go wrong; the entry in the list
is what would close it, and it is the type-safety the instruction asked for stated as what is still owed.

**`⎕arena` is what a variable of type `arena` starts out holding**, and there is nothing else to write there (4444).  An arena is a
place and not a value: what is written says what the place starts out holding, and three zero words is an arena that has asked the
system for nothing yet.

**A collection made out of two others comes out of the same arena the first of them did**, read off the table at run time rather
than decided while compiling.  It keeps an answer where its operands are, which is the only rule that does not need the arena to
be in the type.

---

## 2026-09-14T22:00+02:00 — language and compiler

**A convention per function, and the one the system uses asked for by name**

Decided on the user's direction.  The specification has said since the first page that the calling conventions need not match the
system's and may differ between the functions of one compilation; nothing had taken it up.

**`pl4g` is the language's own convention and the default; `@[cdecl]` asks for the system's.**  The attribute says "the one this
system uses" rather than naming one, so a program need not know what an architecture calls its own; `@[abi("name")]` is still
there for naming a particular one, and `cdecl` is a name it accepts.  A `@[cdecl]` function also keeps its plain name, because both
halves are the same request: the point of asking is to be reachable from a world that has never heard of this language, and that
world knows neither the convention nor the mangling.

Compare: Rust's `extern "C"`, which is this, and whose default `extern "Rust"` is explicitly unspecified for exactly the reason
the specification gives here; Go, which changed its own convention from the stack to registers in 1.17 because nothing outside the
toolchain depended on it; C and C++, where the ABI is the platform's and a compiler may not touch it.

**A call is placed by the callee's convention and not the caller's.**  That was a latent defect rather than a change: with one
convention in the program the two were the same, and the first program with two would have been miscompiled.

**`pl4g`'s argument registers begin where its answer comes back.**  On x86-64 that is `rax` then `rdx` where the system's begins at
`rdi`.  A function that answers with what it was given -- a great deal of what a generated program's small functions are -- then
has the value where it has to be already.  `fn f(p: u8) → u8: p` is a bare `ret`, and its caller has no move to make either.

Getting there needed two more things, and both are worth more than the convention that exposed them:

**A call's arguments are a parallel copy**, for the reason a branch's are.  A value may already be in the register another argument
is being moved into, and that is likelier the more a convention's argument registers are ones the allocator prefers -- which this
one's now are.  The sequencer written for the loops does it.

**A physical register's live range is several stretches and not one.**  A virtual register is a value and gets a hull; a physical
register is written wherever a convention says it is and holds nothing between one such write and the read that takes the value
away.  A hull said it was busy the whole time, so the value that could have had it was sent elsewhere -- a move into a register and
a move straight back out.  Splitting it is what makes the identity function one instruction rather than three.  It also exposed
that nothing said a call *reads* the registers its arguments went into: the hull had been hiding that, and without it an argument
register looked dead from the moment it was written.

**What a call destroys is asked of the callee, not of its convention.**  A convention can only say what a function is allowed to
destroy; a small one destroys far less, and the difference is a save and a reload at every call.  Measured on a program holding
five values across a call to a function that writes two registers: fifteen instructions and no frame, against twenty-eight with a
forty-byte frame and five save-and-restore pairs.

What that answer is worth depends on the order the functions are generated in, since a callee generated after its caller is one the
caller could not ask.  Sorting by the call graph is what makes it always exact, and it is what an inliner would want as well; both
are in the to-do list, as the instruction asked.

**The runtime keeps the system's convention.**  It is written as instructions rather than lowered, so it names its registers
outright; a convention the compiler chose for it would have to be read back out of the assembly.  That is a rule worth stating
rather than a workaround: hand-written code has a settled convention, and everything the compiler generates may have its own.

---

## 2026-09-15T02:00+02:00 — language and compiler

**Arrays, fixed and dynamic**

Decided on the user's direction: `T⟦N⟧` carries everything in the type and needs no memory but its elements; `T⟦⟧` carries what it
needs beside the elements; `a⟦i⟧` reads one and `a⟦i…j⟧` a run of them.

**The length goes after the element type.**  `u8⟦4⟧`, read as four of these.  C writes `T a[4]`, putting half the type on each side
of the name, and Go, Rust and Zig all moved it to one side; they put it in front (`[4]T`, `[T; 4]`), which reads as "an array of
four whose elements are these".  After is the order the language already writes `TYPE?` in and reads the way the value is indexed.

**White square brackets.**  The plain ones stay free for whatever wants them next, and the double parenthesis is the collection's:
a collection is found by its key and an array by its place.  No ASCII substitute, because `a[[i]][[j]]` would end in four brackets
no reader could group by eye.

**What a value of a fixed-length array is, is where its elements are.**  The type says how many, so there is nothing else to
carry, and the backend treats such a value as an address.  A dynamic one is two parts, which is the machinery a result and a tuple
already had; the verifier's rules about making and taking apart a value of several parts were generalized from "a tuple" to
"anything `parts_of` says has parts", which is what that function's docstring had claimed all along.

**A dynamic array is a place and a count, and owns nothing.**  Go's slice header carries a capacity besides, because Go has
`append`; this language has no operation that appends to anything, so a capacity would be a word nothing reads.  That is the entry
in the list, and it is the same entry as "let a program say where the elements live", because appending needs an arena to ask.

**The elements of a local array live in the frame.**  That reverses the note left when `alloca` was deleted -- that storage whose
address is taken comes from an arena.  The note is right for a collection, whose lifetime the program manages; it is wrong for an
array whose length is in its type, which lasts exactly as long as the name and which an arena that never frees would leak once a
turn.  Both kinds exist because there are two questions.

**Every access is checked**, and the check is an instruction of its own rather than a property of the read.  What it tests is an
index against a length, and neither is part of the load.  Where both are written down the answer is known while compiling and the
program is refused; C checks nothing, Go and Rust and Zig all check at run time and none of them refuses the constant case at
compile time as a matter of course.

**A function does not answer with an array type, and a variable at the top level of `T⟦⟧` is refused.**  Both are the same
gap: the language cannot say where a value's elements live, so it cannot tell a slice of a parameter -- which is safe -- from a
slice of a frame, which is not.  Refusing both is the conservative answer and the list says what lifting it needs.

**Layout is where the specification's freedom about data first shows.**  An array variable marked `@[cdecl]` is laid out the way
the system's own compilers would; every other one is laid out whichever way is better.  The freedom is used for one thing today --
an array worth reading a word at a time is aligned so that a word can be read -- and deliberately not for the stride, which is what
an index is multiplied by and which the front end works out without knowing which layout a variable ended up with.  `@[cdecl]`
therefore now applies to a variable as well as to a function, which reads well: it is one attribute saying "this is for a world
that has never heard of this language" about whichever kind of definition it is on.

**A defect found on the way**: the pass that drops what nothing reaches asked only which variables are read and written.  An array
is named by neither, since what a value of one is, is its address; a global array would have been dropped out from under the
program that used it.

---

## 2026-09-15T05:00+02:00 — language

**An array has a shape**

Decided on the user's direction: more than one dimension, with the indices separated by commas.

**A shape and not a nesting.**  `T⟦2,3⟧` is one array of two dimensions, not an array of two whose elements are arrays of
three, and `m⟦i,j⟧` asks for a place in it.  That is the array languages' reading -- APL, BQN and NumPy all give an array a
shape -- and this language's first page says its style is theirs.  C, Go, Rust and Zig have only the other reading and spell it
`m[i][j]`.

Both readings exist here and are different types laid out alike: `T⟦3⟧⟦2⟧` is two arrays of three and is indexed with two pairs of
brackets.  Keeping both is not a second spelling of one thing: what differs is what the program says it means, and one pair of
brackets against two is exactly the difference.

**Row-major**, which is what every language but Fortran does.  It is the choice that makes a row of a table a run of elements, and
so the choice that will make taking a row out of one cost a multiplication and no copy.  It also puts the index arithmetic in the
ordinary Horner shape, which for a vector comes to the index itself -- nothing is paid for the generality.

**Every dimension says how many, or none does.**  Half of each would make the parts of a value depend on which half was told,
which is a third shape of value for one case; the case (`T⟦,4⟧`, a run-time number of rows of four) is real and is in the list.

**One index per dimension, and each checked against its own.**  `m⟦0,5⟧` of a two-by-three is outside, though it is within the
six elements there are in all -- which is what a single check against the total would have let through, and what C lets through
with no check at all.  Fewer indices than dimensions is refused rather than read as a row: taking a row out is worth having and is
one decision with slicing a table, and both are in the list.

**A literal is written a dimension deep.**  The outer list is the first dimension, which is the same order the shape is written in
and the order the elements are laid out in.  The shape can therefore be read off a literal that is written with no type beside it,
which is what `let m := ⟦⟦1u8,2u8⟧,⟦3u8,4u8⟧⟧` needs.

**Only a vector is sliced.**  A row of a table is a run and a column is not: its elements are a row apart, which is a stride and
not something a place and a count can say.  NumPy's answer is a stride beside the place and the count, and that is the entry in the
list; until then a run out of a table has nothing to be.

---

## 2026-09-15T09:00+02:00 — language

**Four things a loop can take its values from**

Decided on the user's direction: `foreach` over a set, a dictionary and an array, an array iterated over its outermost dimension,
a dictionary giving a key and a value, and two names taking those apart the way Python's `for k, v in d` does.

**The protocol is three questions, not a call.**  An iterator's `next` answers the next value or a failure; a loop asks that in
three places, and the three places are what the lowering is built around -- is there another (where it tests), what does this turn
give (in the body), what does the next turn start from (at the branch backwards).  Each of the four answers those three inline.
That is not a shortcut past the protocol: a `next` answering a result *is* those three said as one value, so the loop written here
is the loop a user-written iterator will want.

**A dictionary gives a tuple, and two names take it apart.**  Nothing of its own was needed: several names next to each other take
a tuple apart everywhere a tuple is bound, so `foreach k, v := d:` is the tuple plus a binding that already existed.  Go spells the
same thing as two results of `range`, which is a second mechanism for one case; Python spells it as a pair taken apart, which is
this.

**Iterating an array is over the outermost dimension**, which the instruction asked for and which row-major makes cheap: a row is
a run of elements, so a turn is arithmetic on the place and no copy.  That in turn is what made partial indexing worth having --
`m⟦i⟧` of a `T⟦2,3⟧` is a `T⟦3⟧` -- and the refusal recorded a week ago was lifted rather than worked around, since a loop that
could name a row while a program could not would be the compiler keeping something to itself.

Writing still wants an index per dimension.  What an assignment writes is one element, and copying a whole row is not what `←`
means anywhere else; the entry in the list says the first thing that copies one aggregate into another should settle it for
products and arrays together.

**A table has no order and none is promised.**  Where a key lands is where its hash puts it, and growing the table moves
everything.  Python promises insertion order and pays for it with a second array; Go randomises its walk so that no program can
come to depend on an order it never promised.  This promises nothing and pays nothing, and the specification says so -- which is
what lets the tests add up what a walk finds rather than check what it finds first.

**Finding the next entry is a function of the runtime**, not a second loop written into the lowering.  A table's entries are not
all holding keys, so a walk has to step past the ones that are not; doing that inside "is there another" would mean handing the
body what the search found, and the shape has nowhere to put it.  Making it a call keeps the one shape for all four.

---

## 2026-09-15T12:00+02:00 — language

**A member of a tuple is named the way an element of an array is**

Decided on the user's direction: the array's brackets, and one dimension is all a tuple ever needs.

**One shape for one question.**  `t⟦0⟧` and `a⟦0⟧` both ask for a place among several, and a language that spelled them
differently would be asking a reader to learn two shapes for one idea.  Rust and Swift write `t.0`, which needs a rule saying a
number may stand where a field name does; C++ writes `std::get<0>(t)`, because a function's result type may not depend on an
ordinary argument; Python writes `t[0]`, which is this, and can afford an ordinary index only because its tuples are not typed by
member.

**The index must be known while compiling, and that is what a tuple is.**  The members are of whatever types they were written
with, so which one is wanted decides the type of the expression; an index the program worked out would leave that type to be
settled while the program runs, which no type here is.  Saying so as a diagnostic rather than as a syntax rule is deliberate: the
restriction is a consequence of the type system and reads better stated as one.

What counts as known is a literal, with or without a suffix, and a name bound at the top level to a number that cannot change --
such a name *is* that number, and a program that troubled to give it one should not have to write the number again.  A name bound
inside a function is not one even where nothing assigns to it: what it stands for is the value an expression produced, and whether
*that* could have been worked out while compiling is a question about the expression.  Nothing worked out is one yet, because this
compiler folds after the front end has settled every type -- and settling a type is what the index is needed for.

That leaves one asymmetry worth stating: an array's length may not be a name where a tuple's index may.  The difference is only
when the question is asked -- a tuple's index is settled while a body is lowered, by which time every definition has been
collected, and a type is resolved while they still are being.  Accepting a name there today would accept the ones written above the
use and refuse the ones written below, which is order-dependence in a language that has none elsewhere.  Both are in the list.

**A member is read and not assigned to.**  A tuple is registers, not room in memory.  Assigning to one would mean binding the name
to a tuple made of the others and the new value -- which the language can already be told to do by writing that out -- so making
`←` a second way of saying it is a question about what assignment means, and the same question a whole row of an array asks.  Both
are in the list, to be answered together.

**Nothing is read from memory.**  Naming a member is `extract`, which lowers to nothing at all: the member is already in a
register of its own, and this says which one to go on using.

---

## 2026-09-15T15:00+02:00 — language

**A parameter a function may change**

Decided on the user's direction: `fn f(p: mut u8)`.

**`mut` stands before the type**, where a definition puts it, because it says the same thing a definition's does: the name may be
bound to something else.  Rust puts it before the name -- `fn f(mut x: i32)` -- because that is where Rust's `let` puts it; this
language's `let` puts it after the colon, so this does too.  One rule about where `mut` goes, not two.

**It is no part of the type.**  Two functions differing only in it are one signature, carry one symbol and are called the same way;
nothing outside the function can tell, and adding it to a parameter changes no caller.  That follows from what a parameter is here
-- a value the caller handed over and a name the body has for it -- and it is where a parameter's `mut` parts company with a
pointer's: a `ptr<mut T>` says something about a place both sides reach, so it has to be in the type; a name is not shared.

**It costs nothing.**  A parameter arrives in a register and a name bound to a new value is a register, so counting down in the
parameter itself is what a `mut` one is for and is exactly as cheap as counting down in a local made for the purpose.

**The unused-value rule starts applying at the first assignment.**  What a caller hands over is the caller's business and is exempt,
as it always was; a value the body itself put there and nothing read is a value it need not have worked out, which is what that
rule is about.  So the local stops calling itself a parameter the moment it is assigned to.

Compare: C, where every parameter may be assigned and nothing says so, which is why a reader cannot tell a parameter that stays put
from one that does not; Go and Zig, where none may be and a body that wants to count down makes a local of its own -- one line of
ceremony for a thing the language could simply have allowed the reader to see.

---

## 2026-09-14T12:00+02:00 — language

**A tuple handed over as several arguments**

Decided on the user's direction: `f(⁂t)` hands the members of `t` over as arguments, with ordinary arguments allowed before and
after.

**The glyph is `⁂` (U+2042 ASTERISM) and not `*`.**  The user asked for it by name, and the reason it is the right ask is the rule
this language already has about ASCII characters: one spent on an operator is one no future feature can have.  `*` is spent on
nothing here -- multiplication is `×` -- and spending it on this would be spending the last plain character that still reads as
"multiply" to anyone arriving from another language.  The asterism is also literally the picture of what it means: three asterisks
arranged as one mark, several things standing where one is written.

**It is a rule of the argument list, not an expression.**  The parser accepts `⁂` only in front of an argument, so `let q = ⁂p` is
a syntax error (PL4G-3031) rather than a type error.  The alternative -- making it an expression and refusing it everywhere but a
call -- was tried first and discarded: it puts a check in the checker for something the grammar can simply not admit, and it makes
the tree-sitter grammar and the compiler disagree about what an expression is.  Where a thing cannot occur, the grammar is the
place to say so.

**It expands before anything is counted.**  `_handed_over` in `sema/check.py` turns the written arguments into the handed-over
list, and only then does the arity check and the per-argument type check run.  So a 3-tuple spread into a 2-parameter function is
"takes 2 arguments, not 3" -- the complaint the call would have drawn had its arguments been written out -- and there is no second
family of diagnostics for calls that spread.  Exactly two diagnostics are new: the operand is not a tuple (4464), and the glyph
outside a call (3031).

**It is entirely a compile-time matter.**  A tuple's length and member types are in its type, so the expansion happens in the
checker; a tuple is its members held separately rather than a thing in memory, so the generated code shows no trace of the glyph
and a spread call is the same code as the call written out.  This is the half of the feature that costs nothing, and it is the
only half this language can have: nothing here is variadic, so there is no runtime shape for a spread to turn into.

Compare: Python's `*args`, which this is named after and which must work at runtime because its calls are variadic -- the cost
being that the arity error arrives when the call is made; JavaScript's `...`, which spreads any iterable and so is likewise a
runtime matter, and which doubles as a collection-building syntax this does not; C++'s parameter packs, which expand while
compiling as these do, but belong to templates -- a pack is a thing a declaration introduces, where a tuple is an ordinary value
any expression may produce; Lisp's `apply`, which is a function taking the list last rather than a syntax admitting it anywhere,
and so cannot have arguments after it; and D's tuple auto-expansion, which needs no glyph at all -- the closest design to this one
and the one deliberately not taken, since a tuple that silently becomes several arguments makes `f(t)` and `f(a, b)` the same call
and leaves a reader no way to see which was meant.

What is left open, and is in [TODO-language.md](../TODO-language.md): spreading an array, whose length is in its type when it is
fixed and so could expand the same way; spreading into a tuple literal, `〈⁂a, ⁂b〉`, which is how a tuple is joined to another and
which nothing yet needs.

---

## 2026-09-14T14:00+02:00 — language

**What else the asterism spreads**

The two things the entry above left open, both now done: a fixed-size array spreads into a call, and a spread stands among a
tuple's members.

**An array spreads when its type says its length, and not otherwise.**  The asterism undoes something that travels as one value
and is several, and the compiler has to be able to count the several while compiling -- what it expands into is written into the
program.  A tuple's type names its members one by one; a fixed array's type says how many there are; a dynamic array's does not,
and carries the length beside the elements instead (4465).  That line is exactly the fixed/dynamic line the language already
draws, so no new concept was needed to say where the glyph stops.  Rejected: making the dynamic case work by generating a call
per possible length, and making it work by passing a count -- the first is code proportional to a bound nothing states, and the
second is a variadic call, which this language does not have.

**A table spreads into its rows**, not into its elements, matching `foreach` over a multi-dimensional array.  An array of rank
two is several rows in the same sense a tuple is several members, and a row is a run of elements, so the expansion is arithmetic
on the place and no copy.  The alternative -- flattening to elements -- would make `⁂` the one place in the language where
row-major order is visible to a reader, and would make the rank of what is spread something the reader has to know to count the
arguments.

**A tuple's members are the language's other list, so they admit the glyph too.**  This is what joins one tuple to another and
what extends one, neither of which could otherwise be written at all: `〈⁂a, ⁂b〉`.  It cost a rule rather than a mechanism --
`_pieces_of` already existed for calls, `_parse_spreadable` is what both lists parse their entries with, and the grammar names
one `_spreadable` rule from both places.  What a spread leaves are members like any other, so a tuple made this way has the type
it would have had written out.

**A tuple still has at least one member.**  Spreading an array of no elements is the one way to arrive at none, and it is
refused (4466) rather than admitted.  Rejected: a zero-member tuple type, which would be a type no program can write down --
`〈〉` is not a tuple literal -- reachable only through a spread, and which would have to answer what taking it apart means.

Compare: Python, where `*` spreads any iterable and `f(*[])` is simply a call with no arguments, because the count is a runtime
matter throughout; JavaScript, the same; C++, where a parameter pack of length zero is ordinary and `std::tuple<>` exists, the
language having decided the other way on the empty case; and D, whose static arrays auto-expand in a call with no glyph at all --
again the closest design and again not taken, for the reason the entry above gives.

What is left open, and is in [TODO-language.md](../TODO-language.md): what order a call's arguments are worked out in, which the
language has not said and which the expansion makes visible.

---

## 2026-09-14T16:00+02:00 — language

**Leaving a loop and repeating it, and what a loop is called**

Decided on the user's direction, which set the hard part: `break` and `continue` both name the loop they mean, every time.  What
was left to decide was how a loop comes to have a name.

**The label is `§name`, written between the loop's keyword and what the loop runs on.**  Two things had to be settled: where it
goes, and what marks it.

Where it goes is where every language that has labels puts it and where a reader looks -- before the loop, not after its body.
What marks it is forced: `while outer x` with no marker is already a loop over a name that is true, so the label and the
condition cannot be told apart without one.  That is exactly why Rust writes `'label` where Java writes `label:`; Java gets away
with the plain form because its condition is parenthesised, and this language's is not, for reasons the `if` section gives.

The marker could not be a plain character.  `label:` is what the layout notation already uses to open a body, so `outer: while`
would read as a block; a leading `'` is one ASCII character spent, which this language does not do; `@` belongs to attributes.
`§` is the mark for a named division of a text, which is what a label is, and it is free.

**Rejected: an attribute, `@[label(outer)]`.**  Attributes already attach to statements, and "everything said about one thing is
written in one list" is a principle here, so this was the real alternative.  It was not taken because an attribute states a
quality of a thing, and a label is a binding: it introduces a name that something else refers to, and nothing else in an
attribute list does that.  It also puts the name on its own line, above the keyword, when what a reader wants to know at the
keyword is which loop this is.

**Rejected: making the label optional**, as Java, JavaScript, Go, Perl and Odin all do.  The user required it, and the reason it
is the right requirement is what this language is for: an unlabelled jump means the loop nearest to it, so wrapping a body in a
new loop silently changes what every jump inside it does -- and wrapping a body in a loop is an edit a generator makes.  With the
label required, that edit either keeps meaning what it meant or is reported (4467).  The cost is that a single loop with one
`break` must be named; the cost of the alternative is a class of silent miscompilation of generated code.

**`continue` is the end of the body, not a jump to the test.**  For a `foreach` it applies the iterator's step first, which is
what falling off the end of the body does; anything else would make `continue` skip the advance and loop forever.  For a `while`
there is no step and the next turn is the condition.

**A label is named by something inside its loop, or it is reported** (4469, a warning).  A label exists to be named; one that
nothing names says nothing, and one that was meant to be named means something else is being named instead.

**No loop carries the label of a loop it is inside** (4468), because the inner would hide the outer and leave no way to name the
outer from within -- which is the one thing labels are for.  Two loops neither of which is inside the other may share a label.

**A `break` hands nothing over, so a loop is still not an expression.**  Rust's `break 'label value` makes `loop` produce a
value, which is worth having and is a separate decision: it would make a loop an expression, and the way through that runs the
body no times would then have to produce something too.  It is in [TODO-language.md](../TODO-language.md).

Compare, beyond the above: Ada's `exit Outer when ...`, the closest in spirit -- the loop is named and the exit says which; C and
C++, which have no label and reach for `goto`, the construct this language does not want; Python, which has neither and where
leaving two loops means a flag or a function; and Zig, whose `break :label` marks the label at the jump but not at the loop,
which this does not follow because a name and its use are spelled alike here.

---

## 2026-09-14T18:00+02:00 — language

**Everything is worked out left to right**

Decided on the user's direction, and written into the specification: a call's arguments are worked out in the order they are
written, and so is everything else a program writes several of in a row.

**The rule is one rule, not a rule about calls.**  Arguments, a tuple's members, an array's elements, a set's and a dictionary's
entries, and an operator's two sides all follow it -- a dictionary's being worked out key, value, key, value, the order they
stand in.  Making it a rule about calls only would have left a reader asking the same question again at every other list, and
there is no construct here that would have wanted a different answer.

**Precedence does not move anything.**  `a + b × c` groups as `a + (b × c)` and still works `a` out first: how an expression
groups decides what is worked out *from* what, and the writing decides what is worked out first.  These are two different
questions and it is worth saying that they are, since every language that leaves the order open invites the reading that the tree
decides it.

**Each thing is worked out once**, which had to be said with the order, and was the half that was actually broken: a set's or a
dictionary's entries were each lowered twice -- once to learn what type they shared, once to put them in the table -- so a call
written as an entry was made twice.  The spread expansion had the ordering half wrong for calls, running a spread operand before
an argument written to its left.

**What C keeps by leaving it open is worth nothing here.**  The freedom to interleave two arguments mattered when registers were
few; a compiler that wants it still has it, since it may reorder anything a reader cannot tell apart, and what a reader can tell
apart is precisely what this rule names.  What the freedom costs is that a generator has to avoid relying on an order by
accident, which is not a thing a program writing this language can be careful about -- so the language is careful instead.

Compare: C and C++, unspecified, and C++17 still so for a call's arguments -- `f(i++, i)` is the classic trap; Java, C#,
JavaScript, Python and Rust, all left to right, having decided the same way; Go, which fixes the order of the *calls* inside an
expression and leaves the rest of the operand order open -- half of this rule, and the half that matters least here, since a call
is not the only thing that can stop the program; OCaml, right to left, consistent and the opposite of the order the text is read
in; and Scheme, deliberately unspecified so that no program may depend on it -- defensible where most things do nothing, and not
available here, where arithmetic can stop the program.

---

## 2026-09-14T20:00+02:00 — language

**What a loop comes to**

Decided on the user's direction: a `break` may hand a value over, which makes a loop an expression; a loop may take an `else`
arm; otherwise what it comes to is a result; and everything it may come to is of one type.

**The `else` arm and the result are one decision, not two.**  A loop has a way through that no `break` took, and that way has to
give something for the loop to be an expression.  The `else` arm is that way's value where one is written, and a failure where
none is -- so `T?` with no arm and `T` with one.  That is why Python's `while ... else`, which only runs, and Rust's `break value`,
which only applies to the loop that cannot end on its own, are halves of the same thing: joining them is what makes a loop an
expression for *every* loop rather than for the one shape whose ran-out way does not exist.

**Rejected: making a loop produce a value only where its condition is a constant truth**, which is Rust's answer by construction
-- `loop` has no other way out, so `break` is the only way and the value is unconditional.  It would have avoided the result type
entirely, and it would have meant that adding a condition to a loop silently changes what it comes to, which is the same class of
edit the required labels exist to protect.

**Rejected: a value written on the loop for the ran-out way**, `while c: ... otherwise 0u8`.  It is the `else` arm with less in
it: the arm can compute the value, which is what a search that has to report how far it got needs.

**A `break` hands a value over exactly where the loop's value is read**, and that is checked both ways.  Where it is read, every
`break` hands one over (4470) and something has to give one at all (4472); where it is not, writing one is an error (4471)
rather than a value quietly worked out and dropped.  The alternative -- letting a loop produce a value nothing reads -- would have
made `break §a f()` mean something different depending on where the loop stood, which is the kind of thing this language reports.

**One type for everything the loop may come to** was the user's requirement, and it is also what the shape demands: the block the
loop ends at is reached down every `break` and down the ran-out way, and it reads one value.  Where the loop is being used as
something the type is that; otherwise the first `break` to give a value settles it and the rest are held to it (4473).

**A loop became an expression node**, as `if` and `match` already are, with a statement loop being an expression statement --
so one mechanism serves both and `_lower_stmt` treats it exactly as it treats an `if`.

Compare, beyond Rust and Python above: Zig's `for (xs) |x| { ... } else value`, which is this design, reached from the same place
-- an `else` that had to mean something once the loop was an expression; Kotlin and Scala, which make most things expressions and
leave loops out, so a search over two dimensions goes back to a variable set before the loop; Common Lisp's `loop ... finally
(return v)` with `return-from`, which does all of this and much more in a sublanguage of its own; and Ada, Go, Java and C, where
a loop is a statement outright and the value travels in a variable the loop assigns -- which works, and which makes the reader
prove to themselves that every path through the loop assigned it.

---

## 2026-09-14T22:00+02:00 — language

**The written type reaches what was written**

Decided on the user's direction: an unsuffixed value works in a `break` of a loop with no `else` arm, and in every place like it.

**The bug was one bug, and it was general.**  `_lower_into` handed *nothing* down when a result type was wanted, so that a plain
`T` could come back and be wrapped into `T?`.  That worked for everything with a type of its own and failed for the one thing that
has none -- a literal with no suffix -- in every position a result is wanted: a definition, an assignment, an argument, what a
function answers with, the arms of an `if`, and what a loop comes to.  The loop break was where it was noticed; fixing only the
loop break would have left the other six.

**The rule is split in two, and that is what made it general.**  `_aiming_at` says what is wanted of something that can only ever
*be* an answer -- a literal, an operator's two sides, the members of anything written out -- which is the answer's type, the result
being made around it.  `_accepts` says what a check will take -- the result type, or its answer type.  Everything that chose a
type from the context now asks the first, and everything that compared types now asks the second.

**Rejected: deciding per expression whether it can produce a result**, by looking at its syntax.  A call can, an operator usually
cannot -- except that `÷` and `%` answer with results, so even that much is wrong, and a rule that must be right about every
expression kind is one that will be wrong about the next one added.  The two predicates need to know nothing about expression
kinds: one is asked by things that construct values, the other by things that check them.

**Rejected: a `Wanted` record carrying "this type or its answer"**, threaded through `_lower_expr`.  It is the same information
the result type already carries, and it would have touched every signature in the checker to say what two four-line predicates
say.

Two defects came out with it, both older than this change and both found by writing a test that used the feature in every
position at once.  **`mem.start` was planted wherever memory was first asked for**, which could be inside an arm of an `if`; every
later use then read a value that arm does not reach, so an `if` followed by a loop failed the verifier outright.  It now goes at
the top of the entry block, which is where the chain begins.  And **a block expression took a postfix**: an `if`, a `match` or a
loop in the layout notation ends with no line ending after it, so a following statement beginning with `(` was read as a call on
what the block came to.

Compare: Zig's error unions `!T`, which accept a plain `T` as the success and propagate the expected type into the expression the
same way -- this is Zig's answer; C++'s `std::expected<T, E>`, which converts implicitly from `T`, and where the equivalent
question is answered by overload resolution rather than by a rule about what is wanted; Rust, which requires `Ok(x)` and so never
has the question, at the cost of a constructor at every success; and Haskell, whose bidirectional type checking is the general
form of "what is wanted travels down", with inference filling in what neither side says -- which this language deliberately does
not have, every literal without a suffix needing a context that gives it a type.

---

## 2026-09-15T00:00+02:00 — language

**Answering with more than the registers hold**

Decided on the user's direction: how a function hands back an answer larger than one word is a property of that function; the
first and default choice answers two values in registers and everything larger through storage the caller provides.

**The choice belongs to the callee**, which is the part worth writing down.  It is part of the convention for the same reason the
argument registers are: what a caller must do to receive an answer is settled by the function that answers, so a program may hold
functions that answer different ways and call each the way it expects.  `ReturnStyle` is therefore a field of `Function`, beside
`cconv`, and not a property of the target or of the convention description.

**Two is the number**, because it is what the x86-64, AArch64 and RISC-V ABIs also answer in registers.  Making the first choice
the familiar one means the obvious alternative -- "as many as the convention has return registers" -- is still available as a
second style rather than being what the first one silently was.

**The place goes last among the arguments.**  Every system ABI passes it first, and each does so because its pointer has to be in
one known register whatever else is passed.  This convention is the compiler's own and has no such constraint, so putting the
place last leaves every argument the program wrote in the register it already had -- which matters because a function that answers
this way is otherwise called exactly as it was.

**It is carried out by a pass, not by the three instruction selectors** -- nothing about it differs between targets, and doing it
once is what makes the three agree by construction -- and not by the checker, because the signature it produces is not the
signature the program wrote.  The place is not an argument any program can pass, and no rule about arguments should have to know
that one of them is not one.

Compare: C and C++ on every ABI, where an aggregate too large for registers is returned through a hidden pointer the caller
provides -- this is that, with the pointer moved to the end; Go, which returns multiple values on the stack and is the design
this deliberately does not copy, since it spends stack on the two-value case that fits in registers; Rust, which uses the platform
ABI and so inherits the hidden first pointer; Swift, whose `@out` convention is the same shape with the indirection decided by the
type's size and its witness table; and Zig, where the choice is the compiler's and undocumented, which is what this would have
been had it not been made a named property of the function.

What is left open, and is in [TODO-language.md](../TODO-language.md): the attribute that will let a program choose a style, which
waits for there to be a second one.

---

## 2026-09-15T02:00+02:00 — language

**An answer that has to be taken**

Decided on the user's direction, which reversed the to-do item that prompted it: a call's answer must be taken by default, and
`@[can_ignore]` on the function says a caller need not take it.

**The default is reversed from every language that has this.**  C and C++ have `[[nodiscard]]`, Rust has `#[must_use]`, and all
of them make silence the default and the requirement the exception -- because they are written by people, for whom the common
case is the one worth making quiet.  This language is emitted by a generator, for which the common case is the opposite: a
generator that emits a call and drops its answer has a defect, and a defect that is silent by default is one nobody finds.  So
the rule is on for every function and `@[can_ignore]` is what `#[must_use]` would have been.

It is an error rather than a warning, which is the same choice 5005 already made about a statement whose value goes nowhere, and
for the same reason written down there.

**`@[can_ignore]` is on the function, not on the call**, because that is where the fact lives.  A function whose answer is a
convenience -- the count it updated, the thing it wrote -- is one every caller may ignore; saying so once says it where it is
true, and saves the places that would otherwise repeat it.

**`_` covers the other case**, where most callers want the answer and one does not.  It is an assignment -- `_ ← f()` -- and
therefore needs nothing new in the grammar.

**`_` is not a variable, and the three rules follow from that.**  No definition is needed and none is allowed (4476), because a
definition would make it an ordinary local of that scope: one that could be read, and that would need `mut` to be assigned again,
which is a second meaning for one spelling.  Nothing reads it (4475), there being nothing there.  And what is assigned to it must
produce a value (4477), since dropping nothing is not a thing to say.

Rejected: `let _ = f()`, which is how Rust and Python spell it.  Both treat `_` as a *pattern*, so a definition that binds
nothing is an ordinary definition; here a definition defines, and making one spelling mean "define" and another "do not" is the
kind of thing this language reports rather than admits.  Go's blank identifier is an assignment target exactly as this is, and is
what this follows.

Rejected: an attribute on the statement, `@[ignore(4474)]`, which already works and was the reason to think nothing more was
needed.  It says "do not tell me about this diagnostic", which is a statement about the compiler; `_ ←` says "this value is
deliberately dropped", which is a statement about the program, and the program is what a later reader is trying to understand.

`_` is already the name a `foreach` and a `match` arm use for a value that is not wanted, so this is the same meaning in a third
place rather than a new one.

---

## 2026-09-15T04:00+02:00 — language

**What a function may change**

Decided on the user's direction: a function is pure unless `@[impure]` says otherwise; a pure function may not change anything
that outlives the call; a function calling an impure one is impure; and a pure call whose answer nothing reads is not made.

**The default is the strict one, which is the whole point.**  Every language that has this makes purity the thing you ask for --
D's `pure`, GCC's `__attribute__((pure))`, Rust's `const fn` for its own question -- because they are written by people, for whom
the annotation is a cost paid per function.  A generator pays it once per kind of function it emits and knows which kind it is
emitting, so the cost is near zero and the value is in what the default buys: every function that says nothing is one a caller
may rearrange.  A default that has to be asked for is one most functions would never be given.

**Purity is a property of everything a call reaches**, so the attribute is transitive by requirement rather than by inference
(4480).  Inferring it was the alternative and is what Zig does: the compiler can see the whole program, so it could work out which
functions have effects and never ask.  Not taken, because the answer would then be invisible in the source -- a reader could not
tell whether a function is one a caller may drop without following every call it makes -- and because a change deep in the program
would silently change what is true of a function far away.  Written down, it changes the signature, and changing a signature is a
thing the program can be made to say.

**What counts as a change is asked of the value, not of the syntax.**  `_made_here` walks a place back to a `frame` -- storage
this call made and will lose -- so a row of a local array, an element of one and a slice of one all answer correctly without being
listed.  An array a function was handed belongs to the caller, so writing into one is an effect; that is stricter than C, where a
`pure` function may write through a pointer it was given, and it is the right strictness for a language with no way to say that a
parameter is not shared.

**Making a collection is a change**, since it takes room out of an arena the next call will find shorter.  That makes any
function building a set or a dictionary impure, which is a real consequence and the honest one: the arena is a variable at the top
level and allocating writes it.

**The optimization cost one line.**  `CallInst.has_effects` asks the callee's attribute instead of answering `True`, and
dead-code elimination -- which already drops what nothing uses and what has no effects -- does the rest without being told what
purity is.

**A call that is not made is recorded in the decision log** (`drop-call`), even though the value it produced had no name and the
sweep records only named things otherwise.  It is the largest thing that pass does -- what the program asked for was a function to
run, and it does not run -- and the reader it is for is the generator that emitted the call and cannot find it.  The entry says
why as well as what, because the why is the absence of `@[impure]` on a function the reader wrote and can change.

**A dropped call takes its faults with it**, which is stated rather than hidden: an overflow inside a call nobody made cannot be
reached.  It follows from the call being removable at all, and a program that wants the check wants the answer, which keeps the
call.  The alternative -- a pure function that may still stop the program, so calls must be kept -- would have made the attribute
worth nothing today.

Compare, beyond the above: Haskell, where purity is the default and effects live in the type, the same default reached by a far
larger mechanism; D's `pure`, an attribute, checked, with calls free to be elided, which is the closest existing design and which
still defaults the other way; and C and C++, whose `pure` and `const` attributes are promises nobody checks, so a wrong one is
undefined behaviour rather than a message.

Sixty-one language tests and twenty compiler sources gained the attribute, which is the churn the user predicted and is itself a
fact about the default: what needed it is every program that does something.

---

## 2026-09-15T06:00+02:00 — language

**Walking an array**

Decided on the user's direction: `@[listable]`, which is the Wolfram Language's `Listable`.  An array handed where one of a
function's elements is wanted is walked, the function is called for each, and the answer is an array of the same shape.

**The attribute is on the function, not at the call.**  Julia takes the other decision -- `f.(v)` spells the walk where the call
is written -- and it is a coherent one: it needs no attribute, and a caller may walk any function at all.  This goes the other way
because what it means to hand a function an array is the function's own business, the same reason `@[impure]` and the return
style are the function's: a caller that had to say it would have to know, and a generator emitting the call would have to carry
that knowledge to every call site.

**The parameter's type says when the walk stops**, which is what a language with element types can do and Wolfram cannot: a
Wolfram list has no element type, so `Listable` walks all the way to the leaves and there is nothing else it could do.  Here
`doubled(m)` walks a table twice and `total(m)` walks it once, and the difference is entirely in what the parameter takes.  That
is the feature this design has that the one it is named after does not.

**An argument that is not an array is handed to every call**, which is Wolfram's broadcasting and is what makes `added(v, 10u8)`
read the way it looks.  Rejected: NumPy's rule, which extends shapes by rank and length so that an array of one row stands for
many.  It is powerful and it is the thing people get wrong about NumPy; here an argument is walked or it is not, and the shapes
that are walked agree exactly (4481).

**Every dimension walked is one the type states** (4482).  What the answer is an array of is the shape that was walked, and the
room for it is taken in one `frame` before the calls are written, so a length the type does not say is a length there is nowhere
to put the answer in.  That is the same line the language already draws between the two kinds of array, arrived at again.

**The calls are written out, one per element, rather than made in a loop.**  A loop wants the answer's storage and its index
worked out while the program runs, which is the machinery a dynamic array wants; they are one piece of work and neither is here.
What it costs is code proportional to the shape, which is the honest price of the simplest thing that is correct, and it is why a
stated shape is required rather than merely convenient.

**Operators are not listable yet**, and nothing here is in their way: an operator is not a definition, so there is nowhere to
write the attribute.  When the language lets a program define one, the attribute goes on that definition and the mechanism is the
one already here.  Making the built-in operators listable -- `v + w` element by element -- is a different decision and a larger
one, since it is about the language rather than about a function, and is in [TODO-language.md](../TODO-language.md).

Compare, beyond the above: APL and BQN, which thread every scalar function over arrays by default and need no attribute, their
whole design being arrays -- a language that is about something else cannot take that default without making every function's
meaning depend on what it is handed; and Fortran's elemental procedures, which are this exactly, declared on the procedure, with
the conformance rule this one has.

---

## 2026-09-15T08:00+02:00 — language

**The operators walk arrays**

Decided on the user's direction, which is the to-do item the `listable` work left open: the arithmetic, the comparisons, the
bitwise operators and the logical ones walk an array the way a marked function does.

**No attribute is written on them.**  An operator is not a definition and there is nowhere to put one; and every operator that
would be marked would be marked, which is what saying it of all of them says.  So the rule is the language's rather than each
operator's, which is the one place this parts company with the function case -- and it parts company for the reason the function
case gave for putting the attribute on the function: what it means to hand an operator an array is not something a caller should
have to say, and an operator has no other place to say it.

**The rule is the same rule**, asked of an operator whose operands are never arrays instead of a parameter whose type says what
it takes.  `_walk_operands` and `_walked` share `_one_less`, `_one_of` and the two diagnostics; what differs is only where the
stopping point comes from, which for an operator is "not an array" and for a function is the parameter.

**`and` and `or` are not walked, and could not be.**  Which side is worked out is what they are about, and over an array there is
no such thing as which side: the first element might decide it one way and the second the other.  A program that means the walk
writes `∧` and `∨`, which work out both sides -- which is the distinction those two spellings were introduced for, arriving here
with a second thing to say for itself.

**An operator walks by being lowered again**, once per element, with a node holding an already-worked-out value standing where
each operand was written.  Rejected: pulling the scalar half of each operator out into a function the walk could call.  It is the
obvious shape and it would have meant every check an operator makes -- the exact-float warning, the folding, the saturating
forms, which predicate a signed type compares with -- either moving or being duplicated, and a check that exists in two places
is one that will differ in two places.  Lowering the operator again is the same code by construction.

Compare: APL, BQN and Uiua, where every scalar function threads over arrays and no operator is marked because the whole language
is this; NumPy and Julia, where the operators on an array are separate definitions on an array type, so the question is answered
by dispatch rather than by a rule -- which works because those languages have a way to write such a definition and this one does
not yet; Fortran, whose elemental intrinsics are exactly this and whose operators likewise work element by element on
conformable arrays, with the conformance rule this one has; and C, C++, Rust and Go, where none of it happens and `v + w` on two
arrays is either a pointer sum or a compilation error.

---

## 2026-09-15T10:00+02:00 — compiler

**Microarchitecture levels**

Decided on the user's direction: `--mclevel=LEVEL`, read by each target for itself; x86-64 takes `v1` to `v4`, defaults to `v4`,
and has a program check for its level at startup with `CPUID` rather than through a file.

**The option is one option and the names are the target's.**  A level is not a property compilers share: they are the names one
architecture's own documentation gives to what a processor of a given age can do, and there is no meaning to `v3` on AArch64.  So
the driver validates nothing itself -- it asks the target what it has and reports either that there are none (1012) or that this
is not one of them, with the list (1011).  Rejected: a shared enumeration of "levels" across targets, which would have had to
invent a meaning for each name on each architecture.

**The default is the newest, which is the aggressive choice and the right one.**  A program built for v4 that lands on an older
machine says so in one line the moment it is started; a program built for v1 runs everywhere and quietly leaves twenty years of
instructions on the table, with nothing to tell anyone.  Which of those two a reader would rather have found out about is not a
close question.  The cost is borne by the other half of this: without the check, the aggressive default would mean an illegal
instruction in the middle of somebody's afternoon, and that would make the conservative default the only defensible one.

**`CPUID` and not `/proc/cpuinfo`.**  The user asked for the instruction and it is what the architecture provides for exactly
this question: answered the same way on every operating system, needing nothing to be mounted, and unable to be out of date about
the processor the program is actually running on -- which a file the kernel wrote can be, on a machine whose processors differ or
whose kernel was told to lie.  It is also the only one available to a program that depends on nothing from the system, which this
compiler's output is.

**A leaf is asked to exist before it is asked anything**, leaf zero and leaf `0x80000000` answering with the highest ordinary and
extended leaves.  A processor old enough not to have leaf seven is old enough not to have what leaf seven reports, so the
existence check and the feature check give the same answer -- but reading a leaf that does not exist gives whatever the highest
one does, which is a wrong answer rather than no answer.

**It exits rather than trapping**, which is where this parts company with how the language reports a fault.  A fault is the
program doing something it cannot; this is the program being started on a machine it was not built for, and nothing inside it has
gone wrong.  A signal would say that something had.

Compare: GCC and Clang's `-march=x86-64-v3`, which is where the names come from and which generate for the level and check
nothing -- the resulting `SIGILL` being what the level exists to explain afterwards; glibc's `ld.so`, which does check, through
`CPUID` in the loader, and can pick between several builds of a library because there is a loader to do the picking; Go, which
checks a small set of features in its runtime at startup and prints a line very like this one; and the Linux kernel, which checks
at boot and prints what is missing.  This follows the last two: the check belongs in the program when there is nothing else to
put it in.

**Nothing generated uses a level yet**, every instruction this compiler emits being in v1.  The check is worth having before the
code that needs it, since it is what makes adding such code a change to one place -- and having it now is what makes the default
defensible now.

---

## 2026-09-15T12:00+02:00 — language

**Picking with a mask**

Decided on the user's direction: an array indexed by an array of truth values is picked from; the selection may be assigned to;
picking answers with an array that may be smaller; and for a table the mask may pick rows or elements according to its own shape.

**A shape may be stated in part**, which this required and which the language had explicitly forbidden (4456, now gone).  The
rule said an array carries its whole shape in the type or none of it, and the reason given was that half of each would be "a
second kind of array for a case nothing has asked for".  Picking rows is that case: what it answers with is however many rows,
each as wide as the table was, which is `u8⟦,3⟧` and is not expressible any other way.  The representation already allowed it --
a shape is a tuple of "how many, or nothing", and a value carries a count per dimension either way -- so what changed was one
refusal and one check: a length may be let go of, never exchanged for a different length.

**Which of indexing and picking was meant is never a question about how it was written.**  An array of numbers indexes and an
array of truth values picks.  Rejected: a glyph of its own for picking.  The types already say it, and a second spelling for
"look in this array" is the thing this language avoids.

**The mask's shape decides how much it picks**, rather than an operator or an axis number saying so.  A mask of one dimension over
a table picks rows and one of two picks elements, which is NumPy's rule and is the one that needs nothing written down: the shape
of the thing you already have is the shape of the question you are asking.  APL's compress and Fortran's `PACK` both take the
axis as a separate thing to say.

**Reading and writing are one spelling.**  `v⟦m⟧` on the right picks and on the left writes, which is what NumPy does and what
Fortran deliberately does not -- `PACK` is a function and `WHERE` is a statement there.  What is written on the left of `←` and
what is read on the right should not need different names.

**The room is the whole array's and is this call's own.**  No more can be picked than there were, so a `frame` of the array's own
size is enough, and nothing is allocated -- which keeps picking pure and is why the array picked from has to state its shape
(4485).  Rejected: allocating the exact size from an arena, which would have made every pick impure and every picking function
`@[impure]`, for room that is given back when the call ends anyway.

**Neither half branches.**  Picking writes each thing where the count has got to and advances the count by the mask, so a thing
that was not picked is written where the next one writes over it; assigning spreads the mask to all ones or all zeros across the
element's width and writes `(old & ~m) | (v & m)` to everything.  Both cost the same whatever the mask holds, which is worth
having for its own sake and is what makes the written-out form reasonable.  The spread wants an integer, so an array of
floating-point elements is refused for now.

Compare, beyond the above: MATLAB's logical indexing, which is this; Julia's, likewise, with the same "a mask is what a
comparison over an array gives you" idiom that makes this read well here too; and C, C++, Rust and Go, where none of it exists
and the loop is written out by hand each time.

---

## 2026-09-15T14:00+02:00 — language

**Exit statuses the runtime reserves**

Decided on the user's direction, and it reverses a decision this compiler had already made: a program the runtime stops exits
with a status, never through a signal, and 64 through 127 are reserved for those stops.

**What the reservation buys is that all three cases can be told apart.**  0 to 63 is the program's own, 64 to 127 is the
runtime's, and 128 to 255 is the shell's way of reporting a signal -- so a caller can distinguish a program that chose to fail, a
program the runtime stopped, and a program that really died of a signal, without knowing anything about the program.  No language
compared with here has a range reserved on *both* sides, which is what lets there be three cases rather than two.

**This overturns the trap.**  A fault used to end with an illegal instruction, and the reason written down for it was that a
signal hands a debugger the stack as it stood, while "a status would say less and would be indistinguishable from a program that
meant to exit with it".  The second half of that was the real argument, and the reservation answers it: 64 through 127 are nobody
else's, so a status does say it.  The first half survives as a real cost -- a debugger is handed a process that has already
exited -- and it is outweighed by what a signal costs everyone who is not running a debugger, which is every caller: a shell
reporting 132 cannot be told from a program that exited with 132.

**64 is the general one** and everything a fault reports leaves through it.  What went wrong is in the message, which names the
operation, the function and the line; a number could only say less than that, so distinguishing overflow from an index out of
range by status would be paying a number for something already said better.

**65 is the processor not being the one the program was built for**, which earns a number because it is the one stop that happens
before the program has run at all, and because what a reader does about it is different in kind: build for an older level, or
find a newer machine.  It had been exiting with 1, which is in the program's own range and was wrong the day it was written.

**A program may still return a status in the reserved range.**  The startup function answers with a `u8` and every value of one
is a status; refusing 64 to 127 would mean a rule about a number in a language that otherwise has none.  What the reservation says
is what a program doing that gives up, which is its caller's ability to believe it.

Compare: `sysexits.h`, whose 64 through 78 this borrows both the range and the starting number from, and which is advisory where
this is the compiler's own -- so this runtime can actually keep it; the shell's 128 plus the signal number, which is why the top
range is spoken for and is not something this chose; Python, which exits 1 for an uncaught exception and so cannot be told from a
program that meant to; and Go, which exits 2.

---

## 2026-09-15T17:20+02:00 — compiler

**A narrow sum or difference is computed narrow, and the flags say whether it fit**

Decided on the user's direction: arithmetic on an eight-, sixteen- or thirty-two-bit integer uses the instruction for that width
rather than a wider instruction followed by a comparison, and an architecture that has flags then reads the flags.

**What it replaces.**  A trapping addition of a `u8` used to be `movzbl` on each operand, a thirty-two-bit `add`, a `cmp` against
255 and a branch -- four instructions and a constant to check one addition.  It is now `add %cl,%al` and a branch on the carry
flag.  A signed one is the same with the overflow flag.  Nothing is widened, nothing is compared, and the answer is already
inside its type because it was computed there.

**The flags are the point.**  Every architecture that has them writes, for free, exactly the two facts the check wants: whether an
unsigned operation carried out of the top or borrowed into it, and whether a signed one came out with a sign its operands did not
call for.  Asking the answer afterwards is reconstructing what the hardware already said.

**Which widths this covers is the architecture's own answer, and each backend states it.**  x86-64 has arithmetic at all four
widths, so all four.  AArch64 has it at thirty-two and sixty-four; a byte or a halfword there has no instruction to write flags
about, so those two widths keep the old path.  RISC-V deliberately has no flags -- the ISA's stated reason is that they are a
serialising dependency between instructions -- so it keeps the old path at every width, and the old path had to stay for exactly
that reason.

**A product is left out on all three.**  Whether a multiplication went past is in the upper half of the product, which is a second
instruction everywhere and on x86-64 a form with a fixed pair of registers; widening and comparing is cheaper and constrains
nothing.  x86-64's two-operand `imul` does set the overflow flag, but only for a signed product, so taking it would buy one of the
two cases and leave both paths in place.

**It uncovered a real defect.**  The shared code asked *which* operation it was by comparing against the saturating opcode, which
is `false` for the trapping one -- so a trapping addition took the code written for a subtraction.  The widest signed and
unsigned sums therefore did not notice they had gone past: `9000000000000000000i64 + 9000000000000000000i64` ran on and answered
a negative number on all three targets.  Every such test now names the ordinary operation the two are built from, and two
language tests hold it.

Compare: C, where the check is the programmer's and the idiom is exactly the reconstruction this stops doing, with
`__builtin_add_overflow` added later to let the compiler use the flags; Rust, whose debug builds check every arithmetic operation
and which lowers `checked_add` to the narrow instruction and the flag; Zig, the same with `@addWithOverflow`; Go, which wraps and
so asks nothing; and Swift, which traps by default and reads the flags to do it.  What none of them has that matters here is
RISC-V's position, which is that the flags are not worth the coupling -- so any compiler wanting this check has to carry both
paths, and saying which widths an architecture answers for is what keeps that to one line per backend.

---

## 2026-09-15T21:40+02:00 — compiler

**An operator over an array is one operation over a whole run of elements**

Decided on the user's direction: the arithmetic, the bitwise and the logical operators are done to a whole run at once where the
machine has registers that hold one, as wide as the machine allows, and `--mclevel` is what says which instructions it has.

**The decision that shapes everything else is where the choice is made.**  The front end asks the question of the whole run --
one addition of sixteen lanes -- and a step in each backend brings that down to what that machine has.  It is not the front end
that asks how wide a register is, and it is not a pattern-matcher that notices a run of sixteen additions afterwards.

That the front end does not ask is what keeps a program's meaning out of the machine's hands: the same IR is produced for all
three targets and for every level, and a target that can do nothing still compiles it, an element at a time, which is what the
program said in the first place.  That a pattern-matcher does not notice is the other half: the information that sixteen
additions are the same addition is *in the program* -- it wrote one operator over one array -- and a compiler that threw it away
in the front end and then tried to recover it in the back end would be paying twice for what it already knew.  That is what
autovectorization is, and it is why it is unreliable in every compiler that does it.

**What the language had to promise for this to be sound is that the sides do not overlap**, which it already did.  There is no
way to spell an address in this language, so the only aliasing there can be is the kind the program wrote down: an array read and
written in the same statement, and a slice referring into the array it came from.

**A run shorter than a register is still done in one.**  A run of four bytes is a four-byte read, which leaves the rest of the
register clear, and one operation.  The cost of that decision is that the lanes beyond the run may well "go past" -- zero minus
the number that is in every lane does -- so the check has to be restricted to the lanes the run covers.  The alternative, doing
short runs an element at a time, would have left nearly every array in a real program on the slow path: the arrays this language
has are small.

**The check is the part with no precedent to copy.**  A machine that adds sixteen bytes in one instruction does not write sixteen
carry flags, and no architecture has ever pretended it could; so the four questions are asked of the answer, in `and`, `or` and
`exclusive or`, and the one branch asks whether any lane said yes.  Every language compared with below avoids this question
rather than answering it -- by wrapping, by leaving it undefined, or by not checking at all -- which is why the formulas here are
written out rather than cited.

**Multiplying is left out** on every target, for the reason the narrow scalar arithmetic gave: seeing that a product went past
wants the upper half of it, which none of these machines gives at every lane width.  A run of floating-point numbers is left out
too, the question "did this go past" being a different question there.

**The width follows the level and the operations do not.**  `v3` and `v4` promise AVX2, so a run is thirty-two bytes there and
sixteen on the older two -- and the program has already said at its own entry point that the processor has what it was built for,
which is what makes taking the level at its word safe.  The newer forms are the same operations over more lanes, so nothing but
the width is level-dependent, and the mnemonics are the narrow ones: which form a row is, is said by how wide its registers are.

Compare: **APL, BQN and Uiua**, where every scalar function threads over arrays and the implementations vectorize because the
language never lost the information -- which is the position this takes, and the reason the operator attribute came first;
**C and C++**, where the loop is written out and the compiler tries to recover the fact that it is one operation, with
`#pragma omp simd` and `restrict` existing because it often cannot -- and where signed overflow is undefined and unsigned wraps,
so there is no check to vectorize; **Rust**, whose `std::simd` is explicit and whose ordinary arithmetic panics on overflow in
debug builds and wraps in release, so the checked form is not the one that gets vectorized; **Zig**, whose `@Vector` is an
explicit type in the language and whose `+` on one traps on overflow -- the closest thing to this anywhere, with the difference
that there the program names the width and here it does not; **Go**, which neither vectorizes nor checks; **Fortran**, whose
array expressions are the oldest form of this and whose compilers have vectorized them since before the word existed;
**ISPC**, where the program is written as if for one lane and the compiler supplies the rest, which is this seen from the other
side; and **Haskell**, whose `Data.Vector` fuses loops away and leaves the vectorizing to the backend.

What none of them has is the combination: an operator the program wrote over an array, a width the program never names, and a
check that every element still gets.

---

## 2026-09-15T23:30+02:00 — language

**`⎕wrap`, which says of a region that its arithmetic may go past the end**

Decided on the user's direction: a name written like a function and taking one expression, inside which the operators keep the
low bits of what they came to instead of stopping the program or stopping at the end of the type.

**The thing being decided is the grain.**  Every language that offers wrapping arithmetic at all offers it one operator at a
time -- Rust's `wrapping_add`, Zig's `+%`, Swift's `&+` -- and the places that actually want it want it of a whole expression: a hash,
a checksum, a pseudo-random step, a counter that is meant to run round.  Written one operator at a time those read as a different
program from the one anybody means, and every operator in them has to carry the mark, which is exactly where one gets left off.
Written once around the expression, what a reader has to check is that the expression is one where wrapping is intended, which is
the thing that is actually true or false.

**It is a wrapup and not a function**, and that is not a spelling detail: a function takes values, and this takes an expression
and changes what the operators in it mean.  There is nothing for it to stand for on its own, which is why naming it without an
expression is an error of its own rather than a type mismatch.  The `⎕` sigil is what makes that possible without taking a name
away from any program.

**A saturating operator inside a wrap is an error.**  This is the part that was decided twice.  The first answer was that `⊞`
inside a wrap becomes an addition that runs round, on the grounds that a wrap has to mean one thing and a rule with an exception
in it is a rule that gets the exception wrong.  The user reversed it, and the reversal is right: the two are not a rule and an
exception to it, they are a contradiction.  `⊞` is a program saying *the end of the type is the answer* and a wrap is the same
program saying *the low bits are*.  Silently keeping one of them makes the compiler pick which half of a sentence its author
meant, which is the thing a compiler should never do quietly -- and the cost of picking wrong is a program that computes
something plausible and wrong rather than one that fails to build.

The reason it is not merely a lint is that there is nothing to warn about: neither reading is defensible enough to emit code for.
And the program that wants a saturating step inside a mostly-wrapping expression loses nothing, since it writes that step outside
the wrap and hands the answer in -- which also reads better, the two different intentions being on two different lines.

**Shifts take their distance modulo the width rather than being undefined.**  C leaves a shift by the width or more undefined,
which is where this language otherwise stops the program; inside a wrap it cannot stop the program, so the question is what it
answers instead.  Modulo the width is the answer because every width is a power of two, so it is one `and` and exact -- and
because the three architectures each take it modulo the width of the *register*, which is a different number, so leaving it to
them would have made a program mean three things.

**Dividing is left alone.**  Neither a quotient nor a remainder can go past the end of a type by arithmetic; what they have is a
divisor of zero, which is not an overflow and which a wrap has nothing to say about.

**Over a run it is where the cost is.**  A checked addition of sixteen elements is one instruction and eight more asking whether
any lane went past; a wrapping one is the one instruction.  And it is the only way a run is multiplied at all, the upper half of
a product not being available at every lane width on any of these machines.

Compare, beyond those above: **C and C++**, where unsigned wraps and signed is undefined, so there is no way to ask for one
without the other and no way to ask for neither -- and where `-fwrapv` is the whole-program version of this decision, made once
for a compilation rather than once for an expression; **Go**, which wraps always and offers nothing else, so the question never
arises and neither does the check; **Python**, whose integers do not have ends; and **Ada**, whose `mod` types wrap by being a
different type, which is the third possible grain -- the value's rather than the operator's or the region's.  Ada's is the most
honest of the three and the least usable: it makes a wrapping `u8` a type of its own, so every function that takes one has to
say which it takes, and a program that wants one expression to wrap has to convert going in and coming out.

---

## 2026-09-16T00:40+02:00 — language

**`⧺`, which joins two arrays**

Decided on the user's direction: an operator that puts one array's elements after another's, with the inner dimensions required
to match where the arrays have more than one.

**A glyph of its own rather than `+`.**  Python overloads `+` for both -- list concatenation and array element-wise addition --
and the result is that what `a + b` means depends on which of two things `a` is, which is the single most reliably confusing
thing about numerical Python.  This language had already given `+` the element-wise reading by making the operators walk arrays,
so the two had to be told apart, and a doubled plus is what says "of the things, not of the values".  It is Haskell's spelling,
and `++` is its accepted ASCII substitute for the same reason -- two characters, and this language has no operator that adds one
to something, so two plus signs begin nothing else.

**It binds looser than everything that works out what goes in an array and tighter than every comparison.**  `a + 1u8 ⧺ b`
joins what the two sides came to, which is the reading anyone writing it means, and `x ⧺ y = z` asks about the whole join.

**The inner dimensions must match, and rank must match with them.**  A join goes along the first dimension and leaves every
dimension inside it as it was: two tables of three columns join into a table of three columns.  A table joined to a vector is
refused rather than given a meaning -- there is a meaning available, treating the vector as one row, and it is refused because
the two readings of `m ⧺ v` (a new row, or a flattened concatenation) are equally defensible and the program can say which it
means by writing the brackets.

**Both sides must state their shape.**  What the join answers with is as long as the two together, and room for that many is
taken where the join stands; a side whose length the type does not say would mean taking room while the program runs, which is an
allocation and a change to something that outlives the call.  That is the same rule picking with a mask follows and for the same
reason.

**It is lowered as two runs and not as a loop or a memcpy.**  Each side is read as one value of as many lanes as it has elements
and written into its part of the answer, which is the shape the vectorization step already knows how to cut up -- so a join is
one instruction per register's worth where the machine has registers for it and an element at a time where it has none, with
nothing target-specific written for it.

Compare, beyond the above: **APL**, whose `,` catenates along the last axis and `⍪` along the first, with a conformability rule
this is the fixed-shape case of -- and which has both because its arrays grow, where these do not; **Go**, whose `append` is a
function rather than an operator because a slice carries its length beside it and a join may reallocate, neither of which is true
here; **Fortran**, where `[a, b]` is the array constructor doing this job, so there is no operator and no question about
precedence; **C**, which has neither and leaves it to `memcpy` and two lengths the compiler cannot check; and **Rust**, whose
`concat` and `chain` are methods on slices and iterators, one answering a `Vec` that allocates and the other answering something
lazy, neither of which is a fixed-shape array.

---

## 2026-09-16T02:10+02:00 — language

**`char`, which is one code point and not a number**

Decided on the user's direction: a character type holding a UCS-4 value in thirty-two bits, with the maximum checked, and a
conversion to `u32`.

**The decision inside it is that it is not an integer type.**  Holding a code point in thirty-two bits is a representation, and
representation was never the question -- C's `char` is an integer type of the width of a byte and is therefore neither a
character nor one code point, Go's `rune` is an alias for `int32` so `r + 1` compiles and means nothing, and C++20's `char32_t`
has the width right and the type wrong.  What makes this type worth having is that the arithmetic is *not* defined on it: the sum
of two characters is not a character, and a language that lets it be written has given up the thing the type was for.  Rust
reached the same answer and is the model here.

**The comparisons are defined, and that is not an inconsistency.**  Unicode numbers the code points, and that numbering is a real
order -- it is where every collation in the world starts before it does anything else.  So both questions are asked of a `char`,
unlike an enumeration, whose order is the order somebody happened to write the values in and which therefore answers equality and
nothing more.

**The two conversions are not each other's mirror image, and neither is an assignment.**  Every code point is a number, so `⎕ord`
cannot fail; not every number is a code point, so `⎕chr` checks and stops the program.  A conversion that may stop the program is
a thing a reader should be able to see, which is why it is not a rule about what may be assigned to what -- and the one that
cannot fail is written out too, because a language with no implicit conversions does not get one exception for the easy
direction.

**A number written where a code point is wanted is that code point.**  `let last: char = 0x10ffff` is how the last one is
written, there being no character to type; it is checked where it stands, and so is the same number given to `⎕chr`.  That is
the same rule an unsuffixed literal already follows everywhere -- it takes the type that is wanted -- with the one extra thing a
code point can fail to be.

**The names carry the quad.**  The instruction said `ord`; the project's own rule reserves compiler-provided names with `⎕` so
that no program has to give up a name, and `ord` and `chr` are exactly the names a program working with text would want.  They
are `⎕ord` and `⎕chr` for that reason and the bare spelling is one line away if it is wanted instead.

**`⎕chr` was added, and was not asked for.**  "Assignments have to check for the maximum value" has run-time force only if a
number the program worked out can become a code point, and `⎕ord` goes the other way; without a checked way in, the check would
have been a compile-time one about literals and nothing more.

Compare, beyond the above: **Python**, whose `chr` and `ord` these are named after, and whose characters are strings of length
one -- which works there because a string is the only sequence type that matters and does not here; **Haskell**'s `Char` with
`ord` and `chr` in `Data.Char`, where `chr` is partial and throws; **Java**, whose `char` is sixteen bits and therefore cannot
hold a code point at all, which is the mistake this type exists to not make; and **Swift**, whose `Character` is a grapheme
cluster -- the other place the line could be drawn, and a much larger thing to carry in thirty-two bits.  What a *string* is
stays open, and the grapheme question belongs to it.

---

## 2026-09-16T04:00+02:00 — language

**`str`, which is UTF-8 and is walked rather than indexed**

Decided on the user's direction: a string type, always UTF-8, written between quotation marks, walked by `foreach` a character at
a time, and joined with `⧺`.

**The decision inside it is that there is no index.**  The *n*-th byte of UTF-8 is not the *n*-th character, so a type that let
`s[n]` be written would be a type whose obvious use is wrong -- and every language that offers one has had to pick which of the
two it means and then live with programs that meant the other.  Python pays for the index with either four bytes a character or
three representations of a string; Go offers an index and it is a byte, so `s[0]` of a string beginning with a non-ASCII
character is half a character; Rust refuses the index outright and is the model here.  A walk is what a string offers, and it
reaches the characters in order because that is the order they are encoded in.

**Well-formedness is an invariant and not a check.**  There are two ways to make a string -- a literal, whose bytes the compiler
encoded, and a join, which puts well-formed bytes after well-formed bytes -- and nothing else makes one.  So the decoder in the
walk is a decoder and not a validator, which is where nearly all of the cost of walking text usually goes: Go's `range` over a
string substitutes a replacement character where the bytes are not UTF-8 and therefore checks on every turn, because a Go string
carries no such guarantee.  The guarantee is what buys the check away, and it is affordable here because the language has no way
to build a string out of bytes.

**A join allocates, so a function that joins is impure.**  How long a join is, is not known while compiling, so its bytes cannot
go where a join of two arrays puts them; they come from the arena the compiler provides, which outlives the call.  That is the
same rule a collection already follows, and stating it the same way is worth more than an exception would be.

**What a string is not, yet**, and each is in the to-do list rather than decided here: there is no comparison of two strings, no
length, no slice, and no `mut str`.  The first three are refused honestly -- `"a" = "a"` reports that `=` does not compare
strings -- rather than given a meaning that would have to be taken back.

Compare, beyond those above: **C**, where a string is a pointer and a convention and every length is a linear scan; **Swift**,
whose `String` is a sequence of grapheme clusters, which is what a reader means by "character" and which `char` already left open
as the larger question; and **Java**, whose strings are UTF-16 and whose `charAt` therefore has the same defect Go's byte index
has, one layer up.

---

## 2026-09-16T05:30+02:00 — language

**`#`, which answers how many**

Decided on the user's direction: one operator written before its operand, answering how many things the operand is made of --
characters for a string, the outermost dimension for an array, members for a tuple, entries for a set or a dictionary.

**One operator for five types rather than five names.**  The alternative is what Go and Rust do, a `len` per type reached as a
method, and what it costs is that the five stop looking like one question.  They are one question: *how many things is this made
of*.  Naming it once is what makes `#s`, `#v` and `#d` read alike, and it is what APL does with `≢` and Python with `len`.

**A string's count is characters and not bytes**, and that is the decision the instruction was really about.  Go's `len` on a
string is bytes and Rust's is bytes, and in both the easy call is the one that is usually wrong -- a program that wants to know
how much text there is asks and gets an answer about storage.  Here the number that needs no walk is the one nobody wants, so it
is not the one the operator gives; the byte count is not offered at all yet, and the to-do list records that rather than this
guessing which spelling it should have.

**An array answers its outermost dimension and not how many elements in all.**  `#m` of a `u8⟦2,4⟧` is two.  That is what makes
`#` and `foreach` agree -- a walk over that array gives two rows -- and a count of all eight is a different question, which is a
product of the shape and which nothing yet asks.

**It is not walked over an array.**  Every other operator written before its operand reaches an array by being applied to every
element; this one is defined on the array itself.  That is not an exception to the listable rule so much as a different kind of
operator: the listable ones are defined on values and lifted, and this one was never defined on a value.

**It binds where the other prefix operators bind**, which puts it tighter than every operator written between two operands and
looser than an index or a call: `#v + 1u64` adds one to the count, and `#m⟦1⟧` counts the row.

Compare, beyond the above: **C**, where `strlen` walks and `sizeof` does not and the two are spelled so differently that nobody
confuses them -- which is the one thing C got right here and is an argument for two names rather than one, answered by the fact
that a string is the only one of the five with two counts to tell apart; **Swift**, whose `count` on a `String` is grapheme
clusters and is O(n) for the same reason this is; and **JavaScript**, whose `.length` on a string is UTF-16 code units, which is
neither of the two numbers anybody wants.

---

## 2026-09-16T07:00+02:00 — language

**`⍴`, which answers a shape and makes something of one**

Decided on the user's direction: APL's rho, doing APL's two jobs -- written before one thing it answers that thing's shape, and
written between two it makes something of the shape on its left out of the values on its right, going round again where there are
fewer values than the new object holds.

**What it answers is what it takes**, and that is the decision that makes the two jobs one operator rather than two spellings
that happen to share a glyph.  A number for one dimension, a tuple of numbers for more, so `(⍴a) ⍴ b` is well formed for any
array.  The one place it differs from APL is that one dimension answers the number itself rather than a vector of one: a tuple of
one is not a thing this language has, APL's arrays having no types they must agree with.

**The shape has to be known while compiling**, which is not a restriction chosen for this operator but what an array is here: the
shape is in the type.  Three things count as known -- a literal, a name bound at the top level to a number, and the shape of
something whose type states it.  The third is what keeps `(⍴a) ⍴ b` from being a form of words.

**Too many values are refused rather than dropped.**  APL truncates, and truncating is the one thing this language does not do
anywhere else: a program that wrote more values than the shape holds either got the shape wrong or the values wrong, and which
ones would be left out is not something to guess at.  Fewer values go round again, which is the operator's whole point -- and
"round again" rather than "pad" because what to pad with is a question no type answers.  Fortran's `RESHAPE` takes a `PAD`
argument and is the other answer; taking one here would mean every use writing what it does not care about.

**One value everywhere is a run and not a loop.**  `n ⍴ 0u8` is a splat over as many lanes as the object holds and one store,
which the vectorization step then makes one instruction per register's worth.  That is the common case and it costs nothing to
make it the fast one.

Compare, beyond those above: **NumPy**, whose `reshape` requires the counts to match exactly, and whose `full` and `tile` are the
other two thirds of what this does -- three names for one idea; **BQN**, whose `⥊` is this with the same cycling; and **Julia**,
whose `reshape` is a view rather than a copy, which is a decision about ownership this language cannot make until it has
references.

---

## 2026-09-16T08:30+02:00 — language

**Lists, whose elements will not have to agree and for now must**

Decided on the user's direction: `[a, b, c]`, a sequence of however many values there turn out to be, with the common element
type recorded so that the case where they agree stays recognisable once boxing exists, and `⧺` joining two.

**The decision that matters is where the element type is kept.**  It is in the *type* -- `[u8]` -- and not beside the value.
That is what makes today's restriction temporary rather than structural: a list whose type says what it holds is the one that
needs no tag per element, and the heterogeneous list that boxing will bring is one whose type does not say.  Keeping it in the
value instead would have meant every list paying for the tag today to buy nothing, and keeping it nowhere would have meant the
fast case being unrecoverable later -- which is the position JavaScript engines are in, spending a great deal of effort at run
time recognising arrays that a type could have said something about.

**A list is not an array, and the difference is not length.**  An array carries its shape in its type and takes no room of its
own; a list carries how many beside where its elements are and puts them in an arena.  So the two answer different questions --
"I know how many" and "I do not" -- and having both is worth more than one that tries to be either.  That is Rust's `[T; N]`
against `Vec<T>` and Go's array against slice, and it is the same line.

**Making one is impure**, because it allocates.  That is the rule a collection already follows and stating it the same way is
worth more than an exception.

**No index yet.**  What `l[i]` means where `i` is past the end is a question with three answers -- stop the program, answer a
result, or refuse to compile -- and the array's answer (check and stop) is not obviously the list's, a list's length not being
in its type.  The to-do list records it rather than this deciding it in passing.

Compare, beyond the above: **Python**, whose lists are heterogeneous and boxed always, which is the shape this aims at and the
cost it means to avoid where it can; **Lisp**, where the list is the type and the cons cell is the price; and **Java**, whose
generics box every element of a `List<Integer>` and whose value types exist to undo exactly that.

---

## 2026-09-16T09:45+02:00 — language

**A `foreach` is written the way a `let` is, colon and all**

Decided on the user's direction, and it corrects something this compiler had let slide: the colon before a loop binding's type is
not optional.

**Two spellings of one thing is what this language does not have**, and `foreach x = v` beside `foreach x: T = v` was two.  A
`foreach` binds a name exactly as `let` does -- the name stands for a value and may be given a type -- so it is written exactly
as `let` is, and with neither a type nor a qualifier the two characters read as `:=` there as here.  It could not have been
optional after `while` in any case, a name on its own followed by a colon being a condition with a body after it; having it
optional after `foreach` and required after `while` was the worst of the three possibilities.

**And the type now reaches what the loop walks.**  `foreach x: u8 = [1, 2, 3]` is three bytes.  That follows from the colon
being what it is: a type on the name says what a turn gives, and what a turn gives is what the thing being walked holds -- so the
list, the array or the set being written there can take its own type from it.  Before this the declared type was only checked
against what the expression turned out to be, which made `foreach x: u8 = [1, 2, 3]` fail for want of a type the program had
plainly written down two words earlier.

What is wanted is worked out from how the loop's expression is *written* -- a list of them, an array of as many as are written, a
set of them -- because that is what says which container it is before anything is lowered.  A name or a call says its own type
already and is unaffected.

Compare: **Go**, whose `for i := range v` has the same `:=` and no place for a type at all, the element's being the container's;
**Rust**, where `for x: u8 in v` is not allowed and the pattern carries the type only through `let`; **C++**, whose
`for (uint8_t x : v)` does exactly what this now does, with the declared type converting rather than settling -- which is the
part this does not copy; and **Python**, which has no types to write there.

---

## 2026-09-16T11:00+02:00 — language

**`comptime`, and a type that is never a value**

Decided on the user's direction: `comptime` before `foreach` walks a tuple with each turn's name of that member's type, and
before `if` or `elif` asks a question the compiler answers; `⎕typeof` is what such a question asks about.

**The decision that made the rest easy is that a compile-time condition is never lowered.**  It is answered by walking the
syntax, and nothing in it becomes a value.  That is what makes `⎕typeof`'s "as-yet unspecified representation" not a
placeholder but the honest answer: there is no representation because there is nothing to represent.  A type is not a value here
and never becomes one, so nothing had to be added to the representation, the verifier, the calling convention or any backend --
the whole feature is in the checker.

The alternative was a first-class type value with some encoding, which is what a language needs if types can be passed about and
stored.  This language cannot do that and does not want to yet, so paying for it would have bought nothing.

**`comptime` goes before each arm's keyword, not once before the chain.**  Which arms the compiler settles is a property of each
condition; a chain may mix them, and one that does is a program asking one question of the compiler and another of itself, which
is a reasonable thing to write.  Writing it once before the `if` would have made "the whole chain is settled" the only reading
and would have been wrong for the common case where a settled first arm falls through to an ordinary one.

**A `comptime foreach` has no `break`, no label and nothing to hand over.**  It is the body written out; there is nowhere to jump
to.  Refusing those is honest rather than restrictive -- a loop that is not a loop cannot be left early.

**A tuple is walked only this way, and this walks only a tuple.**  An ordinary loop over a tuple is reported with what to write
instead, and a `comptime foreach` over anything else is reported as well: everything but a tuple holds values of one type, so
writing the body out per element would be the same body many times for no reason.

Compare: **C++**, whose `if constexpr` this is and whose `decltype` `⎕typeof` resembles -- the difference being that a
discarded `if constexpr` branch is still parsed and instantiated outside a template, where here it is simply not lowered, which
is the behaviour people expect and do not get; **Zig**, whose `comptime` and `inline for` are these two under one keyword and
whose `@TypeOf` is this exactly -- the closest of the three; **D**, whose `static if` and tuple `foreach` are the direct
ancestors of both; and **Rust**, which has neither and reaches for macros and traits, paying in compile time and in error
messages for what these two keywords do plainly.

---

## 2026-09-15T17:20+02:00 — language

**`⌈` and `⌊`: the larger of two, and the largest of several**

Decided on the user's direction: `⌈` for the maximum and `⌊` for the minimum, written between two operands or before one.

**The one-sided form is the two-sided one applied along the thing**, which is why one glyph does both jobs.  That is APL's
arrangement and the same arrangement `⍴` already has here; the alternative -- two names, `max` and `maximum`, as Haskell and
Rust have them -- states the relationship nowhere and makes a reader learn two things where there is one.  APL itself writes the
reduction `⌈/`, and this language has no ceiling of an integer to want, so the one-sided glyph was free to mean the reduction.

**An array of more than one dimension answers a row.**  The outermost dimension is walked and what lies underneath is compared
elementwise, so the answer has the shape of one of its rows and the rule applies to itself however deep the array goes.  The
alternative -- the largest element anywhere in it -- was rejected because it is the one answer that cannot be built out of the
others, while this one is: the largest element of a table is `⌈⌈m`.  It is also what APL, NumPy and every array language
answer when told which axis to reduce along, and the outermost is the one a language with row-major layout can answer without a
stride.

That answer needs nested arrays to be regular -- every element at the same depth the same shape -- which the array types have
always been.  Keeping that rule rather than weakening it costs nothing now that lists exist: a ragged collection has somewhere to
live.

**A dictionary is asked about its keys.**  Comparing a key with what it stands for is comparing two different things.  Python's
`max` on a dictionary answers a key for the same reason, and it is what "the largest entry" means to anyone who says it out loud.

**The largest of nothing stops the program.**  Rust answers `None` there, Haskell throws, Python raises, APL answers the identity
of the operation -- the most negative value the type has.  APL's answer is the tempting one because it makes the fold total, and
it is the one rejected hardest: it is a lie about what was in the collection, and a program that got it would go on to use a
number nothing put there.  Answering a result instead would have put a `?` on `⌈v` for every `v`, including the arrays whose
length is in their type and which cannot be empty.  Stopping is what every other question this language cannot answer does.

**Floating-point values got an instruction rather than a refusal.**  All three machines have one, and adding four rows to each
table was cheaper than explaining why the operator a program would most want on a list of measurements is the one it cannot have.
It is also the only floating-point operation here with no check after it: it answers one of the two it was given.

Compare: **APL** and **BQN**, whose `⌈` and `⌊` these are, both forms; **Python**, whose `max` is both forms *and* a third
meaning -- the largest of several arguments -- under one name; **Rust**, whose `Ord::max` and `Iterator::max` are the two forms
with the second answering an `Option`; **Haskell**, whose `max` and `maximum` are the two forms under two names; **C**, which has
`fmax` for floating point and nothing for integers, so every C program writes the comparison out; and **NumPy**, whose `maximum`
is the two-sided elementwise form and `max` the reduction, with an `axis` argument this language answers with "the outermost,
always" because its arrays say their shape.

---

## 2026-09-15T18:40+02:00 — language

**Characters and strings compare, and strings compare by their bytes**

Decided on the user's direction: all six comparisons are defined on `char` and on `str`, and a string comparison reads the bytes
without decoding them.

**The bytes are the definition and not an optimization.**  UTF-8 was designed so that the byte order and the code-point order are
the same order -- a longer sequence begins with a higher leading byte than any shorter one, and within a length the bits of the
code point go in in order -- so "compare the code points" and "compare the bytes" are two descriptions of one answer.  Writing
the specification in terms of code points and the implementation in terms of bytes would have been a claim to check; writing both
and saying they agree is what the encoding is *for*.  It is why UTF-8 won, and it is worth saying out loud in a language that
stores text only that way.

**A prefix comes first**, which decides what two strings that agree as far as the shorter one goes do.  It is what every ordering
of strings anyone uses says, and it is the only answer that makes the order total.

**It is an ordering of code points and not a collation.**  `"Z" < "a"`, and `"ä" > "z"`.  Which words come first in a
dictionary is a question about a language and a locale; it wants tables, it wants to know which language, and its answer changes
between releases of those tables.  A `<` that quietly did that would be an operator whose answer depends on where the program
runs, which is the one thing no operator here does.  What `<` gives instead is a total order that is the same everywhere, which
is what sorting and keying actually need.

**The equal pair goes through the same walk as the other four.**  Checking the lengths first would answer without reading a byte
where they differ -- but the walk answers on the first byte that differs, and two strings meant to be different nearly always
differ early.  What the check would save is the case where one string is a prefix of the other, which is the case the walk has to
do anyway.  One loop in the image for all six was worth more than a branch saved in a case that is rare.

Compare: **Rust**, whose `Ord for str` is this exactly, byte order stated as the definition and `cmp` documented as *not* a
collation; **Go**, the same, with `<` on `string` comparing bytes; **C**, whose `strcmp` is this and whose sign is likewise
unspecified in magnitude -- and whose `strcoll` is the collation kept deliberately separate, which is the split this follows;
**Python**, which compares by code point rather than by byte and so answers the same for any well-formed text, at the cost of
choosing between four bytes a character and three representations; **Java** and **JavaScript**, which compare UTF-16 code units
and therefore put U+E000..U+FFFF *before* the astral planes, an order that is neither the code points' nor any collation's and
that UTF-8 cannot produce; and **Swift**, whose `<` on `String` compares grapheme clusters after normalisation, which is the
other end of the range and is a much larger promise to keep.

---

## 2026-09-15T20:10+02:00 — language

**Four rounding operators, written with arrows**

Decided on the user's direction: `↓` to the whole number below, `↑` to the one above, `↕` to the nearer of the two, `⇕` by the
processor's own rounding mode, all four listable, and a function that writes the fourth marked `@[impure]`.

**The arrows rather than APL's `⌈` and `⌊`.**  Those are ceiling and floor in APL and would have been the obvious glyphs, and
they are already spent here on the largest and the smallest — which is the better use of them, there being no ceiling of an
*integer* to want and APL itself writing the reduction as `⌈/`.  What the arrows buy is that all four roundings are one family
with one shape, which two ceiling-brackets and two more glyphs would not have been; and a double arrow for "go and ask" beside a
single one for "go either way" says the difference between the two without a word.

**The answer is of the type it was given.**  Every machine's instruction does this, and the alternative — answering an integer —
would have to pick a width, and then stop the program whenever the value did not fit in it.  Rounding is a question about the
*value*; converting is a question about the *type*; a language that ran the two together would make the cheap one able to fail.
C, Rust, Go, Zig and APL all answer in the floating-point type as well.

**A tie goes to the even number.**  IEEE 754's round-to-nearest, and what `roundsd`, `frintn` and RISC-V's `rne` all do without
being asked twice — so it is both the principled answer and the free one.  The other rule, a tie going away from zero, is C's
`round` and what most people are taught, and it has a bias that shows the moment many rounded values are added, which is what this
operator is usually part of.  Python and C# made the same choice and are asked about it constantly; that is a cost in surprise,
paid once, against a cost in accuracy paid every time.

**A function that writes `⇕` is impure.**  What it reads is a register of the processor's, which nothing in the language sets, so
two of them with the same argument can answer differently in two places — which is precisely what `@[impure]` already means here.
It also keeps the other three honestly pure, so a compiler may fold them and move them about.  C's `nearbyint` is inside
`#pragma STDC FENV_ACCESS`, which almost no compiler implements and almost no program writes, so in practice C lets the mode be
read with no marking at all and optimizes as though it had not been; Rust and Go do not offer the mode at all.  Marking it is the
smaller of the three answers and the only honest one.

**No truncation towards zero.**  It is `↓` for a positive value and `↑` for a negative one, which the binary form these operators
do not have yet would say in one glyph; adding a fifth glyph for it now would be the spelling that has to be unlearned later.

Compare: **C**, six library functions whose names say nothing about which is which and whose `rint` and `nearbyint` differ only in
whether an inexact answer is signalled; **Rust** and **Go**, three methods each and no way to reach the mode; **Zig**, three
builtins, likewise; **Python** and **C#**, one function with banker's rounding; **APL**, whose `⌈` and `⌊` are these two of the
four and whose glyphs this language spends elsewhere; and **Common Lisp**, whose `floor`, `ceiling`, `round` and `truncate` answer
two values, the quotient and the remainder, which is the one design here that answers more than this does.

---

## 2026-09-15T21:30+02:00 — implementation

**RISC-V is built for an ISA string or a profile, and `rva23` by default**

Decided on the user's direction: `--mclevel` on RISC-V takes a string written the way the architecture's naming convention says
to write one, or the name of a published profile, and the default is RVA23 — which brings F and D with it, among much else.

**The option is one option and the answer is three answers.**  x86-64 has four named levels because its own documentation has
four; RISC-V has no such list because its base is small and everything else is an extension, so what a program is built for is a
*set*.  The alternative was a second option, `--march`, beside `--mclevel`; it was rejected because the question is the same
question — what may the code generator use — and two options for one question is two things to learn and one of them always
wrong for the architecture in front of you.  What changes is who interprets the name, and that was already the target's job.

**Profiles as well as strings, and a profile as the default.**  A string is precise and nobody wants to type one; a profile is a
name a person can hold in their head, and the architecture publishes them for exactly that reason.  Making `rva23` the default
rather than `rv64gc` is the decision that does the most work here: this architecture's Linux ABI has required F and D from the
beginning, so a default of the bare base would have had the compiler refuse the arithmetic every program on it actually uses —
and it brings Zfa, which turns rounding from eight instructions into one.

**An extension the compiler does not know is refused** rather than ignored.  The argument for ignoring is that a program naming
an extension this compiler cannot use loses nothing by it; the argument against is `zfaa`, which is a typo, and which under the
lenient rule silently builds the slower program.  GCC and LLVM both refuse, and for the same reason.  What it costs is a table
row per extension, which the profiles needed anyway.

**Extensions in any order.**  The convention states a canonical order and the specification calls it a convention for *writing* a
name; the same set is the same ISA however it was spelled, so enforcing the order would reject `rv64gc_zbb_zba` — which says
exactly what it means — for the sake of catching a mistake nobody makes.  The canonical form is what the compiler writes back.

**A name ending in digits is a name.**  `sv39` and `zic64b` are extensions whose names end in or contain digits, and `zfa1p0` is
an extension with a version after it; the architecture's own rule that a name never ends in a digit is younger than `sv39`.  What
settles it is which of the two readings is a name the compiler knows, tried longest-first — which is the same thing the refusal
of unknown names buys, used twice.

**Floating point without the extension is refused, not emulated** (8503).  A software floating-point convention is a different
ABI, not a slower one: values go in different registers and the header's flag word says so.  Emitting the hardware instructions
anyway is a program that does not run.  So the two ways out are to build for something that has F and D, or to write a program
that does not use them — and a program that does not use them builds for `rv64imc` today.

Compare: **GCC** and **LLVM**, whose `-march` takes the string and whose `-mcpu` takes a name, with profile names arriving late
and separately — this folds the two into the one option the compiler already had; **Go**, whose `GORISCV64` takes exactly the
profile names `rva20u64`, `rva22u64`, `rva23u64` and nothing else, which is the readable half of this without the precise half;
and **Rust**, whose target features are a list per target with no profile names at all, which is the precise half without the
readable one.

---

## 2026-09-15T22:40+02:00 — implementation

**A RISC-V image says what it was built for, and the string is normalized**

Decided on the user's direction: `--mclevel=rv32…` is an error on the sixty-four bit target, and every image carries
`.riscv.attributes` with `Tag_RISCV_arch`.

**The width is checked against the target and not against a constant.**  `rv32gc` names a different machine, not a different
level of this one.  Writing the check as "this target's addresses are sixty-four bits wide, and the string says thirty-two" is
one line that also refuses `rv64` on a thirty-two bit target of the same family the day there is one — which the instruction
asked for and which a constant would have made a second thing to remember.  `rv64e` is refused beside it for a different reason:
sixteen registers and a calling convention of its own, neither of which this compiler has.  The *parser* still reads both, since
reading a name and being able to build for it are different questions.

**The attribute is not optional on this architecture.**  Every other target this compiler has says what it needs by being what it
is: an x86-64 image runs on x86-64, and a program built for a newer level asks the processor itself at its own entry point.
RISC-V can do neither — its base is small, everything else is an extension, and there is no instruction a program in user mode can
ask.  Without the section, an image built for RVA23 and one built for the bare base are indistinguishable until one of them hits
an instruction the processor has not got.  So it is written always rather than under an option: the cost is as long as the string
is, and the thing it buys cannot be bought any other way.

**Normalized, not merely canonical.**  Two forms were possible: the short one a person writes, `rv64gc`, and the long one with
every implied extension spelled out and every version stated.  The long one is what the attribute wants, because the reader is a
program comparing two images and not a person reading one — and the comparison only works if both toolchains write the same
string.  So the compiler keeps only the long form and drops the short one entirely: a second spelling that nothing reads is a
second thing to keep right.

**It is checked against the GNU assembler**, over a corpus, the way the instruction encodings are.  The normalization rules are
spread over the naming convention, the profile documents and forty years of accumulated extension names — which extension implies
which, what version each is at, what order they go in — and no amount of reading gets that right on the first try.  Asking a
toolchain that already implements it, and keeping the question in the test suite, is what the encoding tables already do.

The corpus leaves out `rva23s64`, where the two disagree by exactly one extension: the profile document makes Zifencei mandatory
for it and binutils' table leaves it out.  The document is what this follows, and what they disagree about is the privileged
half, which no program this compiler builds can use.

**The dump stops showing sections that are not mapped.**  A golden assembly dump is about what instruction selection produced,
and four hundred bytes of hex saying which extensions the profile has would bury five small files that exist to be read.  The
rule is not "skip the attributes" but "skip what is not part of the running program", which is the honest form of it and which
will be right for debug information and notes as well.

Compare: **ARM**, whose `.ARM.attributes` this format is and which has carried it since 2005 for the same reason — a `.o` built
for one floating-point convention linked against another is a silent disaster; **GCC** and **LLVM** on RISC-V, which write this
section and this tag with the same normalization, which is what makes the differential test possible; and **x86-64**, which has
nothing of the kind and does not need it, its levels being four points on a line every processor sits somewhere on.

---

## 2026-09-15T23:50+02:00 — language

**Raising to a power, written `ⁿ` and written raised**

Decided on the user's direction: `ⁿ` is the operator and a number written raised — `a¹⁴` — is that operator with that number on
the right.

**Two spellings of one thing, which this language otherwise refuses.**  What makes it not a second spelling is that neither
covers the other's case: raised digits can only be a number, there being no raised spelling of a variable, and `ⁿ` is the only
way to raise by something worked out.  Where both can be written they mean the same thing, as `⁻3` and `0 - 3` do — the raised
number is how the exponent is *written*, not a second name for the operation.  The alternative was to have only `ⁿ`, which would
have made the notation every reader already knows unavailable; or only the raised digits, which would have left a constant
exponent as the only kind there is.

**The two sides are not of one type**, and this is the only operator here of which that is true.  What is raised is a number;
what it is raised by is a *count*.  Making them agree would have refused `1.5f64³`, which is the thing anyone writes first.  It
is also why the operator is not on the arithmetic path with the others: that path's whole shape is "both sides have one type and
the answer has it too".

**The exponent is a whole number.**  A fractional power is a root, which none of these machines has an instruction for and which
would need a library this compiler does not have.

**A negative exponent is one divided by the positive power, and the answer is a result.**  Decided on the user's direction, after
first being refused outright.  Three answers were possible and every language picks a different one: Python changes the *type*
and answers a float, which a language whose integer types say what they hold cannot copy; Fortran and Ada make it an error; this
makes it the division it already is, and a division here answers a result.  The third is the one that adds nothing — there is no
new rule, no new failure and no new type, only the operator written as what it means.  For an integer it makes a negative power
almost always nothing, which is what dividing one by a whole number greater than one *is*; the operator is not the place to
decide that a program did not mean it.

**Which is why the two spellings are two things.**  `a ⁿ b` cannot see its exponent, so its answer is a result whatever the
exponent turns out to be — exactly as a division's type cannot depend on whether the divisor turns out to be zero.  `a²` can see
it: the exponent is written down and is not negative, so there is always an answer and a `?` would be a mark for a failure that
cannot happen.  `a⁻²` can see it too, and it *is* the division, so it carries the mark.  That settles the one thing that was
uncomfortable about having both notations: they are not two spellings of one meaning after all, and which one a program writes
says something the other cannot say.

**Anything raised to no power at all is one, including zero.**  The empty product is one, and every polynomial written anywhere
means that.  The alternative, a special case for `0⁰`, would make the operator answer differently for a value the program may not
know at the point it writes it.

**Squaring and multiplying, not multiplying out.**  A fourteenth power is five multiplications instead of thirteen, and the
saving is free: a square this computes is always a power the answer contains, so no square overflows where the answer does not,
and the check each multiplication already carries is the whole of the checking.  The loop form pays one thing for that — it must
not square on the last turn, which is the one case the argument does not cover — and that is why its test sits between the two
multiplications.

Compare: **Python**, whose `**` this is; **Fortran** and **Ada**, whose `**` has the same integer rule including the refusal of a
negative exponent; **Haskell**, whose `^`, `^^` and `**` are one operator per combination of integer and fractional, which is the
most honest arrangement anyone has and the hardest to remember; **APL** and **BQN**, whose power takes a float exponent because
everything there is a float; and **C**, **Rust**, **Go** and **Zig**, which have no operator at all — where `pow(x, 2)` instead
of `x*x` is a performance mistake famous enough to have its own compiler optimization.

---

## 2026-09-16T01:20+02:00 — language

**Integer types of any width, and a status that is a `u6`**

Decided on the user's direction: `u1` through `u31` and `i2` through `i31` beside the four machine widths each already had, with
every rule about range unchanged; and the startup function answers a `u6`.

**No `i1`.**  A signed type of one bit holds zero and minus one.  There is no program that wants that pair and no reader who
would guess it, so the name is not given out.  `u1` is kept: nothing and one is a pair programs want constantly.

**What holds a value is the narrowest machine width that contains it**, and not some number of bits packed against its
neighbours.  Packing is what a bit field is, and a bit field is a property of a *field* -- what lies beside it, in what order,
with what padding -- rather than of a type.  A `u3` local, parameter and return type is the thing that was wanted, and each of
those is a register or a byte whatever the width says.  Zig draws the line in the same place.

**Almost none of it reached the code generator**, which is the part worth recording.  The rule for a type narrower than the
register holding it was already written -- compute at the register's width, where the exact answer cannot be lost, then compare
against the type's two ends -- because a `u8` in a thirty-two bit register is that case already.  Three bits took the same path.
What had to be added was a way to put a *signed* narrow value back, there being no instruction that widens from five bits, and a
rule that keeps narrow elements out of the run-at-a-time machinery, whose lane operations check the width they work at rather
than the width the program wrote.

**The startup function answers a `u6`.**  The specification already said that 0 through 63 is the program's own range and that
64 through 127 are the runtime's; the type now says it, so a program that tries to exit with 200 is refused where it writes it
instead of quietly arriving somewhere in the runtime's range.  It is also the first thing in the language that uses a width that
is not a machine width, which is worth something on its own: a feature nothing uses is a feature nobody has checked.

What it costs is that a status has to be *worked out* in `u6`, there being no conversion between integer widths in this language
yet.  Every test that used the exit status as a channel for a number above 63 had to say what it meant some other way, which is
an improvement in each case: a test that asserts inside itself and exits zero says what it checked, and one that exits 174 says
it to whoever remembers what 174 was.

Compare: **Zig**, whose `u3`/`i5` these are and which goes to 65535 bits; **LLVM**'s `iN`, which is where that comes from and
which has no signedness at all in the type; **Ada**, whose range types say the bounds instead of the width and check them exactly
as these do, which is the better notation for the cases where the bound is not a power of two; **C**, whose bit fields are the
nearest thing and cannot be a local or a parameter; and **Rust**, **Go** and **Odin**, which have only the machine widths, so
that a number known to be one of eight things is written as a byte and checked against two hundred and fifty-six.

---

## 2026-09-16T03:10+02:00 — language

**Lifting, written `⌜x⌝`, and the ends of a type**

Decided on the user's direction: what a compile-time question is asked *about* is written between `⌜` and `⌝`, so
`⎕typeof(⌜a⌝) = ⌜u32⌝` rather than `⎕typeof(a) = u32`.

**The grammar was the reason and it is a good one.**  The old spelling worked only because the right-hand side was a builtin
type's name, which the lexer could be told about.  A type the *program* defines is an identifier like any other, and a type
written out in full -- `u8⟦3⟧`, `〈u8, u16〉` -- is not an expression at all, so the general case would have had the parser
decide what a name meant before it could read what followed.  The brackets say it instead, and what is between them is then read
as a type where that reading reaches the closing bracket and as an expression where it does not.  Which of the two a bare name
was is a question about the program and is settled where such questions are settled.

This is **C++26's `^^` under a different glyph**, and for the same reason: there too the problem is that a name is a name and the
grammar must not have to know what it names.  The corner brackets rather than a caret because `^` is already the exclusive or
here and a doubled operator is a spelling to be told rather than one to see; the corners are a bracket pair, which is what this
is.

**`⌈⌜T⌝` and `⌊⌜T⌝` are the ends of a type**, and that fell out rather than being designed.  `⌈` already means "the largest
of what this holds"; the largest of a *type* is the largest value it has, so the operator needed no new meaning and the language
no new name.  Once lifting exists the two are unambiguous, which they were not before -- `⌈u8` would have been the largest
element of something called `u8`.

**The smallest of a floating-point type is its most negative value**, which is C++'s `lowest()` and not its `min()`.  C++ has
both and the number of programs that reached for `min()` and got a tiny positive number is the argument: `⌊` means the smallest,
and a value below it is not one.

**A lift is refused everywhere else** (4514).  There is nothing for one to be at run time -- a type is not a value here, which is
what the `comptime` entry above already decided -- so the three places that read one are the three places a compiler answers.

Compare: **C++26**'s reflection, whose `^^` this is; **C++**'s `std::numeric_limits<T>::max()` and `::lowest()`, which the two
operators are; **Zig**, whose types are values, so `@TypeOf(x)` and `maxInt(T)` need no lifting at all and pay for it elsewhere;
**Ada**, whose `T'First` and `T'Last` read better than anything here and cost a second kind of name; and **Rust**, whose `T::MAX`
is an associated constant and needs types that can carry those.

---

## 2026-09-16T04:05+02:00 — language

**`⎕enumerate`, and what it counts in**

Decided on the user's direction: something that makes an iterator out of an iterator, giving the count and the value as a tuple,
with an optional second argument saying what to count from.

**A tuple rather than two bindings of its own.**  Two names already take a tuple apart wherever one is bound, and a dictionary's
turn is already a pair taken apart that way -- so `foreach i, x := ⎕enumerate(v):` needed nothing beyond the counting.  The
alternative, a second kind of loop binding, would have been a new rule for a thing the language already does.

**What it counts from says what the count is**, rather than the count always being a `u64` that a program then converts.  There
is no conversion between integer widths here, so a fixed `u64` would have made `⎕enumerate(v, 1u8)` impossible to write rather
than merely longer -- and the type is the honest place to say how many turns are expected: a count that runs past the end of what
was asked for stops the program, which is what says the type was too narrow.  Rust's `.enumerate()` is always a `usize` and
counting from one is a `.map` after it; Python's takes a start and has no types to disagree about.

**A name the compiler provides rather than an operator or a method.**  What it makes is an iterator, and this language has no
type for one and no methods to hang it on; a glyph would have to be found for something written once per loop.  The quad already
means "the compiler provides this", and it is what `⎕typeof` and `⎕wrap` are.

**It stands where a loop takes its values from and nowhere else** (4515), for the same reason `⎕wrap` stands where it stands:
what it makes has no representation, so there is nothing for it to be anywhere else.

A dictionary cannot be counted, and that is a limit of the compiler rather than a decision: its turn is already a pair, and a
tuple holding a tuple is not a value this compiler can hold.  It is reported where the count is built, and the to-do list has
the underlying gap.

Compare: **Python**, whose `enumerate` this is including the start; **Rust**, **Swift** and **Kotlin**, whose versions are
methods on the iterator; **Go**, where `range` gives the index whether or not it is wanted; and **C++23**'s `views::enumerate`.

---

## 2026-09-16T05:00+02:00 — language

**`∣` and `∤`, and what zero divides**

Decided on the user's direction: an operator asking whether one number divides another exactly, with the form written before one
operand meaning the same thing with two on the left.

**What it answers depends on what the compiler can see of the divisor**, which was decided twice.  It first answered a truth
value always, on the mathematical reading that zero divides nothing but zero; the user's direction replaced that with the rule
recorded here, and the rule is better.  Nothing divides by zero, and a program that has worked the divisor out has a case with no
answer -- which is what a result is for, and is the shape `÷` already has.  Where the divisor is *written down*, or left out so
that two is written in, there is no such case and no result: the type says what the compiler could see, which is the same
principle the raised exponent follows.

**What the error carries is the number the question was asked about.**  What made it fail is known from the failure itself -- the
divisor was zero -- so carrying the zero would say nothing a reader did not have.  This is the first thing in the language to make
a result whose error carries a value, a type the specification has described since the beginning and nothing could produce.

**A zero written on the left is refused.**  There the answer is a truth value, which has nowhere to say there is no answer, and a
program that wrote a zero asked a question it knew the answer to.

**A comparison's level and a comparison's associativity.**  It relates two numbers and answers a truth value, which is what a
comparison is; and `a ∣ b ∣ c` would be asking whether `a` divides a truth value, so it joins two and no more, exactly as the
comparisons do.

**The form written before one operand is the operator and not a second one.**  "Is it even" is the question it is nearly always
asked, and writing the two in is what every notation for it does.  It costs nothing: the two is made of the operand's own type,
which also means a type with no two -- a `u1` -- refuses it, and that refusal says what is wrong rather than reporting a literal
that does not fit.

Compare: **mathematics**, whose glyphs and whose rule about zero these are; **C**, **Rust**, **Go** and **Python**, which write
`b % a == 0` and get a division by zero where this gets an answer; **Ada**, which has `rem` and `mod` and no divisibility test;
and **APL**, whose `|` is the remainder and which spells the test `0 = a | b` -- this operator with the comparison left to the
program.

---

## 2026-09-16T06:30+02:00 — implementation

**A result whose error carries a value**

Implemented because `∣` needed one.  `TYPE?ERROR` has been in the specification and in the type system since the beginning, and
nothing in the language could make one, so the compiler reported it as a feature it lacked.

**The truth value stays the second part.**  A result that carries something is three values -- the answer, whether there is one,
what the error carries -- and the obvious order puts the new part last so that everything reading the first two goes on working.
That is what was done, and it is why the change was small: `parts_of` is the one place that says what a value is made of, and the
register allocation, the calling convention and the pass that moves a large answer into the caller's storage all ask it.

**Three parts is more than a call answers in registers**, so such a result travels through the caller's storage -- the path an
answer of three parts already took, reached without deciding anything: `in_registers` asks how many parts there are.

**Reading the third part is its own instruction** rather than an index.  A tuple's parts are indexed because they are alike in
kind; a result's are the answer, a truth value and the error, which are three types, so `unwrap`, `failed` and `error` are three
instructions and the pass that takes an answer apart asks which shape it has.

**The `⊥` arm binds it**, exactly as the answer's arm binds the answer.  The machinery was already written for it -- the table of
a result's alternatives already gave the error's type -- and what was missing was passing it to the arm.

A way to *write* a failure followed in the commit after this one, `⊥` and `⊥ value`, which is the entry below.

Compare: **Rust**'s `Result<T, E>`, which this is, with the difference that there the two are a real sum and here they are both
present -- the answer and the error laid out beside each other rather than over each other, which makes reading either one read
and no branch and costs the room of the smaller; **Go**'s pair of return values, which is this without the tag; **Haskell**'s
`Either`; and **C++**'s `std::expected`, which unions them as Rust does.

---

## 2026-09-16T07:15+02:00 — language

**`⊥` and `⊥ value`: a failure, written out**

Decided on the user's direction.  Until now a result could only be *made* by an operation that had no answer for what it was
given -- a division by zero, a divisor that turned out to be zero -- so a program could read a failure and not produce one, which
made a function answering a result something only the compiler could write the interesting half of.

**What it is a failure of is what stands where it stands**, and nothing is written beside the glyph to say.  That is the rule a
value of the answer type already follows: `0u8` written where a `u8?` is wanted is the successful result of that type, and `⊥`
written there is the failure of it.  The alternative -- naming the type, `u8?(⊥)` or some such -- would have been a second way of
saying what the place already says, and would have been the only expression in the language that had to.

**Whether it carries a value is the type's to say.**  A `⊥` with a value where the error carries nothing is refused, and one
without where the error carries something is refused too: there is no failure of `u8?u16` that carries nothing, so allowing the
shorter spelling would have been allowing a value with a hole in it.

**It takes the whole of what follows**, the way `return` does.  In the grammar that made it an expression and never an operand,
which is also the only way the ambiguity resolves: written among the operands, whether the operator after `⊥ a` belonged to what
it carried could not be decided until the operand after *that* had been read.

Compare: **Rust**'s `Err(e)` and `Ok(v)`, which name the variant and so need no context -- the cost being that the type must be
inferred from the name and the value together; **Go**'s `return nil, err`, where the shape of the answer says which half is
which; **Haskell**'s `Left`; and **C++**'s `std::unexpected(e)`, which is this with the variant named.  What this does instead is
what it does for the successful half already, and having the two halves written the same way is worth more than a name on each.

---

---

## 2026-09-16T09:40+02:00 — language

**Default values for parameters, and arguments that say which parameter they are for**

Decided on the user's direction, with the marker for a named argument chosen by the user from three proposals.

**`.NAME ← VALUE` marks an argument with the parameter it is for.**  The three offered were `.name ← v`, `name: ← v` and
`‹name› ← v`.  The dot wins because a leading dot cannot be anything else: a member access needs something on its left, so the
mark costs no new glyph and no lookahead.  It is also what C's designated initializers, Odin and Zig write for the same idea in a
structure's initializer, so a reader who knows any of the three knows this.  The arrow is the language's own binding of a value
to a name, used here as it is everywhere else, which is what the instruction asked for.

**A default is settled while compiling and belongs to the function** (4525).  The alternative is C++'s: an expression looked up
in the definition's scope and worked out afresh at each call, which lets a default name a global, call a function, or read a
member.  That needs a scope to travel with the function into every place that calls it -- C++ gets it from the header model, and
a language with separately checked modules does not have it.  **Python** settles the value once, at definition, which is the
answer taken here; Python's famous trap, the shared mutable default, cannot arise because what is settled is a value and there
are no mutable ones to settle.  **Ada**, **D** and **Swift** are with C++.  **C**, **Go**, **Rust** and **Zig** have no defaults
at all, holding that an overload or an options structure says the same thing where a reader can see it; that argument is weaker
for a language emitted by a generator, which should not have to emit the arguments nobody varies.

**Every parameter after one with a default has one too** (4526), which is C++'s, Python's and D's rule and for the same reason:
arguments written without a name fill from the left.  Named arguments get round it at a call, but the rule is about what a call
without names can mean.

**Arguments by place come first, ones by name after** (4529) -- Python's rule, and the only one that keeps the places counting
from a fixed point.  A name that is not a parameter (4527), a parameter given twice (4528) and a parameter given nothing with no
default (4530) are the three remaining ways a call can fail to name each parameter exactly once.

**What is written is still worked out in the order it is written**, whatever order the parameters end up in.  The reordering is
done on the lowered values, after every argument has been worked out, so the rule that a call's arguments run left to right did
not have to acquire an exception.

---

## 2026-09-16T10:20+02:00 — compiler

**A line break inside brackets, in the tree-sitter grammar**

The compiler's lexer counts open brackets and gives out no end of line while any of them is, so a parameter list or a call may be
written down the page.  The tree-sitter grammar refused every such program, and the specification had never stated the rule the
compiler was implementing; both are fixed here, the specification first.

**An end of line is an extra in the grammar.**  The external scanner is asked first at every position it is asked at, so wherever
the parser would end a statement it still answers with a newline, an indent or a dedent and the layout rules are decided exactly
where they were; the extra catches the breaks the parser would not end a statement at, which inside brackets are the ones the
compiler suppresses.  **tree-sitter-python** reads Python's identical rule the identical way.

The alternative was for the scanner to count the brackets, as the compiler's lexer does.  It cannot: tree-sitter asks an external
scanner only where one of its tokens is valid in the parse state, and inside a bracket none of the three is, so the scanner is
never shown most of the brackets.  Making them external tokens would show them to it, at the price of the scanner lexing a dozen
characters the grammar lexes today, and of every rule naming a bracket naming an aliased token instead.

What the extra costs is that the grammar also admits a line break after a binary operator, after the `=` of a definition and after
`fn`, where the compiler ends the statement and reports an error.  A grammar that takes a few programs the compiler refuses is the
safe direction for one an editor colours with -- the opposite, refusing programs that are perfectly good, is what this fixes -- and
the compiler remains what decides whether a program is one.

---

## 2026-09-16T11:30+02:00 — language

**References: `&T`, `&mut T`, `&x` and `x⌖`**

Decided on the user's direction, with three of the four sub-decisions chosen by the user from proposals.

**A reference is made explicitly, with `&x`.**  The alternative was C++'s, where writing the place is enough and the parameter's
type is the only thing that says a place was handed over.  `&` at the call is what lets a reader see that the callee reaches the
caller's variable, which is the criticism C++ has never answered; Rust, C, Zig and Odin all write the mark.  It costs `&` a second
meaning beside bitwise and, told apart by position exactly as C tells them apart.

**The value is reached explicitly, and a reference may be rebound.**  The alternative was the transparent reference, where `r` is
the referent and nothing can point it elsewhere.  Explicit wins because it keeps `mut` meaning one thing: `mut` before a type says
the name may be bound to something else, which is what it already said, and `&mut` inside the type says the place may be written.
With a transparent reference `mut &mut T` would have had no meaning at all.

**The mark is `⌖`, U+2316 POSITION INDICATOR, written after its operand.**  Postfix so that reaching further into what it answers
reads left to right, which is what Pascal, Modula, Ada and Odin put a mark after a pointer for; `↑` was proposed first and
rejected by the user, being the ceiling rounding already.  U+2316 is the one glyph in Unicode whose name says "position", which is
what a reference holds, and it is in no family with the arrows the roundings use.  Considered beside it: `‸` U+2038 CARET, the
mark Pascal's family uses, as a codepoint of its own so that `^` stays exclusive-or; `!r`, Standard ML's and OCaml's; and `⊃r`,
APL's disclose.

**A reference does not leave the call that made it** -- not answered with (4531), not held at the top level (4532).  What it names
has to outlive it and nothing yet says how long anything lives, so the two escapes are closed rather than guessed at.  Rust
answers the same question with lifetimes; C, Zig and Odin do not ask it.  Lifetime annotations are the next thing to be designed,
and they are what will lift both rules; nothing built here has to be undone when they arrive.  *(Lifted on 2026-09-17 by
`static` and `from`, which say how long; both numbers stayed, for the cases that remain.)*

**A reference names a place holding one value** (4536).  An array, a list, a string, a set, a dictionary, a tuple and a result are
each already several values or already a place, so a reference to one would be a second way of writing what a value of it is --
which is the rule about one meaning and one spelling, applied to a type.

**There is no null reference**, because every one is made from a place that exists.  That is Rust's arrangement and C++'s intent,
and it is what lets reading through one need no check.

---

## 2026-09-16T11:55+02:00 — language

**A type may reach itself through a reference**

Follows from references, and is what a reference in a product is for.  A definition reaching itself was refused (4408) because a
value of such a type would have to hold a value of itself; a reference occupies the same room whatever it names, so a definition
may reach itself through one, and `type Node = value : u8 ; next : &mut Node` is a list.

**The rule is about the way round, not about the field.**  A depth is raised while a reference's pointee is resolved, and a
definition that reaches itself with that depth above zero is allowed.  So a chain of definitions with a reference anywhere on it
is finite, and one with none on it is refused however long it is -- which is the question a value's size actually asks.

**A product and a sum are now equal only to themselves.**  They were nominal in the specification and structural in the
implementation, which nobody had noticed because nothing compared two.  A type that reaches itself makes the difference matter:
comparing by structure walks round the circle for ever.  Rust, Go, Zig and C++ all give a named record its own identity for the
same reason, and Haskell's `data` likewise; only a structural type system -- OCaml's objects, TypeScript -- does otherwise, and
this language decided for nominal long ago.

Compare: **C** and **C++**, where a structure may hold a pointer to itself and an incomplete type is the mechanism; **Rust**,
where `Box<Self>` or `&Self` does it and a bare `Self` is refused with the same reasoning; **Haskell** and **ML**, where every
value is behind an indirection so the question never arises; **Go**, where a struct may hold a pointer to itself and not a value
of itself.  What is decided here is the one thing those all share -- an indirection is what makes it finite -- with the depth
rather than the field as the test, so that a cycle through several definitions is judged by the same rule as a cycle through one.

---

## 2026-09-16T13:30+02:00 — language

**Units: `TYPE ¤UNIT`, and what may be written where**

Decided on the user's direction, with three sub-decisions chosen by the user.

**A unit is part of the type.**  `u32` and `u32 ¤meter` are two types and neither stands where the other is wanted, so `+`, `-`
and the comparisons needed no rule of their own: they already demanded that both sides be the same type.  **F#**'s units of
measure are this feature done fully in a mainstream language, and the bargain is the same one struck here -- checked while
compiling, erased before code is generated.

**A product and a quotient derive a unit; everything else demands one.**  The exponents are added and subtracted, so the seconds
cancel when a speed is multiplied by a time.  A unit is therefore kept as base units and exponents rather than as a name, which is
what makes `¤meter÷second × ¤second` really be `¤meter`.  A literal beside one of the two takes no unit at all, since doubling a
length gives a length.

**A unit that is not builtin is introduced with `unit`**, chosen by the user.  Without it a mistyped unit becomes a unit of its
own, which is the one mistake the feature cannot otherwise catch; F# and Ada both declare their units for the same reason.  Three
forms: a base unit of the program's own, one written in terms of others with a scale, and `unit ¤FROM → ¤TO`, which says a value
in one unit may stand where another is wanted and only the way round it is written.  Where a definition stands is how far it
reaches.

**`¤idx` is what an index must be**, and `¤size` is what `#` answers with.  The user was asked whether an index should accept a
count and answered with the `→` construct instead, so the two stay distinct and a program that wants to index with a count writes
`unit ¤size → ¤idx` once.  That is the better answer: it is one rule that covers every such pair rather than an exception built
into indexing.

**Crossing between units is two steps, written.**  `⎕drop` takes a unit off and `⎕unit` puts one on, and going from one unit to
another is both -- the user's choice from three ways of giving a number a unit.  The alternative, letting a written type give a
unit to a value that had none, would have made the crossing invisible at exactly the place it matters.  Neither applies a factor:
the scale a `unit NAME =` records says the two measure the same thing, and applying it would generate code where units generate
none.

**The division sign is the language's own `÷`.**  The request wrote `¤meter/second` and the definition form `÷`; `÷` is what
every other division in the language is written with, and `/` has no meaning anywhere here.  One meaning, one spelling.

Compare: **Ada**, whose dimension checking applies a conversion factor, which is what this records and does not yet apply;
**Boost.Units**, **Haskell's `units`** and **Rust's `uom`**, which do it in the type system rather than in the language, at a cost
in error messages; **Java** and **Swift** libraries that carry the unit at run time, which costs a word and a check per quantity;
**C**, **Go** and **Zig**, which have nothing.

---

## 2026-09-16T15:10+02:00 — compiler

**A bill of materials in every image**

Decided on the user's direction, with three sub-decisions chosen by the user.

**The tokens are hashed and not the text.**  That is the whole of what makes the thing worth having: a hash over the bytes moves
when somebody reformats a file, and a hash that moves for no reason is one nobody checks.  So the token stream is normalized --
a block's begin and end become one mark each whichever notation wrote them, the layout colon goes away, a statement separator is
one mark whether a newline or a semicolon said so, and a literal is the value rather than the characters.  Two spellings of one
program have one hash.

**SHA-256, all sixty-four hex digits**, chosen by the user from three.  It is what every SBOM format names first and what
`sha256sum` prints, so a row can be checked by hand; the truncated and BLAKE2 alternatives saved bytes in a section that is
already the smallest thing in the file.

**The compiler's row is the hash of a version string**, chosen by the user over hashing the compiler's own sources.  It is a
constant the project bumps, which is what a released compiler would report anyway; hashing the sources would have made every
binary depend on the working tree, which is more truthful and less useful.

**`.sbomstr` is loaded with `.sbom`**, chosen by the user.  The table holds offsets into it, so loading one without the other
gives a running program a table it cannot read -- and a program that can report its own bill of materials is the reason to load
either.

**It is always emitted.**  A bill of materials behind a flag is one nobody can rely on being there.  What it costs is two
sections, about two hundred bytes of headers, and a second loadable segment, which is the price of the sections being read-only
while the code is not.

Compare: **SPDX** and **CycloneDX**, which are documents beside the artifact and describe dependencies rather than sources --
what is here is narrower and is *in* the artifact, which is the property neither has.  **Go**'s `runtime/debug.BuildInfo`, which
is the closest thing: module versions and build settings, embedded in the binary and readable by the program itself; it records
what was depended on rather than a hash of what was compiled.  **Rust**'s `cargo auditable`, which embeds the dependency tree as
compressed JSON.  What is unusual here is the per-definition row: it is a hash of what each function *means*, which nothing else
in that list carries, and it is only meaningful because the hash is over normalized tokens.

---

## 2026-09-16T16:20+02:00 — language

**One entry of a literal says what they all are**

Stated by the user as a correction: `let a := ⟦⟦1u8, 2⟧, ⟦2, 3⟧⟧` is a program, because an array holds one type, one element
defines it and no other contradicts it.  The compiler refused it, and refused `⟦1u8, 2⟧` as well.

**Which element says it makes no difference.**  The question is about the type of the array and there is only one of those, so
reading it off the first element that happens to have a suffix is the same answer as reading it off any of them -- and reading it
before anything is lowered is what makes the two the same.  The rule now holds however deep the writing goes and for a list, a
set and a dictionary too, each of which holds one type.  A tuple is the exception and stays one: its members are independent, so
there is nothing for one member to say about another.

Compare: **C**, where an initializer takes the declared type and there is nothing to infer; **Go**'s composite literals, the same;
**Rust**, whose `[1, 2u8]` is `[u8; 2]` because inference runs over the whole expression and a suffix anywhere constrains the
element type -- which is this rule, arrived at by a much larger mechanism; **Swift** and **Haskell** likewise, by unification.
What is done here is the smallest thing that gives the same answer for literals: look at what is written, take the one type it
says, and lower everything with it.  A general inference pass would subsume it and is not wanted -- this language does not infer,
it reads.

---

## 2026-09-16T17:40+02:00 — language

**`⎕narrow`, and the enumeration that says why a value would not fit**

Decided on the user's direction.

**What it answers with is a result whose error carries a reason.**  A narrowing that answered only "no" would be an optional,
which is Swift's `init?(exactly:)` and throws away the one thing worth knowing; Rust's `TryFrom` carries a `TryFromIntError`
that says nothing more than that it failed.  Three conditions is the smallest set that is useful, and the user named them:
overflow, underflow, and sign.

**`sign` is a case of underflow with a name**, as the user put it.  A negative number put where an unsigned type wants one is not
merely below the bottom -- it is of the wrong kind, and it is the mistake a generator makes by losing track of signedness rather
than by losing track of magnitude.  Telling the two apart costs nothing: where the target is unsigned the bottom is nought, so
the comparison that finds the sign case is the comparison that would have found underflow.

**The enumeration is builtin and global**, on the user's direction.  Every program that narrows anything needs the same three
names, and a condition each program spelled for itself would be three spellings of one thing that no `match` could carry from one
file to the next.  It is written `⎕narrowing`, with the sigil every compiler-provided name carries, so no program has to give up
the name.

**Overflow is numbered nought.**  That makes it what a failure reports where neither of the other two holds, which is right --
above the top is the ordinary way not to fit -- and it is what lets the reason be worked out as a sum rather than as a choice,
there being no select in this IR and no reason to branch for it.

**Each condition is asked only where the two types make it possible**, so the comparisons a narrowing needs are decided while
compiling.  Narrowing to a wider type costs nothing; `u32` to `u8` costs one comparison; and the alternative -- always three
comparisons and a branch -- would have made the checked form expensive enough that a generator would avoid it, which is the one
thing a safe narrowing must not be.

Compare: **C**'s implicit conversions, silent and a category of defect on their own; **C++20**'s braced initialization, which
refuses what it can see and says nothing about what it cannot; **Rust**'s `as` beside `TryFrom`; **Go**, silent; **Ada**, whose
`Constraint_Error` says which subtype was violated at the price of exceptions; **Swift**'s failable initializer.  What is here is
Rust's shape with Ada's amount of detail and neither's cost.

---

## 2026-09-16T18:20+02:00 — language

**`import` becomes `⎕import`**

On the user's direction, for consistency with every other name the compiler provides.  It was a keyword, which is the one thing
that made it unlike `⎕typeof`, `⎕wrap`, `⎕enumerate`, `⎕drop`, `⎕unit` and `⎕narrow` -- all of which look like calls, are read by
the compiler rather than called, and carry the sigil to say so.

What the change buys is the thing the sigil exists for: `import` is now an ordinary identifier, so a program may have a variable
called `import`.  That is the stated reason for the sigil on every one of the others, and it applied here as much as anywhere --
a word as ordinary as this one is a word a generator will want.

It stays a shape the parser knows rather than a call the checker recognizes, because what it makes is not a value: a module is a
file that was read, and there is nothing for an expression to come to.  So the parser matches it by what it says rather than by a
token kind, which is what a compiler-provided name is.

Compare: **Python**, **Go**, **Rust** and **Zig** all spend a keyword on it -- except Zig, whose `@import` is a builtin for this
exact reason, and whose `@` is this language's `⎕`.  That this language and Zig arrived at the same answer from the same premise
is worth recording: an import is a compiler operation wearing a call's clothes, and marking it as one costs a character and frees
a word.

---

## 2026-09-16T20:30+02:00 — language

**Lambdas: `λ PARM: TYPE [CAPTURES] → TYPE` and a body**

Decided on the user's direction, with three sub-decisions chosen by the user and one suggested here and taken.

**A lambda is a value like any other** -- bound to a name, handed to a parameter, called through whatever holds it -- chosen by
the user over a narrower version where the compiler had to see which lambda it was.  That is what needed a call through a
register, which the three instruction selectors refused; the assembler underneath already took one, so what it cost was a guard
to replace and an instruction row in each table.

**Its type is written `fn(u8, u8) → u8`**, chosen by the user from three.  The keyword a function is defined with, then what it
takes and what it answers; a reader who knows `fn twice(of: u8) → u8` knows it.  Rust, Go, Swift and Zig all spell it about this
way.

**The capture list is `[n, &n]`**, chosen by the user from four suggestions, with `&` for by-reference on the user's direction --
C++'s spelling and C++'s distinction.  It is free in that position: what may follow a parameter list is a comma, the arrow or the
body, so a bracket there can be nothing else.

**A lambda does not leave the call that made it**, suggested here and not objected to.  What it brought in lives in that call, so
handing one back would hand back a way of reading storage that is gone.  It is exactly the rule references follow, and it is what
lets the captures live in a frame rather than in the arena -- lifetime annotations lift both at once.

Compare: **C++**, whose capture list this is; **Rust**, which infers what a closure captures and sorts closures into three traits
by what they do with it, which is a larger machine for a language that infers; **Go** and **JavaScript**, which capture by
reference and keep the variables alive by collecting garbage; **Java**, which captures by value and requires what it captures to
be effectively final.  Every one of them answers the lifetime question somehow; this answers it by not letting a lambda leave,
which is blunt and is the same answer references got.

---

## 2026-09-16T21:30+02:00 — language

**`[=]` and `[&]`**

On the user's direction, overruling the argument made when lambdas landed -- that a list saying "whatever the body turns out to
use" says nothing, and that the point of writing one is to read a lambda's dependencies off its first line.  That argument is
still true and is still in the specification as what the two cost; what it left out is that a lambda reaching many names has a
list that is mostly noise, and that a generator emitting one already knows what it emitted.  C++ has both forms for the same
reason.

**What they bring in is what the body writes and does not bind for itself.**  Its own parameters are not among them -- those come
from the caller -- and neither is anything it defines inside, however often that name is written.  The order is the order the
names are first written: nothing about a set of names says which comes first, and an order that varied would make two builds of
one program differ.

**They are told from a list of names by what follows the mark.**  `&` begins a capture of a named variable as well, so which it
is, is decided by whether a name or the closing bracket comes next -- one token of lookahead, and no ambiguity in either front
end.

**A capture list cannot be confused with a parameter whose type is a list**, which the user asked to have verified.  It cannot:
a `[` begins a capture list only where a parameter has been read whole, and a type ends at its own closing bracket -- nothing in
the language lets a type be followed by `[`, an array being written with the white brackets and a lookup being an expression.
Checked both ways: the tree-sitter grammar generates with no conflict, and a program writing every combination -- a list
parameter with no capture list, with a named one, with `[=]`, with `[&]`, and two list parameters before a capture list --
compiles, parses and runs.

---

## 2026-09-16T22:30+02:00 — compiler

**What a capture list saying "all of them" brought in goes in the decision log**

On the user's direction.  A decision is something the compiler chose that the program did not state, and `[=]` and `[&]` are
exactly that: the program says "whatever the body reaches" and the compiler says which names those are.  It is the one thing
about such a lambda a reader cannot get from the source, which is what makes it worth recording rather than merely knowable.

**One entry per name.**  The log's `kind` is the part a reader matches on and `subject` is what the decision is about, so a
`capture` entry whose subject is the variable makes "which variables were brought in" a question the log answers directly.  An
entry per lambda listing them in prose would have made a tool parse English.

**A list that wrote its names decided nothing** and is recorded as nothing, which is the line the log has always drawn.

Two more went in with it, both of the same kind.  `name-lambda` records the name a lambda's code was given, because the program
left it unwritten -- it is what ties a symbol in the binary back to the line, and it is what a `capture` entry names to say which
lambda brought the variable in.  `place-local` records a variable put in storage of its own rather than a register, which is a
placement the program did not ask for; it is also what `[&]` costs, since every name such a body writes is given storage before
it is known which ones are brought in.

Writing the tests for it turned up a real defect: a lambda's function was not owned by the file it was written in, so the
module's name never went in front of it and two files each writing one produced the same symbol.  The compiler caught it -- it
refuses a module in which two definitions come to one symbol -- and there is a language test for it now.

---

## 2026-09-16T23:15+02:00 — language

**Everything a capture list brings in is used**

On the user's direction, for named lists and for `[=]` and `[&]` alike.  A name in a list the body never reaches costs room in
what the lambda carries and a copy where it is written, and tells a reader the lambda depends on something it does not -- which
is the worst of the three, a capture list being worth reading only where it is true.

It was a warning before, and the generic one: "the value given to 'n' here is never read", from the rule about a value nothing
reads.  That rule is still what answers the question -- `_Local.read` is asked after the body is lowered rather than the body
being walked a second time -- but what is said about it is now its own error, because a list that brings in what it does not need
is a statement about the program and not a value that went to waste.

**Writing a name is using it.**  A name brought in by reference may be brought in *to* be written, which is the whole of what `&`
is for, and the place it stands for is then never read.  That needed a flag beside `read`, and it corrected the older rule too: a
place written and not read is not a value nobody read, it is the reason the place is there.  C++ says nothing about either, which
is why an unused capture there is a warning at best.

**`[=]` and `[&]` cannot bring in too much**, what they bring in being what the body reaches.  The rule is asked of them anyway:
where it ever fires of one it is the compiler that has got the reaching wrong, and a rule that is checked where it cannot fail is
a rule that says so when the reasoning behind it breaks.  It did break once while this was written -- a name mentioned only as
the target of an assignment was not found, an assignment keeping its target as a string rather than as a name -- and this is what
found it.

---

## 2026-09-17T00:30+02:00 — language

**Generic functions: `fn f(p: T', q: u32) → T'`**

Decided on the user's direction, with the notation asked for and checked.

**`T'` is free.**  The user asked whether it clashes, and it does not: making a quotation mark continue an identifier is enough,
and it cannot begin one, so `'a'` is still a character literal.  The whole suite passes with the rule in place, and the only
thing it costs is a name immediately followed by a character literal with nothing between them -- which nothing readable writes.
It is Haskell's rule and ML's, and the mark has meant "another one of these" in mathematics since Newton.

**A type parameter is declared by being used**, as the user's example writes it.  There is no `<T>` to write and none to keep in
step with the parameters, which is C++20's abbreviated `void f(auto x)` with a name for the type.  What keeps a mistyped type
from becoming a parameter by accident is the mark: an unmarked name that is not a type is still unknown.

**The body is checked at each instantiation**, chosen by the user over checking it once against written constraints.  There is no
language here for saying what a type parameter must support, and designing one is larger than the generics; what instantiation-
time checking costs is that a generic function nobody calls is never checked, and that a mistake in one is found by whoever calls
it.  The error points at the line it is written on and a note says which call asked for those types, which is C++'s "required
from here" and is the half of it that matters.

**Every type parameter stands in a parameter's type**, chosen by the user over letting a call write its types outright.  So the
types always come from the arguments and nothing is ever written at a call -- and a call that wrote them would be a second way of
saying what the arguments say everywhere else.

Compare: **C++**, whose instantiation-time checking this is; **Rust**, **Swift** and **Go**, which check once against bounds and
need a language for them; **ML** and **Haskell**, which infer rather than write and from whom the mark comes; **Zig**, where a
type is a value at compile time and a generic function is one taking it -- the most economical of the lot, and one that needs
types to be values.

## 2026-09-17T01:30+02:00 — language

**Lifetimes: `&mut static u8` and `→ &u8 from v`**

Asked for by the user, who chose the notation from the alternatives after Rust's was offered: name the source, plus `static`.
What it lifts is the pair of rules that closed both places a reference could escape to.

*(`from` was removed on 2026-09-17, a lifetime name saying the same thing and more; `static` and everything below about the
caller, the body and the provenance walk stand unchanged.  4560, 4568 and 3042 are retired and their numbers are not reused.)*

**There are exactly two lifetimes to say, so there are two words and no names.**  A function answers with at most one reference,
so a named lifetime parameter would only ever have one thing to point at; pointing at the parameter directly says the same with
nothing to invent.  `static` says as long as the program, `from v` says as long as what the parameter `v` named.  That is the
whole of it, and it is also the whole of what the compiler can check without a language for relating several lifetimes at once.

**`static` is in the type, `from` is not.**  A lifetime fixed once and for all is a property of the reference, so it stands where
the rest of the type stands and travels with it into a product or a local: `&mut static u8`.  A lifetime borrowed from a
parameter is a relation between two things in a signature, and nothing a type can write names a parameter, so it stands after the
type and speaks for the whole answer.  The split is why a reference held inside something else still may not be answered with
(4531): neither word has anything to attach to.

**Neither may be left unwritten** (4562).  Rust lets the common case go unwritten by elision, which works because there a missing
lifetime is still a lifetime being inferred; here the two answers mean different things to a caller, and defaulting to either
would make a promise the program did not.

**The caller works out the answer's lifetime.**  The function promises no more than its parameter's, so `first(&total)` for a
variable at the top level answers with a lasting reference and `first(&n)` for a local does not -- one signature read twice,
where both the argument and the answer are in view.  The bits are the same either way; the cast the compiler inserts is a bitcast
and the generated code is unchanged.

**The body is held to the promise** (4561).  What comes back is walked back through the instructions that keep a reference
pointing into the same place -- reading the parameter out of its storage, offsetting it, reading the same bits as another type,
and through a call that made the same promise about its own parameter -- and anything else is refused.  A reference that lasts as
long as the program keeps any promise, so it passes wherever `from` was written.

**`static` and `from` are not keywords.**  They are read where nothing else could stand -- `static` only inside a reference type
and only when a type follows it, `from` only after a return type -- so a program may still have a type called `static` and a
field called `from`, which the product type `type Line = from: Point ; to: Point` already did.

Compare: **Rust**, whose question this is and whose `'a` was offered first and declined; **C++**, which has no rule and where a
dangling reference is a program nobody notices is wrong; **Go** and **Java**, which move what escapes to the heap and need a
collector for it; **Cyclone**, whose region annotations are the ancestor of all of this; **C**, **Zig** and **Odin**, which do
not ask.

**A `&mut` is the only reference to its place while it lives** (4563), and a `&` may share with other `&`s.  The user asked for
this alongside the lifetimes, and it is what makes a reference worth having rather than merely convenient: the promise a `&mut`
carries is that nothing else changes the place under it.  The name itself is a way to the place and counts as one, so a name lent
by a `&mut` may not be read (4565) and a name lent at all may not be written (4566).

**How long a reference lives is lexical**, not worked out from where it is last used.  A reference a name was bound to lives as
long as that name's scope; one nothing bound is gone when the statement is, which is what keeps `bump(&mut n); bump(&mut n)`
legal.  That is Rust's rule before non-lexical lifetimes, and NLL was the alternative: it reads more programs, at the price of a
liveness analysis to say where a reference stops existing.  What decided it is that the whole of this analysis is a list of the
references that are out, walked at two places -- and that a generator wanting the place back can open a scope, which is one line
and needs nothing inferred.

**An element is part of its array**, so lending one lends the array.  Two elements are two places, but telling one index from
another is arithmetic, and a promise that depends on arithmetic is no promise.  Rust says the same and offers `split_at_mut` for
where it matters; there is nothing to offer here yet.  **A variable at the top level is not tracked at all**, being reachable
from every function with no one of them able to see what the others do.

Compare: **C++**, where two references to one object is the ordinary case and `std::vector` invalidating its own iterators is a
hazard rather than an error; **Swift**, which enforces the same exclusivity for `inout` and does part of it at run time;
**ML** and **Haskell**, which reach the same place by having nothing to write through.

## 2026-09-17T03:00+02:00 — language

**Named lifetimes: `&mut ⧖x u32`**

Asked for by the user, who put the question that `from` could not answer: a function whose answer may come from either of two
parameters.  Neither `from b` nor `from c` is true of it, so `from` needed either a list or a general form, and the user chose
the general form with the name written after `⧖`.

**The tick was not available**, which is what the user asked about and what decided the glyph.  A leading `'x`, as Rust writes
it, begins a character literal.  A trailing `x'` is already the type-parameter mark, so it would be one spelling with two
meanings -- the rule the whole language is built on.  U+29D6 WHITE HOURGLASS is the one glyph in Unicode that means "how long",
it is free, and being one character it needs no ASCII substitute.  Considered beside it: `⧗` U+29D7, the same in the other
colour; `⌛` U+231B, which many fonts render as an emoji; `⌇` U+2307 and `‵` U+2035, both free and both saying nothing.

**The name stands where `static` does**, after `mut` and before what is pointed at, the two being one slot answering one
question.  So at most one of them is ever written and no rule is needed to say which wins.

**A lifetime is declared by being used**, as a type parameter already is.  There is no `<'a>` to write at the head and none to
keep in step with the parameters; what makes a name mean anything is that it stands in a parameter's type, and a name the answer
carries and no parameter does is refused (4567).  That is the generics decision applied again, and it is why Rust's declaration
form was not taken even where its idea was.

**Several parameters carrying one name means the shorter of what they named.**  It is the only promise that holds whichever one
the body picked, and it makes the caller's rule the obvious one: the answer lasts as long as the program exactly where every
argument carrying the name did.

**`from` stays**, as the shorter way of saying it where one parameter is named, and writing both on one signature is refused
(4568) rather than read twice.  Whether two spellings should exist at all is the open question the to-do list now carries: `from
v` is exactly `⧖a` on `v` and on the answer, and one meaning with two spellings is what this language does not do elsewhere.

Compare: **Rust**, whose `'a` this is, with the name declared at the head and a leading tick; **Cyclone**, whose region
variables are the ancestor; **C++**, which has none of it and where a dangling reference is a program nobody notices is wrong;
**Go** and **Java**, which move what escapes to the heap; **ML** and **Haskell**, which reach the same place by having nothing
to write through.

## 2026-09-17T04:15+02:00 — language

**A lambda may be `@[listable]`, and so its type carries it**

Asked for by the user, who asked for a syntax to be suggested and chose the one offered.

**The same word in the same place.**  `@[listable] λ a: u8 → u8` is what a function already writes, before the
thing it describes, and nothing else begins an expression or a type with `@[` -- so it parses with nothing to disambiguate.
Considered beside it: the list after the `λ`, which keeps it out of statement position at the cost of writing the same thing
two ways; and a glyph on the arrow, `λ a: u8 ⇝ u8`, which is shorter and says it in the type by construction but is a
second spelling for what a `fn` already says with a word.

**It is part of the type**, which is what makes it worth anything.  A lambda is nearly always called through a name, and what a
name holds is a type; a promise the type did not carry would be one the caller never heard, since the caller is who does the
walking.  It is the argument references already make for `mut`: both sides reach the thing, so the caller cannot be left to
guess.  A function definition needs none of it, a call naming one having the definition in view.

**Only what a caller reads off the type may be said there** (3204).  `inline`, `impure` and the rest are about a body, and a body
is not what a name holds.  They get a target of their own, `AttrTarget.CALLABLE`, so the existing "does not apply" diagnostic
says it.

**A listable function stands where a plain one is wanted**, dropping the walk, chosen by the user over two unrelated types.  It
promises everything the plain one does and adds to it, which is the rule `&mut T` and `&static T` follow, and it goes one way
only.  The pair of addresses is taken apart and put back under the plainer type -- a function is two addresses, so no one
instruction can read it as another type -- and the registers are the same registers.

Compare: **APL**, **BQN** and **UIUA**, where every primitive walks and there is nothing to write; **Julia**, whose `f.(v)` puts
the mark at the call, so the caller decides and no type carries it -- the honest alternative to all of this, and one that would
have meant a second spelling for what `@[listable]` says on a `fn`; **NumPy**, whose `vectorize` wraps a function in a function,
which is what this would have to be if the answer were not to put it in the type; **C++**, whose `std::function` carries no such
thing and whose ranges say it at the call.

## 2026-09-17T05:00+02:00 — language

**A named function is a value, and its type carries `@[listable]`**

Asked for by the user, closing the gap the previous entry left open: a lambda carried the walk in its type and a function did
not, which was one idea with two lives.  It turned out that a named function could not be handed over as a value at all.

**One representation, not two.**  A function value is two addresses whether it came from a `λ` or from a definition, so a
definition needs a shim -- code that takes the environment nobody wrote, drops it, and hands the rest on.  The alternative was a
second representation with a tag saying which kind it holds, which costs a branch at every indirect call to save one call at
some of them; the shim costs one call and nothing else, and is made once per function however many times the name is written.

**Its type is built from its attributes**, as a lambda's is, so `func.ty.listable` is the one place the walk is written down.
The symbol does not move, `mangle` writing the parameters and the result one by one rather than the whole type.

**A generic function is not a value** (4569).  It is compiled once per set of types and which sets those are is what the calls
ask for; named where a value is wanted there is no call to ask, so there is no one function for the name to stand for.  What was
said before was that the name was not defined, which was not true of it.

Compare: **C**, where a function name decays to a bare address and a callback needs a `void *` written out beside it; **C++**,
whose `std::function` is this pair at this price; **Go**, where a method value is the same pair; **Rust**, which tells the two
apart in the type system -- `fn` for the address, `Fn` for the pair -- and so needs no shim for the first, at the cost of a
distinction every signature has to make.

## 2026-09-17T05:45+02:00 — language

**`from` is removed, a lifetime name saying the same thing**

Directed by the user, closing the question the named-lifetimes entry left open.  `→ &u8 from v` was exactly `⧖a` written on
`v` and on the answer, so the language had two spellings for one meaning -- which it does not do anywhere else, and which the
refusal to write both on one signature (4568) only papered over.

**What goes with it**: 3042, the name expected after the word; 4560, the word naming something that is not a parameter; and 4568
itself, there being nothing left for it to be about.  Their numbers are retired rather than reused, a number being the contract
between implementations.  4561 stays and is now about a lifetime name, 4562 stays and now offers `static` or a name.

**`from` becomes an ordinary word again**, which it always was in a program: it was read only after a return type, so a field
called `from` already worked.  Now nothing reads it anywhere and `static` is the one word the language looks for.

What the shorter form bought was two characters on the most common signature, and what it cost was a second mechanism with its
own three diagnostics for a case the general one already covered.  Rust keeps a shorter form of its own -- elision, which writes
nothing at all -- and that is a different trade: it removes a spelling rather than adding one.

## 2026-09-17T06:30+02:00 — language

**`&x` takes whether it may write from what is wanted**

Reported by the user as a program that should not have been refused: `let r: &mut u32 = &v` said the type on the left and was
told the value on the right was the wrong one.

**What a type is written down for is to say what goes in it.**  Whether a reference may be written through is part of its type,
so a context that states the type states that too -- which is the rule an integer literal already follows, and the reason
`let n: u8 = 1` needs no suffix.  `mut` on the right is now written where nothing says: a name whose type is read off its value,
the wildcard, an argument of a call being walked.  Writing it where the context already says it is allowed and says the same
thing.

**What it costs** is the thing a call used to say outright: `bump(&mut n)` announced at the call that the callee may write, and
`bump(&n)` does not.  What is kept is that a reference is still taken explicitly, which is the decision this does not touch --
`bump(n)` is still not a way to hand a place over.  A reader who wants the louder form still writes it, and the compiler's own
output does.

**The place must still allow it** (4537), whichever of the two said the reference may write, so nothing is reachable now that was
not before.  The aliasing rule sees the same thing: `&v` bound to a `&mut u32` records the exclusive borrow.

Compare: **Rust**, where `&mut x` is always written and there is no inference of it; **C++**, where the distinction lives in the
callee's signature alone and a call says nothing at all -- the far end of this line, and the reason the reference itself stays
explicit here.

**A cascade may not claim the compiler is unfinished**

Found while fixing the above: the refused line left the name bound to the error type, and a literal compared against it then
reported that an integer literal with no context is not implemented yet.  That is a claim about the compiler rather than about the
program, it is fatal, and a reader has no way to tell it from the real thing.  A literal measured against the error type now
answers nothing, quietly.  It is a rule about this compiler and not about the language, so it is written down in the
implementation notes rather than the specification.

## 2026-09-17T07:30+02:00 — language

**A block may be written on the line its colon stands on**

Asked for by the user, who asked whether it could be done without creating conflicts in the grammar.  It can, and the measuring
is what decided how.

**It is the layout notation with the indentation left out**, not a third one.  The same statements, the same separators, the same
everything: what it buys is a short thing written short, and what it costs is nothing to learn.  `if b: 0u6 else: 1u6`,
`while n < 3u8: n ← n + 1u8` and `fn twice(a: u8) → u8: a + a` all follow from the one rule.

**The scanner opens it, not the grammar.**  Written as a grammar rule it does create conflicts, and they were counted: a rule
holding any statement converges only after nine declared conflicts, spread through the expression grammar, assignments, units and
loops; a rule holding a single statement still needs one, `if b: x ⊼ y` being readable as the arm's operator or the whole
`if`'s.  A fourth external token standing where the end of line and the indent stand in the other reading costs none, because the
two readings then differ by a token rather than by a guess.

**A block written this way may not open another** (3044), chosen by the user over the rule C and its family use.  The inner block
would end where the outer one does, so an `else` after the two would belong to either; binding to the nearest `if` settles it at
the price of a mistake nobody sees.  Refusing it costs nothing a reader wants: the inner block in braces says where it ends, and
the outer one written out says it the other way.

**The arm takes the whole expression.**  `if b: x ⊕ y` is an arm of `x ⊕ y` and never an `if` an operator is applied to;
parentheses say the other.  That is what every language with this form does, and the only reading in which the colon opens a
block rather than introducing one operand.

Compare: **Python**, whose `if b: x` this is and which refuses the nested case for the same reason; **Haskell**, whose layout
rule has the same notion of a block opened without a newline; **C**, **Java** and **Go**, where the braces are the block and the
dangling `else` is settled by a rule or by requiring them.

## 2026-09-17T08:15+02:00 — language

**A name may hold a reference only to a place that lasts as long as it does**

Reported by the user as a program that should not have compiled: a reference taken inside an arm, assigned to a name bound
outside it, and read after the arm had ended.

**It is the rule that was already there, asked at the third place.**  A function may not answer with a reference that says
nothing about how long it lives (4562), and a variable at the top level may hold only a lasting one (4532).  Both are about a
reference outliving what it names; this is the same thing within one body, where the two scopes are the arm and the body around
it.  Outward is always allowed and inward never, and that is the whole of the rule (4570).

**Depth is what decides it**, not a lifetime name.  A place is as deep as the scope it was made in and a name as deep as the
scope it was bound in; the reference is walked back to the name whose place it is, the way provenance is walked for everything
else about references.  Lifetime names would say more -- they relate two things rather than order them -- and are not needed for
this: within one body the compiler can see both ends.

**It is asked of a place that holds a reference too.**  `rr⌖ ← &v2` puts a reference where `rr` names, and how long that
lasts is how long what names it does.  Without that the rule would be one indirection deep.

**What is left open** is a call that stores one of its arguments into another: `fn keep(slot: &mut &mut u32, r: &mut u32)`
writing `slot⌖ ← r` is right for any one call and wrong for a caller that hands in a long-lived slot and a
short-lived reference.  Closing it needs a signature able to say that two parameters live as long as each other, which is Rust's
`'a: 'b` and which the lifetime names here cannot yet write.  *(Shut on 2026-09-17 by refusing the write rather than the call;
see below.)*

Compare: **Rust**, whose borrow checker asks exactly this; **C++**, **C** and **Odin**, which do not, and where a pointer
outliving its block is the oldest mistake there is; **Go** and **Java**, which move what escapes to the heap.

## 2026-09-17T09:00+02:00 — language

**A place the call did not make takes only a lasting reference**

Directed by the user, closing the hole the previous entry left open and two more like it that turned up while looking.

**The hole was a function writing one of its arguments into another.**  `fn keep(slot: &mut &mut u32, r: &mut u32)` writing
`slot⌖ ← r` is right for any one call and wrong for a caller handing in a long-lived slot and a short-lived
reference.  Rust writes the rule -- `'a` on both -- and refuses the *call*; the lifetime names here relate a parameter to the
answer and cannot say it.  So the *write* is refused (4571), which is sound, needs nothing new in a signature, and costs the
programs that would have been right.  What may still go there is a reference that lasts as long as the program, which outlives
any caller.

**What made it decidable** is that `_named_place_of` answering nothing about a place is not "it outlives everything" but "this
body cannot say".  Reading it the first way is what let the write through.

**Two more carriers, found by asking what else reaches a place.**  A tuple holding a reference lasts as long as the
shortest-lived thing in it, and a lambda lasts as long as the shortest-lived name it brought in by reference -- both could be
handed to a name that outlives them, and both are now asked the same question (4570).  What a lambda brought in by value ties it
to nothing, a name standing for a place handing over what is at the place; that is why only the by-reference captures count.

Compare: **Rust**, which writes the relation and refuses the call, and which can because its lifetimes relate any two things;
**C++**, where a reference member outliving what it refers to is the ordinary hazard; **Go** and **Java**, which move what
escapes to the heap and so never ask.

## 2026-09-17T10:00+02:00 — compiler

**Colour in the diagnostics, and the snippets highlighted by the grammar**

Asked for by the user, who said which highlighter to use.

**Colour is decoration and never information.**  Everything a colour says is
said by the text as well, so the plain output is unchanged to the byte and a
test compares the two.  `NO_COLOR` wins over the option, a program that sets it
having said it is reading this.  *(The option was `--color=auto|always|never`
when this was written; the user settled it on 2026-09-17 as
`--color[=yes|no|auto]`, looking at the standard output.  See below.)*

**The grammar highlights, not the lexer.**  The compiler has a lexer and could
have coloured a line from it with no dependency at all -- but a lexer knows a
name is a name and not that this one is a type and that one a parameter.  The
grammar knows, and its queries are the ones an editor already uses, so a snippet
is coloured the way the same line is coloured where it was written.  One
description of the language rather than two that drift.

**The dependency is optional and stays optional.**  `tree-sitter` is a named
extra and nothing else; without it the snippet is plain and nothing is said,
since a compiler complaining about its own decoration would complain on every
line.  That the compiler needs nothing outside the standard library is worth
more than a coloured snippet.

**Eight colours, not 256.**  What is gained by the larger palette is a shade,
and what is lost is every terminal that has not got it.  What is coloured is
what a reader looks for -- the severity, the place, the carets, and the pieces
of a line that carry meaning; operators, brackets and ordinary names are left
alone, a line in which everything is coloured being one in which nothing stands
out.

Compare: **Clang**, whose `-fcolor-diagnostics` and green carets this follows,
and which highlights nothing inside the snippet; **Rust**, which colours more
and draws more; **GCC**, which added `-fdiagnostics-color` and the same
`auto`/`always`/`never`; the **`NO_COLOR`** convention, which all three honour.

## 2026-09-17T10:45+02:00 — compiler

**The decision log becomes the report log, and holds what the compiler said as well**

Directed by the user, who asked for three things at once: one word for it, more in it, and the lifetime answers written down.

**One word, and the word is *report*.**  What the log holds was called a decision, which was accurate while it held only choices
and became wrong the moment it held diagnostics too.  `--decision-log` is `--report-log`, `DecisionKind` is `ReportKind`,
`decisions.py` is `reports.py`, `bin/pl4g-decisions` is `bin/pl4g-reports`, and the JSON key is `reports`.  This file keeps the
word *decision*, being about decisions people made and not about what the compiler writes down; the two were already
distinguished in its header and now they are distinguished by name.

**What the compiler said goes in beside what it chose**, in one order.  They are different things and `kind` keeps them apart,
but the question a reader has -- what happened to my program? -- is not a question about only one of them, and looking in two
places for "this function is not in your binary" and "you never read what you gave this variable" is looking twice.  A diagnostic
carries the number the catalog gives it; a choice has none, and that absence is what tells the two apart without matching kinds
one by one.

**What was reported, not what might have been.**  A warning a `-W` setting quieted or an `ignore` attribute absorbed is not in the
log, and the severity written down is the one after `-Werror` has had its say.

**Every lifetime answer is written down**, and not only the ones that come out lasting.  It is the one thing about such a call
that neither the signature nor the call site says: a name on two parameters says the two are equal and says nothing about which
of the arguments the answer took its lifetime from, and that is a fact about the one call.  Where two live equally long both are
named, since naming one would read as though the other had been turned down.

**The log's format version goes to 3**, and the viewer reads all three: the key was renamed and entries of a new shape appeared,
and a log written by an older compiler is still a log someone has.

## 2026-09-17T11:15+02:00 — compiler

**`--color[=WHEN]` takes `yes`, `no` or `auto`, and looks at the standard output**

Settled by the user, who gave the spelling, the values and the test.

**The value may be left out, and leaving it out means `yes`.**  A switch written
with nothing after it asks for the thing it names, which is what `--color` alone
has meant since GNU `ls`; the value is what says *when*, and whoever wrote no
value meant now.  The help text spells it `--color[=WHEN]`, brackets and all,
being the first option here whose value is optional.

**`yes`, `no`, `auto` rather than `always`, `never`, `auto`.**  The words a
person would answer the question with.  `auto` is the default, so a run that
says nothing behaves the way a run has always behaved.

**`auto` looks at the standard output**, although the diagnostics go to the
standard error.  What it answers is "is a person watching this run", which is a
question about the run and not about one of its streams -- a build that keeps
the errors in a file is still a build someone is sitting in front of.  It is the
reading GCC takes for `-fdiagnostics-color=auto`… which looks at the stream it is
writing to, and the one settled here is the other.  Both are defensible and this
one was chosen.

Compare: **GNU `ls`**, whose `--color` with no value means `yes` and whose
values are `never`, `always`, `auto`; **GCC** and **Clang**, whose colour
options take `never|always|auto` and test the stream they write to; **`NO_COLOR`**,
which all of them honour and which wins here over anything the option says.

## 2026-09-17T12:00+02:00 — compiler

**`argparse` parses the command line, and there is a `build` command**

Directed by the user, who asked for `argparse` and for subcommand handling, and
added that `build` must not require `-o`.

**The shared table stays the contract, and a test keeps both sides on it.**  The
options were parsed by hand so that every refusal was a numbered diagnostic;
that reason is kept by overriding `ArgumentParser.error` and `.exit` rather than
by parsing by hand.  What is *not* done is generating the parser from the table:
a contract wants both sides checked against it, so the parser is written out and
a test compares the two sets of names in both directions.  It caught a typo the
moment it was written.

**`build` and `test`, and a command line naming neither means `build`.**  Every
command line that worked before this still works and means the same thing.
`test` is declared and reports that it is not implemented, as `--incremental`
does: there is nothing behind it yet, the functions `@[test(...)]` marks being
collected as reachability roots and nothing more, and a command that quietly did
the wrong thing would be worse than one that says so.  What was *not* done is
turning `--version` and `--print-targets` into commands as well: they work, they
are what every build script writes, and a second spelling for each would buy
nothing.

**The output is no longer something the command line has to say**, at the user's
direction.  The sources say what the program is called: `pypl4g prog.pl4g`
writes `prog`, and `--emit` decides the suffix.  Diagnostic 1001 is retired, an
empty command line naming no source being the thing actually missing.  It is
`cc`'s `a.out` question answered the other way, and the way `rustc` and `go
build` answer it: a name that was written down is better than one that was not.

**Two forms are normalised before `argparse` sees them**, `-O` and `--color`.
Both may be written with no value, and `argparse` would take the next word --
`--color prog.pl4g` would colour "prog.pl4g" and compile nothing.

Compare: **`cc`**, whose `-o` defaults to `a.out` and whose options are parsed by
hand for exactly the reasons this one was; **`cargo`** and **`go`**, whose
subcommands this follows and whose default output is the package's name;
**Python's `argparse`**, whose greediness over optional values is the one thing
here that had to be worked around.

## 2026-09-17T13:00+02:00 — language

**`@[test(ARG)]`: three kinds, and the binary each of them is in**

Asked for by the user, who gave the three kinds, what each runs in, and when.
What was settled here is the signature, chosen by the user from the
alternatives.

**A test takes nothing and answers a truth value** (4407).  Nothing calls one
but the runner, so there is nothing to give it; what it answers is whether it
passed, and a truth value is the whole of that.  The alternatives were a status,
which is a second way of saying the same thing, and nothing at all with a fault
for a failure, which is what a test that cannot say "no" is left with.

**With no argument and no parentheses it is a `suite` test.**  That is the kind
most tests are, and an attribute carrying no arguments is written without
parentheses everywhere else here.

**What differs between the kinds is which binary the test is in.**  Not what it
says and not how it is written: an `always` test is in the program and runs
before the startup function is reached, and the other two are in a binary the
compiler builds to run them.  So the difference is settled in one place and each
back end emits the same shape of code around it.

**An `always` test rides the constructor path**, which already called things
before the startup function; what it adds is looking at the answer.  A test that
answers false leaves through the helper a fault leaves through, naming itself: a
program found to be wrong is what that helper is for.  What that costs is that a
run stops at the first failure, the helper having no way to write and carry on.
It is a to-do entry rather than a second helper written now.

**A cross build says what it did not run** (1014) rather than refusing.  The
user directed this: an emulator is used only where a command line names one,
`--test-runner=COMMAND`, and never by looking for one.  Refusing would be
refusing the ordinary case, which is cross-compiling; running whatever emulator
happened to be installed would be a build whose meaning depended on the machine.

Compare: **Rust**, whose `#[test]` functions go into a separate test binary
exactly as `build` and `suite` do, and whose runner reports every failure;
**Go**, which makes the same split by file name; **D**, whose `unittest` blocks
run at startup when the program is built with them, which is what `always` is;
**C** and **C++**, which have none of it and need a framework.

## 2026-09-17T14:00+02:00 — language

**A value of a product is written `Point(x: 1f64, y: 2f64)`**

Chosen by the user from the three the to-do list had carried since products were
added.  The call shape reused, with the named arguments a call already takes, so
there is nothing new to read; a sum names its variant the same way, the two being
one construct in their definitions and one shape here.

Considered: `Point{x: 1f64, y: 2f64}`, after Rust, Go and Zig, which reads well
but gives `{}` a second job in a place a block never stands; and a bare
`(x: 1f64, y: 2f64)` taking its type from the context, which is shortest and is
what an unsuffixed literal already does, at the cost of nothing naming the type
where the value is written.

## 2026-09-17T14:15+02:00 — language

**I/O: `std`, three descriptors, and a ring the startup code makes**

Designed at the user's direction and written down before any of it is built,
since what it waits on is three other things.

**Everything goes through io_uring**, which the user settled.  A ring is made by
the startup code where something the program reaches needs one -- not only I/O:
waiting for a process to end will want it too -- the way the arena and the fault
helper are already emitted only where something asks for them.  For now no
thread serves completions: they are reaped where the ring is used, by the calls
that read and write.

**The names.**  Three descriptor types, named for what may be done through them
rather than for what they are made of:

```
type Reader      ※ a device that can be read
type Writer      ※ a device that can be written
type ReadWriter  ※ both, for a socket or a file opened either way

fn read(from: &mut Reader, into: &mut u8⟦⟧) → u64 ¤size?
fn write(to: &mut Writer, what: u8⟦⟧)       → u64 ¤size?
```

A submitted request carries a `Pending`: the state the kernel completes against,
the buffer it reads into, and what is to become of it.  The ring is a `Ring`,
and the program never names one -- what it names is a descriptor.

**The startup function is given what the program starts with**, at the user's
direction: `@[startup] fn main(init: std.Init) → u6`, with the three predefined
descriptors at `init.io.input`, `init.io.output` and `init.io.errors`.  `Init`
rather than the descriptors outright so that what a program is started with --
its arguments, its environment, what it inherited -- has somewhere to go later
without changing every signature that exists by then.

**Ownership is static and costs nothing new.**  `init` is a local of the startup
function, so `&mut init.io.output` is exclusive by the rule that already refuses
a second `&mut` to a place; a run-time check and a lock are both unnecessary.
What it costs is that lending one field lends the whole of `init` for as long as
the reference lives, the aliasing rule not telling one field of a local from
another -- which is a to-do entry and not a reason to choose differently.

Considered and turned down: a `std.output()` answering the descriptor the first
time and a failure after, which is Rust's shape and a run-time check where a
static one is available; and tracking variables at the top level in the aliasing
rule, which would be exact and independent per descriptor but needs the
whole-program reasoning that rule deliberately avoids.

**What it waits on**, in order: a value of a product can be written; a field of
one can be read; a product can be handed to a call and answered with.  All three
are in the to-do list.  `⎕syscall` and ordering in the IR are the compiler's half
and are listed there.

Compare: **Rust**, whose `Stdout` is taken by a call and guarded by a lock, and
whose `io_uring` crates are libraries rather than the only way; **Go**, which
hides the whole question behind a scheduler and a thread pool; **Zig**, whose
`std.io` passes a writer explicitly and whose `std.os.linux.IoUring` is driven by
hand as this will be; **C**, where the descriptors are three integers anyone may
write to at any time, which is the thing being designed away.

## 2026-09-17T15:00+02:00 — language

**`⎕syscall(NUMBER, ARG...)`: the one way out of the process**

The first of the things I/O waits on, and the one that waits on nothing.

**It is the language's, not a library's.**  There is no C library underneath and
nothing else to call, so a program that is to reach anything outside itself
reaches the kernel, and the compiler is what knows how to enter it.  `C` makes
`syscall(2)` a variadic function of its library; **Zig** names one per arity in
its own; **Go** has `syscall.Syscall`; **Rust** has none and reaches for a
crate.  Here it is written with the sigil every name the compiler provides
carries.

**The number and the arguments are the kernel's.**  Nothing in the compiler
knows what call one is, and the numbers differ between architectures -- `write`
is 1 on x86-64 and 64 on the other two.  Naming them is the standard library's
work: putting a table of them in the compiler would be the compiler carrying a
copy of somebody else's header.

**What comes back is the kernel's own `i64`**, negative where it refused, and
not a result.  Turning `-EAGAIN` into a failure is reading, and what does the
reading is whatever knows which call it asked for; a compiler that wrapped it
would have to know the calls to know what an error means for each.

**Everything given is a machine word** (4572), widened by the checker rather
than by three back ends.  That is what keeps each selector to one shape: by the
time one sees a request to the kernel, everything in it is the width of a
register.  At most six follow the number (4573), there being six such registers
everywhere this compiler generates for.

**It is one instruction and not a call.**  What enters the kernel is one
instruction of the architecture and what it takes is named by the kernel, so a
calling convention has nothing to say about it.  It stays outside the memory
token chain as a call does: what the kernel does to memory is not something the
program can write down.

## 2026-09-17T15:45+02:00 — language

**`⎕sc@write`: a name the compiler provides, carrying a key**

Directed by the user, who gave both the shape and the reason: the compiler must
know the numbers for every architecture and it knows which one it is building
for, so a module says `write` and means whichever it is.

**The mark is `@`, and only a name beginning with the quad may carry one.**
`a@b` is still two things with nothing between them and `@[` still begins an
attribute list; what changed is one line of the lexer, which lets a name the
compiler provides go on past an `@`.  The whole of `⎕sc@write` is one name and
one lookup, which is what "a dictionary key inside the compiler" means.

**It turns over what the previous entry gave to the library.**  An hour earlier
this log said naming the calls was the standard library's work, and that a table
of numbers in the compiler would be the compiler carrying a copy of somebody
else's header.  It is carrying one -- and the reason is that the alternative is
worse: `comptime if` compares types and refuses a question about a value (4502),
so a module choosing between 1 and 64 for itself would have needed either three
builtin type names existing only to be compared, or comptime evaluation of
values, to say something the compiler already knows.  The table is kept small
and honest instead: what the runtime and the library ask for, each number
checked, and a call nothing asks for is not in it.

**A call one architecture has and another has not is its own refusal** (4575),
told apart from a name that is simply mistyped (4574).  `open` is on x86-64 and
not on the two numbered later, which took only `openat`; `fadvise64_64` is the
other way round.  A module that wants such a call asks inside an arm the
compiler settles.

Compare: **Zig**, whose `std.os.linux` has the numbers in the library, per
architecture, written out; **Go**, whose `zsysnum_linux_*.go` are generated per
architecture and live in the library too; **C**, where they come from the
system's headers and not from the compiler at all -- which is the arrangement
this has no equivalent of, there being no headers and no system library here.

## 2026-09-17T16:30+02:00 — language

**A record has values: `Point(.x ← 3u32, .y ← 4u32)` and `p.x`**

The syntax was settled earlier today as the call shape with the arguments named.
What that turned out to mean, once it met the language as it stands, is the
`.name ← value` a call already has and not a `name: value` of its own: the
first spelling exists, already parses inside a call's argument list, and is
documented in the syntax tree as the spelling a structure's initializer takes in
C, Odin and Zig.  Adding the second would have been a second way to name a thing
in an argument list, which is the rule this language is built on.  *(The option
chosen was labelled `Point(x: 1f64, y: 2f64)`; this is the same idea in the
spelling the language already had, and going back to the other is a parser
addition rather than a redesign.)*

**A record travels as its fields.**  `parts_of` answers a record with its field
types, so to everything below the checker it is what a tuple is: several values
going together, placed by a convention the same way.  A record handed to a call
or answered with therefore needed no rule of its own, and the code generator
that refused one (8501) stopped refusing it.  What differs between a record and
a tuple is that one of them named its parts, which is a question for the checker
and for nothing else.

**Every field is given and each once** (4577, 4578).  C fills a field left out
with zero; that half of the designated initializer is not taken, zero being a
value like any other and a program that meant it being able to write it.  A
field left out would otherwise be storage holding whatever was there, which is
the thing this language does not have.

**What is still refused**, and both for one reason: a record holding a record,
and a record reached through a reference.  A record travels as its fields, so a
field that is itself several values has no one register to go in, and a load of
a multi-part value is not a thing the back ends do.  The answer to both is the
same -- a record that lives in memory as a fixed array does, with a field read
at an offset and `&p.x` an address -- and `member_offsets_of` already computes
that layout.  `std.Init` needs both, so it is the next step and not a someday.

Compare: **Rust**, **Go** and **Zig**, whose structure literals name the fields
and whose access is the same mark; **C**, whose designated initializer this is
written like; **ML** and **Haskell**, whose records are the same idea with the
type inferred rather than written.

## 2026-09-17T17:00+02:00 — language

**A record is a value and is copied, not a place that is shared**

Chosen by the user.  `let q = p` gives a second record, and writing through one
name leaves the other as it was -- which is what C, Rust, Go and Zig all do, and
what the register-carried representation already does today by accident of
travelling as its fields.

**What made the question worth asking** is that a fixed array does the opposite:
`let b = a` names the same elements, because a value of an array type *is* where
the elements are.  That is nowhere in the specification -- it falls out of the
representation -- and it is now a to-do entry rather than a thing the language
says.  A record in memory would have inherited it silently, which is the trap
this decision closes.

**What it means for the two things still refused.**  A record holding a record,
and a record reached through a reference, both want a record that lives
somewhere; being a value is what says that binding one to a name copies the
storage rather than sharing it.  `offsets_of` already computes the layout, and
`largeanswers` already hands anything larger than two registers back through
storage the caller provides -- which is where the value semantics of a call and
an answer already come from, and is why answering with a record worked the day
it could be written at all.

Compare: **C**, **Rust**, **Go** and **Zig**, which all copy a structure on
assignment and which this follows; **Java** and **Python**, where every such
thing is a reference and copying is a method call; **collections here**, which
the log already settled as handles that are shared -- a record is not one of
those, being its fields rather than a way to reach them.

## 2026-09-17T18:00+02:00 — implementation

**A record travels as its leaves, so `parts_of` flattens a nested field**

The question was how a record holding a record is handed to a call and answered
with, the last thing records could not do.  Two answers were written down: make
`parts_of` flatten recursively, so a nested field is no longer one `extract`; or
say that such a record travels through storage, which is what the C ABI does for
anything that does not fit its classes.  Flattening was taken, being the smaller
change and the one that keeps a small record in registers.

**What it costs** is exactly the thing named: nothing can take a whole nested
field out of a record *value* with one instruction, because the value has no
part that is that field.  Reading one is reading its leaves, and `_leaves_of`
and `_leaves_from` are the two directions of that.  Where a whole nested field is
wanted from a value -- `whole(l)` answering a `Point` -- the record is put in a
frame and the field read from there, which is a store and some loads that the
optimiser may remove and the language never mentions.

**What it buys** is that every part of a record is one value, which is what
everything below the checker already assumed: `through_storage` asks exactly
that question and now always gets yes, the instruction selectors see only
leaves, and `part_offsets_of` is the single place saying where each of them
lies.

Compare: **C**, whose System V ABI classifies a structure by its *fields
recursively* into eight-byte units -- flattening, with the classes on top;
**Rust** and **Go**, which pass anything large through storage and small
aggregates in registers by a similar flattening; **Zig**, which puts the rule in
the language and passes a large structure by pointer visibly; **ML** and
**Haskell**, where a record is boxed and one pointer travels, which is the
answer this does not take.

## 2026-09-17T19:00+02:00 — language

**The `std` module, and a startup function that takes what it was started with**

The first of the I/O design of 2026-09-17T14:15, which waited on three things
about records and now waits on nothing.  `modules/std.pl4g` defines `Reader`,
`Writer`, `ReadWriter`, `Io` and `Init`; `@[startup] fn main(init: mut std.Init)
→ u6` is a signature the compiler accepts; and `std.write` goes
to the kernel with `⎕syscall`, which the ring will
replace and the interface will not notice.

**Which type `Init` is, is settled by where it was written down** and not by its
shape.  A record a program defines for itself and calls `Init` is a record it
defined for itself, and the entry point hands the descriptors to the one type the
compiler knows -- the one in the module the installation provides.  Checking the
shape instead would let a program be handed three file descriptors by accident,
which is the kind of thing this language is for refusing.  The other way, an
attribute in `std.pl4g` marking the type, was turned down because a program could
write the attribute too.

**The descriptors arrive in registers and not through storage.**  `parts_of(Init)`
is three `i32`s once a nested field is flattened, so the entry point writes 0, 1
and 2 into the registers `argument_places` names and calls.  `target/started.py`
holds both the numbers and that mapping, so the three entry points share the
decision rather than each stating it; it asserts there are as many registers as
descriptors, which is what catches a field added to `Io` on one side only.

**Exclusivity is what the aliasing rule already says.**  `&mut init.io.output` is
exclusive because a second `&mut` to the same place is refused, so two names for
one device is a compile error and not a lock -- and the cost the design predicted
is the cost paid: lending one field lends the whole of `init`, the rule not
telling one field of a local from another.  That is a to-do entry, and the entry
existed before this landed.

**What `write` answers is the kernel's number**, an `i64` negative where it
refused, rather than the `u64 ¤size?` the design wrote down.  A result wants
somewhere for the error to go, which is an enumeration of what the kernel says
and a table that does not exist yet; the signature changes when it does, and the
to-do list says so.

Compare: **C**, whose `main` takes the arguments and whose three descriptors are
integers anyone may write to at any time; **Rust**, whose `std::io::stdout` is
taken by a call and guarded by a lock, a run-time check where a static one is
available, and whose `main` takes nothing; **Go**, whose `os.Stdout` is a package
variable; **Zig**, which passes a writer explicitly as this does and whose `main`
may take an allocator and the arguments; **Haskell**, where the whole question is
inside `IO` and the descriptors are handles of the library.

## 2026-09-17T20:00+02:00 — implementation

**Acquire and release are said on the read and the write, not by a fence**

The memory token, settled on 2026-09-12, says that two accesses of one program
happen in an order.  It says nothing about the order a *second* observer sees
them in, and a ring shared with the kernel is exactly that question.  So a load
may acquire and a store may release, written on the instruction: `load.acquire.u32`,
`store.release.u32`.

**On the instruction rather than as a barrier of its own**, which was the
alternative.  A barrier is a second thing that has to be kept next to the access
it belongs to, by every pass that moves anything; the word on the access cannot
come apart from it.  It is also what two of the three machines actually have --
AArch64's `ldar` and `stlr` are one instruction each -- so a barrier would have
been a shape the compiler invented and then had to fold back.  RISC-V, which has
no such form for a plain load, gets the fence its architecture asks for and the
selector is the one place that knows.

**What each machine answers**, which is the whole of what an ordering costs:
x86-64 emits what it would have emitted, its reads already acquiring and its
writes already releasing; AArch64 emits `ldar`/`stlr`, which carry no offset, so
the selector adds one first; RISC-V emits `fence r, rw` after the read and
`fence rw, w` before the write.  A test asserts each of those three, instruction
by instruction, because "it compiled" would not have told them apart.

**An ordered access is one access.**  A value of several parts is several, and
which of them the ordering belonged to would have no answer, so it is refused
(8501); floating point is refused too, the ordered forms naming integer
registers.  A `load.acquire` counts as having an effect although a plain load
does not: what it does is order what comes after it, which a read nobody looks
at does as much as one somebody does.

Not taken, and not needed yet: sequential consistency, which on x86-64 is the
one ordering that costs an instruction -- a write followed by a read of another
place -- and which nothing driving a ring asks for.

Compare: **C11** and **C++11**, whose `memory_order` is a parameter of the
atomic operation and not a separate fence, which this follows; **Java**, whose
`volatile` says the same thing by being a property of the field; **Rust**, the
same as C11; **Go**, which has no such thing in the language at all and puts it
in the library; **Linux's own `smp_load_acquire`**, which is where this pair of
fences on RISC-V comes from.

## 2026-09-17T21:00+02:00 — language

**`⎕acquire` and `⎕release`: the ordering is said at the access**

Chosen by the user from three.  `⎕acquire(REF)` reads a place and `⎕release(REF, VALUE)`
writes one, each saying what a second observer may see; they carry the sigil
every name the compiler provides carries, for the reason every one of them does.

**Turned down: a reference that carries the promise** -- `&shared u32`, whose
every read acquires and every write releases.  It is the tidier type story and
is what Java's `volatile` does, and it was turned down because the promise is
then on the place rather than on the access: driving a ring publishes an index
with a release and reads the same index back plainly a moment later, and a type
that said "always" would make that second read pay for the first.  Said at the
access, the ordinary read stays ordinary and the one that matters is visible
where it is written.

**Also turned down: keeping it out of the language** and emitting the ring code
per target, as the startup and the fault helper are emitted.  The user chose the
other half of the same question the other way: the ring is driven by `std.pl4g`
written in pl4g, one source for three architectures, which is also what puts the
language on real work.

**What they take.**  A reference (4580) to one value: a record, a tuple and a
result each travel as the several values they are made of, so ordering one would
be that many accesses and which of them the word belonged to would have no
answer (4582).  `⎕release` writes, so its reference is a `&mut` (4581) -- the
ordering says what others see and nothing about who may write.  Both make the
function impure: one notices what something else did, the other lets something
else notice.

Compare: **C11** and **Rust**, whose `memory_order` is a parameter of the
operation, which this is; **Java**, whose `volatile` is the answer not taken;
**Go**, which has none of it in the language; **Zig**, whose `@atomicLoad` takes
the ordering as an argument, which is this shape exactly; **Linux's own
`smp_load_acquire` and `smp_store_release`**, which are this pair by another
name and are what the ring code will read like.

## 2026-09-17T22:00+02:00 — language

**`⎕at` and `⎕span`: the one door to memory a program was handed**

Chosen by the user from three.  A ring shared with the kernel is three `mmap`s
and a set of offsets into them, so driving one in the language needs a way to
reach memory at an address the program worked out -- which nothing in the
language had.  `⎕at(ADDRESS, ⌜TYPE⌝)` answers a reference and
`⎕span(ADDRESS, COUNT, ⌜TYPE⌝)` an array whose length is not in its type.

**Two names and not one.**  One would have done -- several values are reached by
arithmetic, one `⎕at` at a time -- and the second was taken because an array whose
length is not in its type *is already* a place and a count, so `⎕span` represents
nothing new and gives the language back its own bounds checking over memory it
did not allocate.  A buffer handed to `write` is that shape and nothing else.

**Turned down: a mapping that answers bytes**, `⎕map(fd, offset, length)`, so that
no raw address is ever spelled.  It is the safer-looking answer and it does not
finish the job: the head of a ring is a `u32` inside those bytes, so reading one
still needs a typed view of a slice -- a second thing to design, and one that
would be `⎕at` with the address hidden.  Hiding the address would have bought
nothing that the program could not undo.

**The lifted type is the answer's** and not the pointee's, so `let head: &mut u32
= ⎕at(a, ⌜&mut u32⌝)` writes the type once in two places and a reader
comparing them sees the same words.  `⌜mut u32⌝` was what the question
proposed and does not parse: `mut` belongs to a definition and not to a type.

Neither makes a function impure.  Making a place is not reaching through it, and
what reaches through it is an ordinary read or write that says so already.

**The user also settled how much of the ring `std` exposes: none of it.**  A
program names descriptors and calls `read` and `write`; `Ring`, `Pending`, the
submission entry and the completion entry are `std`'s own and are not exported.

Compare: **C**, where a cast of an integer to a pointer says this and may be
written anywhere; **Rust**, whose `*mut T` from a `usize` is `unsafe`, the word
marking a region rather than the operation; **Zig**, whose `@ptrFromInt` is this
exactly -- one name for the one thing that cannot be checked; **Go**, whose
`unsafe.Pointer` is the same idea behind a package a program has to name;
**Ada**, whose `System.Address_To_Access_Conversions` is a generic a program
instantiates, which is the same decision with more ceremony.

## 2026-09-17T23:00+02:00 — language

**A field of a record may be assigned to**

Not a decision so much as a hole: records have been readable, referable and
copyable since 2026-09-17 and `p.x ← v` was refused by the *parser*, which knew
about a name, an element, an entry and a dereference and not about a field.  A
ring's submission entry is nothing but field assignments, which is where it
surfaced.

**Only the field is written**, which is what having an address per field is for:
the rest of the record is not read, not copied and not touched.  So the record
has to be somewhere -- a name given storage of its own, what a reference names,
or a field of one of those -- and a record the program worked out is a value in
registers with no place for a field to be at (4588).

**The mutability mark stays on the binding.**  A field is as mutable as the
record holding it and a record behind a reference as mutable as the reference,
so `p.x ← v` on a `p` without `mut` is refused (4004) and `r⌖.x ← v` through a
`&Point` is refused (4535).  ML puts the mark on the field instead, which would
let one record hold both kinds; that is a second axis for a reader to track and
was not taken.

**A field that is itself a record is written out where it stands**, by the same
path a definition's literal takes: there is no register a value of one could be
made in, and going through one would be the thing records-in-records already
does not do.

Left open at the time and done an hour later: a record at the *top level*.  A
record literal of constants is laid out into the bytes a global holds, field by
field where `offsets_of` puts each and zeroes between, and the global's address
stands where a placed local's does -- so reading a field, writing one and taking
a reference into one work there as they do for a local.  `RecordConst` is what
carries it, beside the array constant that was already there.

Compare: **C**, **Rust**, **Go** and **Zig**, where this is the same statement
written the same way; **Haskell**, which has no such statement and builds a new
record naming the fields that differ; **APL**, where the whole idea is an
indexed assignment into a nested array.

## 2026-09-18T00:00+02:00 — language

**`⎕widen(EXPR, ⌜TYPE⌝)`, which answers the value and not a result**

Chosen by the user from three.  The language had `⎕narrow` and nothing going the
other way, so the only route from a `u32` to a `u64` was
`⎕narrow(a, ⌜u64⌝) ?? 0u64` -- a check that cannot fail and a default that
cannot be reached.  A ring's offsets are `u32` and its addresses `u64`, which is
where it became unavoidable.

**It answers the value itself**, which it may do only because it cannot fail.
That is what makes it the opposite of `⎕narrow` rather than a second spelling of
it: narrowing can fail, so what it answers says whether it did; widening cannot,
so there is nothing to say.

**Turned down: widening implicitly** where the wider type is known and nothing
can be lost.  It is what C and Zig do and it would reverse a rule the
specification states in so many words -- nothing widens or narrows on its own --
which is the rule behind `1u8 & 2u16` not compiling.  Also turned down: one name
for both directions, answering a result or a value depending on the types, which
would make what a call comes to depend on a reader working out which case it is.

**The rule is "does every value of the one type fit the other"**, not "is it
wider", also at the user's direction.  An unsigned type goes into a signed one
where there is room to spare for the bit the sign takes; a signed type goes into
no unsigned one at all.  The alternative was reinterpreting the bits at one
width, which is fast and would let `⁻1i64` become a very large `u64` quietly --
exactly the surprising interpretation of a value this language is built to
avoid.

Compare: **C**, whose integer promotions are the reason this language has a name
for it; **Rust**, whose `as` is one word for both directions and says nothing
about which happened; **Zig**, whose widening is implicit and whose `@intCast`
checks; **Ada**, where a conversion is written out and checked, which is this
pair split the same way; **Haskell**, whose `fromIntegral` is one function for
every direction and wraps silently.

## 2026-09-18T01:00+02:00 — language

**The ring: driven in pl4g, and a way out where there is none**

`modules/std.pl4g` now does `io_uring_setup`, the three `mmap`s and the whole
submit-and-reap cycle, written in pl4g.  One source for three architectures,
which the user chose over per-target assembly beside the startup code, and it
put the language on real work: nothing had to be added for the ring alone, every
piece it wanted having been asked for and decided on its own terms first.

**qemu-user answers `io_uring_setup` with `ENOSYS`.**  It cannot do otherwise --
the rings are memory shared with the kernel and an emulator would have to
translate every address in them -- so a ring can never run under the emulators
two of the three targets are tested with.  That collides with the instruction
that all I/O go through `io_uring`, and the user chose the way out: `std` tries
once and falls back to the plain `read` and `write` calls where there is no ring.
Every language test then runs on all three targets, the native run exercising the
ring and the emulated ones the fallback.  Turned down: running the I/O tests
natively only, which would leave the ring code on two architectures compiled and
never executed; and aborting where no ring can be made, which would make every
emulated run of any program that does I/O fail.

**Tried once and not again.**  A system without `io_uring` will not grow one, and
asking a second time would cost a request to the kernel for every read and write
a program ever does.

**A request carries a place in a table and not an address.**  `user_data` is the
index of the `Pending` that says what is known about the request, chosen by the
user over the address of one -- which is what the kernel's own users put there
and would have wanted `⎕address`, the inverse of `⎕at`.  Nothing the kernel
reads back is now an address of anything the program holds.

**None of it is exported**, also the user's choice: a program names descriptors
and calls `read` and `write`, and that there is a ring underneath, or is not, is
not something it can see.

Left open: the submission entry holds `addr : &u8`, a reference into the caller's
buffer, and nothing checks that the buffer outlives the request.  It does here --
the request is complete before the call returns -- but that is a property of this
design and not of the type, and a ring that answered later would need the
language to be able to say it.

Compare: **liburing**, whose `io_uring_prep_write` and `io_uring_submit` this is
by hand and whose memory ordering is the same pair of barriers; **Rust**'s
`io-uring` crate and **Zig**'s `std.os.linux.IoUring`, which are the same shape
in a library; **Go**, whose runtime hides the whole question behind a scheduler
and a thread pool, which is the answer this language has no threads for yet.

## 2026-09-18T02:00+02:00 — language

**`⎕address(REF)`, and the hole a field assignment had left open**

Two halves of one thing.  Writing a field did not ask how long what goes into it
lasts, so `h⌖.at ← &gone` put a reference to a local into a place the caller
owns -- while `h⌖ ← Holder(.at ← &gone, …)`, the same write one level up, was
already refused (4571).  A field was the way round the rule, which is a dangling
reference and not a decision.

Closing it refused what the ring does: its submission entry held `addr : &u8`,
a reference into the caller's buffer, and a parameter does not last long enough
for a place from outside.  So the user chose the other name: `⎕address(REF)`,
the inverse of `⎕at`, and the entry holds the `u64` the kernel's field actually
is.  The rule then applies everywhere with nothing excused from it.

**Turned down: excusing a place that came from `⎕at`** on the grounds that the
compiler was never told how long it lasts.  It is arguable and it would need the
compiler to track where a place came from, which nothing else asks it to do.
Also turned down: leaving the hole and writing it down, which is what the
alternative to a decision looked like here.

**Making the number is safe; using it is not**, which is where the line is drawn
and is where Rust draws it too -- `as usize` on a raw pointer is safe and
dereferencing one is `unsafe`.  So `⎕address` makes no function impure: asking
where something is changes nothing, and what is done through the address says so
itself.  What has an address is a place, so a value the program worked out has
none (4592).

Compare: **Zig**, whose `@intFromPtr` and `@ptrFromInt` are this pair exactly;
**C**, where `&x` and a cast to an integer are the same split and neither is
marked; **Go**, whose `uintptr` is the type and whose garbage collector makes the
question harder than it is here; **Ada**, whose `System.Address` and
`Address_To_Access_Conversions` are the same two directions with more ceremony.

## 2026-09-18T03:00+02:00 — compiler

**Every test is run, and every failure named**

A failing test left through the helper a fault leaves through, which writes a
message and exits, so a run ended at the first one.  That makes a reader fix one
thing and run again to be told the next, which is the opposite of what a test
binary is for.

`__pl4g_report` is `__pl4g_abort`'s write and then a *return*, emitted only where
a test runs -- a write that comes back is of no use to anything else.  The count
is kept in a register a call leaves alone (`ebx`, `x20`, `s2`, all callee-saved
in the conventions this compiler generates), so nothing has to be saved around a
call and no storage has to be found for a number that lives for a few
instructions.  A count that is not nought exits with **66**, a reserved status of
its own: a program that fails a test never started, so nothing the startup
function would have answered means anything.

The status says that something was wrong and the messages say what, which is the
division every test runner makes.  Comparisons: **Rust**'s harness and **Go**'s
both run every test, report each, and exit with one number; **C** and **C++**
with an `assert` stop at the first, which is the behaviour this replaces; **D**'s
`unittest` blocks, which `always` tests are modelled on, stop at the first as
well.

## 2026-09-18T04:00+02:00 — implementation

**The I/O runtime is C, compiled ahead of time and packaged with the compiler**

At the user's direction, and a change of where the runtime is written rather
than of what it does.  It had been pl4g, in `modules/std.pl4g`, which is what
put the language on real work and is where it learned what driving a ring needs;
every piece it asked for was decided on its own terms and every one stayed.

**What is packaged is the code and its relocations.**  Chosen by the user from
three: the extracted data rather than the object files, and rather than
compiling when the compiler is built.  So building a pl4g program needs no C
compiler and the compiler needs no reader for relocatable objects -- only
changing the runtime needs either.  The cost is that what is committed can fall
behind the C, and a test that recompiles and compares is what stops it.

**`@[external("SYMBOL")]`** says the body lives elsewhere, under a symbol of its
own, and that the call follows the system's convention.  The user chose the
symbol being named over the function's own name being it: what a program calls
something and what the thing it calls is called need not agree, one being a name
in a language and the other a name in an image.  Turned down as well: naming the
convention too, which nothing needs while the only somewhere else is the
runtime the compiler carries.

**`@[abi]` on a record** says it is laid out the way one compiled by something
else is, and that **only a reference to one crosses a call**.  The user chose
that over implementing the three system ABIs' aggregate classification, which is
what passing one by value would have needed; a reference is passed the same way
by every convention there is, so the rule costs nothing and asks for nothing.
Turned down: a layout-only meaning, which would leave a by-value `@[abi]` record
wrong at the boundary with nothing saying so.

**A function defined somewhere else is impure by being external.**  Nothing here
can see what it does, so it is taken to do everything, which is the rule a call
through a value already follows.  Found by testing rather than by reasoning: a
call to one the program had not marked `@[impure]` was silently dropped at every
optimisation level, the write removed and nothing printed, with no diagnostic
anywhere.  A program that meant to do I/O and quietly did not is the worst kind
of wrong, and a promise the compiler cannot check is not worth offering -- so
there is no way to declare an external function pure.

**A function with no body is a declaration**, and what says there is none is
that the line ends after the header.  A body begins with `:` or `{`, so the two
readings never both hold, and anything else after a header is the error it
already was.

Compare: **C**, whose `extern` and separate compilation this is, with a linker
where this has a compiler that carries the code; **Rust**, whose `extern "C"`
and `#[repr(C)]` are this pair almost exactly and whose `build.rs` compiles the
C at build time; **Zig**, which compiles C itself and needs neither; **Go**,
whose cgo compiles the C as part of the build and pays a calling-convention
crossing for it; **Java**, whose JNI is the same two halves with a much wider
boundary.

## 2026-09-18T05:00+02:00 — language

**`std.read` and `std.write` answer a result, and the error is a named thing**

**Decided here rather than asked about.**  The user was offered the question
three times and said "continue" each time, which is direction to get on with it;
so this is mine, it is written down with what was turned down, and it is cheap to
overrule -- the whole of it is one enumeration and one function in
`modules/std.pl4g`.

The design of 2026-09-17 wrote the signature as `→ u64 ¤size?`, a result with no
error value at all.  That is not what landed: losing *why* a write failed is the
thing a caller most wants and the thing hardest to get back.  What landed is
`→ u64 ¤size ? Error`.

**`Error` is an enumeration whose numbers are the kernel's own.**  A reader who
knows what `EAGAIN` means knows what `would_block` is, and a reader who does not
need not learn the numbers.  Eighteen of them are named -- the ones a read, a
write or an open can give -- and `other` is what anything else comes to.

Turned down, and why:

- **The kernel's number, as an `i32`.**  Loses nothing and is what the kernel's
  own interface is, but it is a number where this language has spent the whole
  I/O design making things types: a descriptor is a type and not a number, and an
  error should not be the exception.
- **An enumeration the *compiler* provides**, as `⎕narrowing` is.  The errno
  table is the system's and changes with it, which makes it the library's
  business and not the language's.
- **A record of a named kind and the raw number**, which loses nothing at all.
  A record as a result's error is a part that is itself several values, which is
  the one shape `parts_of` and `through_storage` would both have to be taught;
  it is the right answer eventually and is in the to-do list.

**What `other` costs** is the number, for an errno the list does not name.  That
is the known hole, and naming the whole table is what closes it.

Also here: **a value of an enumeration another module exports may be named**,
`std.Error.would_block` being the two readings of the mark one after the other
rather than a third one.  It was simply missing.

Compare: **Rust**, whose `io::Error` carries the raw number and a kind, which is
the record turned down above; **Go**, whose `error` is an interface and whose
`syscall.Errno` is the number with names beside it; **Zig**, whose error sets are
exactly this enumeration and which has no `other` because the set is closed by
the compiler; **Haskell**, whose `IOException` carries a kind and the text;
**C**, where it is `errno` and a global.

## 2026-09-18T06:00+02:00 — language

**`⎕narrow` reads a number as a value of an enumeration**

Found by cost.  Answering an I/O result named rather than numbered doubled what a
program that does I/O takes to compile -- `std-init` 11.0 ms to 22.1 -- and about
half of that was eighteen `elif` arms in `modules/std.pl4g` turning an errno into
a name.  Behind the cost was the real thing: **a program could not make a value
of an enumeration from a number at all**, so every program that reads one off a
device, a protocol or a kernel would have written that chain out.

An enumeration is a narrower type than the one it is held as, so this is a
narrowing and fails the way one does.  What differs is that its values are not a
range -- the number has to *be* one of them -- so `⎕narrowing` grows a fourth
value, `absent`.  One name for one question: does this number fit this type.

**No branch.**  An enumeration is held as its number, so where the number is one
of them the answer is the same bits; what has to be worked out is only whether it
is one, which is one comparison per value folded together with "or".  A chain of
arms would be the same comparisons with a jump between each.

What it bought: the library's eighteen arms became one line, and the compile time
came back from 22.1 ms to 20.3 -- less than the whole difference, because the
comparisons are still eighteen and the compiler now writes them.  Doing better
wants the values sorted into ranges, which is the compiler's to improve without
any program changing, and is in the to-do list.

Turned down: a cast that does not check, which is `@enumFromInt` in a fast Zig
build and is the thing this language does not do; and leaving it out, which
leaves the chain in every program that needs it.

Compare: **Rust**, whose `TryFrom<u8>` a derive writes out and whose error hands
the number back; **Zig**, whose `@enumFromInt` checks in a safe build and not in
a fast one; **C#**, whose `Enum.IsDefined` is a library call over reflection;
**C**, where an enumeration holds any number of its underlying type and the
question cannot be asked.

## 2026-09-18T07:00+02:00 — language

**Three descriptors and no way to open anything, and `⎕bytes` for writing text**

The user settled the scope: there is no `open` call and no representation for a
file name, and what a program has is standard input, output and error on the
descriptors every system gives them.  `std.write` is the free function that
writes, taking the writer first and writing unbuffered.

All of that was already built but for one thing: **a program could not write
text.**  `write` takes bytes, which is what a device takes, and nothing reached
inside a string -- there is no index, the *n*-th byte of UTF-8 not being the
*n*-th character, and `#` answers characters.

`⎕bytes(TEXT)` answers them.  It costs nothing: a string and an array of bytes
whose length is not in its type are the same two words, so it says which of the
two is meant and emits no instruction.

**Saying it is the point.**  Turned down: letting a string stand where bytes are
wanted, which every other conversion in this language refuses to do on its own,
and which would make a walk over characters and a walk over bytes read alike.
Also turned down: `write` taking a `str`, which would read better at the one call
site and leave a program with a buffer of bytes -- what `read` fills -- unable to
write it back.

**The error list is what a read or a write can give.**  With no way to open
anything, the refusals that belong to opening were names nothing could reach, and
each was a comparison every program paid for: four went, two that reading and
writing really do give came in.

Compare: **Rust**'s `as_bytes`, this exactly and free for the same reason;
**Go**'s `[]byte(s)`, which copies; **Java**'s `getBytes`, which takes a charset
because its strings are not UTF-8; **C**, where a string *is* its bytes and the
question cannot be asked.

## 2026-09-18T08:00+02:00 — language

**What a program is started with is an object it holds, and what it is handed is
where that object is**

At the user's direction, and a change from what landed a day earlier: the
startup function took `std.Init` by value, arriving as its three leaves in three
registers.  It now takes `&mut std.Init`, and the record is an object of the
writable data that the image carries, filled in before anything runs.

**Two things follow that could not before.**  The record may *grow* -- the
arguments, the environment, whatever else arrives with a program -- without the
one signature every program writes changing, which is the reason `Init` was a
record rather than three descriptors in the first place and which passing it by
value quietly gave up: three leaves in three registers is a convention that
breaks the moment there are four.  And a program may **change what it was
started with**: `init⌖.io.output ← std.Writer(.fd ← 2i32)` makes what it writes
go where it reports, which a value handed over in registers could not be made to
do.

**Laid out by the program's own declaration.**  `part_offsets_of` says where each
descriptor goes, which is the same place a field read anywhere else comes from,
so a field added to `Io` is one the entry point fills in without being told.  It
asserts there are as many leaves as descriptors rather than filling in part way.

A program that takes no parameter carries no such object and no writable data at
all, which is what it did before and what most programs will do.

Compare: **C**, whose `main` is handed `argc` and `argv` by value and whose
environment is a global; **Rust**, where `std::env::args` is a call into the
runtime and nothing is handed to `main` at all; **Go**, the same; **Zig**, whose
`std.process.args` is likewise a call.  This hands over one address and lets the
program see everything through it, which is the shape that grows without
anything being rewritten.

## 2026-09-18T09:00+02:00 — language

**A write is started and waited for separately, and nothing outstanding
survives the program**

At the user's direction.  `std.write` submits a request and answers a `Pending`;
`std.flush` waits for one and says how it went; `std.write_sync` is the two
together; `std.drain` waits for everything.  Every request still outstanding
when the startup function returns is waited for before the process ends, which
a destructor in `std` arranges.

**The handle holds a slot and nothing else.**  What the kernel said goes in the
ring's own table, so a request the ring is still working on and one that was
done on the spot are read the same way.  That is what makes emulating a ring
right: where there is none the work is done as it is asked for and the answer is
put where an answer goes, and `flush` reads one number out of one place either
way.  It also keeps the handle *one value*, which a result's answer has to be --
a record of two words there would be a part that is itself several values, which
is the one shape nothing below the checker can place.

**The error is the number the kernel said**, which cannot be wrong.  That
replaces carrying the enumeration, which lost the number for anything it did not
name; `std.as_error` names one for a program that would rather compare a name,
and the enumeration is still there for it.

**A record of one value lives in one register.**  `Pending` is a record of one
field, and a result whose answer is one asked for a register of the record's own
type -- which the three instruction selectors refused outright, a record having
been a thing only memory held.  A record is what it is made of, and one made of
a single value is that value's register.

Compare: **liburing**, whose `io_uring_submit` and `io_uring_wait_cqe` this pair
is and whose users must drain before exit themselves; **POSIX AIO**, whose
`aio_write` and `aio_return` are the same shape with a control block for a
handle; **C**'s `fwrite` and `fflush`, which is this with a buffer between the
program and the device rather than a request; **Go** and **Rust**, whose writes
are synchronous and whose asynchrony is somewhere else entirely.

## 2026-09-18T10:00+02:00 — language

**The words a program was named with, read by the entry point**

At the user's direction.  `std.Init` gains `args : str⟦⟧`; the entry point reads
what the kernel left at the stack pointer and the runtime counts each one.

**Counted and not nul-terminated.**  The kernel's shape is a pointer to bytes
ending in a nul, which nothing in this language has a use for: a `str` is where
the bytes are and how many there are.  So each is counted once, before the
program starts, rather than by everything that ever reads one.  The run of them
is asked of the system, since how many there are is not known until the program
runs, and is never given back -- it lasts as long as the program does, which is
as long as what a program was named with is worth having.

**A record may hold a string**, which it could not before this and which is what
`args` needed.  A value of several parts is several values one after another in
memory, so a load of one is one load per part and a store one store per part;
`part_offsets_of` now answers for every shape and not only for a record.  The
three instruction selectors had an arm for a *result* and nothing else, so a
record holding a string was refused (8501) -- which is a gap `args` surfaced
rather than one it made.

Three things that bit, all worth writing down because none of them is obvious:

- **The call follows the system's convention**, and on x86-64 the language's own
  and the system's put their first arguments in different registers.  The callee
  decides, and the entry point was using the caller's.
- **On AArch64 the stack pointer and the zero register share an encoding.**  A
  move between registers reads it as the zero; an addition of nothing is the
  form that reads it as the stack pointer.  The first version silently read
  nought and answered no arguments.
- **An mmap that succeeds answers an address, not a small number.**  The check
  for a refusal was written as a range test that every successful address also
  passes, so the arguments were read, put somewhere, and thrown away.

Compare: **C**, whose `argc` and `argv` are handed to `main` and whose strings
are the kernel's own, nul and all -- so every length is walked for again at each
use; **Rust** and **Go**, where they are a call into the runtime and are copied
into the language's own strings; **Zig**, whose `std.process.args` is an iterator
over the same memory and whose lengths are found the same way this does.

## 2026-09-18T11:00+02:00 — runtime

**A stack of the program's own, with a guard below it**

At the user's direction.  `--stack-size=SIZE` (1 MiB by default) and
`--guard-size=SIZE` (64 KiB), a stack mapped by the program before anything of
it runs, and a handler that turns running off the bottom of it into an exit
status of its own (67).

**Why not the stack the kernel supplied.**  Its size belongs to whoever started
the program and is a `ulimit` away from being something else; its guard is one
page, which a single large frame steps over; and exhausting it is a SIGSEGV
indistinguishable from following a bad address.  A generator emitting a program
knows how deep that program goes, and the three things it wants -- to say how
much, to be sure the bottom is caught, and to be told apart when it is -- are
none of them things the environment can be asked for.

**One mapping, then part of it opened.**  `mmap` of `guard + size` with
`PROT_NONE`, then `mprotect` of the upper `size` to read and write.  The guard is
the remainder, so it is unreachable because nothing ever made it otherwise:
there is no second mapping that could land elsewhere and no window in which the
guard is ordinary memory.  The alternative -- map the stack, then map a guard
below it -- was rejected for both of those.

**The handler needs a stack of its own**, which is the whole difficulty of
catching this: there is no room on the stack that just ran out.  `SA_ONSTACK`
with an alternate stack, and `SA_SIGINFO` so that the address that faulted is
known and can be compared against the guard.  A fault anywhere else is not the
runtime's business: the handler takes itself off and returns, the instruction
runs again, and the program dies of the signal it really got.

**A status and not a signal**, which is the rule the reserved range exists for.
67 says the stack ran out, and what to do about it -- build with more, or find
the recursion that does not end -- is a different thing to do than what any
other stop asks for, which is why it is not the general number.

**Nothing of it is required to succeed.**  Where the mapping is refused the
program carries on with the stack it already had.  `--stack-size=0` asks for
that deliberately and is the only way to build an image carrying none of the
packaged runtime, since the call is at the entry point and every program makes
it otherwise.

**`PT_GNU_STACK` carries the size** although nothing loads a stack from it here.
It is where the format keeps that number, so a reader of the image finds it in
the usual place, and it is what the program falls back to.

Compare: **Rust**, which is this exactly -- a guard page, `sigaltstack`, a
handler that recognizes the address and prints "has overflowed its stack" -- and
then aborts, so the status is a signal after all; **Go**, which grows its stacks
instead and only reports "stack overflow" when it will not grow further, at the
cost of a check in every prologue; **C** on Linux, where the guard is the
kernel's, the size is the loader's and the program dies of SIGSEGV with nothing
said; **Java**, which raises `StackOverflowError` and can do so because every
call already goes through a machine that counts; **Zig**, which has
`--stack-size` for the same reason this does and leaves catching it to the
operating system.  What none of them has is the size decided when the program is
built *and* the exhaustion reported as a status a caller can tell from the
program's own.

## 2026-09-18T12:00+02:00 — runtime

**The stack is made by the entry point, not by anything the image carries**

At the user's direction, and a change to the entry above rather than a new
feature: the same stack, the same guard, the same status, made by instructions
the compiler selects instead of by a function compiled from C and packaged with
it.

**What was wrong with carrying it.**  The call was at the entry point, so every
program made it, so every program pulled in the packaged runtime: two and a half
kilobytes of object code, four sections, and a fixed 0.2 ms of placing it, in
programs that wanted nothing else from it.  The smallest image the compiler
could produce went from 1424 bytes to 5072.  Emitting the code instead costs
about nine hundred bytes and leaves the packaged runtime to the programs that
reach it -- which was the rule everywhere else and had one exception.

**It is written once and emitted for all three**, which is what
`target/allocator.py` already did and what made this cheap: the architecture
fills in a record saying which numbers its system calls have, which registers
they take, and how the stack pointer is read and written, and everything else is
shared.  Writing the entry point's assembly three times was the alternative and
was rejected for the reason that record exists.

**Readable and writable first, then the guard taken away.**  The mapping is
asked for whole and not populated and the guard is turned to no access with
`mprotect`, which is the order the user asked for; the guard is then what is
left of the mapping rather than a thing placed beside it.  The order this
compiler had before -- the whole of it unreachable, then the stack part opened
-- reaches the same state and was changed because the user said which way round
it should be.  Neither has a window in which the guard is ordinary memory that
anything could reach: nothing runs between the two calls.

**Below the stack the kernel made**, at a hint worked out from the stack pointer
as the entry point found it.  A hint and not a demand, so nothing depends on it;
what it is worth is that the program's stack stays where a stack lives in the
address space rather than in the middle of where mappings are handed out.

**The handler's stack is the top of the same mapping.**  One call rather than
two, and it puts the three regions -- guard, stack, the handler's little stack
-- in a known order.  The separate mapping the C version made was a second call
for a second thing of the same kind.

**The guard is rounded up to the largest page the architecture may use** where
the program is built, because what `mprotect` takes away is a whole number of
pages of whatever the running kernel chose and the program is built once for all
of them.  4 KiB on x86-64 and RISC-V, 64 KiB on AArch64.

Compare: **Go**'s runtime, which is linked in and is the thing that makes every
stack; **Rust**, whose guard page and handler are `std`'s and are therefore in
every binary that uses `std`; **Zig**, which emits its start code rather than
linking it, as this now does; **C** on Linux, where none of it exists because
the kernel's stack is the program's stack.  What this has that none of them has
is the choice being the compiler's: the stack is made by code the compiler wrote
for this program, so a program that wants none of it carries none of it.

## 2026-09-26T22:00+02:00 — tooling

**An editor reads the grammar the compiler reads**

At the user's direction: a configuration for Neovim, so that a `.pl4g` file is
coloured by the tree-sitter grammar this project keeps.  `editors/nvim` is a
Neovim package of four files and three links.

**It holds no copy of anything.**  `parser/pl4g.so` links to what
`bin/pl4g-grammar` builds and `queries/pl4g/*.scm` link to the grammar's own
queries under the names the editor looks them up by.  The alternative -- copy the
queries into the shape an editor wants -- is two statements of one thing, and the
project has one of those already (the grammar and the compiler) with a test to
keep them together.  A link needs no test beyond one that it still points
somewhere, which is what `tests/compiler/test_editors.py` begins with.

**Nothing computes a path.**  What finds the parser and the queries is the
runtime path, which is the mechanism the editor already has; a package that
worked out where it was installed and registered the grammar by hand would work
too and would be a second mechanism.  So installing is a link into
`pack/*/start/`, and pulling the project brings the grammar with it.

**The queries are written coarse first, because the later pattern wins.**  This
was a bug found by writing the configuration: tree-sitter's own highlighter and
Neovim both take the *last* pattern that matches, and this project's queries were
written for the opposite rule -- so the compiler coloured `u64` as a type while
every editor and `tree-sitter highlight` coloured it as a variable.  The queries
are now ordered the way both of those read them, and the compiler's colouriser
breaks a tie the same way.  One rule, written down in the query file, in
`spec/details.md` and in the grammar's README.

**The configuration says what the language settles and no more.**  Four spaces
and no tab, because a tab in indentation is an error (2103); the two comment
markers, because the grammar has two; a name holding an apostrophe or an `@` as
one word, because the lexer says so.  It does not turn folding on although the
fold query is there, and it does not set a colour scheme: both are the reader's,
and a package that decided would be deciding for every file.

Compare: **Rust**, whose editor support is `rust-analyzer` and whose tree-sitter
grammar lives in another repository entirely, so its queries are somebody else's
to keep current; **Go**, the same with `gopls` and `vim-go`; **Zig**, which ships
`zig.vim` beside the compiler and leaves the tree-sitter grammar out of the tree;
**nvim-treesitter**, where the queries for two hundred languages live in one
repository apart from every one of those languages, which is what makes a query
drift from the grammar it is about.  What this does instead is keep the grammar,
the queries, the compiler that reads them and the editor configuration that reads
them in one tree, with a test that opens the editor.

## 2026-09-26T23:30+02:00 — tooling

**The compiler is the language server**

At the user's direction: a server an editor can use, reusing the compiler, asked
for by a word on its command line.  `pypl4g lsp`, and what answers every question
is the compiler -- the same lexer, parser, checker and code generation, over the
text the editor holds.

**Confirmed with the user before it was written**, which is what this file is for:
what the first server can do (diagnostics, an outline, and -- next -- hover and
where a name is defined), how it is asked for (a command word beside `build` and
`test`, rather than the option that was proposed), and what makes it recompile
(the front end while typing, the whole compiler on save).

**Why not a server beside the compiler.**  Every other answer in this space is a
reimplementation of the front end: **rust-analyzer** and **gopls** are that, and
have to be, their languages' batch compilers being far too slow to run on a
keystroke; **zls** parses Zig itself; **pylsp** wraps a pile of tools that each
read the file again.  **clangd** is the other shape -- the compiler's own front end
in a server -- and it is the one to copy here, because this compiler already runs
in a few milliseconds and an incremental reimplementation would buy nothing and
cost a second statement of what the language is.  The project has one second
statement already, the tree-sitter grammar, and it is kept honest by a test; a
third would drift, and the one a reader would notice drifting is the one in the
editor.

**A command word rather than an option.**  The user proposed `--lsp` and chose the
word: what the compiler is being asked to *do* is what a command word says, and
every option is about how it does it.  It takes no source file, and one named
beside it is refused (1017) rather than ignored.

**Front end while typing, everything on save.**  Nearly every diagnostic is in the
front end, which is the fast part; the back end has a handful that nothing else
can find (8501, 9901), and the moment they matter is the moment there is a file.
The alternative -- everything on every keystroke -- was rejected on the largest
sample in the suite, which is 115 ms of work and would be felt.

**Three ways of counting, and the editor picks.**  The protocol counts along a
line in sixteen-bit units unless both ends agree otherwise, and this language is
written in glyphs of three bytes with characters outside the basic plane in the
test suite.  So the server offers `utf-32`, `utf-8` and `utf-16` in that order --
the first being exactly the characters the compiler counts -- and converts through
the line's own text for the other two.  Most servers implement `utf-16` alone and
are quietly wrong on a line like this one.

**What a name is comes from a side table, not from a second walk.**  The checker
resolves every name already and forgets what it found; a server needs exactly
that, so the checker writes it down when it is handed somewhere to write it --
four call sites, one test against nothing per name, and nothing recorded for a
build.  The alternative was for the server to resolve names itself over the syntax
tree, which is the reimplementation this whole design exists to avoid: it would be
right about the easy cases and wrong about the ones a reader asks about.

**Uses come from the checker, definitions from the tree.**  Where a definition is
and what its documentation says is in the syntax tree already, so nothing is
recorded for one; standing on a definition is answered from the tree and standing
on a use from the table.  Hover therefore shows a record's own documentation
comment without the checker having carried it anywhere.

**The editor's text and not the file's**, through a source manager that hands out
what the editor holds and reads the disk for everything else.  So an import from a
buffer with unsaved changes is read as the buffer has it, which is eleven lines
and no change to the compiler.

## 2026-09-28T12:00+02:00 — tooling

**Zed reads the same grammar and runs the same server**

At the user's direction, and beside the Neovim package rather than instead of it:
`editors/zed` is a Zed extension holding the two halves an editor needs, and
neither of them a copy.  The colouring query is a link to
`tree-sitter-pl4g/queries/highlights.scm`, which is the file Neovim reads and the
file a diagnostic is coloured with; the language server is `bin/pypl4g lsp`, which
is the compiler.

**One highlight query for both editors, because both read it the same way.**  Zed
resolves a capture name against the theme by dropping the last part until
something matches, so `@keyword.conditional` is coloured as a keyword and
`@variable.parameter` as a variable; and Zed, like Neovim and like tree-sitter's
own highlighter, lets the pattern written later win.  So the coarse-first order
the queries already have is right for it, and a second copy would have been a
second thing to keep current.  What Zed wants that Neovim does not is asked for in
Zed's own captures -- an outline written with `@item`, `@name` and `@context` --
and those files are Zed's alone because nothing else reads them.

**The grammar comes from a commit, which is the one thing that could drift.**
Zed builds a grammar by cloning a repository at a revision; it will not read a
working tree, and there is no option that makes it.  So `extension.toml` names a
revision, `bin/pl4g-zed-rev` writes it, and a test refuses a revision whose
grammar directory is not the one in the tree -- the same shape as the test that
keeps the grammar and the compiler agreeing, for the same reason.  The
alternatives were to point the manifest at a local path, which works on one
machine and is not shareable, or to let it drift silently, which is what this
project has a test for.

**The language server needs twelve lines of Rust**, compiled to WebAssembly,
because a command is the one thing Zed will not read from a file.  What those
lines do is choose between the compiler the settings name, `bin/pypl4g` of the
project the file is in, and `pypl4g` on the path; the middle one is the answer
that matters, since a program in a checkout of the language should be checked by
the compiler it is written beside.

**Nothing here runs Zed**, which is the difference between this and the Neovim
package: that one has a test that opens the editor and reads what came out.  What
a test can check of an extension is that it says what it means to say -- the
manifest and the language configuration agree with each other and with the
compiler's own option table, every query compiles against the grammar, the link
resolves, the revision is the right one -- and the extension was built to
WebAssembly by hand once to show that the code is the API's.

Compare: **Rust**, **Go** and **Python**, whose Zed support is an extension in a
repository of its own, written by somebody who is not the compiler's author and
pinned to a grammar in a third repository -- three places for one language;
**Zig**, whose editor support is `zls`, a separate program; **Gleam**, whose
extension is the shape this one copies, in the language's own tree.  What this has
that none of them has is the queries being the compiler's own file rather than a
copy of it.

Open questions
--------------

Questions that are open, and tasks not yet done, are kept in the to-do lists rather than here:
[TODO-language.md](../TODO-language.md) for the language, [TODO-pypl4g.md](../TODO-pypl4g.md) for the compiler and
[TODO-editors.md](../TODO-editors.md) for what an editor reads, with a list of its own for each further tool as one is needed.

This file records decisions that were made.  A decision that leaves something open says so in its own entry, and the thing left
open belongs in a list.
