Macros and Reflection
=====================

**The rules form is decided and implemented**, on 2026-09-30; see
[decisions.md](decisions.md) for the entry and [the specification](spec.md) for what
the language now says.  The function form is not, and waits on the interpreter this
document's estimate names.  What follows is the reasoning, kept as it was written
except where the implementation found it wrong, which is marked where it happened.

It takes the design a related language settled on --
two forms of macro, a parse tree handed over rather than a value, hygiene by
renaming, expansion between parsing and checking -- and asks what it becomes in
*this* language.  The answer is smaller than the original in one place and larger
in another, and both differences come from what pl4g already has.

The short version: **the design transfers whole, and three of its four notations
are already here under other names.**  What it needs new is a keyword, a type, five
compiler-provided questions and a place in the pipeline.


What the language already has
-----------------------------

Measured.  Each of these was compiled or read out of the compiler while this was
written.

**A mark meaning "handed over as written rather than as what it evaluates to".**
That sentence is the definition of a macro, and it is also the definition of the
lifting marks:

    ⌜u32⌝         the type and not a value of it
    ⌜a⌝           the name and not what it stands for

The specification's own words for `⌜x⌝` are that it "lifts what is written between
the brackets out of the program and into the compiler".  A macro is a function whose
arguments are lifted.  Three places read a lift today and a fourth was added with
the conditions; a lift anywhere else is refused (4514).

**A layout that already tells one expression from a run of statements.**  A block
written after a `:` on one line holds one thing and one written indented under it
holds several, and the scanner settles which.  That is exactly the distinction a
quote needs between an expression and a block.

**`comptime`, and how little it may ask.**  `comptime if` and `comptime foreach` are
settled by the compiler and leave nothing behind, and what a `comptime if` may ask
is whether two types are the one type.  So the *word* is taken and the thing it
would need is not there: nothing in the compiler runs a loop and builds a value at
compile time, which is where most of the estimate below goes.

**A pipeline with the stage boundary already exposed.**  `--emit=tokens`, `ast`,
`ir`, `asm`, `elf`.  Expansion goes between `ast` and the checker, which is a stage
the driver already stops at.

**`@[…]` for what the compiler is told about a definition**, and `@[listable]`
among them -- which is the annotation the original's recursion example leans on.

**Operators a program defines**, including a pair of brackets, and **`#` as an
operator glyph**, so no identifier can hold one.

**Bundles, whose lines are read where the bundle was written.**  That decision, made
for bundles, answers the question the original leaves open about macros.  It is the
most useful thing pl4g brings to this design and it is under *Hygiene* below.

What there is nothing of: a macro, a type for a piece of the program, a way to ask
what an expression is made of, and `comptime` before `fn`.


What transfers unchanged
------------------------

Most of it, and it is worth saying which so that the adjustments below are read as
the exceptions they are.

**Two forms.**  A list of rewrite rules and a function over the program's text.
Every mature system ends up with both -- Scheme's `syntax-rules` and `syntax-case`,
Rust's `macro_rules!` and procedural macros -- and the reason is the one the
original gives: a rule matches a *shape*, and "π is among the factors" is not a
shape, since a product nests arbitrarily deep.

**Expansion after parsing and before any check.**  What the checker sees has no
macro left in it, so a macro cannot make a program that would not otherwise be
legal, and every diagnostic is about what the macro wrote.  It cannot run before
parsing: a macro is handed a parse tree and there is none yet.  The original notes
that C has to do it earlier because its grammar is not context-free; pl4g's is,
which is a property this project has paid for deliberately -- the tree-sitter
grammar exists and a test holds it to agreeing with the compiler on every program.

**An argument is expanded after the invocation that holds it**, so a macro sees what
the caller wrote, including a nested invocation; what comes out is expanded in turn,
and a macro reaching itself is stopped after a fixed number of rewrites.

**Every piece keeps the position it was written at.**  What came from the caller
points at the caller and what came from the macro points into the macro.  pl4g's
`Span` is a byte range into a `SourceManager` that already holds several files, so
a span pointing into either is what it does anyway -- this needs no new machinery,
only the discipline of not dropping spans while rewriting.

**Hygiene by renaming, with `#`.**  A name a macro binds is renamed with a `#` and a
number; `#` is an operator glyph in pl4g exactly as in the original, so no
identifier can hold one and the renamed name collides with nothing a program writes.

