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

Compare C's `42U` and `42L`, Rust's `42u8`, and C#'s `42L`; the form here is Rust's, and the fallback is Odin's.

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

#### Unicode glyphs and their substitutes

Where a construct is written with a Unicode glyph, the glyph is canonical: it is what the compiler itself emits, what the
formatter produces, and what the documentation shows.

Whether an ASCII substitute exists is decided per glyph, by one rule: **a substitute may only be a sequence of more than one
character.**  A single ASCII character is never a substitute, because it would then be unavailable to every future feature of the
language.  So `->` is accepted for `→`, while `#` is *not* accepted for `※` and remains free.

Using an accepted substitute is not an error.  A warning reports it for anyone who wants their sources in canonical form; it is
off by default, since the substitute is accepted usage and not a defect.

Compare Fortress and Agda, which admit glyphs with no ASCII spelling at all, with Haskell and Idris, which accept both
everywhere.  The rule above sits between them and, unlike either, states a reason that decides each case on its own.

### Types

The primitive types are:

| Type | Meaning |
|---|---|
| `i8` `i16` `i32` `i64` | signed integers of the stated width |
| `u8` `u16` `u32` `u64` | unsigned integers of the stated width |
| `f32` `f64` | binary floating-point numbers of the stated width |
| `bool` | truth values |
| `void` | the unit type: one value, no information |

The width is always part of the name.  There is no type whose size depends on the target, because that would be exactly the kind
of surprising interpretation of a program that this language does not admit.

Considered were `int32`/`uint32`/`float64` (Go, C#, D, Java), `s32`/`u32` (the Linux kernel and much embedded code), and the
unsized `int`/`long` of C.  The chosen spelling is that of Rust, Zig, Odin, WebAssembly and LLVM's own IR; it is the shortest
unambiguous form, and it is short enough to repeat in every diagnostic and every dump of the intermediate representation without
making them harder to read.

A literal that its type cannot represent is an error rather than a truncation or a wrap-around.

### File Structure

At the top level of a file one can find:
- module handling
- compile-time expressions as assertions and contracts
- type definitions
- variable definitions
- function definitions


#### Attributes

An attribute states something about an object that is not part of the object's own description: how a function takes part in the
life of the program, which foreign convention it follows, where a variable is placed.  Attributes apply to any kind of object --
functions, types, variables, modules -- and not only to functions.

An attribute list is written before the definition it applies to:

```
@[NAME]
@[NAME, NAME(ARGUMENTS), ...]
```

Several attributes may be given in one list, and several lists may precede one definition.

Arguments are written as in a call: positional arguments first, named arguments after.

```
@[startup]
@[test(build), inline]
@[abi("sysv64", variadic=false)]
@[align(64), section(name=".hot")]
```

Each attribute declares the kinds of object it accepts and the parameters it takes, so the compiler checks the number, the order,
the names and the kinds of the arguments.  An attribute name the compiler does not know is an error, never an ignored annotation:
a misspelling must not be able to silently drop a property the program depends on.

Considered were `#[...]` (Rust), `[[...]]` with namespaces (C++11, and the aspect clauses of Ada), `{. .}` pragmas (Nim),
`@(...)` (Odin), a bare `@name` in the manner of a decorator (Python, Java, D), `pragma` statements (Ada), and magic comments
(Go's `//go:...`).  The chosen notation takes the list-in-brackets shape from Odin and Rust and puts it behind `@`, which no other
construct uses: a parser can commit on the first character, which keeps the grammar context-free, and `#` stays free for a future
feature.  Parametrization follows Python and C#, so that the common one-argument case stays terse while an attribute with several
parameters remains self-describing.


#### Variable Definition

A variable is defined with:

```
var NAME: TYPE = VALUE
```

The colon is always written.  The type after it may be left out, in which case the variable's type is the type of its value:

```
var counter: u64 = 0u64     ※ the type is stated
var total := 42u8           ※ the type is the value's, which its suffix names
var spaced : = 42u8         ※ the same; ':=' is two tokens, not one
```

**A variable is always given a value where it is defined.**  There is no form that leaves one uninitialized.  A variable without a
value would have to hold something the program never named, and no rule about what that something is would make it named; C leaves
it undefined, Go and Java fill it with zeroes, and both answers are ones a reader has to know rather than read.

The same form defines a variable at the top level and inside a function.  A variable at the top level exists for as long as the
program does and lives in the image; one inside a function exists while the function does.  Naming either yields its value.

Assignment to a variable after its definition is not specified yet.

Considered for the notation: `x: u8 = 3u8` with `x := 3u8` as the short form, after Go and Odin; and `let`, after Rust, Swift and
ML.  The keyword was kept because it lets a parser commit on the first token of a definition, which is what keeps the grammar
context-free and the compilation parallelizable -- the same reason `@[` introduces an attribute.  `let` was not used because in
every language that has it, it binds something that does not change.

#### Function Definition

Functions are defined at the top level of a file with the syntax:

```
fn NAME(ARG1: TYPE1, [ARGN: TYPEN]) → RETVALTYPE BLOCK
```

where `NAME` is a valid identifier naming the function, `ARG?` are parameter names, `TYPE?` are type descriptions for the parameter,
`RETVALTYPE` is the type of the return value.  `BLOCK` is the code of the function, in one of the two notations.  In layout format,
the function header is followed by a colon, a newline, and then the properly indented code.  When the function header is followed by
a `{` it uses the explicit syntax and continues until the respective closing `}`.

Function return values are specified with the `return` keyword.  The function immediately returns and the remaining statements in the
block are ignored (the compiler must issue a warning in this case).  If the `return` statement is the last statement in the function
then the `return` keywords can and should be skipped (a warning is issued in this case).


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

The three kinds of test are one attribute with a parameter rather than three attributes, because they are three answers to one
question.  These attributes exclude one another: a function is one of these things or none of them.

The startup function takes no parameters and returns `u8`:

```
@[startup]
fn main() → u8:
    0
```

The value it returns becomes the exit status of the process.  Control is transferred to it without arguments, so a signature with
parameters would leave the arguments undefined.

The return type is `u8` because that is how wide an exit status is.  What a program hands to the system is truncated to eight bits
before anything can observe it, so a wider type would let a program state a status that cannot arrive -- written with a wider type,
`return 256` would compile and the process would exit with 0, which is precisely the kind of surprising interpretation of a value
this language does not admit.  With `u8` the literal is out of range and the compiler says so, where it is written.

Compare C, where `main` returns `int` and the value is silently truncated; Go, where `main` returns nothing and the status is set
by calling into the library with an `int` that is likewise truncated; and Rust, whose `ExitCode` is built on a `u8` for this same
reason.  The choice here is the most explicit of these: one signature, the status is the returned value and nothing else, and the
type says what a status can actually be.

A constructor and a destructor take no parameters and return `void`, because the sequence that calls them has nothing to pass and
nowhere to put a result.


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

The bare name is used in one case: a function that declares a foreign calling convention keeps it.  The point of declaring one is
to be reachable from a world that has never heard of this language, and that world knows the function by the name it was given.

Because the result type is part of the name, two functions that differ only in what they return are different names.  Nothing
depends on that yet; it is what would let a result take part in choosing between functions of one name, which is not specified.


Runtime
-------

The I/O functionality is asynchronous by default.  All I/O operations, as well as some others,
are handled through the `io_uring` system call.  I/O is represented through objects which have
a representation in the language itself.
