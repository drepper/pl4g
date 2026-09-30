Conditions on Functions, and Constraints as Conditions
======================================================

**Decided and implemented**, on 2026-09-30; see
[decisions.md](decisions.md) for the entry and
[the specification](spec.md) for what the language now says.  What follows is the
reasoning, kept as it was written except where the implementation found the
document wrong, which is marked where it happened.

It answers a question the user put: the language wants pre- and post-conditions,
and a constraint on a generic could be one of them rather than a thing of its
own.

The answer is that one clause does both, and that **what decides which it is, is
whether the expression is over values or over types.**  A clause over values is a
condition: it is evaluated, it must be a `bool`, and the program stops where it is
false.  A clause over types is a requirement: nothing is evaluated, and what is
asked is that the expression can be written.

    fn f(a: T') → T'  pre(somefunc(a)):        a condition, and an error unless
                                        somefunc answers a bool
    fn f(a: T') → T'  pre(somefunc(T')):       a requirement: there is a somefunc
                                        that accepts a T'
    fn f(a: T') → T'  pre(somefunc(T') → T'):  the same, and it answers a T'

So **a constraint on a generic mentions only types**, and `pre(somefunc(a))` is not
one: `a` is a value, so that clause is a condition and `somefunc` is called.  The
expression form is then still **better than the signature form** proposed in
[constraining generics](constraining-generics.md) -- so that document's layer 1
should adopt it -- and it can say everything that form could, a type-level clause
being able to speak about a type no parameter holds a value of.

Three things a condition could mean
-----------------------------------

As with the two things a bound buys, most of the argument is about which of these
is meant, so they are separated first.

**Checked (C1).**  The condition is evaluated and the program stops where it does
not hold.  What `assert` is, what Eiffel and D do, and what this language already
does for every arithmetic operation that cannot answer.

**Proved (C2).**  The compiler must show the condition holds at every call, and a
call it cannot show is refused.  What SPARK, Dafny and Whiley do.  It needs a
solver, a language for saying what a loop preserves, and an answer for every
place a proof fails that is not a bug.  It is a different project.

**Typed (C3).**  The condition is not evaluated at all: what it asks is that the
expression *can be written*, and that is a fact about types.  `pre(somefunc(T'))`
requires a `somefunc` that accepts a `T'`, and `pre(somefunc(T') → T')` requires one
and names what it answers.  The expression is over types, which is what says that
this is the reading meant.

This proposal is **C1 and C3, with C3 doing the work of a constraint**, and C2
noted as what neither forecloses.  The important observation is that C3 is not a
weaker C1 -- it is a different question about the same expression, and the
notation can ask either.


What the language already has
-----------------------------

Measured.  These were compiled and run while this was written.

**A check that stops the program, with a message and a number.**  `AssertInst`
carries what was wanted and which kind of stop it is; a fault names the operation,
the function and the line; the status is one of a table of kinds.  A condition
needs none of that built.

**The caller is already named.**  A fault walks the stack and writes a line per
frame, so a check inside a callee already reports who called it:

    t.pl4g:4:5: pl4g: ...  in 'inner'
      called from 'middle'
      called from 'outer'

This is the argument that settles where a pre-condition is checked, below.

**What can be shown while compiling is an error and not a fault.**  The language
already draws that line for arithmetic: an overflow the compiler can see is a
compilation error, and one it cannot is a fault.  A condition follows the same
rule without inventing it.

**Purity is a thing the compiler knows.**  A function says whether it may change
what outlives the call, and a pure one may be moved, repeated or dropped.  A
condition wants exactly that property.

**A signature may already hold an expression** -- a parameter's default value --
but it must be settled while compiling and **may not read another parameter**
(4525).  So the precedent is for an expression in a signature and not for one in a
scope that holds the parameters.  That part is new, and it is the part the user
called "a more complicated parsing of expressions" -- and it is wanted by conditions
only, a requirement reading no values at all.

What there is nothing of: a condition on a function, a name for what a function
answered, or any expression in a signature that mentions a parameter.


The proposal
------------

### One clause, and the one rule that decides

    pre(EXPR)              EXPR over values: a condition.  It is evaluated, it must
                           be a `bool`, and the program stops where it is false.
    pre(EXPR)              EXPR over types: a requirement.  Nothing is evaluated,
                           and what is asked is that the expression can be written.
    pre(EXPR → T')         the same requirement, and T' is what it answers.

**The reading is decided by what the expression is over and by nothing else** --
not by the arrow, which only says what a requirement answers, and not by a second
keyword.  An operand is a value or a type, and a clause is one level or the other
throughout.

**A type is written as a type parameter or between lifting marks.**  `T'` bare,
because the apostrophe is already the marker; `⌜u64⌝` or `⌜u8⟦3⟧⌝` for a type that
is not one, because that is what the marks are for.  This is the fourth place that
reads a lift, and 4514 -- a lift anywhere else is refused -- grows by that one
entry and no more.

    fn take(xs: A', i: I') → E'
        pre(A'⟦I'⟧ → E')
        pre(i < ⍴xs):

The first clause is over types: indexing an `A'` with an `I'` must be writable, and
`E'` is what it answers.  The second is over values: the index must be less than
the length, which is checked.  One clause form has said what the other document
needed two notations for, and which is which is read off the operands.

**One expression per clause, and as many clauses as a function wants.**  `pre` and
`post` may be written in any order and any number of times.  Each clause is its own
check with its own span, so a failure points at the clause that failed:

    error: 'take' needs 'i < ⍴xs', and it does not hold here
        take(row, 9)
    note: take says so here
        pre(i < ⍴xs)

A comma-separated list inside one `pre` would have to carry its own positions to
say that much, and would then be a list of clauses with worse punctuation.
Separate clauses also let a reader put a requirement next to the condition that
depends on it, and make the settling order the order on the page.

### Why a requirement must be over types

A requirement is the only way to say something about a type **no parameter holds a
value of**:

    fn total(xs: A') → E'
        pre(A'⟦⌜u64⌝⟧ → E')
        pre(zero(E') → E'):

`E'` is the element type, settled by the first clause.  The second asks that a
`zero` of it can be had, which the summation needs before it has added anything --
and no expression over `xs` can ask for it, there being no `E'` in the signature to
write one over.  That requirement is `Default` in Rust, `mempty` in Haskell, `T{}`
in C++, `create make` in an Eiffel generic constraint, and a gap in Go; every
language with generics has had to answer it, and a value-level expression cannot.

**Three refusals keep the line sharp.**

*A clause mixing types and values.*  `pre(i < ⍴xs ∧ somefunc(T'))` is refused
rather than given a meaning: the reading that makes the type operand legal is the
reading that stops checking the other half, and a condition silently becoming a
requirement is the one failure mode this notation could have.  What to write
instead is two clauses, which is what several clauses are for.

*An arrow on a value-level clause.*  `pre(somefunc(a) → E')` is refused.  Settling
a type from the type of a value is what the parameter list already does, and
`somefunc(a)` is a call that happens -- so the arrow, which says what an
unevaluated expression *would* answer, has nothing to name.

*A type-level `post`.*  A requirement is a fact about types and does not happen at
a point in the call, so it belongs to `pre`.  **A `post` is always a condition**,
which also settles what `post(EXPR → T')` means: nothing, and it is refused.

### The marks and the apostrophe

**The bare `T'` costs the grammar nothing, measured.**  The apostrophe already
continues an identifier in the lexer and in `tree-sitter-pl4g/grammar.js` alike, so
`T'` is one identifier token today and an identifier is already an operand.
Written in an expression it parses and then fails to resolve:

    let x: u6 = T'
    t.pl4g:3:17: error: 'T'' is not defined [PL4G-4003]

A syntax error would have meant a grammar change.  A name that is not defined means
one arm in the name resolution and nothing else.

**A lift keeps its three readings, which is what tells a type from a value.**
`⌈`, `⌊`, `⎕typeof` and the comparison of two lifts take a lifted type and answer
a value, as today; a lift anywhere else in a clause *is* the type.  So the marks
distinguish the two things a clause could want of a type:

    ⌈⌜T'⌝       the largest value the type holds -- a value, so the clause is a condition
    ⌈T'         that ⌈ applies to a T' at all -- a type, so the clause is a requirement

**A concrete type takes the marks.**  `pre(somefunc(⌜u8⌝))` says it, and
`pre(somefunc(0u8))` says the value-level thing; a concrete type always has a value
that can be written, which is why the interesting case is the type parameter and
why the apostrophe is where the notation is free.

### What follows from the split

**Purity is asked of a condition only.**  A requirement is not evaluated, so what
it names need not be pure: the question was whether the call type-checks.

**A requirement never has a run-time cost**, by construction rather than by
optimisation, which is the other half of the removability seam below.

**A type parameter standing in an operand settles nothing.**  It must already be
settled -- by an argument or by an earlier clause's arrow -- which is 4555's
refusal and not a new one.

### Where the clauses go, which is the one real cost

Several clauses want several lines, and **a signature may not span lines today
except inside its parentheses.**  Measured, both of these compiled while this was
written.  Parameters across lines are fine, the existing rule that line breaks are
free inside brackets covering them:

    fn plus(a: u8,
            b: u8) → u8:
        a + b

Anything after the closing parenthesis is not:

    fn plus(a: u8, b: u8)
            → u8:
        a + b

    t.pl4g:3:1: error: indentation does not match any enclosing block [PL4G-2101]

The newline ended the header, the indented `→ u8:` line opened a block of its own,
and the body then matched no indentation -- which is the error, three lines away
from the cause.  And the newline *had* to end the header, because **a header with
no body is a declaration**: `@[external]` functions are written that way, and the
parser says so where it decides, that "what says there is none is that the line
ends here".

So a clause on a line of its own asks for a newline inside a header, in the one
place a newline already means something else.  Three ways out:

**W1.  One line, and nothing changes.**

    fn take(xs: A', i: I') → E' pre(A'⟦I'⟧ → E') pre(i < ⍴xs):

Legal under today's rules exactly as written -- the clauses are after the return
type and before the colon, all on the header's line.  It reads well for two short
clauses and badly for four, which is the whole of the objection to it.

**W2.  A newline before `pre` or `post` continues the header** (recommended).  The
clause lines are indented past the `fn`, and the header ends where it ended before,
at the colon that opens the body:

    fn take(xs: A', i: I') → E'
            pre(A'⟦I'⟧ → E')
            pre(i < ⍴xs):
        xs⟦i⟧

The rule is one token of lookahead: a newline whose next line begins with `pre` or
`post` does not end the header, and the indentation of those lines is not a block.
For the compiler's parser that is a line of code where it decides between a body
and a declaration.  For the grammar it is the external scanner's business, since
the scanner is what turns a line's leading spaces into INDENT, and it would have to
be told that these lines are a continuation -- **which is the piece to prototype
first**, the scanner being where this project has paid for cleverness before.

**W3.  The clauses inside brackets**, where line breaks are already free:

    fn take(xs: A', i: I') → E' ⟨pre(A'⟦I'⟧ → E'),
                             pre(i < ⍴xs)⟩:

No layout rule at all, and no scanner work: the brackets do what brackets already
do.  The cost is a bracket pair that means nothing but "more lines follow", and a
closing bracket wedged between the last clause and the colon.  It is the safe
answer if W2's scanner work turns out badly.

Eiffel is the language that had this problem and solved it by giving every part of
a routine its own keyword -- `require`, `do`, `ensure`, `end` -- so that no line
break is ambiguous, the body having an introducer of its own.  This language's body
introducer is the colon, which is enough for W2 to end the clause list on; what it
lacks is Eiffel's `end`, which is why the newline rather than the colon is the
question.

**The semantics do not depend on which.**  W1 is legal today, so the checking, the
settling and the folding below can land whole, with clauses on one line, before any
layout question is answered -- and W2 then becomes a change to the parser and the
scanner that no other part of the feature waits on.  That is the recommended
order.

### The scope inside a clause

The parameters, and nothing else the body has.  Every parameter is in scope in
every clause -- not only those to the left, the whole signature having been read
before any clause is checked -- which is what makes `pre(i < ⍴xs)` mean what it
looks like.  A clause may not mention a local of the body: the body has not begun.

A type parameter a clause *settles* is in scope from that clause onwards, which is
the one thing the order of the clauses decides.  The parameters are in scope in a
type-level clause too and cannot be written there, which is not a second scope but
the same one read for types.

### Post-conditions, and what the answer is called

    fn double(x: u8) → u8  post(⎕answer = x + x):

`⎕answer` is what the function answered.  A `⎕` name is how the language already
spells a thing the compiler provides, so this needs no new kind of name -- and it
cannot collide, `⎕` being the compiler's.

A post-condition is checked before every return, which is where the frame is
already given back, so there is one place to put it and it is already walked.

A `post` is always a condition, as above: a requirement does not happen at a point
in the call, and a type settled by what a function answers is what the return type
is for.

### What must be true of the expression

**It must be pure.**  A condition that changed something would be a condition
whose checking changed the program, and a condition that may be compiled out (see
below) would then change the program by being compiled out.  Purity is a property
the compiler already tracks, so this is a check and not a new analysis.  It is
asked of a condition only: a requirement is not evaluated, so what it names need
not be pure.

**A condition must be a `bool`.**  A requirement need not: what it answers is
either named by an arrow or of no interest.

**A requirement's arrow may name a fresh parameter or an existing type.**
`pre(A'⟦I'⟧ → E')` settles `E'`; `pre(T' ⌈ T' → T')` states that the answer is the
already-settled `T'` and fails if it is not.  Which of the two it is, is read off
whether the name is already settled -- which is the same subtlety the other
document's open list already carries, and it is worth stating in the
specification rather than leaving to be inferred.

### Where a condition is checked

**In the callee**, once, at the top -- and at every return for a post-condition.

The reason is the stack walk: a check in the callee that fails already says which
function was called and who called it, so the thing a caller-side check was for is
had for nothing.  One copy of the code, and the message is no worse.

**Folded where the arguments are known.**  A pre-condition on constant arguments
is a constant expression; the folder collapses those, and the inliner puts a
small callee where it was called, after which the folder sees the arguments.

*Corrected by the implementation, and half of this is not there.*  A check the
folder can see **holds** is removed, which is what makes a condition free where
the compiler can see it: that half is implemented and tested.  A check the folder
can see **fails** is left alone and the program stops when it runs.  Reporting it
as a compilation error would need the optimizer to raise a diagnostic about the
language, and it has no channel for one -- so the rule "what the compiler can see
is an error" holds only for what the *checker* settles, which is a literal
falsehood, a question about types, and the operators the checker folds.  The
inliner-driven error is a to-do and not a property of the design.

That is C2's benefit in the cases where nobody needs a solver, and what arrives by
composition is the free half.

### What a failure is

Two more numbers in the table of kinds, because they are two different faults with
two different culprits: **a pre-condition that did not hold is the caller's**, and
**a post-condition that did not hold is the callee's**.  A caller acting on a
status can tell "I was called wrongly" from "the thing I called is broken", which
is precisely the distinction the numbers exist for.

### The typing half is not removable

A build may want conditions compiled out; every language that has them offers it,
and `--conditions=ignore` is how this one does.
The two readings come apart exactly here: **a requirement is not there to remove**,
being what makes the program type-check and lowering to nothing anyway, and **a
condition always is**, being what makes it stop.  That the one notation separates
cleanly into a removable half and a permanent half is the strongest argument for
the unification -- and it separates at the same place the rule does, the level the
expression is over.

### Settling order

A type parameter is settled by an argument, or by a requirement's arrow, read
after the arguments and then clause by clause in the order written.  A parameter
whose settling would depend on itself is refused; so is one nothing settles, which
is rule 4555 extended once more.  An operand settles nothing, so a clause that
mentions a type parameter only as an operand must come after whatever settled it.

With one clause per expression the order is the order on the page, so a reader
working out where `E'` came from reads down the clauses rather than along a list.


What this replaces
------------------

[Constraining generics](constraining-generics.md) proposes a bound written as a
signature: `needs A'⟦I'⟧ → E'`.  With the rule above, the two notations are
**the same notation under a different keyword**:

    needs A'⟦I'⟧ → E'
    pre(A'⟦I'⟧ → E')

which is the strongest argument the unification has: there is nothing to learn
twice.  What `pre` buys over `needs` is that the same keyword also carries the
value-level conditions a signature cannot express -- `pre(i < ⍴xs)` -- and that
a reader of a signature finds both in one place.  What it costs is that `pre` names
two things; the alternative of keeping both keywords is below.

**The grammar is the expression grammar either way.**  The other document needed "a
small grammar of its own -- an operator between operand types, a call, a prefix
operator, an index", which is the expression grammar with types in the operand
positions.  Saying it that way is the whole saving: one grammar, and an operand that
may be a type.

So: layer 1 of that document becomes this clause, layer 2's bundles are the next
section, and layer 3 -- the body may use nothing it did not ask for -- is unchanged
in meaning, with "ask for" now meaning "appears in a requirement".  `needs` does not
survive any of the three: layer 2 applies a bundle inside a `pre` rather than naming
it after a keyword, so **the whole design has two clause keywords, `pre` and
`post`**, and nothing else.


Bundles, and what abbreviates what
----------------------------------

Layer 2 of the other document is the **bundle**: a name for a set of bounds, so
that `number(T')` stands for the four signatures a number wants.  The question is
how it meets the clause above, and the answer begins with an observation that
decides most of it.

### A condition already has its abbreviation, and it is a function

    fn inrange(i: u64, n: u64) → bool:  i < n

    fn take(xs: A', i: I') → E'  pre(inrange(i, ⍴xs)):

That is a condition under the rule as it stands: the expression is over values, so
the call happens and the answer is a `bool`.  **A value-level clause needs no
abbreviation mechanism, because the language has functions.**  Nothing has to be
added, and a condition that is worth a name gets one the way everything else in the
language does.

A requirement cannot do that, and the reason is the whole of why bundles exist:
**there is nothing to call at the type level.**  A type is not a value here, so
there is no function from types to a verdict, and a set of requirements that wants a
name has to be named by something new.  That something is the bundle.

So the two halves of the clause abbreviate in the two ways the language allows, and
the split is the same one the rule already draws:

| Clause kind | What abbreviates it | Why |
|---|---|---|
| a condition, over values | a pure function answering `bool` | values can be passed and calls can happen |
| a requirement, over types | a bundle | types cannot be passed, so nothing can be called |

### A bundle holds expressions, and says nothing else

    bundle number(T'):
        T' + T' → T'
        T' - T' → T'
        T' ⌈ T' → T'
        T' = T' → bool

    bundle iterator(I', E'):  next(I') → E' ?

    bundle indexed(A', I', E'):  A'⟦I'⟧ → E' ; ⍴A' → u64

**No `pre` inside a bundle.**  Everything in a bundle is a requirement -- a
condition there would have no values to be about, and a `post` no call to be after
-- so the keyword would be a word repeated on every line to say the only thing a
line there can say.  What separates the lines is what separates statements
everywhere: a line, a `;`, or braces.  A bundle's body is an ordinary block.

Three simplifications come with that, and they are the argument for it.  **The
layout question does not touch a bundle**: the `:` and the indentation are `fn`'s,
so the newline problem the clause list has does not arise here.  **The level
question does not arise either**: a bundle's parameters are types and its body can
name no value, so every expression in it is over types by construction and there is
no rule to state.  And the body is exactly what layer 2 of the other document
already wrote, so that document's notation survives unchanged.

### A bundle is asked for by applying it

    fn largest(a: T', b: T') → T'
        pre(number(T'))
        pre(a ≠ b):

    fn sum(it: I') → E'  pre(iterator(I', E')):

No third keyword.  `pre(number(T'))` is a clause over types like any other, and it
means what such a clause always means: **that the application can be written.**  If
`number` is a function, that is a `number` accepting a `T'`; if it is a bundle, it is
every expression the bundle holds with `T'` put where its parameter stands.  One
reading, computed two ways according to what the name is of.

That is also the answer to the objection an earlier draft raised against this
notation, that a clause standing for four requirements is not one clause.  It is one:
the requirement is that the application is well-formed, and *checking* it recurses
into the bundle exactly as checking a call recurses into what the callee requires.
Nothing about a clause changes.

**A bundle answers nothing**, and from that one fact two refusals follow:
`pre(number(T') → X')` is refused, there being nothing for the arrow to name, and a
bundle may appear only as the whole of a clause, never as an operand inside one --
`pre(f(number(T')))` has no reading.

**Settling, nesting and concrete arguments** all follow from expansion.  A bundle
settles what its expressions settle, in the order they are written in the bundle, and
where the clause stands in the signature is where that happens -- so
`pre(iterator(I', E'))` settles `E'` exactly as the `next(I') → E' ?` it stands for
would.  A bundle may name another by applying it, which is what a line of a bundle
body holding `ordered(T')` does; a cycle among them is refused.  An argument may be
any type an operand may be, so `pre(number(⌜u8⌝))` is how a test asks whether `u8`
is one.

**A failure names both places.**  Each expression in a bundle has its own span, in
the bundle's definition, and the clause has one in the signature -- so a bound that
is not met reports the line, the bundle, and the function that asked:

    error: 'largest' needs 'T' ⌈ T' → T'' for T' = 'Colour', and there is none
        largest(red, blue)
    note: 'number' asks for it here
        T' ⌈ T' → T'
    note: 'largest' asks for 'number' here
        pre(number(T'))

### Alternatives for the bundle

**A keyword of its own**, `needs number(T')` as a third clause kind beside `pre` and
`post`, which is layer 2's notation in the other document and was this section's
recommendation in an earlier draft.  What it buys is that a reader knows a bundle
from a function without resolving the name: `pre(foo(T'))` requires a function `foo`
or expands a bundle `foo`, and nothing in the line says which.  What it costs is a
keyword for a distinction the rule does not otherwise draw -- the two mean the same
thing, that the application can be written -- so the keyword would be marking how
the compiler answers a question rather than which question was asked.  Turned down
for that, at the user's direction, and worth remembering as the fallback if the
lookup proves to matter: this language is meant to be *generated*, which is the
reason to expect it will not.

**`pre` inside a bundle**, so that a bundle body and a signature's clause list look
alike.  Turned down as a word that can only say one thing, and with it goes the
newline problem: a bundle body is a block, and a block already knows how to hold many
lines.

**A function over types, as D and Zig have.**  D writes `if (isNumeric!T)` and Zig
writes a `comptime` function taking a `type` and answering `bool`; both are the
value-level abbreviation applied one level up, and both make a bundle unnecessary.
This is the alternative with the most weight behind it, and it is turned down for now
rather than refuted: a function over types needs types to be values, which is the
alternative this document already turned down for the reason that type parameters
here are inferred and not passed.  What is worth saying is that it does not conflict:
such a function would still need a primitive for "this expression can be written",
which is exactly the requirement clause, so a later language of type-level
computation would be *built on* this proposal rather than replace it.  Note also how
little separates the two once a bundle is applied rather than named: `pre(number(T'))`
is already the syntax a type-level predicate would use, so adopting one later changes
what `number` is and not how it is asked for.

**A bundle as a nominal type, as Rust and Swift have.**  A trait or a protocol is a
bundle *and* a type a value can be behind, and `where T: Add + Sub` is a list of
them.  The other document turned this down as P2 -- a bound as a name -- and nothing
here revisits it: the point of a bundle being an abbreviation is that nothing
conforms to it, so there is no coherence rule and no orphan rule to write.

### Bundles in other languages

**C++20** is the closest: a concept is exactly a bundle, `requires Number<T>` is
`pre(number(T'))`, and a concept's body is a list of requirements over named stand-in
values.  The differences are that a C++ concept is a `bool`-valued expression, so
concepts compose with `&&` and `||`, and that subsumption gives partial ordering of
overloads.  A bundle composes by naming another bundle and does not order anything,
which is the whole of what it gives up and the whole of what it saves.

**Haskell**'s classes are bundles with two things added: laws, which are not checked,
and coherence, which makes an instance global and unique.  Superclasses are a bundle
naming a bundle.  It is the best evidence that the abbreviation is the useful part
and that the conformance machinery is what costs.

**Rust** is Haskell's design with orphan rules, and **Swift**'s protocols are the same
again with existentials on top.  Both conflate the abbreviation with a type.

**Go**'s interfaces hold method sets and, since generics, type sets -- a bundle whose
members are named methods rather than expressions, which is why an operator cannot be
in one and why `constraints.Ordered` has to be a union of concrete types.

**D** and **Zig** have no bundles and need none: `isNumeric!T` and a `comptime`
predicate over a `type` are functions over types, which is the alternative above.
They are the two languages that answer this question by not having it.

**Odin**'s `where` clauses take compile-time expressions and are not named, so a
constraint repeated over five procedures is written five times -- the state a bundle
exists to avoid.

**Eiffel**'s generic constraint names a class, which is the nominal answer, forty
years before Rust's.

**APL**, **BQN** and **UIUA** have no signatures, and **LISP** and **Scheme** have
macros over forms rather than anything over types; in both families the question does
not arise.

**Wolfram** names a test with `PatternTest` -- `f[x_?NumericQ]` -- which is a
*function* used as a constraint, and so is the value-level abbreviation again, in the
one language on this list where a type-level one would have nothing to mean.

### What the bundle does not decide

- **Whether a bundle may hold a comptime condition** -- something over lifted types
  that the compiler settles, `⌈⌜T'⌝ ≥ 255u64` being one.  It is over values by the rule,
  and a bundle's body is over types, but it is the one kind of value-level expression
  that needs no values -- so the rule may want an exception and does not have one.
- **Whether a bundle may be asked for anywhere but a function** -- on a record, on a
  type definition, on a collection's element type.

*Answered since:* **a bundle may be imported**, with `@[export]`, and its lines are
read with the names its own file can see.  That last is what "a bundle is
substitution" did not already settle: substituting into a line is not the same as
substituting the line into the place that applied it, and it is the difference
between a bundle meaning one thing everywhere and a bundle meaning whatever the
applying file happens to hold.
- **Whether two bundles may overlap**, which is a question only if anything ever
  chooses between them, and nothing here does.

Alternatives considered
-----------------------

**Two keywords.**  `needs(...)` for requirements and `pre(...)` for conditions,
sharing one grammar.  It costs one keyword and buys that `pre` never means
something that is not a condition -- a real readability gain, since
`pre(A'⟦I'⟧ → E')` asserts nothing and calling it a precondition is a small
lie.  The recommendation is the single keyword because the user asked for the
integration and because the operands say plainly which it is; the two-keyword form
is a one-line change to this proposal if the lie grates.

**Marks on a type parameter too.**  Require `pre(somefunc(⌜T'⌝))` rather than
allowing the bare name, so that every type in an expression is marked and there is
no exception to remember.  Turned down because the marks already mean the type
*itself* where an operator takes one -- `⌈⌜u8⌝` is 255 -- so marking the operand form
too would make `⌈⌜T'⌝` ambiguous between the largest value the type holds and the
largest value in one of them.  The apostrophe carries the distinction for free, and
a concrete type, which has no apostrophe, takes the marks.

**A named value for the type, as C++ has.**  `pre(t: T')(somefunc(t))` introduces a
name for a value of the type, which is exactly a requires-expression's parameter
list.  Turned down because under the rule above a named value makes the clause
value-level, which is to say it makes `somefunc(t)` a call: the name is the very
thing that would have to be excepted.  C++ needs it to write `a < b` over two values
of one type; a clause here writes `T' < T'` and means two unrelated values, which is
the honest reading of what a requirement asks.  If one ever needs to say "these two
are the same value", this is the notation to come back to.

**Types as values, as Zig has.**  Make a type an ordinary comptime value and
`somefunc(T')` is an expression with nothing special about it -- no rule about
operands, no refusal for mixing.  It is the most principled alternative and it is
turned down because this language's type parameters are *inferred* rather than
passed: there is no parameter to hold the type and nowhere a value of it would come
from.  The rule above is the restricted form of this that inference allows.

**Caller-side checking.**  The message points at the call without a stack walk,
and the fold is immediate rather than waiting for the inliner.  It costs code at
every call site and it makes a condition part of the calling convention.  With the
walk already emitted, the callee side wins.

**Conditions as ordinary statements.**  `⎕assert(x > 0u8)` as the first line of
the body: no signature change at all, and no way for a *caller* to see the
requirement or for the compiler to check it at the call.  It is what a programmer
does today with no feature at all, and the whole value of the feature is that the
condition is in the signature.

**Proof (C2).**  Named to be turned down for now.  It wants a solver, a language
for loop invariants, and a story for every refusal that is not a bug.  What this
proposal should not do is foreclose it: a condition written in the signature and
required to be pure is exactly what a prover would want to read, so C2 is a later
reading of the same clause -- which is the same shape of argument as layer 3.


What it does not decide
-----------------------

- **Old values.**  *Half of this is now decided.*  `⎕entry(NAME)` is a parameter's
  value at entry, which costs nothing -- a parameter arrives in the entry block
  and SSA keeps it, so nothing is copied.  What is still open is `⎕old` over an
  arbitrary *expression*, which does want a copy the caller cannot see the cost
  of; and a post-condition on an impure function is correspondingly weak whatever
  it can name.
- **Quantifiers.**  `pre(every i: xs⟦i⟧ > 0u8)` is what a condition over a
  collection wants and is a loop in a signature.  It is where this feature stops
  being cheap.
- **Whether a condition may read a variable at the top level.**  A pure function
  may read one, so a condition could; but a condition over a mutable one is a
  condition whose truth changes, which is not what a signature should promise.
  Forbidding it is probably right and is not obviously right.
- **Loop invariants**, which are the same idea inside a body and are the other
  half of what C2 would need.
- **Whether a bound may be satisfied by more than one candidate**, inherited from
  the other document and unchanged.
- **Refinement.**  There is no subtyping, so there is no rule about a weaker
  pre-condition or a stronger post-condition being inherited -- which is a whole
  chapter of Eiffel's design this language does not have to write.


Comparisons
-----------

**Eiffel** invented this and is not on the list but has to be first: `require`,
`ensure`, `old`, class invariants, and the inheritance rules that make
pre-conditions weaken and post-conditions strengthen down a hierarchy.  Its
lesson for this proposal is the size of what subtyping adds, and the good news is
that this language has none.

**C** has `assert`, which is C1 with no signature and no caller checking, and
`static_assert`, which is a compile-time condition with no access to the
arguments.  The two halves this proposal unifies are, in C, two macros that cannot
see each other.

**C++** has both halves separately and neither where this puts them.  Concepts are
C3 and live in the signature; contracts have been proposed for four standards
running and are still not in.  Its requires-expression is this proposal's
requirement form -- `requires(T a, T b) { a < b; }` introduces names and writes
expressions over them -- and the difference is only that a function's parameters
are already named here, so nothing has to be introduced.

**D** is the most instructive comparison in the list, because **D has both and did
not unify them**: `in` and `out` blocks are C1, `out(r)` names the answer as
`⎕answer` does here, and template constraints are a separate `if (...)` over
compile-time predicates.  A D programmer writes the same requirement twice in two
notations when a template's parameter needs both.  That is the state this proposal
is trying not to arrive at.

**Go** has neither, and panics.  A pre-condition is an `if` and a `panic` at the
top of the function, by convention, and nothing a caller can read.

**Rust** has `debug_assert!` and traits, which is again the two halves apart; its
contracts remain a proposal and its `where` clauses are C3 only.  What Rust has
that bears on this is the removability question: `debug_assert!` is compiled out
and `assert!` is not, and the language asks the programmer which every time.  This
language asks the *build* instead, with `--conditions=`, which is g++'s
`-fcontract-evaluation-semantic` and is what a decision per build rather than per
call site looks like.

**Odin** has no contracts and a `when` clause on polymorphic procedures, which is
C3 as a compile-time predicate -- D's answer without D's contracts.

**Zig** has neither as a feature and both as code: `std.debug.assert` is C1, and a
`comptime` check in the body is C3.  Both are in the body, which is where this
proposal says they should not be.

**APL**, **BQN** and **UIUA** have no signatures to put a condition in.  What they
have is the observation that most of what a pre-condition would say is about
*shape*, and that a language whose errors are RANK and LENGTH has decided to check
those at run time and say so plainly.

**LISP** and **Scheme** have `assert` and, in Common Lisp, `declare` and `check-type`
-- conditions in the body, restartable rather than fatal, which is a fourth thing a
condition could mean and one this language cannot want: a program that stops is
the whole of its fault model.

**Python** has `assert`, removable with `-O`, and nothing in the signature; its
type annotations are C3 with no enforcement at all.  It is the cautionary case for
removability: `assert` is so removable that nobody may rely on it, so everybody
writes `if not x: raise` instead and the feature is dead.

**Haskell** puts conditions in types -- a smart constructor, a refinement type
with LiquidHaskell -- and so has no separate notion.  LiquidHaskell is C2 done as
an annotation language over a real compiler, and is the best evidence that C2 can
be bolted on later to a language that wrote its conditions down.

**Wolfram** has patterns and `Condition` -- `f[x_] := ... /; x > 0` -- which is a
pre-condition that selects *which definition applies* rather than one that stops
the program.  It is the fifth meaning, and it belongs to a language where a name
has many definitions.


### A type in an operand, in other languages

Every language with generics has had to answer the same question: how does a
constraint speak about a type when no value of it is in hand?

**Zig** answers it by making types values -- `comptime T: type` is a parameter, so
`somefunc(T)` needs no notation at all -- and is the reason the specification's own
comparison already notes that `std.math.maxInt(T)` takes a type where `@TypeOf(x)`
takes an expression.  It is the design this proposal is a restricted form of.

**C++20** answers it by naming a value that does not exist:
`requires(T t) { somefunc(t); }`.  `t` is never evaluated; it is there so the
operations can be written over something.  `pre(somefunc(T'))` is that with the name
left out -- and leaving it out is what keeps the clause tellable from a condition.

**D** answers it with `T.init` -- a value of the type spelled as an attribute of it
-- inside `is(typeof(somefunc(T.init)))`.  **Ada**'s `T'First` is the same shape of
answer, and the apostrophe is on the other side of the name, which is a coincidence
worth smiling at.

**Rust** and **Haskell** answer it by not using expressions at all: `T: Default` and
`mempty :: a` are declarations that the type provides something, carried by a trait
or a class dictionary.  Haskell's is the cleanest statement of the problem -- a class
method whose type mentions `a` in no argument position is precisely a requirement no
expression over values can express.

**Swift** does the same with protocol `init()` requirements and static members, for
the same reason.

**Go** does not answer it.  A type set constrains what operators work and a method
set constrains what methods exist, and neither can require a way to *make* a value;
`new(T)` gives the zero value and that is the whole of it.  Go is the evidence that
the gap is real and that a language can ship with it.

**Eiffel** answers it in the constraint itself: `[G -> ANY create make end]` says a
`G` can be made, which is the `pre(zero(E') → E')` of the motivating example, forty
years earlier.

**Wolfram** writes the type in the argument position literally -- `f[x_Integer]` --
and is the one language whose notation for this is the same notation, though there
the head test is checked at run time and selects a definition rather than stating a
requirement.

**APL**, **BQN** and **UIUA** have no static types, so the question does not arise;
what corresponds is that a function's conformance is a fact about shape, discovered
when the data arrives.  **LISP** and **Scheme** likewise: a macro can look at a form
and not at a type.

What it would take
------------------

- **Parsing**: any number of clauses after the return type, each `pre(` or `post(`
  holding one expression optionally followed by `→ NAME`, and the same in
  `tree-sitter-pl4g/grammar.js` in the same commit.  `pre` and `post` may be
  contextual keywords: a type cannot be followed by an identifier, so one token of
  lookahead decides.  A type in an operand needs **nothing** here -- the apostrophe
  already continues an identifier in both the lexer and the grammar, and the lifting
  marks are already an expression.  With the clauses on the header's line (W1 above)
  that is all of it; the newline rule (W2) is a second change, to the parser and to
  the external scanner, and is where the estimate is least certain.
- **The front end, for requirements**: resolve each operand as a type, look the
  operation up over types, and bind the arrow's name.  No scope of values is needed
  at all, which is what makes this the half to land first.
- **The front end, for conditions**: a scope holding the parameters, open while the
  clauses are checked and closed before the body -- the one genuinely new thing here,
  and smaller than it sounds because the parameters are already bound by the time the
  body is lowered.
- **The refusals**: a clause whose operands are of both kinds, an arrow on a
  value-level clause, a type-level `post`, a condition that is not a `bool`, an
  impure condition, and a type parameter used as an operand before anything settled
  it.  They are the rule, so they are most of the checking.
- **Settling**: the arrow's name bound from what the operation answers, ordered after
  the arguments; the refusals for a cycle and for a parameter nothing settles.
- **Lowering**: a condition becomes an `AssertInst` at the top of the body, or
  before each return for a `post`; nothing new in the IR, and the two new statuses
  in the table.  A requirement lowers to nothing.
- **The report log**: which conditions were folded away, since a condition the
  compiler proved is a thing a reader would like to know was free.
- **Tests**: a condition that holds, one that fails at run time, one the folder
  refuses while compiling, a `post` over `⎕answer`, a requirement that settles a
  type, one that fails at the call, `pre(zero(E') → E')` where nothing provides a
  `zero`, `pre(somefunc(⌜u8⌝))` for a concrete type, one test for each of the six
  refusals, `⌈⌜T'⌝` and `⌈T'` in two clauses meaning the two different things, a condition
  checked per instantiation of a generic, and for bundles: one that holds, one whose
  line is not satisfied and which reports all three spans, one naming another, a cycle
  refused, an arrow on one refused, and one used as an operand refused.

- **Bundles**, which are a layer of their own and come last: a `bundle` definition,
  whose body is an ordinary block of expressions and so needs no new layout and no
  new grammar beyond the definition itself; a name in a clause that resolves to one,
  which is one arm where a call is resolved; the refusals of a cycle, of an arrow on
  one, and of one used as an operand; and the two notes a failure reports.  Nothing
  in the earlier work has to anticipate it -- applying a bundle is a clause like any
  other, and no keyword is added.

The order to land it in is requirements first -- they need no scope of values, and
they are what makes the other document buildable -- conditions second, which are an
`AssertInst` and a status, and bundles third, which are substitution over what the
first two settled.  The first two share the grammar and nothing else, so either
could come first if the other turns out to want more thought.


What the implementation found
-----------------------------

All three landed together, and four things came out differently from the estimate.

**A requirement is checked by asking the checker.**  Rather than a walk over the
expression deciding what each operation answers for the given types, the clause is
*lowered*: a value of each type operand is bound in a scope of its own, inside a
function nothing will emit, with what it reports thrown away, and what comes back
is either a type or a refusal.  So "can this be written" is answered by the rules
that already decide what may be written.  That is why the notation being an
expression is worth more than the document argued: the saving is not one grammar
instead of two, it is one *checker* instead of two.

**The parameter scope the estimate worried about is not needed for requirements.**
It is needed for conditions, exactly as predicted, and there it is the parameters
already bound by the body.  A requirement binds its own names and reads none of
the function's.

**The three lift readings had to be reached before lowering, not through it.**  A
lift is not a value, so a condition holding one -- `pre(⌜u8⌝ ≠ ⌜u16⌝)` -- cannot be
lowered at all.  The compiler's existing "what does this comptime question come
to" is asked first and the clause is settled there, which also gives the free and
the impossible condition their two outcomes for nothing.

**Eight refusals, not six.**  The two the document did not have are a bundle used
where something with an answer is wanted, which the document states as a rule and
did not count, and a bundle's line written over values, which the document argues
for at length and did not list.
