Turning Values into Text
========================

**Decided**, on 2026-10-01; see [decisions.md](decisions.md) for the entry.  `⍕` is a
value's text, `std.format` is a macro invoked with the marks, and the template holds
`{}` and `{name}`.  What follows is the reasoning as it was written, with the section
on the call marking which of its four ways was taken and why -- and with the two
prerequisites that trying it turned up.

**A proposal.  The shape is decided and the pieces are not.**  The question is what
this language should have where C++ has `std::format` and `std::print` and Python has
f-strings, and the answer comes out in three layers that can be decided separately --
which is the useful part, because the first two need no change to the compiler at all.

**What is settled**: a `std.format`-like call, whose template is a literal taken apart
while compiling by a compile-time function; it counts the holes against the argument
list, or reads a name out of the template as an f-string does; and what it answers is
the conversions and the constant pieces already put together -- a `str` for
`std.format`, and the writes themselves for `std.print`.  It should look like an
ordinary call and not like a macro invocation.  Concatenation alone is not enough,
because the check is the point and because the same thing has to reach `std.print`.

**What is open** is listed at the end, and the biggest of them is which of two ways an
ordinary-looking call gets its arguments unevaluated.

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

Three possibilities.  The first two need nothing at all and the third is the one the
template would otherwise grow into.

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

`{:>8.2}`.  For: it is what everyone knows.  Against: **it is a second language written
inside a string literal, where this project's grammar cannot see it** -- the tree-sitter
grammar and the test that holds it to the compiler are the reason a syntax cannot drift
here, and a syntax inside a string escapes both.  And it is the one part of
`std::format` a generator gains nothing from: a generator has the width as a number and
would have to print it into the template for the formatter to parse back out.

Turned down; the section on alternatives says the rest of it.

**W2 is the recommendation**, and W1 as well if the glyph's own meaning is wanted --
they do not conflict.  What stays in the template is then `{}` and `{name}`, and nothing
that needs parsing beyond finding them.


Layer 3 -- the call
-------------------

**This is the layer the question was really about, and its direction is settled**: a
`std.format`-like call whose template is checked while compiling.  A join is not
enough, because the same thing has to extend to `std.print`, and because what a
template buys is the check -- that the holes and the arguments agree -- which a join has
no way to get wrong and no way to state.

What happens at compile time is the whole of it: a compile-time function takes the
template apart, either counting its holes against the argument list or reading a name
out of it, and answers **the conversions and the constant pieces already put together**.
`std.format` answers that as a `str`; `std.print` writes it.

### It should look like an ordinary call, and here is how it can

The macros landed with `f⌜…⌝`, which marks the arguments as handed over unevaluated.
That is right for a rewrite rule and wrong here: `std.format("x = {}", x)` is a call in
every language that has it, and a notation of its own would be a second thing to learn
for a function whose arguments *are* evaluated -- just inside the expansion rather than
before it.

Four ways to get an ordinary-looking call expanded while compiling:

**M1.  A parameter of type `syntax` means the argument is handed over as written.**
The callee's signature decides, and nothing at the call is marked.

```
macro format(template: syntax, ⁂args: syntax) → str:
    …
std.format("x = {}", x)
```

**Turned down.**  It is a third way to invoke a macro, and a macro is invoked with the
marks; what they say -- handed over as written -- is exactly what is true of a template.
The reasoning as it was written follows.

The argument for it was that **the rule falls out of what
`syntax` already means** rather than being added.  A function with a `syntax` parameter
already cannot be an ordinary function: nothing at run time may hold a piece of the
program (7022), so such a function is already the macros' alone and already cannot be
called in the ordinary way.  Saying that its arguments arrive as written is the only
reading left.  It is Zig's `comptime` parameter exactly -- a parameter whose argument is
compile-time-known, at a call that looks like any other.

What it costs is what the macros decision spent a paragraph avoiding: a reader at the
call cannot see that the arguments are not evaluated first.  For this use the difference
is invisible -- the arguments *are* evaluated, in the order written, in the expansion --
and the trade is worth making for a call everyone already knows how to read.

**M2.  `macro` invoked with parentheses.**  The same thing said by the keyword instead
of by the parameter type.  It needs the namespace question reopened: a name may be both
a macro and a function today, precisely because the two invocations cannot be confused,
and this would make them confusable.

