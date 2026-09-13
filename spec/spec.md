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

#### Modules

A file brings another in with a definition:

```
let limits := import("lib/limits")
```

The name on the left is what the module is called here; the string is the name of the module.  What the module exports is named
through it: `limits.ceiling`.  Nothing else of it can be named -- what a module does not export is its own, and two modules may
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

**Arguments are positional**: what an argument is for is decided by where it stands.  A call hands over exactly the arguments
the function takes -- nothing is variadic, nothing has a default -- and each has the type of the parameter it is handed to, with
nothing widened to make two types meet.  A literal with no suffix takes the parameter's type, which is what lets a call be written
with plain numbers.  Whether arguments may also be *named*, as an attribute's are, is not decided; nothing here forecloses it.

A call binds tighter than every operator and to whatever stands immediately before it, so `a.b(c)` calls `a.b` and `f(x) + 1` adds
to what the call answered with.  The parentheses are what say a call is being made, not what carry the arguments, so a call with
none is written with them all the same.

**A call may stand as a statement of its own.**  It is the first expression in the language that does something besides produce a
value, which is why the rule that a statement's value must be used does not apply to it.

Compare C, C++, Java and Go, where a call is likewise the exception to the discarded-value rule; and Rust, where a call producing
a value that is discarded is a warning unless the type says otherwise.  A rule of that kind wants a way for a function to say its
answer must not be dropped, which this language will want too and does not have yet.

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

**Two divisions have no answer, and both stop the program.**  Dividing by zero is the obvious one -- and the three architectures
do three different things about it, one raising a fault of its own, one answering with all ones and one with zero, so it is asked
about first and the program stops the same way everywhere.  The other is the most negative number divided by `⁻1`, whose quotient is
one past the largest the type can hold; it is the only pair that overflows, and an unsigned type never meets it.

Compare C, where signed overflow is undefined and unsigned overflow wraps, and where `-ftrapv` and the sanitizers exist because
neither answer is what anyone wanted; Rust, which panics in a debug build and wraps in a release one, so that a program means two
different things depending on how it was built; Zig, which faults in both and has `+%` for wrapping; and Swift, which faults and
has `&+`.  Zig's and Swift's position is the one taken here, with `⊞` and its relatives in place of `+%` -- and with no wrapping
operator at all, wrapping being a thing to ask for by writing the wrap rather than by writing an operator that hides it.

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

**Equality is defined on numbers and on truth values**; ordering is defined on numbers alone.  Two truth values can be the same or
different, but neither comes before the other, so `ready < seen` is refused rather than given an answer by way of the
representation.  Strings will be added to both when there are strings.

**The comparisons do not chain.**  `a < b < c` is refused, because a comparison answers with a truth value and a second one beside
it would be comparing that answer with a number.  Where that is what was meant, parentheses say so: `(a < b) = ready` compares two
truth values and is well formed.  Where the intent was that a value lies between two others, that is two comparisons joined with
`∧`.  Rust refuses the same writing for the same reason.  Python gives it a third meaning by chaining, which reads
well and is the only language that does it; C and Go give it the meaning above and leave it to be a mistake.

Splitting equality from ordering, as C does, would decide only what `a < b = c` means, and that expression has no meaning here.
One level for all six is what Go and Rust do.

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

#### Statements that are expressions

A statement may be an expression on its own.  **Its value must be used**, and there is exactly one place where it is: the last
statement of a body is the body's result.  Anywhere else the value goes nowhere, and a statement that is nothing but an expression
does nothing at all, so it is refused.

```
@[startup]
fn main() → u8:
    1u8                    ※ refused: this value is not used
    count = 1u8            ※ refused, and the same mistake C makes easy
    count & mask           ※ refused
    0u8                    ※ the result of the function
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
@[abi("sysv64", variadic=false)]
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
fn main() → u8:
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

**An assignment stands for the variable it changed.**  It refers to the variable, not to the value that was written, so reading it
gives what the variable now holds.  As the last statement of a function it is therefore the function's result, the way any other
last statement is:

```
let counter: mut u8 = 1u8

@[startup]
fn main() → u8:
    counter ← 42u8     ※ the program exits with status 42
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
fn NAME(ARG1: TYPE1, [ARGN: TYPEN]) → RETVALTYPE BLOCK
fn NAME(ARG1: TYPE1, [ARGN: TYPEN]) BLOCK
```

where `NAME` is a valid identifier naming the function, `ARG?` are parameter names, `TYPE?` are type descriptions for the parameter,
`RETVALTYPE` is the type of the return value.  `BLOCK` is the code of the function, in one of the two notations.  In layout format,
the function header is followed by a colon, a newline, and then the properly indented code.  When the function header is followed by
a `{` it uses the explicit syntax and continues until the respective closing `}`.

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