**`$` inside a quote** puts something into the tree: a piece of program as itself, a
number or a string as what a program would have written to mean it.  `$` is unused
in pl4g and is not in the symbol categories, so it is not a glyph a program could
define as an operator either.

**Reflection is the same mechanism seen from the other end**, and questions about
*types* are answered after expansion because macros write the program and the types
are asked of what they wrote.


The adjustments, and why each is forced
---------------------------------------

### 1.  The invocation is `f⌜…⌝`, because `⟦⟧` is taken twice over

The original writes `sin⟦2.0 × std.π⟧`.  In pl4g `xs⟦i⟧` is an element of an
array, `⟦1u8, 2u8⟧` is an array, and `⟦⟧` is a bracket pair a program may define for a
type of its own.  So the mark is unavailable three times.

What is available is better than a free pair:

    sin⌜2.0 × ⎕π⌝
    swap⌜x, y⌝

**The lifting marks already mean what a macro invocation means.**  The original's
first paragraph says a function receives `6.283185307179586` and a macro receives
`2.0 × std.π`; the specification's paragraph on `⌜⌝` says it lifts what is written out
of the program and into the compiler.  Those are one sentence.  Adopting a second
notation for it would be adopting a second word for one idea, which is the thing
this language most consistently refuses.

What it costs: `⌜⌝` holds one type or one name today, and this makes it hold a
comma-separated list of expressions -- and 4514's "three places read a lift" grows
by a place that is much larger than the other three.  The marks become genuinely
overloaded: a type, a reference, and a piece of the program.  That is the honest
price and it is argued for below rather than waved past.

### 2.  There is no separate quote, because a lift is one

The original has `⟬…⟭` for a piece of the program held rather than run.  pl4g's
`⌜…⌝` is that already, so the same marks serve:

    ⌜⎕sinpi(1.0)⌝                    an expression
    ⌜
        let t: i64 = 1                  a run of statements
        ⎕write(out, t)
    ⌝

On one line it holds one expression; with its contents indented under the opening
mark it holds statements.  The layout scanner already draws that line for every
block in the language, so this is the rule the language has rather than a rule for
quotes.

**And then the invocation is not a notation at all.**  `f⌜x⌝` is a name applied to a
quote, which is what it *is*: the brackets are around the arguments because the
arguments are what is unusual, which is the original's own argument for putting them
there -- arrived at here without choosing anything.

### 3.  A reference is a lift too, because `※` is the comment glyph

The original writes `※×` for the operator itself, `※std.π` for the constant.  `※` begins a
comment in pl4g, so it is unavailable -- and unnecessary:

    ⌜×⌝          the operator
    ⌜⎕π⌝         the constant

`⌜a⌝` is already "the name and not what it stands for", which is the whole of what
the original's `※` says.  So three notations in the original -- invoke, quote, refer --
are one notation here, and the difference between them is what stands inside: a
name, an operator, an expression, or a run of statements.

That is the single largest adjustment and the one that makes the design *fit* rather
than merely be transplanted.  It is also where the risk is: one notation meaning
four things is harder to read than four meaning one each, and the argument that it
is right is that the four things are the same thing -- a piece of the program,
lifted.

### 4.  One keyword, and the shape says which form

The original heads the rule form with `@macro_rules` and the function form with
`macro`.  pl4g's annotations are `@[name]` and say what the compiler is *told about*
a definition; which kind of definition it is, is not that.  So both forms are
headed by one keyword and told apart the way this language already tells things
apart -- by their shape:

    macro sin:                              a name and a body of rules
        ⌜$a × ⎕π⌝  → ⌜⎕sinpi($a)⌝
        ⌜⎕π × $a⌝  → ⌜⎕sinpi($a)⌝
        ⌜$x⌝         → ⌜⎕sin($x)⌝

    macro sin(e: syntax) → syntax:             a parameter list and a body
        …

**A parameter list is the discriminator**, and the language already uses exactly
that device twice: a bundle's body is bare lines where a function's is statements,
and an operator's arity says whether its prefix or its infix reading is being
defined.  A reader at a definition still tells the two apart at a glance, which is
what the original wanted two keywords for.

The rules form is shaped like a `bundle`, and that is not a coincidence: both are a
name, some parameters and a body of bare lines that are not statements.

### 5.  The questions are `⎕` names, because there are no methods

