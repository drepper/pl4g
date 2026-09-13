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

---

Open questions
--------------

Questions that are open, and tasks not yet done, are kept in the to-do lists rather than here:
[TODO-language.md](../TODO-language.md) for the language and [TODO-pypl4g.md](../TODO-pypl4g.md) for the compiler, with a list of
its own for each further tool as one is needed.

This file records decisions that were made.  A decision that leaves something open says so in its own entry, and the thing left
open belongs in a list.
