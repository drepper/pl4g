Conditions on Functions, and Constraints as Conditions
======================================================

A proposal, not a decision.  It answers a question the user put: the language
wants pre- and post-conditions, and a constraint on a generic could be one of
them rather than a thing of its own.  The two forms proposed were

    fn f(a: T') → T'  pre(somefunc(a)):
    fn f(a: T') → E'  pre(somefunc(a) → E'):

and the answer of this document is that they are the same clause with two
readings, that the reading is decidable from the notation, and that the
expression form is **better than the signature form** proposed in
[constraining generics](constraining-generics.md) -- so that document's layer 1
should adopt it.


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
expression *can be written*, and that is a fact about types.  `pre(somefunc(a))`
requires a `somefunc` that accepts a `T'`; `pre(somefunc(a) → E')` requires one
and names what it answers.

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
called "a more complicated parsing of expressions".

What there is nothing of: a condition on a function, a name for what a function
answered, or any expression in a signature that mentions a parameter.


The proposal
------------

### One clause, two readings

    pre(EXPR)              a condition: EXPR must be writable, and must be true
    pre(EXPR → T')         a requirement: EXPR must be writable, and T' is its type

The reading is decided by the notation and nothing else.  With `→ T'` the clause
states no condition -- there is nothing to be true, the expression's *type* being
what was asked about -- and without it the expression must be a `bool` and is
checked.

**One expression per clause, and as many clauses as a function wants.**  A clause
holds one expression; a function that has several things to say writes several
clauses, and `pre` and `post` may be written in any order and any number of times:

    fn take(xs: A', i: I') → E'
        pre(xs⟦i⟧ → E')
        pre(i < ⍴xs):

Read in the order written: indexing an `A'` with an `I'` must be writable and `E'`
is what it answers; and the index must be less than the length, which is a
condition on the values.  One clause form has said what the other document needed
two notations for.

That the clauses are separate rather than a comma-separated list inside one is not
only taste.  **Each clause is its own check with its own span**, so the message a
failure reports points at the clause that failed and not at a list containing it:

    error: 'take' needs 'i < ⍴xs', and it does not hold here
        take(row, 9)
    note: take says so here
        pre(i < ⍴xs)

A list inside one `pre` would have to carry its own positions to say that much, and
would then be a list of clauses with worse punctuation.  Separate clauses also let
a reader put a requirement next to the condition that depends on it, and let the
settling order be read off the page rather than off a rule about commas.

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

    fn take(xs: A', i: I') → E' pre(xs⟦i⟧ → E') pre(i < ⍴xs):

Legal under today's rules exactly as written -- the clauses are after the return
type and before the colon, all on the header's line.  It reads well for two short
clauses and badly for four, which is the whole of the objection to it.

**W2.  A newline before `pre` or `post` continues the header** (recommended).  The
clause lines are indented past the `fn`, and the header ends where it ended before,
at the colon that opens the body:

    fn take(xs: A', i: I') → E'
            pre(xs⟦i⟧ → E')
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

    fn take(xs: A', i: I') → E' ⟨pre(xs⟦i⟧ → E'),
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
the one thing the order of the clauses decides.

### Post-conditions, and what the answer is called

    fn double(x: u8) → u8  post(⎕answer = x + x):

`⎕answer` is what the function answered.  A `⎕` name is how the language already
spells a thing the compiler provides, so this needs no new kind of name -- and it
cannot collide, `⎕` being the compiler's.

A post-condition is checked before every return, which is where the frame is
already given back, so there is one place to put it and it is already walked.

`post(EXPR → T')` is **not** proposed: a type settled by what a function answers
is what the return type is for.  A `post` with an arrow should be refused rather
than given a meaning.

### What must be true of the expression

**It must be pure.**  A condition that changed something would be a condition
whose checking changed the program, and a condition that may be compiled out (see
below) would then change the program by being compiled out.  Purity is a property
the compiler already tracks, so this is a check and not a new analysis.

**A condition must be a `bool`.**  A requirement need not: its type is the point.

**A requirement's arrow may name a fresh parameter or an existing type.**
`pre(xs⟦i⟧ → E')` settles `E'`; `pre(a ⌈ b → T')` states that the answer is the
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
is a constant expression; the folder already collapses those, and the inliner
already puts a small callee where it was called, after which the folder sees the
arguments.  So `f(0u8)` with `pre(x > 0u8)` becomes a call to a function whose
first act is a check that is known to fail -- which the compiler can then report
*as a compilation error*, by the rule the language already applies to arithmetic.

That is C2's benefit in the cases where nobody needs a solver, and it arrives by
composition rather than by design.

### What a failure is

Two more numbers in the table of kinds, because they are two different faults with
two different culprits: **a pre-condition that did not hold is the caller's**, and
**a post-condition that did not hold is the callee's**.  A caller acting on a
status can tell "I was called wrongly" from "the thing I called is broken", which
is precisely the distinction the numbers exist for.

### The typing half is not removable

A build may want conditions compiled out; every language that has them offers it.
The two readings come apart exactly here: **a requirement is never removable**,
being what makes the program type-check, and **a condition always is**, being what
makes it stop.  That the one notation separates cleanly into a removable half and
a permanent half is the strongest argument for the unification.

### Settling order

A type parameter is settled by an argument, or by a requirement's arrow, read
after the arguments and then clause by clause in the order written.  A parameter
whose settling would depend on itself is refused; so is one nothing settles, which
is rule 4555 extended once more.

With one clause per expression the order is the order on the page, so a reader
working out where `E'` came from reads down the clauses rather than along a list.


What this replaces
------------------

[Constraining generics](constraining-generics.md) proposes a bound written as a
signature: `needs A'⟦I'⟧ → E'`.  The expression form is better, and the reasons
are worth being explicit about since it was the other document's recommendation.

**It reuses a grammar instead of inventing one.**  The signature form needed "a
small grammar of its own -- an operator between operand types, a call, a prefix
operator, an index".  The expression form needs the expression grammar, which
exists, and a scope that holds the parameters, which does not but is one thing
rather than a grammar.

**Positions come for free.**  `needs D' ⸨K'⸩ → u64 ?` had to write types into
positions; `pre(d⸨k⸩ → V' ?)` writes the parameters, and where they stand is where
they stand.

**It spans both levels.**  A signature over types can only say what is
type-correct.  `pre(i < ⍴xs)` is a requirement no bound can express, and it is the
one a reader of `take` most wants to see.

**And bundles still work**, now holding expressions over named parameters:

    bundle iterator(I', E'):  (it: &mut I') pre(next(it) → E' ?)

which is, to within punctuation, a C++20 concept -- a requires-expression
introduces the names and writes the operations over them.  That the two designs
met at the same place from different directions is the best evidence available
that the place is right.

So: layer 1 of that document becomes this clause, layer 2's bundles hold these
expressions, and layer 3 -- the body may use nothing it did not ask for -- is
unchanged in meaning, with "ask for" now meaning "appears in a requirement".


Alternatives considered
-----------------------

**Two keywords.**  `needs(...)` for requirements and `pre(...)` for conditions,
sharing one grammar and one scope.  It costs one keyword and buys that `pre` never
means something that is not a condition -- which is a real readability gain, since
`pre(xs⟦i⟧ → E')` asserts nothing and calling it a precondition is a small lie.
The recommendation is the single keyword because the user asked for the
integration and because the arrow is a visible marker; the two-keyword form is a
one-line change to this proposal if the lie grates.

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

- **Old values.**  `post(count = ⎕old(count) + 1u8)` wants the state at entry,
  which is a copy the caller cannot see the cost of.  Nothing here proposes it,
  and a post-condition on an impure function is correspondingly weak.
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
and `assert!` is not, and the language asks the programmer which every time.

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


What it would take
------------------

- **Parsing**: any number of clauses after the return type, each `pre(` or `post(`
  holding one expression optionally followed by `→ NAME`, and the same in
  `tree-sitter-pl4g/grammar.js` in the same commit.  `pre` and `post` may be
  contextual keywords: a type cannot be followed by an identifier, so one token of
  lookahead decides.  With the clauses on the header's line (W1 above) that is all
  of it; the newline rule (W2) is a second change, to the parser and to the external
  scanner, and is where the estimate is least certain.
- **The front end**: a scope holding the parameters, open while the clauses are
  checked and closed before the body -- which is the one genuinely new thing here,
  and is smaller than it sounds because the parameters are already bound by the
  time the body is lowered.
- **Settling**: the arrow's name bound from the expression's type, ordered after
  the arguments; the refusals for a cycle and for a parameter nothing settles.
- **Lowering**: a condition becomes an `AssertInst` at the top of the body, or
  before each return for a `post`; nothing new in the IR, and the two new statuses
  in the table.
- **Purity**: a check that the expression is pure, which is a question already
  asked of every call.
- **The report log**: which conditions were folded away, since a condition the
  compiler proved is a thing a reader would like to know was free.
- **Tests**: a condition that holds, one that fails at run time, one the folder
  refuses while compiling, a requirement that settles a type, a requirement that
  fails at the call, a `post` over `⎕answer`, an impure condition refused, a
  `post` with an arrow refused, and the interaction with a generic -- a condition
  checked per instantiation.

The order to land it in is requirements first (C3, which makes the other document
buildable) and conditions second (C1, which is an `AssertInst` and a status), and
the two halves share nothing but the grammar -- so either could come first if the
other turns out to want more thought.
