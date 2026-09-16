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

**What it comes to is two addresses**: where its code is, and where what it brought in with it is.  That is one type whether it
brought anything in or nothing, so either stands where a `fn(…)` is wanted -- which is what lets a function take one without
knowing which it will be given:

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

**A lambda does not leave the call that made it** -- it may not be what a function answers with (4549), nor what a variable at
the top level holds (4550) -- because what it brought in belongs to that call.  It is the rule a reference follows, and lifetime
annotations will lift both at once.  What is left is where a lambda earns its keep: bound to a name, handed to a parameter, and
called through whatever holds it.

**A call through one is a call to whatever it holds**, so nothing about the callee is known: a function that makes one is impure,
because what it calls may do anything.

Compare: **C++**'s lambdas, whose capture list this is, down to the `&`, the `[=]` and the `[&]`.  **Rust**'s closures, which
infer what they capture and sort themselves into three traits by what they do with it; **Go**'s and **JavaScript**'s, which
capture by reference and keep the variables alive by garbage collection; **Java**'s, which capture by value and require what they
capture to be effectively final.  The lifetime question every one of those answers somehow is the one answered here by not
letting a lambda leave the call -- the blunt answer, and the same one references got.

#### Narrowing

**`⎕narrow(EXPR, ⌜TYPE⌝)` makes a value of a narrower type out of one of a wider**, and says so where it will not fit.

```
let n: u8?⎕narrowing = ⎕narrow(count, ⌜u8⌝)
let small: u8 = n ?? 0u8
```

Nothing in this language widens or narrows on its own, so a value that is to become one of another type is written as becoming
one.  What makes narrowing different from widening is that it can fail, so **what it answers with is a result**: the value where
it fits, and why not where it does not.

**The error carries which way it did not fit**, as a value of `⎕narrowing`, an enumeration the compiler provides:

| value | when |
| --- | --- |
| `overflow` | the number is above the top of the type |
| `underflow` | it is below the bottom of the type |
| `sign` | it is negative and the type has no negative values |

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
t.pl4g:6:5: pl4g: addition that does not fit in 'main'
```

The message is built whole when the program is compiled -- the compiler knows which operation it was, in which function, at which
line -- so what runs at the moment of the fault is a write and a trap: no formatting, no number to turn into text, no allocation,
nothing that could itself fail.  That matters more here than anywhere else, because this is the code that runs when something has
already gone wrong.  It goes to standard error through a raw system call, that being the only place a program depending on nothing
from the system can write to.

The program then **dies by a signal at the point of the fault**, with its stack still standing, which is what a debugger wants to
be handed.  It is the same signal on every target.  A status would say less and could not be told from a program that meant to
exit with it.

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
arena the compiler provides -- which is a change that outlives the call, so **a function that joins strings says `@[impure]`**,
the same as one that puts something in a collection.  Joining a string to an array, or an array to a string, is refused (4489):
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

**No value of one can be written yet.**  The language has no syntax for making a product or a sum, none for reading a field, and
none for asking which variant a sum holds -- the last of which needs control flow the language also does not have.  The types are
therefore declarable and not yet usable: a function that takes or answers with one compiles as far as the code generator, which
says it cannot generate for it (8501).  The to-do list carries the three questions.

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
name.  `&mut` further needs a place the program could have written where it stands (4537), since a reference that allows writing
is a way to write it.

**`⌖` is written after what it reads through**, so reaching further into what it answers reads left to right without brackets:
`rows⌖⟦2⟧` is an element of what `rows` names.  That is what Pascal, Modula, Ada and Odin put a mark after a pointer for, and it
is why the mark is not a prefix as C's is.  Written after anything that is not a reference there is no place to read (4534).

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
lifetimes it is, and there is a word for each:

```
fn counter() → &mut static u8:    ※ as long as the program
    &mut total

fn first(v: &u8) → &u8 from v:    ※ as long as what v named
    v
