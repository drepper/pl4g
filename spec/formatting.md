Turning Values into Text
========================

**A proposal.  Nothing here is decided.**  The question is what this language should
have where C++ has `std::format` and Python has f-strings, and the answer comes out in
three layers that can be decided separately -- which is the useful part, because the
first of them needs no change to the compiler at all.

Everything said below about what the language does today was compiled and run while
this was written, and the programs are quoted as they were.


What the language already has
-----------------------------

**Text, and a way to join it.**  `str` is bytes that are always well-formed UTF-8, two
words wide, owning nothing.  `⧺` joins two, taking room from the arena, which is why a
function that joins says `@[impure]`.

**An operator is a free function whose name is the glyph, and it dispatches on the
operand's type.**  That is the one mechanism in this language that picks an
implementation by type, and it landed with the operators decision.

**Default parameter values and named arguments.**  A parameter may have a default and
every parameter after one with a default has one too (4526); the reason given in the
specification is that a generator emitting a call should not have to emit the arguments
nobody varies.

**Requirements on a generic type**, so one definition can cover every integer width.

**And no overloading**, which the bundles decision states outright.  Two functions of
one name are a duplicate definition.

**What is missing is number-to-text, entirely.**  Nothing in the language, the module
or the runtime turns a number into its digits.  The fault path deliberately formats
nothing -- `spec/details.md` says so and gives the reason -- so there is not even a
private one to lift.  Output today is bytes:

```
⎕drop(std.write(&mut init.io.output, ⎕bytes("hello\n")) ?? 0)
```


The first surprise: layers 1 and 2 need no compiler change
----------------------------------------------------------

**A program can define APL's format glyph today.**  `⍕` (U+2355) is `So`, the language
gives it no meaning, and an operator's name may be any glyph the language did not take.
This compiles and runs as it stands:

```
type Point = x: u8 ; y: u8

@[impure]
fn `⍕`(p: Point) → str:
    "a point"

@[startup, impure]
fn main() → u6:
    let p: Point = Point(.x ← 1u8, .y ← 2u8)
    let s: str = "it is " ⧺ ⍕p
    ⎕narrow(⎕drop(#s), ⌜u6⌝) ?? 1u6          ※ 13
```

**It may be defined for a built-in type too**, the language having given the glyph no
meaning for one either, **and as an infix at the same time**, arity being what tells a
prefix operator from an infix one:

```
@[impure]
fn `⍕`(n: u8) → str: …                       ※ ⍕7u8
@[impure]
fn `⍕`(width: u64, n: u8) → str: …           ※ 4u64 ⍕ 7u8
```

**And one definition covers every width**, by a requirement:

```
bundle number(T'):
    T' + T' → T'
    T' ÷ T'

@[impure]
fn `⍕`(n: T') → str pre(number(T')):
    …
```

All three of those were compiled and run.  So the whole of "a value's text" and "a
value's text to a width" is writable as a module today, and what remains for the
compiler is the template notation and one optimization.

`⍕` is APL's own primitive for this: monadic `⍕` formats a value, dyadic `⍕` formats it
to a specification.  Borrowing the glyph borrows the meaning, which is what `⍴` and
`⌈`/`⌊` already did here.


Layer 1 -- what a value's text is
---------------------------------

### The mechanism is forced

With no overloading, a *named* function cannot be written once per type.  The operator
is the only thing in the language that dispatches on a type, so a type says its text by
defining an operator, and nothing else is available without a new language feature.

That is not a consolation prize.  It is the answer the attaching-code document reached
for `⇧` and `⇩`, and this is the same question asked about a different operation: a
program's own type joins in by writing the operator, and the compiler does not have to
know the type exists.

### What the compiler has to provide

`⍕` for the types a program cannot define an operator for without the language saying
it may: the integers of every width, `f32`/`f64`, `bool`, `char`, `str` itself, an
enumeration's value, and a value carrying a unit.  Each is a decision of its own:

