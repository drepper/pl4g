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
nothing beyond the fact of it.  `TYPE?ERROR` is a result whose error is a value of its own; the syntax is part of the language and
nothing in the language makes such a value yet, so a program that writes it is told the compiler lacks the feature.

`÷` and `%` answer with one: `u8 ÷ u8` is a `u8?`, and so is `u8 % u8`.  That is the whole of what a division does about a divisor
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

**A value of the answer type, written where a result is wanted, is the successful result.**  That is how a function that answers
with a result says it succeeded -- `fn share(a: u8, b: u8) → u8?` ending in `(a ÷ b)? + 1u8` answers with the sum -- and there is
no other way to write one.  The reverse is not admitted: a result where a plain value is wanted is refused, since accepting it
would be dropping the error silently.

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

The six approximate comparisons have none for the first rule's sake as much as the second: there is no sequence of ASCII
characters that says "approximate" without being read as something else, and `~=` or `=~` would be a spelling to learn rather than
one to see.

`⟦` and `⟧` have none: `[[` and `]]` would each be two characters and so pass the first rule, and they fail the second -- an
array of arrays is written `a⟦i⟧⟦j⟧`, whose substituted form would end in four brackets that no reader could group by eye.

`…` has none, and the reason is the second rule rather than the first: `...` is three characters and so passes, but it is three
copies of the character a member access is written with, and telling `a...b` from `a . ..b` would be a question of how far the
lexer can look ahead rather than of what the characters are.  One character is one token, which is what a range is.

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
fields or through a chain of other definitions: a value of such a type would have to hold a value of itself, and the indirection
that makes that finite elsewhere -- a pointer, a reference, a box -- is not something this language can yet write.

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

**A loop is a statement and not an expression**, which is where it differs from `if` and `match`.  Those produce a value because
every way through them produces one; a loop has a way through that runs the body no times at all, and there is nothing for that
way to produce.

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
the condition is a truth value.  Rust's `loop` is an expression, producing what a `break` hands it; there is no `break` here yet,
and until there is, a loop has nothing to produce.  Python's `while` has an `else`, which runs when the loop ended by its
condition rather than by a `break`; with no `break` the two cannot differ, so there is nothing for one to mean.

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


#### foreach

`foreach` runs its body once for each value something gives out.

```
let total: mut u8 = 0u8
foreach i = 0u8…5u8:
    total ← total + i
```

**It shares `let`'s shape**: one or more names, an optional type, an equal sign, and what the loop takes its values from.  The
colon before the type may be left out along with the type, where the values say what they are.

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
foreach x = a:           ※ every element of a vector
foreach row = m:         ※ every row of a table
foreach k = s:           ※ every key of a set
foreach k, v = d:        ※ every key of a dictionary, and what it stands for
```

**Iterating an array is over its outermost dimension**, so a turn of a `T⟦2,3⟧` gives a `T⟦3⟧`.  Row-major is what makes that
cheap: a row is a run of elements, so naming one is arithmetic on the place and no copy at all.  An array whose type does not say
its shape is walked the same way, which is what lets a function take one of any length and still say what to do with each element.

**A table is not walked in the order its keys were put in it**, and it has no such order: where a key lands is where its hash puts
it, and growing the table moves everything.  Python promises the order keys were added in and pays for it with a second array; Go
deliberately randomises its walk so that no program can come to depend on an order it never promised.  This promises nothing.

**Several names take each value apart**, the way several names take a tuple apart in a definition.  That is the whole of what
`foreach k, v = d:` is -- a dictionary gives a key and a value together as a tuple, and two names take a tuple apart everywhere
else too, so the form needs nothing of its own.  Over something that gives one value, several names are refused.

**`_` is the name that is not a name**, as it is in a `match` arm: the loop runs a turn for each value there is and the value
itself is not wanted.  Nothing is bound, so nothing is reported as a value nothing reads.

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

The bare name is used in one case: a function marked `@[cdecl]` keeps it.  The point of asking for the system's convention is to
be reachable from a world that has never heard of this language, and that world knows the function by the name it was given.

Because the result type is part of the name, two functions that differ only in what they return are different names.  Nothing
depends on that yet; it is what would let a result take part in choosing between functions of one name, which is not specified.


Runtime
-------

The I/O functionality is asynchronous by default.  All I/O operations, as well as some others,
are handled through the `io_uring` system call.  I/O is represented through objects which have
a representation in the language itself.
