Programming Language for Generators
===================================

This is a programming language which is meant to be generated, not written by humans. It is not important that keyboard support
for entering the language exists which opens the door to using Unicode glyphs for operators and functions. Precedence of names
used in other languages is less important than concise and unambiguous representation of the meaning. But the language has to be
understood by humans and therefore the requirement is that the code behaves, when executed, exactly as the code describes it. No
undefined behavior, no surprising interpretation of inputs. If a program depends on CPU and/or OS features a test for the
availability of the features has to be performed at startup and the program terminated with an appropriate message in case the
requirements are not met.

To achieve this all operations that can fail are represented through a sum type.  The basis
is a sum type of two types, the first being the result type of the operation and the second
being the error value.  If there if no specific error value, the error value type can be
void or nil or whatever the representation of the unusable type is.  If the result of the
operator is also not specific, the result type can also be void and the actual representation
of the type can be a simple boolean.

Operations of the compiler must be parallelizable and to ensure this the grammar has to be context-free, there is no process
definitions in order. The predominant style of writing code must be functional with pure functions, curryed functions, and
combinators for functions as it is possible with array languages.

The calling conventions /explicitly/ are not required to match the Linux ABI. Only functions that are explicitly defined to follow
a specific ABI must be called with the rules of that ABI. Otherwise, the calling conventions (argument parsing, register
preservation, etc) can vary even among the functions of a single compilation process. Data layout is up to compiler and it can
assume that all code which ends in the same binary is compiled with a compatible compiler. This also means that data layout (for
product types etc, alignment) does not have to be fixed, product types can be reordered for efficiency (packing, common concurrent
access, etc) as long as the result is consistent when using compatible compilers and when there are ways to request compatible
code/data generation for interoperability with existing code.

In incremental mode, the compiler does not terminate after generating the binary but instead
monitors the input files for changes and performs a new compilation, modifying the previously
generated binary.

The language must allow for easy parallelization and vectorization.  There must be no implicit
dependencies like memory alising forcing operations in a certain order.  The tools and/or
runtime must catch this.  The language must provide operators/primitives to write code while
avoiding explicit control flow as much as possible.

The compiler must be able to generate a log of all decisions it made and write them out into
a file in JSON format.  A schema for the file format has to be generated.  Create a separate
Python program which takes a log file and generates a report with the respective source code
sequences surrounded by the extended explanation of the logged decision.

The language itself also provides support for testing and building. The build process is controlled through a function that is
written exclusively using compile time-constant statements and expressions. Tests come in three flavors:
- tests that always run after building and when the program is first started
- tests which run when a build finished
- tests which run when a testsuite run is requested

The language allows writing code with little concern of the actual way the code is compiled, similar to scripting languages. But
for all decisions made it must be possible to guide the compiler to create efficient code. The log file must aid the user in
determining where explicit instructions to the compiler can change the decisions.

The language must allow compile-time reflection. It must be possible to inspect input code and modify or generate new code from it
which then gets added to the compiled code. Access operators for the internal representation of the code is provided, compile-time
operators transform the code and the resulting new structure(s) is/are then passed to the code generation part of the compiler.

The extended use of compile time code requires that higher-level data types are built into the library and are not exclusively
provided in the library. Aside of containers with various access keys, values, and performance at least strings are supported. The
compiler and well as any generated program requires that the encoding of text uses UTF-8 encoding. This simplifies some code but
also requires the implementation of the difference of the length and the size of a string.


Implementation
--------------

Certain aspects of the compiler have to be visible to the code.

Errors and warnings the compiler emits can be handled in the code.  To enable this, errors and warnings must be unique, constant,
and consistent numbers.  These numbers and the associated errors/warnings must be consistent between implementations and therefore
a formal, definitive collection of the errors must be maintained.


The Language
------------

In this section the language is described.  As it is developed new text is added to this section.

### Syntax

The language has both an layout-based syntax and a explicitly specified syntax.  Layout-based syntax uses rules and syntax
similar to Python.  Explicit syntax uses `{` and `}` enclosed blocks of statements which are individually separated by semicolons.
Indentation and explicit syntax cannot be mixed for individual blocks but properly nested blocks can have different syntaxes.

**A semicolon separates two statements in either notation.**  The layout notation does not need one -- the end of a line separates
too -- but it takes one, so that two short statements may stand on one line and so that what a statement is does not depend on
which notation it is written in.  Inside braces there are no ends of lines to separate with, so the semicolon is the only
separator there.

**An end of line inside brackets does not end a statement.**  The lexer counts the brackets that are open -- the parentheses of a
call, a parameter list or a grouping, the brackets an array or a list is written and looked in with, the marks a tuple, a set and
an attribute list are written with, and the braces of an explicit block -- and gives out no end of line while any of them is.  So
a call with more arguments than fit across a line, or a function with more parameters, is written down the page:

```
fn four(first: u8,
        second: u8,
        third: u8 ← 0u8) → u8:
    …

let n: u8 = four(1u8,
                 2u8,
                 .third ← 8u8)
```

That is Python's rule and is written here for Python's reason: the alternative is a mark at the end of every line that continues,
which a generator would have to emit and a reader would have to look for.  There is no such mark in this language -- a line that
continues is a line inside brackets, and nothing else.

**A semicolon separates and never terminates**, which is the whole of the rule and decides everything else about it.  What follows
a semicolon is another statement; where nothing is written there, that statement is the empty one.  So `a;` is two statements and
not one, `a;;` is three, and a body ending in a semicolon ends in a statement that does nothing.  That matters because the last
statement of a body is the body's result, which the next section is about.

Strings are written as in C/C++ and many other languages enclosed in `"` and supporting an escape notation like modern C and C++ for
special characters as well as the Unicode notation.

#### Integer Literals

An integer literal may be written in decimal, or in hexadecimal, octal or binary with a `0x`, `0o` or `0b` prefix.  Underscores may
separate the digits anywhere.

A literal states its type with a suffix naming that type: `3u8` is a `u8`, `42i32` is an `i32`, `0x1fu8` is thirty-one as a `u8`.
The suffix is the type's own name, so there is nothing to look up.  No ambiguity arises: no name of an integer type begins with a
digit, and none of the letters a hexadecimal literal uses begins one either, so `0x1fu8` splits where it looks like it does.

A literal without a suffix takes its type from the context it appears in.  Where there is a context *and* a suffix they must agree.

Where there is neither, the literal is an **untyped** value: a number of no particular width, which takes the type of wherever it
ends up, as in Odin.  Untyped values are described here and are not implemented yet; a literal that would be one is reported as a
feature the compiler lacks rather than given a width by some default rule.  That the compiler says so is the point -- the
alternative, as C and Rust and Go all take it, is that a rule the reader has to know decides the width silently.

A literal is made negative by a leading `⁻` (U+207B SUPERSCRIPT MINUS) with nothing between the sign and the digits: `⁻3i8`,
`⁻0x1fi32`.  The sign is part of how the number is written, not an operator applied to a number, so `⁻ 3i8` with a space is an
error rather than a subtraction of some kind.

That is why it is a glyph of its own rather than `-`.  Everywhere `-` is both a sign and subtraction, a reader and a parser have to
tell `a -b` from `a - b` from `a-b`, and the answer depends on spacing or on a precedence rule that has to be learned.  Here `-`
will only ever be subtraction and `⁻` only ever a sign, so neither question arises.  This is APL's arrangement, which writes its
negative literals with a high minus `¯` for exactly this reason; the superscript minus is the same idea in a character that says
"minus" outright.

A negative literal is subject to the same rule as any other value: `⁻128i8` is the smallest `i8` and is accepted, `⁻129i8` is not,
and `⁻1u8` is not, because no unsigned type has a negative value to hold.

Compare C's `42U` and `42L`, Rust's `42u8`, and C#'s `42L`; the form here is Rust's, and the fallback is Odin's.

#### Floating-Point Literals

A floating-point literal is written in one of the two forms C has: a decimal one, with a point or an exponent or both -- `3.5f64`,
`1e10f64`, `6.02e23f64` -- and a hexadecimal one, with an `0x` prefix and a `p` exponent that counts powers of two -- `0x1.8p3f64`
is twelve.  Underscores may separate digits in either, as in an integer literal.

The hexadecimal form exists because it is exact by construction: every digit of it is a digit of the value that is stored, so a
number written that way is the number the program gets and no rounding happens in between.  That is why C has it and why it is
here.

The type is named by a suffix, as an integer literal names its own: `f32` or `f64`.  Digits with a floating-point suffix and no
point are a floating-point literal too, so `3f64` is the number three, written without a point that would say nothing.

A point is part of a literal only when a digit follows it.  That is what keeps `1.x` able to mean a member of something once there
are values that have members, rather than a literal followed by a name.

A literal is made negative by the same leading `⁻` an integer literal uses: `⁻2.5f32`.

#### Modules

A file brings another in with a definition:

```
let limits := ⎕import("lib/limits")
```

The name on the left is what the module is called here; the string is the name of the module.  What the module exports is named
through it: `limits.ceiling`.

**`⎕import` carries the sigil every name the compiler provides carries**, and for the reason every one of them does: a program
that wants a variable called `import` should not have to give it up because the compiler wanted the word.  It looks like a call
and is not one -- a module is not a value and there is nothing for an expression to come to -- so it is a shape the syntax knows
rather than a function, which is what the sigil says about it as much as about the name.  Nothing else of it can be named -- what a module does not export is its own, and two modules may
each define a name without either seeing the other's.

A module is read while the program is compiled, and nothing of it reaches the program but the definitions it holds.  It is
therefore not a value: it cannot be given a type, cannot be changed, cannot be computed with, and cannot be brought in inside a
function, where what it holds would belong to one call rather than to the program.

**Where a module is looked for.**  A name beginning with `/` names a file outright and only that place is looked at.  Otherwise the
directory of the file doing the importing comes first, then the directories a build gives the compiler, then the ones the
installation provides -- and the last of those only where the name has no `/` in it, since a name with a `/` is a path and a path
is not something to go looking for somewhere the program knows nothing about.  The extension `.pl4g` is added where the name does
not already end in it.

**A module is read once.**  A file reached from two places is one module: its definitions are in the program once, not once per
route to them.  A ring of imports is an error, because a module is read while the file importing it is being read and a ring has no
beginning.

**What a module is called.**  In the program a module's name is the name of its file without the extension, with the name of
whatever imported it in front, so a module reached through another is `outer.inner`.  A module reached more than one way has more
than one such name and the shortest is the one used -- the one sorting first where two are the same length -- since a program
should not be made to carry the longest way of reaching something.  Two different files with the same name would come to one name,
so both get a few characters of the hash of their path to tell them apart.

Compare Python, where a module is an object and importing one runs it; Go, where an import path is resolved by the build system and
the name is the last element; and C, where `#include` is text and there are no modules at all.  This is closest to Go's in what it
means and to neither in how it is found: the file doing the importing decides first, which is what makes a directory of sources
work with nothing configured.

#### The `std` module

One module the installation provides, holding what a program is started with and the devices it is started with open:

```
let std := ⎕import("std")

type Reader     ※ a device that can be read
type Writer     ※ a device that can be written
type ReadWriter ※ both, which is what a socket and a file opened either way are

type Io   = input : Reader ; output : Writer ; error  : Writer
type Init = io : Io ; args : str⟦⟧

type Pending ※ a write that has been started
enum Error   ※ what the kernel said, where it refused, by name

fn write(to: &mut Writer, what: u8⟦⟧) → Pending ? i32
fn flush(of: Pending)                 → u64 ¤size ? i32
fn write_sync(to: &mut Writer, what: u8⟦⟧) → u64 ¤size ? i32
fn read(from: &mut Reader, into: u8⟦⟧)     → u64 ¤size ? i32
fn drain()
fn as_error(n: i32) → Error
```

**The three a process inherits are the only devices there are.**  There is no way to open anything and no way to write down the
name of a file: what a program has is `init⌖.io.input`, `init⌖.io.output` and `init⌖.io.errors`, on the descriptors every system
gives them.  `ReadWriter` is declared for what will answer one and nothing answers one yet.

**`args` is what the program was named with**, read off the stack before anything runs.  The first is the name it was run under,
as it is everywhere, and the rest are what followed.  Each is a `str` -- bytes and a length -- and the length is counted once
before the program starts rather than by everything that reads one; the kernel's own shape is a nul at the end, which nothing in
this language has a use for.

**The environment is not in this record**: it is `⎕environ`, a name the compiler provides, because it is a table and not a
run of words -- only the entry point can reach the strings and only the compiler knows how a table is laid out.  What a program is
started with is a record so that whatever else it inherits can be added to it without any signature changing; a dictionary is not
one of those things.

**A descriptor is a type and not a number.**  What says a thing may be written is the type of the name standing for it, so there is
no way to hand a `Reader` to `write` and no way to write to a number a program made up.  The three a process inherits arrive in
`init.io`, which is what the startup function may take -- **the record itself and not a reference to it**:

```
@[startup, impure]
fn main(init: mut std.Init) → u6:
    …
```

`Init` is `@[unique]`, so having it is having the thing itself: the devices in it cannot be had twice over, and a program that
passes it on takes the reference where it passes it.  `mut` before the type says the program may write the record it was handed.
What the entry point hands over is still *where* the record is -- it is in the image, the entry point filled it, and there is no
caller to put it in registers -- which is what a record name means everywhere else.

**`std.io` is the three of them, named**, so a program need not be handed anything to write:

```
⎕drop(std.println⌜&mut std.io.output, "x = {}", x⌝ ?? 0)
⎕drop(std.println⌜&mut std.io.error, "went wrong"⌝ ?? 0)
```

The same three `Init` carries and the same values: a descriptor a process starts with is a number the kernel fixed long before this
language existed, so there is nothing to find out and nothing to be handed.  A program that takes `Init` reads them from there, one
that does not reads them from here, and they are the same devices either way.

**A variable another module exports is a place.**  That is what `std.io` needed and what it says generally: a variable at the top
level is a place for as long as the program is, and reaching one through the module that wrote it does not change that -- a
reference may be taken of it, a field of it is read at an offset from it, and the reference lasts as long as the program.

**One mutable reference is the whole of the concurrency rule.**  `&mut init.io.output` is exclusive because a second `&mut` to the
same place is refused, so two names for one device is a thing the compiler refuses rather than a thing a lock prevents.

**Starting a write is not doing it.**  `write` submits the request and answers a `Pending`; the kernel is not told about it until
something waits, so several may be in flight at once.  `flush` waits for one and says how it went, and `write_sync` is the two
together -- which is what a program with nothing else to do while it waits should say.

**Nothing is lost by not asking.**  Every request still outstanding when the startup function returns is waited for before the
process ends; `drain` is the same thing a program may do itself.  A request the kernel has not answered is a write that may not
have happened, and a program that ended without waiting would have written nothing and said nothing about it.

**The handle is the same thing whether there is a ring or not.**  Where there is none the work is done as it is asked for and what
the kernel said is put where an answer goes, so `flush` reads one number out of one place either way.

`flush`, `write_sync` and `read` answer how many bytes went or came, **or why none did** -- a result of the language's own kind, so
that a program that ignores the failure is one the compiler can see ignoring it.  Writing nothing and reading into nowhere are
nothing done, and answer nought.

**What the kernel said comes back as the number it said**, which cannot be wrong.  `as_error` names one for a program that would
rather compare a name: `Error` is an enumeration whose numbers are the kernel's own, so a reader who knows what `EAGAIN` means
knows what `would_block` is, and whose names are the language's, so a reader who does not need not learn them.  `other` is what
anything the list does not name comes to.  What is named is what a read or a write of an inherited descriptor can actually give: a
refusal that belongs to opening is not there, because a name nothing can reach is a comparison every program pays for and none of
them needs.

**Every write is one request and nothing is held back.**  A program that wants fewer, larger writes makes them itself; there is
nothing between it and the device.

**`text` and `⍕` are the text of a value**, and `std` has one definition of each for every type:

```
std.text(1234u32)                ※ "1234"
⍕⁻128i8                          ※ "⁻128"
⍕Point(.x ← 1u8, .y ← 2i16)      ※ "Point(.x ← 1, .y ← 2)"
```

`⍕` is APL's own glyph for this -- monadic format -- and the two are one body under two names: the operator for writing, and the
name for a program that delegates to it.  **What comes out reads back as what went in.**  The raised minus is the one a negative
literal is written with, and a record is written in the notation a program writes one in.

**It is one definition and not one per type.**  The language has no overloading, so a glyph has one meaning per number of operands
and a generic body with `comptime if` says a different thing for each type inside that one meaning.  **Nothing in the number arm
names a width**: the type it is compiled for settles what the arithmetic is, so one body is every integer type there is.  And
nothing is ever negated -- the remainder of a negative number is negative and its digit is what that is away from nought, which is
the only way to write the smallest value a type has, there being no positive of it.

**A record is written by reflection over its fields** (see [Asking what a type is](#asking-what-a-type-is)), so a type `std` was
never told about is written without being told, and a field that is a record is written the same way.

**A program says what its own type's text is by writing the definition for its file**, which wins over the imported one, and
handing every other type back to `std.text`:

```
fn `⍕`(v: T') → str:
    comptime if ⎕typeof(⌜v⌝) = ⌜Secret⌝:
        "hidden"
    else:
        std.text(v)
```

What is not there yet: a `char`, there being no way to make a string of one; a floating-point number, whose shortest text that reads
back as the same number is an algorithm of its own; and an enumeration, whose names do not reach run time.

**`format`, `print` and `println` take a template**, and they are macros:

```
std.format⌜"x = {}, y = {}", x, y⌝
⎕drop(std.println⌜&mut init.io.output, "x = {}", x⌝ ?? 0)
```

The template is handed over as it is written, which is what lets its holes be counted against the arguments **before the program
runs**: a template that does not agree with them is refused at the invocation, in the macro's own words.  `{}` takes the next
argument, and `{{` and `}}` are braces that stand for themselves.

What a template comes to is **a join of the pieces between the holes and `⍕` of each argument**, so nothing happens while the
program runs that would not have happened had the join been written out -- and since it is `⍕`, a type a program defined formats by
the definition that program wrote.

**The room comes from an arena the formatting is told about.**  `std.text` takes a reference to one and every join inside it goes
there; `⍕v in a` is the operator form of the same thing, and it is what `format` writes into the code it generates.  `format` itself
names `⎕heap`, so what it answers lasts -- or **the arena named before the template**, `std.format⌜scratch, "{} and {}", a, b⌝`,
whose joins and `⍕`s are all `in scratch`, so that what it answers goes when `scratch` is given back and a function may answer it
under `→ str in a`.  Which form is meant is read off the first piece: a template is text written out, and anything else names the
arena.  **`print` and `println` make a pool of their own** at every invocation and give it back
when the write has finished -- the expansion is

```
let pool: mut arena = ⎕arena
defer ⎕empty(pool)
Io.println(to, ⎕bytes(…the join, in pool…))
```

standing where the invocation does -- so a formatted line takes room for as long as it takes to write it and not a moment longer.  A
pool the module kept for every print would not do: an argument that itself prints, or any other function emptying it, would give
back text a line was still being built from, and that is why only what made an arena may give it back (below).

Putting those two together: a program that prints a formatted line declares *nothing*.  The device carries permission to write,
allocating is no effect at all, and `@[impure]` is left for a change to something the program did not pass in.

There is no mini-language inside the template: a width or a base is asked for by a call in the hole, where a reader and the checker
can both see it.

**`print` and `println` write every byte they were given.**

```
⎕drop(std.println(&mut init.io.output, ⎕bytes("hello")) ?? 0)
⎕drop(std.print(&mut init.io.output, ⎕bytes("no newline")) ?? 0)
```

That is what they add over `write_sync`, which answers what the kernel took.  **The kernel may take fewer bytes than it was
offered** -- a pipe with room for some of them, a signal part way through -- and a program that printed a line wants the line
printed, so what is left is offered again until none is.  What comes back is the total, which is every byte wherever it answers at
all.  A device that would have to wait and was told not to says so instead of being asked again in a circle: the refusal is the
kernel's number, `would_block` among them.

**`println` writes the newline in the same request**, as a second run of bytes rather than a second call.  `writev` takes a list of
runs, so the text and the newline reach the kernel together and nothing else writing to that device can land between them -- which
is the thing two calls cannot promise and the reason the newline is not simply joined onto the text first: joining allocates and
copies, and a line is written to be written rather than to be kept.  Where the kernel takes fewer bytes than the list held, the
rest is written plainly, which is simpler than walking the list and is the uncommon case.

Compare: **C**, whose `printf` buffers and whose `write` is the thing underneath; **Go**'s `fmt.Println`, which joins the newline
onto the text and writes once; **Rust**'s `println!`, which writes into a locked buffered writer; **Zig**, whose writer interface
makes the caller choose whether anything is buffered.  What is unusual here is that nothing is buffered and the line is still one
request, which is what a list of runs of bytes buys.

**Text is written as its bytes**, which `⎕bytes` answers:

```
⎕drop(std.write(&mut init.io.output, ⎕bytes("hello\n")) ?? 0)
```

A device takes bytes and a program has text, and the two are not one thing said twice: a string is UTF-8 and says so, and this is
where the encoding is named.  It costs nothing -- a string and an array of bytes whose length is not in its type are the same two
words -- and saying it is the point, since `#` answers characters of the one and bytes of the other.

**Everything goes through `io_uring` where there is one.**  A ring is made on the first read or write anything does and driven by
hand: a submission entry filled in, the index published with a releasing write, `io_uring_enter`, and the completion tail read with
an acquiring one.  No thread serves completions; they are taken in by the calls that use the ring.

**Where there is no ring the calls go straight to the kernel.**  It is tried once and not again: a system without `io_uring` will
not grow one, and asking twice would cost a request for every read and write a program ever does.  That a ring is there, or is
not, is not something a program can see -- what it names is a descriptor.

**None of that is written in this language.**  It is the runtime the compiler carries: C, compiled ahead of time for every
architecture, reached through `@[external]` and handed the ring as a record marked `@[abi]`.  What `std` is, is the descriptor
types and two calls; `Ring` is its own and is not exported.

Compare **Rust**, whose `std::io::stdout` is taken by a call and guarded by a lock, a run-time check where a static one is
available; **Go**, whose `os.Stdout` is a package variable anything may write to; **Zig**, which passes a writer explicitly as this
does; **C**, where the three are integers anyone may write to at any time, which is the thing being designed away.

### Expressions

An expression may be enclosed in parentheses, which say how its operators group and mean nothing else.  Where there are none, each
operator binds as tightly as the table below says: a tighter one takes its operands first.

| Operator | Meaning | Binds |
|---|---|---|
| `~` `¬` | complement, written before its operand | tightest |
| `×` `÷` `⊠` | multiplication and division | |
| `+` `-` `⊞` `⊟` | addition and subtraction | |
| `&` | bitwise and | |
| `^` | bitwise exclusive or | |
| `|` | bitwise or | |
| `=` `≠` `<` `>` `≤` `≥` | comparison | |
| `⊼` `⊽` | not both, neither | |
| `∧` `and` | logical and | |
| `⊕` | logical exclusive or | |
| `∨` `or` | logical or | loosest |

The bitwise operators are defined on integer values only, and both sides of one have the same type; the result has it too.  Nothing
is widened to make two types meet, so `1u8 & 2u16` does not compile: a value of one width silently becoming a value of another is
exactly the quiet reinterpretation this language refuses everywhere else.  A literal without a suffix takes the type of whatever it
is written against, on either side, so `count & 3` and `3 & count` mean the same thing.

A truth value is not a one-bit integer.  `bool` has two values and no representation the language promises, so there is nothing for
a bitwise operator to work on; the logical operators below are what applies to a truth value.  That is the rule of Go and Rust; C,
where `&` on two conditions is legal and usually a mistake, is the example not followed.

The relative binding of the three is the one C settled on and Rust, Go and Zig kept, so that a reader coming from any of them reads
these the same way.  C's choice of making them bind *looser* than comparison is a famous defect, which this language does not
inherit: the comparisons bind looser than the bitwise operators, so `a & b = c` reads as `(a & b) = c`.

#### Calls

A function is called by writing its arguments after its name, in parentheses:

```
twice(21u8)
difference(50u8, 8u8)
prepare()
```

**An argument is for the parameter in the place it stands**, unless it says which parameter it is for.  Nothing is variadic, and
each argument has the type of the parameter it is handed to, with nothing widened to make two types meet.  A literal with no
suffix takes the parameter's type, which is what lets a call be written with plain numbers.

##### Saying which parameter an argument is for

An argument may name the parameter it is for, written `.NAME ← VALUE`:

```
fn line(text: str, indent: u8 ← 0u8, width: u8 ← 80u8) → u8:
    …

line("hello")                       ※ indent 0, width 80
line("hello", 4u8)                  ※ indent 4, width 80
line("hello", .width ← 40u8)        ※ indent 0, width 40
line(.width ← 40u8, .text ← "hi")   ※ any order, since each says what it is for
```

**The dot is what says the name is a parameter's.**  A leading dot cannot be a member access, there being nothing on its left for
a member to belong to, so the spelling is free and the reading is unambiguous without looking anything up.  It is what C's
designated initializers, Odin and Zig all write for the same idea in a structure's initializer, and reusing it here means one
mark for "this names a field of the thing being built", whether the thing is a structure or a call.

**Arguments written by place come first and ones written by name after** (4529).  That is the reason the places exist: once an
argument has named its parameter the places no longer count from anywhere, so a call that went back to counting would have to say
from where.  A name that is not a parameter of the function is refused (4527), and so is a parameter given twice -- by place and
then by name, or by name twice (4528).

**The order they are worked out in is the order they are written in**, which is the rule for every list of things in this
language and does not change because the parameters end up in another order.  A call is the only thing that can be noticed
happening, so what a reader sees is the text, not the signature.

##### What a parameter is given where a call gives it nothing

A parameter may say what it is given where no argument does, written `← VALUE` after its type:

```
fn plus(n: u8, by: u8 ← 2u8) → u8:
    n + by

plus(1u8)                           ※ 3
plus(1u8, 10u8)                     ※ 11
```

**A default belongs to the function and not to any call of it.**  What is written is settled while compiling, once, where the
function is (4525), and every call that leaves the argument out hands over that same value -- a call in this file, and a call in
a file that has only imported the function.  A default may therefore be a literal, or a value of an enumeration, and not an
expression naming anything.

Compare: **C++** takes the other road.  A default there is an expression looked up in the definition's scope and worked out
afresh at each call, so `void f(int n = g())` calls `g` once per call and `void f(int n = m)` reads a member the caller cannot
see.  That needs a scope to travel with the function into every translation unit that calls it, which is exactly what C++'s
header model provides and what a language compiling modules separately does not have.  **Ada** and **D** settle for the same
thing C++ does, and **Python** goes further in the other direction: a default there is worked out *once*, when the function is
defined, which is the same answer this language gives -- except that Python's default may be a mutable object, and the famous
trap of a shared default list is what settling a value rather than an expression avoids by having no mutable values to settle.
**Swift** allows an arbitrary expression, worked out at the call.  **C** has no defaults at all, and **Go**, **Rust** and **Zig**
deliberately have none either, on the ground that an overload or an options structure says the same thing where a reader can see
it; this language takes the opposite view for the reason it takes most of them -- a generator emitting a call should not have to
emit the arguments nobody varies.

**Every parameter after one with a default has one too** (4526).  Arguments written without a name fill the parameters from the
left, so a parameter with a default before one without would leave a call one argument short with no way to say which one it left
out.  Naming the arguments is what gets round that at a call; the rule is about what a call written without names can mean.
Every parameter has a value at every call -- one the call wrote, by place or by name, or the default the definition wrote -- and
a parameter with none of the three is refused (4530).

**Arguments are worked out left to right**, in the order they are written.  A call is the only thing in this language that can
be noticed happening -- it may write a variable at the top level, and it may stop the program -- so it is the only thing the
order is visible through, and that is the reason to state it rather than to leave it: where two arguments each do something, a
reader has to be able to say which happened.

```
f(g(), h())          ※ g runs, then h
f(g(), ⁂pair_of())   ※ g runs, then pair_of, whose members become arguments 2 and 3
a + b × c            ※ a is worked out first, though the multiplication groups first
```

The rule is the same everywhere a program writes several things in a row: a call's arguments, a tuple's members, an array's
elements, a set's or a dictionary's entries -- and a dictionary's are worked out key, value, key, value, the order they are
written in.  It is also the rule for an operator's two sides, and **precedence does not move anything**: how an expression groups
decides what is worked out *from* what, not what is worked out first.  `a + b × c` groups as `a + (b × c)` and still works `a`
out before `b`.

**Each is worked out once.**  Writing a call as an argument, an element or an entry calls it one time, which is the other half of
being able to say what a program does.

A call binds tighter than every operator and to whatever stands immediately before it, so `a.b(c)` calls `a.b` and `f(x) + 1` adds
to what the call answered with.  The parentheses are what say a call is being made, not what carry the arguments, so a call with
none is written with them all the same.

Compare: **C and C++ leave the order unspecified**, and C++17 still does for a call's arguments -- `f(g(), h())` may run either
first, and the classic trap is `f(i++, i)`.  What that bought was the freedom to interleave the two, which mattered when
registers were few; a compiler that wants the freedom now has it anyway, because it may reorder anything a reader cannot tell
apart, and what a reader can tell apart is precisely what this rule names.  **Java, C#, JavaScript and Python** all specify left
to right, having decided the same way and for the same reason.  **Go** specifies that function calls inside an expression are
made left to right while leaving the rest of the operand order open.  **OCaml** famously evaluates a call's arguments *right* to
left, which is consistent and still surprises everyone who reads it, being the opposite of the order the text is read in.
**Scheme** leaves the order unspecified on purpose, so that a program may not depend on it at all -- a defensible answer in a
language where most things do nothing, and not one available here, where arithmetic can stop the program.  **Rust** specifies
left to right.

The freedom C keeps is worth nothing to a language that is written by a program: a generator emits what it means, and an order
the compiler chooses is one more thing the generator would have to avoid relying on by accident.

**A call may stand as a statement of its own.**  It is the one expression in the language that does something besides produce a
value, which is why the rule that a statement's value must be used does not apply to it as written.

**What applies instead is that a call's answer must be taken** (4474).  A function that answers with something is a function
whose answer is the point of calling it, so a call whose answer goes nowhere is a mistake or a leftover -- and in a language
emitted by a generator a leftover is a defect in the generator.  A function that answers with nothing needs none of this: there
is no answer for anything to take, and standing as a statement is what a call to one is for.

Two things say that an answer really is not wanted, and they say it at the two places the fact can live:

```
@[can_ignore]
fn bump() → u8:            ※ true of every call to it: the answer is a convenience
    …

_ ← twice(4u8)             ※ true of this call: work it out and drop it
```

**`@[can_ignore]` is written on the function**, because that is where the fact is: a function whose answer is a convenience --
the count it updated, the thing it wrote -- is one every caller may ignore, and saying so once says it where it is true rather
than at each of the places that would otherwise have to repeat it.

**`_` is written at the call**, for the case where most callers do want the answer and this one does not.  It is described under
Assignment, being an assignment.

Compare: C, C++, Java and Go, where a call is the exception to the discarded-value rule outright, so that a dropped answer is
never remarked on -- C and C++ have `[[nodiscard]]` and Java has nothing; Rust, where `#[must_use]` marks the types and functions
whose answers must be taken and the report is a warning.  All of them default the other way round from this, and they can: they
are written by people, for whom the common case is the one worth making silent.  A language emitted by a generator has the
opposite common case -- a generator that emits a call and drops its answer has a defect -- so the default is reversed and
`@[can_ignore]` is what `#[must_use]` would have been.

`_` is Go's, Rust's and Python's blank identifier, used here for the one thing all three use it for and for nothing else.

**A tuple may be handed over as several arguments rather than as one**, by writing `⁂` (U+2042 ASTERISM) in front of it among the
arguments:

```
fn three(a: u8, b: u8, c: u8) → u8:
    a + b + c

let pair: 〈u8, u8〉 = 〈1u8, 2u8〉
three(⁂pair, 4u8)          ※ the same call as three(pair⟦0⟧, pair⟦1⟧, 4u8)
three(4u8, ⁂pair)
```

The glyph is three asterisks arranged as one mark, which is what it says: several things stand where one is written.  Python
spells this with the single asterisk this language leaves free for a future operator, so the meaning is borrowed and the
character is not.

**The members become ordinary arguments.**  Arguments may be written before the spread and after it, more than one tuple may be
spread in the same call, and each contributes its members in the order they stand in.  What the call hands over is the list that
results, and everything the previous paragraphs say applies to that list and not to what was written: the count must be the
function's count, each argument must have its parameter's type, and a tuple of the wrong length is reported as the wrong number
of arguments, because that is what it is.  A generator writing a call therefore need not know how a tuple it is passing along was
put together.

**An array whose type says its length is spread as well**, and for the reason a tuple is: it too is several values travelling as
one, with how many of them there are written in the type.

```
fn ends(top: u8⟦3⟧, bottom: u8⟦3⟧) → u8:
    top⟦0⟧ + bottom⟦2⟧

let v: u8⟦3⟧ = ⟦10u8, 19u8, 20u8⟧
three(⁂v)                          ※ the same call as three(v⟦0⟧, v⟦1⟧, v⟦2⟧)

let m: u8⟦2,3⟧ = ⟦⟦1u8, 2u8, 3u8⟧, ⟦4u8, 5u8, 6u8⟧⟧
ends(⁂m)                           ※ a table is several rows
```

**An array of more than one dimension gives its outermost dimension**, which is the rule `foreach` already follows over one: what
a table is several of, is rows.  A row is a run of elements rather than a copy of them, so this costs the arithmetic on the place
and nothing else.

**An array whose type does not say its length may not be spread.**  It carries its length beside its elements, so how many it
holds is a thing the program works out while it runs, and what the glyph expands into is written into the program being compiled.
This is the one place the two kinds of array part company at the surface, and the type says which is which.

**What is spread must be one of those two.**  Anything else is one value already and stays one, however much it holds; a
dictionary and a set hold as many things as they turn out to hold, which is exactly what a type that counts does not do.

**The glyph stands in the two lists this language has and nowhere else**: a call's arguments, and the members of a tuple.
Several things standing where one is written is a thing only a list has room for, so `⁂` is not an expression and `let q = ⁂p`
is not a program.

Nothing is read from memory for a tuple, and nothing is copied for an array.  A tuple is its members held separately, so
spreading one decides which registers the call is handed; an array's elements are read where they lie, and the reads are the ones
writing the indices out would have produced.

Compare Python, whose `*args` this is, and which needs the mechanism at runtime because its calls are variadic; JavaScript's
`...`, which spreads any iterable and so is equally a runtime matter; C++'s parameter packs, which expand while compiling as
these do but belong to templates rather than to calls; and Lisp's `apply`, which takes the list as its last argument and is a
function rather than a syntax.  This is the compile-time half only: what is spread is a type that counts, so the expansion
happens in the checker and the generated code shows no trace of it.

#### Lambdas

**A function may be written where a value is wanted.**

```
λ PARM: TYPE, … [CAPTURES] → TYPE
```

and then a body, in either notation:

```
let one: fn(u8) → u8 = λ a: u8 → u8 { a + 1u8 }

let two: fn(u8) → u8 = λ a: u8 → u8:
    a + 2u8
```

The parameter list has no parentheses round it, there being nothing before it for them to separate it from -- and it needs none:
what ends it is the capture list, the arrow or the body, and none of the three can be part of a parameter.  **The arrow and the
type after it may be left out**, and then the lambda answers with nothing, which is how a function definition says the same.

Everything a body is, this body is: the last statement is what it answers with and no `return` is written for it, a statement
whose value goes nowhere is reported (5005), and a body that must answer and does not is reported (5003).

**Its type is `fn(TYPE, …) → TYPE`**, the keyword a function is defined with and then what it takes and what it answers.  The
parameter names are not in it because a type is not a definition: what a caller has to know is the types, and what the names are
is the body's business.

**What it comes to is three words**: where its code is, where what it brought in with it is, and the allocator that room came
from -- none for a lambda that brought nothing in, or keeps what it brought in in the frame, as a string carries its own.  That is
one type whether it brought anything in or nothing, so either stands where a `fn(…)` is wanted -- which is what lets a function
take one without knowing which it will be given:

```
fn apply(g: fn(u8) → u8, x: u8) → u8:
    g(g(x))

apply(λ a: u8 → u8 { a × 2u8 }, 3u8)
```

##### What a lambda may name

**Its parameters, what its capture list brought in, and what the whole program has** -- and nothing else.  A name from around it
that is not in the list is not a name inside it at all (4552).  So what a lambda depends on is read off its first line rather than
found by reading its body, which is the whole point of writing the list.

```
let n: mut u8 = 10u8
let by: fn(u8) → u8 = λ a: u8 [n] → u8 { a + n }      ※ what n held
let seen: fn(u8) → u8 = λ a: u8 [&n] → u8 { a + n }   ※ n itself
n ← 20u8
by(5u8)                                             ※ 15: it kept the ten
seen(5u8)                                           ※ 25: it sees the twenty
```

**`[n]` brings in what the name held** where the lambda was written, copied in; **`[&n]` brings in the variable itself**, so a
change afterwards is one the lambda sees and it may write the variable where the variable is `mut`.  That is C++'s distinction
and C++'s mark for it, and the mark is the same `&` a reference type is written with -- what it says here is what it says there.
A name is brought in once (4553), and a name that is not there to bring in is refused (4551).  A lambda that brings nothing in is
written with no list at all, an empty one being a second spelling of that (3041).

**Everything a capture list brings in is used** (4554).  A name in it the body never reaches is a thing the list says and the
lambda does not do: it costs room in what the lambda carries and a copy where the lambda is written, and -- worse than either --
it tells a reader the lambda depends on something it does not.  A capture list is worth reading only where it is true.

**Writing a name is using it** as much as reading it is, a name brought in by reference sometimes being brought in *to* be
written:

```
let put: fn(u8) = λ a: u8 [&n]:
    n ← a                                   ※ the only mention of n, and a use
```

**`[=]` and `[&]` say the same of every name the body reaches** rather than of named ones, which is what those two say in C++:

```
let by: fn(u8) → u8 = λ a: u8 [=] → u8 { a + n + m }      ※ what n and m held
let seen: fn(u8) → u8 = λ a: u8 [&] → u8 { a + n + m }   ※ n and m themselves
```

Which names those are is **what the body writes and does not bind for itself**: its own parameters are not among them, since
those come from the caller, and neither is anything it defines inside, however often that name is written.  The order they are
brought in is the order they are first written, which is the only order there is -- one that changed would make two builds of one
program differ.

A list that says "all of them" says less than one that names them: what a lambda depends on is then found by reading the body
rather than read off its first line.  It is here because a lambda reaching many names is a lambda whose list is mostly noise, and
because a generator emitting one knows what it emitted.

Because what they bring in is what the body reaches, **they cannot bring in too much**.  The rule above is asked of them all the
same: where it ever fires of one, it is the compiler that has got the reaching wrong and not the program.

**They are told from a list of names by what follows the mark.**  `&` begins a capture of a named variable as well, so which it
is, is what comes after it: a name in a list, and the closing bracket here.

##### A capture list is not a parameter's list type

A parameter's type may itself be a list, so `[` can follow a parameter -- and there is still only one reading:

```
λ v: [u8] → u8 { … }              ※ a list of bytes, and no capture list
λ v: [u8] [n] → u8 { … }          ※ a list of bytes, then a capture list
λ v: [u8] [=] → u8 { … }          ※ and with the list that says all of them
λ v: [u8], w: [u16] [&] → u8 { … }
```

A `[` begins a capture list only where a parameter has just been read whole, and **a type ends at its own closing bracket**:
nothing in the language lets a type be followed by `[`, an array being written `⟦⟧` and a lookup being an expression rather than a
type.  So what follows a complete parameter list can be nothing but the capture list, the arrow or the body, and the parser
never has to guess.

##### Where a lambda keeps what it brought in

**A lambda may leave the call that made it**: be answered, be put in a record or a list that is, be written through a reference
into the caller's record.

```
fn adding(n: u8) → fn(u8) → u8:
    λ a: u8 [n] → u8 { a + n }

let add5: fn(u8) → u8 = adding(5u8)
add5(1u8)                                ※ 6
```

**Where what it brought in is kept is the compiler's choice**, as whether a string carries its allocator is.  A lambda that provably
stays in the call that writes it keeps it in that call's frame, which costs nothing to make and nothing to give back.  Every other
keeps it in `⎕heap`.  One stays when it is handed straight to a call of a function, or bound by `let` to a name that is not `mut`
and is from then on only called or handed to a call of a function -- a function handed one is held to the rules below, which do not
let it leave that call.  Anything else may take it further: another name, a field, a list, a capture, an answer.  Which of the
three each lambda got is in the report log (`allocator`).

**`in` after the capture list says where it is kept**, before the arrow -- `in` after the answer's type says where the *answer* is
made, as it does for a function.  `λ a: u8 [n] in pool → u8 { … }` keeps it in the arena `pool`, which then gives it back with
everything else it made: the lambda is made in `pool` as a string written `in pool` is, and is dead once `pool` is emptied (4615).
`[n] in ⎕heap` keeps it in the heap and says so in the lambda's type (below).  A lambda that brings nothing in has nothing to keep,
and `in` is not written for it (4643).

**What a lambda reaches is held to the rule a string made in an arena is.**  It reaches the room it keeps what it brought in in,
what everything it brought in by value was made in, and every variable it brought in by reference -- or reaches through a
reference it brought in.  So:

- **It may not be given to a name that outlives what it reaches** (4570, 4614): a variable of an inner block brought in by
  reference, an arena of an inner block, the frame of a lambda that stays.
- **It may leave the call only reaching what lasts beyond it** (4632): `⎕heap`, the image, an arena the signature names with
  `→ T in a`.  A variable of the call, an arena the call made, and whatever a parameter was handed do not -- the last because the
  caller may have kept it in its own frame, which the signature does not say.  A lambda is never copied, as a string is: what it
  brought in is laid out the way its body reads it, and nothing else knows how.
- **Called after what it reaches was given back, it is refused** (4615), as reading a string would be.

```
fn counting(n: u8) → fn(u8) → u8:
    let seen: u8 = n
    λ a: u8 [&seen] → u8 { a + seen }    ※ 4632: seen is gone with the call

fn twice(g: fn(u8) → u8) → fn(u8) → u8:
    λ a: u8 [g] → u8 { g(g(a)) }         ※ 4632: g may be in the caller's frame
```

**`fn(u8) in ⎕heap → u8` is the type of a lambda whose environment lasts**: kept in `⎕heap`, or none at all.  A value of it reaches
nothing that is gone before the program is, so it may be answered, kept in a field of the caller's, and brought into a lambda that
leaves -- whatever it was handed in as.  Its type promises more than `fn(u8) → u8` does and stands where that is wanted; the other
way is refused (4213), the program saying where it wants the promise kept rather than the compiler guessing.

```
fn compose(f: fn(u8) in ⎕heap → u8, g: fn(u8) in ⎕heap → u8) → fn(u8) in ⎕heap → u8:
    λ x: u8 [f, g] in ⎕heap → u8 { g(f(x)) }

let n: u8 = 10u8
let add: fn(u8) in ⎕heap → u8 = λ x: u8 [n] in ⎕heap → u8 { x + n }
let double: fn(u8) in ⎕heap → u8 = λ x: u8 → u8 { x × 2u8 }    ※ brings nothing in: lasts as it is
let both: fn(u8) in ⎕heap → u8 = compose(add, double)
```

`[n] in ⎕heap` makes one, and what it brings in has to last too (4644): made in `⎕heap` or the image, or a lambda whose own type
says it lasts -- not a variable of the call, an arena, or whatever a parameter that says nothing was handed.  A lambda that brings
nothing in, and a function named where a value is wanted, stand where one is wanted as they are, kept nowhere.  Only `⎕heap` is
written in a type (4642): an arena lasts as long as its maker keeps it, which a type cannot say.

**An environment is never given back.**  What a lambda kept in `⎕heap` stays there, as a string a name held does; one kept in an
arena goes with the arena.  The third word says which allocator it is, so that giving it back can come later without changing what
a lambda is.

**A variable at the top level may not hold a lambda** (4550).  Its value is in the image, and the image does not yet hold an address
of code -- nor, for that matter, a string (9902).

What is left is where a lambda earns its keep: bound to a name, handed to a parameter, answered, and called through whatever holds
it.

**A call through one is a call to whatever it holds**, so nothing about the callee is known: a function that makes one is impure,
because what it calls may do anything.

Compare: **C++**'s lambdas, whose capture list this is, down to the `&`, the `[=]` and the `[&]`.  **Rust**'s closures, which
infer what they capture and sort themselves into three traits by what they do with it; **Go**'s and **JavaScript**'s, which
capture by reference and keep the variables alive by garbage collection; **Java**'s, which capture by value and require what they
capture to be effectively final.  The lifetime question every one of those answers somehow is answered here the way it is for a
string made in an arena: the compiler knows where the environment is and what it reaches, puts it in the heap where it may leave,
and refuses what would reach something gone; where a lambda crosses a call, its type says whether it lasts, as Swift's
`@escaping` and Rust's `'static` bound on an `Fn` do.  **C++** leaves the same question to the programmer, a `[&]` lambda returned being a
dangling reference nothing reports; **Rust** answers it with `move` and lifetimes on `impl Fn`, and boxes the closure (`Box<dyn
Fn>`) where the caller cannot know its size -- which is where every escaping lambda here is; **Swift** marks the escaping parameter
(`@escaping`) and heap-allocates the context of every closure that may escape; **Go** decides by escape analysis, as this does,
with a collector behind it.

#### Narrowing

**`⎕narrow(EXPR, ⌜TYPE⌝)` makes a value of a narrower type out of one of a wider**, and says so where it will not fit.

```
let n: u8?⎕narrowing = ⎕narrow(count, ⌜u8⌝)
let small: u8 = n ?? 0u8
```

Nothing in this language widens or narrows on its own, so a value that is to become one of another type is written as becoming
one.  What makes narrowing different from widening is that it can fail, so **what it answers with is a result**: the value where
it fits, and why not where it does not.

**Widening is `⎕widen(EXPR, ⌜TYPE⌝)` and answers the value itself**, which it may do only because it cannot fail:

```
let n: u64 = ⎕widen(small, ⌜u64⌝) + 1u64
```

The rule is **does every value of the one type fit the other**, and not "is it wider".  A narrower type goes into a wider one of
the same signedness; an unsigned type goes into a signed one where there is room to spare for the bit the sign takes; a signed
type goes into no unsigned one at all, a negative number being somewhere the unsigned type does not reach.  Anything else is a
narrowing and is refused (4591), which is what `⎕narrow` is for.  A value that is already of the type wanted is widened
trivially, so a program that writes it uniformly need not ask whether it had to.  What the number counts is its own and travels
with it (4590 for a type that is not a whole number, 4589 for a second argument that is not a lifted type).

Compare: **C**, which widens silently and whose integer promotions are the reason this language has a name for it; **Rust**, whose
`as` is one word for both directions and says nothing about which happened; **Zig**, whose widening is implicit and whose
`@intCast` checks; **Ada**, where a conversion is written out and checked, which is this pair split the same way.

**The error carries which way it did not fit**, as a value of `⎕narrowing`, an enumeration the compiler provides:

| value | when |
| --- | --- |
| `overflow` | the number is above the top of the type |
| `underflow` | it is below the bottom of the type |
| `sign` | it is negative and the type has no negative values |
| `absent` | the type has no value with that number |

**`⎕narrow` reads a number as a value of an enumeration too**, that being a narrower type than the one it is held as:

```
let why: ⎕narrowing = ⎕narrow(code, ⌜Error⌝) ?? Error.other
```

What differs is that an enumeration's values are not a range: the number has to *be* one of them, and `absent` is what says it was
not.  It is the same question the other three answer -- does this number fit this type -- asked of a type whose values have gaps
between them.  There is no other way to make one: a program that reads a number off a device, a protocol or the kernel and wants
it named would otherwise write a comparison per value.

`sign` is the case of underflow the language can say more about.  A negative number put where an unsigned type wants one is not
merely below the bottom: it is of the wrong kind, and a reader told "sign" knows which mistake was made rather than only that one
was.  `overflow` is numbered nought, which makes it the condition a failure reports where neither of the others holds -- above
the top is the ordinary way not to fit.

The enumeration is the compiler's rather than each program's because every program that narrows anything needs the same three
names: three spellings of one condition would not carry from one file to the next, and a `match` over one would have to be
written again in each.  Its values are written and matched the way any enumeration's are.

**The type is lifted** (4547).  A type is not a value, so it is written between `⌜` and `⌝` as it is everywhere the compiler is
asked about one; without the marks a type's name and a value's name are both identifiers and which was meant would depend on what
the name turned out to be.

**Both types are whole numbers** (4548).  Whether a value fits is a question about the ends of a type, and those are the types
with ends a value can fall outside.  A floating-point number narrowed to a whole one asks what is to be done with what is after
the point, which is a different question and not one the language answers yet.

**Each condition is asked only where it can hold.**  A type whose every value the other one has cannot be overflowed into, and a
source with no negative values cannot be negative -- so those comparisons are settled by the two types while compiling and none
of them reaches the program.  Narrowing to a wider type therefore costs nothing at all, and narrowing `u32` to `u8` costs one
comparison.

**A unit stays.**  Narrowing a length gives a length: what changes is how much room the number has, and a unit says nothing about
that.  The unit is written where it belongs, before the mark that makes the type a result:

```
fn shorter(far: u32 ¤meter) → u8 ¤meter?⎕narrowing:
    ⎕narrow(far, ⌜u8⌝)
```

Compare: **C**'s implicit conversions, which narrow silently and are the source of an entire category of defect; **C++20**'s
`{}` initialization, which refuses a narrowing the compiler can see will not fit and says nothing about one it cannot;
**Rust**'s `as`, which truncates silently, beside `TryFrom`, which answers a `Result<T, TryFromIntError>` -- this is that, with
the error saying *which* way it did not fit rather than only that it did not.  **Go** truncates silently.  **Ada** raises
`Constraint_Error`, which says which subtype was violated at the cost of an exception mechanism this language does not have.
**Swift**'s `init?(exactly:)` answers an optional, which is Rust's answer with the reason thrown away.

#### Arithmetic

| Operator | Meaning |
|---|---|
| `+` | addition |
| `-` | subtraction |
| `×` | multiplication |
| `÷` | division |
| `%` | what is left over after division |

Multiplication and division are glyphs rather than `*` and `/`, for the reason every glyph here is a glyph: a single ASCII
character spent on an operator is one no future feature can have, and `×` and `÷` are what the operations are written with
outside programming.  `+` and `-` keep their characters, there being nothing else they could reasonably mean -- and `-` is
unambiguous because the sign of a negative literal is `⁻` and not a minus, so `a - ⁻1i8` needs no rule about spaces.

Both operands have the same type, as everywhere, and the result has it too.

**An answer that will not fit stops the program.**  `200u8 + 100u8` is 300, which a `u8` cannot hold, so the program does not
continue with 44 and does not continue with 255: it stops, and says so.  Nothing is wrapped and nothing is left undefined.

```
t.pl4g:6:5: pl4g: addition that does not fit in 'inner'
  called from 'middle'
  called from 'outer'
  called from 'main'
```

The first line is where the fault was, and **the lines under it are how the program got there**: one per function still standing
when it happened, outwards, ending where the program was started.  The function the fault was in is not among them, the first line
having named it.  A function that was put where it was called is not on the stack and so is not named: what this says is where the
program was, which is not always what the program was written as.

The message is built whole when the program is compiled -- the compiler knows which operation it was, in which function, at which
line -- and so is every line under it.  What runs at the moment of the fault is a write per line and nothing else: no formatting,
no number to turn into text, no allocation, nothing that could itself fail.  That matters more here than anywhere else, because
this is the code that runs when something has already gone wrong.  It goes to standard error through a raw system call, that being
the only place a program depending on nothing from the system can write to.

The program then **exits with a status out of the range the runtime reserves**, 79 through 127, and with the number that kind of
fault has: 80 where an answer will not fit, 82 where an index was outside what it names, and so on down the table below.  A signal is not a status: a shell
reports one as 128 plus the number, which collides with whatever the program might have chosen to exit with, and a caller has to
know to look for it.  A program that dies of a signal really did die of one, and that is worth being able to believe.

Where an overflow can be shown at compile time it is a compilation error rather than a fault, since a program that must stop every
time it runs is a program that need not be run.

**Division truncates toward zero**, and `%` is what is left over after it, so what is left over carries the sign of what was
divided: `⁻7i8 ÷ 2i8` is `⁻3i8` and `⁻7i8 % 2i8` is `⁻1i8`.  That is what all three architectures' instructions do and what C, Rust, Go,
Java and Zig all say; Python floors instead, and would answer `⁻4` and `1`.  Taking the hardware's answer costs nothing and means
`a` is always `(a ÷ b) × b + a % b`.

**Two divisions have no answer, and neither stops the program: a division answers with a result.**  Dividing by zero is the
obvious one -- and the three architectures do three different things about it, one raising a fault of its own, one answering with
all ones and one with zero, so it is asked about first and the same thing happens everywhere.  The other is the most negative
number divided by `⁻1`, whose quotient is one past the largest the type can hold; it is the only pair that overflows, and an
unsigned type never meets it.  `u8 ÷ u8` is therefore a `u8?` and not a `u8`, and what takes the number out of one is `?` or
`??`; **Results** below says how.

That is the one place the language answers a missing answer with a value rather than by stopping.  The difference is that these
two cases are ordinary -- a divisor a program did not choose is zero often enough that every program dividing by one has to say
what to do about it -- where an addition that overflows says the type was wrong.

Compare C, where signed overflow is undefined and unsigned overflow wraps, and where `-ftrapv` and the sanitizers exist because
neither answer is what anyone wanted; Rust, which panics in a debug build and wraps in a release one, so that a program means two
different things depending on how it was built; Zig, which faults in both and has `+%` for wrapping; and Swift, which faults and
has `&+`.  Zig's and Swift's position is the one taken here, with `⊞` and its relatives in place of `+%` -- and with no wrapping
operator at all, wrapping being a thing to ask for by writing the wrap rather than by writing an operator that hides it.

#### Raising to a power

**`a ⁿ b` raises `a` to the power of `b`**, and **`a²` raises it to a power written as a raised number**.  The glyph is the
letter mathematics writes an exponent that is not a number with, raised the way an exponent is written.

```
a ⁿ b                          ※ u32?: a raised to whatever b is
a²                             ※ u32:  raised to a power written down, which cannot be negative
a¹⁴                            ※ u32:  raised to the fourteenth
a⁻²                            ※ u32?: one divided by the square, which is a division
1.5f64³                        ※ 3.375: what is raised is a number, what it is raised by is a count
```

**The two are not two spellings of one thing**, and what tells them apart is what they answer with.

| Written | Answers | Because |
|---|---|---|
| `a ⁿ b` | `T?` | the exponent may turn out to be negative, and a negative one is a division |
| `a²` | `T` | the exponent is written down and is not negative, so there is always an answer |
| `a⁻²` | `T?` | the exponent is written down and *is* negative, so this is a division |

**A negative exponent is one divided by the positive power**, which is a division and answers a result exactly as `÷` does:
there is no answer where what was raised is zero.  For an integer that makes a negative power almost always nothing, which is
what dividing one by a whole number greater than one *is*; the operator is not the place to decide that a program did not mean
it, and the division it is written as is the one the program would have written itself.

**The operator's answer is a result whatever the exponent turns out to be.**  Whether it is negative is not known where the
operator is written, so the type cannot depend on it -- exactly as a division's cannot depend on whether the divisor turns out to
be zero.  A raised number is known, so `a²` carries no mark for a failure that cannot happen.

**Its two sides are not of one type**, and it is the only operator here of which that is true.  What is raised is a number of
whatever type it is, and what it is raised by is a **count** -- how many times to multiply the one by itself.  So the answer has
the type of the left side and the right side is an integer of its own, which is what makes `1.5f64³` the natural thing to write.
A raised number with nothing to say what width it is, is a `u64`, since an exponent is never the thing a program is being careful
about.

**The exponent is a whole number** (4510).  A fractional power is a root, which no instruction on any of these machines computes
and which this language does not offer; one is written as a call to whatever computes it.

**What is raised is a number** (4509), which is what may be multiplied: an integer or a floating-point value.

**Anything raised to no power at all is one**, including zero.  The empty product is one, and that is what every polynomial
written anywhere means by it.

**It binds tighter than multiplying and is right associative**, as it is on paper: `2 × a²` squares `a` first, and `a ⁿ b ⁿ c`
is `a` raised to what `b ⁿ c` came to -- the only reading that is not a longer way of writing `a ⁿ (b × c)`.  A number written
raised binds where a call and an index bind, which is to whatever stands immediately before it: `f(x)²` squares what the call
answered with.

**Every multiplication it does is a multiplication**, with the check that one carries: an answer that will not fit stops the
program, and inside `⎕wrap` it goes past the end of the type instead.  Where both sides are written down the answer is worked
out while compiling and one that will not fit is refused there (4214).

Compare: **Python**, whose `**` this is and which answers a *float* for a negative integer exponent -- a language whose integer
types say what they hold cannot do that, so the answer here is the division rather than a change of type; **Fortran** and **Ada**,
whose `**` raises an integer by a non-negative integer and makes a negative exponent an error, which is the third answer to the
same question; **APL** and **BQN**, whose `⍟` and `⋆` take a float exponent, having a float everything; **C**, which has no
operator and whose `pow` is a floating-point library function, so that `x*x` is what everyone writes and `pow(x, 2)` is a famous
performance mistake; **Rust**, **Go** and **Zig**, which have no operator either and whose `pow`/`powi` name the integer and
floating-point cases separately; and **Haskell**, which has three operators -- `^`, `^^` and `**` -- one per combination of
integer and fractional, which is the most honest arrangement anyone has and the hardest to remember.

#### Dividing without a remainder

**`a ∣ b` asks whether `a` divides `b` with nothing left over**, and **`a ∤ b` whether it does not.  Written before one
operand it is the same operator with two on the left**, which is the question "is it even".

```
b ∣ a                          ※ bool: does b divide a exactly
b ∤ a                          ※ bool: and does it not
∣n                             ※ bool: is n even -- the same as 2 ∣ n
∤n                             ※ bool: is n odd
```

**It relates two numbers and answers a truth value**, which is a comparison's shape -- so it is written where a comparison is
written, binds as one, and joins two and no more: `a ∣ b ∣ c` would be asking whether `a` divides a truth value.

**Both sides are integers of one type** (4518), the usual rule: a number written without a suffix takes the other side's type.
A floating-point division leaves nothing over by construction, so there is no question here to ask of one.

**What it answers depends on what the compiler can see of the divisor.**

| Written | Answers |
|---|---|
| `∣n`, `∤n` | `bool` -- two is the divisor and two is not zero |
| `3u8 ∣ n` | `bool` -- the divisor is written down and is not zero |
| `a ∣ n`, where `a` is worked out | `bool?u8` -- it may turn out to be zero, and then there is no answer |

**Nothing divides by zero**, so there is a pair of operands the operator has no answer for -- which is what a result is for, and
is the same shape `÷` already has.  **What the error carries is the number the question was asked about**, since what made it
fail is known from the failure itself: the divisor was zero, and a zero says nothing a reader did not have.

```
3u8 ∣ n                        ※ bool
a ∣ n                          ※ bool?u8
(a ∣ n) ?? false               ※ bool: and what to say where there was no answer
```

The parentheses there are not optional: `??` binds tighter than a comparison, and this binds where a comparison binds, so
`a ∣ n ?? false` is `a ∣ (n ?? false)` -- the same grouping `x = a ÷ b ?? c` has and for the same reason.

**A zero written on the left is refused** (4520).  Where the divisor is written down the answer is a truth value, which has
nowhere to say there is no answer -- and a program that wrote a zero there asked a question it knew the answer to.

**Where the divisor is written down there is no test for zero at all**, and no result either -- which is always so for the form
written before one operand, two being written into it.

**It is listable**, so written with an array on either side it answers a truth value per element.

Written before an operand of a type that has no two -- a `u1` -- it is refused (4519): it is the operator with two on the left,
and there is no two to write.  What was meant there is a question about that one bit, and comparing it with nothing says it.

Compare: **mathematics**, whose `∣` and `∤` these are; **C**, **Rust**, **Go** and everything else in that line, which write
`b % a == 0` and get an undefined program or a signal where this gets a result to read; **Ada**, whose `rem` and `mod` are the
two remainders and which has no divisibility test; **Python**, the same with `%` and a `ZeroDivisionError`; and **APL**, whose
`|` is the remainder and which spells the test `0 = a | b`, which is this operator with the comparison left to the program.

#### Moving bits

Four operators move the bits of a number sideways.  They bind where multiplication does.

| Operator | Meaning |
|---|---|
| `«` | shift left |
| `»` | shift right |
| `↺` | rotate left |
| `↻` | rotate right |

The two angle marks point the way the bits go and the two circle arrows turn them round.  Both operands have the same type, as
everywhere, and the distance is a value of that type.

**What falls off the end of a shift is gone.**  That is what a shift is, and it is why a shift does not fault on bits lost the way
an addition faults on a sum that does not fit: `«` is how a bit pattern is built, and a pattern that grew past the end of the type is
one that was asked for.  A rotation puts back at the other end what a shift would have dropped.

**Shifting a signed number right brings in copies of its sign**, so `⁻8i8 » 1i8` is `⁻4i8` and not a large positive number.
Shifting an unsigned one brings in zeroes.  The type is what decides, and there is no second operator for the other reading.

**A distance of the width of the type or more stops the program.**  There is no answer to give: the three architectures answer
such a shift three different ways, and two of them take the distance modulo the width of the *register*, which is not the width of
the type.  So the distance is compared with the width and the program stops where it is too far, which is the same thing happening
everywhere.  A negative distance is caught by the same comparison.

C leaves this undefined and is where a good deal of trouble with it comes from; Rust panics in a debug build and masks in a
release one; Go and Zig define a shift by too much as zero.  Stopping is what this language does with every other operation that
has no answer, and doing the same here is what makes the rule one rule.

**A rotation turns the bits of the type**, not of the register the value happens to be held in -- `13u8 ↻ 1u8` is `134u8`, which
is what turning eight bits round gives.  It is defined on **unsigned** types only: the bit that would come round into the top of a
signed number is the one that says which sign it has, so the answer would be a number unrelated to the one that went in.  Where
the bits are what is wanted, an unsigned type is what says so.  Rust rotates signed integers too, and is the example not followed.

#### Counting bits

Two names the compiler provides ask about the bits of a number rather than about the number.

```
⎕ones(200u8)      ※ 3 -- how many of its bits are set
⎕lead(1u8)        ※ 7 -- how many zeroes stand above the highest one that is
```

**Both count over the type's own width.**  `⎕ones(255u8)` is eight and `⎕ones(255u64)` is eight as well, and
`⎕lead` of the two is nought and fifty-six: the bits above a value's type are not the value's, and the zeroes above its
highest set bit are the ones its type has room for.  A signed number is counted as the bits two's complement gives it, so
`⎕ones(⁻1i8)` is eight.

**`⎕lead` of nought is the width of the type.**  That is the only answer that makes a count of leading zeroes a count of
leading zeroes, and it is said here because two of the three architectures have an instruction that leaves it undefined -- a
program would otherwise mean one thing on one machine and another elsewhere.

**Both answer with a `u8`** whatever they were given, the widest count there is being sixty-four.  A count is not of the same kind
as the thing counted: adding it back to the number it came from would be adding a length to a number, and the type saying so is
what makes that a thing a reader sees.

Compare: **C**, where these are `popcount` and `clz` built in to two compilers and undefined for zero in both; **Rust**, whose
`count_ones` and `leading_zeros` are methods of every integer type and define zero the way this does; **Go**, whose `math/bits`
says the same; **Zig**, whose `@popCount` and `@clz` likewise.  The disagreement in the field is only about zero, and the answer
every language that chose one picked is the width.

#### Saturating arithmetic

Three operators compute a sum, a difference and a product that **answer with the nearest value the type can hold** rather than
going past it.

| Operator | Meaning |
|---|---|
| `⊞` | saturating addition |
| `⊟` | saturating subtraction |
| `⊠` | saturating multiplication |

Each is the sign of the operation it is built from, in a box; what the box says is that the answer stays inside something.  So
`200u8 ⊞ 100u8` is 255 and not 44, and `5u8 ⊟ 9u8` is 0 and not 65532.  Both operands have the same type, as everywhere, and
the result has it too.  They are defined on integers.

**These are the operations for which going past the end is the intended answer.**  The ordinary `+`, `-` and `×` above fault when
a result does not fit, which is the right answer where a result that does not fit is a mistake; these are for the places where it
is not -- a counter that stops at its maximum, a difference that stops at zero.  A language with only the first kind makes the
second kind be written as three statements and a comparison, and one with only the second makes every mistake silent.

Multiplication binds tighter than addition, as it does in writing, and all three bind tighter than the bitwise operators.  That
last is C's order too, and the one place C's order of operations was not a mistake.

Compare Rust, which has `saturating_add` and its relatives as methods and no operators; Zig, which has `+|`, `-|` and `*|`; and C
and Go, which have neither and leave it to be written out.  Zig's position is the one taken here -- that these are common enough
to deserve a notation -- with glyphs instead of punctuation pairs, for the reason every glyph in this language is a glyph.

#### Going past the end on purpose

**`⎕wrap(EXPR)`** is written around an expression whose operators are to go past the ends of their types and keep the low
bits, which is what C does and what this language otherwise refuses.

```
⎕wrap(200u8 + 100u8)              ※ 44, and not a program that stops
⎕wrap(counter × 2654435761u32)   ※ a hash, whose whole point is the bits it ends with
```

**It is not a function.**  Nothing is called, nothing is passed, and it answers with whatever the expression inside answers with
-- so an unsuffixed literal inside takes its type from outside exactly as it would have without it.  It is written like one
because there is no other notation that says "this far and no further" about an expression, and because parentheses are where a
reader already looks for that.

**Every operator written inside it that could go past the end wraps.**  `+`, `-` and `×` keep the low bits instead of
stopping the program.  `«`, `»`, `↺` and `↻` take their distance modulo the width of the type instead of stopping the
program where it is the width or more -- every width is a power of two, so that is exact.  The bitwise operators and the
comparisons are unchanged, having nothing to go past.

**`⊞`, `⊟` and `⊠` inside a wrap are an error.**  A wrap says the low bits are the answer; a saturating operator says the
end of the type is.  Only one of the two can be what the program meant, and which one is not something for a compiler to guess
at -- so it is reported rather than resolved.  Where the saturating step really was meant, it is written outside the wrap and its
answer handed in:

```
let held: u8 = a ⊞ b               ※ this one stops at 255
⎕wrap(held × 3u8)                ※ and this one runs round
```

**Dividing and taking a remainder are unchanged.**  Neither can go past the end of a type by arithmetic; what they have is a
pair with no answer at all, and a wrap has nothing to say about that, so there is nothing for them to contradict either.

**It reaches what is written inside it and no further.**  A function called from inside one was written somewhere else and says
for itself what its operators mean:

```
fn summed(x: u8, y: u8) → u8:
    x + y                        ※ this addition checks, wherever it is called from

⎕wrap(summed(200u8, 100u8))       ※ stops the program, inside `summed`
```

**It reaches a whole array as it reaches a single value**, which is where it costs the most and saves the most: an operator over
an array asks the same question of every element, and a wrap is what says that question is not being asked at all.

Compare: **C and C++**, where unsigned arithmetic wraps and signed arithmetic is undefined, so there is no way to ask for one
without the other and no way to ask for neither, and where nothing contradicts anything because nothing is said; **Rust**, whose
`wrapping_add` and relatives are methods, one per operation,
which reads as a different program rather than as the same program with one thing said about it; **Zig**, whose `+%`, `-%` and
`*%` are operators of their own -- the closest of the three, with the difference that there every operator in an expression has
to carry the mark and here the expression carries it once; **Go**, which wraps always and offers nothing else; and **Swift**,
whose `&+`, `&-` and `&*` are Zig's answer with different spelling.  What none of them has is a way to say it of a region rather
than of an operator, which is what makes `⎕wrap` worth being a wrapup rather than nine more glyphs.

#### How many

**`#x` answers how many things `x` is made of**, as a `u64`.  What that means is the thing's own business:

| Written before | Answers |
|---|---|
| a string | how many **characters** it has, not how many bytes |
| an array | how many along its **outermost** dimension |
| a tuple | how many members it has |
| a set or a dictionary | how many it is holding just now |

```
#"a£€"                        ※ 3, and the bytes are six
#⟦1u8, 2u8, 3u8⟧                ※ 3
#m                             ※ 2, of a u8⟦2,4⟧ -- rows, not elements
#〈1u8, 2u16〉                    ※ 2
#⸨1u8, 2u8, 5u8⸩              ※ 3
```

**A string's count is the only one that is work.**  A tuple's is how many members its type names, a fixed array's is the first
number of its shape, a dynamic array's is the count it carries beside where its elements are, and a table's is a field of the
table -- all four are read or are already known.  A string's is a walk, because there is no arithmetic on the number of bytes
that gives the number of characters; that is the whole reason this operator says "characters" and not "bytes", since the number
nobody has to walk for is the one nobody wants.

**It binds as tightly as the other operators written before their operand.**  So `#v + 1u64` asks about `v` and adds one, and
`#m⟦1⟧` asks about the row rather than about the table -- an index binding tighter than any operator, as a call does.

**It is not walked over an array** the way `~` and `¬` are.  Those are defined on values and reach an array by being applied to
every element; this one is defined on the array itself, and asking it of every element would be a different question about a
different thing.

Asking it of something that is one thing is an error (4495).

Compare: **APL**, whose monadic `≢` is this exactly -- the length of the leading axis -- and whose `⍴` gives the whole shape;
**Python**, whose `len` is this for every one of these and is a function; **Go** and **Rust**, where `len` is a method and a
string's is *bytes*, so counting characters is a different call and the easy one is the one that is usually wrong; and **C**,
where `strlen` walks and `sizeof` does not and the two are spelled so differently that nobody confuses them, which is the one
thing C got right here.

#### The largest and the smallest

**`⌈` and `⌊` are APL's ceiling and floor doing APL's other job.**  Written between two things they answer the larger or the
smaller of the two; written before one thing they answer the largest or the smallest of what it holds.

```
a ⌈ b                          ※ the larger of the two
a ⌊ b                          ※ the smaller

⌈⟦3u8, 9u8, 2u8, 7u8⟧            ※ 9u8
⌊"a£€"                         ※ 'a': the smallest code point in it
⌈⸨1u8: 10u16, 7u8: 20u16⸩      ※ 7u8, a dictionary being asked about its keys
```

**One glyph for both forms is not an economy.**  The one-sided form is the two-sided one applied along the thing, so `⌈v` and
`v⟦0⟧ ⌈ v⟦1⟧` agree on an array of two, and a reader who knows one form knows the other.

**What "what it holds" means is the thing's own business**, as it is for `#`:

| Written before | Answers |
|---|---|
| an array of one dimension | the largest or smallest of its elements |
| an array of more | a **row**: its outermost dimension walked, the elements underneath compared |
| a tuple | the largest or smallest of its members, which must all be of one type |
| a string | a `char`: the highest or lowest code point in it |
| a list | the largest or smallest of its elements |
| a set | the largest or smallest it is holding |
| a dictionary | the largest or smallest **key** it is holding |

**An array of more than one dimension answers a row**, and that is APL's rule.  Its outermost dimension is walked and the
elements underneath are compared with each other, so the *j*-th of the answer is the largest of the *j*-th of every row and the
answer has the shape of one of its rows.  It works however deep the array goes, a row of a row being a row.

```
let m: u8⟦2,3⟧ = ⟦⟦1u8, 8u8, 3u8⟧, ⟦5u8, 2u8, 9u8⟧⟧

⌈m                             ※ u8⟦3⟧ holding 5 8 9
```

Nested arrays have a regular shape -- every element at the same depth is the same shape -- which is what makes that answer a
single type.  Now that lists exist there is somewhere for a ragged collection to live, so the array types can keep the rule
rather than weaken it to admit one.

**A dictionary is asked about its keys.**  Comparing a key with what it stands for is comparing two different things, and a
dictionary that answered a pair would answer something no comparison had ordered.

**Both operands must be comparable with each other** (4506).  A tuple whose members are of different types is not several of one
thing: there is no type for the answer to have.  Lists keep the same rule and will keep it when a list may hold several types --
what is refused is not the writing but the question.

**The largest of nothing stops the program.**  It is not a value of any type, and answering with the end of the type -- the
smallest `u8`, say -- would be a lie about what was there.  A list, a set, a dictionary, a string or an array whose length the
type does not say is asked about its count once, before the walk, and never again.

**The two-sided form is listable**, so one side may be a whole array and the answer is elementwise, the same rule every operator
written between two operands follows.

**It binds looser than the arithmetic and tighter than the bitwise operators**, so `3u8 + 2u8 ⌈ 4u8` is the larger of the sum
and the other side.  The one-sided form binds as tightly as the other operators written before their operand, so `⌈m⟦1⟧` asks
about the row.

**On floating-point values it is one instruction**, all three of these machines having one, and it is the only arithmetic
operator that asks nothing afterwards about what came out: it answers with one of the two it was given, which the program had
already.

Compare: **APL**, whose `⌈` and `⌊` these are, both forms, with the difference that reduction there is written `⌈/` and the
one-sided glyph means ceiling -- this language has no ceiling of an integer to want, and `⌈` before a thing is free to mean the
reduction; **BQN** and **Uiua**, which follow APL; **C**, whose `fmax` is the two-sided form as a function and which has no
integer one; **Python**, whose `max` is both forms as one function and answers the largest of the *arguments* when there are
several, which is a third meaning for the same name; **Rust**, whose `Ord::max` is the two-sided form and `Iterator::max` the
one-sided, answering an `Option` where this stops the program; and **Haskell**, whose `max` and `maximum` are the two forms
under two names.

#### Comparisons

Six operators compare two values.  All six bind equally, and looser than every other operator.

| Operator | Asks | Substitute |
|---|---|---|
| `=` | whether the two are the same value | |
| `≠` | whether they are not | |
| `<` | whether the left comes before the right | |
| `>` | whether it comes after | |
| `≤` | whether it comes before it or is it | `<=` |
| `≥` | whether it comes after it or is it | `>=` |

**A comparison answers with a truth value**, whatever it compared, so the result of one is a `bool` and the type of what was
compared says nothing about it.  As with every other operator, both sides have one type and nothing is widened to make two types
meet; a literal without a suffix takes the type of the other side, so `count < 3` and `3 > count` mean what they look like.

`=` compares and does not assign.  An assignment is written `NAME ← VALUE` and there is no other way to write one, so `=` is
free to ask a question and asks nothing else.  That is why a comparison whose answer is discarded is refused: `count = 1u8`
written as a statement is what someone writes who has spent years in a language where `=` assigns, and it would otherwise be a
line that quietly does nothing.  The last statement of a body is the body's result, so a comparison there is the point of the
line and is not reported.

**What each of the six is defined on:**

| Type | Equality | Ordering |
|---|---|---|
| numbers | yes | yes |
| `char` | yes | yes, by code point |
| `str` | yes | yes, by code point, character by character |
| truth values | yes | no |
| an enumeration | yes | no |
| a set or a dictionary | yes | no |

Two truth values can be the same or different, but neither comes before the other, so `ready < seen` is refused rather than given
an answer by way of the representation.  An enumeration's order is the order somebody happened to write the values in, and the
language promises nothing about it.  Two collections are one collection or they are not; whether one is part of another is a
question Python answers with `≤`, and here ordering asks which comes first, which neither does.

**A `char` is ordered by its code point**, which is a real order and not one of the representation's making: Unicode numbers the
code points, and every collation in the world starts from that numbering before it does anything else.

**A `str` is ordered by its characters**, the first one that differs deciding, and a string that is a prefix of another comes
first.

```
"abc" < "abd"                  ※ true: 'c' before 'd'
"ab" < "abc"                   ※ true: a prefix comes first
"a£" < "a€"                    ※ true: U+00A3 before U+20AC
"" < "a"                       ※ true: a prefix of everything
```

**Nothing is decoded to answer it.**  UTF-8 was designed so that comparing the bytes of two strings gives the same answer as
comparing the code points they stand for, so the ordering above is a walk of bytes -- which is both the definition and the
implementation, and the cheapest thing either could have been.  That is what the encoding was *for*, and a language that decoded
first would be paying to arrive at the same answer.

This is an ordering of code points and not a collation.  `"Z" < "a"` and `"ä" > "z"`, because U+005A comes before U+0061 and
U+00E4 after U+007A; which words come first in a dictionary is a question about a language and a locale, and it is not a question
`<` can answer.  What `<` answers is a total order that is the same everywhere, which is what a program sorting or keying by
strings needs.

**The comparisons do not chain.**  `a < b < c` is refused, because a comparison answers with a truth value and a second one beside
it would be comparing that answer with a number.  Where that is what was meant, parentheses say so: `(a < b) = ready` compares two
truth values and is well formed.  Where the intent was that a value lies between two others, that is two comparisons joined with
`∧`.  Rust refuses the same writing for the same reason.  Python gives it a third meaning by chaining, which reads
well and is the only language that does it; C and Go give it the meaning above and leave it to be a mistake.

Splitting equality from ordering, as C does, would decide only what `a < b = c` means, and that expression has no meaning here.
One level for all six is what Go and Rust do.

#### Floating-point numbers

`f32` and `f64` hold binary floating-point numbers, as IEEE 754 defines them, and the hardware's own floating-point instructions
are what operate on them.  A binary this compiler writes therefore requires a target that has them.  On x86-64 and on AArch64 that
requirement is already the base of the ABI and so nothing has to be recorded; on RISC-V it is the header's flag word, which says
the double-precision convention.  A soft-float target is not supported, and the language does not describe one: the point of a
type that says what a value is would be lost if the same program were fast on one machine and a hundred times slower on another
without saying so.

**Four operators are defined on a floating-point value**: `+`, `-`, `×` and `÷`.  Everything else is not, and is refused rather
than given a meaning:

| Refused | Because |
|---|---|
| `&` `\|` `^` `~` | these are questions about bits, and a floating-point type says the value is a number and not the bits it is kept in |
| `«` `»` `↺` `↻` | the same, and there is no sense in which a floating-point value has a bit to move sideways |
| `⊞` `⊟` `⊠` | saturating means the nearest end of a range of whole numbers, which a floating-point type has no notion of |
| `%` | what is left of a division that stopped at a whole number, which is not the division this type does |

`÷` on floating point is a third operation and neither of the two that integers have: it truncates towards nothing.  It answers
with a result, as the whole-number divisions do, and the one pair it has no answer for is a divisor of zero -- which is what keeps
`1f64 ÷ 0f64` from being an infinity that the rule below would then stop the program over.  `f64 ÷ f64` is an `f64?`.

**An answer that is not a finite number stops the program.**  Every one of the four operations is followed by a check, and a
result that is an infinity or a not-a-number reports where it happened and stops, exactly as an integer sum that will not fit
does.  A division asks its own question first, so a divisor of zero is the error and never reaches this.  Where both operands are
written down, the compiler sees it while compiling and reports it then (4214) rather than
building a program that must stop whenever it is started.

That is the language's own rule applied to this type rather than an exception carved out of it: everywhere else a value a program
holds is a value its type can represent, and an infinity is what the format says when it cannot say the number.  A program that
carried one would then be computing with a value that stands for no number, and every answer after it would stand for no number
either, which is how a mistake in a floating-point program usually travels a long way from where it was made.

The check costs a subtraction, a comparison and a branch not taken.  Subtracting a value from itself answers zero where the value
is finite and not-a-number where it is an infinity or already a not-a-number, so one question settles both cases.

Compare: C leaves it to the floating-point environment, where the flags are raised and almost no program reads them, and to
`-ftrapping-math`, which is on by default and traps nothing without the exceptions being unmasked.  Java and Go define the
infinities into the language and carry them.  Rust does the same and offers `checked` arithmetic for the integers alone.  Nothing
in common use stops by default, and the reason is compatibility with C rather than a judgement that carrying on is right.

**All six comparisons are defined**, and `=` and `≠` on floating point are diagnosed (4217).  Two floating-point values arrived at
by different routes are rarely the one value even where the numbers they stand for are equal, so asking whether they are is nearly
always the wrong question.  It is a warning and not an error because the question is sometimes the right one -- a value compared
against one it was assigned from, or against a number every format holds exactly -- and `@[ignore(4217)]` is how a program says it
meant it.  The question usually meant is the approximate one, which is written with its own operators.

Compare: C, Go, Rust, Zig and Odin all let `==` on a floating-point value stand without a word; a warning for it is something
several lint tools offer and no compiler turns on.  This language turns it on because a diagnostic that names the number of the
rule is the language's way of stating a rule the reader may not know, and because a generator that emits an exact comparison has
almost certainly emitted the wrong one.

#### Rounding

**Four operators answer the whole number a floating-point number rounds to.**  Three name a direction and the fourth asks the
processor.

| Written | Rounds | Compare |
|---|---|---|
| `↓x` | to the whole number **below** it, towards −∞ | C's `floor` |
| `↑x` | to the whole number **above** it, towards +∞ | C's `ceil` |
| `↕x` | to the **nearer** of the two, a tie going to the even one | C's `nearbyint` under the default mode |
| `⇕x` | by **whichever way the processor is rounding just now** | C's `nearbyint` |

```
↓ 2.5f64                        ※ 2.0
↑ 2.5f64                        ※ 3.0
↕ 2.5f64                        ※ 2.0 -- a tie goes to the even one
↕ 3.5f64                        ※ 4.0 -- which is why this goes the other way
↓ ⁻2.5f64                       ※ -3.0 -- towards minus infinity, not towards zero
```

**The answer is of the type it was given.**  A rounded `f64` is an `f64`, not an integer.  That is what every machine's
instruction does and it is the honest answer: which integer type the value would fit in is a question about the value and not
about its type, and an answer that turned out not to fit would have to stop the program.  A program that wants an integer says so,
and the conversion is written where it happens.

**A tie goes to the even number**, which is what IEEE 754 calls round-to-nearest and what all three of these machines do.  So
`↕2.5` is 2 and `↕3.5` is 4.  The other rule -- a tie going away from zero, which is C's `round` and what most people are taught
-- has a bias that shows up as soon as many values are rounded and added, which is the thing this operator is usually part of.

**`⇕` reads the processor's rounding mode**, which is state outside the function and outside the language: two of them with the
same value can answer differently in two places.  So **a function that writes `⇕` is marked `@[impure]`** (4508).  Nothing in the
language sets the mode -- a program starts in round-to-nearest and stays there unless something outside it says otherwise -- so
this operator is for a program that is called from one that does.

**They are defined on floating-point values and on nothing else** (4507).  An integer is a whole number already.

**They are listable**, so written before an array they reach every element, and they bind as tightly as every other operator
written before its operand.

Compare: **C**, whose `floor`, `ceil`, `round`, `trunc`, `rint` and `nearbyint` are these plus two more, as six library functions
whose names say nothing about which is which; **C#** and **Python**, whose `round` is banker's rounding as `↕` is, which
surprises people every time; **Rust**, whose `f64::floor`, `ceil` and `round` are methods and whose `round` is ties-away, with no
way to reach the current mode at all; **Go**, the same through `math`; **APL**, whose `⌈` and `⌊` *are* ceiling and floor of a
number, which this language spends on the largest and the smallest instead -- there being no ceiling of an integer to want, and
the arrows saying which way a value moves at least as plainly; and **Zig**, whose `@floor`, `@ceil` and `@round` are builtins and
which likewise offers no way to ask the mode.

What is not here is a truncation towards zero, and it is not an omission: it is `↓` where the value is positive and `↑` where it
is negative, and the operator that would say it in one glyph is the binary form these four do not have yet.

#### Approximate comparisons

Six more operators compare two floating-point values, allowing for the small errors such a computation accumulates.  Each is one
of the exact comparisons with the question asked of a **tolerance** rather than of the values themselves.  They bind exactly as
the exact comparisons do, and like them they do not chain.

| Tolerant | Exact | Reads as | Asks |
|---|---|---|---|
| `≅` | `=` | alike | \|a-b\| ≤ t |
| `≇` | `≠` | not alike | \|a-b\| > t |
| `⪅` | `≤` | less than or alike | a-b ≤ t |
| `⪆` | `≥` | greater than or alike | b-a ≤ t |
| `⪉` | `<` | less than and not alike | b-a > t |
| `⪊` | `>` | greater than and not alike | a-b > t |

None has an ASCII substitute.  There is no sequence of ASCII characters that says "approximate" without being read as something
else, and inventing one would be the kind of spelling a reader has to learn rather than see.

**They are defined on floating-point values and on nothing else** (4218).  Two integers are equal or they are not: there is no
error in one for a tolerance to allow for.

The two sides have one type, as everywhere else.  Where that type is `f32` the difference is computed in `f32` and then widened to
`f64` to be measured, which every `f32` value fits in exactly; the tolerance itself is one number and is not narrowed to meet it.

#### Results

Some operations have no answer for some of their operands.  A division by zero is the first, and the most negative number of a
signed type divided by minus one is the second.  What such an operation answers with is a **result**: a value of the type it would
have answered with, or the fact that there is none.

A result type is written `TYPE?`, where `TYPE` is the **answer type**.  The mark with nothing after it says the error carries
nothing beyond the fact of it.  **`TYPE?ERROR` is a result whose error is a value of its own**: `bool?u8` is a truth value, or
the fact that there is none and a `u8` saying something about why.

**The `⊥` arm of a `match` binds what the error carries**, exactly as the answer's arm binds the answer.  Where the error
carries nothing there is nothing for a name there to stand for, and one written is refused (4521).

```
match a ∣ n:
    bool(yes):  …            ※ the answer
    ⊥(number):  …            ※ what the error carries
```

**A result that carries something is three values rather than two** -- the answer, whether there is one, and what the error
carries.  Three is more than a call answers in registers, so such a result travels through the caller's storage, which is the
path an answer of three parts already takes; nothing about writing one says so.

`÷` and `%` answer with one: `u8 ÷ u8` is a `u8?`, and so is `u8 % u8`.  `∣` answers with one whose error carries something,
where its divisor is not written down.  That is the whole of what a division does about a divisor
it has no answer for -- it does not stop the program, and it does not answer with a number that stands for nothing.

**Two operators read a result.**

`EXPR?` hands back the answer where there is one, and **leaves the function carrying the error** where there is not.  So the
function it is written in must itself answer with a result, and with one whose error type is the same (4221); its *answer* type
need not be the same, since what travels is the error.  Applied to something that is not a result it is refused (4220).  It binds
as tightly as a call does and to whatever stands immediately before it, so `a ÷ b?` is `a ÷ (b?)` and the whole division is
written `(a ÷ b)?`.

`EXPR ?? DEFAULT` hands back the answer where there is one and the value written after it where there is not.  The default is
computed only in that case, which is the rule `and` and `or` follow and for the same reason.  It binds tighter than the
comparisons and looser than everything that computes a number, so `a ÷ b ?? c + d` takes the whole of each side; it is right
associative, so `a ?? b ?? c` is "a, or else b, or else c", which is the only reading of it that is well typed.

**`⊥` is the failure, written out**, and **`⊥ VALUE` is one whose error carries that value.

```
fn nothing() → u8?:          ⊥
fn saying(why: u16) → u8?u16:  ⊥ why
```

What it is a failure *of* is not written with it: it is whatever stands where it stands, exactly as a value of the answer type
written there is the successful result of the same type.  So it needs a place that wants a result -- the answer of a function
that answers one, a variable whose type says one, an argument of a parameter that takes one -- and there is no reading of it
anywhere else (4522).

**Whether it carries a value is the type's to say and not the program's**: a value written where the error carries nothing is
refused (4523), and a `⊥` written without one where the error carries something is refused too (4524), there being no failure of
such a type that carries nothing.

**It takes the whole of what follows it**, the way `return` does, so `⊥ a + b` carries the sum and there is no reading in which
`⊥ a` is the left side of anything.

**A value of the answer type, written where a result is wanted, is the successful result.**  That is how a function that answers
with a result says it succeeded -- `fn share(a: u8, b: u8) → u8?` ending in `(a ÷ b)? + 1u8` answers with the sum -- and there is
no other way to write one.  The reverse is not admitted: a result where a plain value is wanted is refused, since accepting it
would be dropping the error silently.

**The written type reaches what was written.**  Where a result is wanted, something that can only ever *be* an answer -- a
literal, the two sides of an operator, the members of anything written out -- is asked for the answer's type, and the result is
made around it.  So a literal with no suffix may be written wherever a result is wanted, taking the answer's type exactly as it
would anywhere else:

```
let named: u8? = 1                  ※ a definition
changed ← 1                         ※ an assignment
let added: u8? = 1 + 2              ※ both sides of an operator
take(8)                             ※ an argument, where the parameter is 'u8?'
fn give() → u8?: 4                  ※ what a function answers with
let asked: u8? = if c { 5 } else { give() }
let looked: u8? = while §x c:
    break §x 6                      ※ what a loop comes to
```

This is one rule and it holds in every one of those places, including the ones nobody has tried: what is wanted travels down to
what was written, and only something that could itself have been a result -- a call, a name, a loop, an `if` whose arms are
results -- is measured against the result type rather than against its answer.

The answer type may not be `void` (4222): a result of nothing is a truth value written as though it were more, and the language
admits one spelling per meaning.

Compare: Rust's `Result<T, E>` and `?`, which this follows in the operator and not in the constructor -- Rust writes `Ok(x)`,
which it can because `Ok` is an ordinary constructor of an ordinary sum type.  Zig's error unions write `!T` and accept a plain
`T` as the success, which is what is done here.  C++'s `std::expected<T, E>` converts implicitly from `T` likewise.  Go returns a
second value and leaves checking it to discipline, which is the arrangement this one exists to avoid: here the answer cannot be
read without the error having been dealt with, because reading it is what `?` and `??` do.

`??` is C#'s and Swift's spelling for the same idea, applied there to a value that may be absent rather than to one that may have
failed; Rust spells it `unwrap_or`, a method call, which this language has no way to write.

**A result is a value like any other.**  A local holds one, a variable at the top level holds one, a function takes one and
answers with one.  The only constant of a result type a program can write is the successful one, by the rule above: `let kept: mut
u8? = 1u8` is the result holding one.

##### What a result occupies

The compiler decides this, as it does for a product and a sum, and the language says only what follows.  In memory a result is
its answer where an answer goes and one byte beside it saying whether there is one, rounded up to the answer's alignment -- the
same shape a sum has, and not a sum, because the error carries nothing where a variant of a sum carries something.  In registers
it is two: the answer in a register of whatever kind the answer wants, and the truth value in an ordinary one.  Passed to a
function or answered with, that is the two registers every one of the three ABIs already uses for a two-word answer, and a
floating-point answer takes one register of each kind.

**A division whose operands are both written down and have no answer between them is diagnosed** -- 4215 for a zero divisor, 4223
for the one overflowing pair.  Both are warnings, since the expression is well formed and its value is the error; they are
reported because the error is the only thing such a division will ever produce, which is nearly always a mistake.  `@[ignore]`
says it is meant.

#### Logical operators

Six operators and two words work on truth values, and on nothing else.

| Operator | Name | Arity | True when |
|---|---|---|---|
| `∧` | and | binary | both operands are true |
| `∨` | or | binary | at least one operand is true |
| `⊕` | exclusive or | binary | exactly one operand is true |
| `⊼` | nand | binary | not both operands are true |
| `⊽` | nor | binary | neither operand is true |
| `¬` | not | unary | the operand is false |
| `and` | and, short-circuiting | binary | both operands are true |
| `or` | or, short-circuiting | binary | at least one operand is true |

None of them has an ASCII substitute.  The candidates would be `&&`, `||` and `!`, and spelling the logical operators with the
characters the *bitwise* ones use is the one confusion this language is built not to have.

**They join truth values and nothing else.**  `count ∧ ready` does not compile: there is no rule here that a number other than
zero counts as true.  That rule is where a good deal of C's trouble comes from, and where the question really is whether a number
is zero, `≠` asks it.  In the other direction `&` does not take two truth values either, for the reason given above: a `bool`
has no representation the language promises, so there are no bits to and together.

**`and` and `or` do not compute their right operand where the left one settles the answer.**  `∧` and `∨` always compute both.
That is the whole difference, and it is a difference nothing can yet observe: no expression in the language has an effect, can
fail, or can fail to finish, so the two forms give the same answer in the same time.  They are separate now so that programs
written today say which they meant, and so that the day an expression can have an effect is not the day every program has to be
read again.  The words are words rather than glyphs for the same reason: what distinguishes them is *when* they evaluate, which is
something a reader has to be told rather than shown.

**Where they bind.**  The logical operators join whole questions, so they bind looser than everything else: `a < b ∧ c < d` reads as
it looks.  Among themselves they take the order of the three bitwise operators they mirror -- `∧` tighter than `⊕` tighter than
`∨` -- so that one set of habits serves for both.  `and` binds exactly where `∧` does and `or` where `∨` does, since they say the
same thing.  `¬` binds tighter than every operator written between two operands, so `¬ ready ∧ seen` is `(¬ ready) ∧ seen` and
`¬ (a < b)` needs its parentheses -- the rule `!` follows in C, Go and Rust.

**`⊼` and `⊽` do not associate.**  `a ⊼ b ⊼ c` is refused: neither operator is associative, so the two ways of grouping it are
two different questions and a reader cannot tell which was meant.  Parentheses say which.  They share a level of their own, tighter
than `∧`, which is the only thing that has to be said about them since they cannot be written next to one another.

Compare APL, which writes these as `∧` `∨` `⊼` `⊽` and is where the glyphs come from; C, C++, Java, Go and Rust, which have `&&`,
`||` and `!` and no nand or nor at all; and Python and Ada, which use words throughout.  Taking the glyphs for the six that always
compute both operands and words for the two that do not is what makes the difference between them visible in the source rather
than something to be remembered.

#### Ranges

A range is the whole numbers from one end towards another.  It is written with `…` (U+2026 HORIZONTAL ELLIPSIS), with two ends or
with three.

```
0u8…5u8         ※ nought, one, two, three, four
0u8…10u8…2u8    ※ nought, two, four, six, eight
5u8…0u8…⁻1      ※ five, four, three, two, one
```

**`A…B` and `A…B…C` mean what Python's `range` means** with two arguments and with three: the first end is included and the second
is not, and the third says how far each turn moves.  Python is the language this is taken from outright, because the half-open
convention is right for the same reason there: the count of values is the difference between the ends, and two ranges that meet at
a number cover everything between their outer ends exactly once.

**The ends are of one integer type** (4439, 4440).  Nothing is widened on the way in: a language that widened here would decide the
type of every value the loop gives out by a rule the reader has to know.  Only a whole number has a next one, so a floating-point
range is refused -- what one would have to say is how many steps it takes rather than how large each is, which is a different
construct.

**How far each turn moves is written down** (4441), and it is not zero (4442).  Its sign says which way the range runs, and that
decides which comparison ends the loop; a step the compiler cannot read would need both comparisons and a choice between them on
every turn, for a generality nothing has asked for.  The sign is read off the step and what is left is the distance, so a range
that counts down is written the same way over an unsigned type as over a signed one.

**Three ends and no more** (3029).  A range is not a binary operator: three written with one would nest, and `a…b…c` does not mean
`(a…b)…c`.  The ends bind one level in from the range itself, so `1…n-1` reads the way it looks.

**A range stands where a loop takes its values from, and nowhere else yet** (4443).  Giving one a name would make it a value like
any other, with a type and a place in memory, and nothing yet needs that.

Compare: Rust writes `a..b` and `a..=b` and makes a range a value with a type; Python's `range` is a callable object; Go has
none and counts with `for i := 0; i < n; i++`; Zig writes `0..n` only in a `for`.  Writing the ends of a step as a third part
rather than as a separate construct is Python's; Rust needs `.step_by(c)`, which is a method on an iterator and so needs iterators
to be values first.

#### Statements that are expressions

A statement may be an expression on its own.  **Its value must be used**, and there is exactly one place where it is: the last
statement of a body is the body's result.  Anywhere else the value goes nowhere, and a statement that is nothing but an expression
does nothing at all, so it is refused.

```
@[startup]
fn main() → u6:
    1u6                    ※ refused: this value is not used
    count = 1u8            ※ refused, and the same mistake C makes easy
    count & mask           ※ refused
    0u6                    ※ the result of the function
```

It is an error and not a warning, and it applies to a bare literal and a bare name as much as to anything computed.  The reason is
the same one that runs through the rest of this document: the language is generated rather than written, and a generator that
emits a line with no effect has a defect in it -- one that a warning would let through into a program nobody reads the warnings
of.  Where the line is meant to stand as it is, `@[ignore(5005)]` on the statement says so, which is what that attribute is for.

The rule is what makes `=` safe to use for comparison.  `count = 1u8` written as a statement is what someone writes who has spent
years in a language where `=` assigns; here it compares and throws the answer away, and is reported with a note saying that an
assignment is written `←`.

C, C++ and Java allow a discarded expression and warn about some of it, which is where `-Wunused-value` comes from.  Go refuses it
outright, admitting only a call, a receive and a few others as statements; Rust warns by default and has `#[must_use]` for the
cases it cannot warn about in general.  Go's position is the one taken here, and for a stronger version of Go's reason: Go refuses
it because such a line is almost always a mistake, and this refuses it because such a line was emitted by a program.

An expression that does something besides produce a value is a different question, and a call will be the first of them.  There is
no way to write a call yet; when there is, this rule asks the expression whether it has an effect rather than knowing that none
has.

#### Comments

A comment is introduced by `※` (U+203B REFERENCE MARK) and runs to the end of the line.  A comment introduced by `※※` is a
documentation comment: it belongs to the definition that follows it and is made available to the language server and to the
documentation tool.

Considered were `#` (Python, Ruby, shell, Nim, Julia), `//` with `/* */` (C, C++, D, Go, Rust, Zig, Odin), `--` (Haskell, Ada, Lua,
SQL), and `;` (Lisp, Scheme, assemblers).  A glyph was chosen for the same reason the language uses glyphs elsewhere: the language
is generated rather than typed, so ease of entry buys nothing, while every ASCII character spent on a comment is a character no
future feature can have.  `※` is used as a reference mark in Japanese and Chinese text to draw attention to a note, which is what
a comment is.

The block comments of C and D were not adopted.  A generator emits line by line and never needs to wrap a region, and a
non-nesting block comment silently breaks when the region it wraps already contains one.

##### What a documentation comment may say

A documentation comment is prose, and **a line of one that begins with `\` or `@` followed by a word is a command**: it says
something about a particular part of the definition rather than about the whole of it.  The commands are Doxygen's, with the
spelling and the alternatives Doxygen has -- `\param`, `\brief`, `\return`, `\returns`, `\raises`, `\note`, `\warning`,
`\see`, `\pre`, `\post`, `\since`, `\deprecated` and a few more -- and either sigil may be used for any of them, which is
Doxygen's own rule.  `@` begins an attribute list everywhere else in the language and there is no ambiguity, an attribute never
being inside a comment.  What follows a command belongs to it until the next one begins, so a command may run over several lines.
`\param` names the parameter it is about, and Doxygen's `[in]`, `[out]` and `[in,out]` may be written after it and are ignored:
which way a parameter is passed is said in its type here.

**The compiler checks what a command claims.**  A `\param` that names nothing the function takes (4601), one that names the same
parameter twice (4602), a `\return` on a function that answers with nothing (4603), either of those on a definition that is not a
function (4604), and a word that is not a command at all (4600) are warnings, on unless turned off.  They are warnings because what
is wrong is the comment and a comment cannot make a program mean something else; they are on because a comment that describes a
parameter the function has not got misleads exactly the reader who trusted it.  What the compiler does *not* check is whether every
parameter has a `\param`: a comment that says less than it could is not wrong.

**Nothing else in a comment is interpreted.**  There is no markup, no `\code`, no grouping, no way to refer to another definition
and have it become a link.  A language server shows the summary, the parameters as a list, and each remark under its own heading.

Compare: **Doxygen** and **Javadoc**, whose commands these are, and whose markup is not taken; **Rust** and **Go**, whose
documentation comments are prose with no commands at all, so that what a parameter means is said in a sentence and nothing checks
it; **D**, whose Ddoc has named sections rather than commands; **Python**, where the convention is a docstring whose sections are a
matter of which of three styles the project chose.  What is taken here is the spelling a reader already knows and the checking that
a compiler is in a position to do.

#### Unicode glyphs and their substitutes

Where a construct is written with a Unicode glyph, the glyph is canonical: it is what the compiler itself emits, what the
formatter produces, and what the documentation shows.

Whether an ASCII substitute exists is decided per glyph, by two rules: **a substitute may only be a sequence of more than one
character**, and **it must not be ambiguous with anything else**.  A single ASCII character is never a substitute, because it would
then be unavailable to every future feature of the language.  So `->` is accepted for `→`, while `#` is *not*
accepted for `※` and remains free.

The second rule decides `←`, which has no substitute at all: `<-` is two characters, but `x <- y` and `x < -y`
would be told apart only by the spaces around them, and the language makes no other distinction of that kind.  The same two rules
give `≤` and `≥` the substitutes `<=` and `>=`, which are two characters and
are ambiguous with nothing: `<` and `=` cannot stand next to each other in any other way, since a comparison's operands are never
comparisons.  `≠` has none, and that is a decision rather than an omission: `!=`, `/=` and `<>`
all pass both rules, so the question was which of three to bless, and blessing none of them is the answer that leaves the glyph as
the one way to write it.  A substitute exists to rescue a glyph that is hard to enter; `≤` and `≥` have theirs because `<=` and
`>=` are what every keyboard and every reader already produces for them, which is not true of any of the three candidates here.

The six approximate comparisons have none for the first rule's sake as much as the second: there is no sequence of ASCII
characters that says "approximate" without being read as something else, and `~=` or `=~` would be a spelling to learn rather than
one to see.

`⟦` and `⟧` have none: `[[` and `]]` would each be two characters and so pass the first rule, and they fail the second -- an
array of arrays is written `a⟦i⟧⟦j⟧`, whose substituted form would end in four brackets that no reader could group by eye.

`…` has none, and the reason is the second rule rather than the first: `...` is three characters and so passes, but it is three
copies of the character a member access is written with, and telling `a...b` from `a . ..b` would be a question of how far the
lexer can look ahead rather than of what the characters are.  One character is one token, which is what a range is.

`⇧` and `⇩` have none: `^` and `v` are one character each, and `++`, which is what C would reach for, says "one more" of a
number rather than "one along" of a walk -- and is two copies of the character addition has.  The arrows are hollow where
assignment's is solid, which is the distinction between moving a walk and moving a value.

`†` has none, and the first rule is what decides it: every ASCII spelling anyone would reach for -- `!`, `-`, `~` -- is one
character, and a word such as `del` would be a keyword taken out of a program's reach for a line it writes rarely.  The glyph is
what a mark against a name has meant in print for centuries, and a generator has it.

`§` has none, for the second rule's sake: the candidates are `::` and `@@`, and neither says "a name for this place" to anyone
who has not been told.  A label is written by a generator, which has the glyph.

`⁂` has none, and the first rule is what decides it: `**` is the only spelling anyone would reach for, being what Python
writes the same idea's sibling with, and it is two copies of the character multiplication deliberately left free.  Spending it
here would spend it for the one meaning this language has already decided not to give it.

`ⁿ` and the raised digits have none, and it is the first rule that decides it: `^` and `**` are what anyone would reach for,
and `^` is one character and already the exclusive or.  `**` passes both rules and is what Python, Fortran and Ada write, and it
is not taken here for the reason `⁂` was not: two asterisks are two copies of the character multiplication was deliberately left
free of, and spending them here would spend them for a meaning this language has already declined to give.  The raised digits
could have no substitute in any case -- what makes them what they are is that they are raised.

`⌈` and `⌊` have none, and the second rule is what decides it.  `><` and `<>` are two characters and say nothing about
which of the two is which, and `<>` is one of the three spellings of `≠` this language declined to bless; `|^` and `|_` are
the shapes of the glyphs drawn in ASCII, which is a spelling to be told rather than one to see.  The glyphs themselves are what
APL has used for these for fifty years, and a generator has them.

Using an accepted substitute is not an error.  A warning reports it for anyone who wants their sources in canonical form; it is
off by default, since the substitute is accepted usage and not a defect.

Compare Fortress and Agda, which admit glyphs with no ASCII spelling at all, with Haskell and Idris, which accept both
everywhere.  The rule above sits between them and, unlike either, states a reason that decides each case on its own.

### Types

The primitive types are:

| Type | Meaning |
|---|---|
| `u1` … `u32`, `u64` | unsigned integers of the stated width |
| `i2` … `i32`, `i64` | signed integers of the stated width |
| `f32` `f64` | binary floating-point numbers of the stated width |
| `bool` | truth values |
| `void` | the unit type: one value, no information |

The width is always part of the name.  There is no type whose size depends on the target, because that would be exactly the kind
of surprising interpretation of a program that this language does not admit.

**The width is any number of bits and not only the four a machine has registers for.**  `u3` holds 0 through 7, `i5` holds −16
through 15, `u1` holds nothing and one.  Everything that is true of `u8` is true of them: an answer that will not fit stops the
program, a value given to one is checked against its range, and a literal outside it is refused where it is written.

```
let small: u3 = 5u3            ※ 0 through 7
let tiny: i2 = ⁻2i2            ※ -2 through 1
let flag: u1 = 1u1             ※ nothing and one
let wide: u31 = 2000000000u31
```

**There is no `i1`.**  A signed type of one bit holds zero and minus one, which is a pair of values no program wants and a name
every reader would misread.

**What one takes in memory is what holds it**, which is the narrowest machine width that contains it: a byte up to eight bits,
then doubling.  A `u3` is three bits of information in one byte, and several of them side by side are several bytes.  Packing
them would make them bit fields, which are a different thing with a different question about what lies beside them, and the
language does not have those.

**What they are for is saying what a value is.**  A number that is one of eight things is a `u3`, and writing it so makes the
compiler check every arithmetic answer against those eight rather than against two hundred and fifty-six.  The exit status of a
program is the example the language uses itself: it is a `u6`, because 0 through 63 is what a program may exit with.

Compare: **Zig**, whose `u3` and `i5` these are, up to 65535 bits; **LLVM**'s own `iN`, which is where that comes from; **Ada**,
whose range types say the bounds rather than the width and check them the same way; **VHDL** and **Verilog**, where an arbitrary
width is what the whole language is about; and **C**, whose bit fields are the nearest thing and are a property of a *field* in a
structure rather than a type, so that there is no way to write one as a local, a parameter or a return type.

Considered were `int32`/`uint32`/`float64` (Go, C#, D, Java), `s32`/`u32` (the Linux kernel and much embedded code), and the
unsized `int`/`long` of C.  The chosen spelling is that of Rust, Zig, Odin, WebAssembly and LLVM's own IR; it is the shortest
unambiguous form, and it is short enough to repeat in every diagnostic and every dump of the intermediate representation without
making them harder to read.

#### Boolean Values

A `bool` has exactly two values, written `true` and `false`.  Nothing else is one.  A number is not a truth value spelled
differently, so `let flag: bool = 1u8` does not compile, and neither does using a `bool` where a number is wanted; there is no
conversion in either direction that happens without being asked for.

That is narrower than C, where any scalar is a condition and `bool` is an integer type that happens to hold 0 or 1, and narrower
than Python, where every object has a truth value.  It is the rule of Go, Rust, Zig, Odin and Haskell, and it is the one this
language wants for the same reason it refuses to truncate a number: a program that said `1` and meant "true" reads as a program
about a number.

What a `bool` occupies in memory is one byte, which is the compiler's business and not the language's; the language says only that
the type has two values.

A value that its type cannot represent is an error, wherever it appears: a literal, the value a variable is defined with, or a
value the compiler works out for itself.  Nothing is truncated and nothing wraps around.  This is the no-surprises rule applied to
numbers -- a program that stored 300 in a `u8` and read back 44 would not be behaving as it reads, and no rule about which bits
survive would make it so.

#### Characters

**`char` is one Unicode code point.**  A literal of one is written between apostrophes, with the escapes a string takes.

```
let a: char = 'A'
let omega: char = 'ω'
let newline: char = '\n'
let named: char = '\u0041'          ※ the same code point as 'A'
let last: char = 0x10ffff        ※ a number written where a code point is wanted
```

**A value of one is held in thirty-two bits**, which is where every code point fits with room to spare.  That says how much room
one takes and not which numbers are values of it: **the last code point is U+10FFFF** (4493), which is as far as UTF-16's
surrogate pairs reach and which every encoding has had to agree with since.  A number above it, or below zero, is not a code
point; one written down is reported, and one worked out while the program runs stops the program.

**It is not an integer type.**  Adding two of them is not a character and neither is a third of one, so the arithmetic is not
defined on them.  All six comparisons are: what the ordering means is the order Unicode numbered them in, which is a real order and
the one every collation in the world starts from before it does anything else.  That is the difference from an enumeration, whose
order is the order somebody happened to write the values in and which therefore answers equality and nothing more.

**`⎕ord` and `⎕chr` cross between a code point and its number**, and they are not each other's mirror image:

| Written | Takes | Gives | Can it fail |
|---|---|---|---|
| `⎕ord(c)` | `char` | `u32` | no -- every code point is a number |
| `⎕chr(n)` | `u32` | `char` | yes -- not every number is a code point |

Which of the two can fail is why neither is written as an assignment and why nothing converts on its own.  A conversion that may
stop the program is a thing a reader should be able to see, and one that cannot is still a thing a reader should be told is
happening.  The two carry the quad for the reason every compiler-provided name does: so that no program has to give up the names
`ord` and `chr`.

Compare: **C**, whose `char` is an integer type of the width of a byte, which is neither a character nor one code point;
**C++20**'s `char32_t`, which is this and is still an integer type, so `c + 1` compiles; **Rust**'s `char`, which is exactly
this -- a code point, thirty-two bits, not an integer, with `as u32` free and `char::from_u32` checked and answering an
`Option`; **Go**'s `rune`, which is an alias for `int32` and therefore arithmetic; **Python**, whose `chr` and `ord` these are
named after and whose characters are strings of length one; and **Swift**, whose `Character` is a grapheme cluster rather than a
code point, which is the other place the line could have been drawn and is a much larger thing to carry in thirty-two bits.

What is not decided here is what a *string* is, which is the question a grapheme cluster belongs to.

#### Strings

**`str` is text, and its bytes are always well-formed UTF-8.**  A literal of one is written between quotation marks, with the
escapes a character literal takes.

```
let greeting: str = "hi"
let wide: str = "a\tb\u00a3ω"
```

**A value of one is where the bytes are and how many there are** -- two words, the shape an array whose type does not say its
length has.  It owns nothing: the bytes are a literal's, in the image, or an arena's, put there by a join.

**That the bytes are well-formed is an invariant and not a hope.**  There are two ways to make a string: a literal, whose bytes
the compiler encoded itself from what the source held, and a join of two, which puts well-formed bytes after well-formed bytes.
Nothing else makes one, so nothing that reads one has to check it -- which is where nearly all of the cost of walking text
usually goes.

**`foreach` over a string gives its characters, as `char` values, in order.**

```
foreach c: char = greeting:
    ⎕ord(c)                      ※ 104, then 105
```

A turn is not a byte.  The bytes are UTF-8 and the characters are what they encode, so what the loop carries is where in the
bytes it is and a turn moves it on by however many that character took.

**There is no index and no length.**  The *n*-th byte of UTF-8 is not the *n*-th character, so an index whose obvious reading is
wrong is worse than no index; a walk is what a string offers, and it reaches the characters in order because that is the order
they are encoded in.

**`⎕bytes(TEXT)` answers what it is made of**, as a `u8⟦⟧` (4597 for anything that is not text):

```
let raw: u8⟦⟧ = ⎕bytes("a£€")      ※ three characters, six bytes
```

It costs nothing: a string and an array of bytes whose length is not in its type are the same two words, so this says which of the
two is meant and emits no instruction.  Saying it is the point -- `#` answers characters of the one and bytes of the other -- and
it is the compiler's name because nothing else in the language reaches inside a string.  It is how text goes to anything that
takes bytes, a device among them, and being the one thing that answers them is what names the encoding.

Compare: **Rust**'s `as_bytes`, which is this exactly and free for the same reason; **Go**'s `[]byte(s)`, which copies because a
Go string is immutable and a slice is not; **Java**'s `getBytes`, which takes a charset because its strings are not UTF-8; **C**,
where a string *is* its bytes and the question cannot be asked.

**`⎕str(CHAR)` is a string of one character**, and it is the third way a string is made:

```
⎕str('x')                        ※ "x"
⎕str('£')                        ※ two bytes
```

A literal, a join of two, and this.  It is the compiler's name for the reason the other two are not a program's to write either:
that a string's bytes are well-formed UTF-8 is an invariant, and encoding one code point is what keeps it one **by construction and
not by inspection**.  There is deliberately no way in from bytes, which would have to be checked rather than constructed, so
`⎕bytes` is a one-way door.  It takes a character (4610) and not a number: not every number is a code point, and `⎕chr` is what
says so.

It allocates, how many bytes a code point takes not being known until it is looked at -- which, like every allocation, is no
effect: what it makes is new, and a pure function may do it.

**All six comparisons are defined on strings**, and the order is the order the code points are in -- the first character that
differs deciding, and a string that is a prefix of another coming first.

```
"abc" < "abd"                    ※ true
"ab" ≤ "abc"                      ※ true: a prefix comes first
```

**Nothing is decoded to answer one.**  UTF-8 was designed so that comparing the bytes of two strings gives the same answer as
comparing the code points they stand for, so the comparison is a walk of bytes and not a walk of characters.  A string that
carried its characters in four bytes apiece would compare no faster; it is the encoding that makes this cheap.

It is an ordering of code points and not a collation, which is a question about a language and a locale and not one `<` can
answer.  See [Comparisons](#comparisons).

**`⧺` joins two strings**, as it joins two arrays.

```
let both: str = greeting ⧺ " there"
```

What is different is where the answer goes.  How long a join is, is not known while compiling, so room for it is taken from the
arena the compiler provides, `⎕heap`, unless `in` names another.  **Allocating is not an effect**: what a join makes is new and
nothing else can reach it, so a pure function may join strings and answer the result.  Joining a string to an array, or an array to a string, is refused (4489):
a string is not an array of bytes that happens to be spelled differently.

Compare: **Rust**, whose `&str` is this -- bytes, guaranteed UTF-8, no index by character, iterated with `.chars()` -- and whose
`String` is the owning one this does not have yet; **Go**, whose `string` is bytes with no encoding guarantee at all, so `range`
over one decodes and substitutes a replacement character where the bytes are not UTF-8, which is a check on every turn that this
does not need; **Python**, whose strings are sequences of code points with an index, which costs either four bytes a character or
three representations; **C**, where a string is a pointer and a convention; and **Swift**, whose `String` is a sequence of
grapheme clusters, which is what a reader means by "character" and is a much larger thing -- and is the question `char` already
left open.

#### Product and sum types

A program defines a type by writing

```
type NAME = NAME : TYPE ; NAME : TYPE ; ...
type NAME = NAME : TYPE | NAME : TYPE | ...
```

**The separator says which kind of type it is.**  `;` makes a **product**, which holds all of its parts at once -- a record.  `|`
makes a **sum**, which holds exactly one of them -- a tagged union.  Those are the two characters that already mean "and also" and
"or else" everywhere else in the language, so which one a definition uses is the whole of what a reader has to see.  A definition
that uses both is refused (3017).

Each part is written `NAME : TYPE`, the same shape a parameter and a variable are written with.  A part with no name would be one
that could only be reached by counting, and the compiler is allowed to reorder a product's fields, so counting is exactly what
must not be possible.  Two parts of one type may not share a name (4406).

**One pair with no separator to go by is a product.**  A record of one field is a useful thing -- a name for a number that is not
that number -- and a choice between one alternative is not.

**The sequence may be written over several lines**, in either of the two ways the language already breaks a line:

```
type Span = { first : u32 ; last : u32 }

type Line =
    from : Point ;
    to : Point
```

Braces work because the lexer gives out no ends of lines inside them.  Indenting the parts under the definition works because the
separator ends the line it is written on and the end of line is then skipped.  Neither notation changes what separates the pairs:
a line may be broken after a separator and not before one, which is what keeps "this pair was the last" decidable where it is
written rather than one line further on.

**No two alternatives of a sum may have the same type** (4410).  An arm of a `match` names the type of the alternative it takes,
so two alternatives of one type would be two an arm could not choose between.  The rule is a sum's alone: a product holds all of
its fields at once and reaches each by name, so two fields of one type are no trouble there.

**One variant of a sum may be `void`**, and says the value is this alternative and carries nothing further:

```
type Choice = nothing : void | small : u8 | large : u64
```

Only one, by the rule above -- which means an enumeration, every one of whose alternatives carries nothing, cannot be written
today.  The to-do list carries that question.

A **field** of a product may not be `void` (4409): a product holds all of its fields, so one that carries no information leaves the
product meaning exactly what it would have meant without it.

**A type definition may name a type defined below it**, and may name one another module exports (`m.Point`), for which the
definition there needs `@[export]` as a function or a variable does.  What it may not do is reach itself (4408), through its own
fields or through a chain of other definitions -- **unless a reference stands somewhere on the way round**.  A value of such a
type would otherwise have to hold a value of itself and there would be no size that fitted; what a reference occupies is the same
whatever it names, so a definition may reach itself through one and a value of it is as big as its other fields and one address.

```
type Node = value : u8 ; next : &mut Node       ※ a list
type Tree = leaf : u8 ; left : &Tree ; right : &Tree
type Pair = n : u8 ; other : &Other             ※ and two that reach each other
type Other = m : u16 ; back : &Pair
```

The rule is about the way round and not about the length of it: a chain with a reference anywhere on it is finite, and one with
none on it is not, however many definitions it passes through.  So `type A = b : B` beside `type B = a : A` is still refused.

That is the list, the tree and the graph -- every shape whose definition says "and another one of me" -- and it is what a
reference in a product is for.

**A defined type is nominal.**  Two definitions with the same parts are two types, because a definition is what says what a value
*is*, and two things that happen to be laid out alike are not one thing.  Two files each defining `Point` define two types, even
where the parts agree.

Compare: Rust's `struct` and `enum`, which are two keywords for what is here one construct with two separators; Go's `struct`
and its absence of a sum type; Zig's `struct` and `union(enum)`; Haskell's single `data` declaration, where `|` separates the
alternatives exactly as it does here and a record's fields are named in braces; ML's `type ... = ... | ...`.  The shape here is
Haskell's `|` and a `;` beside it, which is as close to one construct for both as the two ideas allow: a product and a sum differ
in one character, which is what they differ in.

Considered and rejected: `type Point = (x: i32, y: i32)`, which reuses the parenthesis shape of a parameter list but gives no
obvious spelling for a sum; separate keywords after Rust, which is two constructs for two things that are dual; and an untagged
`type Result = i32 | Error`, naming only the types, which leaves a sum's parts unnamed and so unreachable.

**A value of either is written the same way**, which is what one construct for the two means where a value is made rather than
where a type is declared:

```
let p: Point = Point(.x ← 1f64, .y ← 2f64)          ※ every field of a product
let n: Number = Number(.whole ← 5i64)          ※ one part of a sum
let c: Choice = Choice(.nothing ← true)        ※ a part that carries nothing
```

A product writes every field and each once, there being no default to fall back on; a **sum writes exactly one part** (4800), which
is what a sum holds.  The part named is one the definition has (4801), and a part whose type is `void` is written `.name ← true`
(4802): something has to stand where a value is written, and what it says is that this is the part.

**What a value of a sum is, is where its bytes are.**  A product travels as the values it is made of -- its fields, in registers --
and a sum cannot: it is its largest part with a tag after it, which is bytes and not a value.  So a value of one is room holding
those bytes, and what is passed to a function, matched on, or answered with is where that room is.  A function answering with one
is handed the room by its caller, which is the convention an answer of more values than the registers hold already takes; nothing
about that is written in the program.

What is not there yet: a **record or a tuple holding a sum** (9902), which would have to be held in memory itself for the same
reason, and a **sum at the top level**, which waits on the same thing a collection there waited on.  Reading a field of a product
and asking which part a sum holds are both there: the first is `p.x`, the second is `match`.

##### What a value of one occupies

The compiler decides this, and the language says only what follows from it.  A product is its fields, each starting where its own
alignment allows; the compiler is free to choose their order, and the order it chooses today is the order they were declared.  A
sum is its largest variant with a one-byte tag after it, the tag last rather than first because a tag ahead of a payload wanting
eight bytes is seven bytes of padding and behind it is often none.  The whole of either is rounded up to its own alignment, which
is the largest of its parts'.

#### Units

**A number may say what it counts**, written after its type:

```
let d: u32 ¤meter = 100
let t: u32 ¤second = 5
```

`¤meter` and `¤second` are part of the types, so `d` and `t` are of two types and neither stands where the other is wanted.
The unit is the only thing that differs: the bits are the same bits, the register is the same register, and **except in a
signature nothing about a unit reaches the generated code**.

**What is added must be of one kind; what is multiplied need not be.**  A sum of a length and a time is nothing, so `+`, `-` and
every comparison ask that both sides carry the same unit -- which they do by being the same type, so no rule of their own was
needed.  A product and a quotient work a new unit out instead:

```
let v: u32 ¤meter÷second = (d ÷ t) ?? 0
let back: u32 ¤meter = v × t          ※ the seconds cancel
```

A unit is kept as base units and exponents and not as a name, so `¤meter÷second` times `¤second` really is `¤meter`: the
exponents are added for a product and subtracted for a quotient, and what cancels, cancels.

**A number written down counts nothing.**  `d × 2` is twice whatever `d` is and not a length times a length, so a literal beside
a product or a quotient takes no unit from what stands next to it -- the one place a literal does not take everything from its
context.

**How a unit is written.**  `¤` and then base units, read left to right: `×` puts the next one above the line, `÷` below it, and a
raised number after one is what it is raised to.  `¤gram×meter÷second²` is what a newton is.  A name between quotation marks is a
unit whose name is not an identifier, which is what lets a program count things the language has never heard of.

**The units the language provides** are the seven SI base units -- `second`, `meter`, `gram`, `ampere`, `kelvin`, `mole`,
`candela` -- and two the compiler counts with:

- **`¤size` is what `#` answers with.**  How many there are is a quantity like any other, and saying so is what stops a count of
  things being added to a count of seconds.
- **`¤idx` is what an index must be** (4541).  Which element is wanted is not a length and not a count of apples.  A literal is
  whatever it is asked to be and so is never wrong there -- `v⟦2⟧` needs nothing written -- but anything else says what it counts.

```
let at: u64 ¤idx = 2
v⟦at⟧                                ※ which one
v⟦#v - 1⟧                             ※ a count, where `unit ¤size → ¤idx` says it may stand
```

**Any other unit is introduced before it is used** (4539), which is what keeps a unit mistyped in one place from quietly becoming
a unit of its own -- the one mistake a language with no such rule cannot tell from a new kind of quantity.  Three forms:

```
unit apples                                      ※ measured in nothing but itself
unit mph = 1609344 ÷ 3600000 × meter ÷ second    ※ and in terms of others
unit ¤size → ¤idx                                ※ and where it may stand
```

The first introduces a base unit of the program's own.  The second says what one of them is in terms of others -- numbers and
names in one product, read left to right -- which records that the two measure the same thing; nothing yet converts between them,
and two units with the same base units and different scales are two units.  The third says a value written in one unit may stand
where another is wanted, and **only the way round it is written**: a count of things may be told to stand where an index is
wanted without an index being allowed to stand for a count.  Where a definition stands is how far it reaches -- at the top level,
the whole file; inside a body, that body.

**A unit belongs to a number** (4540).  A truth value counts nothing and a code point counts nothing; an array is several of
something and the unit belongs to what it holds, which is where it is written, `u8 ¤meter⟦4⟧` being four lengths.

**Nothing crosses from one unit to another on its own.**  `⎕drop(x)` takes a unit off and `⎕unit(x, ⌜UNIT⌝)` puts one on, and a
number going from one unit to another is written as both:

```
let far: u32 ¤mph = ⎕unit(⎕drop(v), ⌜mph⌝)
```

That is the point rather than the price: the step that would otherwise have been silent is the step that is said out loud.
`⎕drop` of something with no unit is refused (4543) and `⎕unit` on something that has one is refused (4544), so neither can be
written where it does nothing or where it hides a crossing.  The unit is written between the lifting marks (4546) because a unit
is not a value -- there is nothing at all for one to be while the program runs.

Compare: **F#**, whose units of measure are this feature and the one full implementation in a mainstream language -- `[<Measure>]`
declares a unit, the exponents cancel the same way, and the units are erased before code is generated, which is the same bargain
struck here.  What F# adds is inference of unit-polymorphic functions, which this language does not have because it does not
infer.  **Ada** checks dimensions with aspects on a numeric type and applies a conversion factor, which is what the `unit NAME =`
form here records and deliberately does not yet apply.  **Boost.Units**, **Haskell's `units`** and **Rust's `uom`** do it with
the type system rather than in the language, at a cost in error messages this language has no reason to pay.  **Swift** and
**Java** have libraries that carry the unit as a value at run time, which is the other design entirely: it costs a word and a
check per quantity, and catches at run time what this catches while compiling.  **C**, **Go** and **Zig** have nothing, and the
Mars Climate Orbiter is the usual reason to want it.

#### References

**`&T` is a name for a place someone else holds**, and `&mut T` one the place may be written through.  `&x` makes one out of a
place, and `x⌖` reads what is at the place one names.

```
let n: mut i8 = 1i8
let r: &mut i8 = &mut n
r⌖ ← 3i8                     ※ writes n
let seen: i8 = r⌖ + 1i8      ※ reads n, which is 3
```

**Whether the place may be written is part of the type**, because the one who wrote the reference and the one who reads it both
reach that place: what a caller may do to its own variable is not a thing the callee can be left to guess.  That is exactly where
it differs from the `mut` a variable or a parameter carries, which says only that the *name* may be bound to something else and is
no part of any type.  Both may be written, and each half is then decided by its own word:

```
let moving: mut &mut i8 = &mut n
moving⌖ ← 20i8               ※ writes the place
moving ← &mut m               ※ binds the name to another place
```

`mut` before the type says the name may move; `mut` inside the reference says the place may be written.  A `&T` refuses the first
line (4535) and a name without `mut` refuses the second.

**`&` is written before a place** (4533): a name, a variable at the top level, an element of an array.  A value the program worked
out is in no particular place -- it may be in a register and it may be nowhere at all -- so there is nothing for a reference to
name.  A reference that allows writing further needs a place the program could have written where it stands (4537), since it is a
way to write it.

**Whether it may be written is part of the type, so the context says it.**  `&x` where a `&mut T` is wanted is a reference that
may write, exactly as an integer literal where a `u8` is wanted is a `u8` -- what a type is written down for is to say what goes
in it.  `mut` on the right is written where nothing says: a name whose type is read off its value, the wildcard, an argument of a
call being walked.

```
let r: &mut i8 = &n              ※ may write: the type said so
bump(&n)                         ※ may write: the parameter said so
let m := &mut n                  ※ may write: nothing else says, so this does
let s: &i8 = &n                  ※ may not; `&mut n` here would be refused
```

Writing `mut` where the context already says it is allowed and says the same thing, which is what makes the call above readable
either way; writing it where the place may not be written is refused (4537) whichever of the two said it.

**`⌖` is written after what it reads through**, so reaching further into what it answers reads left to right without brackets:
`rows⌖⟦2⟧` is an element of what `rows` names.  That is what Pascal, Modula, Ada and Odin put a mark after a pointer for, and it
is why the mark is not a prefix as C's is.  Written after anything that is not a reference there is no place to read (4534).

**`⎕syscall(NUMBER, ARG...)` asks the kernel for something**, and answers what the kernel put in the register it answers
in: an `i64`, negative where it refused, which is the kernel's convention and not one invented here.

```
let wrote: i64 = ⎕syscall(1i64, 1i64, &message⟦0⟧, 6i64)   ※ write(1, message, 6) on x86-64
```

**The number is named rather than written out**: `⎕sc@write` is 1 on x86-64 and 64 on the other two, so one source says
`⎕sc@write` and means whichever it is being built for.  The compiler holds that table, because the compiler is what knows
which architecture it is; a name it has no number for is refused (4574), and one that some architecture has and this one does not
is refused differently (4575), so a mistyped call and a call that is simply not here read apart.

**A name the compiler provides may carry a key**, written after `@`, and only such a name may: `a@b` is still two things with
nothing between them, and `@[` still begins an attribute list.  The key is looked up by the compiler, so `⎕sc@write` is one
name and not a name and an operator.

**Everything given is a machine word** (4572): a whole number of any width, widened by its own signedness, or a reference, read
as the address it is.  Nothing else fits in a register the kernel reads.  At most six arguments follow the number (4573), there
being six such registers on every architecture this compiler generates for.  **Asking the kernel changes what outlives the
call**, so a function that does it says `impure`.

It is how the standard library will reach anything outside the process, and it is the only way: there is no C library underneath
and nothing else to call.

**`⎕acquire(REF)` reads a place and `⎕release(REF, VALUE)` writes one**, each saying what a *second* observer may see.  An
acquiring read is one nothing written after it may be seen to have happened before; a releasing write is one nothing written
before it may be seen to have happened after.

```
let n: u32 = ⎕acquire(&ring.tail)     ※ nothing written after this is seen before it
⎕release(&mut ring.head, n + 1u32)    ※ nothing written before this is seen after it
```

They are the compiler's names because what they say is about the machine and not about the value: nothing a program could write
for itself makes a read acquire.  **It is said at the access and not by the type of the place**, so the same place read the
ordinary way elsewhere is an ordinary read -- which is what driving a ring wants, an index being published with a release and read
back plainly by the same program a moment later.  A type that carried the promise, as Java's `volatile` does, would make every
access pay for the one that needed it.

What each is given is a reference (4580) to **one** value: a record, a tuple or a result travels as the several values it is made
of, so reading or writing one is that many accesses and which of them the ordering belonged to would have no answer (4582).
`⎕release` writes, so its reference is a `&mut` (4581).  A function using either says `impure`, since one notices what something
else did and the other lets something else notice.

Compare: **C11** and **Rust**, where the ordering is a parameter of the operation and not a fence of its own, which this follows;
**Java**, whose `volatile` is the property-of-the-place answer; **Go**, which keeps the whole question out of the language;
**Linux's own `smp_load_acquire`**, which is this pair by another name.

**`⎕at(ADDRESS, ⌜TYPE⌝)` is a place at an address the program worked out**, and `⎕span(ADDRESS, COUNT, ⌜TYPE⌝)` is a run of
them:

```
let head: &mut u32 = ⎕at(mapped + 8i64, ⌜&mut u32⌝)
let bytes: u8⟦⟧ = ⎕span(mapped, 4096u64, ⌜u8⟦⟧⌝)
```

Nothing else in the language makes a place out of a number, which is why these are the compiler's names: **what is there is what the
program says is there**, and there is nothing to check it against.  They are how a program reaches memory something else gave it --
what `mmap` answered, what a device said -- and being the only way is what keeps that door in one place rather than in every type
that wanted one.

**The lifted type is what the answer is**, not what is pointed at, so the words on the definition and the words in the brackets are
the same words.  `⎕at` answers a reference (4585); `⎕span` answers an array whose length is not in its type (4586), which is already a
place and a count and so represents nothing new -- how many there are is the argument, and a type that also said would be a second
statement of one thing.  How many is a whole number (4587).

**What may be given as the address** is a machine word -- which is what a request to the kernel answers with, signed or unsigned --
or a reference, which is a place being read as a place of another type.  A narrower number is refused (4584): half of an address is
not a place, and widening one would be the compiler deciding which half.  Neither name makes a function impure: making a place is
not reaching through it.

Compare: **C**, where a cast of an integer to a pointer says this and may be written anywhere; **Rust**, where it is `unsafe` and
the word marks a region rather than the operation; **Zig**, whose `@ptrFromInt` is this exactly, one name for the one thing that
cannot be checked; **Go**, whose `unsafe.Pointer` is the same idea behind a package a program has to name.

**`⎕address(REF)` is the way back**: a place as the number it is.

```
let at: u64 = ⎕address(&m⟦0⟧)
```

It is the compiler's name for the same reason its inverse is, and the two are the only ways through that door.  What a program has
it for is handing an address to something outside itself: a request to the kernel takes one and a ring's submission entry holds one
in a field.  **What has an address is a place** and a reference is how this language names one, so a value the program worked out
has none (4592) -- taking a reference of the name it is bound to is what gives it one.  It makes no function impure: asking where
something is changes nothing, and what is done through the address says so itself.

Compare: **Rust**, whose `as usize` on a raw pointer is this and is *not* `unsafe` -- making the number is safe and using it is
not, which is the line drawn here too; **Zig**, whose `@intFromPtr` is this exactly; **C**, where `&x` and a cast are the same
split.

**A value of a record gives a value for each of its fields**: `Point(.x ← 3u32, .y ← 4u32)`.  It is written the way a
call names a parameter, which is the same idea asked of a field -- the mark says the name is the thing's and not a variable's --
and it is the spelling C, Odin and Zig give a structure's initializer.

**Every field is given and each once** (4577, 4578).  There is no default to fall back on and nothing that would be right to
leave behind: a field left out would be storage holding whatever was there, which is what this language does not have.  C fills
such a field with zero, which is the half of the designated initializer not taken here -- zero is a value like any other and a
program that meant it can write it.  A name that is not a field is refused (4576), and a value given without naming one is
refused as well (4579): a record's fields have names and a tuple is the shape for several values that go by position.

**A field is read with the same mark**: `p.x`.  That mark does three things -- a module's name, an enumeration's value, and a
record's field -- told apart by what stands on its left, which is what Go, Rust and Zig all do.

**`@[device]` says a value of the type is permission to do input or output.**  A function handed one may read or write it without
being marked `@[impure]`; see [the purity rules](#function-definition).  `std`'s three descriptors are marked so, and so is the
handle a request in flight goes by.

**`@[unique]` says there is one of a value and it is never copied** (4400):

```
@[unique]
type Handle = fd : i32

let h: Handle = Handle(.fd ← 1i32)
let n: i32 = h.fd                ※ a field: not a copy
peek(&h)                         ※ a reference: not a copy
let other: Handle = h            ※ refused
```

What it is for is a value that stands for something outside the program.  Two of those would be two names for one thing, which is
what the rule about a second `&mut` already refuses for a reference and what nothing refused for the value itself.

**Reading the name as a value is what a copy is**, so that is what is refused: binding it to a second name, handing it to a call by
value, answering with it, putting it inside something else.  **A field of it and a reference to it are not copies** -- both are read
from where the value is -- so a program reaches into such a value freely and passes it along by taking `&` or `&mut`.

**A record travels as its fields.**  To everything below the checker it is what a tuple is: several values going together,
placed by a convention the same way, so a record handed to a call or answered with needs no rule of its own.  What differs
between the two is that one of them named its parts.

**A record *name* stands for storage of its own**, which is what a name a reference is taken of already does.  A field is read at
an offset from it, so `&p.x` is an address like any other and a field behind a reference is read without reading the whole record
first.  Binding a record to a name writes the fields into that storage: **a record is a value and not a place**, so two names are
two records and writing through one leaves the other as it was -- which is what C, Rust, Go and Zig all do.

**A field may be a record**, and a value written out is written where it will live rather than made and then copied:

```
type Line = from : Point ; to : Point

let l: mut Line = Line(.from ← Point(.x ← 1u32, .y ← 2u32),
                       .to ← Point(.x ← 3u32, .y ← 4u32))
bump(&l.to.x)                    ※ a reference to a field of a field
```

Reading one follows the offsets -- `l.to.x` is where `l` is, plus where `to` lies, plus where `x` lies -- and only the field is
read.  Such a record is handed to a call and answered with like any other, because **what travels is the values a record is made
of and a field that is itself a record is not one of them**: a `Line` goes as four `u32`s and not as two `Point`s.  Which of them
a field is, and where it lies, is the compiler's to remember -- nothing in the language says it.

**A variable at the top level may hold one**, and its value is bytes in the image: every field is given and each once, padding
between them is zeroes, and it is a place for as long as the program is -- so a field of it is read at an offset, written at one,
and `&moving.to.x` is an address like any other.  That is C's static initializer and Rust's `const` one; Go, whose package-level
variable may be initialized by generated code, is the other answer and is not taken.

**A record of one field is a record**, made, passed, answered with and referred into like any other.  Nothing about a record asks
how many fields it has, so `type Handle = fd : i32` is what says which of the things that may be done with a number may be done
with this one -- which is what C's structure of one member, Rust's newtype and Haskell's `newtype` are each for.

**A reference that promises more stands where less is wanted.**  `&mut T` promises everything `&T` does and adds writing, and
`&static T` promises everything `&T` does and adds the time, so each stands where the weaker one is asked for -- in a call, a
definition, a return.  The bits are the same bits, so nothing is emitted for it.  Both go one way only: a place nothing may write
is no use where writing is, and a call's storage is no use where the program's is wanted.

**A reference names a place holding one value** (4536): a number, a truth value, a code point, a value of an enumeration, a
record, a choice between records, and a reference itself.  An array, a list, a string, a set, a dictionary, a tuple and a result are each already several
values or already a place, so a reference to one would be a second way of writing what a value of it already is.  A reference
takes the whole of what follows it, so `&u8⟦4⟧` is a reference to an array of four -- which is refused -- and never an array of
four references.

**A reference leaving the call says how long what it names lives.**  A reference is only worth having while what it names is
still there, and a caller cannot see into the function to work that out.  So a function handing one back says which of exactly two
lifetimes it is, and there is a way of writing each:

```
fn counter() → &mut static u8:      ※ as long as the program
    &mut total

fn first(v: &⧖a u8) → &⧖a u8:    ※ as long as what v named
    v
```

**Both stand in one slot**, after `mut` and before what is pointed at, since they answer one question: `&mut static u8` and
`&mut ⧖x u8`.  At most one of them is ever written, and writing neither is refused (4562) -- there is no default, because the
two answers differ and either guess would make a promise the program did not.

**A lifetime name means "as long as whatever else in this signature carries it".**  Written on one parameter and on the answer it
says what that parameter's own name could have said; written on several it says they all live as long as each other, and the
answer lives as long as the shortest of them.  That second case is what no shorter form can say -- when the answer may come from
either of two, neither of them alone is true of it:

```
fn either(a: bool, b: &mut ⧖x u32, c: &mut ⧖x u32) → &mut ⧖x u32:
    if a:
        b
    else:
        c
```

U+29D6 WHITE HOURGLASS is the one glyph in Unicode that means "how long"; it is a single character, so it has no ASCII substitute
and claims nothing.  **A lifetime is declared by being used**, exactly as a type parameter is: there is no list at the head of the
signature to write and none to keep in step, and a name the answer carries that stands in no parameter's type is refused (4567).
A name on a parameter and not on the answer is allowed and says nothing, what makes a promise being the answer carrying it.

Rust writes it as `fn either<'a>(b: &'a mut u32, c: &'a mut u32) -> &'a mut u32`: the same idea, with the name declared at the
head and marked by a leading tick.  Neither is available here.  A leading tick begins a character literal, and written after a
name the tick is already the type-parameter mark, so `a'` would be one spelling with two meanings; and declaring at the head is
what the generics decision turned down, for the reason it turned it down -- a list to write and to keep in step with the thing it
describes.

**What comes back really has to carry the name** (4561).  The compiler walks
the answer back the way provenance is walked everywhere else -- reading the parameter out of its storage, offsetting it, reading
the same bits as another type, through a call that made the same promise about its own parameter, and through what an `if` or a
loop came to, where every branch has to reach one of them -- and refuses a reference reached by none of those.  A reference that
lasts as long as the program keeps any promise, so answering with a variable at the top level is allowed wherever a name was
written.

**The caller works out the rest.**  A function promises no more than its parameters' lifetime, so the same call read two ways
gives two answers: `first(&total)` for a variable at the top level answers with a reference that lasts as long as the program, and
`first(&n)` for a local does not.  Where several parameters carry one name, the answer lasts that long only where *every* one of
them did, that being the only promise that holds whichever one the body picked.  One signature, decided where both the argument and the answer are in view.  A reference that
lasts as long as the program stands wherever a shorter-lived one is wanted, the other way round being refused (4203).  Which it
came to is written to the report log (`lifetime`) every time, since neither the signature nor the call says it -- and where a
name stands on several parameters the log says which of the arguments the answer took its lifetime from, which is a fact about
that one call.

**A variable at the top level holds a reference only where it says `static`** (4532), which is that rule asked at the other place
a value escapes to.  A reference inside something else -- a tuple, a product, a collection -- may not be answered with at all
(4531): `static` belongs to one reference and a tuple may hold several, and a name written on the whole answer would say nothing
about which of them it was about.

Rust softens all of this with elision, which lets the common case go unwritten; nothing here does, because a missing lifetime
there is still a lifetime being inferred and here the two answers mean different things to a caller.  C++ has no rule at all and
a dangling reference is a program nobody notices is wrong; Go and Java move what escapes to the heap instead, which needs a
collector.  Where a reference does not leave the call none of this is written, which is most of what references are for:

```
fn main() → u6:
    let n: mut i8 = 1i8
    bump(&mut n)                 ※ the call says a place is handed over
    …

@[impure]
fn bump(at: &mut i8):
    at⌖ ← at⌖ + 1i8
```

**A name may hold a reference only to a place that lasts as long as it does** (4570).  A name bound further out lasts longer
than a place made further in, so giving it a reference to one would leave it naming a variable the program can no longer name:

```
let r: mut &mut u32 = &v1
if b:
    let v2: mut u32 = 1u32
    r ← &v2                   ※ refused: r outlives v2
r⌖                          ※ what this would read is gone
```

Outward is always allowed, inward never.  It is the rule a function answering with a reference follows, asked at the third place
a reference can escape to -- not out of the call and not into a variable at the top level, but outward within one body -- and it
is asked of a place that holds a reference as much as of a name: `rr⌖ ← &v2` puts one where `rr` names, and how long
that lasts is how long what names it does.

**It is asked of everything that can reach a place**, not only of a reference.  A tuple holding one lasts as long as the
shortest-lived thing in it, and a lambda lasts as long as the shortest-lived name it brought in *by reference* -- what it brought
in by value is a copy and ties it to nothing.

```
let f: mut fn() → u32 = λ [&v1] → u32 { v1 }
if b:
    let v2: mut u32 = 9u32
    f ← λ [&v2] → u32 { v2 }   ※ refused: f outlives v2
```

**A place the call did not make takes only a lasting reference** (4571).  How long such a place lasts is the caller's business
and nothing in the signature says it, so a reference of this call's own put there would outlive the call that made it -- and
neither end can see the mistake: the body cannot see how long the place lasts and the caller cannot see what the body did with it.

```
@[impure]
fn keep(slot: &mut &mut u32, r: &mut u32):
    slot⌖ ← r                        ※ refused (4571)
```

Rust writes the rule instead of refusing it -- `fn keep<'a>(slot: &mut &'a mut u32, r: &'a mut u32)` says the two live as long as
each other, and the *call* is then what is refused.  Saying that needs a signature able to relate two parameters, which `⧖a`
cannot yet do: it relates a parameter to the answer.  Until it can, the write is refused where it is written, which costs the
programs that would have been right and keeps out the ones that would not.

Rust asks the same question and answers it the same way.  C and Odin do not ask it, and a pointer to a block-scoped variable
outliving its block is the oldest mistake there is.

**A `&mut` is the only reference to its place while it lives**, and a `&` may share with other `&`s (4563).  That is what makes
a reference worth having rather than merely convenient: whoever holds a `&mut` knows nothing else can change the place under it,
and whoever holds a `&` knows nothing can change it at all.  Two references where either may write would give up both promises at
once, so the second one is refused and a note points at the first.

```
let n: mut u8 = 1u8
let r: &mut u8 = &mut n
let second: &mut u8 = &mut n   ※ refused: r is still out
```

**The name itself counts as a way to the place.**  While a `&mut` is out, the name may not be read (4565) -- reading through the
reference says the same thing and says it once -- and while any reference is out, the name may not be written (4566), that being
exactly the change whoever holds it was promised would not happen.  A `&` lends nothing away that a read would disturb, so a name
lent that way stays readable.

**A reference lives until the name that kept it does, and no longer.**  One nothing bound -- handed straight to a call -- is gone
when the call is, if what the call answers cannot hold a reference; otherwise when the statement is.  So the same place may be lent
again on the next line, and again in the same expression:

```
bump(&mut p)                   ※ lent for this call
bump(&mut p) + bump(&mut p)    ※ and twice more, one after the other
(⍕a in pool) ⧺ (⍕b in pool)    ※ an arena lent to two calls in turn
```

That is the lexical rule, and it is Rust's before non-lexical lifetimes, with the one refinement that an argument's borrow ends
with the call where the answer has nowhere to keep it.  Rust now ends a borrow at its last use, which reads more
programs at the price of a liveness analysis to say where a reference stops existing; here a generator that wants the place back
opens a scope or writes the statement, and the rule can be read off the source with nothing computed.  **An element is part of its
array**, so lending one lends the array: two elements are two places, but telling one index from another is arithmetic, and a
promise that depends on arithmetic is no promise.  **A variable at the top level is not tracked** -- it is reachable from every
function and no one of them can see what the others do -- so the rule is about names a body binds.

**The rule is about taking a reference, not about copying one.**  A reference is a value like any other, so binding a second
name to one already in hand -- `let other: &mut u8 = r` -- makes a second way to the place that no `&` was written for and that
nothing here counts.  What is checked is every place a reference is *made*, which is where a generator would make a mistake; what
is not checked is a program that hands one it already has to two names.  Rust closes that with moves and reborrows, which need an
ownership rule this language does not have.

C++ has no such rule and two references to one object is the ordinary case, which is why `std::vector` invalidating its own
iterators is a hazard rather than an error.  Swift enforces the same exclusivity for `inout` and does it partly at run time.  ML
and Haskell reach the same place by having nothing to write through.

**A function that writes through a reference is impure** unless the place is storage the call itself made.  That is not a rule of
its own: it is the rule about writing memory the function did not make, asked of a reference, and the compiler answers it by
looking at where the address came from.  A reference handed in by a caller names the caller's storage, so writing through it is a
change that outlives the call (4479).

Compare: **C++**'s `T&`, which is made by writing the place and read by writing the name -- no mark at either end -- and can never
be pointed elsewhere; a reader of a C++ call cannot see that the callee will write the variable, which is the cost of the marks
this language keeps.  **Rust**'s `&T` and `&mut T`, whose spelling this is, with `&x` at the call and `*r` to read through; what
Rust has beside it is lifetimes and the borrow rules, which say how long what is named lives and that a `&mut` is the only
reference to it at that moment.  This language has neither yet, and closes the escapes instead -- a blunter rule that refuses some
good programs and no dangling ones.  **Go**'s `*T` with `&x` and `*p`, garbage-collected, so a pointer to a local is simply
allowed and the local outlives the call.  **Odin**'s `^T` with `&x` and `p^`, where the mark after the pointer is the one this
follows.  **Zig**'s `*T`, with `&x` and `p.*`.  **C**'s `T *`, where nothing says whether a pointer may be null, whether it names
one value or many, or how long what it names lives; the three things `&T` here answers by construction are the three C leaves to
a comment.

**A reference is never nothing.**  There is no null reference and no way to write one, because every one is made from a place that
exists.  That is Rust's arrangement and C++'s intent, and it is what lets `r⌖` need no check.

#### Enumerations

An enumeration is a fixed set of named values and nothing else.

```
enum Colour { red ; green ; blue }

enum Wide : u32:
    first ;
    second
```

`enum NAME` says what the type is called; the names of its values follow, in braces or indented under a colon, separated by `;`
either way (3021).  A definition lists at least one (3022), and no two of its names are alike (4418).

**A value may say which number it is stored as**, with `NAME = NUMBER`, or take the number of an earlier value of the same
enumeration, with `NAME = OTHER` (3023).  Two values written with the same number outright are refused (4423): two a program
cannot tell apart, written as though they were two things, is a mistake and not a shorthand.  Taking another's *name* says
outright that the two are one thing and is how an alias is written -- `retry = again` gives `retry` the number `again` has, and
the two are then one value under two names.  A name taken must be a value written earlier (4424).

**What a value says nowhere the compiler chooses**: one past the last, starting at zero -- and, for a flag enumeration, the
smallest power of two above every number already used, starting at one, so that each value is a bit of its own however the ones
before it were written.

**`enum NAME : TYPE` says how much room a value takes and says nothing else.**  The type must be an integer type (4420) and must
hold every one of the values (4419), which are numbered from zero in the order they are written.  It is **not** a conversion:
writing `: i32` does not make a value of the enumeration an `i32`, does not let one stand where an `i32` is wanted, and does not
let an `i32` stand where the enumeration is.  A value of an enumeration is one of its names; the type is how the compiler lays one
out and is the whole of what it says.

Because the type and the indented form of the list are both introduced by a colon, a definition that names a type and indents its
values carries two of them -- one belongs to the type and one opens the block -- exactly as a function that answers with something
and has an indented body carries both a return type and a colon.

**Where no type is given the compiler chooses the smallest unsigned integer type that holds every value**: `u8` for values up to
255, then `u16`, `u32`, `u64`.  That is a promise about size and alignment and about nothing else.  The numbering from zero is
what the compiler does and is not something a program can observe, there being no conversion to observe it with; a later
implementation may number them differently, and a program that reads as it behaves cannot tell.

Compare: C, where an enumerator *is* an `int` and converts both ways silently, which is the source of a whole class of mistakes
this language does not admit; C++'s `enum class`, which is this arrangement down to the optional underlying type; Rust's
fieldless `enum`, which needs `as` to become a number; Go, which has no enumerations and uses typed constants instead.  The
spelling here is C++'s `enum class` without the second word, there being no other kind to distinguish it from.

**A value is written `TYPE.NAME`** -- `Colour.red` -- so that two enumerations may each have a `red`.  An arm of a `match` writes
the name alone, the type being known there already.  A name that is not one of the enumeration's is refused (4421).

**`=` and `≠` compare two values of one enumeration.**  Two of them are one value or they are not.  Ordering is not defined on
them: the order of the values is the order the definition wrote them in, and the language promises nothing about that.

##### Flag enumerations

`@[flag]` says the values of an enumeration are meant to be **combined**.

```
@[flag]
enum Perm : u8 { read ; write ; run }
```

Two things follow.  The values the compiler chooses are powers of two rather than consecutive numbers, so each is a bit of its
own.  And **the bitwise operators are defined on two values of the type** -- `&`, `|`, `^`, and `~` on one -- each answering with
a value of the enumeration.  Nothing else is: arithmetic on one would be a question about the number it is stored as, which is the
one thing the type does not say.

The language has no bitwise nand or nor operator -- `⊼` and `⊽` are the *logical* ones and work on truth values -- so a "neither"
is written `~(a | b)`.

A value of a flag enumeration may be a combination that no single name stands for, which is the point of one.  So the names do not
account for every value, and **a `match` over a flag enumeration needs an arm taking the rest** (4425).

#### Tuples

A tuple is several values travelling as one, written between `〈` and `〉` (U+3008 and U+3009).

```
fn both(x: u8, y: u8) → 〈u8, u8〉:
    〈x + y, x × y〉

let sum, product := both(a, b)
```

A type is written the same way: `〈u8, u8〉`.  Angle brackets rather than parentheses, so that a tuple of one thing is still a
tuple and not the thing with brackets round it.

**A tuple is a product with no names**, and that is the point of it: naming the parts of an answer that is taken apart on the spot
would be naming something that does not outlive the line it is written on.  Where the parts mean something beyond their position,
a product type with named fields is what says so.

**Names written next to each other take one apart**, one name per member, in a definition or in an assignment:

```
let sum, product := both(a, b)
low, high ← sorted(b, a)
```

There have to be exactly as many names as the tuple has members (4435): a member with no name would be a value the program worked
out and threw away without saying so.  Only a tuple can be taken apart that way (4436).

Each name of an assignment is assigned as it would be on its own, so a name that is not a variable, or one nothing may change, is
reported where it is written.

##### Making one out of others

`⁂` stands among a tuple's members as it stands among a call's arguments, and means the same thing: what follows it is several
values, and they stand there one each.  That is how one tuple is joined to another and how one is extended.

```
let pair: 〈u8, u8〉 = 〈1u8, 2u8〉
let wide: 〈u8, u16〉 = 〈3u8, 4u16〉

〈⁂pair, ⁂wide〉              ※ 〈u8, u8, u8, u16〉
〈9u8, ⁂pair, 9u8〉           ※ 〈u8, u8, u8, u8〉
```

What may be spread is what may be spread into a call -- a tuple, or an array whose type says its length -- and for the same
reasons, which the Calls section gives.  What it expands into are members like any other: a tuple made this way has the type it
would have had written out, and nothing that reads it can tell which way it was made.  None of it costs an instruction, a tuple
being its members held separately.

**A tuple still has at least one member.**  Spreading an array of no elements is the one way to arrive at none, and it is
reported (4466) rather than admitted: there is no tuple of nothing, and what a spread leaves obeys the rule that what is written
obeys.

##### Naming one member

`t⟦0⟧` is the member at that place, counting from nought -- the same brackets an array is looked in with, because what is being
asked for is a place among several and the language does not have two shapes for one question.

```
let pair: 〈u8, u16〉 = both(7u8, 300u16)
let low: u8 = pair⟦0⟧
let high: u16 = pair⟦1⟧
```

**The index has to be known while compiling** (4460).  That is mandatory, and it is what a tuple is rather than a rule chosen for
it: the members are of whatever types they were written with, so **which member is wanted decides what type the expression has**.
A type this language settles while the program runs is a type it does not have, so the index is settled before it runs or the
program is refused.

What counts as known:

| Written | Known |
|---|---|
| `t⟦0⟧`, `t⟦0u8⟧` | a literal, with or without a suffix |
| `t⟦FIRST⟧` | a name bound at the top level to something that cannot change and holds a whole number |
| `t⟦at⟧` for a local `at` | no -- what it stands for is the value an expression produced |
| `t⟦1 + 1⟧` | no -- a constant to a reader, and not yet to this compiler |

A name bound at the top level to a number that cannot change *is* that number, and a program that troubled to give it one should not
have to write the number again.  A name bound inside a function is not one even where nothing assigns to it: what it stands for is
the value an expression produced, and whether that expression could have been worked out while compiling is a question about the
expression rather than about the name.  Nothing worked out is one yet, and the to-do list says what asking earlier would need.

There is **one index** (4463), for the same reason an array of one dimension takes one: a tuple is a run of members and not a
shape.  Whether the index names a member is known while compiling too (4461), since how many members a tuple has is part of its
type.

**A member is read, not assigned to** (4462).  A tuple is a value and not a place: its members are registers, not room in memory
that something else could be pointing at.  Assigning to one would mean binding the name to a tuple made of the others and the new
value, which is what writing that out does.

Taking a tuple apart with names is still the way to have all of it at once, and is what a generated program will mostly write:
`t⟦0⟧` is for where one member is wanted and the others are not.

Compare: Rust's `t.0` and Swift's `t.0`, which spell the index as a field name and so need a rule saying a number may be one;
C++'s `std::get<0>(t)`, which spells it as a type argument because a function's result type may not depend on an ordinary one;
Python's `t[0]`, which is this, and which can be an ordinary index only because its tuples are not typed by member.

Compare: C++'s `auto [a, b]` and `std::tie`, which this follows in what it does and not in how it is written -- there is no second
pair of brackets around the names, the comma being enough to say that several names are being bound.  Python and Go write the same
thing the same way.  Rust writes `let (a, b)`, keeping the brackets so that a pattern looks like the value it matches, which
matters there because a pattern may be nested and here it may not.

##### What a tuple occupies

The compiler decides this.  In registers a tuple is one register per member, of whatever kind each member wants, which is what a
function that answers with two things needs and is the same arrangement a result already has.  In memory it is laid out as a
product of the same members would be.

Because a tuple is one register per member, a tuple answered with has to fit in the registers the convention answers in -- two on
each of these targets, counted per kind -- and one that does not is refused rather than silently put somewhere else.  A tuple of
two is therefore always fine, and a tuple of three integers is not yet.

#### Arrays

An **array** holds several values of one type, one after another.

```
let a: u8⟦4⟧ = ⟦10u8, 20u8, 30u8, 40u8⟧
let n: u8 = a⟦2⟧
a⟦0⟧ ← 5u8
```

`⟦` and `⟧` are U+27E6 and U+27E7, the white square brackets.  The plain ones are left free for whatever wants them next, and the
double parenthesis is the collection's: a collection is found by its key and an array by its place, which are different questions
however alike they read.  A type, a value of one and a lookup in one are written with the same brackets, as a collection's are.

**The type is written after what it holds.**  `u8⟦4⟧` is four of them, which is the order it is read in: four of these, not an
array of four whose elements are these.  More than one may follow -- `u8⟦4⟧⟦3⟧` is three arrays of four.

**One element says what they all are.**  An array holds one type, so an element that says which says it for every other -- wherever
that element stands, and however deep the writing goes:

```
let a := ⟦1u8, 2⟧                    ※ u8⟦2⟧: the first says it
let b := ⟦1, 2u8⟧                    ※ the same, said by the second
let c := ⟦⟦1u8, 2⟧, ⟦2, 3⟧⟧          ※ u8⟦2,2⟧: one element says it for the table
```

Which element is written down makes no difference, because the question is about the type of the array and there is only one of
those.  An element that contradicts it is refused (4431), and a literal that says nothing takes it -- which is the rule a literal
follows everywhere: a suffix says what it is, and without one it is whatever the place wants.  Where *nothing* says anything, as
in `⟦1, 2⟧`, there is no type to take and the array is refused.

The same holds for a list, a set and a dictionary, which each hold one type as well; a tuple does not, its members being
independent, so each member of one says its own type or takes it from the type the tuple is written into.

##### Shape

**An array has a shape: one entry per dimension, separated by commas.**  `u8⟦4⟧` is a vector of four, `u8⟦2,3⟧` a table of two
rows of three, `u8⟦2,2,2⟧` a cube.  There is no limit on how many.

```
let m: u8⟦2,3⟧ = ⟦⟦1u8, 2u8, 3u8⟧, ⟦10u8, 20u8, 30u8⟧⟧
let n: u8 = m⟦1,2⟧
```

**The elements are in row-major order**: the last dimension is the one whose neighbours are next to each other.  That is what
every language but Fortran does, and it is what makes a row of a table a run of elements rather than a stride.

**An array of more than one dimension is written a dimension deep** (4458): the outer list is the first dimension and each of its
entries is the array of the dimensions left.

**There is one index per dimension** (4457), in the order the shape was written in, and **each is checked against its own
dimension** -- `m⟦0,5⟧` of a two-by-three is outside, though it would be within the six elements there are in all.

**Fewer indices names a row**: everything the dimensions left over reach.  `m⟦1⟧` of a `T⟦2,3⟧` is a `T⟦3⟧`, and row-major is what
makes it cheap -- a row is a run of elements, so naming one is arithmetic on the place and no copy at all.  Where the array says
its shape the row says its own, and where it does not the row carries the counts that are left over.  An assignment still wants
every index (4457): what it writes is one element, and copying a whole row is not what `←` means anywhere else.

##### Fixed and dynamic

**`T⟦N⟧` says how many elements there are**, and carries everything about the array but the elements themselves.  A value of one
needs no room beyond theirs, and what a value of one *is*, is where the elements are.  `N` is a number written down (4447): how
many elements there are is part of the type, so it is something the compiler reads rather than something the program works out.

**`T⟦⟧` does not**, and is where the elements are together with how many there are.  It owns nothing: the elements are an array's,
or part of one.  For an array of more than one dimension it carries one count per dimension, so `T⟦,⟧` is a table of no stated
shape and takes three words.

**Every dimension says how many, or none does.**  Half of each would be a value whose parts depend on which half is which,
which is a second kind of array for a case nothing has asked for.

**A `T⟦N⟧` stands where a `T⟦⟧` is wanted**, which is how an array is passed to something that takes any length.  That goes one
way only: a `T⟦4⟧` promises four, and nothing that has lost its count can promise that.

```
fn total(xs: u8⟦⟧, n: u8) → u8: …

let a: u8⟦6⟧ = ⟦1u8, 2u8, 4u8, 8u8, 16u8, 32u8⟧
total(a, 6u8)
```

**A shape may be stated in part.**  `u8⟦,3⟧` is however many rows of three columns and `u8⟦2,⟧` is two rows of however many;
each dimension says or does not say for itself.  What makes that worth having is what picking rows out of a table produces --
however many were picked, each still as wide as the table was -- which is a type of exactly that shape and nothing else.

A value of such a type carries a count for every dimension, the stated ones included, so that what a value of an array type *is*
does not depend on how much of its shape the type says.  **What may be let go of is a length, never a length for a different
one**: a `u8⟦2,3⟧` stands where `u8⟦,3⟧` or `u8⟦,⟧` is wanted and does not stand where `u8⟦,4⟧` is.

##### Shaping one

**`⍴` is APL's rho and does APL's two jobs.**  Written before one thing it answers that thing's shape; written between two it
makes something of the shape on its left out of the values on its right.

```
⍴v                             ※ u64: how many, of a u8⟦4⟧
⍴m                             ※ 〈u64, u64〉: two and three, of a u8⟦2,3⟧

3 ⍴ 7u8                        ※ u8⟦3⟧: 7 7 7
6 ⍴ ⟦0u8, 1u8⟧                 ※ u8⟦6⟧: 0 1 0 1 0 1
〈2, 3〉 ⍴ v                     ※ u8⟦2,3⟧: the four, and then round again
```

**What it answers is what it takes**, which is not a coincidence: a number for one dimension and a tuple of numbers for more, so
`(⍴a) ⍴ b` is well formed for any array `a`.  A tuple of one is not a thing this language has, which is why one dimension
answers the number itself; APL answers a vector of one there and can, its arrays having no types to agree with.

**The shape has to be known while compiling** (4496).  An array carries its shape in its type, so a shape the compiler cannot see
would be a shape the type could not say.  A literal is known, a name bound at the top level to a number is known, and the shape of
something whose type states it is known -- which is what makes `(⍴a) ⍴ b` more than a form of words.  Every number of it says
how many, so none of them is none (4497).

**The values fill the new object in the order its elements lie in** -- the last dimension moving fastest, which is how they are
laid out -- and **begin again where they run out**.  One value goes everywhere.  Going round again is what makes
`n ⍴ ⟦0u8, 1u8⟧` alternate, which is the thing this operator is for, and it is why the rule is "round again" rather than
"pad": what to pad with is a question no type answers.

**More values than the new object holds is refused** (4498).  Which ones would be left out is not something to guess at, and a
program that meant to leave some out can say which.  APL truncates there; this does not, for the same reason nothing else here
quietly drops what a program wrote.

It binds tighter than `⧺` and looser than everything that works out what goes in an array, so `2 ⍴ 3u8 + 4u8 ⧺ v` is
`(2 ⍴ (3u8 + 4u8)) ⧺ v`.

Compare: **APL**, whose `⍴` this is, including the cycling and the two jobs -- the differences are that a shape here has to be
known while compiling, arrays here having types, and that too many values are refused rather than dropped; **NumPy**, whose
`reshape` requires the counts to match exactly and whose `full` and `tile` are the other two thirds of this; **BQN**, whose `⥊`
is the same operator with the same cycling; and **Fortran**, whose `RESHAPE` takes a `PAD` argument, which is the question this
answers by going round again instead.

##### Joining two arrays

**`A ⧺ B` is the elements of `A` followed by the elements of `B`**, in an array as long as the two together.  `++` is the
accepted substitute.

```
let a: u8⟦3⟧ = ⟦1u8, 2u8, 3u8⟧
let b: u8⟦2⟧ = ⟦4u8, 5u8⟧

a ⧺ b                            ※ u8⟦5⟧, holding 1 2 3 4 5
a + 10u8 ⧺ b                     ※ 11 12 13 4 5: it binds looser than the arithmetic
```

**It is not one of the operators that reach an array by being applied to every element of it.**  Those are defined on values;
this one is defined on arrays themselves.  Its two sides are of two different types and what it answers with is of a third, so
none of the rules about walking an array apply to it.

**The join goes along the first dimension and every dimension inside it stays as it was** (4492).  Two tables of three columns
join into a table of three columns; a table of three columns and a table of two do not join at all, and neither does a table with
a vector.  Row-major is what makes this a copy of one run after another rather than an interleaving: the last dimension is the one
whose neighbours are next to each other, so a table's rows are already laid out in the order a join wants them.

```
let m: u8⟦2,3⟧ = ⟦⟦1u8, 2u8, 3u8⟧, ⟦4u8, 5u8, 6u8⟧⟧
let n: u8⟦1,3⟧ = ⟦⟦7u8, 8u8, 9u8⟧⟧

m ⧺ n                            ※ u8⟦3,3⟧
```

**Both sides hold the same thing** (4491), an array holding one type, and **both say their shape** (4490), since room for the
answer has to be taken where the join stands and how much room that is, is how long the two sides are together.  A side that is
not an array at all is reported as such (4489); a single value is made an array of one by writing it between the brackets.

Compare: **Haskell**, whose `++` this borrows both the meaning and the ASCII spelling from; **APL**, whose `,` catenates along the
last axis and `⍪` along the first, with a conformability rule this one is the fixed-shape case of; **Python**, where `+` on lists
is this and `+` on arrays is element-wise, which is exactly the confusion a separate glyph avoids; **Go**, whose `append` is a
function because a slice's length is not in its type; and **Fortran**, where `[a, b]` is the array constructor doing this job.
What settles the glyph is that this language already uses `+` for the element-wise addition that Python's arrays use it for, so
the two had to be told apart, and a doubled plus is what says "of the things, not of the values".

##### Picking with a mask

**An array indexed by an array of truth values is picked from**: the things the mask says true, in the order they were in.

```
let v: u8⟦4⟧ = ⟦10u8, 20u8, 30u8, 40u8⟧

v⟦⟦false, true, false, true⟧⟧    ※ u8⟦⟧, holding 20 and 40
v⟦v > 25u8⟧                      ※ u8⟦⟧, holding 30 and 40
```

The second is the shape a mask usually arrives in: a comparison walked over an array answers with exactly one truth value per
element, which is what a mask is.

**How many were picked is not known while compiling**, so what picking answers with is an array whose first dimension the type
does not state.

**The mask's shape is the array's leading dimensions**, as many of them as it has (4484), and what is picked keeps the dimensions
the mask said nothing about:

```
let m: u8⟦3,2⟧ = ⟦⟦1u8, 2u8⟧, ⟦3u8, 4u8⟧, ⟦5u8, 6u8⟧⟧

m⟦⟦true, false, true⟧⟧           ※ u8⟦,2⟧: rows, each still two columns wide
m⟦some⟧                          ※ u8⟦⟧ for a bool⟦3,2⟧: elements, keeping no shape
```

**Assigning through a mask writes one value to everything it picked** and to nothing else -- whole rows where the mask picks
rows, by the same rule:

```
v⟦v > 25u8⟧ ← 1u8                ※ 10, 20, 1, 1
m⟦⟦false, true, false⟧⟧ ← 9u8    ※ the middle row, both of it
```

**The array picked from states its shape** (4485).  What picking answers with is held in room enough for everything that could
have been picked, and room enough for that is what the shape says; where the type does not say it, the room would have to be
taken while the program runs, which is an allocation and a change to something that outlives the call.

**Nothing branches, either way.**  Picking writes each thing where the count has got to and then advances the count by the mask,
so a thing that was not picked is written where the next one writes over it -- which is sound because the room is this call's own.
Assigning writes every element either the old value or the new one, chosen with the mask spread across the width of the element,
so that no instruction depends on what the mask holds.

Compare: NumPy's boolean indexing, which this is -- `v[v > 25]` reads and `v[v > 25] = 1` writes, with the same rule that the
mask's shape is the array's leading dimensions; APL's compress `/`, which is the same operation with a much older spelling and
which threads over an axis chosen by the operator rather than by the mask's rank; MATLAB's logical indexing, likewise; and
Fortran's `PACK` and `WHERE`, which separate the two halves into a function and a statement where this has one spelling doing
both, on the grounds that what is written on the left of `←` and what is read on the right should not need different names.

##### Reading, writing and slicing

`a⟦i⟧` is the element at `i`, counting from nought, and `a⟦i⟧ ← v` puts a value there.  The index is a whole number of any integer
type (4451); a literal with no suffix is read as a count, which is the only place an index takes its type from anything but
itself.

**Every read and every write is checked.**  Where both the index and the length are written down, the answer is known while
compiling and a program that could only fail is refused (4450).  Where either is not, the check is one comparison and a branch that
does not come back -- the same shape an addition that does not fit has, and reported the same way: the message says what was wanted
and where, and the program stops.

`a⟦i…j⟧` is the elements from `i` up to but not including `j`, which is a `T⟦⟧`.  It is the same half-open convention a range has
everywhere else, so the count is the difference between the ends.  A step there is refused (4453): what a slice is, is a place and
a count, and every other element is not something a place and a count can say.

**Only a vector is sliced** (4459), for the same reason.  A row of a table is a run of elements and a column is not: its elements
are a row apart, which is a stride.  Until a slice can carry a stride there is nothing for a run out of a table to be.

##### Writing an element

**An element is written only through a name that says it may be**: `let a: mut u8⟦4⟧`, a parameter `a: mut u8⟦⟧`, a `&mut`
reference -- as a field of a record is (4004).  An array named without `mut` is read and not written, whoever holds it, so `mut` on
an array parameter says the body writes the caller's elements.  A caller hands such a parameter only an array that may be written
(4640): not one named without `mut`, and not one `in ⎕static`.  A function that writes an array it is handed is not a value yet
(4641), a function type not saying which parameters write.

Compare Rust's `&[T]` and `&mut [T]`, C++'s `const T*` and `T*`, and D's `const(T)[]`: the same split, said on the parameter.  Before
this an array not marked `mut` could be written all the same, which a table in a read-only part of the image cannot be.

##### Where the elements are

A variable at the top level of type `T⟦N⟧` holds them itself, as bytes in the image.  One inside a function holds them in the
function's own room, which lasts exactly as long as the call.

Two things follow from that, and both are refusals the language will lift when it can say where something lives:

- **A function does not answer with an array type** (4454).  The elements of an array a function made are in that function's own
  room, which is gone by the time the caller reads them.  A slice of something that outlives the call would be safe, and there is
  no way yet to say that one does.
- **A variable at the top level of type `T⟦⟧` is refused** (4455).  It owns nothing, and there is nothing for it to point at
  before the program runs.

##### What one occupies

The specification says the compiler decides data layout, and that a definition which has to be reachable from a world that has
never heard of this language gives that freedom up.  **An array variable marked `@[cdecl]` is laid out the way the system's own
compilers would lay it out**; every other one is laid out whichever way is better.

```
@[cdecl, visible]
let shared: u8⟦24⟧ = ⟦…⟧      ※ aligned as one element is, which is what C says

let ours: u8⟦24⟧ = ⟦…⟧        ※ aligned as this compiler likes
```

What the freedom is used for today is one thing: an array large enough to be worth reading a word at a time is put where a word can
be read.  How far apart two elements are is *not* among the things that may differ, and that is deliberate -- it is what an index
is multiplied by, so a freedom there would have to be told to everything that indexes.

Compare: C, whose arrays decay to a pointer and lose their length, which is where a great many of its defects come from; Go, whose
`[4]T` and `[]T` are exactly this pair and which this follows; Rust's `[T; 4]` and `&[T]`, the same pair with a lifetime on the
second; Zig's `[4]T` and `[]T`.  What none of them writes is the length after the element type; C writes `T a[4]`, which puts part
of the type on the left of the name and part on the right, and every language since has moved it to one side.

On shape this follows the array languages rather than the systems ones.  A `u8⟦2,3⟧` is one array of two dimensions, not an array
of two whose elements are arrays of three: it has a shape the way an array in APL, BQN or NumPy has one, and `m⟦i,j⟧` asks for a
place in it rather than for an element of an element.  C, Go, Rust and Zig all have only the second reading and spell it `m[i][j]`;
this language has that reading too -- `u8⟦3⟧⟦2⟧` is two arrays of three -- and the two are laid out alike and are different types.
The difference shows where a program says what it means: a table is indexed with one pair of brackets, and an array of arrays with
two.

#### Lists

**`[a, b, c]` is a list**: however many values there turn out to be, one after another.  The type is written the way a value of
one is, so `[u8]` is a list of bytes.

```
let a: [u8] = [1u8, 2u8, 3u8]
let empty: [u8] = []
let plain: [u16] = [1, 2, 3, 4]      ※ the elements take the list's type
```

**A list is not an array.**  An array carries its shape in its type and takes no room of its own; a list carries how many there
are beside where the elements are, and the elements live in an arena.  So an array is what a program reaches for when it knows
how many, and a list when it does not.  Making one allocates, and allocating is no effect: a pure function may make a list and
answer it.

**Its elements must all be of one type, for now.**  A list is the sequence whose elements need not be: what makes that work is
*boxing* -- a value held with enough beside it to say what it is -- which this compiler does not do yet, so until it does, they
must agree (4500).  **The type they agree on is recorded in the list's type**, and that is the part worth saying out loud: when
boxing arrives, a list whose elements are all of one type is the case worth *not* boxing, and a compiler that had thrown the type
away could not find it again.

**A list written with nothing in it takes the type wanted where it stands** (4501) -- the value of a name whose type is written,
an argument, what a function answers with.  Where nothing wants one there is nothing to take it from.

**`#` answers how many** it holds, **`foreach` walks its elements** in order, and **`⧺` joins two**:

```
#a                                   ※ 3
foreach x := a:                       ※ 1, then 2, then 3
a ⧺ [4u8]                            ※ [u8] holding four
```

Both sides of a join hold the same type, which today is every list's rule and will later be the case worth not boxing.

**`†l⟦i⟧` takes one element out**, and answers what it was:

```
let l: mut [u8] = [1u8, 2u8, 3u8]
let gone: u8 = †l⟦1⟧                ※ 2, and the list is [1, 3]
†l⟦0⟧                              ※ or as a statement, with nothing read
```

What followed the element moves down into its place and the list is one shorter.
**What follows the `†` is where the list is** (4701) and not a list that was worked out: a list is where its elements are and
how many there are, so one element fewer is a different pair of words, and they have to go back where the list was.  The place has
to allow it, so the name needs `mut` (4004) -- and **another name holding that list keeps what it had**, a list being a value and
not a handle.  That is the bargain Go strikes with its slices, written down here rather than discovered.

**An index past the end stops the program** (as an array's does), which is a comparison at run time: a list's length is not in its
type, so there is nothing for the compiler to refuse.

There is no plain `l⟦i⟧` read yet (4449); what reads one element is a cursor, below.

##### Walking one with a cursor

A **cursor** is where a walk over a list has got to.  `⎕iter(l)` is one at the first element:

```
let it: mut = ⎕iter(l)          ※ the type has no spelling of its own; the name takes it
unless it:                    ※ until the walk is over
    if ∣it⌖:                     ※ what it is at, asked whether it is even
        it ← †it               ※ that one goes, and the walk goes on
    else:
        it ← ⇧it               ※ on to the next
```

- **`⎕iter(l)`** is given the *place* the list is in and not the list (4701, 4703), because a walk may take an element out and
  what is left has to go back where the one holding the list will read it.  So what may be walked is what may be written.
- **`it⌖`** is the element the walk is at, read through the mark that asks what is at a place -- which is what a cursor says.
  Reading through a walk that is over stops the program.
- **`⇧it`** is the cursor at the next element and **`⇩it`** the one at the element before (4704).  A walk that steps off either
  end stops the program: there is nowhere for such a cursor to point, and answering a result instead would put a `??` on every
  step of every walk.
- **`†it`** takes out what the walk is at and answers the cursor at what followed -- which is the same cursor, since what came
  after has moved down into the place the element left.  Where what went was the last element, the answer is a walk that is over.
- **A cursor where a truth value is wanted asks whether the walk is over**, which is the one question anything walking a list asks
  of it.  It is that way round because `unless` is what reads it, and a loop that runs *until* the walk is over needs no `¬`.

A cursor is two words -- where the list is and how far along -- and has no spelling as a type yet, so it lives in a name whose
type is read off its value.  Passing one to a function waits on that spelling, and the to-do list records it.

Compare: **C++**, whose `it = v.erase(it)` is this idiom exactly, and whose iterators the removal invalidates -- here there is
nothing to invalidate, a cursor being where the list is rather than where an element is; **Rust**, whose `Vec::retain` is this loop
written as a call and whose iterators cannot remove at all; **Python**, where removing while walking is the mistake every tutorial
warns about; **Go**, which has neither.

Compare: **Python**, whose lists are heterogeneous and boxed always, which is the shape this is aiming at and the cost it means
to avoid where it can; **Lisp**, where a list is the type and a cons cell the cost; **Rust**'s `Vec<T>` and **Go**'s slices,
which are this without the heterogeneous future and which therefore need no boxing at all; and **JavaScript**, whose arrays are
heterogeneous and whose engines spend a great deal of effort recognising the case where they are not -- which is the case this
language records in the type instead of recovering at run time.

#### Arenas

An **arena** is where a program's memory comes from.  It is a type like any other, and a program makes as many as it wants:

```
let scratch: mut arena = ⎕arena
```

`⎕arena` is what a variable of that type starts out holding, and there is nothing else to write there (4444): an arena is a place
the allocator keeps its state in, not a value a program computes, and what is written says only that the place starts out holding
an arena that has asked the system for nothing yet.

**There are two kinds of allocator.**  An **arena** -- a *pool* -- is a bump pointer over a list of chunks: an allocation out of
one is an addition and a comparison, where the current chunk has no room it asks the system for another, nothing is given back
on its own, and the whole of it is given back at once.  That is what makes one safe for storage with a known lifetime, and wrong
for a program that runs for a long time.

**`⎕heap` gives back one object at a time.**  It is the allocator everything that allocates and names no other one comes out of,
and it is in the runtime: sizes are rounded to a *class* -- sixteen bytes apart up to 256, then powers of two up to 64 KiB --
each class keeps what was given back to it, and anything larger is mapped on its own.  **Giving back takes the size**, which the
compiler always knows, so no object carries a header saying it -- C23's `free_sized`, C++'s sized `delete` and Rust's `dealloc`
are the same interface.  Where the compiler knows an allocation is `⎕heap`'s it calls the heap straight away; elsewhere one
compare picks the kind.

**`EXPR in NAME` says which arena an expression takes its room from**, and `⎕empty(NAME)` gives the whole of one back:

```
let scratch: mut arena = ⎕arena
let both: str = first ⧺ second in scratch
let said: str = ⍕v in scratch
⎕empty(scratch)
```

What may carry `in` is what takes room: a join of two strings or two lists, a collection written out, and an operator whose
definition asks for an arena -- which is what `⍕v in scratch` is, the second operand being where to put what it builds.  Anything
else is worked out in registers and has nowhere to come from (4568).

**Taking room from an arena is not an effect**, from `⎕heap` or from any other.  What is made is new: nothing that was there
before the call can reach it, so nothing anybody else holds changes, and a pure function may allocate wherever it likes -- named
with `in`, or not named and so from `⎕heap`.  What `⎕heap` holds lasts as long as the program, so a function may answer it; that is
how a function hands a caller text when the caller gave it no arena.  Filling what was just made -- the entries of a collection,
the elements of a list -- writes only that new room.

**`⎕empty` is the only granularity there is**, which is what makes an arena a *pool*: room is taken from it for as long as it is
wanted and the whole of it goes in one call.  What is left is an arena with nothing in it, so taking room from it again asks the
system for a first chunk.  Emptying is what can disturb somebody else, which is why only the function that made an arena may
do it (below), and `⎕heap`, which nobody made, is never emptied.

**`⎕arena` works inside a function as well as at the top level**, where it is three words of nought in the frame -- so a pool of one
call's own is one line, and giving it back is one more.  With `defer` they are the same line's neighbours, and the giving back
happens on every way out of the block:

```
let scratch: mut arena = ⎕arena
defer ⎕empty(scratch)
```

**An allocation that cannot be met stops the program.**  Answering with a result would put a `?` on every value a program builds
rather than computes, and there is nothing a program could usefully do at that point that the system will not do better by
refusing to start it.

Compare: Zig, where every allocator is a value and every allocation names one, which is where this arrangement comes from; Odin,
where the allocator is in an implicit `context` and a collection does not say which it uses; Rust, where a collection is
parameterised by its allocator in its type; C and Go, where there is one heap and nothing says so.  This sits with Zig and Rust:
a program should be able to read where a value lives off the line that makes it.

##### What an arena holds

**A value made in an arena does not outlive it.**  The compiler keeps track, for every name, of the arenas what it holds was made
in, and refuses every way a program could read memory an arena has given back:

- **Read after `⎕empty`** (4615).  Every name holding something made in the arena is dead from the `⎕empty` on, and stays dead after
  the block it was written in ends -- on some way there it was given back, and a name read later cannot tell which way it came.  A
  name given something new is alive again.
- **In a loop** the same holds across turns: a turn that empties an arena leaves the next turn reading what was made in it before
  the loop began, so that is dead for the whole of the loop.  What a turn makes for itself it makes again each turn, and is not
  touched.
- **Answered by the function that made the arena** (4613).  A function gives back its own arenas before it returns, so what it
  answers out of one is gone by the time anybody reads it.  An arena the caller handed over by reference is the caller's, and what
  is made in it lasts as long as the caller says -- which is how a function returns text it built: it asks to be handed the arena.
- **Put where it outlives the arena** (4614): a name bound further out than the arena, given something made in it, and the value a
  block comes to when the arena was made inside the block.  It is the rule a reference follows, asked of what an arena holds.

**What a value was made in is read off what is written.**  An arena is named where room is taken from it -- after `in`, or handed to
a call -- and a name carries the arenas of what it was given.  **A join of two strings is made in its arena and nowhere else**, its
bytes being copied, so what the halves were made in stays theirs.  A call whose signature says nothing is taken to answer something
made in any arena it was handed, which is the only thing a caller can know without seeing into the function; an answer that came
from somewhere that lasts is then a cautious guess and never a wrong one -- and a signature can say exactly (below).  A value of a
type that points nowhere -- a number, a `bool`, a fixed array of those -- was made in nothing, whatever it was computed from.

**It follows every way through a body, as a name's value does.**  Each arm of an `if` or a `match` starts from what held before
it, and where the arms join a name was made in anything it was made in along any arm that reaches the join -- and is dead if it is
dead along any of them.  An `if` with no `else` has a way through that runs no arm, and an arm that leaves the function reaches no
join at all.  What an `if` comes to is what its arms *yield*, the last expression of each, and not the temporaries they used on the
way.  An arena given back in every arm that reaches the join is given back after it.

**What a way out runs, it runs along that way only.**  A `defer ⎕empty(s)` lowered at a `return`, a `?`, a `break` or a `continue`
kills what was made in `s` along the way that leaves, and the way past it still holds what it held -- the arena has not been given
back there yet.

**A loop's turns are one way after another.**  A name the body assigns may hold, at the top of a turn and after the loop, what any
turn gave it, so it is taken to be made in anything the body names as well as in what it held before.  The way out of the loop is
the top of a turn, the end of the body, and every `break`.

**A part is the whole's.**  A field, an element or an entry given something made in an arena makes the whole hold it: what the
whole was made in grows, and is never replaced.  Reading a part of a dead name is reading a dead name.  And what a `match` takes
out of a value, what a `foreach` takes out of what it walks, and what a tuple is taken apart into, were made where the whole was.

**Only what made an arena gives it back** (4616).  One handed over by reference is its maker's, who may be holding things made in
it; `⎕heap` and an arena at the top level last as long as the program, and anything anywhere may be holding things made in those.
Without this rule none of the above would hold: a function emptying an arena it was handed would kill names in its caller that the
caller cannot see die.

**A pure function gives back every arena it made, on every way out** (4617).  Room an arena took from the system stays taken until
it is given back, so leaving with one still holding room changes something that outlives the call -- which is what `@[impure]` is
for.  What counts is an `⎕empty` on that way out, written there or put off with `defer`.

Compare: **Rust**, whose lifetimes say the same thing about every reference and check it with a borrow checker; an arena crate such
as `bumpalo` ties what it hands out to the arena's lifetime, which is this rule written as a type.  **Zig** and **Odin** have the
arenas and none of the checking: freeing one and reading what was in it is the program's mistake to find.  **C++**'s
`std::pmr::monotonic_buffer_resource` is an arena with the same lack.  **Go** and **D** collect garbage, so the question does not
arise and neither does the control.  This sits with Rust in what it refuses and with Zig in what it costs: nothing at run time,
and a rule a reader can apply by looking at where a name's value was made.

##### Knowing the allocator

**How every object that points somewhere is given back is known.**  A string and a list carry, beside where they are and how many,
the allocator they came from -- none for text in the image, which nothing gives back -- so whatever holds one can give it back
without being told.  That is the default, and it is safe: no object's allocator is ever unknown.  **The compiler reads the word
only where it has to.**  Where it knows the allocator while compiling it calls that allocator straight away, or nothing at all for
a pool, and the word is never read; which of the two it chose for each name is written to the report log (`allocator`), and every
copy it made is there too (`copy-into-allocator`).  Nothing of it surfaces in the language: a program says where a value is made
with `in`, and the rest is the compiler's.

**What saying nothing means is `⎕heap`.**  A join, a list or a collection written with no `in` is made there, and so is what a
function answers whose signature names no arena.  A parameter that says nothing may have been made anywhere: it carries its
allocator, and lasts as long as the call.

**An answer is kept in the allocator the signature names**: `→ T in a`, or `⎕heap`.  One made anywhere else is copied there before
the function leaves -- before what it put off runs, so an arena given back on the way out has been copied out of first.  That is
how a value made in a scope's own arena reaches past the scope.  Text in the image goes as it is; a value of the heap's is the
caller's alone, so only a temporary of the heap's is answered as it is, and a name holding one is copied.  **What cannot be copied
is refused** (4624): a record or a tuple holding text, a result, and anything holding a value marked `@[unique]`, which is what
`@[unique]` is for.

**An answer whose allocator is fixed travels without it.**  `→ str in ⎕heap` says every answer is made in the heap -- text in the
image is copied there.  A caller that knows the allocator learns nothing from the third word, so such a function answers a string
or a list as two words, and every call puts the third back.  Where the signature names an arena, or nothing, the compiler looks at
the body: one whose every way out answers something made in `a`, or something the heap just made, is fixed there all the same --
`⎕heap`'s address or the arena the call handed to `a` is what the caller puts back -- and its callers, checked after it, the call
tree being walked callees first, rely on it.  Text in the image answered as it is keeps the third word, which says it is nobody's;
naming `⎕heap` is what says no answer ever is.  Which functions answer thin is in the report log (`answer-thin`).
A function called through a function value, or seen from outside the image, keeps the word.

**A value stays two words for as long as its allocator is known.**  Inside a scope the compiler holds the allocator of every
value whose allocator it knows, and puts the third word back only where the value is needed whole -- handed to a call, stored,
answered.  Where values join -- the arms of an `if`, the turns of a loop -- they stay two words if every one is made in the same
allocator the compiler can name again: `⎕heap`, none, or what a parameter is given.  Values from two allocators, or from one
known only at run time, carry it.  Every such decision is in the report log: `lean-value` where a value stays two words,
`fat-value` where it carries its allocator and why, `answer-thin` for an answer that travels without it.

**What is written into a place from outside the call lasts as long as the caller can read it.**  A field of a record a
reference parameter names, or an element of an array the caller handed over, is the caller's, and read after the call has ended --
so what goes there is held to what an answer is: one made in `⎕heap` or in the image goes in as it is, and one made anywhere else
is copied into `⎕heap` on the way in.  What cannot be copied is refused (4633), and so is a lambda reaching anything that does not
last (4632).

A value made in a local arena reaches past the scope either as a copy, as above, or with the arena itself (below).

##### What is in the image

**`⎕static` names the allocator that is no allocator: the image.**  What is in it is made while compiling, lies in a part of the
image nothing writes, lasts as long as the program and is never given back -- its allocator word is nought, which is what text
written down has always carried.

**`→ T in ⎕static` says every answer is in the image.**  The answer then travels without its allocator, the caller adding none
(`answer-thin` in the report log).  Nothing is copied into the image while the program runs, so an answer made anywhere else is
refused (4635), and `⎕static` is named alone (4634).  A body whose every answer is text written down is held to be answering in the
image without saying so, as one answering only what the heap just made is held to answer in the heap.

```
fn digit(d: u64) → str in ⎕static:
    let digits: str⟦10⟧ = ⟦"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"⟧ in ⎕static
    digits⟦⎕unit(d, ⌜idx⌝)⟧
```

**`… in ⎕static` puts a container in the image**: an array, a list, a set or a dictionary written down of what the compiler knows
-- numbers, characters, truth values, values of an enumeration, text, records and containers of those, a container inside one being
in the image as well.  At the top level and inside a body alike: inside one, the name stands for where the image keeps it, and
nothing is built when the line runs.  Anything worked out while the program runs is refused (4636), and so is a variable that may
change (4637): nothing writes the image, so a `mut` name, a `mut` collection type, an element written and a `&mut` taken of one are
all refused.  A table in the image is laid out entry by entry where the program's own insertions would have put it, so a walk of it
meets its keys in the order a walk of the same table built while running would.  A table made out of one -- a union, say -- comes
out of `⎕heap`, there being no allocator to come out of the same one.

**A definition that never changes and holds only what the compiler knows says `in ⎕static`** (4638).  It is what C++ makes of a
`static const` table, and saying it is what tells a reader the table is not built every time the line runs -- a stronger rule than
C++'s, where the reader has to look for the keyword.  A container written with an arena of its own, or a value worked out from
anything else, is not such a definition.

Compare **C** and **C++**, where `static const` is how a table goes in read-only data and nothing makes a reader write it; **Rust**,
whose `&'static str` is this lifetime and whose `static` items this placement, with `phf` for a table built while compiling; **D**'s
`immutable` and **Zig**'s comptime-known slices, both placed in read-only data; and **Go**, whose composite literals are built at
run time unless the compiler proves otherwise and says nothing either way.

##### Handing an arena out

**A function may hand an arena of its own out with its answer**: `→ T in pool`, where `pool` is no parameter and not `⎕heap`, says
the body makes `pool` -- `let pool: mut arena = ⎕arena`, in the body's own scope (4627) -- and that the answer is made in it.  The
caller receives both, the arena under a name of its own:

```
fn words(text: str) → [str] in pool:
    let pool: mut arena = ⎕arena
    …build the list in pool…

let found: [str] in kept = words(line)
defer ⎕empty(kept)
```

**`kept` is then the caller's arena like any it made**: `found` is made in it, `⎕empty(kept)` gives both back, and a pure function
gives it back before it leaves (4617).  The body may not give `pool` back (4628) -- it is the caller's from the moment the call
returns -- and what it answers is kept in `pool` by the rules of any answer: made elsewhere, it is copied in.

**Nothing moves.**  Where the caller keeps `kept` is handed to the call, as a parameter no program writes, and the body's `let pool`
starts the arena there instead of in its own frame: what is made in `pool` is already where the caller will hold it, and every
allocator a value of it carries names the caller's arena.  The answer is then a `→ T in a` answer for that parameter, so it travels as
two words where every way out makes it in `pool`.

**Only a definition receives one** (4629): the call is the whole of what `let v: T in kept = …` is given.  Written anywhere else -- an
argument, part of an expression, a definition with no `in`, or the function named as a value -- what it hands out would have no name
to be given back by.  `f(…) in kept` is refused too (4630): `in` after an expression says it takes room from an arena that exists,
and this arena does not yet.  And a definition with `in` whose value is no such call is refused (4631).

**A lambda may hand one out**, `λ n: u64 → str in mine: …`, and its type says so: `fn(u64) → str in it`, the name after `in` being the
reader's.  A call through a value of such a type is received the same way.

Compare: **Zig**, where a function that makes an `ArenaAllocator` and returns it with what it built returns a struct holding both,
which nothing checks; **Rust**, where returning a `Bump` with a `Vec<'bump, _>` is the self-referential struct the borrow checker
cannot express, which `ouroboros` and `yoke` exist for; **C++**, a `pmr` container returned beside a `unique_ptr` to its resource;
**Cyclone**'s dynamic regions, unique handles a function returns and a caller opens.  The alternatives are in
[scoped-arenas.md](scoped-arenas.md).

##### What a container holds

**Every element carries its allocator**, so the elements of one container need not share one.  A container of text or lists is
made with an allocator -- the one `in` names, or `⎕heap` -- and an array, which is room in the frame, carries none of its own: `in`
after one says where its elements are kept.  A container of values that point nowhere needs no allocator at all, and `in` after
one says nothing (4568).

```
let v: mut str⟦3⟧ = ⟦"x", t, u⟧ in a     ※ elements kept in a
let l: [str] = ["k", t]                   ※ the list and its elements in ⎕heap
let n: u8⟦2⟧ = ⟦1u8, 2u8⟧                 ※ numbers point nowhere: no allocator
```

**What is put in goes in as it is wherever that is safe, and is copied into the container's allocator otherwise** -- in a literal,
over an element with `v⟦i⟧ ← x`, and in a join of two lists:

- **Text in the image** goes in as it is: nothing gives it back.
- **A pool's container** never gives back one element.  What it holds may come from anything that lasts as long as it does: its
  own arena, an arena the caller handed over, an arena bound further out, and `⎕heap`.  **Replacing an element calls nothing at
  all.**  An arena bound beside the container is not provably long enough -- it may be given back while the container is read --
  so what it made is copied.
- **A heap container** gives back what it replaces, asking the element which allocator it came from.  So what it holds of the
  heap's is its alone: a temporary of the heap's is moved in, anything else of the heap's is copied, and what a long enough lived
  pool made goes in as it is, giving that back being nothing.

The copy goes all the way down: a list of lists of text is copied list by list and text by text, and a temporary that was copied
is given back.  **Some values cannot be copied yet** (4623): a record or a tuple holding text, a set or a dictionary -- and an array
of text held by another name, which is its elements, so copying it by value would give each element two owners.

**A value read out of a heap container is the same object, not a copy**, and lives until an element is replaced: after `v⟦i⟧ ←`,
or after `v` is handed to a call that may replace one, what was read out of it before is dead (4626).  Which element is not asked:
telling one index from another is arithmetic.  A pool's container is not touched by this, giving nothing back one at a time.

**Which allocator a container keeps its elements in has to be known to write one** (4622), and the compiler knows it of a container
this body made or was handed by name.  A parameter says it with `in` -- `v: mut str⟦⟧ in a` -- and keeps them in `⎕heap` where it
says nothing; a container handed to it has to keep them where it says (4625), a pool's and the heap's being given back from by
different rules.  A field of a record keeps them in `⎕heap`, and only an array nobody else names may be put in one.

Sets and dictionaries are not held to this yet: their keys and values go in as they are.

Compare: **C++**, whose containers copy what is assigned into them and destroy what they replace, a `pmr` container copying into
its own memory resource -- this, with the copy decided at run time by the resource's identity rather than proven while compiling,
and with a dangling reference to a replaced element left for the program to avoid.  **Rust** refuses a store whose lifetime is too
short instead of copying, and the borrow checker refuses the read of a replaced element as this does.  **Zig**'s `ArrayList` holds
what it is handed, and which allocator made an element is the program's to know; its "managed" containers store the allocator as
these do.  **Go** and **Java** collect garbage and share.

##### Where an answer was made

**`→ T in a` says the answer was made in the arena parameter `a` names**, or in something that lasts at least as long -- `⎕heap`, a
literal, a variable at the top level.  It is the `in` an expression is written with, said of what the call comes to:

```
fn label(a: &mut arena, b: &mut arena, v: u16) → str in a:
    let scratch: str = ⍕v in b          ※ working space
    "#" ⧺ scratch in a                  ※ the answer

let name: str = label(&mut long, &mut short, 7u16)
⎕empty(short)                           ※ name lives on: it was made in long
```

**A caller** takes the answer to be made in exactly what it handed the named parameters: emptying `short` leaves `name` alive, and
emptying `long` kills it.  Without the annotation the answer is made in `⎕heap`.

**The body is held to it**: an answer made anywhere the signature does not name is copied into the arena it does name, before the
function leaves (above), and refused where it cannot be copied (4624).

**`→ T in a, b`** says the answer may come from either, so it lives as long as the shorter of the two -- the rule a lifetime name on
several parameters has.

**`s: T in a` says a parameter was made in what `a` names**, which is what lets a body answer it under `→ T in a`.  A caller is held
to it (4620): what it hands `s` was made in the arena it hands `a`, or in something lasting longer.

**What follows `in` is a parameter holding an arena**, `&mut arena` or `&arena` (4618), or -- on the answer -- `⎕heap`, which fixes
the answer in the heap (above).  A local arena is given back before the call ends and cannot be named.  Said of a type that points nowhere it says nothing
(4621, a warning).  It is said of the whole answer; `(str, str) in a` says it of both.

**It is a lifetime, spelled as the arena.**  An arena-made value is a reference in all but spelling -- `str` is a pointer and a
length -- and "made in `a`" is "lives as long as what `a` names".  So `in a` is what `⧖x` shared by the arena parameter and the answer
would say, with nothing to declare and no name to keep in step.  `std.text` and two-operand `⍕` say `→ str in a`.

Compare: **Rust** with an arena crate such as `bumpalo`, `fn label<'b>(a: &'b Bump, …) -> &'b str`, which is this with the lifetime
written out -- Rust has to, a reference and its arena being two things there.  **Cyclone**'s regions, `char *ρ label(region_t<ρ>
r)`, are the closest ancestor: a region handle as a parameter and the region on the answer's pointer type.  The **ML Kit**'s regions
infer all of it, so what a reader sees is the compiler's output.  **D**'s `return scope` marks the parameter rather than the
answer.  **Zig** and **Odin** say it in a comment -- "caller owns the returned memory, allocated with `allocator`" -- and nothing
checks it; **C++**'s `pmr` containers remember their resource and a signature says nothing about which.  The alternatives are in
[answer-arenas.md](answer-arenas.md).

#### Sets and dictionaries

A **set** holds keys and says nothing about them beyond whether it holds them.  A **dictionary** says what each of its keys stands
for.  Both find a key by hashing it, so a lookup in either takes a time that does not grow with how much is in it.

```
let s: ⸨u8⸩ = ⸨1u8, 2u8, 3u8⸩
let d: ⸨Key: u8⸩ = ⸨Key.a: 1u8, Key.b: 2u8⸩
```

**A type is written the way a value of one is**: `⸨T⸩` is a set of `T` and `⸨K: V⸩` a dictionary from `K` to `V`.  That is the
arrangement a parameter list and a call already have -- what a thing is and what a thing looks like are written alike.

`⸨` and `⸩` are U+2E28 and U+2E29, a double parenthesis.  Braces are the explicit block notation and square brackets are what an
array will want, so neither was free; and a collection written down and a lookup in one are then the same shape, which is what
lets `s⸨k⸩` read as "the set at k" without a second pair of characters to learn.

**Which of the two a collection is is decided by its first entry.**  A colon after it makes it a dictionary and its absence a set
(3026 where a later entry then has none).  One written with nothing in it, `⸨⸩`, is neither until something says which, and what
says so is the type it is wanted as (4432).

**Every key of one collection is of one type**, and so is every value of one dictionary (4431).  Nothing is widened to make two
meet, here as anywhere else.

##### What can be a key

A key is hashed to find where it might be and then compared to see whether it is there, so a type that can be one is a type `=` is
defined on and answers **exactly**: the integer types, `bool`, `char`, enumerations, and `str` (4429).

**Text is a key**, and is the one key that is not a single word.  Both questions have an answer over its bytes: two strings that
say the same thing are one key wherever their bytes are, which is what `=` already answers, and the hash is taken over the same
bytes.  Everything that reaches a program from outside it -- the environment, a name in a file, a header -- arrives as text, so a
language whose dictionaries could not be keyed by it would have every one of those searched in a loop.  What it costs is stated
rather than hidden: a key that is a word is hashed by one multiplication and compared by one instruction, and a key that is text is
hashed by a walk over its bytes and compared by another; an entry of such a table is wider by a word.

Floating point is left out on purpose, and the reason is worth stating: a not-a-number is equal to nothing, including itself, so a
key put in could never be found again; the two zeroes are equal and have different bits, so hashing them by their bits would put
one where the other is not; and two values arrived at by different routes rarely are one value, which is the thing the approximate
comparisons exist for and which a hash table cannot use.  A product, a sum and a result have no equality at all yet, so none of
them can be a key either.

The value type of a dictionary may be anything that is not `void` (4430) -- a dictionary whose keys stand for nothing is a set --
and it need not fit in a word: an entry holds the value as what it is, so a string, a tuple or a record goes under a key as readily
as a number does (4445 where it is another collection, which would be a place in an arena outliving the entry by accident rather
than by saying so).

##### Reading and writing

`s⸨k⸩` on a set answers **whether it holds the key**, which is a `bool`.

`d⸨k⸩` on a dictionary answers with a **result**: `V?`, the value where there is one and the fact that there is none where there
is not.  So a key that is not there cannot be read past by accident, and `d⸨k⸩ ?? 0u8` says "or this instead" with nothing new to
learn -- it is Python's `d.get(k, 0)` written with the operator the language already has, and `d⸨k⸩?` hands the miss back to the
caller.  Python raises `KeyError`; this language has no exceptions and has a type that says the same thing in the signature.

`d⸨k⸩ ← v` puts a value in a dictionary under a key.  A set has nothing to assign to (4434): a key goes into one by joining it
with a set holding that key.

**`†d⸨k⸩` takes a key out, and answers what was there.**  It undoes a lookup, so it is written before one (4700) and answers
exactly what that lookup answers: the value where there was one, and whether there was one at all where the collection is a set.

```
let gone: u8 = †d⸨"one"⸩ ?? 0u8      ※ what was under the key, and the key is gone
†d⸨"two"⸩                          ※ or written as a statement, with nothing read
if †s⸨k⸩:                          ※ a set says whether it held the key
```

Nothing is reported for a value it leaves unread, which is what lets the one spelling be both the question and the line that only
removes -- where Python needs `d.pop(k)` for the one and `del d[k]` for the other.  It is one walk: the entry is found once, read,
and given up.  Taking out a key that is not there is nothing happening, and answers what a lookup of it would answer.

**It is writing, so it needs `mut`** (4598), which reading does not: a name holding the read-only type cannot empty a table under
everything else holding one.

**Putting anything in one needs `mut` in the type** (4598).  A collection type written `mut ⸨K: V⸩` says entries may be put in it
and one written `⸨K: V⸩` says they may not, which is the distinction `&mut T` and `&T` draw about a place and is drawn here for
the same reason: a collection is a handle, so the one who made it and the one who was handed it reach the one table, and what may
be done to that table is part of the type rather than of any one name for it.

```
let d: mut ⸨str: u8⸩ = ⸨"a": 1u8⸩   ※ entries may be put in it
d⸨"b"⸩ ← 2u8
let seen: ⸨str: u8⸩ = d              ※ the same table, read-only from here
fn holds(t: ⸨str: u8⸩, k: str) → bool    ※ and a parameter that promises not to write
```

`mut` stands where a type stands: before a variable's, before a parameter's, before a field's and before what a function answers
with.  On a variable or a parameter it is the one it has always been -- the name may be bound to something else -- and a
collection is where that one word says the second thing as well, there being two ways a handle can change.  A collection written
down is fresh and is the writable type, so a literal may be given to either.

**It goes one way only.**  A table that may be written stands wherever one that may only be read is wanted, as `&mut T` stands
where `&T` is wanted; the bits are the same handle and nothing is generated for the crossing.  A field whose type is a read-only
collection is not a place to put a different collection either (4599): what stands there is what every part of the program reads.

**Four operators join two sets**, with the meanings and the spellings Python gives them:

| Written | Holds |
|---|---|
| `a \| b` | everything in either |
| `a & b` | everything in both |
| `a ^ b` | everything in one and not the other |
| `a - b` | everything in `a` and not in `b` |

`=` and `≠` compare two sets, or two dictionaries, for holding the same thing.  Ordering is not defined on them: Python reads `<=`
as "is part of", and here ordering is about which of two comes first, which neither does.

##### Where one lives

A collection is a table in an **arena**, and `in` says which:

```
let a: ⸨u8⸩ = ⸨1u8, 2u8⸩ in scratch
let b: ⸨u8⸩ = ⸨2u8, 3u8⸩
```

What follows `in` is a variable of type `arena` (4446), and it is a name rather than an expression: what goes there is a place the
allocator keeps its state in, and a place is named rather than computed, for the same reason the left of an assignment is a name.
A collection written with no `in` comes out of `⎕heap`, the arena the compiler provides.

A collection made out of two others comes out of the same arena the first of them did, which is what keeps an answer where its
operands are.

##### One at the top level

A variable at the top level may hold a collection:

```
let counts: mut ⸨str: u64⸩ = ⸨"a": 1u64, "b": 2u64⸩
```

What the image carries is a word of nought, and what fills it is a constructor the compiler generates: a collection is a table in
an arena, made by running code, and a variable at the top level is bytes in the image.  One such constructor is generated per file
that has any, giving each of those variables its table **before the program's own constructors**, so that one of those may read a
table; within a file they are built in the order they are written, so one written in terms of another reads what that one was
given.

Everything a definition inside a function may say may be said here: a literal, an arena named with `in`, a call, an operator over
two collections.  And a name is a handle, here as anywhere, so two names for one table are two names for one table.

##### What one costs

The compiler decides this and the language says only what follows.  A value of a set or a dictionary type is where its table is
and nothing else, which is one word: how much it holds and how much room it has for more are in the table rather than beside it,
so that two names for one collection see one answer.  The table is elsewhere and is no part of the value, which is what lets a
collection be passed to a function and answered with like anything else.

What a key stands for may be a value of any size: an entry is a run of words, and a value of several goes in one as readily as a
value of one does.  What it may not be is something with no size to copy (4445).

**An entry has three states**, which is what taking a key out costs.  A probe walks a path until it finds an empty entry, so an
entry a key was taken out of is marked *given up* rather than emptied: a probe walks past it, and an insertion takes the first one
it walked past.  What decides when a table is rebuilt is what a probe has to walk past -- the keys it holds and the entries given
up -- and what decides how big the new one is, is the keys alone.  So a table that loses as many keys as it gains is rebuilt at
the size it has, rather than doubling for ever.

Compare: Python, whose semantics these are and whose `{}` and `set()` this replaces with one pair of brackets and a rule about the
first entry; Go, whose maps are built in and which has no set; Rust, where both are library types and neither has syntax.  A
language emitted by a generator wants the shape written down rather than constructed by a call, which is why these have syntax
here.

**What is not built yet** is a walk over a collection whose keys or whose values take more than one word: `foreach k, v := d` over
a `⸨str: str⸩` is refused by the backend (8501) where the same walk over a `⸨u8: u8⸩` is not.  Reading one by key, writing one and
counting one work whatever it holds; only the walk is waiting, and it is in the to-do lists.

### Statements

#### if

`if` runs one of several bodies, according to conditions asked in order.

```
if a < b:
    kept ← 1u8
elif a = b:
    kept ← 2u8
else:
    kept ← 3u8
```

**The condition stands on its own.**  There are no parentheses around it, because nothing needs them: what ends the condition is
the body, which begins with a colon or a brace, and neither can be part of an expression.  Parentheses may of course be written
inside a condition as they may inside any expression; they are simply no part of the `if`.

**The condition is of type `bool`** (4427).  A number is not a condition: C treats any scalar as one, which is what makes `if (x =
0)` compile there, and this language has nothing for a number to mean in that place.  Where the question is whether a number is
zero, `n = 0u8` asks it.

**Zero or more `elif` follow**, each with a condition of its own, asked only where the ones before it did not hold -- which is
what makes an `elif` an `elif` and not a second `if`.  **An `else` may follow them**, with no condition, and is the last arm there
is (3024): nothing can be asked after the arm that runs when nothing else did.

**An arm's body is written the way a function's is**: a colon and an indented block, or braces.  Both notations take the same
arms.

**`if` is an expression, and a statement where nothing wants its value**, exactly as `match` is.  Where a value is wanted of it,
every arm produces one and they are all of one type, and **there has to be an `else`** (4428) -- an `if` without one has a way
through that runs no arm, and that way would owe a value it has nowhere to get.  An arm ending in `return` owes none, leaving the
function rather than reaching the place the arms join.

```
let first: u8 = if a < b:
    4u8
elif a = b:
    5u8
else:
    6u8
```

A name bound outside the `if` and assigned inside one arm means what it reads afterwards, by the same machinery `match` uses: the
block the arms join at takes it as a parameter and each arm hands its own value over.

Compare: Python, whose `elif` this is and whose layout notation this shares; Rust, where `if` is an expression and the `else` is
required for the same reason, and which also refuses a non-boolean condition; C, where the condition is any scalar and the
statement has no value, so a separate `?:` exists for the case this covers.  There is no `?:` here and no need of one.

Whether `match` or `if` should be preferred where both would do is not something the language says.  `match` over a `bool` and an
`if` are the same two branches written two ways, and that is a place where two spellings mean one thing -- kept because the two
constructs are about different questions, one about which alternative a value holds and the other about whether something is so.

#### match

`match` takes a value's alternatives apart.  It applies to a **sum**, whose alternatives its definition lists, and to a
**result**, whose two are the answer and the error (4411).

```
match a ÷ b:
    u8(quotient):
        kept ← quotient
    ⊥:
        kept ← 0u8
```

**An arm names the type of the alternative it takes**, and binds what that alternative carries: `u8(quotient)` takes the
alternative whose type is `u8`.  Written without the parentheses it takes the alternative and binds nothing, which is how one
carrying nothing is taken (4416).  Naming a type the value has no alternative of is refused (4412).

That is why no two alternatives of a sum may have the same type: an arm could not choose between them.

**`⊥` (U+22A5 UP TACK) is the error arm of a result.**  A result's two alternatives may name one type -- `u8?u8` is a perfectly
good type -- so which arm is which cannot be said by naming a type, and this says it.  It is logic's "bottom", the proposition
that never holds, which is as close to "there is no answer" as a single character comes.  `⊥(name)` binds what the error carries
where it carries something, and `⊥` alone takes an error that carries nothing.  It is an arm of a result and not of a sum (4415),
a sum naming every one of its alternatives by a type.

**`match` also takes an enumeration apart**, whose alternatives are its values.  An arm names one of them -- `red` -- and binds
nothing, a value of an enumeration carrying nothing beyond being itself.

**`_` takes every alternative no earlier arm took**, and binds nothing (4422): the alternatives it takes may carry different
things or nothing at all, so there is no one value a name after it could stand for.  It works on every kind of value a `match`
takes apart.

**Every alternative must be taken, and none twice** (4414, 4413).  A value holds one of them, so a `match` that left one out
would be a program with nowhere to go when the value held it -- which is the thing a sum type exists to make impossible.  An arm
that takes nothing an earlier arm left -- which is what a wildcard written after arms that already take everything comes to -- is
reported rather than left standing (4417).

**An enumeration is taken apart by a chain of comparisons**, one per value an arm names, with the last arm -- or the wildcard,
where there is one -- reached by falling off the end of the chain.  A jump table is the other way and is a thing the compiler may
choose later; the language says only which arm runs.

**The arms stand where the statements of a body would**, in either of the two block notations, and an arm is a pattern and then a
body written the way a function's is -- a colon and an indented block, or braces.  Inside braces the arms follow one another with
nothing between them, each ending in the brace that closes it.

**`match` is an expression, and a statement where nothing wants its value.**  Written as a statement of its own the arms are runs
of statements like any other body and the value of each one's last statement goes nowhere.  Where a value is wanted of it -- as
the value a variable is given, as what an assignment writes, as what a function answers with -- **every arm ends in a statement
that has a value and they are all of one type** (4426), and the block the arms join at carries it out.

That is what lets an assignment be pulled out of the arms and written once:

```
let kept: u8 = match chosen:
    red:
        1u8
    green:
        2u8
    blue:
        3u8
```

The statements that have a value are the ones that may be the last of a function's body: an expression, and an assignment, which
stands for the variable it changed.  An arm ending in `return` owes none -- it leaves the function rather than reaching the place
the arms join.

A `match` written at the end of a line takes that line's end with it, the indentation closing after it, so nothing follows it on
that line and nothing separates it from the next.

**A name bound outside the match and assigned inside an arm** means what it reads: after the match it stands for whatever the arm
that ran gave it.  What makes that work is the block the arms join at taking the name as a parameter, which is also why the
unread-value rule says nothing about such a name while the arms are being checked -- whether an earlier value survives is a
question about paths, and that rule is a statement about a straight line of code.

Where every arm leaves the function there is no block to join at and none is made.

Compare: Rust's `match`, which this follows in shape and in insisting on exhaustiveness, and which writes `Pattern => expr` where
this writes a pattern and a body -- Rust's arms are expressions and these are statements.  Rust names a variant by its
constructor; here an alternative is named by its type, which is what removes the need for a constructor to exist before a value
can be taken apart.  Swift's `switch` is also exhaustive; C's `switch` is not, and falls through besides, which is the pair of
mistakes every language since has fixed.  Zig writes `switch` with `else` as the catch-all; this has none, deliberately.

#### while

`while` runs its body again for as long as the condition holds.

```
let total: mut u8 = 0u8
let n: mut u8 = 5u8
while n > 0u8:
    total ← total + n
    n ← n - 1u8
```

**The condition stands on its own.**  There are no parentheses around it, for the reason an `if`'s has none: what ends it is the
body, which begins with a colon or a brace, and neither can be part of an expression.

**The condition is of type `bool`** (4437).  A number is not a condition: C treats any scalar as one, which is why `while (n)`
counts down there and means nothing here.  Where the question is whether a number has reached zero, `n ≠ 0u8` asks it.

**The body is a statement list**, written in either notation, exactly as a function's body or an arm of an `if` is.

**A loop is an expression where something reads what it comes to**, and a statement everywhere else -- which is what an `if` is
too.  What it comes to is what a `break` hands it; the section on leaving a loop says how, and what the way through that runs the
body no times at all comes to.

**A name a turn changes is the same name on the next turn.**  The value one turn leaves is the value the next turn reads, and the
value the last turn leaves is what follows the loop reads.  That is what makes a loop able to count anything, and it is the same
rule an `if` follows for a name its arms assign -- said of a body that runs more than once rather than of one of several bodies
that run once.

The unread-value rule says nothing about such a name while the loop is being checked, for the same reason it says nothing inside
the arms of an `if`: whether an earlier value survives is a question about paths, and that rule is a statement about a straight
line of code.  The branch that starts the next turn reads every value it carries, so a counter a loop counts down is not a value
nothing reads.

**The body is a scope**, so a name defined in it is defined afresh on every turn and is gone after the loop.

**`unless` is the same loop with its condition read the other way round**: the body runs *until* the condition holds.

```
let it: mut = ⎕iter(l)
unless it:                            ※ until the walk is over
    it ← ⇧it
```

Everything else about it is `while`'s: the condition, the body, a label, a `break`, a `continue`, an `else` arm.  It is one word
rather than a `¬` because the conditions it is for are already the negative of what a reader means -- a cursor asked whether a
walk is over is the one there is -- and `while ¬done` reads as a double negative where `unless done` reads as a sentence.  It
takes a condition and not a binding (4705): `while x := ...` ends where there is no next value, and read the other way round that
would be a walk that stops before it starts.

Compare: C, C++, Go, Rust, Zig and Odin all have `while` (Go spells it `for`, Rust also has `loop`), and all but C and C++ insist
the condition is a truth value.  **Perl** and **Ruby** have `until`, which is this, and `unless`, which is the same word for the
one-armed `if`; **Haskell** has `until` as a function.  The word here is `unless` for both readings of the same negation, there
being no `if`-without-an-else to want it.

#### Leaving a loop and repeating it

`break` leaves a loop before its condition says to, and `continue` begins its next turn without running the rest of the body.
**Both name the loop they mean**, and the loop is named by writing `§` (U+00A7 SECTION SIGN) and a name after its keyword:

```
let found: mut u8 = 0u8
foreach §rows r := 0u8…4u8:
    foreach §cols c := 0u8…4u8:
        if c = 0u8:
            continue §cols        ※ the inner loop's own next turn
        if r × c = 6u8:
            found ← r × 10u8 + c
            break §rows           ※ leaves both loops at once
        if c > r:
            break §cols           ※ leaves the inner loop only
```

**The label is written every time** (3033), on the jump and on the loop alike.  What a jump with no label means is the loop
nearest to it, which is a thing that changes when a loop is put around it -- and putting a loop around something is what a
program that writes this language does.  Naming the loop makes the jump say what it means rather than where it stands, and makes
moving a body between loops either correct or reported rather than silently something else.

**The glyph is what keeps a label apart from what follows it.**  `while outer x` would otherwise be a loop over a name that is
true, and every language that puts a label here has needed a marker of some kind.  The section sign is what marks a named
division of a text, which is what a label is; it has no ASCII substitute, `§` being the only thing that says it and every
plain character that might have done being spent or wanted elsewhere.

**The label stands between the keyword and what the loop runs on**, which is where a reader looks to see which loop this is.  It
is written the same way on `while` in either of its spellings and on `foreach`.

**A jump names a loop it is inside** (4467).  Leaving a loop that has already ended, or one that has not started, is not a thing
that could happen, so a label that names no enclosing loop is an error rather than a jump to somewhere.

**No loop carries the label of a loop it is inside** (4468).  The inner one would hide the outer, leaving nothing that could name
the outer from within -- a name means the nearest thing it could mean.  Two loops neither of which is inside the other may share
a label, there being no place both can be named from.

**A label nothing names is reported** (4469, a warning).  It is there to be named; where nothing does, it says nothing about the
program, and where something was meant to, what that something names instead is a different loop.

**What `continue` does is what reaching the end of the body does**: the step the iterator takes where there is one, and then the
condition again.  For a `while` there is no step, so its next turn is its condition.

**A name the loop carries is handed over where the jump stands.**  The block a loop ends at is reached from the test and from
every `break`, so what follows the loop reads what the way actually taken left there -- not what the last turn began with.

##### What a loop comes to

**`break` may hand a value over**, and a loop something reads is an expression:

```
fn root_of(square: u8) → u8:
    let found: u8? = foreach §looking i := 0u8…10u8:
        if i × i = square:
            break §looking i
    found ?? 99u8
```

**A loop has a way through that no `break` took** -- the condition stopped holding, or the values ran out -- and that way has
nothing to hand over.  So **a loop with no `else` arm comes to a result**: the answer where a `break` gave one, and the fact that
there is none where the loop simply ended.  That is the same shape a division gives and is read the same way, with `??`, `?` or a
`match`.

**An `else` arm is what the ran-out way comes to**, and a loop with one comes to a plain value:

```
foreach §looking i := 0u8…10u8:
    if i × i = square:
        break §looking i
else:
    99u8
```

The arm runs where the loop ran out and not where a `break` left it, which is Python's `while ... else` -- the difference being
that here it gives the value that way through has, rather than merely running.  Where the loop's value is read the arm gives one,
and where it is not the arm is a run of statements like any other body.

**Everything a loop may come to is of one type** (4473): every `break` naming it, and its `else` arm.  The block the loop ends at
is reached down all of those ways and reads one thing, so there is one type for that thing to have; nothing is widened to make
two of them meet.  What the type is, is what the loop is being used as where that says, and otherwise what the first thing to give
one gave.

**A `break` hands a value over exactly where the loop's value is read.**  Where it is read, every `break` in the loop hands one
over (4470) -- a way out with nothing to give would leave the block after the loop reading a value that was never handed to it --
and something in the loop has to give one at all (4472).  Where it is not read, a value handed over here would be worked out and
thrown away, so writing one is reported (4471) rather than quietly dropped.

**A value handed over costs nothing beyond itself.**  It travels the way the names the loop carries travel, as one more thing the
block after the loop takes; a loop with no `else` arm makes the result where the `break` is and the failure where the test leaves,
which is two instructions in the two places rather than anything on the path round.

Compare: Java, JavaScript, Go, Perl and Odin, all of which allow a label and all of which make it optional, so that the common
case is the one that changes meaning when a loop is inserted; Ada, whose `exit Outer when ...` names the loop and which is the
closest to this in spirit for the jump itself; C and C++, which have no label at all and reach for `goto` instead; and Python,
which has neither a label nor `goto`, and where leaving two loops means a flag or a function.

On the value: **Rust** is where `break 'label value` comes from, and it is the same idea -- with the difference that Rust gives
it only to `loop`, whose body has no way of ending on its own, so the question of what the ran-out way produces never arises.
Here it arises for every loop, and the answer is the `else` arm or a result.  **Python**'s `while ... else` is where the arm comes
from, and there it only runs; giving it the value of that way through is what makes the two features one feature instead of two.
**Zig** writes `for (xs) |x| { ... } else value`, which is this, and reaches it from the same place -- an `else` that had to mean
something when the loop is an expression.  **Kotlin** and **Scala** make most things expressions but leave loops out, so a search
over two dimensions goes back to a variable set before the loop.  **Common Lisp**'s `loop ... finally (return v)` and its
`return-from` do all of it and more, in a sublanguage of its own.

#### defer

`defer STATEMENT` puts a statement off until **the block it is written in is left**, whichever way that happens: off its end, by a
`return`, by a `break` or `continue` leaving it for a loop around it, and by a `?` handing a failure on.

```
let scratch: mut arena = ⎕arena
defer ⎕empty(scratch)
let both: str = first ⧺ second in scratch
if both = "":
    return 1u6                  ※ scratch is given back here
…                               ※ and here, at the end
```

**The last put off runs first**, and only what was reached runs: a `defer` the block never got to has put nothing off.  What a block
comes to -- the value of its last statement, or what a `return` answers -- is worked out **before** what was put off runs, so a
deferred `⎕empty` cannot take away the value it was meant to protect, and a deferred assignment does not change what was answered.

**What may be put off is something to do**: an expression, or an assignment.  Not a way out of anything -- a deferred statement runs
while the block is already being left, so a `return`, a `break`, a `continue` or a `?` in one would leave twice -- and not a `let`,
whose name would be gone as soon as it was made, nor another `defer` (4612).  A deferred statement means what it would have meant
where it was written: a name a block inside has taken over since does not change it.

It is lowered at every way out, so the code is repeated rather than jumped to.  A mistake in it is still one mistake and is
reported once.

Compare: **Go**'s `defer`, whose name this is, runs when the *function* returns and takes a call; deferring in a loop piles calls up
until the function ends, which is rarely what was meant.  **Zig**'s `defer` is this one -- the end of the scope, any statement, last
first -- and its `errdefer` runs only on the way out with an error, which this does not have (yet).  **Swift**'s `defer` and **D**'s
`scope(exit)` are the same as Zig's, D adding `scope(failure)` and `scope(success)`.  **Odin**'s `defer` is Zig's.  **C++** and
**Rust** say this with a destructor, which runs at the end of the scope for every object that has one, and which a program writes
once per type rather than once per use; **Python**'s `with` and **Java**'s `try`-with-resources are that with a protocol.  **C**
has `goto cleanup`, and GNU C `__attribute__((cleanup))`.  A destructor would need a type of its own for every resource; here the
one resource that wants it, an arena, is given back by one line beside the line that makes it.

### Names the compiler provides

A name beginning with `⎕` (U+2395 APL FUNCTIONAL SYMBOL QUAD) belongs to the compiler.  A program may read and assign the ones
that exist and may **not define one** (4219).  That is what lets the compiler add another later without taking a name away from a
program written before it existed -- the problem every language has that puts its own names in the same namespace as a program's.
APL marks its system names with the same glyph for the same reason, and `⎕CT` is the one this language's tolerance is modelled on.

There are three so far.

| Name | Type | Holds |
|---|---|---|
| `⎕tolerance` | `mut f64` | the tolerance the approximate comparisons measure against; 10⁻¹³ until a program sets it |
| `⎕heap` | `arena` | the arena everything that allocates and says no other one comes out of |
| `⎕environ` | `⸨str: str⸩` | the environment the process was started with, read-only |

`⎕tolerance` is a variable and not a number built into the compiler because the right tolerance depends on how far the values being
compared have travelled, which is the program's business and not the language's.

**`⎕environ` is the environment**, a name standing for what it stands for:

```
let home: str = ⎕environ⸨"HOME"⸩ ?? "/"
```

What the kernel leaves after the arguments is a run of `NAME=VALUE` strings; each is split at the first `=` where its bytes
already are, so a name and a value point into the string the kernel handed over and nothing is copied.  A variable with no `=` in
it is a name standing for nothing, and one written `NAME=` is a name standing for text of no length -- which is a value, and is
not the same answer as having none.

It is a name the compiler provides rather than a field of what a program is started with, because it is a table and not a run of
words: only the entry point can reach the strings and only the compiler knows how a table is laid out, so what builds it is
generated and called before anything of the program runs -- before the constructors, so that one of those may read it too.

**Nothing may be put in it** (4598, 4599).  Its type is `⸨str: str⸩` and not `mut ⸨str: str⸩`, so an entry assignment is
refused, and the name is not a place to put a different dictionary: every part of a program reads the one environment.  **What it
says is what the process started with** and goes on saying it -- the table is built once and nothing writes it afterwards, so a
change to the process's own environment, whenever the language grows a way to make one, would leave this dictionary as it is.
There is nothing to start a process with yet, which is where C's `setenv`, Go's `os.Setenv` and Python's writable `os.environ`
differ, and Rust's `set_var`, which is `unsafe` for a reason that has nothing to do with this one.

**While the compiler is working something out it is the compiler's own environment**, which is what a build function reads: a
build is run by the compiler, so the process it asks about is the one whose command line the reader typed.  `std.Build.env` is
that same dictionary under the name the field has.

**A program that never names one carries nothing for any of them.**  Each is made on first ask, so what is not asked for is not
there: no tolerance in the data, no arena, and for the environment no table, no builder and nothing asked of the system.

Compare: APL's `⎕CT`, which is a *relative* tolerance -- two values are alike when they differ by less than `⎕CT` times the larger
of them.  That is the better rule for values of widely differing size and costs a multiplication and a magnitude more than this
one; it is not what is implemented, and the entry in the to-do list holds the question of whether it should be.

### File Structure

At the top level of a file one can find:
- module handling
- compile-time expressions as assertions and contracts
- type and enumeration definitions, which are read before anything else in the file: a function's signature may name a type defined below it, and
  a type definition may name one defined below itself
- variable definitions
- function definitions


#### Settled while compiling

**`comptime` before `foreach` or before `if` says that the compiler answers it**, and that what it answers leaves nothing behind.

##### comptime foreach

**`comptime foreach` walks a tuple**, and it is the one loop whose turns may differ in type.

```
let values: 〈u8, u16, u32〉 = 〈1u8, 2u16, 3u32〉
comptime foreach v := values:
    ...                          ※ v is a u8, then a u16, then a u32
```

A tuple's members are of whatever types they were written with, so a loop over one cannot be one body run again: the name would
have to be of one type and there is no one type.  **The body is written out instead, once per member**, each with the name
standing for that member and of that member's type.  There is no loop in what comes out -- no counter, no branch backwards, no
test -- which is also why such a loop has nothing to hand over and takes no name for a `break` to call it by.

A tuple is the only thing it walks (4503); everything else holds values of one type and is walked by an ordinary loop.  And a
tuple is walked only this way (4504), for the same reason.

##### Lifting

**`⌜x⌝` lifts what is written between the brackets out of the program and into the compiler**: `⌜u32⌝` is the type and not a
value of it, and `⌜a⌝` is the name and not what it stands for.

```
⌜u32⌝                          ※ the type
⌜u8⟦3⟧⌝                        ※ a type written out in full
⌜Colour⌝                        ※ a type the program defined
⌜a⌝                             ※ the name, whatever it is of
```

**The brackets are what keeps the grammar context-free, and that is the whole reason they are there.**  A type's name and a
value's name are both identifiers; a type written out in full -- `u8⟦3⟧`, `〈u8, u16〉`, `⸨u8: u16⸩` -- is not an expression at
all; and a type the program defined is a name this compiler cannot tell from a variable's until it has looked it up.  Without
something saying "what follows is lifted", the parser would have to know what the names turned out to mean before it could read
them, which is not a thing a parser can do.  With it, the reading is settled by the brackets and *which* of the two it was is a
question about the program.

**Three places read a lift**, and a lift anywhere else is refused (4514) because there is nothing for it to be at run time:

| Written | Answers |
|---|---|
| `⎕typeof(⌜a⌝)` | the type of what was lifted |
| `⌜A⌝ = ⌜B⌝`, `⎕typeof(⌜a⌝) ≠ ⌜B⌝` | whether two types are the one type, in a question the compiler settles |
| `⌈⌜T⌝`, `⌊⌜T⌝` | the largest and the smallest value the type `T` has |

A fourth reading is the same sentence said of a program rather than of a type: in the body of a `macro` or a `comptime fn`, marks
holding something that is not a type hold **a piece of the program**, which is what a macro is handed and what it writes.  See
[Macros](#macros).

**`⌈⌜T⌝` and `⌊⌜T⌝` are the same operators and the same word** they have everywhere else, asked of a type rather than of
something holding several values.  `⌈⌜u8⌝` is 255, `⌊⌜i8⌝` is −128, `⌈⌜u3⌝` is 7, `⌈⌜char⌝` is U+10FFFF.

```
let most: u8 = ⌈⌜u8⌝
let least: i5 = ⌊⌜i5⌝
```

**A floating-point type's smallest is the most negative number it holds**, not the smallest positive one.  C++ calls the second
`min()` and the first `lowest()`, and the number of programs that have reached for `min()` and got a tiny positive number is the
argument for not repeating it: `⌊` means the smallest, and there is no value below it.

Compare: **C++26**'s `^^`, which lifts a name into a reflection and which this follows in shape and in reason -- there too the
problem is that a name is a name and the grammar must not have to know what it names; **C++**'s `std::numeric_limits<T>::max()`
and `::lowest()`, which `⌈⌜T⌝` and `⌊⌜T⌝` are; **Zig**, whose `@TypeOf(x)` takes an expression and whose `std.math.maxInt(T)`
takes a type, the two written differently because Zig's types *are* values there; **Ada**, whose `T'First` and `T'Last` are these
two under an attribute notation that reads better and costs a second kind of name; and **Rust**, whose `T::MAX` is an associated
constant, which needs types to be able to carry those.

##### comptime if

**`comptime if` asks a question the compiler answers**, and **`⎕typeof` of a lift is what it asks about**:

```
comptime if ⎕typeof(⌜v⌝) = ⌜u8⌝:
    bytes ← bytes + v
comptime elif ⎕typeof(⌜v⌝) = ⌜u16⌝:
    halves ← halves + v
else:
    words ← words + v
```

**No test reaches the program.**  An arm the compiler settled as true is the whole of the `if` -- what follows it cannot be
reached -- and one it settled as false is not there at all.  That is not an optimization: the arms it did not choose are *never
lowered*, which is what lets them be of types that would not otherwise agree, and is the whole point of the construct.

**`comptime` stands before the keyword of each arm it applies to**, and not once for the whole `if`.  Which arms the compiler
settles is a property of each condition, so a chain may mix them: a `comptime if` whose condition is false simply goes on to
whatever follows, settled or not.

**What may be asked** is whether two types are the one type -- two lifts, or `⎕typeof` of one and another, joined with `=` or
`≠` -- and those answers joined with the logical operators.  Anything about a value is a question about what the program does and
is reported (4502).

**`⎕typeof` takes a lift** (4512) **and answers a type, and a type is not a value.**  There is nothing for one to be at run time,
which is why the representation is deliberately unspecified and why it may stand nowhere else (4505).  What it is of is read off
the expression without lowering it, and asking what a name is of counts as reading that name.  Asking it of a lifted *type* is
asking for the type of a type, which is refused (4513): the answer would be what was written.

Compare: **C++**, whose `if constexpr` this is and whose `decltype` `⎕typeof` resembles -- with the difference that there the
discarded branch is still parsed and instantiated unless the enclosing thing is a template, where here it is simply not lowered;
**Zig**, whose `comptime` and `inline for` are the same two constructs under one keyword and whose `@TypeOf` is `⎕typeof`;
**D**, whose `static if` and `foreach` over a tuple are the direct ancestors of both; and **Rust**, which has neither and reaches
for macros and traits instead.

##### Asking what a type is

**Three questions the compiler answers about a type**, which is what lets one generic definition do a different thing for a
record than for a number -- the language having no overloading, so that a glyph has one meaning per number of operands and a
`comptime if` arm is what stands in for a second definition.

| Written | Answers | Where it stands |
|---|---|---|
| `⎕isrecord(⌜T⌝)` | whether `T` is a record | a condition `comptime` was written on, and nowhere else (4605) |
| `⎕typename(⌜T⌝)` | the type written out, as `str` | anywhere a value stands |
| `⎕fields(v)` | a record's fields as a tuple of pairs | anywhere a value stands |

```
type Point = x : u8 ; y : u16

fn `⍕`(v: T') → str:
    comptime if ⎕isrecord(⌜T'⌝):
        let opened: str = ⎕typename(⌜T'⌝) ⧺ "("
        let out: mut str = opened
        comptime foreach f := ⎕fields(v):
            if out ≠ opened:
                out ← out ⧺ ", "
            out ← out ⧺ "." ⧺ f⟦0⟧ ⧺ " ← " ⧺ ⍕f⟦1⟧
        out ⧺ ")"
    else:
        …
```

**`⎕isrecord` is a truth the compiler settles and not a value**, so it may stand only where `comptime` says the compiler is
answering -- the rule `⎕typeof` follows, for the same reason: there is nothing for it to be at run time.  A program that wants the
answer where a value goes has already been told it by the arm it is standing in.

**`⎕fields` answers a tuple of pairs**, each pair a field's name and its value.  A tuple because `comptime foreach` walks one and
because the fields are of different types, which is the one thing a tuple is for; **pairs rather than two tuples** because two
walks cannot be taken together, so a name and its value travel as one turn.

**In the order the type declares them**, which is not the order they lie in: the compiler may reorder a product's fields, and what
a reader of the answer wants is what the program wrote.  **It costs nothing beyond the names**: a record already travels as its
fields, so a pair is the field read where it lies with a literal beside it.

**Only a record has fields to answer** (4606).  A sum holds one of its parts rather than all of them, so what its fields are is not
a question with one answer; a tuple's members have no names; and everything else is one value.  A generic definition asks
`⎕isrecord` first and reaches the refusal only where it did not.

**`⎕typename` is defined for every type**, not only a record's: `⌜u8⌝` is as much a type as `⌜Point⌝`, and what wants the name is
a value's text, which puts it in front of the fields.  It takes a lift (4607) for the reason `⎕typeof` does.

What this adds up to is that **a generic definition can write the text of a type it was never told about**, recursively -- a field
that is a record is written by the same definition -- and in the notation the language itself uses:

```
Line(.from ← Point(.x ← 1, .y ← 234), .to ← Point(.x ← 56, .y ← 7))
```

Compare: **Zig**, whose `@typeInfo` this is a narrow slice of -- one union with a payload per kind of type, walked at `comptime`
exactly this way, and whose `std.fmt` prints a struct by this method; **Odin** and **D**, which have full compile-time
introspection over a type's members; **Rust**, which has none and derives an implementation with a procedural macro instead;
**Go**, whose `reflect` does this at run time and pays for it there; **C++26**, whose reflection answers a `std::meta::info` a
`consteval` function takes apart, which is the same shape arrived at thirty years later; and **C**, where the question cannot be
asked.

What is deliberately absent is **a general question about what kind of type something is**.  `⎕isrecord` is one predicate, and a
second kind -- a sum, an array, an enumeration -- would want an answer naming which, rather than a predicate each.  That wants a
`comptime if` that can compare something other than two types, and it waits for a use.

#### foreach

`foreach` runs its body once for each value something gives out.

```
let total: mut u8 = 0u8
foreach i := 0u8…5u8:
    total ← total + i
```

**It shares `let`'s shape**: one or more names, a colon, an optional type, an equal sign, and what the loop takes its values
from.  **The colon is always written**, exactly as in a variable -- with neither a type nor a qualifier the two characters read
as `:=`, and they are the same two tokens either way.  It binds a name the way `let` does, so it is written the way `let` is;
two spellings of one thing is what this language does not have.

**A type written on the name says what a turn gives** -- and what the loop takes its turns from can take its own type from that:

```
foreach x: u8 = [1, 2, 3]:     ※ three bytes: the type on the name settles them
foreach y: u16 = ⟦10, 20⟧:       ※ and the same for an array
foreach r: u8⟦2⟧ = ⟦⟦1, 2⟧, ⟦3, 4⟧⟧:   ※ a turn of a table gives a row, so the row is what is written
```

Without it the numbers inside would have nothing to say what they are, the thing they are written in being the only thing that
could say and having been asked first.  A range already worked this way, and now everything the loop can walk does.

**What it takes its values from has to be an iterator** (4438).  An **iterator** is a value with a `next` answering the next value
or a failure; the failure is what ends the loop.  The result type is how that is said, and it does not surface: the names are
bound to what there was, and a loop over something with nothing in it runs no turns.

**Four things are iterators**, and none of them is called: each one's `next` is lowered where it is asked.

| Written | A turn gives |
|---|---|
| a range | each whole number it stands for |
| an array of one dimension | each element |
| an array of more | each row of the outermost dimension |
| a set | each key it holds |
| a dictionary | a key and what it stands for, as a tuple |

```
foreach x := a:           ※ every element of a vector
foreach row := m:         ※ every row of a table
foreach k := s:           ※ every key of a set
foreach k, v := d:        ※ every key of a dictionary, and what it stands for
```

**Iterating an array is over its outermost dimension**, so a turn of a `T⟦2,3⟧` gives a `T⟦3⟧`.  Row-major is what makes that
cheap: a row is a run of elements, so naming one is arithmetic on the place and no copy at all.  An array whose type does not say
its shape is walked the same way, which is what lets a function take one of any length and still say what to do with each element.

**A table is not walked in the order its keys were put in it**, and it has no such order: where a key lands is where its hash puts
it, and growing the table moves everything.  Python promises the order keys were added in and pays for it with a second array; Go
deliberately randomises its walk so that no program can come to depend on an order it never promised.  This promises nothing.

**Several names take each value apart**, the way several names take a tuple apart in a definition.  That is the whole of what
`foreach k, v := d:` is -- a dictionary gives a key and a value together as a tuple, and two names take a tuple apart everywhere
else too, so the form needs nothing of its own.  Over something that gives one value, several names are refused.

**`_` is the name that is not a name**, as it is in a `match` arm: the loop runs a turn for each value there is and the value
itself is not wanted.  Nothing is bound, so nothing is reported as a value nothing reads.

##### Counting the turns

**`⎕enumerate(x)` makes something to walk out of something a loop could already walk**, and what a turn gives is the count and
what the walk gave, as a tuple.

```
foreach i, x := ⎕enumerate(v):          ※ 0 and the first element, 1 and the second, …
foreach n, y := ⎕enumerate(l, 1u8):     ※ counting from one, in a byte
```

**Two names take that apart**, which is what two names already do everywhere a tuple is bound -- so it needed nothing of its own
beyond the counting.  It is the iterator the loop would have had with a number carried beside it, so it works over everything a
loop works over: an array, a list, a string, a set, a range.

**A second argument says what to count from**, and **its type is the type the count has**: `⎕enumerate(v, 1u8)` counts in bytes
from one, and without it the count is a `u64` from zero.  There is no third thing to say -- how far the count moves each turn is
one, which is what counting is.

**The count goes up by one and the addition is an addition**, with the check every other one carries.  So counting a hundred
things from a `u6` stops the program, which is what says the type was too narrow rather than quietly starting again from zero.

**It stands where a loop takes its values from and nowhere else** (4515).  What it makes is an iterator, and a loop is the only
thing that takes one: there is no type for an iterator here and nothing else to do with one.

A dictionary is the one thing it cannot count, and not for a reason of its own: a dictionary's turn is already a pair, so
counting it would make a tuple holding a tuple, and a value of one of those is not a thing this compiler can hold yet.

Compare: **Python**, whose `enumerate` this is, including the optional start; **Rust**, whose `.enumerate()` is a method on the
iterator and whose count is always a `usize` from zero, so counting from one is a `.map` after it; **Swift** and **Kotlin**,
whose `enumerated()`/`withIndex()` are the same thing under longer names; **Go**, where `range` gives the index without being
asked, which is convenient until the index is not wanted; and **C++23**'s `views::enumerate`, which is this as a range adaptor.

**`while` written with a binding is the same statement.**

```
while i : u8 = 0…5:
    total ← total + i
```

After `while` the colon has to be written, because a name on its own followed by a colon is a condition with a body after it; the
type may still be left out.  The two spellings exist because the instruction that asked for the loops asked for both: `foreach`
says what the loop is, and `while` says that the two kinds of loop are one construct with two ways of deciding when to stop.

**The name is bound afresh on every turn** and is gone after the loop.  It is never `mut`: what it stands for is what the turn
gave, and the next turn gives another.

**A label goes after the keyword**, before the names: `foreach §rows r := 0u8…4u8:`, and likewise `while §rows r: u8 = …`.  What
it is for is in the section on leaving a loop and repeating it, above.

Compare: Python's `for x in r`, which this follows in meaning -- including `for k, v in d` taking a pair apart, which is where the
two-name form over a dictionary comes from; Rust's `for x in r` over anything that is `IntoIterator`; Go's `for i, v := range`,
which spells the pair as two results rather than as one tuple; Zig's `for (0..n) |i|`.  What none of them has is the second
spelling, and what this has instead of Rust's trait is a single iterator protocol the compiler knows -- until a program can write
one, there is nothing for a trait to abstract over.

#### Attributes

An attribute states something about an object that is not part of the object's own description: how a function takes part in the
life of the program, which foreign convention it follows, where a variable is placed.  Attributes apply to any kind of object --
functions, types, variables, modules -- and not only to functions.

An attribute list is written before the definition it applies to:

```
@[NAME]
@[NAME, NAME(ARGUMENTS), ...]
```

Several attributes may be given in one list, separated by commas.  **One list, and only one, may precede one definition.**  Two
lists attached to one thing say exactly what one list holding both would say, and a language that admits two shapes for one
meaning makes every reader and every tool learn both.  A blank line between two lists does not make them two things, because it
changes nothing about what they attach to; both are still read as attached to the definition that follows, and both are refused.
The exception this rule leaves room for is an attribute that attaches to nothing -- an instruction to the compiler rather than a
description of what follows -- which may stand before a list without being part of it.  There are none yet.

Arguments are written as in a call: positional arguments first, named arguments after.  Each line below is one list on its own,
shown as an example of a shape; they are four separate examples and not four lists before one definition.

```
@[startup]
@[test(build), inline]
@[cdecl(variadic=false)]
@[align(64), section(name=".hot")]
```

The parentheses are how an attribute carries its arguments, so **an attribute carrying none is written without them**: `@[inline]`
and not `@[inline()]`.  This holds even where the attribute could have taken arguments and they were all left to their defaults,
which is exactly the case where the two spellings would otherwise both be available and mean the same thing.  For the same reason a
list holds at least one attribute: `@[]` says what writing nothing says.

Both rules follow from one principle, and the principle is worth stating on its own because it governs more of the notation than
attributes.  **Where two ways of writing something would mean the same thing in every respect, the language admits one of them.**
A second spelling buys nothing and costs everyone who reads the language: a person learns two shapes, a tool matches two shapes,
and a program comparing two sources has to know the two are the same.  This does not narrow what can be said -- every one of these
rules leaves a shape that says exactly what the refused shape said -- and it is not applied where the shapes differ in meaning,
however slightly.  A type written out where it could have been taken from the value is not a second spelling of the same thing:
it says the type, and says it whatever the value later becomes.

Each attribute declares the kinds of object it accepts and the parameters it takes, so the compiler checks the number, the order,
the names and the kinds of the arguments.  An attribute name the compiler does not know is an error, never an ignored annotation:
a misspelling must not be able to silently drop a property the program depends on.

##### Calling Conventions

**A function follows the language's own convention, `pl4g`, unless it says otherwise.**  The specification says in so many words
that the convention need not match the system's and that it may differ between the functions of one compilation, so it does: which
registers carry the arguments, which carry the answer, and which a function hands back as it found them are the compiler's to
choose, and it chooses them per function.

```
@[cdecl]
fn write(fd: i32, buf: u64, n: u64) → i64:
    …
```

**`@[cdecl]` says the function follows the one this system uses**, whatever this architecture calls it, and that it keeps its plain
name.  Both halves are the same request: the point of asking for the system's convention is to be called by, or to call, something
that has never heard of this language, and such a thing cannot be expected to know how a name is mangled either.
`@[cdecl(variadic=true)]` says a variadic one.

**A call is placed by the convention of the function being called.**  Which register an argument goes in is the callee's to say,
not the caller's, so a program may hold both kinds of function and call each the way it expects.

**How a function hands back an answer of more than one value is the function's own choice**, and part of its convention for the
same reason the argument registers are: what a caller has to do to receive an answer is settled by the callee and by nothing else.
There is one choice so far and it is what every function makes:

| | |
|---|---|
| up to two values | the registers the convention answers in |
| more than two | storage the caller provides |

Two is the number because it is what the three system ABIs answer in registers as well, so the first choice is the familiar one
rather than an arbitrary one.  **Storage the caller provides** means what it says: the caller makes a place, hands it over, and
reads the answer out of it when the call comes back; the callee writes the parts into it rather than answering with them.  None of
that is written down by the program -- the place is not an argument any program can pass -- and none of it is visible in what the
program means.  A function answering with `〈u8, u16, u8, u8, u8〉` is called and read like any other.

A second choice would be a second row of that table and nothing else moved, which is the point of saying that the choice belongs
to the function: a program that wants one will say so on the function, and every caller will follow.

**What `pl4g` is, is the compiler's business and may change**, which is what makes this worth having: a convention the language
does not describe is one the compiler can improve without any program being rewritten.  What it is today, on every target, is a
convention whose argument registers begin where the answer comes back, so that a function answering with what it was given has the
value where it has to be already.  `fn f(p: u8) → u8: p` is one instruction: `ret`.

Compare: C, where the ABI is the platform's and a compiler may not touch it; C++, where the same holds and the name is mangled so
that overloads can coexist; Go, which changed its own convention from the stack to registers in 1.17 precisely because nothing
outside the toolchain depended on it; Rust, whose `extern "C"` is this `@[cdecl]` and whose default `extern "Rust"` is explicitly
unspecified for the same reason.  This language is emitted by generators and compiled whole, so the freedom Go and Rust reserve is
the ordinary case here and the system's convention is the exception asked for by name.

##### Functions defined somewhere else

**A function with no body is the declaration of one defined somewhere else**, and `@[external("SYMBOL")]` is what says where:

```
@[external("pl4g_io_write"), impure]
fn io_write(r: &mut Ring, fd: i32, at: u64, len: u64) → i64
```

What says there is no body is that **the line ends after the header**.  A body begins with `:` or `{`, so the two readings never
both hold; anything else after the header is neither, which is the error it already was.  A function with neither a body nor the
attribute is refused (4593) and one with both is refused as well (4594): a body and a definition elsewhere are two answers to
"where is this", and exactly one may be given.

**What may be named is what the compiler carries.**  There is no linker and nothing else to reach, so the symbols `@[external]`
accepts are the ones the packaged runtime defines (4595) -- which is what makes a name that will not be there something said where
it is written rather than left to whatever reads the image.  The call follows the system's convention, which is what lets the two
sides agree without either knowing how the other compiles a call of its own.

**Such a function is impure by being external**, so `@[impure]` need not be written and a pure function that calls one is refused
(4480).  Nothing here can see what it does, so it is taken to do everything -- the rule a call through a value already follows,
asked of a call to a body that is not in this compilation.  Believing one pure would let a call nobody read be dropped, and the
I/O would go with it.

**`@[abi]` on a record says it is laid out the way one compiled by something else is**: the fields are never reordered, and
**only a reference to one crosses a call** (4596).  Something compiled by something else passes a record by rules of its own --
rules this compiler does not follow and does not have to, a reference being passed the same way by every convention there is.
`@[abi]` on a *function* is the same request about the same thing, and says which convention by name; written with no name it says
the system's, which is what `@[cdecl]` says.  **The name has to be one the target knows** (3210): `pl4g` and `cdecl` are names
every target answers to -- the one being the language's own and the other "whatever this system calls C" -- and anything else is
asked of the target being compiled for, which says what it knows and is told in the diagnostic.  A name one architecture knows is
in general a name another does not, so `sysv64` written for AArch64 is refused rather than quietly meaning the language's own
convention under a name nothing would mangle.  **A name on a record says nothing** (3211) and is refused as well: how a record is
laid out is one thing and the same whoever compiled the other side, whereas a convention is about how a function is *called*.

Compare: **C**, whose `extern` declaration this is, with a linker behind it rather than a compiler that carries the code;
**Rust**, whose `extern "C"` and `#[repr(C)]` are this pair almost exactly; **Zig**, whose `extern fn` and `extern struct`
likewise; **Go**, whose cgo compiles the C as part of the build, which is the answer not taken here.

##### Exporting and Being Visible

**Nothing a program defines is let out of it.**  There are two ways out and they are different questions, so there are two
attributes and neither implies the other:

```
@[export]
let lent: u8 = 7u8          ※ a file importing this module may name it

@[visible]
let offered: u8 = 8u8       ※ the finished image offers the symbol

@[export]
@[visible]
let both: u8 = 9u8          ※ both, said separately

let private: u8 = 10u8      ※ neither
```

`export` is about the language: it decides what a file importing this module may name, and nothing else of the module can be
named.  `visible` is about the image: it decides whether the symbol table of the finished binary offers the definition to something
linked or loaded beside it.  Either attribute applies to a function and to a variable alike.

They are separate because a definition may be wanted outside the program without being part of what its module lets in -- an entry
point a loader calls, say -- and a module may lend something to the files that import it that no binary need ever name.  Keeping
them as one attribute means a module cannot lend anything without also offering it, and a definition it lends is then reachable
from outside and cannot be left out of the image, so a library module carries everything it defines whether the program uses it or
not.

That is what separating them buys, and it is visible in the program: a definition a module lends and nothing imports is a
definition nothing reaches, and it is not in the image.

The default is the one worth having by default.  What a module lends is its interface, and an interface is worth stating; what it
does not lend it can change freely.  C has the opposite default, with `static` as the exception, which is why a name never meant to
be part of an interface so often becomes one by accident.  Compare Rust, which draws this same line -- `pub` for what a module
lends and a separate attribute for what a binary offers -- with Go, which has one rule for the first and leaves the second to the
linker.

`visible` decides two further things about a name in the generated program: how widely its symbol is bound, and how far it is
visible.  Those are different questions too -- the binding says whether the name is one among many in this program or one the whole
program shares, and the visibility is the part that still says so if something later makes the symbol global.  What is visible is
bound globally and left visible; what is not is bound locally *and* marked hidden.

##### Quieting a Diagnostic

Two attributes keep a diagnostic from being reported for the construct they are attached to.  They differ in one thing: what
happens when the diagnostic does not arise.

```
@[ignore(NUMBER)]     ※ the diagnostic is not reported, whether or not it arises
@[expect(NUMBER)]     ※ the same, and the construct is asserted to raise it
```

```
@[startup]
fn main() → u6:
    @[expect(4006)]
    let a: mut u8 = 5u8
    a ← 4u8
```

In the layout notation the attribute stands on its own line, indented with the statement it belongs to; that indentation is what
says which statement that is.  In the brace notation it simply precedes the statement.  Either may be given more than once in the
one list -- `@[ignore(5002), expect(4006)]` -- since one of them names one diagnostic and a construct may raise several; they are
not written as a list apiece, which is the general rule about attribute lists and not something about these two.  The number must
be one the compiler can emit.

What is said covers the construct it is attached to.  For a definition it also covers the variable being defined for as long as
that variable exists, because not everything a definition raises is raised while the definition is being read: that nothing ever
reads the value it gives is only known once the variable is gone.

**An assertion that nothing meets is an error.**  `expect` says what a construct raises, so that a reader knows why it is there and
so that the compiler can tell when it no longer is.  One that is never met is stale -- the code changed, or the number was wrong --
and it now hides nothing while saying something false.  It is an error rather than a warning because the attribute exists to be
relied upon: what it claims is either so or it is not.

Where the intent is only to keep a diagnostic quiet, whether or not it arises, `ignore` is what says that and says nothing further.

Rust draws the same distinction, between `#[allow]` and `#[expect]`; it reports the stale case as a warning.  C, C++ and their
compilers have only the permitting form -- `#pragma GCC diagnostic ignored` and the like -- which is why a suppression there can
outlive what it suppressed with nothing to notice.

**An error may be quieted too, by either attribute, but the construct it is attached to is then discarded.**  A definition that
raises an error cannot be compiled, and half of one would be worse than none: the function or the variable is left out of the
program entirely.  What follows from leaving it out is not itself hidden -- discarding the only startup function is still reported
as there being none.

**Except where the error is about how the code is written.**  A few errors say that something the compiler could perfectly well
compile breaks a rule about how programs are to be written -- a statement whose value is not used is the first of them.  The
construct is whole, so quieting such an error leaves it standing and the program is compiled with it.  The distinction is not a
judgement made case by case: each diagnostic states in the shared catalog which kind it is, so every implementation of the language
draws the line in the same place.  Without it the attribute would be a way to delete code, which is the opposite of what someone
writing `@[ignore]` on a line they meant to keep is asking for.

Considered were `#[...]` (Rust), `[[...]]` with namespaces (C++11, and the aspect clauses of Ada), `{. .}` pragmas (Nim),
`@(...)` (Odin), a bare `@name` in the manner of a decorator (Python, Java, D), `pragma` statements (Ada), and magic comments
(Go's `//go:...`).  The chosen notation takes the list-in-brackets shape from Odin and Rust and puts it behind `@`, which no other
construct uses: a parser can commit on the first character, which keeps the grammar context-free, and `#` stays free for a future
feature.  Parametrization follows Python and C#, so that the common one-argument case stays terse while an attribute with several
parameters remains self-describing.


#### Variable Definition

A variable is defined with:

```
let NAME: TYPE = VALUE
let NAME: mut TYPE = VALUE
```

The colon is always written.  The type after it may be left out, in which case the variable's type is the type of its value:

```
let counter: u64 = 0u64     ※ the type is stated
let total := 42u8           ※ the type is the value's, which its suffix names
let spaced : = 42u8         ※ the same; ':=' is two tokens, not one
```

**A variable cannot be changed unless its type says `mut`.**  That is the default, and it is the one worth having by default: a
name that keeps its value can be reasoned about wherever it appears, which is what the language's functional style calls for, and a
name that does not is worth marking where it is introduced.

```
let limit: u8 = 255u8       ※ keeps its value
let count: mut u8 = 0u8     ※ may be changed
```

**`mut` qualifies the type, not the name.**  It stands where the type stands, after the colon, and either part may be left out:

```
let count: mut u8 = 0u8     ※ the qualifier and the type
let count: mut = 0u8        ※ the qualifier; the type is the value's
let count: u8 = 0u8         ※ the type; the variable keeps its value
let count := 0u8            ※ neither
```

Whether a thing may be changed is a property of the thing, not of the name it is reached by, which is why it belongs to the type.
The distinction is not merely tidiness: a value is a value, and it is the *place* that is writable or not -- so when a variable is
an address, as one at the top level is, what carries the qualifier is the reference.  That is what makes `&mut u8` and `&u8`
different types, and what keeps the rule in one place rather than in two.

The word is Rust's.  The placement differs: Rust writes `let mut x: u8`, qualifying the binding, and spells the type-level form
`&mut u8` only for references.  Compare C and C++, where `const` qualifies the type and the default is the other way round; Kotlin
and Scala, which use two keywords (`val` and `var`) and so make the two look like different constructs; Go, where everything may
change; and ML and Haskell, where nothing may.

**A variable is always given a value where it is defined.**  There is no form that leaves one uninitialized.  A variable without a
value would have to hold something the program never named, and no rule about what that something is would make it named; C leaves
it undefined, Go and Java fill it with zeroes, and both answers are ones a reader has to know rather than read.

The same form defines a variable at the top level and inside a function.  A variable at the top level exists for as long as the
program does and lives in the image; one inside a function exists while the function does.  Naming either yields its value.

#### Assignment

A variable that was defined with `mut` is changed by an assignment:

```
NAME ← VALUE
```

```
let count: mut u8 = 0u8
count ← 7u8
```

The value must have the variable's type, and -- as everywhere -- must fit it: `count ← 300u8` does not compile.

**What may stand on the left is a place**: a name, a field of a record, an element of an array, an entry of a dictionary, and what
a reference names -- each written the way it is read.

```
p.x ← 9u32                       ※ one field of a record
l.to.x ← 7u32                    ※ a field of a field
r⌖.from ← Point(.x ← 0u32, .y ← 0u32)   ※ through a reference, written out
```

**Only the field is written.**  The rest of the record is not read, not copied and not touched: a record that is somewhere has an
address for each field, and writing one is a store to that address.  So the record has to *be* somewhere -- a name given storage of
its own, what a reference names, or a field of one of those.  A record the program worked out is a value in registers and has no
place for a field to be at (4588); binding it to a name is what gives it one.

**A field is as mutable as the record holding it**, and a record reached through a reference is as mutable as the reference.  The
mark is written once, where the thing was made or where it was lent, and a field does not get to say it again: `p.x ← v` on a `p`
defined without `mut` is refused (4004), and `r⌖.x ← v` through a `&Point` is refused (4535).

**A field that holds a reference holds it as long as the field lasts**, which is the rule a write through a reference already
follows and which a field is no way round: a reference of this call's own written into a place that came from outside would
outlive the call that made it, and is refused (4571).  Compare **ML**, whose records are
immutable unless a *field* says `mutable`, which is the other place the mark could go; this language puts it on the binding, as C,
Rust, Go and Zig all do.

##### Assigning to `_`

`_` is where a value goes when the program means to work it out and not use it:

```
_ ← bump()             ※ the call is made for what it does
_ ← seen + 100u8       ※ anything with a value may be dropped, not only a call
```

**It is not a variable.**  It needs no definition and may not have one (4476); it is the same `_` in every scope; and nothing
reads it (4475), so there is no `mut` to write and no question about what it held before.  Those three follow from what it is: a
place to put a value, not a name for one.

**What is assigned to it must produce a value** (4477).  Dropping nothing is not a thing to say, and a call that answers with
nothing already stands as a statement of its own.

The reason it exists is the call made for what it does by a function that answers anyway -- the case `@[can_ignore]` covers when
it is true of every caller, and this covers when it is true of one.

`_` is the same name a `foreach` and a `match` arm use for a value that is not wanted, which is the same meaning: a place where a
name would go, saying that no name is needed.

Compare: Go, where `_` is the blank identifier and assigning to it is how an unwanted result is discarded, exactly as here;
Rust and Python, where `_` is a pattern rather than a place, so `let _ = f()` is a definition that binds nothing -- a spelling
this language does not have, since a definition here defines and `_` is not defined.

**An assignment stands for the variable it changed.**  It refers to the variable, not to the value that was written, so reading it
gives what the variable now holds.  As the last statement of a function it is therefore the function's result, the way any other
last statement is:

```
let counter: mut u8 = 1u8

@[startup]
fn main() → u6:
    counter ← 42u6     ※ the program exits with status 42
```

That it refers to the variable rather than to the written value is what makes this the same rule as everywhere else rather than a
special case: the last statement of a function is its result, and this statement's result is the variable.  Where the result is not
wanted, nothing reads the variable back.

Compare C and C++, where an assignment is an expression yielding the value assigned, which is what allows `a = b = 0` and also
`if (x = 0)`; Python and Go, where an assignment is a statement and has no value at all; and Algol 68, where it yields the
variable, as here.  An assignment is a statement here too and cannot appear inside an expression, so the C hazard does not arise;
what it has is a result, which only the last statement of a block is in a position to use.

**Assignment is written `←`, not `=`.**  `=` compares two values and does nothing else.

The arrow is the older notation: Algol, Smalltalk and APL all wrote assignment with a left-pointing arrow, and it was ASCII rather
than any argument about language design that replaced it with `=` in C and everything that followed C.  Having done so, those
languages needed something else for comparison, and chose `==` -- which is why `if (x = 0)` is a mistake C makes easy, and why
several later languages spend a compiler warning on it.  Pascal, Ada and Go avoid the collision by spelling assignment `:=`; this
language avoids it by leaving `=` to mean what it means everywhere outside programming.

An assignment is a statement and not an expression, so it has no value and cannot appear inside one.  Writing `=` where an
assignment belongs is reported, naming the arrow, rather than left to become a comparison whose result is discarded.

`←` has **no** ASCII substitute, by the rule above.  The only candidate is `<-`, and `x <- y` cannot be told from
`x < -y` without depending on the spaces around it -- a distinction the language does not otherwise make, and one R lives with.
Two characters that could mean two things are not a substitute.

**A value too large for the variable's type is an error, not a truncation.**  `let small: u8 = 300u8` does not compile.  The value a
program writes is the value the variable holds, or the program does not compile; there is no width at which a number quietly
becomes a different number.  C and Go both narrow silently here, and C++ does unless the initializer is braced.

**A value that nothing reads is reported.**  Where nothing reads what a variable was given, between the point it is given and the
point it is replaced or the variable goes out of reach, giving it cannot affect what the program does:

```
let a: mut u8 = 5u8         ※ warning: the value given to 'a' here is never read
a ← 4u8
```

Where that is deliberate -- a program written to exercise the compiler, say -- the program says so with `@[expect(4006)]` rather
than being written around the warning.

The same holds for a variable at the top level, with one difference: any function may name one, so whether anything reads it is a
question about the whole program and is answered once every function has been checked rather than where the variable goes out of
reach.  A variable the program writes and never reads is reported; one the program exports never is, since something outside may
read it.  The attribute that says the report is meant still stands on the definition, where a reader would write it.

A variable nothing names at all is not reported but simply left out of the program, along with anything else nothing can reach.

Considered for the notation: `x: u8 = 3u8` with `x := 3u8` as the short form, after Go and Odin, which is terse but leaves a
statement beginning with an identifier ambiguous until the parser has looked past the name.  A keyword lets a parser commit on the
first token of a definition, which is what keeps the grammar context-free and the compilation parallelizable -- the same reason
`@[` introduces an attribute.

`let` is that keyword, as in ML, Haskell, Rust, Swift and ordinary mathematical writing.  It says that a name is being given a
meaning, which is what a definition does.

#### Function Definition

Functions are defined at the top level of a file with the syntax:

```
fn NAME(ARG1: TYPE1 [← DEFAULT1], [ARGN: TYPEN [← DEFAULTN]]) → RETVALTYPE BLOCK
fn NAME(ARG1: TYPE1 [← DEFAULT1], [ARGN: TYPEN [← DEFAULTN]]) BLOCK
```

where `NAME` is a valid identifier naming the function, `ARG?` are parameter names, `TYPE?` are type descriptions for the parameter,
`DEFAULT?` is what the parameter is given where a call gives it nothing -- described under Calls, since what it is for is visible
there -- and `RETVALTYPE` is the type of the return value.  `BLOCK` is the code of the function, in one of the two notations.  In layout format,
the function header is followed by a colon, a newline, and then the properly indented code.  When the function header is followed by
a `{` it uses the explicit syntax and continues until the respective closing `}`.

**A colon with something after it on the same line opens a block that the end of that line closes.**  It is the layout notation
with the indentation left out and not a third one: the same statements, the same separators, the same everything -- so what it
buys is a short thing written short.

```
fn twice(a: u8) → u8: a + a

if b: 0u6 else: 1u6
if b: n ← 5u8
while n < 3u8: n ← n + 1u8
if n = 1u8: 1u8 elif n = 2u8: 2u8 else: 3u8
```

**What closes it** is the end of the line, or anything on that line that cannot continue it: the `else` or `elif` of the same
chain, the brace of a block it stands in, a comment taking the rest of the line, and -- where the block stands inside brackets --
the bracket that closes them or the comma that separates one value from the next.  A semicolon *can* continue it, so two
statements may stand there the way two may stand on any line.

```
f(if a: 1u8 else: 2u8)                 ※ the `)` closes the block the `else` opened
f(if a: 1u8 else: 2u8, 3u8)            ※ and so does the comma before the next argument
⟦if a: 1u8 else: 2u8, 3u8⟧            ※ in an array, a tuple, a set or a dictionary alike
```

**A block written this way may not open another** (3044).  The inner one would end where the outer one does, so an `else` after
the two would belong to either and a reader would have to know a rule to say which.  C, Java and their family settle that by
binding to the nearest `if`, and pay for it with a mistake nobody sees; here the question is never asked.  Writing the inner block
in braces says where it ends, and so does writing the outer one out:

```
if a: if b: 1u6 else: 2u6              ※ refused (3044)
if a: if b { 1u6 } else { 2u6 }        ※ the inner block says where it ends
```

Compare: **Python**, whose `if b: x` this is and which refuses the nested case for the same reason; **Haskell**, whose layout rule
has the same notion of a block opened without a newline; **Go**, which requires the braces always and so never asks.

##### Walking an array

**`@[listable]` says what it means to hand the function an array where one of its elements is wanted**: the function is called for
each, and what the call comes to is an array of the same shape holding the answers.

```
@[listable]
fn doubled(n: u8) → u8:
    n + n

let v: u8⟦3⟧ = ⟦1u8, 2u8, 3u8⟧
doubled(v)                       ※ u8⟦3⟧, holding 2, 4, 6
```

**The shape is the argument's; what the answer holds is the function's.**  The two need not agree: a test over numbers walked
over an array of them gives an array of truth values of the same shape.

**An argument that is not an array is not walked** and goes to every one of those calls unchanged:

```
@[listable]
fn added(a: u8, b: u8) → u8:
    a + b

added(v, w)                      ※ both walked, in step
added(v, 10u8)                   ※ v walked, 10 handed to each call
```

**Arguments walked together are walked in step** (4481), so they agree about how many there are along each dimension that is
walked -- and about nothing else.  The first of one goes with the first of the other, and there is no first of one to go with a
second the other does not have.

**The walk takes one dimension off at a time and stops, for each argument, where what is left is what its parameter takes.**  So
one function walks a table twice and another walks it once, and which it is, is a question about the parameter:

```
let m: u8⟦2,3⟧ = ⟦⟦1u8, 2u8, 3u8⟧, ⟦4u8, 5u8, 6u8⟧⟧

doubled(m)                       ※ u8⟦2,3⟧: walked down to the numbers
total(m)                         ※ u8⟦2⟧, for `fn total(xs: u8⟦3⟧) → u8`
```

**Every dimension walked is one the type states** (4482).  What the answer is an array of is the shape that was walked, so a
shape nobody stated is one there is no room to answer with -- an array whose type does not say its length cannot be walked, and
neither can one the walk never reaches the parameter's type from.

**A function that takes nothing may not be marked** (4483): there is nothing to hand it an array in place of.

**A function named where a value is wanted is one**, with the type its definition gives it, `@[listable]` and all:

```
@[listable]
fn twice(a: u8) → u8:
    a + a

let walks: @[listable] fn(u8) → u8 = twice
walks(v)                         ※ u8⟦3⟧: the walk came with it
let plain: fn(u8) → u8 = twice   ※ which drops it
```

So a function and a lambda are two ways of writing one kind of value, and either stands where the other does.  What such a value
holds is three words, where the code is, where what was brought in is and its allocator; a function brings nothing in, so the
last two are nothing.  **A generic function is not one** (4569): it is compiled once per set of types and which sets
those are is what the calls ask for, so named where a value is wanted there is no call to ask and no type for the name to have.

**A lambda says it the same way**, written before the thing it describes -- and there the word is part of the *type*:

```
let double: @[listable] fn(u8) → u8 = @[listable] λ a: u8 → u8:
    a + a

double(v)                        ※ u8⟦3⟧, holding 2, 4, 6
double(7u8)                      ※ u8, holding 14
```

It has to be.  A lambda is nearly always called through a name, and what a name holds is a type, so a promise the type did not
carry would be one the caller never heard.  That is the argument a reference already makes for `mut`: both sides of the call
reach the thing, and the caller cannot be left to guess.  A function definition needs none of this -- a call names it, so the
definition is in view -- which is why `@[listable] fn twice(...)` is written with no such word in any type.

**Only what a caller reads off the type may be written before a lambda** (3204).  `inline`, `impure`, `abi` and the rest are
about a body, and a body is not what a name holds; `listable` is the one thing so far that a caller acts on.

**A listable function stands where a plain one is wanted**, dropping the walk, by the rule every other promise follows: it
promises what the plain one does and adds to it.  One way only, and the two are the same three words in the same three
registers, so nothing is emitted for it.

Compare: **APL**, **BQN** and **UIUA**, where every primitive walks and there is nothing to write at all; **Julia**, whose
`f.(v)` puts the mark at the call, so the caller decides and no type carries anything; **NumPy**, whose `vectorize` wraps a
function in another function, which is what this would have to be if the answer were not to put it in the type.

**The operators walk arrays too**, and by the same rule.  No attribute is written on them: an operator is not a definition, so
there is nowhere to write one, and every operator that would be marked would be marked -- which is what saying it of all of them
says.

```
v + w                            ※ both sides walked, in step
v + 10u8                         ※ one side walked, the number used at every turn
v > 2u8                          ※ bool⟦3⟧: the answer is the operator's type
m + col                          ※ a table against a column: each row meets its number
```

What is walked is **everything that works out both of its sides**: the arithmetic and its saturating forms, the comparisons
exact and approximate, the bitwise operators and the shifts, the logical operators written with glyphs, and the two written
before their operand.  The walk goes down to what the operator is defined on, which is never an array, so it goes all the way.

**`and` and `or` are not walked**, and could not be.  Which side is worked out is what they are about, and over an array there is
no such thing as which side: the first element might decide it one way and the second the other.  A program that means the walk
writes `∧` and `∨`, which work out both sides and say so.


**The calls are written out**, one per element, rather than made in a loop.  That is what a shape known while compiling makes
possible and is why one is required; a loop would be wanted where the shapes grow, and nothing about what this means would change.

**How many instructions that turns into is the compiler's business and not the program's.**  An operator written over an array
asks the same question of every element and of nothing else, so a machine with registers holding several values at once may ask
it of a whole run of them in one instruction -- and one without such registers asks it of each element in turn.  The answer is
the same either way, including the answer to "did this go past the end of its type": an element that would have stopped the
program on its own stops it just the same when it was one lane of sixteen.  Nothing in the language says which happened, and a
program cannot be written that can tell.

What the compiler is allowed to assume in doing it is that **the two sides do not overlap in memory unless the program said so**.
The only ways they can are the ones the language can spell: an array read and written in the same statement, and a slice that
refers into the array it came from.

Compare: the Wolfram Language, whose `Listable` this is, including the broadcasting of an argument that is not a list and the
requirement that the lists walked together have the same length -- and where it goes further than here, since a Wolfram list has
no element type and so no question of when to stop.  Here the parameter's type says when, which is what makes `total` walk a
table once and `doubled` walk it twice.  APL and BQN thread every scalar function over arrays by default with no attribute at all,
their whole design being about arrays; NumPy broadcasts by rank and length with rules for extending shapes, which this does not
do -- an argument is walked or it is not.  Julia spells it at the call, `f.(v)`, which puts the choice with the caller rather than
with the function: the opposite decision, and a coherent one, taken the other way here because what it means to hand a function an
array is the function's own business.

##### What a function may change

**A function is pure unless it says otherwise.**  What pure means here is that calling it changes nothing a later call or a later
reader could notice, which is what lets a caller move a call, make it once instead of twice, or not make it at all.

```
@[impure]
fn bump():
    counter ← counter + 1u8
```

A pure function may **read** anything, a variable at the top level among it; it may **write its own storage**, an array it made
being gone when the call ends; and it may call other pure functions.  What it may not do is change anything that was there before
the call or is there after it:

| | |
|---|---|
| writing a variable at the top level | 4478 |
| writing memory it did not make -- an array it was handed, a set or a dictionary it was handed | 4479 |
| calling a function marked `@[impure]` | 4480 |

**Reading or writing a device is not on that list.**  `@[impure]` means the function changes something *global*, and a write to a
device somebody handed over is not that: it is the one effect the program's own structure accounts for.  **Permission to do it
travels with the device** (4538):

```
※ No attribute of any kind: the `&mut Writer` is the permission.
fn quiet(to: &mut std.Writer) → u64 ¤size ? i32:
    std.Io.print(to, ⎕bytes(""))
```

**A parameter whose type is marked `@[device]` carries it**, and so does one that holds such a type *anywhere* inside it -- which is
how the startup function's `std.Init` carries three and why writing through it needs nothing said.  **A function handed none says
`@[io]`**, which is what the drain a program does before it ends has to say, having nothing handed to it.

**Reaching a device through a variable is not being handed one.**  `std.io` is ambient, so a function that writes through it is
doing something its signature does not account for and declares it.  That one word is the whole difference between the two ways of
reaching the same three devices, and it is what makes the other way mean anything.

**Doing it is what marks the function**, not holding the device: a function handed a `&mut Writer` that never touches it is a
function a caller may still drop, and one that writes is one nothing may drop, move or repeat.  So the attribute a program writes is
a declaration and the flag the rest of the compiler reads is a fact about the body.

That makes **I/O a capability and not an attribute**, which is this language's answer to what a monad is for: what sequences the
effects is the value being passed along, and a function with no such value cannot perform them.  `std.Init` is `@[unique]`, so the
permission cannot be duplicated -- only lent -- which makes it a *linear* capability.

Compare: **Haskell**, where `IO` is a type constructor and the world is threaded through it invisibly -- the same idea with the
token hidden rather than written as a parameter; **Clean**, whose unique `*World` is this exactly, a value passed along that cannot
be copied; **Austral** and the capability-based languages, where a capability is an ordinary linear value and this is the whole
design; **Rust**, where `&mut Stdout` is a capability in effect and nothing in the type system says that writing needs one;
**Zig** and **Go**, where any function may write to any descriptor it can name.  What is left for `@[impure]` here is what those
languages have no word for at all: a change to something the program did not pass in.

**Allocating is not on the list either.**  Taking room from an arena makes something new that nothing else can reach, so a
pure function may join strings, make a list or a collection, and answer what it made.  Writing into a collection it was *handed*
is on the list -- that is memory somebody else made -- and so is giving an arena back, which only the arena's maker may do.

**Every one of them names the function that is pure, and points at it** (4511).  That is not always the function the message is
about or even in the same file: a call a macro wrote is reported where the macro wrote it, which is wherever that macro lives, and
what has to change is one attribute on the *caller's* line.

**Purity is a property of everything a call reaches**, not of one function's own statements: whatever the callee may change, the
caller may change by calling it.  So a function calling an impure one is itself `@[impure]`, and the chain of those attributes is
the one line that says where the effects in a program begin.

**The default is the strict one**, which is the opposite of the choice a language written by people would make.  A generator
knows what it is emitting: saying `@[impure]` where an effect is meant costs it one word, and having the compiler check the rest
costs it nothing.  The value is in what the default buys -- every function that does not say otherwise is one a caller may
rearrange -- and a default that has to be asked for is one most functions would never be given.

**What purity is worth, today, is that a call nothing reads is not made.**

```
_ ← worked_out(3u8)     ※ pure: the call is dropped
_ ← notes(42u8)         ※ impure: the call is made, whoever wants the answer
```

That is what makes `_ ←` worth writing rather than merely allowed.  **A dropped call takes with it any way it had of stopping the
program**: an overflow inside a call nobody made cannot be reached.  That follows from the call being removable at all, which is
what the attribute was declared for; a program that wants the check to happen wants the answer, and reading the answer keeps the
call.

**A call that is not made is written to the report log** (`drop-call`), naming the function and saying why.  A generator that
emitted a call and cannot find it in the output is told where it went, and told that what let it go was the absence of
`@[impure]` -- which is a property of a function the generator wrote and can change.

More will follow from it -- a call made twice with the same arguments worked out once, a call moved out of a loop -- and none of
it needs the language to say anything further.

Compare: Rust, whose `const fn` is a different question (what may run while compiling) and whose purity is otherwise carried by
`&mut` in the type system, so that the compiler knows what may be changed without anything being declared; Haskell, where purity
is the default and effects live in a type, which is the same default reached by a much larger mechanism; D's `pure`, which is
this -- an attribute, checked, with the compiler free to elide calls -- and which is the closest existing design; C and C++,
where `__attribute__((pure))` and `[[gnu::const]]` are promises the compiler does not check, so a wrong one is undefined
behaviour rather than a message; and Zig, which has no such attribute and infers what it can.

##### Parameters a function may change

A parameter may be marked `mut`, which says the body may bind the name to something else.  `mut` stands where it stands in a
definition -- before the type -- and says the same thing there.

```
fn total(n: mut u8) → u8:
    let sum: mut u8 = 0u8
    while n > 0u8:
        sum ← sum + n
        n ← n - 1u8
    sum
```

**It is no part of the type.**  What the caller hands over is a value, and what the body does with its own name for it is the
body's business: two functions differing only in `mut` are one signature, carry one symbol and are called the same way.  Nothing
outside can tell, and nothing outside has to be told -- adding `mut` to a parameter changes no caller.

That is where a parameter's `mut` differs from a reference's.  A `&mut T` says something about the *place* it names, which the
caller and the callee both reach, so it is part of the type and has to be; a parameter's says something about a name, and a name
is not shared.

A parameter that does not say `mut` is what it was given and stays so (4004), which is the rule every other name follows.

**The value nothing read** is asked about a parameter from the moment the body assigns to it, and not before.  What a caller hands
over is the caller's business; a value the body itself put there and nothing read is a value it need not have worked out, which is
what that rule is for.

Compare: C, where every parameter may be assigned and nothing says so, which is why a reader cannot tell a parameter that stays put
from one that does not; Rust's `fn f(mut x: i32)`, which is this, with `mut` before the name rather than before the type because
that is where Rust's `let` puts it; Go and Zig, where a parameter cannot be assigned at all and a body that wants to count down
makes a local of its own.

**A header with no arrow says the function answers with nothing.**  There is no name to write for that and writing one is refused:
`fn prepare():` is how it is said, and `fn prepare() → void:` is not a second way of saying it.  `void` is not a type any value can
have, so naming it where a type belongs says less than leaving the place empty.

What follows from a function answering with nothing is three things, and all three are the ordinary rules applied rather than
rules of their own:

- **A `return` in it carries nothing.**  `return 1u8` is refused: there is nothing for the value to become.
- **An expression as its last statement is a value that goes nowhere.**  The last statement of a body is the body's *result*, and
  a body with no result has nowhere to put one, so a bare expression there is refused exactly as one in the middle of a body is.
  What belongs at the end of such a body is a statement that does something -- an assignment.
- **A semicolon at the end asks for nothing.**  The empty statement it leaves behind produces no value, and no value is what was
  wanted.

The same semicolon in a function that *does* answer with something is the other way round: the body then ends in a statement that
produces nothing, and the function has not answered.  That is an error, and it is the one thing the rule about semicolons is worth
having a rule for -- `7u8;` and `7u8` are different programs, and the difference is visible.

**A call to such a function has no answer, so there is nothing to use.**  Where a value is wanted, naming one is an error: it
cannot start a variable, it cannot be assigned, and it cannot be an operand of anything.

```
let kept: u8 = prepare()      ※ refused: there is nothing to put in it
kept ← prepare()              ※ refused, for the same reason
kept ← prepare() + 1u8        ※ refused, and twice over
```

**With one exception, and it is an abbreviation rather than an exception to the rule.**  In a function that itself answers with
nothing, `return f()` is allowed where `f` also answers with nothing, and means the call followed by returning.  No value is
carried anywhere in it -- which is why it is well formed where all three lines above are not.  It exists so that a function ending
in a call to another can say "and that is the last thing I do" in the place a reader looks for it.

C and C++ allow exactly this and for a reason this language does not have yet -- a template returning `T` where `T` may be `void`
-- but the abbreviation reads well on its own, and it is the same shape the language already admits in `return x` beside a bare
`x` as the last statement.

Nothing of this reaches the representation as a value: a call that answers with nothing is an instruction whose result nothing may
name, and the compiler refuses to build anything that names one.

Function return values are specified with the `return` keyword.  The function immediately returns and the remaining statements in the
block are ignored (the compiler must issue a warning in this case).  If the `return` statement is the last statement in the function
then the `return` keywords can and should be skipped (a warning is issued in this case).

Rust draws the same distinction with the same mark, and for the same reason: a block's value is its last expression, and a
semicolon after it turns the block into one that has none.  C, C++, Go and Java have no such distinction to draw, a semicolon
there terminating a statement rather than separating two.


##### Calling itself last

**A call of a function to itself in tail position is guaranteed to be a loop, and takes no additional stack.**  It is a jump back to
the start of the function with the arguments as the new parameters, whatever optimization level a build asks for: a guarantee of the
language and not a choice of the compiler's, so a program may recur as deep as it likes in tail position and rely on it.

```
fn count(n: u64, acc: u64) → u64:
    if n = 0u64:
        acc
    else:
        count(n - 1u64, acc + 1u64)      ※ a jump: ten million turns, one frame
```

**A call is in tail position** where what it answers is what the function answers and nothing is left to do after it: the last
expression of the body, a `return` of the call, or the last expression of an arm of an `if` or a `match` that is itself in tail
position, however deep.  A call whose function answers nothing is in tail position where the function returns right after it.  A
function answering `T ? E` that answers the call's result as it came is in tail position too; `f(x)?` is not, the `?` being work
done on the answer.

**What is not in tail position**, and takes a frame like any call:

- an answer something is done with -- `n + f(n - 1)`, a conversion, a `?`;
- a call in a block with a `defer` the call is inside of, what is put off running after it;
- a call in a function with a `post` condition, which is checked after it;
- a call handing over a reference into the calling function's own storage -- `f(&mut mine)` for a local `mine` -- which the next turn
  of the loop would reuse while the reference still names it.

**Where an answer is made is kept arm by arm.**  An answer made somewhere the signature does not name is copied there before the
function leaves (Knowing the allocator), and for an `if` or a `match` that is the answer, that copy is made at the end of each arm and
not after they join -- so an arm ending in a call of the function to itself still ends in that call, and an accumulator of text
recurs as a loop.  The call's own answer is made by the same rule one call down and needs no copy.

**Calls between two functions are not covered**: `f` calling `g` calling `f` takes stack.  Turning those into jumps needs every
function of the cycle to keep its frame the same size and its arguments in the same places, which a call in general does not.

Every call of a function to itself is in the report log: `tail-call` where it became a jump, `self-call` with why where it did not.

Compare: **Scheme**'s standard requires proper tail calls of every call in tail position, between functions too, and **Lua** has
them; **ML** and **Haskell** implementations make them, the languages leaving it unsaid.  **C**, **C++**, **Go** and **Rust** leave
it an optimization a compiler may or may not make -- Rust reserves `become` for asking -- and **Clang**'s `[[clang::musttail]]` and
**Zig**'s `@call(.always_tail, …)` ask for it call by call and refuse to compile where it cannot be done.  **Python** and **Java**
never do it.  This takes the guarantee Scheme gives, for the case that needs nothing of any other function: a function calling
itself, where the frame being reused is its own.

##### Macros

**A macro is written where a function is called and is not a function call**: what stands between its marks is handed over as it is
written and not as what it evaluates to.

```
macro twice:
    ⌜$x⌝ → ⌜$x + $x⌝

twice⌜3u8⌝                       ※ 3u8 + 3u8
```

That is the whole of what a macro is for.  A function receives a number; a macro receives the multiplication that would have
produced it, and can rewrite it.

**The marks are the language's own.**  `⌜…⌝` lifts what is written between the brackets out of the program and into the compiler,
which is the same sentence -- so an invocation is a name applied to a quote rather than a notation of its own, and a reader who
knows `⌜u8⌝` knows what `f⌜x⌝` does to `x`.  `(…)` calls a function and `⌜…⌝` invokes a macro; a macro and a function are not in
one namespace, so a name may be both and neither is a mistake.

**A macro is written in one of two forms**: a list of rewrite rules, which is below, or a function over the program's text, which is
further down.  Both are `macro`, and which one it is, is said by the shape of what follows the name.

**A macro is a list of rewrite rules.**  Each states what the arguments have to look like and what the invocation is replaced by.
Rules are tried in order and the first that matches decides, so a rule whose pattern is holes goes last; where none matches, the
invocation is refused (7012).

```
macro pick:
    ⌜$x, $x⌝ → ⌜1u8⌝           ※ written alike
    ⌜$x, $y⌝ → ⌜0u8⌝           ※ anything else
```

Within a pattern, **`$a` is a hole**: it matches anything and remembers what it matched.  A name, a literal or an operator matches
only itself, structurally.  **A hole written twice matches only where the two are written alike**, which is how a rule says its
arguments agree -- and two pieces of a program are alike where the same thing is written in both, wherever each was written, so the
positions are not compared.

**A pattern holds one expression per argument**, and how many there are is part of the shape: a rule for two does not match an
invocation with one.  Nothing between the marks is a macro of no arguments, which is a thing to write.

**A template holds one expression** (7019), **or a run of statements** where its contents are indented under the opening mark.  The
marks do not hide the ends of lines the way the other brackets do, so the indented form is the layout every block in the language
already has with the mark in place of a colon.

```
macro swap:
    ⌜$a, $b⌝ → ⌜
        let t: u8 = $a
        $a ← $b
        $b ← t
    ⌝
```

**A macro that writes statements is invoked on a line of its own**, and what it writes replaces that line.  The statements go
into the run around them rather than into a block of their own, which would be a scope the macro did not ask for.  **Where a value
stands instead, the statements are a scope of their own** and what they come to is what the last of them does -- so the last has to
be an expression (7014).  What they bound is gone, and what a `defer` among them put off has run, by the time the value is used,
which is how `std.println` makes a pool, gives it back and still answers how many bytes went.  GNU C's statement expressions,
`({ … })`, are this without the macro.  **A template may
assign to a hole**, and which kind of place that is, is settled once the hole is filled; one filled with something nothing can be
assigned to is refused (7018), pointing at the argument rather than at the template.

###### When expansion happens

**After parsing and before any check.**  What the checker, the type rules and the code generator see is a program with no macro left
in it, so a macro cannot produce a program that would not otherwise be legal, and every diagnostic the language gives is given about
what the macro wrote.  `--emit=expanded` writes the tree at exactly that point.

**It cannot run earlier.**  A macro is handed the parse tree of its arguments and there is none before parsing.  The C preprocessor
works on characters because it has to: C's grammar is not context-free, and `a * b;` needs to know whether `a` is a type.  This
grammar is context-free and an invocation is marked, so the text around a macro can be read without knowing what the macro is.

**An invocation is expanded before what is written inside it**, so a macro is handed its argument as the caller wrote it, including
any invocation nested in it.  What comes out is expanded in turn, so a macro may write another one -- and one that writes something
reaching itself again is stopped after 64 rewrites (7015).  That is a limit and not an analysis: whether expansion ends is the
halting problem.

**Nothing is checked after an expansion that failed.**  What the checker would be handed is the program the macro could not write,
and every message about it would be about something nobody wrote.  What *parsing* reported does not stop it, the parser recovering
and the checker having things to say about what it did parse.

###### Positions

**Every piece of an expansion keeps the position it was written at.**  What came from the caller points at the caller's text and what
came from the macro points into the macro's definition, so an error in an expanded program names the place the offending text was
actually written -- which is either the invocation or the macro, and those are the only two answers that can be right.

That falls out of substituting trees rather than text: a tree from the caller carries the caller's spans and one from the template
carries the macro's, because those are the spans the parser gave them.

###### Hygiene

**A name a template binds is renamed to something no source file can spell**: the name with a `#` and a number after it.  `#` is an
operator glyph, so no identifier can hold one and the renamed name collides with nothing a program writes.

In `swap` above the caller's variable may be called `t` and so is the macro's temporary.  Without the renaming the macro's `t` would
shadow the caller's and the swap would leave both where they were.

**A name a macro *reads* resolves where the macro was written**, not where it was invoked: a macro may write a call to something its
own file can see and the caller cannot, and a caller with a function of the same name does not change what the macro means.  That is
the rule a bundle's lines already follow and for the same reason -- substituting *into* a line is not the same as substituting the
line into the place that applied it -- so a macro means one thing everywhere it is invoked.

###### A macro written as a function

**The other form of macro is a function over the program's text**: it is handed pieces of the program, works something out, and
answers the piece that replaces the invocation.  From the parameter list on it is written exactly as a function is -- the keyword is
the whole difference.

```
macro sum(e: syntax) → syntax:
    if ⎕head(e) = ⌜+⌝:
        ⌜$(⎕part(e, 0u64)) × 10u8⌝
    else:
        ⌜$e⌝

sum⌜3u8 + 4u8⌝                   ※ 3u8 × 10u8
sum⌜5u8⌝                         ※ 5u8
```

Which form a `macro` is, is said by its shape and not by a second keyword: a parameter list says it is this one, and rule lines say
it is the other.  A macro takes as many pieces as its parameters say (7026) and must answer one (7025).

**`syntax` is the type of a piece of the program**, and it is a type only while the compiler runs.  A parameter, a local or a
function that names it exists for the macros and is not in the program: nothing at run time may hold a piece of the program (7022),
there being nothing at run time for it to be a handle into.  It is one value wide and compares with `=`, which asks whether the same
thing is written in both, wherever each was written.

**Three questions take a piece apart.**  `⎕head(e)` is what it is made by -- for a literal, its type: `str`, `char`, `bool`, the
number's own type -- `⎕parts(e)` how many pieces it applies its head to, and
`⎕part(e, n)` which one.  An operator alone between the marks is the operator itself rather than an expression using it, which is
what `⎕head` of a sum answers -- so nothing about any particular operator is built in, and finding a sum is a macro asking whether
the head is `⌜+⌝`.  `⎕part` names which part it wants rather than answering them all, which is what keeps an array out of the
machine that runs a macro: how many there are is its own question and a walk over them is a loop the macro writes.  Asking one of
the three where nothing has a piece of the program is refused (7027), as is giving it the wrong number of arguments (7028).

**`⎕name(e)` is what a piece is written as, as text.**  A string literal answers what is between its quotation marks and a name
answers itself; anything else is refused, there being no one word an expression is written as.  That is what lets a macro read a
template, a template being a literal.

**`⎕refuse(TEXT)` is how a macro says it will not write what it was asked for** (7029).  What is wrong in that case is the
*invocation* and not the compiler, so the message is the macro's own and it points where the invocation is -- without it a template
with three holes and two arguments would be reported as the compiler giving up.  It answers a piece of the program and never
answers, so it stands where the macro's answer stands.

**`⌜…⌝` in the body of something that runs while the compiler does holds a piece of the program**, which is the same reading the
invocation has and the reason the marks are the right ones.  `$a` puts the piece a name holds into the tree, and **`$(EXPR)` puts
what an expression answers**: a piece of the program as itself, a number as what a program would have written to mean it, and text
as the literal a program would have written -- which is what lets a macro work something out and write the answer.  Anything else is refused (7021), and marks holding a piece where
nothing runs while the compiler does are refused too (7020).

**A body may write statements** by answering a quote whose contents are indented, exactly as a template may, and what it writes
replaces the line the macro was invoked on.  Hygiene is the same in both forms: a name the macro binds is renamed once per time the
macro runs.  **The renaming is the run's, not the quote's**: every quote the macro writes takes the same new names, readings and
bindings alike, so one quote may name what another binds -- `std.println` hands `formatted` the quote `⌜pool⌝` to say where the text
goes, and the quote holding `let pool` is another one.  A quote written by a function the macro calls is renamed by itself.

```
macro bump(a: syntax) → syntax:
    ⌜
        let step: u8 = 1u8
        $a ← $a + step
    ⌝
```

**`comptime fn` is an ordinary function the macros may call.**  A macro is one function and cannot be two, so a walk that has to
descend calls something that recurses:

```
comptime fn deepest(e: syntax) → u64:
    if ⎕parts(e) = 0u64:
        1u64
    else:
        1u64 + deepest(⎕part(e, 0u64))

macro depth(e: syntax) → syntax:
    ⌜$(deepest(e))⌝
```

`comptime` stands before the keyword, a function being of one kind as a whole.  Such a function is installed before expansion for
the macros to call, **and again in the ordinary way for the program** -- unless what it takes or answers is a piece of the program,
and then it is the macros' alone.  So `comptime` says when a function exists rather than what it computes, and a helper worth having
in both places is written once.

###### A macro of a module

**A macro may be exported, and is invoked through the module that wrote it**: `m.f⌜…⌝`, which is the path every other name a module
holds is reached by.

```
let std := ⎕import("std")
std.println⌜&mut init.io.output, "x = {}", x⌝
```

**Expansion comes before anything is checked and an import is resolved while checking**, so a module whose macro is invoked here is
found and read by the expander -- through the same cache the checker fills, so the file is parsed once however many stages want it.
A macro of a module this file does not import cannot be reached at all, which is the rule every other name follows.

**A name the macro reads means what it means where the macro was written.**  A macro in `std` naming `Io.print` means `std`'s, and
the file that invoked it may have no `Io` at all -- so the name is written at the caller the way every other name of that module is:
`Io.print` becomes `std.Io.print`, which is a path the language already reads.  Both forms of macro follow the rule, and only the
names the module writes at the top level are touched -- what the *caller* handed over arrives by filling a hole, and a name of the
caller's is never requalified however much it looks like one of the module's.

**A name the module does not export cannot be reached from the caller**, so a macro naming one is refused there (4104).  That is the
limit of writing the name as a path, and it is the same limit everything else about a module has.

###### A parameter that stands for all the rest

**`⁂` before a macro's last parameter says it stands for all the arguments from there on** (7030), which arrive as the one piece
several pieces already are:

```
macro format(template: syntax, ⁂args: syntax) → syntax:
    let holes: u64 = ⎕parts(args)
    …                            ※ ⎕part(args, n) is each of them
```

So **a macro takes any number of arguments without the language gaining a variadic function**: how many arrived is `⎕parts` of a
piece, which is a question about the program and one the compiler answers.  The glyph already means "several things stand where one
is written", read here from the other end -- it is written where the one is, and what arrives is the several.  Only a macro may
have one, and only as its last: a function is called with a count that is written down, and there is nothing after such a parameter
for an argument to reach.

###### How a macro is run

**The macros are compiled and the compiler runs what it compiled.**  Every `macro` and every `comptime fn` is checked and lowered
into a module of its own, at the same time as the program and by the same code, and the resulting IR is interpreted.

That is what makes a macro subject to every rule the language has rather than to a second and weaker set: its types are checked, its
purity is checked, and a mistake in it is reported by the same diagnostic a mistake anywhere else would get.  A macro the compiler
cannot lower is refused with the reason (7024), and one whose run stopped -- a step limit, a division by zero, a `⊥` -- is refused
with that (7023).  The limit is a limit and not an analysis, for the same reason the expansion limit is.

###### What is not there yet

- **`⎕kind`**, which would say what sort of thing a piece is without comparing its head against something; **`⎕name`**, the name a
  piece reads as; and **`⎕apply`**, building a piece from a head and its parts rather than from a template.  All three are in the
  design and none is callable.
- **An array of `syntax`.**  `⎕parts` and `⎕part` are what a macro walks with, and a macro cannot hold the parts of something all at
  once.
- **A macro that writes a definition**, which is what would make the feature worth most and what would have expansion run before the
  definitions are collected rather than before they are checked.
- **Following a reference to what it names**: asking a piece that reads `foo` for the definition of `foo`.  Expansion runs before the
  definitions are installed, so there is nothing yet to ask.

###### Comparisons

| Language | A macro sees | Written as | Invoked as | Hygienic |
|---|---|---|---|---|
| **C** | characters | `#define` | `f(x)`, indistinguishable | no |
| **Rust** | tokens | `macro_rules!`, proc macros | `f!(x)` | mostly |
| **Scheme** | a parse tree | `syntax-rules`, `syntax-case` | `(f x)`, indistinguishable | yes |
| **Common Lisp** | a parse tree | `defmacro` | `(f x)`, indistinguishable | no, `gensym` by hand |
| **Julia** | a parse tree | `macro` | `@f x` | mostly |
| **Nim** | a parse tree | `macro` | `f(x)` | yes |
| **Wolfram** | an expression | rules with `:>` | `f[x]` | no |
| **Zig** | nothing | `comptime fn` | `f(x)` | n/a |
| **pl4g** | a parse tree | `macro`, rules or a function | `f⌜x⌝` | yes |

What pl4g's row says that none of the others does is that **the mark around the arguments is the language's existing mark for "not
evaluated"**.  Rust and Julia mark the name; Lisp, Scheme and Nim mark nothing.  Marking the arguments says the thing that is
actually true of them, and marking them with `⌜⌝` says it with a notation already in the language.

Turned down: **text or token substitution**, since there is nothing to take apart and nothing to be hygienic about, and this
language's tokens are glyphs so a token run is no more structured than a string; **reader macros**, as Common Lisp has, because a
program that changes how text is scanned cannot be parsed without being run, which would cost the tree-sitter grammar and the test
that holds it to the compiler; **`f!(…)`**, since `!` is not this language's to spare; and **`@[macro]` on a function**, since an
attribute says what the compiler is told *about* a definition and which kind of definition it is, is not that.

##### Named inside a type

**A definition's name may be a path, and that is the whole of what attaches it to a type.**

```
type Walk = at : u8 ; last : u8

@[impure]
fn Walk.next(it: &mut Walk) → u8:
    it⌖.at ← it⌖.at + 1u8
    it⌖.at

Walk.next(&mut w)
```

**The first parameter is written out.**  There is no receiver and no `self`: the language has nothing else implicit, and a
receiver would be the first thing a reader has to know is there without seeing it.  So what the path gives is **namespacing**, and
namespacing is what it is for -- two types may each have a `next`, neither reserves the word, and a free function may be called
`next` as well.

**It is called as a path**, which is the notation a module's member already uses.  `value.name(args)` is **not** part of this: what
a path names is found by the path, and nothing is looked up on a value.  Nothing is implicit about which function a call names.

**The first part must be a type this file defines** (4608).  A type's own file is where what belongs to it is written; a type from
another module is deliberately out, since saying yes brings with it the question of who may add what to whose type.  That is a
larger decision and this does not foreclose it.

**What belongs to a type is reached through the type**, so a path from another module has three parts:

```
let m := ⎕import("bag")
m.Bag.doubled(&b)
```

and that is the only three-part path the language has -- there is never a fourth, a type's own file being where its definitions
are.  Only an exported one is a name an importing file can write (4609); one that is there and not exported is a different thing
from one that does not exist, and is reported as it.

**Nothing declares what a type has.**  Having a definition of that name is the whole of what it means to have one, which is what
lets a protocol be a name: the `next` of a type `T` is the function `T.next`.

**The symbol in the generated program is the path**, mangled as any other name is, so nothing about the back end changes.

Compare: **Go**, whose methods are declared this way round -- a receiver before the name -- and whose interfaces are satisfied by
having the methods; **Rust**'s `impl` blocks, which group what belongs to a type and introduce a receiver; **Zig**, where a
function inside a struct is reached as `T.f(x)` and `x.f()` is sugar for exactly that; **Python**, where the receiver is written
out as `self` and the sugar is the only way to call; **C++** and **Java**, where a method is part of the type's definition and the
receiver is implicit.  This is Zig's answer without the sugar: the path is the call, and whether `value.name(args)` is ever added
is a separate question that this leaves open.

What this does **not** decide: whether a type may be extended from outside its file; whether the same rule applies to an
enumeration, a tuple, a unit or a built-in type (`u8.next` is a question nobody has asked); and whether `value.name(args)` is
sugar worth having.

##### Operators

**An operator is a function whose name is the glyph**, written between grave accents:

```
type Point = x : u8 ; y : u8

fn `+`(a: Point, b: Point) → Point:
    Point(.x ← a.x ⊞ b.x, .y ← a.y ⊞ b.y)

Point(.x ← 1u8, .y ← 2u8) + Point(.x ← 3u8, .y ← 4u8)
```

The accent is the one character of ASCII the language gave no meaning, and it reads as a quotation of the glyph -- which is what it
is: the *name* of the operator rather than the operator.  Nothing else about the definition is special.  It may be generic, it may
have conditions, its symbol is the signature written out (`+(Point,Point)Point`), and it is called by writing the operator.

**One parameter is the prefix reading of a glyph and two is the infix one** (4922).  Several glyphs are both -- `⌈xs` is the largest
of what `xs` holds and `a ⌈ b` is the larger of two -- and the number of operands is what says which of them a definition is
of.  Nothing is written to say so, and a glyph may have both: they are two definitions and neither is a repeat of the other.

**The language's own meaning cannot be replaced.**  A definition is consulted only where the operator had no meaning for those
operands to begin with, so `1u8 + 2u8` is three in every program and a definition over `u8` is never reached.  That is what makes
the feature purely additive: no program can change what another's arithmetic means by being linked with it.

**An exported operator is in force wherever its module is imported**, and is written without naming the module:

```
※ digits.pl4g
@[export, impure]
fn `⍕`(n: u64) → str: …

※ and in a file that imports it
let d := ⎕import("digits")
… "n=" ⧺ ⍕1234u64
```

There is no name to qualify an operator by, so putting it in force is the only thing exporting one could mean -- and it is what
lets a library say what a glyph means for the types it deals in.  **Two modules exporting one glyph for one number of operands are
refused** (4924), at the second import rather than at a use, there being nothing to decide between them; **a definition in this
file wins over an imported one**, silently, because what is written here is what a reader has in front of them and it is the way
out of that refusal.  An operator a module does *not* export cannot be told from one it never wrote, there being no name to ask
about -- which is where this differs from a bundle.

###### Which glyphs

**Unicode decides.**  A glyph may be an operator if it is a symbol -- the `Sm` category, which is the mathematical symbols, and
`So`, which is the rest -- so what is left out is the ASCII punctuation the grammar needs for itself.  Asking Unicode rather than
writing a list is what makes the rule one sentence and what lets a program use a glyph nobody thought of.

Two clauses qualify it.  **A glyph the language already uses as an operator is one** whatever Unicode calls it, since not all of
them are symbols there: `-` is a dash, `^` a modifier symbol, `⌈` and `⌊` are brackets, `«` and `»` quotation marks, and `ⁿ` a
raised letter.  **And a glyph the language has given a meaning that is not an operator is not one** (3052), whatever Unicode calls
it: the arrow of a signature, the arrow that binds a name, the failure value, the quad that begins a name the compiler provides, a
lifetime, the two lifting marks, and the raised minus of a negative literal.

**`∧`, `∨`, `?` and `⌖` may not be named either.**  The first two decide *whether* to work something out, and a function takes
its arguments already worked out -- so one written for them would change when things happen and not what they mean, which is the
trap C++ left open on `&&`.  `?` and its pair are about a result rather than about what a result holds, and `⌖` is about a
reference.

**An operator is one glyph** (3051), or a **pair of brackets**, which is the next section.  Two glyphs beside each other that are
not a pair are two operators, which is what makes an expression readable without a table of which pairs mean something.

###### A pair of brackets

**The brackets the language uses are operators like any other**, so a program may say what they mean for a type of its own.  The
name is the *pair*: an opening bracket and a closing one written together.

```
type Grid = a : u8 ; b : u8 ; c : u8 ; d : u8

fn `⟦⟧`(g: Grid, i: u64) → u8:            …
fn `⟦⟧`(g: Grid, i: u64, j: u64) → u8:    …
fn `⸨⸩`(g: Grid, k: u8) → bool:            …

g⟦0u64⟧   g⟦1u64, 1u64⟧   g⸨3u8⸩
```

**A pair is written around what it is applied to**, so its first parameter is that and the rest are whatever stands inside -- **as
many as it likes** (4922), which is what lets `g⟦i, j⟧` be said.  A definition of one parameter would be a pair with nothing
between the brackets and is refused.  Two definitions of one pair taking different numbers are two definitions, told apart the way
a glyph's prefix and infix readings are.

**`Ps` and `Pe` decide which brackets**, as `Sm` and `So` decide which glyphs: Unicode's opening and closing punctuation, which is
every bracket there is.  Out of it come the brackets the grammar needs for itself -- `(`, `[`, `{`, `〈` and their partners -- and `⌈`
and `⌊`, which Unicode calls brackets and this language calls the larger and the smaller of two: a pair beginning with one of those
could be defined and never written.  The array brackets and the collection brackets are neither, which is why they are the two the
language itself uses and a program may define.

**A pair the language has no brackets for may be defined too**, and is then written where the language's are:

```
fn `⟬⟭`(g: Grid, i: u64) → u8:  …        g⟬1u64⟭
```

**Nothing checks that the two halves are each other's mirror.**  Unicode does not say which closer belongs to which opener, and a
program that pairs them oddly has written something odd rather than something ambiguous: what closes a use is whatever closing
bracket arrives, and the pair it makes is then looked up like any other name.  A line may be broken inside a pair a program defined,
as it may inside one the language has.

**This is what makes `pre(A'⟦I'⟧ → E')` satisfiable** by a type a program defined, which is the requirement a program's own collection
would most want to meet and the one a design thinking only about `+` forgets.

Compare: **C++**'s `operator[]`, which took until C++23 to accept more than one index, and `operator()`, which always did -- the
distinction this language does not have to draw, a pair being a pair.  **Python**'s `__getitem__`, which takes one argument and gets
several by being handed a tuple.  **Rust**'s `Index<Idx>`, one index of a type the trait names.  **Haskell** has no brackets to
define and uses `!` as an ordinary operator.  **Ada** has no indexing operator to overload at all: indexing and calling are spelled
alike there, so a function *is* the answer -- which is the other way to make a program's own collection readable, and the one a
language pays for at every call.

###### An operator the language does not have

A symbol the language gives no meaning may be defined all the same, and then written:

```
fn `⊛`(a: Point, b: Point) → Point:  …
fn `♯`(p: Point) → u8:               …

♯(p ⊛ q)
```

Such an operator has no meaning but the definition's, so a use with nothing defined is a mistake about the *definition* (4923) and
not about the character.

**It binds as tightly as multiplying, and to the left.**  One level for all of them: a program cannot declare a level and should
not be able to, the glyph set being the language's and so being its table.  Tight rather than loose so that a reader who does not
know the glyph still knows how the line groups -- `a ⊛ b + c` is `(a ⊛ b) + c` -- and stated against something a reader
knows rather than given a level of its own.  Written before one operand it binds as every operator written there does.

Compare: **Swift**, which lets a program declare new operators *and* their precedence, in named groups.  That is the larger
language this stops short of, and the thing it gives up is the ability to write a glyph that binds loosely.  **Haskell** lets a
program name an operator and declare its fixity per operator.  **APL**, **BQN** and **UIUA** have no precedence at all -- every
function binds the same way and the reading is right to left -- which is the other way to make a novel glyph safe to read.

###### The builtin operators, written out

Every operator the language has, as the definition it would be if a program wrote it.  They are not definitions a program can see or
replace; this is what the language means, said in the notation a program uses to say the same kind of thing.  `N'` stands for a
number, `I'` for an integer, `C'` for a collection and `T'` for anything.

```
fn `+`(a: N', b: N') → N'              fn `-`(a: N', b: N') → N'
fn `×`(a: N', b: N') → N'              fn `÷`(a: N', b: N') → N' ?
fn `%`(a: N', b: N') → N' ?            fn `ⁿ`(a: N', b: I') → N'

fn `⊞`(a: N', b: N') → N'              fn `⊟`(a: N', b: N') → N'
fn `⊠`(a: N', b: N') → N'

fn `=`(a: T', b: T') → bool            fn `≠`(a: T', b: T') → bool
fn `<`(a: N', b: N') → bool            fn `>`(a: N', b: N') → bool
fn `≤`(a: N', b: N') → bool            fn `≥`(a: N', b: N') → bool
fn `≅`(a: F', b: F') → bool            fn `≇`(a: F', b: F') → bool
fn `⪅`(a: F', b: F') → bool            fn `⪆`(a: F', b: F') → bool
fn `⪇`(a: F', b: F') → bool            fn `⪈`(a: F', b: F') → bool
fn `∣`(a: I', b: I') → bool            fn `∤`(a: I', b: I') → bool
fn `∣`(a: I') → bool                  fn `∤`(a: I') → bool

fn `&`(a: I', b: I') → I'              fn `|`(a: I', b: I') → I'
fn `^`(a: I', b: I') → I'              fn `~`(a: I') → I'
fn `«`(a: I', b: I') → I'              fn `»`(a: I', b: I') → I'
fn `↺`(a: I', b: I') → I'              fn `↻`(a: I', b: I') → I'

fn `⊻`(a: bool, b: bool) → bool         fn `⊼`(a: bool, b: bool) → bool
fn `⊽`(a: bool, b: bool) → bool         fn `¬`(a: bool) → bool

fn `⌈`(a: N', b: N') → N'              fn `⌊`(a: N', b: N') → N'
fn `⌈`(c: C') → E'                     fn `⌊`(c: C') → E'
fn `↓`(a: F') → F'                    fn `↑`(a: F') → F'
fn `↕`(a: F') → F'                    fn `⇕`(a: F') → F'

fn `#`(c: C') → u64 ¤size              fn `⍴`(c: C') → u64 ¤size
fn `⍴`(shape: I', c: C') → C'           fn `⫽`(a: C', b: C') → C'
fn `⇧`(c: cursor) → cursor             fn `⇩`(c: cursor) → cursor

fn `⟦⟧`(a: A', i: I', …) → E'          fn `⸨⸩`(d: D', k: K') → V' ?
```

**What the table leaves out is what a function cannot say.**  `∧`, `∨`, `and` and `or` decide whether to work their right
side out, so no signature describes them; `?` and `??` are about a result; `⌖` is about a reference; and `⌜⌝` is the grammar
saying that a type follows.  That everything else fits -- down to the two bracket pairs, whose last line is the one a program's own
collection wants -- is the argument for the notation: what the language does to values and what a program may do to its own are the
same kind of thing, said the same way.

Compare: **Ada**, which writes `function "+" (Left, Right : Vector) return Vector` -- this with quotation marks, which Ada needs
because `+` is not a legal identifier there and a language whose operators are glyphs does not.  **Haskell**, where an operator *is*
a function whose name happens to be symbols and `(+)` names it.  **C++**, which spells the name `operator+` and chooses per
operator between a member and a free function; the free form is there to answer which side decides, which a definition over both
parameters answers by writing both down.  **Rust** has a trait per operator, which answers the same question at the cost of a trait
system.  **D** reached `opBinary!"+"` -- one function over all binary operators -- after `opAdd`, and both are names rather than the
glyph.  **Python**'s `__add__` is that too, with `__radd__` beside it for the side that did not decide.  **Go**, **Odin** and
**Zig** have no operator definitions at all, on purpose; the argument that answers them here is that a requirement
(`pre(T' ⊞ T' → T')`) cannot otherwise be met by anything a program defines, so this is not sugar.

##### Conditions

**A signature may say what a function requires of its types and what it demands of its values**, in as many clauses as it has
things to say:

```
fn take(xs: A', i: I') → E' pre(A'⟦I'⟧ → E') pre(i < ⍴xs):
    xs⟦i⟧
```

**What a clause means is decided by what its expression is over, and by nothing else.**

| The expression is over | The clause is | What happens |
|---|---|---|
| values | a **condition** | it is evaluated, it must be a `bool`, and the program stops where it does not hold |
| types | a **requirement** | nothing is evaluated; what is asked is that the expression *can be written* |

So `pre(i < ⍴xs)` is a condition and `pre(A'⟦I'⟧ → E')` is a requirement, and `pre(somefunc(a))` is a condition whatever
`somefunc` does -- `a` is a value, so the call happens and the answer has to be a truth.  A clause whose operands are of both
kinds is refused (4905) rather than given a reading: the reading that makes the type operand legal is the one that stops checking
the half that is about values, and a condition quietly becoming a requirement is the one mistake this notation could make.  Two
clauses say it.

**One expression per clause, and the clauses stand between the header and the body**, each with a span of its own, which is what
lets a failure point at the clause that failed rather than at a list holding it.  **They may stand on lines of their own**, below
the header and indented further than it, and so may the colon or the brace the body begins with:

```
fn take(xs: A', i: I') → E'
    pre(A'⟦I'⟧ → E')
    pre(i < ⍴xs):
    xs⟦i⟧
```

Such a line goes on with the one before it and opens no block: `pre(` and `post(` begin nothing else, and neither does a colon or
a brace standing first on a line, so reading one there is never a guess.  The body is indented against the header's first line, as
it always is.  A newline followed by anything else still says the function has no body.

###### A condition

**A `pre` is checked once at the top of the body and a `post` before every return**, where `⎕answer` stands for what the
function answered:

```
fn twice(a: u8) → u8 pre(a > 0u8) post(⎕answer > a):
    a ⊞ a
```

**`⎕entry(NAME)` is what a parameter held when the function was entered**, which a post-condition needs where the body has
bound the name to something else since:

```
fn bump(a: mut u8) → u8 post(⎕answer > ⎕entry(a)):
    a ← a ⊞ 1u8
    a
```

Written `a` instead, that post-condition asks whether six is more than six and does not hold: the name stands for what the
parameter is at the return.

**Nothing is copied for it.**  A parameter arrives in the entry block, and a name bound to something else does not disturb the
value that arrived -- so `⎕entry` names a value the function already has.  What it costs is keeping that value alive to the
return, which the register allocator prices like any other live range.

**It takes the name of a parameter and nothing else** (4920, 4921): not an expression, not a local of the body, not a variable at
the top level.  An expression would have to be worked out at entry and its answer carried through the body, which is the cost
Eiffel's `old` has and this does not -- so what may be written is the thing that is already there.  **It may be written only in a
`post`** (4919): a `pre` is checked before any of the body has run, so there the name and the value at entry are the same thing.

Compare: **Eiffel**'s `old`, which takes an expression and copies what it answers; **D**'s `out` blocks, which have no `old` at
all and so cannot say this; **C++**'s contracts proposals, whose `@pre` and `old` have been through several spellings for the same
reason this one is restricted -- what to copy and who pays for it is the whole of the question.  Restricting it to a parameter is
what makes the answer "nobody".

**In the callee and not at the call.**  A check there that fails already names the function and, through the stack walk, who
called it -- so what a check at every call site would buy is had for nothing, and there is one copy of the code.  `⎕answer` may be
written only in a `post` (4908): there is nothing yet for it to stand for when a `pre` is checked.

**A condition is a `bool`** (4906) and nothing is converted, exactly as in an `if`.  **A condition is pure** whatever the
function it belongs to may do: one whose checking changed something could not be compiled out, and a build that compiled it out
would run a different program.  The purity rules are therefore asked of the clause on its own, with a note saying why.

**What the compiler can see is an error and what it cannot is a fault**, which is the rule arithmetic already follows.  A
condition that comes to a constant falsehood is refused where it is written (4918), since every call of the function would stop;
one that comes to a constant truth costs nothing, and the report log (`condition-holds`) is where a reader is told it was free.
A check that only becomes constant after the inliner has put the callee where it was called is removed too -- which is what makes
a written-out condition free wherever the compiler can see it holds -- but it is not reported, the optimizer having no channel for
a diagnostic about the language.

**A `pre` that does not hold exits 89 and a `post` 90**, two numbers because they have two culprits.

###### What a build does about a condition

**`--conditions=SEMANTIC` says what a condition does in this build**, and a clause says what must be true.  Three semantics:

| Written | What a condition does |
|---|---|
| `--conditions=check` | evaluate it and stop the program where it does not hold.  The default, and what a program ships with |
| `--conditions=ignore` | evaluate nothing and emit nothing |
| `--conditions=observe` | evaluate it, say so where it does not hold, and go on |

**A requirement is not among them.**  It is what makes the program type-check, so it is never evaluated and never removable, and
a build that could turn one off would be a build in which a different program compiles.  The switch is the condition half only.

**Nothing compiles only because the checks are off.**  Every semantic asks the same questions of the clause -- that it is a
`bool`, that it is pure, that its names mean something, that only a `post` may write `⎕answer` -- and what differs is what
reaches the binary.  That is the one property that makes the switch safe to have, and it is where **Python**'s `assert` went
wrong: removable enough that nobody could rely on it, so everybody wrote `if not x: raise` instead and the feature died.

**`observe` is for a run that reports everything**, which is what the run of a binary's own tests already does and for the same
reason: a run that ended at the first failure would make a reader fix one thing and run again to be told the next.  It costs more
than the write.  The helper it calls comes back, so the call destroys what the convention lets it destroy and the register
allocator keeps nothing live across it -- where the stopping path calls something that never returns and so costs nothing to call.
A build that observes is a build that asked for that.

**Which conditions this build left out is in the report log** (`condition-dropped`), one entry per clause, so that "which checks
are in this binary" is a question the log answers rather than one a reader works out from the command line that made it.  A
condition the compiler settled is there too (`condition-holds`).

**There is no `assume`.**  The C++ papers have a fourth semantic: evaluate nothing and let the compiler act as though the
condition held.  It is the only one of the four that can make a program go wrong silently, and this language has no undefined
behaviour anywhere else -- a program that cannot answer stops and says so.  Adding a build flag that introduces it is a larger
decision than choosing what to do about a check, and nothing about the three above forecloses it.

Compare: **g++**'s `-fcontract-evaluation-semantic`, which this is, with the same three of its four semantics and without the
levels -- `default`, `audit` and `axiom` are a second axis, and a clause here has no way to say which level it is on.  **Eiffel**
has assertion monitoring levels set per class in the configuration file rather than on the compiler's command line.  **D** compiles
`in` and `out` blocks out with `-release`, which is one switch for contracts, bounds checks and asserts together -- three things a
build may want to decide separately.  **Rust** asks per call site instead, `assert!` against `debug_assert!`, which puts the
decision in the program where a build cannot revisit it.

###### A requirement

**A requirement asks that an expression can be written over the types, and says nothing about any value.**  A type stands where a
value would, and means "some value of this type":

```
fn total(a: T', b: T') → T' pre(T' ⊞ T' → T'):
    a ⊞ b
```

**A type is written as a type parameter or between the lifting marks.**  `T'` bare, the apostrophe being the marker already -- a
type parameter cannot be a value's name, which is what the marks exist to settle for every other type; and `⌜u64⌝` or `⌜u8⟦3⟧⌝`
for one that is not. **This is the fourth place that reads a lift**, and the three that read one for what the type *is* keep
their meaning inside a clause:

```
⌈⌜T'⌝      the largest value the type holds, which is a value -- so the clause is a condition
⌈T'        that ⌈ applies to a T' at all, which is a type -- so the clause is a requirement
```

**The arrow says what a requirement answers.**  A name nothing has settled is settled by it, which is how a requirement reaches a
type no argument mentions; a name already settled is compared, and a requirement answering something else is not met (4901).  The
arrow may be written only on a requirement (4902): on a clause over values the call happens and the parameter list is where a
type is read off a value, so there is nothing for it to name.  What follows it is a type, written bare or in the lifting marks the
operands use -- `→ u8` and `→ ⌜u8⌝` say the same.

**A type parameter written only in the answer is settled by an arrow**, so what an operation answers is what the function does:

```
fn doubled(a: T') → E' pre(T' + T' → E'):        ※ E' is what T' + T' answers, call by call
    a + a

fn sized(a: T') → E' pre(⌜u6⌝ + ⌜u6⌝ → E'):      ※ over types written down: E' is u6, known here
    0u6
```

**A type parameter standing in an operand is read and never written** (4904).  It must already be settled -- by an argument or by
the arrow of an earlier clause -- which is why the clauses are read in the order they are written.

**A requirement is a `pre`** (4903).  It is a fact about types and does not happen at a point in the call, so a `post` is always
a condition.

**A requirement of a generic is met by the types a call gives it**, checked before the function is made for them; one that cannot
be written is reported at the requirement, with a note naming the call:

```
error: 'total' requires 'T' ⊞ T' → T'', and it cannot be written for T' = bool
    fn total(a: T', b: T') → T' pre(T' ⊞ T' → T'):
note: 'total' is asked for T' = bool here
    total(true, false)
```

That is what a bound buys at the call: the message is about the signature, which is the part a caller can read.  What it buys
in the body is under Generic Functions, "What a body may do".

**Only a generic function has requirements** (4647).  One with no type parameters takes the types its signature writes down, so a
question over types has nothing to constrain and one answer, known where it is written.  Its pre-conditions are conditions: an
expression answering `bool`, about its parameters and checked as the call is made, or one the compiler answers -- two types
compared, `pre(⌜u6⌝ = ⌜u6⌝)`.  A comparison of two types is a condition and not a requirement, whatever function it is on.

###### A bundle

**A bundle is a name for a set of requirements.**  Its lines carry no keyword, everything in a bundle being a requirement, so a
word saying so would say on every line the only thing a line there can say -- which makes the body an ordinary block, separated
by a line, a `;` or braces:

```
bundle number(T'):
    T' ⊞ T' → T'
    T' ⌈ T' → T'

bundle comparable(T'):  T' < T' → bool ; T' = T' → bool

fn bigger(a: T', b: T') → T' pre(number(T')) pre(comparable(T')):
    a ⌈ b
```

**One is asked for by applying it**, which is a clause over types like any other and means what one always means: that the
application can be written.  For a function that is one accepting those types; for a bundle it is every line it holds with the
arguments put in.  There is no keyword for it -- the clause that applies a bundle is a requirement, and marking how the compiler
answers a question would not be marking which question was asked.

**A bundle's parameters are type parameters** (4910) and its arguments are types (4912), so `pre(number(⌜u8⌝))` is how a program
asks whether `u8` is one of whatever the bundle names.  **A bundle answers nothing**, so an arrow after one and any use of one
inside a larger expression are refused (4915): a bundle is the whole of a clause or it is nowhere.

**A bundle holds no condition** (4917).  Its parameters are types and it can name no value, so a line over values would have
nothing to be about -- and a condition needs no bundle, a pure function answering `bool` being what abbreviates one:

```
fn inrange(i: u64, n: u64) → bool:  i < n

fn take(xs: A', i: I') → E' pre(inrange(i, ⍴xs)):
```

That is the whole reason a bundle exists: the value level abbreviates with a function, and the type level cannot, there being
nothing to call there.

**A bundle may be imported.**  `@[export]` on one says a file importing this module may apply it, and it is applied through the
name that module was bound to, which is how everything else a module holds is reached:

```
※ shapes.pl4g
@[export]
bundle number(T'):
    addable(T')
    T' ⌈ T' → T'

bundle addable(T'):  T' ⊞ T' → T'
```

```
let shapes := ⎕import("shapes")

fn bigger(a: T', b: T') → T' pre(shapes.number(T')):
    a ⌈ b
```

**A bundle's lines are read with the names its own file can see**, and that is the rule that matters.  `number` applies
`addable`, which `shapes` keeps to itself; a file applying `number` cannot name `addable` and does not need to.  A bundle whose
line names a function gets *that file's* function, so a file with a function of the same name does not change what the bundle
asks for -- which is what keeps a bundle meaning one thing everywhere it is applied.

**A bundle without `@[export]` is the file's own** (4104), and that is told apart from a name the module does not have at all: the
module keeps every bundle it wrote, so "it is not yours to apply" and "there is no such thing" are two different things to be told.

**A bundle may apply another** and a cycle among them is refused (4913) -- once every bundle is known and whether or not anything
applies one, a cycle being a fact about the bundles rather than about any call.  Nothing conforms to a bundle: it is
substitution, so there is no coherence rule and no orphan rule, and a type admits `⌈` because `⌈` works on it.

###### Comparisons

**Eiffel** invented this: `require`, `ensure`, `old`, class invariants, and the rules that weaken a pre-condition and strengthen
a post-condition down a hierarchy -- a chapter this language does not have to write, having no subtyping.  **D** has both halves
and did not unify them: `in` and `out` blocks are the value half, `out(r)` names the answer as `⎕answer` does, and template
constraints are a separate notation for the type half, so a D programmer writes one requirement twice.  **C++** has the type half
as concepts, where a concept is a bundle and `requires Number<T>` is `pre(number(T'))`; its contracts have been proposed for four
standards running.  A concept is a `bool` expression, so concepts compose with `&&` and order overloads by subsumption, which a
bundle gives up and which is what it saves.  **Rust**, **Haskell** and **Swift** answer the type half with traits, classes and
protocols, each of which is also a type something can be behind, and each of which brings coherence with it.  **Zig** and **D**
make a bundle unnecessary by having functions over types, which needs types to be values -- the design this is a restricted form
of, restricted because type parameters here are inferred and not passed.  **Go** has neither and panics.  **Odin**'s `when`
clauses are the type half unnamed.  **Python**'s `assert` is so removable that nobody relies on it, which is the cautionary case
for the removable half.  **Wolfram** writes the value half as `PatternTest` -- `f[x_?NumericQ]` -- and the type half as
`f[x_Integer]`, a type in an argument position, which is this notation in the one language on the list where a type-level
question would have nothing to mean.


##### Special Functions

Some functions have to be treated special.  The attribute syntax denotes this status of the function alongside the function definition.
Some special functions:
- the startup function.  This is the function control is transferred to after the startup phase is completed.  Exactly one function can
  be marked this way.  If no function is marked as such compilation fails.
- constructor function.  These are functions that run before control is transferred to the startup function.
- destructor function.  Similar, just that they are called when control is handed back by returning from the startup function.
- test function.  This is a function run as part of the testing.  Multiple functions can be marked as such.  Different attributes
  indicate which of the three types of tests this is.

The attributes that mark them are:

| Attribute | Meaning |
|---|---|
| `@[startup]` | the startup function.  Exactly one per program |
| `@[constructor]`, `@[constructor(priority)]` | runs before control is transferred to the startup function |
| `@[destructor]`, `@[destructor(priority)]` | runs when the startup function returns |
| `@[test(always)]` | a test that runs after a build and when the program is first started |
| `@[test(build)]` | a test that runs when a build finishes |
| `@[test(suite)]` | a test that runs when a testsuite run is requested |
| `@[build]` | the function the compiler runs to find out what to build.  At most one per program |

The three kinds of test are one attribute with a parameter rather than three attributes, because they are three answers to one
question.  These attributes exclude one another: a function is one of these things or none of them.

##### The build function

**A program may say how it is built, in the language it is written in.**  A function marked `@[build]` is **run by the compiler**
rather than compiled into anything, and what it leaves behind is what gets built:

```
let std := ⎕import("std")

@[build]
fn build(b: &mut std.Build):
    b⌖.output_dir ← "out"
    foreach name := ⟦"hello", "tool"⟧:
        std.add_executable(b, name, ⟦name ⧺ ".pl4g"⟧)
```

**At most one function per program is marked this way.**  What it leaves behind is the whole description of the build, so two of
them would be two descriptions with nothing to say which was meant (7002).  It takes exactly one parameter -- a mutable reference
to `std.Build` -- and answers with nothing (7004): the compiler hands it the object and what it has to say it says by writing into
it.  A file holding one describes a build rather than being a program, so it needs no startup function and nothing of it is
compiled.

**Where the build function is, is where the build is described.**  A source named on the command line that holds one is a build
file whatever it is called; a command line naming no source at all looks for `build.pl4g` where the compiler was run (1018).  A
command line that names sources holding no build function compiles them the way it always did, which is what every command line
that worked before this still means.

**Everything in it must be something the compiler can work out** before the program runs (7000): numbers, truth values,
characters, strings, runs of them, records, references, the ordinary control flow, and calls to functions written in the same
program.  There is no floor under how long it may take to do that, so there is a ceiling instead: an evaluation that has not
finished after a hundred thousand steps is given up on (7001), which is what a loop with no end and a recursion with no end both
come to.

**What it may say** is what a command line would otherwise have said.  The fields of `std.Build` are the settings -- where what is
built goes, what to build it for, how hard to work on it, how much stack a program gets -- and a field left alone keeps what the
command line said, so a build file says only what it means to decide.  What to build is added with `std.add_executable`, which
takes a name and the run of sources to build it from; the sources are found beside the build file, so a build file is a way into
a project from anywhere.  `std.option` and `std.option_flag` ask what the command line said about a name, which is written
`-Dname=value` or `-Dname`, so that a build file can be told something from outside without being edited.

**A build reads the environment the compiler was run with.**  `⎕environ` answers it -- a build is run by the compiler, so the
process it asks about is the one whose command line the reader typed -- and `std.Build.env` is that same dictionary under the name
the field has:

```
b⌖.output_dir ← ⎕environ⸨"PL4G_OUT"⸩ ?? "out"
b⌖.output_dir ← b⌖.env⸨"PL4G_OUT"⸩ ?? "out"      ※ the same question, asked of the object
```

It is read-only for the reason the program's is: the type says so (4598).  It differs from `std.option` in where the answer comes
from and in nothing else -- an option is what this command line said and a variable is what the surroundings say -- so a build
file that wants either asks both and says which it prefers.

The functions the compiler provides are marked `@[builtin]` where the `std` module declares them: they have no body and no symbol,
and what they do is change a description the compiler is holding.  Calling one from a program is calling something that is not
there, and is refused (7006).

Compare: **Zig**, whose `build.zig` is this and is what this is modelled on -- a function handed a `*std.Build`, run before
anything is compiled, that says what the compilation is; the difference is that Zig compiles that file into a program and runs it,
where this is worked out by the compiler itself and therefore cannot open a file or start a process.  **Make**, **CMake** and
**Meson**, which are separate languages with their own rules, so that a project is written in two languages and the second one is
nobody's favourite.  **Cargo** and **Go**, where the build is a manifest rather than a program, which is simpler until the day it
has to decide something.  **Bazel** and **Buck**, whose build files are a real language (Starlark) that is deliberately not the
language being built.  What this has that none of them has is that the build is written in the language, checked by the compiler
that checks the program, and run by the compiler that compiles it.

The startup function takes what the program was started with, or nothing, and returns `u8`:

```
@[startup]
fn main() → u6:
    0

let std := ⎕import("std")

@[startup, impure]
fn main(init: &mut std.Init) → u6:
    ※ the devices the process inherited, as descriptors and not as numbers
    ⎕drop(std.write(&mut init⌖.io.output, ⎕bytes("hello\n")) ?? 0)
    0u6
```

The value it returns becomes the exit status of the process.  The one parameter it may take is **a reference to the record the
`std` module calls `Init`**, and no other: the entry point has the devices the process inherited to hand over and nothing else, so
a parameter of any other type would be left undefined (4404).  It is `Init` rather than the descriptors themselves so that the
arguments, the environment and whatever else a program is started with have somewhere to go without changing the one signature
every program writes.  Which type that is is settled by where it was written down -- the `std` module the installation provides --
and not by its shape, so a record a program defines for itself and calls `Init` is a record it defined for itself.

**What is handed over is where it is.**  The record is an object the image carries, in the writable data; the entry point fills it
in and passes its address.  That is what lets the record *grow* without this signature changing, and what lets a program **change
what it was started with** -- `init⌖.io.output ← std.Writer(.fd ← 2i32)` makes what it writes go where it reports.

**The entry point reads the arguments into it.**  What the kernel leaves at the stack pointer is the count, then that many
pointers, then a null, and that is the only moment the stack pointer says so -- everything after it puts something there.  A
program that takes no parameter carries no such object, reads no arguments and asks the system for nothing.

`init⌖.io.input`, `init⌖.io.output` and `init⌖.io.errors` are what the process inherited open.  Taking `&mut init⌖.io.output` is
exclusive by the rule that already refuses a second `&mut` to a place, so **two names for one device is a thing the compiler
refuses rather than a thing a lock prevents**, and nothing is checked while the program runs.

The return type is `u8` because that is how wide an exit status is.  What a program hands to the system is truncated to eight bits
before anything can observe it, so a wider type would let a program state a status that cannot arrive -- written with a wider type,
`return 256` would compile and the process would exit with 0, which is precisely the kind of surprising interpretation of a value
this language does not admit.  With `u8` the literal is out of range and the compiler says so, where it is written.

Compare C, where `main` returns `int` and the value is silently truncated; Go, where `main` returns nothing and the status is set
by calling into the library with an `int` that is likewise truncated; and Rust, whose `ExitCode` is built on a `u8` for this same
reason.  The choice here is the most explicit of these: one signature, the status is the returned value and nothing else, and the
type says what a status can actually be.

##### Exit statuses the runtime reserves

**A program the runtime stops leaves through exit with a status, never through a signal.**  A signal is not a status: a shell
reports one as 128 plus the number, which collides with whatever a program might have chosen to exit with, and a caller has to
know to look for it.  A program that dies of a signal really did die of one, and that is worth being able to believe.

**79 through 127 are reserved** for stops the runtime reports.  That leaves the ranges around it to their owners:

| Range | Whose |
|---|---|
| 0–63 | the program's own, the startup function's result, which is a `u6` |
| 64–78 | `sysexits.h`, which is nobody's to take |
| 79–127 | the runtime's, for a stop it reports |
| 128–255 | a signal the program really died of, as the shell reports it |

**The middle range is left alone.**  `sysexits.h` has named 64 through 78 since 4.0BSD -- `EX_USAGE`, `EX_DATAERR`, `EX_NOINPUT`
and the rest -- and a great deal of software written since reads them.  A runtime that stopped a program with 64 would be saying
"the command line was wrong" to everything that knows the convention, which is the opposite of what it means.  So the runtime
begins at `EX__MAX` plus one and the two never meet.

**Each kind of stop has a number of its own.**  The message says which operation, in which function, at which line, and says it
better than a number could -- but a message is for a person and a status is for a program.  A caller that wants to retry one
failure and give up on another is reading the status, and an answer that will not fit, an index outside its array and an
allocation that could not be met are three different things.

| Status | What stopped the program |
|---|---|
| 79 | a stop with no more particular number — nothing uses it, and it is there for the kind not yet thought of |
| 80 | an answer that will not fit its type: a sum, a difference, a product, a shift, a rotation |
| 81 | an operation whose answer is not a number, which is a floating-point one that came to an infinity or to not-a-number |
| 82 | an index or a slice outside what it names, and the largest or smallest of nothing |
| 83 | a number turned into a code point that is not one |
| 84 | a walk over a list used after it ended, or moved off either end |
| 85 | the system would give no more memory |
| 86 | the program ran off the bottom of its stack |
| 87 | the processor is not the one the program was built for, or the system has turned off registers it uses |
| 88 | a test the binary runs did not pass |
| 89 | a pre-condition written in a signature did not hold, which is the **caller's** fault |
| 90 | a post-condition written in a signature did not hold, which is the **callee's** |

**89 and 90 are two numbers because they have two culprits.**  A caller acting on
a status can tell "I called this wrongly" from "the thing I called is broken",
which is the whole reason the range has a number per kind; and the stack walk
beside the message already names which caller.

**86 is the one stop a program could not report for itself**: there is no room left to report it in, which is why the handler
that does report it runs on a stack of its own.  **87 is the one that happens before the program has run at all.**  **88 comes
after every test has been run and every failure named**, a run that ended at the first failure being one that makes a reader fix
one thing and run again to be told the next.

**The startup function answers a `u6`**, which is the first range and nothing else.  The type is what says the rule rather than
a paragraph a reader has to have read: a program that tries to exit with 200 is refused where it writes it, and one that works
its status out arrives at a number that is already in range.  It also means the status has to be *worked out* in `u6` -- there is
no conversion between integer widths yet -- so a program that computes something wider says so and answers with something else.
And it means a program **cannot** return a status in either reserved range, so a caller reading one knows it did not come from
the program.

**The reservation is what makes a status enough to say it with.**  Without it, a runtime stop and a program that chose to fail
would be the same number, which is the objection that used to argue for the signal; with it, a caller can tell the cases apart
without knowing anything about the program.

Compare: `sysexits.h`, whose range this begins after rather than inside -- it is advisory where this is the compiler's own, and
the way to keep a convention one cannot enforce is to stay out of its way; the shell's 128 plus the signal number, which is the
reason the top range is spoken for and not something this chose; Python, which exits 1 for an uncaught exception and so cannot be
told from a program that meant to; Go, which exits 2 for a panic and 1 for a failing test and has nothing to say about the rest;
and Rust, which exits 101 for a panic and has been unable to change it since.  What none of them has is a range reserved on both
sides with a number per kind inside it, which is what lets a caller act on what happened rather than only notice that something
did.

A constructor and a destructor take no parameters and return `void`, because the sequence that calls them has nothing to pass and
nowhere to put a result.


#### What the Image Says It Was Built From

**Every image carries a bill of materials**, in two sections that are always emitted.  There is no flag: one that a flag turns
off is one nobody can rely on being there, and the question it answers -- what is this built from -- is asked of binaries nobody
thought to ask about at the time.

`.sbom` is a table of rows, three four-byte fields each: where the hash is, what kind of thing the row is about, and where the
name is.  The two offsets are into `.sbomstr`, which the table's `sh_link` names.  Both are loaded and read-only: the table holds
offsets into the strings, so one without the other would be a table a running program could not read.

The hashes are **SHA-256, written out as all sixty-four hex digits**, so that a row can be checked against what `sha256sum`
prints.  There are five kinds:

| tag | what it is about |
| --- | --- |
| 1 | the compiler, by the name it calls itself |
| 2 | one source file |
| 3 | every source, in the order they were read |
| 4 | one function |
| 5, 6, 7 | one type, one variable, one unit |

The hash of every source together is the hash of *their hashes*, in order -- which says the same thing as reading them all again
and is what lets the whole be checked without the parts.

**What is hashed is the tokens and not the text.**  A program means the same thing written with indentation or with braces, with
one space or four, with a comment in the middle or without, and `0x10` and `16` are one number; a hash over the bytes would call
all of those different programs.  So the token stream is normalized first:

- the marks that say a block begins and ends become one mark each, whichever notation was used -- an indent and a brace are one
  thing, and so are a dedent and the closing brace;
- the colon that introduces a layout block goes away, because the mark after it already said a block begins;
- a statement separator is one mark whether a newline or a semicolon was written;
- a literal is written as the value it stands for rather than as the characters it was written with.

Two spellings of one program therefore have one hash, which is the only thing that makes a hash of a definition worth recording:
a definition whose hash moves when somebody reformats it says nothing about whether the program changed.

A definition's hash covers its attributes as well as its body, because what a definition says about itself is part of what it is.
Every definition of every source read is named, including one the image did not need and dropped: what the bill of materials is
about is what went in.

#### Generic Functions

**A name with a mark after it is a type a call settles.**

```
fn largest(a: T', b: T') → T':
    a ⌈ b

largest(1u8, 2u8)                 ※ u8
largest(5u32, 3u32)               ※ u32
```

`T'` is a **type parameter**: it stands for whatever type a call turns out to give it.  The mark is written after the name and
not before it, so that the name reads as a name and the mark as a note about it -- which is what the prime has meant in
mathematics for three hundred years and in ML and Haskell for fifty.

**A type parameter is declared by being used.**  What a function's type parameters are is which marked names its parameters
mention, so there is no list to write and none to keep in step with the parameters.  A name a program has not defined and that
carries the mark is one, which is also what keeps a mistyped type from quietly becoming a parameter: an unmarked name that is not
a type is still unknown.

**The types come from the arguments.**  Each argument is lowered where it stands, as any call's is, and what it turns out to be
says more about the types -- so an argument is lowered knowing what the ones to its left already said, and a literal with no
suffix takes the type an earlier argument settled:

```
largest(9u8, 4)                   ※ the 4 is a u8, because the 9u8 said so
```

**The type a parameter is written with says how to read the argument's.**  `T'⟦⟧` against `u8⟦4⟧` says `T'` is `u8`, and
so do `&T'`, `[T']`, `⸨T'⸩`, `〈T', U'〉` and `fn(T') → T'` against the shapes they name.  Where the two are not
the same shape there is nothing to read (4556), and where two places in one argument say different things about one parameter
they cannot both be right (4557).

**Every type parameter stands in a parameter's type** (4555).  One written only in what the function answers with is one no call
could settle, and a call that wrote its types outright would be a second way of saying what the arguments already say everywhere
else.

**A requirement moves the message to the signature.**  `pre(T' ⊞ T' → T')` says what a type parameter must support, and a
call whose types do not is refused at the requirement with a note naming the call -- so the part a caller can read is the part
that says what went wrong.  A requirement is checked *before* the function is made for those types, and its arrow may settle a
type parameter no argument mentions, which is the one thing that reaches past 4555.  Conditions above says what a requirement is
and how one is written; [constraining-generics.md](constraining-generics.md) is the reasoning that chose the notation.  It is
also the whole of what the body may do with the type (below).

##### What a body may do

**A generic function is checked where it is written, against its requirements alone.**  Nothing is known of a type parameter but
what the function's `pre` clauses say, so an operator or a function applied to a value of one is allowed exactly where a
requirement names it -- the requirement itself, or a line of a bundle it applies, with the operands' types as written -- and
answers what the requirement's arrow says (4645).  Where a requirement has no arrow its answer is a type nothing more may be done
to.  **The operation has to be the one the requirement writes, over the same types**: `pre(⌜u6⌝ + ⌜u6⌝ → E')` holds of every call
whatever `T'` is, so it says nothing of `a + 1u6` with `a` a `T'` -- that needs `pre(T' + ⌜u6⌝ → E')`, which a note under the
message spells out (4648).

```
fn largest(a: T', b: T') → T':
    a ⌈ b                          ※ 4645: nothing says T' admits ⌈

fn largest(a: T', b: T') → T' pre(T' ⌈ T' → T'):
    a ⌈ b                          ※ and now something does
```

**It is checked whether or not anything calls it**, so a mistake in one is found by whoever wrote it rather than whoever first
calls it, and a name no program defined is reported in a generic nobody calls.  **There is no substitution failure**: what a type
must support is read off the requirements and checked at the call (4900), before anything is made for those types, and nothing an
instantiation finds can quietly make a call mean something else.

**A generic function calling another with its own type parameters hands on what it was given**, and all it knows of those types
is what its own requirements say -- so every requirement of the one called has to follow from the caller's (4646).  What a type
must support reaches the signature a call reads, up the whole chain.  `std.⍕` calling `std.text` asks nothing, because `text`
asks nothing.

**Some things ask nothing of the type.**  A value of a type parameter may be bound, handed on and answered, and taken apart by
the shape its parameter is written with -- an element of `T'⟦⟧`, what `&T'` names, a member of `〈T', U'〉` -- and a lambda the
function was handed may be called.  A literal with no suffix beside one has no type to take from it, and is refused as any other
operation the requirements do not name is.

**A `comptime` construct is the exception, and the one place the old rule stays.**  `comptime if` and `comptime foreach` ask
what the types *are*, which nothing knows where the function is written: their arms are checked where a call says, for the types
it gives, what the arm was chosen for being what licenses what it does.  That is how `std.text` takes a value of any type apart.
The report log says which functions are checked where they are written (`generic-checked`) and which leave `comptime` arms to the
call (`generic-deferred`); the definition is kept as written, so that the rest of the check is made then, once per set of types.

Compare: **Rust**, **Swift** and **Haskell**, which check a generic body once against its bounds, and where this comes from;
**C++**, whose concepts constrain the call and still check the body per instantiation, unconstrained operations and all -- and
whose substitution failure is the thing not inherited; **Go**, which checks the body against the constraint's methods and type
sets; **Zig** and **D**, which check per instantiation, compile-time code being their whole mechanism -- the one corner kept here.

**One function is made per set of types**, not one per call: a second call saying what an earlier one said gets that same
function.  The two are told apart by their symbols on their own, a symbol being the signature written out.  Which sets of types a
generic function was compiled for is written to the report log (`instantiate`), since the program said nothing about them.

**A generic function is not the runtime's entry point, a constructor or a test** (4558): each of those is one thing the program
has, and something written once per set of types is none of them.

**A generic function may be exported**, and is reached by name through the module or -- where its name is a glyph -- by being in
force there:

```
※ text.pl4g
@[export, impure]
fn text(v: T') → str: …
@[export, impure]
fn `⍕`(v: T') → str: text(v)

※ and in a file that imports it
let t := ⎕import("text")
t.text(1234u64)
⍕true
```

**An instance made for an importing file is checked where the function was written**, not where it was called: its body names
what its own file can see, and a name the calling file happens to have of the same spelling does not change what the body means.
That is the rule a bundle's lines already follow and it is the same reason -- substituting *into* a definition is not
substituting the definition into the place that used it.  The arguments are the calling file's expressions and are lowered
there, which is the line between the two.

**A generic body with `comptime if` is what this language has in place of overloading.**  A glyph has one meaning per number of
operands and two definitions of one name are a duplicate (4001), so a type does not get its own definition of `⍕` -- the one
definition tests the type while the compiler runs:

```
fn `⍕`(v: T') → str:
    comptime if ⎕typeof(⌜v⌝) = ⌜bool⌝:
        if v: "true" else: "false"
    comptime elif ⎕typeof(⌜v⌝) = ⌜str⌝:
        v
    else:
        …                        ※ every integer width, in one body
```

An arm not taken is not checked, which is what lets the arms be of different shapes; and nothing in the arithmetic branch needs
to know how wide the integer is, the type it is compiled for settling that.  **A program says what its own type's text is by
writing the definition for its file** -- which wins over an imported one silently -- testing for the types it cares about and
handing the rest back to the module's under its name.  So the extension point is an arm and not an overload, which is why a
module exporting one of these exports the same body twice: once as the operator and once under a name.

This is **Zig**'s answer to the same problem, `comptime` branching on `@typeInfo` where C++ would specialize a template and Rust
would write a second `impl`.  What it costs is that the arms are a closed list in one place; what it buys is that there is no
overload resolution to specify, and nothing about which of two candidates is better.

Compare: **C++** templates, whose instantiation-time checking this is, and whose `template<typename T>` this leaves out -- the
parameters being named by being used, as C++20's abbreviated `void f(auto x)` does.  **Rust** and **Swift**, which check a
generic body once against bounds written on it, which is stronger than checking a body per instantiation against requirements a
caller is held to.  **Go**, whose type
parameters are written in brackets and constrained by interfaces.  **ML** and **Haskell**, whose `'a` and `a` are inferred rather than
written, and from whom the mark is borrowed.  **Zig**, where a type is an ordinary value at compile time and a generic function
is a function taking one -- the most economical answer of the lot, and one that needs types to be values.

#### Names in the Generated Program

The name a function is known by in the generated program is its signature written out: the name, the parameter types in
parentheses separated by commas, and then the result type.

```
fn main() → u8                            main()u8
fn absdiff(a: i32, b: i32) → i32          absdiff(i32,i32)i32
fn take(p: ptr<u8>, n: u64) → void        take(ptr<u8>,u64)void
fn apply(f: fn(i32) → i32, x: i32) → i32  apply(fn(i32)i32,i32)i32
```

A function belonging to a module is named within it, the module name first and separated by a full stop:
`text.utf8.decode(ptr<u8>)u32`.

The type names are the normalized ones: the same names the language uses, with no spaces and no glyphs, so that a name stays one
word.  A composite type is written out structurally, with its fields in the order they were declared -- the compiler may reorder
the *layout* of a product type, but the declaration is what identifies the type.

**Nothing is encoded.**  There is no substitution table, no length prefix and no abbreviation for a type that appears twice.  The
name is longer than an encoded one and it is readable exactly as it stands, which is the trade this language makes: a symbol table
listing, a disassembly, a profile and a backtrace all show the signature without a tool in between, and no demangler exists because
there is nothing to undo.  Compare C++, whose encoding compresses a name to the point where reading one requires a program to do
it; Rust and Swift, which encode similarly; Go, which writes a readable name but leaves the types out of it; and C, which uses the
bare name and so cannot tell two functions apart at all.

The bare name is used in one case: a function marked `@[cdecl]` keeps it.  The point of asking for the system's convention is to
be reachable from a world that has never heard of this language, and that world knows the function by the name it was given.

Because the result type is part of the name, two functions that differ only in what they return are different names.  Nothing
depends on that yet; it is what would let a result take part in choosing between functions of one name, which is not specified.


Runtime
-------

The I/O functionality is asynchronous by default.  All I/O operations, as well as some others,
are handled through the `io_uring` system call.  I/O is represented through objects which have
a representation in the language itself.
