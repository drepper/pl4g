Attaching Code to Objects
=========================

A proposal, not a decision.  It is now the second half of a pair:
[constraining generics](constraining-generics.md) comes first, and what that one
calls a bound over a function's name is what this one calls a protocol -- so read
that first, and read the last section of it for what it does to this.

What this is for is the question the language cannot answer today: **how does a program say that a piece of code belongs to a type?**
Nothing decided here is settled until the user says so; what is here is the
ground, four designs, a recommendation, and what each of them costs.


What wants it
-------------

Three things, in the order they asked.

**A program writing an iterator.**  `foreach` walks five things and the compiler
is every one of them.  For a program's own type to be walked, the loop has to
find that type's `next`, and there is nowhere for a type's `next` to be.  The
to-do entry called this "a way to write a type with a `next`", which reads as a
small thing; it is the whole of this document.

**`std.Build`.**  `add_executable` is a function the compiler knows rather than
one the module writes, because a function that belongs to the build object has
nowhere to live.  The to-do list says so in its own words: "Each of them is a
field or a function on `std.Build`".

**Everything a reader would call a method.**  `⍴` is a glyph, `⎕ones` is a
builtin, `⎕narrow` is a builtin.  Each of them is a thing the compiler does to a
value because the language has no way for a *program* to do things to values by
name.  Every one of those builtins is a decision that could not be deferred.


What the language already has
-----------------------------

Measured rather than remembered: each of these was compiled and run while this
was written.

**Generics, with no bounds language.**  A parameter written `T'` is read off the
argument, one function is made per set of types, and the body is checked per
instantiation -- so an operation a type does not admit is reported in the
definition with a note naming the call that asked for it.  That is C++'s bargain
and the specification says so outright.

*This is the most important fact here.*  A protocol needs no trait system,
because the language already dispatches by checking each instantiation.  A
generic function calling `next(it)` works the moment `next` resolves.  Nothing
has to be said about what `I'` supports, because nothing anywhere says what a
type parameter supports.

**Compile-time discrimination of types.**  `comptime if` and `⎕typeof(⌜v⌝) =
⌜u8⌝` let a generic body branch on the type it was instantiated for.  A protocol
could be written by hand today, as a chain of `comptime if` over every type that
implements it -- which is a closed set written in one place, and is exactly what
is wrong with it.

**A record can hold a function, and a program can call it.**

    type Holder = step : fn(u8) -> u8 ; at : u8
    let h: Holder = Holder(.step <- twice, .at <- 3u8)
    let f: fn(u8) -> u8 = h.step
    f(h.at)                            -> 6

So a vtable by hand is expressible.  What it costs is a word per function in
every value, an indirect call, and purity: a call through a value is assumed to
do everything, so `f(h.at)` forces `@[impure]` on whoever writes it.

**`h.step(h.at)` does not parse as a call.**  `NAME.NAME(` is read as a path
through a module, so the program above is refused with "'h' is not a module, so
nothing can be named through it" (4103).  Any method syntax has to be resolved by
what the left side *is*, which is a real change to a rule that is currently
syntactic.

**Paths already exist.**  `std.Build` is a type named through a module, so
`A.B` is a shape the parser reads and the resolver follows.

**Attributes already attach things.**  `@[listable]` attaches a calling rule to a
function; `@[abi]` attaches a layout to a type; a unit attaches a meaning to a
number.  None of them attaches *code*.

What the language does **not** have: a method, a trait, an interface, an `impl`,
overloading, or any way for two functions to share a name.


The four designs
----------------

### A. By name and first parameter

A function whose first parameter is the type *is* the type's function.  `foreach`
over a `Walk` looks for a function called `next` whose first parameter is `Walk`
or `&mut Walk`.

    fn next(it: &mut Walk) -> u8?          @[impure]
    foreach x := walk:                     -- finds it

*What it costs.*  Two types in one file cannot both have a `next`, because two
functions cannot share a name.  Making them able to is overload resolution --
picking between candidates by argument type -- which is a new and large thing,
and the first one in the language.  Every protocol name becomes reserved in every
file that implements one.

*What it buys.*  No syntax at all.  A program that writes `next` has written the
method.

### B. A function named inside the type

A function may be named `Walk.next`, and that name is the whole of what attaches
it.  Called as a path, which the parser already reads:

    type Walk = at : u8 ; last : u8

    @[impure]
    fn Walk.next(it: &mut Walk) -> u8?
        ...

    Walk.next(&mut walk)                   -- a path, like std.Build
    foreach x := walk:                      -- finds Walk.next