The original writes `e.head()`, `e.kind()`, `e.arguments()`, `e.name()` and
`std.syntax.funcall(…)`.  pl4g has no way to attach code to a type -- that is
[attaching code to objects](attaching-code.md), still open -- and no sub-modules
under `std`.  So they are what pl4g spells everything the compiler provides with:

| Written | Answers |
|---|---|
| `⎕kind(e)` | what it is, as an enumeration the compiler provides |
| `⎕name(e)` | the name it reads, as `str ?` |
| `⎕head(e)` | what it is made by, as `syntax` |
| `⎕parts(e)` | what it applies its head to, as `syntax⟦⟧` |
| `⎕apply(head, parts)` | the piece of program that applies one to the others |

`⎕kind` answers an **enumeration** rather than a string, which is the one place this
improves on the original: pl4g has `enum`, a `match` over one is checked for
covering every value, and a misspelled kind is then an error where a misspelled
string is a branch that never runs.

`⎕parts` rather than `⎕arguments`, because `parts_of` is already this compiler's word
for what something is made of.

**Every one of them becomes a method the day attaching code lands**, with no change
of meaning: `⎕head(e)` and `e.head()` would be the same question.  So this adjustment
costs nothing later and unblocks now.

### 6.  `syntax` is a type the compiler provides

One word wide, a nominal type like `arena` in the allocator plan, and what `⌜…⌝`
answers.  `=` between two of them asks whether the same thing is written in both,
which the compiler provides as it provides `=` on an enumeration.

An array of them is `syntax⟦⟧`, which is pl4g's array notation and needs nothing.

### 7.  `comptime fn`

The original's `comptime fn` moves a function to the other side of the line macros
run on.  pl4g has `comptime` before `if` and `foreach` and not before `fn`, so the
keyword is there and the position is new.  The meaning transfers unchanged: it is
installed before expansion for the macros to call and again in the ordinary way for
the program, and nothing about it is special at the call.

`@[listable]` on one is what makes the original's `factors` example work, and pl4g
has that attribute already.


Hygiene, where pl4g has the answer the original left open
---------------------------------------------------------

The original settles half of hygiene -- a name a macro *binds* is renamed -- and
says of the other half:

> The other half of hygiene -- that a name a macro reads resolves where the macro
> was written rather than where it was invoked -- is not settled yet.

**pl4g settled it, for bundles, and the argument is the same one.**  A bundle's
lines are read with the names its own file can see, so a bundle may require an
operation the applying file cannot name, and a function of the same name in the
applying file does not change what the bundle asks for.  The reason given there was
that a requirement is part of what the defining file said, and that substituting
*into* a line is not the same as substituting the line into the place that applied
it.

Every word of that is true of a macro.  So:

**A name a macro reads resolves where the macro was written.**  A macro may write a
call to something its own file can see and the caller cannot; a caller with a
function of the same name does not change what the macro means.  A macro means one
thing everywhere it is invoked.

That is not a new decision, it is the decision pl4g already made, applied to the
other thing it is about -- which is the strongest evidence available that the answer
is right.  The implementation is the same one, too: each macro keeps the tables of
the file that wrote it and they are in force while its body is expanded.


What a macro is for here
------------------------

The original's motivating example is trigonometry: `sin⌜2.0 × π⌝` should become
`sinpi(1.0)`, and a `comptime` function cannot do it because by the time one is
entered the multiplication has been rounded and the information is gone.  It
transfers, and pl4g has no floating-point library yet, so the names in it are
hypothetical here.

Two motivations are pl4g's own and are worth stating, because they are what would
make this worth building here rather than worth having in general.

**A check that names what it checked.**  A condition that fails reports the clause
by the words it was written in -- the compiler reads them back out of the source by
its span.  A macro would let a *program* do that for anything:

    macro checked(c: syntax) → syntax:
        ⌜
            unless $c:
                ⎕fail($(⎕written(c)))
        ⌝

    checked⌜i < ⍴xs⌝

The message names the expression because the macro was handed the expression.  A
function could not: it receives a truth value, and what was asked is gone.

**Nine operators from one line.**  The operator design turned down D's
`opBinary!"+"` -- one definition over every binary operator -- on the ground that it
is a layer over the notation rather than an alternative to it, and noted that *a
language for generators will want it the day something wants nine operators at
once*.  A macro is that layer:

    macro elementwise(ty: syntax, ops: syntax) → syntax:
        … one `fn `op`(a: ty, b: ty) → ty` per operator in `ops` …

    elementwise⌜Vec3, ⟦⌜+⌝, ⌜-⌝, ⌜×⌝, ⌜⊞⌝⟧⌝