```

**`static` belongs to the type and `from` to the signature.**  A lifetime fixed once and for all is a property of the reference
itself, so it is written where the rest of the type is, after `mut` and before what is pointed at: `&mut static u8`.  A lifetime
borrowed from a parameter is a relation between two things in the signature, and no type could name a parameter, so it is written
once, after the type, for the whole answer.  Saying neither is refused (4562): there is no default, because the two answers differ
and either guess would make a promise the program did not.

**`from` names a parameter of this function** (4560), and what comes back really has to come from it (4561).  The compiler walks
the answer back the way provenance is walked everywhere else -- reading the parameter out of its storage, offsetting it, reading
the same bits as another type, and through a call that made the same promise about its own parameter -- and refuses a reference
reached by none of those.  A reference that lasts as long as the program keeps any promise, so answering `from v` with a variable
at the top level is allowed.

**The caller works out the rest.**  A function promises no more than its parameter's lifetime, so the same call read two ways
gives two answers: `first(&total)` for a variable at the top level answers with a reference that lasts as long as the program, and
`first(&n)` for a local does not.  One signature, decided where both the argument and the answer are in view.  A reference that
lasts as long as the program stands wherever a shorter-lived one is wanted, the other way round being refused (4203).  Which it
came to is written to the decision log (`lifetime`), since neither the signature nor the call says it.

**A variable at the top level holds a reference only where it says `static`** (4532), which is that rule asked at the other place
a value escapes to.  A reference inside something else -- a tuple, a product, a collection -- may not be answered with at all
(4531): `static` belongs to a reference and there may be several, and `from` speaks for the whole answer, so neither word has
anything to attach to.

Rust writes both lifetimes as named parameters, `fn first<'a>(v: &'a u8) -> &'a u8` and `&'static u8`, which says more -- several
lifetimes at once, and relations between them -- at the cost of a name to invent at each signature, softened by the rules that let
the common case go unwritten.  Here a function answers with at most one reference, so such a name would only ever have one thing to
point at, and pointing at the parameter directly says the same with nothing invented.  C++ has no rule at all and a dangling
reference is a program nobody notices is wrong; Go and Java move what escapes to the heap instead, which needs a collector.  Where
a reference does not leave the call none of this is written, which is most of what references are for:

```
fn main() → u6:
    let n: mut i8 = 1i8
    bump(&mut n)                 ※ the call says a place is handed over
    …

@[impure]
fn bump(at: &mut i8):
    at⌖ ← at⌖ + 1i8