**M3.  A compiler-known call**, `std.format` special-cased in the checker the way
`⎕narrow` is.  Turned down in favour of the macro, and it is the one to come back to if
the prerequisites below prove worse than they look.  **Much cheaper** -- the compiler has the literal's text in hand and
nothing in the macro system has to change at all -- and it is the fallback if the
estimate below comes out badly.  What it costs is that the format language lives in the
compiler rather than in a module: `std.print` needs the compiler changed, a program
cannot write its own `format`-like function, and a second implementation of the language
has to reproduce the mini-language rather than reading it out of `std`.

**M4.  Keep `⌜⌝`**: `std.format⌜"x = {}", x⌝`.  **Taken.**  Cheapest of all and it is
what exists, and the objection -- that it does not look like a call -- is answered by
what the marks say about what is between them, which for a template is the truth.

### Variadics, which the language does not have and does not need here

A call's arguments are counted and there is no variadic parameter list.  But a macro is
handed *pieces*, and how many pieces a piece holds is `⎕parts` -- a compile-time
question.  So **a trailing `syntax` parameter that collects the remaining arguments as
one piece** gives a variadic call without the language gaining variadic functions:

```
macro format(template: syntax, ⁂args: syntax) → str
```

`⁂` is the glyph for "several things stand where one is written", which is what this is,
and it is being read here in the direction opposite to the one it already has.  That
symmetry is either the argument for the glyph or the argument against it.

The alternative is `std.format("x = {}", 〈x, y〉)` with the tuple written out, which
needs nothing new and which a generator would not mind at all -- and which a person
writing a print statement would mind every time.

### What the macro system has to gain, measured

Four things, and the interpreter's share of it is smaller than expected.  Both of these
were read out of the compiler while this was written:

- **`__pl4g_str_join` is ordinary IR in the module** -- an allocation and two copy loops
  -- so the macro machine can already run a join.  The one callee with no body is
  `__pl4g_alloc`, which is per-target assembly, so the interpreter needs exactly **one
  native hook**: a bump over the `bytearray` it already has.
- **`foreach` over a string is inlined IR** too, continuation-byte test and all, and so
  is a string comparison.  So walking a template needs no new opcode.

What is actually missing:

1. **Globals in the interpreter.**  A string literal is a global holding its bytes, and
   the machine has no `GlobalVar` at all -- it would have to materialize a global's
   initial bytes into its memory and answer an address as an offset.  This is what makes
   a literal readable, and it is the larger half of the work.
2. **`__pl4g_alloc` as a native bump**, per the above.
3. **A question that answers what a piece is written as**, for a string literal: its
   text, as a `str`.  This is `⎕name` from the design, which the function form left over,
   asked of a literal rather than of a name.
4. **`$(EXPR)` where the expression answers a `str`**, making a string literal piece.
   The rule already there is that `$(…)` puts "a piece of the program as itself, and a
   number as what a program would have written to mean it"; text is the same sentence
   with one more type in it.

And one thing that is missing and is not about strings at all:

5. **A way for a macro to refuse with its own message.**  A template with three holes and
   two arguments has to say that, and today a macro that will not do its job can only
   stop, which is reported as "running 'format' stopped" (7023) -- a message about the
   compiler where the program is what is wrong.  Nothing else in this proposal is worth
   having without it: the check is the whole reason the template is not a join.

### `std.print`, and why it comes out better here than in C++

`std::print` formats into a buffer and writes the buffer.  Here the pieces are known
while compiling and the device takes bytes, so the expansion can be **a write per
piece** and there need be no buffer and no allocation at all:

```
std.print(&mut io.output, "x = {}, y = {}\n", x, y)
```

becomes the writes for `"x = "`, for `⍕x`, for `", y = "`, for `⍕y` and for `"\n"` --
and the constant ones are literals already in the image.  The I/O design makes that the
good shape rather than a clever one: `write` submits to the ring and does not wait, so
several are in flight at once and the drain before the program ends collects them.  A
formatted line costs one submission per piece and no heap, which is something
`std::print` cannot do because its formatter has nowhere to put the pieces.

