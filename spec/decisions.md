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

---

Open questions
--------------

Questions that are open, and tasks not yet done, are kept in the to-do lists rather than here:
[TODO-language.md](../TODO-language.md) for the language and [TODO-pypl4g.md](../TODO-pypl4g.md) for the compiler, with a list of
its own for each further tool as one is needed.

This file records decisions that were made.  A decision that leaves something open says so in its own entry, and the thing left
open belongs in a list.