```

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
when the statement is, so the same place may be lent again on the next line:

```
bump(&mut p)                   ※ lent for this statement
bump(&mut p)                   ※ and again, which is one at a time
```

That is the lexical rule, and it is Rust's before non-lexical lifetimes.  Rust now ends a borrow at its last use, which reads more
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

**Every dimension says how many, or none does** (4456).  Half of each would be a value whose parts depend on which half is which,
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
how many, and a list when it does not -- and **making a list is a change that outlives the call**, so a function that makes one
says `@[impure]`, the same as one that makes a collection.

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

There is no index yet, and the to-do list records it rather than this deciding what `l⟦i⟧` would mean where the list is empty.

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

**`⎕heap` is the arena the compiler provides**, which everything that allocates and says no other one comes out of.

**An arena is a bump pointer over a list of chunks.**  An allocation out of one is an addition and a comparison; where the current
chunk has no room, the arena asks the system for another and links it on.  Nothing is given back on its own, and a whole arena is
given back at once -- which is the whole of what makes an arena safe for storage with a known lifetime, and the whole of what makes
it wrong for a program that runs for a long time.  A second allocator will implement the same three operations, and the language
will name it the same way.

**An allocation that cannot be met stops the program.**  Answering with a result would put a `?` on every value a program builds
rather than computes, and there is nothing a program could usefully do at that point that the system will not do better by
refusing to start it.

Compare: Zig, where every allocator is a value and every allocation names one, which is where this arrangement comes from; Odin,
where the allocator is in an implicit `context` and a collection does not say which it uses; Rust, where a collection is
parameterised by its allocator in its type; C and Go, where there is one heap and nothing says so.  This sits with Zig and Rust:
a program should be able to read where a value lives off the line that makes it.

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
defined on and answers **exactly**: the integer types, `bool`, and enumerations (4429).

Floating point is left out on purpose, and the reason is worth stating: a not-a-number is equal to nothing, including itself, so a
key put in could never be found again; the two zeroes are equal and have different bits, so hashing them by their bits would put
one where the other is not; and two values arrived at by different routes rarely are one value, which is the thing the approximate
comparisons exist for and which a hash table cannot use.  A product, a sum and a result have no equality at all yet, so none of
them can be a key either.

The value type of a dictionary may be anything that is not `void` (4430) -- a dictionary whose keys stand for nothing is a set.

##### Reading and writing

`s⸨k⸩` on a set answers **whether it holds the key**, which is a `bool`.

`d⸨k⸩` on a dictionary answers with a **result**: `V?`, the value where there is one and the fact that there is none where there
is not.  So a key that is not there cannot be read past by accident, and `d⸨k⸩ ?? 0u8` says "or this instead" with nothing new to
learn -- it is Python's `d.get(k, 0)` written with the operator the language already has, and `d⸨k⸩?` hands the miss back to the
caller.  Python raises `KeyError`; this language has no exceptions and has a type that says the same thing in the signature.

`d⸨k⸩ ← v` puts a value in a dictionary under a key.  A set has nothing to assign to (4434): a key goes into one by joining it
with a set holding that key.

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

##### What one costs

The compiler decides this and the language says only what follows.  A value of a set or a dictionary type is where its table is
and nothing else, which is one word: how much it holds and how much room it has for more are in the table rather than beside it,
so that two names for one collection see one answer.  The table is elsewhere and is no part of the value, which is what lets a
collection be passed to a function and answered with like anything else.

A dictionary's value has to fit in a word (4445), which is the same restriction its key has for a duller reason: an entry is
words, and what goes in one has to fit in one.

Nothing takes a key out of a collection yet, so what is put in stays in.

Compare: Python, whose semantics these are and whose `{}` and `set()` this replaces with one pair of brackets and a rule about the
first entry; Go, whose maps are built in and which has no set; Rust, where both are library types and neither has syntax.  A
language emitted by a generator wants the shape written down rather than constructed by a call, which is why these have syntax
here.

**Nothing builds one yet.**  Everything above is written, typed and checked; a program that uses a collection is told the compiler
lacks the feature (9902).  What is missing is not the collection: it is a heap for the table to be in, which the compiler must
emit itself and whose shape is an open question, and a loop for a lookup to walk, which the language does not have.  Both are in
the to-do lists.

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

Compare: C, C++, Go, Rust, Zig and Odin all have `while` (Go spells it `for`, Rust also has `loop`), and all but C and C++ insist
the condition is a truth value.

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

### Names the compiler provides

A name beginning with `⎕` (U+2395 APL FUNCTIONAL SYMBOL QUAD) belongs to the compiler.  A program may read and assign the ones
that exist and may **not define one** (4219).  That is what lets the compiler add another later without taking a name away from a
program written before it existed -- the problem every language has that puts its own names in the same namespace as a program's.
APL marks its system names with the same glyph for the same reason, and `⎕CT` is the one this language's tolerance is modelled on.

There is one such name so far.

| Name | Type | Holds |
|---|---|---|
| `⎕tolerance` | `mut f64` | the tolerance the approximate comparisons measure against; 10⁻¹³ until a program sets it |

It is a variable and not a number built into the compiler because the right tolerance depends on how far the values being compared
have travelled, which is the program's business and not the language's.  A program that never names it carries nothing for it: it
is dropped along with everything else nothing reaches.

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
an address, as one at the top level is, what carries the qualifier is the pointer.  That is what will make `ptr<mut u8>` and
`ptr<u8>` different types once pointers can be written down, and what keeps the rule in one place rather than in two.

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
| writing memory it did not make -- an array it was handed, a set or a dictionary | 4479 |
| calling a function marked `@[impure]` | 4480 |

**Making a set or a dictionary is a change**, since it takes room out of an arena and the next call gets what this one left of
it.  So a function that builds one is `@[impure]`, whatever it does with it afterwards.

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

**A call that is not made is written to the decision log** (`drop-call`), naming the function and saying why.  A generator that
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

That is where a parameter's `mut` differs from a pointer's.  A `ptr<mut T>` says something about the *place* it names, which the
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
fn main() → u6:
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

##### Exit statuses the runtime reserves

**A program the runtime stops leaves through exit with a status, never through a signal.**  A signal is not a status: a shell
reports one as 128 plus the number, which collides with whatever a program might have chosen to exit with, and a caller has to
know to look for it.  A program that dies of a signal really did die of one, and that is worth being able to believe.

**64 through 127 are reserved** for stops the runtime reports.  That leaves the ranges either side to their owners:

| Range | Whose |
|---|---|
| 0–63 | the program's own, the startup function's result, which is a `u6` |
| 64–127 | the runtime's, for a stop it reports |
| 128–255 | a signal the program really died of, as the shell reports it |

**64 is the general one**: a stop the runtime has no more particular number for yet.  Everything a *fault* reports leaves through
it -- an answer that will not fit, a division by zero, an index outside its array, a shift too far, an allocation that failed --
because what went wrong is in the message, which names the operation, the function and the line, and a number could only say less.

**65 is the processor not being the one the program was built for**, which has a number of its own because it is the one stop that
happens before the program has run at all, and because what to do about it -- build for an older microarchitecture level, or find
a newer machine -- is a different thing to do.

**The startup function answers a `u6`**, which is that first range and nothing else.  The type is what says the rule rather than
a paragraph a reader has to have read: a program that tries to exit with 200 is refused where it writes it, and one that works
its status out arrives at a number that is already in range.  It also means the status has to be *worked out* in `u6` -- there is
no conversion between integer widths yet -- so a program that computes something wider says so and answers with something else.

**The reservation is what makes a status enough to say it with.**  Without it, a runtime stop and a program that chose to fail
would be the same number, which is the objection that used to argue for the signal; with it, a caller can tell the three cases
apart without knowing anything about the program.

A program is not stopped from returning a status in the reserved range: the startup function answers with a `u8` and every value
of one is a status.  What the reservation says is what a program that does so is giving up, which is the ability of its caller to
believe it.

Compare: `sysexits.h`, whose 64 through 78 are the convention this borrows its range and its starting number from -- and which is
advisory where this is the compiler's own, so the runtime can actually keep it; the shell's 128 plus the signal number, which is
the reason the top range is spoken for and not something this chose; Python, which exits 1 for an uncaught exception and so
cannot be told from a program that meant to; and Go, which exits 2 and panics through a signal-like path that prints a stack
trace.  What none of them has is a range reserved on both sides, which is what lets all three cases be told apart rather than two.

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

**The body is checked for each set of types.**  What may be done to a value of a type parameter is what may be done to the type
it turned out to be, and nothing before the call knows what that is -- so an operation the types do not admit is reported where
it is written, in the definition, with a note saying which call asked for those types:

```
error: ⌈ is defined on integers, not on ⸨u8⸩
    a ⌈ b
note: largest was compiled for ⸨u8⸩ because of this call
    let n: ⸨u8⸩ = largest(s, t)
```

That is C++'s bargain and not Rust's: there is no language for saying what a type parameter must support, so there is nothing to
check a body against until a call says what the types are.  What it costs is that a generic function nobody calls is never
checked at all, and that a mistake in one is found by whoever calls it.  What it buys is that nothing has to be said twice --
a generator emitting a function knows what it will call it with.

**One function is made per set of types**, not one per call: a second call saying what an earlier one said gets that same
function.  The two are told apart by their symbols on their own, a symbol being the signature written out.  Which sets of types a
generic function was compiled for is written to the decision log (`instantiate`), since the program said nothing about them.

**A generic function is not the runtime's entry point, a constructor or a test** (4558): each of those is one thing the program
has, and something written once per set of types is none of them.

Compare: **C++** templates, whose instantiation-time checking this is, and whose `template<typename T>` this leaves out -- the
parameters being named by being used, as C++20's abbreviated `void f(auto x)` does.  **Rust** and **Swift**, which check a
generic body once against bounds written on it, which is stronger and needs a language for the bounds.  **Go**, whose type
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