*What it costs.*  One rule in the parser (a definition's name may be a path), one
in the resolver (a path whose first part is a type), and a decision about whether
the first parameter is implicit.  It does not give `walk.next()` sugar; that is a
separate step and may never be taken.

*What it buys.*  Namespacing for nothing: two types may each have a `next` and
neither reserves the name.  It composes with everything already there -- paths
parse, generics check per instantiation, `comptime if` discriminates.  And the
protocol's rule is one sentence: *the `next` of a type `T` is the function named
`T.next`.*

### C. A field holding a function

What works today, made cheap: the compiler recognises a field whose value is a
function it knows, and calls it directly rather than through the register.

*What it costs.*  A word per function in every value, which is the wrong price
for something every value of a type shares.  And it does not answer the question:
a protocol still cannot *find* a type's `next` without being handed a value that
already holds it.

*What it buys.*  Nothing new -- it is an optimisation of something already
written -- but it is the only one of the four that lets two values of one type
carry different code, which is what a callback is.

### D. A trait with bounds

`trait Iterator { fn next(&mut self) -> T? }`, and a generic function may say
`I': Iterator`.

*What it costs.*  A language for bounds, which the generics decision explicitly
turned down; a second checking regime beside instantiation-time checking; and the
whole of coherence -- who may implement what for whose type.  It is the largest
thing in this document by an order of magnitude.

*What it buys.*  A generic function checked once rather than per instantiation,
and an error reported at the call rather than in the definition.  That is worth a
great deal and it is worth it *later*: it is a change to how generics are
checked, not a way to attach code, and doing it to get an iterator would be
answering a small question with the largest available answer.


Recommendation
--------------

**B, and nothing else yet.**

The reasoning is that B is the only one of the four that is *small* and *not a
dead end*.  It needs no overloading (A does), no per-value cost (C does), and no
bounds language (D does).  It reuses three things that already exist -- the path
syntax, module-member resolution, and instantiation-time checking -- and it leaves
every larger decision open: `walk.next()` sugar can be added to B, and a trait
can be added over B, and neither is foreclosed.

What B decides, concretely:

1. A definition's name may be a path of two parts, `T.name`, where `T` is a type
   defined in the same file.  Not a type from another module, not three parts:
   both are questions nobody has asked.
2. The first parameter is written out.  No implicit receiver, no `self`.  The
   reason is that the language has nothing else implicit, and a receiver would be
   the first thing a reader has to know is there without seeing it.
3. `T.name(args)` is how it is called.  `value.name(args)` is **not** part of
   this proposal.
4. A protocol is a *name*: the `next` of a type `T` is the function `T.next`, and
   `foreach` over a value of `T` calls it.  Nothing declares that `T` is an
   iterator; having a `T.next` of the right shape is what being one is.
5. The symbol in the image is the path, mangled as any other name is, so nothing
   about the backend changes.

What it does **not** decide, and should not:

- **Purity.**  A `next` that advances through `&mut` writes memory that outlives
  the call, so it is impure, and every `foreach` over a program's iterator is
  then inside an impure function.  The pure shape answers the element and the
  next iterator together -- `fn Walk.next(it: Walk) -> <T, Walk>?` -- and that
  return type cannot be written at all today, the `?` being read only after a
  named type.  Which shape the language wants is a decision of its own, and it
  has a prerequisite in the to-do list either way.
- **Whether a type may be extended from outside its file.**  Saying no is the
  smaller answer and can be relaxed; saying yes brings coherence with it.
- **Whether the same rule applies to an enumeration, a tuple, a unit, or a
  built-in type.**  A `u8.next` is a question this does not ask; the proposal covers
  a type a program defined with `type`.


Comparisons
-----------

**C** attaches nothing: a function over a `struct` is a function whose first
parameter is a `struct`, and the convention is the name -- `fclose(FILE *)`.
That is design A without the ambition, and C lives with the collisions by
prefixing every name with the module's, which is what B's paths do properly.

**C++** has member functions, and everything that followed from putting the
receiver inside the type: overloading, `this`, access control, virtual dispatch,
and a name lookup nobody can summarise.  Its templates are where this language's
generics come from, and its instantiation-time checking is what makes a trait
unnecessary here.

**D** has uniform function call syntax: `x.f(y)` *means* `f(x, y)`, so there are
no methods at all and every free function is callable as one.  That is the most
economical answer to "attach code to objects" ever shipped -- it attaches nothing
and gets the syntax anyway -- and it is design A with sugar.  Its cost is D's
cost: with no namespacing, the name is the interface, and a module's functions
are in one flat namespace.

**Go** decides it by the receiver: `func (w *Walk) Next() (byte, bool)` is in
`Walk`'s method set because it says so in the parentheses.  Its interfaces are
satisfied structurally -- nothing declares that `Walk` implements `Iterator` --
which is precisely the fourth point of the recommendation, arrived at
independently and for the same reason: a declaration of intent is a thing to keep
in step with the code.

**Rust** is D above: a trait, an `impl`, coherence, and bounds checked once.  It
is the most complete answer and the most expensive, and its `IntoIterator` is
three traits and an associated type to say what `foreach` needs.

**Odin** has no methods and says so as a design position: a procedure taking the
type is the method, and `using` brings a struct's fields into scope.  It is A,
chosen deliberately, by a language that also has no overloading -- which is the
honest version of A and shows what A costs.

**Zig** is B, almost exactly: a function declared inside a `struct` is
`Struct.function`, called as a path, with the receiver written out; `struct` is a
compile-time value, so a "trait" is a function from a type to a type.  Its
iterators are `it.next()` returning an optional, which is this proposal's shape
with the sugar.  That the smallest sensible answer here is the one Zig chose is
the strongest argument for B in this document.

**APL**, **BQN** and **UIUA** attach nothing to anything: an array is an array,
and a function is a function that takes arrays.  A "method" is a name, and
dispatch is on rank and shape rather than on type.  What they have to say to this
language is not a design but a warning: every glyph the compiler spends on a
builtin is a glyph a program cannot spend, and `⍴`, `⎕ones` and `⎕narrow` exist
because there was no way to write them.  A program that can attach code needs
fewer builtins, which is an argument for doing this sooner rather than later.

**LISP** and **Scheme** attach code to nothing and dispatch on everything: CLOS
puts the method outside the class entirely, dispatches on every argument rather
than the first, and lets anybody add a method to anybody's class.  It is the
proof that "which argument is the receiver" is a choice and not a law -- and the
reason nearly everyone chooses the first is that a reader looking for a type's
code wants one place to look.

**Python** attaches by a dictionary on the class and resolves at every call,
which makes everything possible and nothing checkable; its iterator protocol is
two names, `__iter__` and `__next__`, found by name and not by declaration --
which is again the fourth point.

**Haskell** attaches nothing to types and everything to classes: an instance is
declared apart from both, resolution is by type inference, and the bounds are the
class.  It is D taken to its conclusion, and what it costs is that the code a
call runs cannot be read off the call.

**Wolfram** attaches code to *patterns* rather than to types: a definition is a
rewrite rule, and `f[x_Integer]` and `f[x_List]` are two rules on one name.  A
type is a pattern that matches, dispatch is by the most specific rule, and there
is no receiver at all.  It is the only design here that would need no new name
form -- the shape of the argument would say which body runs -- and the reason not
to reach for it is that it makes the set of definitions of a name open-ended,
which a compiler that wants to see a program whole cannot want.


What it would take
------------------

An estimate, so that the decision is not made without one.

- **The parser**: a definition's name may be `IDENT . IDENT`.  One rule, and the
  same rule in `tree-sitter-pl4g/grammar.js`, in the same commit.
- **The resolver**: a two-part path whose first part names a type in this file
  resolves to that type's function.  Beside the module-path rule that is there.
- **Mangling**: the symbol is the path.  Nothing new; a name with a dot is a name.
- **`foreach`**: one arm in `_iteration_of` -- a type with a `T.next` of the
  right shape is walked by calling it -- and the loop machinery already carries
  state across turns and tests a result, which is what it needs.
- **The specification**: a section under Definitions, and an entry in
  `decisions.md`.
- **Tests**: a type with two functions, two types each with a `next`, a call
  through the path, a `foreach` over a program's iterator, and the refusals -- a
  path naming something that is not a type, a `T.next` of the wrong shape.

The `foreach` arm is the only part that touches anything delicate, and the piece
worth doing *before* any of it is smaller still: **`foreach` over a cursor**,
which is refused today (4438) although a cursor is the compiler's own iterator
value and the loop's shape already fits it.  That needs no decision from this
document at all, and it is the half of the iterator entry that is not blocked on
anything.