That also means `std.print` is not "`std.format` then write": it is the same macro
answering a different thing, and it is the form that works in a program with no arena.

### Which template style

The question left two open, and they are not exclusive:

- **`{}` positional**, counted against the argument list.  The check is "as many holes as
  arguments", and this is C++, Rust and Python's older form.
- **`{name}` naming something in scope**, which is Python's f-string, C#'s `$"{x}"` and
  Rust's `format!("{x}")`.  It needs one more thing than the list above: a way to make a
  *name* piece out of text, which `⎕name` in the other direction does not give.

**And `{name}` is deliberately unhygienic**, which is the one thing to decide with open
eyes.  Every other name a macro writes resolves where the macro was written -- that is
the decided rule and the reason a macro means one thing everywhere.  A name read out of
the caller's template must resolve at the *caller*, or the feature does nothing.  Python
has the same hole and it is why an f-string cannot be passed around as a template.  So
it is an exception, it has to be written down as one, and the honest way to spell it is a
question of its own -- a macro asking for "the name the caller would have meant by this
text" rather than the general power to conjure a name.


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
feature, and that this proposal takes one of them and borrows from the other.  Python's f-string is a *grammar* decision -- where the template is taken
apart -- and every language that copied it copied the grammar.  `std::format` is a
*library* decision resting on compile-time evaluation, and what it buys over
interpolation is a template that can be a variable, which almost nothing wants and
which is where every format-string vulnerability comes from.

This language is unusual in the table in three ways: it has no overloading, so the
extension point is an operator; it has no variadics, so a template's arguments arrive as
one piece and are counted while compiling; and its writes go to a ring and do not wait,
so a formatted line needs no buffer.

The row the recommendation lands nearest is **Rust**'s: a template that must be a
literal, checked by something that runs while compiling, with `{}` and `{name}` both, and
a form that writes rather than allocates.  The difference is that Rust's is a macro
invoked as one and this is a call.


Alternatives considered, and what became of them
------------------------------------------------

**A join and nothing else** -- `"x = " ⧺ ⍕x ⧺ ", y = " ⧺ ⍕y`.  Not turned down so much as
demoted: it is what the recommendation *lowers to*, and it stays the thing a generator
emits directly.  What it cannot do is check anything, and it does not reach `std.print`
without a buffer.

**An interpolating string literal** -- a marked literal whose holes are the `$name` and
`$(EXPR)` the macros landed with.  This is what `{name}` inside the template does
instead, with the template staying an ordinary string the compiler happens to read.  The
literal form is better notation and it is a grammar change, a new mark to choose, and a
second way to say the same thing; if `{name}` lands it is not worth having as well.

**A format mini-language** -- `{:>8.2}`.  Turned down for the reason the section on
options gives: it is a second language inside a string literal, where this project's
grammar test cannot see it, and a generator holding a width as a number would have to
print it for the formatter to parse back out.  Named arguments with defaults say the same
thing where both a reader and a checker can see it.


What this proposal recommends
-----------------------------

In the order that has each piece useful before the next needs it:

1. **`⍕` as the text of a value**, dispatched by the operand's type like every other
   operator, and the compiler providing it for the built-in types.  Needs no language
   change: measured.  Nothing else here works without it, since it is what the
   expansion calls for every hole.
2. **The buffer-filling form as the primitive**, with the `str` form and the
   write-to-a-device form on top.  Pure, no-heap formatting then exists rather than
   being retrofitted, and `std.print` is the device form.
3. **`std.text` and its siblings with default arguments** for a width, a base and a
   precision, so that a hole asking for one is `$(std.text(x, .base ← 16u8))` -- a call,
   checked, and not a spelling inside a string.  What stays in the template is `{}` and
   nothing else, which is the part of a format mini-language that carries its weight.
4. **A macro's own refusal**, without which the check the template exists for cannot be
   reported as a fault in the program.
5. **Globals and an allocation hook in the macro interpreter**, which is what lets a
   macro read a literal and build a string; plus `⎕name` of a literal and `$(str)`.
6. **`std.format` and `std.print` as macros with `syntax` parameters**, invoked as
   ordinary calls, with a trailing parameter collecting the rest of the arguments.
7. **Folding a chain of joins into one allocation**, which `std.format` wants and which
   every join in the language wants anyway -- measured: a chain of four pieces allocates
   three times today and copies everything to the left of each join.