| Type | What its text is | The hard part |
|---|---|---|
| `uN`, `iN` | digits, base ten, `⁻` for a negative | none; a loop and a remainder |
| `f32`, `f64` | the shortest text that reads back as the same number | Ryū or Grisu; this is the real work |
| `bool` | `true` / `false` | nothing, and it is a decision that they are words |
| `char` | the character itself | one code point encoded, which `⎕bytes` already does |
| `str` | itself | free |
| an enumeration | the name, not the number | the names have to reach run time, which today they do not |
| a unit | the number and the unit's name | the same question again |

Two of those rows are the ones worth arguing about.  **A float's shortest round-trip
text is a real algorithm**, several hundred lines and a table, and every language that
got it wrong first had to do it twice.  **An enumeration's names do not exist at run
time** in this compiler: a value is its number, and printing the name means emitting a
table of names and a way to reach it, which is a decision about what a program pays for
something it may not use.

### Comparisons

| Language | How a type says its text | Dispatch |
|---|---|---|
| **C** | it does not; `printf` is told by the format string | none; a mismatch is undefined behaviour |
| **C++** | a `std::formatter` specialization | template specialization, checked at compile time |
| **Rust** | `impl Display` / `impl Debug` | a trait, coherent and checked |
| **Go** | a `String() string` method | an interface, satisfied structurally |
| **Zig** | a `format` method the printer looks for by name | duck typing at `comptime` |
| **Odin** | a procedure in a table, or reflection over the type | runtime type information |
| **D** | `toString` | compile-time introspection |
| **Python** | `__str__` / `__repr__` / `__format__` | a method, found at run time |
| **Haskell** | a `Show` instance | a class, resolved statically |
| **Lisp** | `print-object` | a generic function, dispatched at run time |
| **APL** | `⍕`, which is total: every array has a text | a primitive, no extension point |
| **pl4g** | an operator whose name is `⍕` | by the operand's type, at compile time |

The row that is nearest is **Zig**'s -- a thing looked for by name on the type, at
compile time, with no interface to declare -- and the difference is that here it is
looked for the way every other operator is, so a reader who knows how `+` is defined
for a type knows how its text is.


Layer 2 -- a width, a base, a precision
---------------------------------------

Three possibilities, and only the third needs anything new.

### W1.  Dyadic `⍕`, which is APL's answer

`8u64 ⍕ x` is `x` in a field eight wide.  APL's dyadic format takes two numbers -- a
width and a number of decimals -- and this language could take a tuple for the pair.

For: it is the glyph's own meaning, it costs nothing, and arity already tells the two
apart.  Against: `〈8u64, 2u8〉 ⍕ x` is a specification encoded as numbers, which is
the thing about `printf` that this language would otherwise be avoiding, and a reader
cannot tell which number is which.

### W2.  Ordinary functions with default arguments

```
std.text(x)
std.text(x, .base ← 16u8)
std.text(x, .width ← 8u64, .fill ← '0')
std.fixed(x, .digits ← 2u8)
```

For: it needs *nothing* -- the language has defaults and named arguments and says in so
many words that they are there for a generator that should not have to emit what nobody
varies.  Every option is a value of a type, so `.base ← 16u8` is checked and
`.base ← "hex"` is not a program.  Against: no overloading means one name cannot serve
`u8` and `f64` unless it is generic, and the requirements that would say "a number" are
layer-2 generics, which are in.

### W3.  A mini-language inside the string, which is what C++ and Rust and Python have

`{:>8.2}`.  For: it is what everyone knows.  Against, and this is the argument that
matters here: **it is a second language written inside a string literal, where this
project's grammar cannot see it.**  The tree-sitter grammar and the test that holds it
to the compiler are the reason a syntax cannot drift in this project, and a syntax
inside a string escapes both.  It is also the one part of `std::format` that a
generator gains nothing from: a generator has the width as a number and would have to
print it into a string for the formatter to parse back out.

**W2 is the recommendation**, and W1 as well if the glyph's own meaning is wanted --
they do not conflict.


