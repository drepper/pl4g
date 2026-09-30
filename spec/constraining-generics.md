Constraining Generics
=====================

A proposal, not a decision.  It comes before
[attaching code to objects](attaching-code.md) at the user's direction, and the
ordering turns out to be the right way round: a constraint that can say "this
type has a `next`" *is* the protocol that document was looking for, so settling
constraints first makes that one smaller rather than larger.


Two different things a constraint buys
--------------------------------------

Nearly every argument about bounds is really an argument about which of these two
is wanted, so they are separated before anything else.

**Better errors (P1).**  Today a mistake in a generic is reported in the
definition with a note naming the call:

    error: the operands of '⌈' are of different types ...
        a ⌈ b
    note: largest was compiled for ⸨u8⸩ because of this call
        let n: ⸨u8⸩ = largest(s, t)

What a reader wanted is the other way round: *this call is wrong, because `⸨u8⸩`
does not admit `⌈`*.  Getting that needs the bound to be checked **at the call**.
It does not need the body to be checked differently at all.

**The body checked once, before any call (P2).**  A generic nobody calls is
today checked not at all -- and not merely loosely.  This compiles clean:

    fn broken(a: T', b: T') → T':
        a ⌈ b ⌈ nonsense          ← a name no program defined

`nonsense` is undefined and the compiler says nothing, because there is no set of
types to check the body for.  Fixing *that* needs every operation the body
performs to be licensed by a bound, which is a change in what a generic *is*.

P1 is cheap and breaks nothing.  P2 is the valuable one and it makes programs
that compile today stop compiling: a generic that uses more than it declares
becomes an error.  A proposal that does not say which it is buying is not a
proposal, and this one buys **P1 now and P2 as a later tightening of the same
syntax**, for reasons given under the recommendation.


What the language already has
-----------------------------

Measured, each of these compiled and run while this was written.

**Type parameters are declared by being used.**  `T'` is a parameter because it
is a marked name the program has not defined; there is no list to write.  Every
parameter must stand in a parameter's type (4555), a shape that cannot be read
against the argument's is refused (4556), and two places disagreeing about one
parameter are refused (4557).

**The types come from the arguments**, left to right, and a literal with no
suffix takes the type an earlier argument settled.

**The body is checked per set of types**, and an operation the types do not admit
is reported in the definition with a note naming the call.  There is no bounds
language, which the specification states as a position: "That is C++'s bargain
and not Rust's."

**A generic may call a generic**, and it works today: `biggest` calling `bigger`
twice compiles and runs.  Nothing checks that the caller's types satisfy anything
the callee needs, because there is nothing to satisfy -- the check happens once
both are instantiated.

**One function per set of types**, told apart by their symbols, with the sets
written to the report log as `instantiate`.

**`comptime if` and `⎕typeof(⌜v⌝)`** discriminate types at compile time, so a body
can already branch on what it was instantiated for.  What that cannot do is
report anything *before* the instantiation.

What there is nothing of: a bound, a `where`, a trait, a class, an instance, or
any word in a signature that says what a type must support.


The four vocabularies
---------------------

A constraint system is mostly a choice about what a constraint is *made of*.

### V1. Kinds the compiler already knows

A closed set of words naming categories the compiler computes anyway: `number`,
`integer`, `float`, `truth`, `text`, `record`, `collection`.

    fn largest(a: T' number, b: T' number) → T'

*Costs.*  A closed set: a program cannot add a kind, so the day something wants
"has a `next`" the answer is another word in the compiler.  And the words are
about *what a type is* where the body cares about *what may be done to it* --
`⌈` is defined on every number and on nothing else today, but that is a fact
about `⌈` and not about the word `number`.

*Buys.*  Almost free.  The compiler has the predicates already.

### V2. The operations the body may use