8. **`{name}` naming something in scope**, last and separately, because it is the one
   part that has to break a rule the language decided on purpose.

**M3 -- `std.format` known to the compiler rather than written in `std`** -- is the
fallback for 5 and 6 together, and it is much cheaper.  It is worth keeping in view: if
globals in the interpreter turn out to be more than they look, the notation and the check
can be had without them, at the price of the format language living in the compiler.


What trying it turned up
------------------------

Two things that have to land first, neither of them about formatting.  Both were found
by writing the program and compiling it.

**An exported operator is not in force where the module is imported.**

```
※ m.pl4g
@[export, impure]
fn `⍕`(n: u64) → str: …

※ use.pl4g
let m := ⎕import("m")
… ⍕1234u64          ※ error: nothing says what '⍕' means for one operand (4923)
```

The operator table is built from the file's own definitions.  `⍕` for the built-in
types belongs in `std`, so this has to change, and the rule has to be that **an exported
operator is in force wherever its module is imported** -- there being no name to qualify
an operator by.  Two modules claiming one glyph for one type is then a conflict to
report, which is the price and is the same price Rust and Haskell pay for global
instances.

**A macro cannot be exported, and trying crashes the compiler.**

```
※ m.pl4g
@[export]
macro twice:
    ⌜$x⌝ → ⌜$x + $x⌝

※ use.pl4g
… m.twice⌜3u8⌝
```

reports `expected ')' to close the group` at the marks, and then
`internal compiler error: unknown kind of top-level definition` (9901) from the module
loader.  So two pieces: a macro invoked through a module's name has to parse, and the
loader has to know what a macro is.  The macros decision left "whether a macro may be
exported" open and `std.format` closes it; the internal error is a bug either way, an
unimplemented thing being a diagnostic and not a crash.

**And one smaller thing.**  A top-level array of string literals is not implemented
(9902), which is what a digit table wants to be:

```
let DIGITS: str⟦10⟧ = ⟦"0", "1", …⟧      ※ fatal: not implemented yet
```

A local one works, so `⍕` for an integer can be written today at the cost of filling the
table on every call.  Integer-to-text needs nothing else: this compiles and answers 4
for `⍕1234u64`, which is the length of what it built.

```
@[impure]
fn `⍕`(n: u64) → str:
    let digits: str⟦10⟧ = ⟦"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"⟧
    if n < 10u64:
        digits⟦⎕unit(n, ⌜idx⌝)⟧
    else:
        ⍕(n ÷ 10u64 ?? 0u64) ⧺ digits⟦⎕unit(n % 10u64 ?? 0u64, ⌜idx⌝)⟧
```

What this does not decide
-------------------------

- **How an ordinary-looking call gets its arguments unevaluated**: a parameter of type
  `syntax` saying so (M1), or `macro` invoked with parentheses (M2).  M1 is recommended
  because the rule falls out of what `syntax` already means; M2 needs the question of
  whether a name may be both a macro and a function reopened.
- **How the rest of the arguments are collected**: `⁂args: syntax` read in the direction
  opposite to the one `⁂` already has, or a tuple written out at the call.
- **Whether `{name}` is in at all**, and if it is, how a macro spells "the name the
  caller would have meant by this text" -- which is an exception to hygiene and has to
  be written down as one.
- **Whether an enumeration's names reach run time**, which is what printing a name needs
  and which is a cost every program would carry for something most do not use.  The
  alternative is that `⍕` of an enumeration is its number, and a program that wants the
  name writes the operator over a `match`.
- **Float text**, the one genuinely large piece of work here, and whether the first
  version is shortest-round-trip or something honest and worse.
- **Whether `⍕` is the glyph.**  It is APL's and it is free, which is the whole argument
  for it; a reader who does not know APL sees a mark with no mnemonic.
- **Padding a value whose text is not ASCII**, where a width in characters and a width in
  columns are different questions and the second is not answerable without tables.
- **Whether a template may be a value**, which is what `std::format` allows and what none
  of this does: the template has to be a literal for the check to happen at all.  That is
  the same trade Rust's `format!` makes, and it is where every format-string
  vulnerability comes from in the languages that did not make it.