Layer 3 -- the template
-----------------------

### T1.  No template at all: a join

```
let line: str = "x = " ⧺ ⍕x ⧺ ", y = " ⧺ ⍕y
```

Needs nothing but layer 1.  It is what Go asks for with `+` and `strconv`, and it is
what a generator would emit anyway: a generator has the pieces as a list and joining
them is one loop, where writing a format string means printing a template and then
having it parsed back.

**What it costs today is real, and measured.**  `"a" ⧺ "b" ⧺ "c" ⧺ "d"` emits three
calls to the allocator at `-O1`, one per join, each copying everything to its left:
n−1 allocations and O(n²) bytes copied for n pieces.  Nothing folds a chain and nothing
folds a join of two literals.

**So T1 wants an optimization, and it is worth having on its own.**  A chain of joins
is one expression the compiler can see whole: add the lengths, allocate once, copy each
piece in.  Every join in the language gets faster, not only the formatted ones, and a
join of literals folds into a literal in the image.  That is the piece of work this
proposal would put first whichever template is chosen, because every template lowers to
a chain.

### T2.  An interpolating literal, with the holes the macros already have

```
let line: str = ⁋"x = $x, y = $(y + 1)"
```

`$name` and `$(EXPR)` are **exactly** the notation the macros landed with, and they mean
here what they mean there: something is put in at this point.  A hole is an ordinary
expression, checked as one, with no mini-language anywhere -- `$(⍕(x, .base ← 16u8))`
is how a width is asked for, which is a call and not a spelling inside a string.

It lowers to T1, so it is sugar, and it is the sugar a human reader gets the most from.

What has to be decided is **what marks such a literal**, because `$` cannot start
meaning something inside every string that already exists.  A mark is needed and the
language has no letter prefixes, so it is a glyph before the quotation mark (`⁋` above
stands in for whichever) or a bracket pair of its own.  This is the weakest part of the
proposal and the place to argue.

### T3.  `std.format("x = {}, y = {}", x, y)`, which is C++ and Rust

For: it is what the question asked about, and the template reads as one piece of text.

Against, and each of these is concrete here:

- **It has no argument list to put the values in.**  This language has no variadic
  functions; a call's arguments are counted and `⁂` spreads a tuple whose length is in
  its type.  So it is `std.format("…", 〈x, y〉)`, a tuple written out.
- **Checking the template against the arguments is a macro**, and a macro cannot do it.
  The macro that just landed is handed *pieces of the program*; it can be handed the
  string literal, and there is no way to see the literal's text and no way to build a
  piece from a number.  Those are `⎕name` and `⎕apply` -- which are precisely the three
  things the function form left over.  So T3's cost is "finish the macro system, then
  write a parser for the mini-language as a `comptime fn`".
- **And the mini-language is W3**, with the objection W3 already has.

T3 is the most expensive of the three and the one that fits this language least.  It is
not absurd -- once `⎕name` exists it is a weekend -- but it buys a notation whose whole
value is familiarity.


Where the bytes go
------------------

A question the layers do not settle, and it cuts across all of them.

**Into the arena**, answering a `str`.  What `⧺` does.  Every formatting function is
then `@[impure]`, so **nothing pure can format**, and a program with no heap cannot
either.

**Into a buffer the caller gives**, answering how many bytes went.  Pure, allocates
nothing, and what the fault path would need if it ever wanted to say a number -- which
it deliberately does not today.  It is `snprintf`, `std::format_to`, Rust's `write!`,
Zig's `bufPrint`.

**Straight to a `Writer`**, answering what `write` answers.  No intermediate at all,
which is what printing a line actually wants.

These are not exclusive and the layering is obvious: **the buffer one is the primitive**
and the other two are written on it -- the arena one takes a buffer of the length it
worked out, and the device one takes a buffer and writes it.  That is the order C++
arrived at after the fact (`format_to` underneath `format`) and the order Zig started
from.  Deciding it this way costs nothing and means the pure, no-heap form exists rather
than being retrofitted.