**And the language is written by generators.**  A macro is a thing the *program*
writes rather than a thing the generator writes, so the case for it in a language
meant to be machine-generated is weaker than in one meant to be typed -- a generator
can emit the nine definitions.  That is an argument against the whole feature and it
belongs here rather than in a footnote: what answers it is that a generator emits
programs a *person* then reads and debugs, and a macro invocation is shorter to read
than what it stands for.  Whether that is worth the price is the user's call, and it
is the first question this document asks.


What it does not decide
-----------------------

- **Whether a macro may write a definition**, as the `elementwise` example does.  It
  is what the feature is most worth and it is also what makes expansion have to run
  before the definitions are collected rather than merely before they are checked.
  The original allows it; nothing here has worked out what it does to the module
  system.
- **Following a reference to what it names.**  Asking a `syntax` that reads `foo`
  for the definition of `foo`.  Out of reach in the original for the same reason it
  would be here: expansion runs before the definitions are installed.
- **Whether `⌜⌝` can carry this much.**  The one real risk of adjustment 3, and the
  alternative is a free bracket pair -- `⟬⟭` is unused -- at the cost of a second
  notation for one idea and of a pair a program can no longer define.
- **What `⎕kind` answers for a clause, a bundle or an operator definition**, now that
  the language has all three.
- **Whether a macro may be exported**, which bundles answered with `@[export]` and
  which would presumably answer the same way.


Alternatives considered
-----------------------

**A free bracket pair for the invocation**, `f⟬…⟭`.  Nearest to the original,
and turned down because the lifting marks already say what it would say.  Kept as
the answer if `⌜⌝` proves to carry too much.

**A lifted argument in an ordinary call**, `sin(⌜2.0 × ⎕π⌝)`.  It needs *no new
grammar at all* -- a call whose argument is a lift parses today -- and it is the
cheapest thing on this page.  Turned down because it marks the argument twice and
because a macro and a function would then look alike at the call, which is the one
thing the original is most careful about.  Worth revisiting if the estimate for the
invocation grammar comes out badly.

**`@[macro]` on a `comptime fn`.**  No new keyword: a function marked as expanding
rather than running.  Turned down because an attribute says what the compiler is
told about a definition and this is what kind of definition it is -- the same reason
the rules form is not `@[macro_rules]`.

**`comptime fn` instead of macros**, which is Zig's answer.  Turned down for the
original's reason and it is worth repeating because pl4g has `comptime`: a
`comptime fn` receives a *value*, so by the time it is entered `2.0 × π` has been
rounded and what the macro needed is gone.

**Text or token substitution.**  Nothing to take apart and nothing to be hygienic
about; and this language's tokens are glyphs, so a token run is no more structured
than a string.

**Reader macros**, as Common Lisp has.  A program that changes how text is scanned
cannot be parsed without being run, which would cost this project the tree-sitter
grammar and the test that holds it to the compiler -- a price paid deliberately and
not to be given back.


Comparisons
-----------

The original's table, with pl4g's row filled in:

| Language | A macro sees | Written as | Invoked as | Hygienic |
|---|---|---|---|---|
| **C** | characters | `#define` | `f(x)`, indistinguishable | no |
| **Rust** | tokens | `macro_rules!`, proc macros | `f!(x)` | mostly |
| **Scheme** | a parse tree | `syntax-rules`, `syntax-case` | `(f x)`, indistinguishable | yes |
| **Common Lisp** | a parse tree | `defmacro` | `(f x)`, indistinguishable | no, `gensym` by hand |
| **Julia** | a parse tree | `macro` | `@f x` | mostly |
| **Nim** | a parse tree | `macro` | `f(x)` | yes |
| **Wolfram** | an expression | rules with `:>` | `f[x]` | no |
| **Zig** | nothing | `comptime fn` | `f(x)` | n/a |
| **C++26** | a typed tree | `consteval` and `^^` | a call | n/a |
| **pl4g** | a parse tree | `macro`, rules or a function | `f⌜x⌝` | yes |

**What pl4g's row says that none of the others does** is that the mark around the
arguments is the language's existing mark for "not evaluated".  Rust and Julia mark
the name; Lisp, Scheme and Nim mark nothing; the original marks the arguments with a
pair chosen for the purpose.  Here the arguments are marked with the mark that
already means it, so a reader who knows `⌜u8⌝` knows what `f⌜x⌝` does to `x`.

