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

A module is a source file, brought in by `let name := import("somename")` and named through afterwards as `name.thing`.  Several
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

---

---

Open questions
--------------

Questions that are open, and tasks not yet done, are kept in the to-do lists rather than here:
[TODO-language.md](../TODO-language.md) for the language and [TODO-pypl4g.md](../TODO-pypl4g.md) for the compiler, with a list of
its own for each further tool as one is needed.

This file records decisions that were made.  A decision that leaves something open says so in its own entry, and the thing left
open belongs in a list.