The whole comparison
--------------------

| Language | Notation | Template checked | Extension point | Allocates |
|---|---|---|---|---|
| **C** | `printf("%d", x)` | no, and `-Wformat` is a lint | none | no |
| **C++20** | `std::format("{}", x)` | yes, `consteval` | `std::formatter` | `format` yes, `format_to` no |
| **Rust** | `format!("{x}")` | yes, a macro | `Display` | `format!` yes, `write!` no |
| **Go** | `fmt.Sprintf("%d", x)` | no, `go vet` is a lint | `String()` | yes |
| **Zig** | `std.fmt.allocPrint` | yes, at `comptime` | a `format` method | `allocPrint` yes, `bufPrint` no |
| **Odin** | `fmt.tprintf` | no | runtime type information | yes |
| **Python** | `f"{x}"` | the grammar splits it | `__format__` | yes |
| **C#**, **Swift**, **Kotlin**, **JS**, **Ruby** | interpolation in the grammar | the holes are expressions | a method | yes |
| **Haskell** | `printf`, or `show` and `++` | `printf` is type-class trickery | `Show` | yes |
| **Lisp** | `(format nil "~d" x)` | no | `print-object` | yes |
| **APL** | `⍕x`, `spec ⍕ x` | the primitive is total | none | n/a |
| **BQN** | `•Fmt` | total | none | n/a |
| **Wolfram** | `ToString`, `StringTemplate` | no | `Format` | n/a |

**What the table says** is that the two things the question asked about are not one
feature.  Python's f-string is a *grammar* decision -- where the template is taken
apart -- and every language that copied it copied the grammar.  `std::format` is a
*library* decision resting on compile-time evaluation, and what it buys over
interpolation is a template that can be a variable, which almost nothing wants and
which is where every format-string vulnerability comes from.

This language is unusual in the table in three ways, and each points the same direction:
it has no overloading, so the extension point is an operator; it has no variadics, so a
library function taking a template takes a tuple as well; and it is meant to be written
by a generator, which would rather emit a join than a template.


What this proposal recommends
-----------------------------

1. **`⍕` as the text of a value**, dispatched by the operand's type like every other
   operator, and the compiler providing it for the built-in types.  Needs no language
   change: measured.
2. **The buffer-filling form as the primitive**, with the arena form and the
   write-to-a-device form on top of it.  Pure, no-heap formatting then exists rather
   than being added later.
3. **`std.text` and its siblings with default arguments** for width, base and
   precision, and no mini-language anywhere.
4. **Folding a chain of joins into one allocation**, which every join in the language
   wants and which every template lowers to.
5. **An interpolating literal reusing `$name` and `$(EXPR)`**, decided last and
   separately, because it is sugar over 4 and because what marks it is the one thing
   here with no obvious answer.

And **not** `std.format` with a template parsed at compile time, unless the familiarity
is what is wanted for its own sake: it needs the three leftovers of the macro system, a
tuple where C++ has a pack, and a mini-language in a place this project's grammar cannot
check.


What this does not decide
-------------------------

- **What marks an interpolating literal**, if there is one.
- **Whether an enumeration's names reach run time**, which is what printing a name
  needs and which is a cost every program would carry for something most do not use.
  The alternative is that `⍕` of an enumeration is its number, and a program that wants
  the name writes the operator itself over a `match`.
- **Float text**, which is the one genuinely large piece of work here, and whether the
  first version is shortest-round-trip or something honest and worse.
- **Whether `⍕` is the glyph.**  It is APL's and it is free, which is the whole
  argument for it; a reader who does not know APL sees a mark with no mnemonic.
- **Padding a value whose text is not ASCII**, where a width in characters and a width
  in columns are different questions and the second one is not answerable without tables.
- **Whether a template may be a value**, which is what `std::format` allows and what
  interpolation does not, and which is the difference the comparison table ends on.