**C++26** is worth its own line because its answer to reflection is the one this
document's `⌜⌝` already follows: `^^name` lifts a name into a reflection for the
same reason `⌜name⌝` does, which the specification's comparison on lifting already
records.  Its macros -- token injection -- run after checking, which is why the
original places it under *worth having later, alongside macros rather than instead
of them*.


What it would take
------------------

- **The lexer and the grammar**: `⌜…⌝` holding a comma-separated list of
  expressions, or a run of statements when indented, and `$` before a name or a
  parenthesised expression inside one.  The grammar change is in
  `tree-sitter-pl4g/grammar.js` in the same commit, and the statement form is where
  it is least certain: the external scanner decides indentation, and a quote that
  opens a block is a shape it has not seen.
- **The parser**: `macro NAME:` with rule lines, `macro NAME(…) → syntax:` with a body,
  and `comptime` before `fn`.  One AST node for a rule and one for a macro.
- **A stage of its own**, between `parse` and `check`, with `--emit=expanded` beside
  the five stages the driver already stops at.  It is the first pass over the AST
  the compiler has, everything today going straight from parse to check.
- **`syntax`**: a nominal type, the five `⎕` questions, the kind enumeration, and `=`.
  The tree the parser makes is what `syntax` holds, so nothing is converted.
- **The rules matcher**: structural matching with holes, a hole written twice
  matching only where the two are alike, rules tried in order, and the refusal where
  none matches.
- **The evaluator**: the function form runs at expansion, which means a `comptime`
  interpreter over the AST that can call `comptime fn`.  **This is the large piece**
  -- the compiler has a constant folder and a `comptime if`, and neither is an
  interpreter that can run a loop and build a value.
- **Hygiene**: renaming what a macro binds, and the tables of the defining file kept
  on each macro, which is the bundle machinery again.
- **Tests**: a rule that matches and one that does not, a hole written twice, rules
  tried in order, a macro writing statements, a macro writing another macro, the
  recursion limit, hygiene over a name the caller also uses, a name the macro reads
  that the caller has a different one of, positions pointing at the caller and at
  the macro, and each refusal.

The order that gets something useful soonest is the rules form alone: it needs the
notation, the matcher and the stage, and none of the interpreter.  That is also the
half that answers the "nine operators" motivation, since writing nine definitions
from a list is substitution rather than computation.  The function form follows the
`comptime` interpreter, which is a project of its own and has other things waiting
on it.


What the implementation found
-----------------------------

The rules form landed in that order.  Four things came out differently from the
estimate, and one of them is a hole in this document.

**A template that assigns to a hole needed a node of its own.**  `$a ← $b` is the
middle of the swap example, which is this document's own illustration of hygiene --
and an assignment in pl4g says on its face what kind of place it writes, there being
a separate statement for a name, a field, an entry, an element and what a reference
names.  A hole is not known to be any of them until it is filled.  So the parser
builds one node for "an assignment to whatever this hole turns out to be" and
expansion turns it into whichever of the five the filled target is; a hole filled
with something nothing can be assigned to is refused there, pointing at the argument
rather than at the template.  The document did not notice that its own example needed
this.

**A macro of no arguments is worth writing**, and `⌜⌝` had to be allowed to hold
nothing for it.  The document assumed a pattern holds at least one expression.

**The refusal of the function form belongs to the parser**, and so to the syntax
block of the catalog rather than the compile-time one.  The grammar does not describe
a parameter list on a macro -- the language does not have the form -- so the grammar
refuses one too, and a test that holds the two to agreeing is what noticed: a
refusal numbered outside 2000-3999 would have said the program parsed.

**A failed expansion has to stop the compilation.**  Otherwise the checker is handed
the program the macro could not write and reports on text nobody wrote -- five
messages for one mistake, which is what the first run of the refusals showed.  It
counts the errors expansion itself added rather than asking whether anything failed,
because a *parse* error must not stop the checker: the parser recovers and the
checker has things to say about what it did parse, which is the behaviour every other
stage already has.  A test of the existing suite is what caught that distinction.

**And the positions needed nothing.**  The document says every piece keeps the
position it was written at, and it does, because substituting trees keeps the spans
the parser gave them.  Both directions are tested: an error in the template points
into the macro and an error in an argument points at the invocation.