A bound is the operation the body performs, written out -- operators and, once
code can attach to types, functions:

    fn largest(a: T', b: T') → T'  needs T' ⌈ T' → T'
    fn sum(it: I') → E'            needs next(&mut I') → E' ?

A name alone will not do, and the reason is worth stating here rather than under
the recommendation: `needs T' ⌈` would say that `T'` admits `⌈` and would quietly
assume the other operand is a `T'`.  That is true of `⌈` and false of a dictionary
read, an index, and any function of more than one type -- so a bound is written
with a type in every position the operation has, and with what it answers.

*Costs.*  Verbose for a body that does much: a loop over a collection wants `⍴`,
an index, a comparison and an addition, and writing four signatures in every
declaration is a tax on the common case.  That is what bundles are for, below.

*Buys.*  Three things, and they are why this is the recommendation.  It needs **no
new kind of definition** -- no `trait`, no `class`, no `instance` -- because the
vocabulary is names the language already has.  It is **exactly the list P2 needs**:
checking a body once means checking that it uses nothing outside its bounds, and
the bounds are that list.  And it **extends for free** the moment a function can
belong to a type: `needs I' next` is a protocol, and `foreach`'s rule becomes
"the loop's expression must admit `next`" rather than a second mechanism.

### V3. Named bundles a type conforms to

A `trait`, `class` or `protocol`: a named set of requirements, and a type declares
it satisfies one (Rust, Swift) or is found to (Go).

*Costs.*  A definition form, a conformance rule, and -- if conformance is
declared -- coherence: who may say that whose type satisfies whose bundle, and
what happens when two answers disagree.  Coherence is the part nobody enjoys and
every language that declared conformance has had to solve.

*Buys.*  The ergonomics V2 lacks, and a name to talk about.  Which is why the
recommendation keeps the name and throws the rest away: see below.

### V4. A predicate in comptime

Zig's answer, and nearly free here because `comptime if` and `⎕typeof` exist:

    comptime if ⎕typeof(⌜a⌝) ≠ ⌜u8⌝:
        ⎕error("largest wants a number")

*Costs.*  It reports at the instantiation, which is where the error is reported
*now*.  So it buys a better *message* and neither P1 nor P2: there is still
nothing to check before a call, and the check is written in the body rather than
in the signature, where a reader of the call cannot see it.

*Buys.*  A good message with no new language at all, and it is worth saying that
this is available today to anyone who wants it.


Recommendation
--------------

**V2, checked at the call, with bundles as pure abbreviations, and P2 later.**

One thing this says that the first draft of it did not: **a bound is a signature
and not a name.**  A name says which operation and nothing about where the
constrained type stands in it, which is enough for `⌈` -- both operands being the
same type -- and enough for nothing else.  A dictionary read has a container on
one side and a key on the other; a function of three parameters may have one that
is constrained; and an iterator's element type is not in any argument at all.  So
the required operation is written out, with a type in every position and `→` for
what it answers, and the three questions a name left open are answered by the
notation rather than by a rule.

Three layers, each landable on its own and each useful without the next.

### Layer 1: a bound is a signature, checked at the call

**Superseded in its notation.**  [Conditions on functions](conditions.md) proposes
writing the requirement as an *expression over the parameters* rather than as a
clause of a `pre` -- `pre(A'⟦I'⟧ → E')` where this section writes
`needs A'⟦I'⟧ → E'`, which is the same notation under a different keyword.
What the keyword buys is that the *same* one also carries conditions over values,
which a signature cannot express, so a reader of a signature finds both in one
place.  Read this section for what a bound *is* and that one for how it should be
written; everything below about checking, settling and P1 is unchanged by the
change of keyword.

A clause is one level or the other throughout: a bound mentions only types, and a
clause over values is a condition that is evaluated.  That is the rule that keeps
the two out of each other's way.


A bound is **not** a name.  Writing `needs T' ⌈` would say that `T'` admits `⌈`
and would quietly assume the other operand is a `T'` too -- which is true of `⌈`
and false of half the operations a generic body performs.  So a bound is the
required operation *written out*, with a type in every position it has and `→`
for what it answers:

    fn largest(a: T', b: T') → T'        needs T' ⌈ T' → T'
    fn count(d: D', k: K') → u64         needs D' ⸨K'⸩ → u64 ?
    fn at(xs: A', i: I') → E'            needs A'⟦I'⟧ → E'
    fn sum(it: I') → E'                  needs next(&mut I') → E' ?
    fn same(a: T', b: U') → bool         needs T' = U' → bool
    fn many(x: T') → u64                 needs ⍴T' → u64

Each form is the operation as a body would write it, with the operand positions
filled in.  An operator keeps its shape -- `T' ⌈ T'` is infix because `⌈` is
infix, `A'⟦I'⟧` puts the index where an index goes, `⍴T'` is prefix -- and a
function is written as a call.  That is what makes the answer to "which position
is the constrained type in?" not need asking: it is in the position it is written
in.

**Three things this buys that a list of names cannot say.**

*Which operand is which.*  `needs D' ⸨K'⸩ → u64 ?` says a `D'` may be read with a
`K'`.  `needs K' ⸨D'⸩ → u64 ?` says something else and would be a different
bound.  A list of names has no way to tell them apart, and `⸨⸩` is the case that
makes this plain: a dictionary read has a container on one side and a key on the
other, and they are never the same type.

*What else the operation takes.*  A function of three parameters where one is
constrained is written with all three: `needs put(&mut T', K', V') → bool`.

*What it answers.*  `needs A'⟦I'⟧ → E'` is where `E'` comes from.  That is the
work an associated type does elsewhere, done by the bound that needs it.

**A type parameter may be settled by a bound.**  Rule 4555 says every type
parameter stands in a parameter's type, because one written only in what the
function answers with is one no call could settle.  A bound settles one too:
`E'` in `needs A'⟦I'⟧ → E'` is whatever indexing an `A'` with an `I'` answers, and
the call knows that once `A'` and `I'` are settled.  So the rule becomes: **every
type parameter is settled by an argument or by a bound**, bounds read after the
arguments and left to right, and one that neither settles is refused as 4555
refuses one now.

That is what lets `fn sum(it: I') → E'` be written at all: the element type is
not in any argument and is not the program's to write -- it is what `next`
answers.

**The check.**  A call settles the parameters as it does today, then each bound is
asked of the settled types.  One that does not hold is reported **at the call**,
which is P1:

    error: 'largest' needs '⸨u8⸩ ⌈ ⸨u8⸩', and '⌈' is not defined on a set
        let n: ⸨u8⸩ = largest(s, t)
    note: largest says so here
        fn largest(a: T', b: T') → T'  needs T' ⌈ T' → T'

The body is still checked per instantiation, exactly as now.  So **nothing that
compiles today stops compiling**: a bound is a promise the call must keep, not yet
a limit on the body.  A generic with no bound behaves as it does now.

A generic calling a generic must satisfy the callee's bounds.  With signatures
that is not a subset check on names but the same question asked one level up --
does *this* function's set of bounds license the one the callee wants -- and where
the caller's parameters are still open it is answered by matching the callee's
bound against the caller's, which is the same shape-matching rule 4556 already
does between a parameter's written type and an argument's.

### Layer 2: a bundle is an abbreviation

A bundle names a set of bound signatures, and takes the parameters they are
written over:

    bundle number(T'):    T' + T' → T'  ;  T' - T' → T'  ;  T' ⌈ T' → T'
                          T' = T' → bool
    bundle iterator(I', E'):  next(&mut I') → E' ?
    bundle indexed(A', I', E'):  A'⟦I'⟧ → E'  ;  ⍴A' → u64

    fn largest(a: T', b: T') → T'   needs number(T')
    fn sum(it: I') → E'             needs iterator(I', E')

A bundle is **not** a type, not a value, and nothing conforms to it: `needs
number(T')` expands to its signatures with the parameters substituted, and each is
checked as layer 1 checks one.  So there is no conformance rule and no coherence,
because there is nothing to conform *to* -- a type admits `⌈` if `⌈` works on it,
which is a question about `⌈`.

That a bundle takes parameters is what lets it carry the answer type: `iterator(I',
E')` says `E'` is settled by the bound, exactly as the written-out form does.  A
bundle may name another bundle; a cycle among them is refused.

With the clause notation of [conditions](conditions.md) **a bundle's body is
unchanged from what is written above** -- bare expressions, separated by a line, a
`;` or braces, since everything in a bundle is a requirement and a word saying so on
every line would say the only thing a line there can say.  What changes is that
`needs` goes away: a bundle is asked for by applying it inside a clause, which that
document works out in full under *Bundles, and what abbreviates what*:

    bundle iterator(I', E'):  next(I') → E' ?

    fn sum(it: I') → E'  pre(iterator(I', E')):

`pre(iterator(I', E'))` is a clause over types and means what one always means, that
the application can be written -- which for a bundle is every expression it holds
with the arguments put in.  So no keyword is added at all.  The observation that
makes the whole of it fall out is that a condition needs no bundle -- a pure function
answering `bool` abbreviates one -- and that a requirement cannot use a function,
there being nothing to call at the type level.

That is the whole of what V3's ergonomics are worth, at none of V3's price -- and
with parameters it reaches what V3 needs an associated type for.

### Layer 3: the body may use nothing it did not ask for

A rule, not new syntax: an operation in a generic body on a value of a type
parameter must be named in that parameter's bound.  Then the body is checked once,
`nonsense` above is an error the day it is written, and a generic nobody calls is
checked like every other function.

This is where programs stop compiling, which is why it is last and why it should
arrive behind a switch before it arrives as a rule -- a warning first, then an
error.  A generic with no bound at all would have to mean "nothing may be done to
it", which is a sharp change, so the rule wants a way to say "checked the old
way" for as long as anything needs it.

### Why layered rather than all at once

Because the syntax does not change between the layers.  Layer 1 is a check at
the call, layer 3 is the same list read as a limit on the body, and a program
written for layer 1 is already written for layer 3 if it was honest.  The
alternative -- design P2 and land it whole -- makes every generic in the language
and in `std` change in one commit, and makes the first thing a reader meets the
strictest version of it.


What this does not decide
-------------------------

- **How a bound is written.**  `needs T' ⌈` is what this document writes to have
  something to read; `where`, an attribute `@[needs(...)]`, and a mark on the
  parameter itself are all candidates.  A signature is part of an object's own
  description, which argues against an attribute; and the language has no `where`
  to reuse.  The word matters less than that the list is in the signature, where
  a reader of the call can see it.
- **Whether an operator's name in a bound is the glyph or a word.**  `⌈` is what
  a body writes; `largest` is what a person says.  The glyph is fewer decisions.
- **Whether a bound may say anything about a *shape*** -- `needs iterable(T')`
  versus writing the parameter as `E'⟦⟧`.  The second is already expressible as a
  parameter type and probably answers it.
- **Whether a bundle may be exported from a module.**  It is a name like any
  other, so probably yes, and then nothing more.
- **Whether a bound may be satisfied by more than one candidate.**  `needs
  next(&mut I') → E' ?` asks for *a* `next`; the language has no overloading, so
  today at most one can exist and the question does not arise.  It arises the day
  it does, and the answer wanted then is probably that an ambiguous bound is
  refused rather than resolved.
- **Whether what a bound answers must match exactly.**  `needs A'⟦I'⟧ → E'`
  settles `E'`, so there is nothing to match; but `needs T' = T' → bool` states
  `bool` outright and a type whose `=` answered something else would fail it.
  Stating the answer and settling it are two different uses of one notation, and
  which is meant is currently read off whether the answer names a fresh parameter.
  That is subtle enough to want saying in the specification rather than inferring.
- **How a bound reaches an operation with no notation** -- a cast, a field, a
  match arm.  Every example here is an operator or a call because those are what
  a bound can be written as; `needs .name : T' → U'` and its like are questions
  nobody has asked yet.


Comparisons
-----------

**C** constrains nothing: a macro over types is text, and the error is wherever
the text lands.  It is the version of C++'s bargain with no diagnostics at all,
and the reason every C generic is a naming convention in a comment.

**C++** is where this language's generics come from, and its history is this
document's argument.  Templates checked at instantiation gave errors nobody could
read; `enable_if` and traits classes were bounds written by hand in the type
system; concepts arrived twenty-nine years later and are, in essence, layer 1 and
layer 3 of this proposal with a much larger vocabulary.  `requires
requires` is what happens when the syntax is grown rather than designed.

Its **requires-expression** is layer 1's signature form almost exactly, and is
worth reading beside it: `requires(T a, T b) { { a < b } -> convertible_to<bool>;
}` writes the operation with its operands and says what the result must be, which
is `needs T' < T' → bool` with more punctuation.  That C++ arrived at the same
shape after thirty years of trying the alternatives is the strongest argument for
it here.  What C++ has that this does not propose is a *conversion* on the answer
-- `convertible_to<bool>` rather than `bool` -- which is a question the open list
above now carries.

**D** has template constraints as ordinary compile-time boolean expressions --
`if (isNumeric!T)` -- which is V4 with a place in the signature to write it.  That
is the cheapest thing anyone has shipped that still reports at the call, and it
is worth noting as the alternative to layer 1 if arbitrary predicates are wanted;
its cost is that a bound is code and cannot be read as a list.

**Go** constrains with interfaces, and its type sets are close to layer 2: an
interface used as a constraint may name methods *or* a set of types, including
unions of primitives, which is exactly `bundle number`.  Go declares nothing:
conformance is structural, which this proposal follows.

Where Go cannot follow is the thing that made a signature necessary here: an
interface constrains **one** type, the receiver, so "a `D'` may be read with a
`K'`" is not a Go constraint at all -- `map[K]V` is built in because a
user-written one could not be constrained.  That is the clearest evidence that a
bound over one name is not enough.

**Rust** is V3 in full: traits, `impl`, coherence, associated types, where
clauses, and a body checked once.  It is the most complete answer available and
its cost is visible in the size of the language; `IntoIterator` being three traits
and an associated type to say what a `for` loop needs is the part worth weighing
against layer 2's one line.

**Odin** has no constraints and no overloading, and its answer to `largest` is to
write it per type or to use its `where` clauses on polymorphic procedures, which
are D's compile-time predicates.  It is the position this language holds today,
held deliberately.

**Zig** is V4 and says so: a constraint is `comptime` code, an interface is a
struct of function pointers built by hand, and the error is an `@compileError`
with whatever text the author wrote.  It gets remarkably far, and what it gives
up is that a call cannot be checked against a signature -- which is the one thing
P1 is.

**APL**, **BQN** and **UIUA** constrain by rank and shape rather than by type: a
function is defined for the shapes it is defined for, and a mismatch is a RANK or
LENGTH error at run time.  There is nothing to write in a signature because there
are no signatures.  What they contribute here is the observation that the useful
constraint is often about *shape* -- which is the third open question above, and
the one the language already half-answers by letting a parameter be written
`T'⟦⟧`.

**LISP** and **Scheme** constrain nothing and check nothing until the call
happens, at run time; CLOS dispatches on classes and so replaces the constraint
with the dispatch.  It is the limit case of late checking, and the reason it is
liveable there and not here is that a compiler cannot ask the programmer to run
the program to find out whether it compiles.

**Haskell** is the other limit: a class *is* the constraint, an instance is
declared apart from both the class and the type, inference finds the constraints
the programmer did not write, and a body is checked exactly once.  Its
multi-parameter type classes are layer 2's parameterised bundles -- `class
Indexed a i e` is `bundle indexed(A', I', E')` -- and its associated types are
what `→ E'` does here; that Haskell needed a language extension for each says
which of the two notations grew and which was designed.  It is the most
principled answer and it buys the principle with a language where the
constraints are inferred -- which is not available here, the types coming from the
call and not from a solver.

**Wolfram** constrains by pattern: `f[x_Integer]` is the constraint and the
definition in one, and a call that matches no pattern is simply undefined.  It is
the design where a bound and a body are the same thing, and the reason not to
reach for it is the reason given in the other proposal: it makes the set of
definitions of a name open-ended.


What each layer would take
--------------------------

**Layer 1.**  A form in the signature, which is a small grammar of its own -- an
operator between operand types, a call, a prefix operator, an index -- and the
same rule in `tree-sitter-pl4g/grammar.js` in the same commit.  The bounds carried
on the function; the amendment to 4555 so that a bound may settle a parameter, and
the ordering that goes with it; a check after the types are settled, beside 4555
through 4557; three diagnostics -- a call whose types do not satisfy a bound, a
bound written over an operation that does not exist, and a parameter nothing
settles; and the matching rule for a generic calling a generic.  The backend is
untouched: a bound is gone before anything is lowered.

The signature grammar is the part that grew when the hole in the name-only form
was found, and it is worth saying that it is *reused* rather than new: an operator
written between two types is the shape `_parse_type_ref` and the expression parser
each already read, on either side of the same glyph.

**Layer 2.**  A definition form for a bundle, a name to resolve, an expansion,
and a refusal for a cycle.  No checking of its own -- it expands into layer 1.

**Layer 3.**  The hard one, and the estimate is honest: every operation in the
semantic analysis that is performed on a value whose type is a parameter has to
ask whether the bound licenses it, which means the check has to be threaded
through the places that today simply ask the settled type.  It also wants a way to
say "check the old way", and every generic in the language's own sources has to
grow a bound.  It should not be attempted until layers 1 and 2 have been lived
with.


What it does to the other proposal
----------------------------------

If layer 1 lands, [attaching code to objects](attaching-code.md) gets smaller in
two ways.

Its fourth decided point -- *a protocol is a name, and nothing declares
conformance* -- stops being a position the proposal has to argue and becomes the
way bounds already work: `needs next(&mut I') → E' ?` is the protocol, checked at
the call, and it says what the other document could not -- which argument the
iterator is, that it is taken by reference, and that the element type comes from
what `next` answers.

And `foreach`'s rule becomes one sentence with no new machinery: *the loop's
expression must satisfy `iterator(I', E')`, and the name it binds is an `E'`*.
That is layer 1's check, asked by the loop instead of by a call, and the element
type falls out of it rather than being a further question.

The recommendation there does not change -- a function named `T.next` is still
the smallest way to say which code is a type's -- but it is now the second half of
one design rather than the whole of its own.
