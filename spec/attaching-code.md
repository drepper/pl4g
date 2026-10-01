Attaching Code to Objects
=========================

**Design B is decided and implemented**, on 2026-10-01; see
[decisions.md](decisions.md) for the entry and [the specification](spec.md) for what the
language now says.  Two things B deliberately left out came in with it, because moving
`std.print` into `std.Io` needed them: a type from another module, and therefore a
three-part path.  What stayed out is writing `T.name` for a type the file does not define.
The Operators section below is already marked as settled another way.

A proposal, not a decision.  It is now the second half of a pair:
[constraining generics](constraining-generics.md) comes first, and what that one
calls a bound over a function's name is what this one calls a protocol -- so read
that first, and read the last section of it for what it does to this.

What this is for is the question the language cannot answer today: **how does a program say that a piece of code belongs to a type?**
Nothing decided here is settled until the user says so; what is here is the
ground, four designs, a recommendation, and what each of them costs.


What wants it
-------------

Four things, in the order they asked.

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

**A bundle that requires an operator** -- which arrived after this document was
written, and is the sharpest of the four because it is a hole in something that
already works.  A requirement may ask for `T' ⊞ T' → T'`, and the bundle
`number(T')` asks for four such things; but **only a built-in type can ever
satisfy one**, because an operator on a type a program defined is an operator the
language has no way to provide.  Measured, both of these:

    let a: Point = Point(.x ← 1u8, .y ← 2u8)
    if a = b: …
    error: '=' compares numbers and truth values, not 'Point' [4207]

    let s: mut ⸨Point⸩ = ⸨Point(.x ← 1u8, .y ← 2u8)⸩
    error: 'Point' cannot be a key: it is hashed and compared, and this type
           answers neither exactly [4429]

The second message is worth reading twice: *answers neither*.  The language
already frames a key as a type that **answers** a hash and a comparison, which is
a type with code attached to it and nothing else.  So this document is what stands
between a record and a dictionary key, and the operator question is most of what
stands there.


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


What the step operators change
------------------------------

Asked after the operators landed: the language has `⇧` and `⇩` for the next and
the previous of a walk, and an operator is now a function a program may write.  So
what does the iterator -- the first and largest of the four things this document is
for -- look like now?

### The example, and it compiles today

    type Walk = at : u8 ; last : u8

    fn `⇧`(it: Walk) → Walk:
        Walk(.at ← it.at ⊞ 1u8, .last ← it.last)

    fn `⇩`(it: Walk) → Walk:
        Walk(.at ← it.at ⊟ 1u8, .last ← it.last)

    ⎕narrow((⇩⇧⇧w).at, ⌜u6⌝) ?? 1u6            ※ two forward, one back: 2

Measured: that compiles and runs.  **Part of what this document is for is already
available and the document did not say so** -- which is the first thing the
question changes.

### The protocol is four operations, and the cursor is where to read them off

The compiler's own cursor is the specification of what an iterator is in this
language, so what a program's own iterator has to be able to do is what a cursor
does.  Four things, and each was measured against the compiler as it stands:

| What | The cursor spells it | Can a program define it? |
|---|---|---|
| on to the next, or back one | `⇧it`, `⇩it` | **Yes**, today |
| what the walk is at | `it⌖` | No: `⌖` is reserved (3052) |
| take it out and move on | `†it` | No: `†` is punctuation, not a symbol (3052) |
| whether the walk is over | the cursor where a truth is wanted | No: there is no operator for it (4437) |

**That reframes the whole document.**  "A program writes an iterator" is not one
decision about attaching code; it is four questions, three of which are about
whether a particular glyph or reading may be a program's.  None of the four
designs above asks any of them.

### E.  The protocol is operators

The three glyphs and the truth reading, and no name anywhere:

    fn `⇧`(it: Walk) → Walk:   …        ※ works now
    fn `⌖`(it: Walk) → u8:      …        ※ wants `⌖` to be nameable
    fn `†`(it: Walk) → 〈u8, Walk〉:  …   ※ wants `†` to be nameable

    foreach x := w:  …                 ※ finds them

*What it buys.*  **Uniformity with the compiler's own cursor.**  `foreach` over a
cursor is refused today (4438) although a cursor is the compiler's own iterator,
and this document's estimate already names that as the piece worth doing first.
Under E the two are *one* rule -- a thing with the step operators is walkable,
whoever wrote it -- where under B they are two, since a cursor has no `T.next` and
never will.

And it needs **none of design B**.  The central decision of this document is not a
prerequisite for its central motivation, which is the sharpest thing the question
changes.

*What it costs.*  Three decisions, each smaller than B and each on its own:

- **`⌖` is reserved**, on the ground that dereference is about references.  That
  now looks premature: `it⌖` on a cursor is not a reference at all, it is what the
  walk is at, and the specification says so in those words.  It is the smallest of
  the three and probably right to undo.
- **`†` is not a symbol**, so the Unicode rule keeps it out.  It is a prefix operator
  of the language all the same, so adding it to the list of glyphs that qualify
  because the language already uses them is one entry -- the same clause `-`, `^`
  and `⌈` are in.
- **The truth reading has no operator at all.**  "Whether the walk is over" is a
  cursor standing where a `bool` is wanted, which is a *conversion* and not an
  operator, and nothing in the language lets a program define one.  This is the one
  question of the four that is genuinely about attaching code, and the answers are
  a glyph for it (`∽it`, say), a named protocol (design B), or a bundle (design F).

### F.  The protocol is a bundle

Not available when this document was written, because bundles were not:

    bundle walkable(I', E'):
        ⇧I' → I'
        ⌖I' → E'

    fn sum(it: I') → E' pre(walkable(I', E')):  …

*What it buys.*  **The protocol is written down in the language** rather than being
a rule in the compiler.  `foreach` could *require* the bundle, so a type that fails
to be walkable is told which line of the bundle it failed and where -- which is
what a bound buys over a lookup, and it is the whole argument of
[constraining generics](constraining-generics.md) applied here.  A program may also
write its own bundles over the same operators, which a compiler-known protocol
cannot be.

*What it costs.*  Nothing new: bundles are in, requirements are in, and the
operators are the ones E needs.  F is not an alternative to E but the thing that
*says* what E means -- so the two go together, and neither needs design B.

### H.  The protocol is a function

Worth naming because a language shipped it while this document sat: **Go 1.23**
walks anything that is a function taking a `yield`.  Here that would be

    foreach x := λ …

and nothing is attached to any type at all.  It is turned down rather than
explored, and the reason is the one this language keeps coming back to: a lambda is
two words and a call through a pointer, where a cursor is a value and a step is an
addition.  A generated program that walks a list a million times should not pay for
an indirect call each time, and that is exactly the case this language is for.  It
is the right answer for a language with closures and a garbage collector, and the
wrong one here.

### How the evaluation changes

**B is still right for what it was right for, and is no longer the first step.**

The four things that want this document split cleanly now.  `std.Build`'s
`add_executable` is a *named* operation on a value, which is B and nothing else.
"Everything a reader would call a method" is B too.  A bundle requiring an operator
is answered already -- operators landed.  And the **iterator**, which is the first
and largest, is answered by E and F together, neither of which needs B.

So the ordering in *What it would take* is wrong in its first line.  The smallest
step towards a program writing an iterator is not design B; it is, in order:

1. `foreach` over a cursor, which needs no decision from anywhere and which the
   estimate already calls out.
2. **`⌖` nameable**, which is one entry removed from a list and which makes E's second
   row work.
3. A way to ask **whether a walk is over** -- the one real decision, and the one to
   put to the user.
4. `foreach` over anything with the operators, which is then one rule covering the
   compiler's cursor and a program's own walk alike.

Comparisons for a protocol made of operators: **C++**, whose iterators are exactly
this -- `*it`, `++it`, `it != end` and not one named method -- and whose
`std::input_iterator` concept is design F over them; C++ is the language that
answers this question the way E and F do, and it does so because it also has the
operators.  **Rust** answers it with a name and a trait, `next` and `Iterator`,
which is B and D; **Python** likewise with `__next__`.  **Go** has all three answers
in its history: an interface, then a channel, then a function.  **APL**, **BQN** and
**UIUA** have no iterators at all, whole-array operations being the point, which is
the reminder that a language may earn its way out of the question.


Recommendation
--------------

**B, and nothing else yet.**

*Written before the operators landed.  See "What the step operators change" above:
B is still the answer for a protocol that is a name, and it is no longer the first
step towards the iterator, which the step operators and a bundle answer between
them without it.*

The reasoning is that B is the only one of the four that is *small* and *not a
dead end*.  It needs no overloading (A does), no per-value cost (C does), and no
bounds language (D does).  It reuses three things that already exist -- the path
syntax, module-member resolution, and instantiation-time checking -- and it leaves
every larger decision open: `walk.next()` sugar can be added to B, and a trait
can be added over B, and neither is foreclosed.

What B decides, concretely:

1. A definition's name may be a path of two parts, `T.name`, where `T` is a type
   defined in the same file.  Not a type from another module, not three parts:
   both are questions nobody has asked.  *Both were asked within the day, by
   `std.print` wanting to live in `std.Io`: a path through a module reads
   `m.T.name` and is the only three-part path the language has.  A definition for
   a type the file does not define stayed out.*  The second part may be an operator
   instead of a name, which the section on operators below is about.
2. The first parameter is written out.  No implicit receiver, no `self`.  The
   reason is that the language has nothing else implicit, and a receiver would be
   the first thing a reader has to know is there without seeing it.
3. `T.name(args)` is how it is called.  `value.name(args)` is **not** part of
   this proposal, and an operator is not called by either: `a ⊞ b` is how one is
   written, which is the whole reason for attaching it.
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


Operators
---------

**Decided and implemented, and not as recommended here.**  What landed is a
*fifth* notation nobody in this section proposed: an operator is a **free
function whose name is the glyph**, written between grave accents.

    fn `+`(a: Point, b: Point) → Point:  …
    fn `⍴`(p: Point) → u64:             …

See [the specification](spec.md) for what it says and
[decisions.md](decisions.md) for the entry.  **Why it beats O1**, which is worth
writing down because this section argued for O1 at length:

**It needs nothing from this document.**  O1 -- `fn Colour.⊞(…)` -- is design B
plus an alternative in B's rule, so it cannot land until B does.  A free function
needs a *name*, which the language already has a place for; nothing about
attaching code to a type had to be decided first, and none of it was.

**It is generic for nothing.**  `fn `+`(a: T', b: T') → T'` is one definition for
every type whose body works, made once per set of types like any other generic.
O1 cannot express that at all: the type before the dot is one type.  That is the
larger of the two gains and it was not noticed while O1 was being recommended.

**Which side decides is answered by writing both sides down.**  O1 answers it by
not having the question -- both operands are the type before the dot -- and this
answers it the same way for the same reason, except that the reason is now the
parameter list rather than a rule.  A mixed-type operator is still not
expressible, and still for a decision of its own.

**And the arity rule survives unchanged**: one parameter is the prefix reading of
a glyph and two the infix one.  That was the best part of O1 and it needed no
receiver.

**And a bracket pair is an operator after all.**  This section called the pair
"the one a design that thinks only about `+` and `-` forgets" and then listed it as
a name of two characters; what landed is that, with the part this section did not
work out -- Unicode's `Ps` and `Pe` decide which brackets, the pair is written
around what it is applied to, and it takes **as many operands as it likes**, which
is what `g⟦i, j⟧` needs and what C++ took until C++23 to allow.  So `pre(A'⟦I'⟧ → E')` is
satisfiable by a type a program defined, which was the last of the four things this
document is for.

What is left of this section is the reasoning that chose *what may be a glyph* and
*what may not be attached*, which the implementation took as it stands: Unicode's
symbol categories decide, with the language's own operator glyphs added and the
glyphs it has given another meaning removed; `∧` and `∨` cannot be functions.  The rest of the section is the road not taken,
kept because the comparison is what makes the notation that landed look inevitable
rather than arbitrary.

Design B says a function may be named `T.name`.  An operator is not a name, so
this was a question of its own -- and it is the question the fourth thing above
turns on, since what a bundle asks for is almost always an operator.

### What a program has to be able to say

Three shapes, and the requirement notation is where to read them off, since a
requirement is exactly what a program will have to satisfy:

    T' ⊞ T' → T'        an infix operator on two values of the type
    ⍴T' → u64            a prefix operator on one
    A'⟦I'⟧ → E'       a bracket pair, which is neither

The third is the one a design that thinks only about `+` and `-` forgets.  A
program's own collection is the thing most likely to want code attached to it, and
`⟦⟧` and `⸨⸩` are how a program would reach into one.

### Four notations

**O1.  The operator stands where the name does** (recommended at the time; see
above for why the notation that landed is better).

    fn Colour.⊞(a: Colour, b: Colour) → Colour:   …
    fn Bag.⍴(b: Bag) → u64:                       …
    fn Table.⟦⟧(t: Table, i: u64) → u8:      …

The second part of the path is an operator token instead of an identifier.  It
reads as the thing it defines, and the language is already comfortable with a
glyph where a name goes -- `⎕narrow` is a name whose first character is a glyph a
program may not write, and the lifting marks put a *type* where an expression
goes.  The cost is one alternative in one grammar rule, on top of the alternative
design B needs anyway.

**O2.  A word, with the operator declared beside it.**

    fn Colour.largest(a: Colour, b: Colour) → Colour alias ⌈:  …
    @[operator("⌈")] fn Colour.largest(…):                …

Eiffel's, which writes `plus alias "+"`, and the attribute form costs no grammar
at all -- attributes exist, and one taking a string needs nothing new.  What it
buys is a name for the operation, which a reader of the definition may prefer and
which gives something to put in a diagnostic.  What it costs is that the operator
and the name can disagree, and that there are then two ways to call one thing.

**O3.  A fixed set of names.**

    fn Colour.plus(a: Colour, b: Colour) → Colour:  …

D's old `opAdd` and Python's `__add__`: the compiler knows which name means which
operator.  No grammar change at all and no new token anywhere.  It is turned down
because the table is a second language to learn -- `⊞` would need a name, and so
would `⊟`, `⊠`, `⍴`, `⌈` and the rest, and none of those names exists yet.  A
language whose operators are glyphs has already decided that the glyph is the
name.

**O4.  One function, taking the operator.**

    fn Colour.infix(op: ⌜operator⌝, a: Colour, b: Colour) → Colour:
        comptime if op = ⌜⊞⌝: …

D's `opBinary!"+"`, and the one alternative with a real argument in this
language's favour: **pl4g is a language for generators**, and a generator emitting
a vector type wants to say "every arithmetic operator is elementwise" once rather
than nine times.  `comptime if` and the lifting marks are most of what it needs.

It is turned down *for now* rather than refuted, and the reason is that it is not
an alternative to O1 but a layer over it: something still has to say what an
operator on a type *is*, and O4 is a way of writing several of them together.  The
ordering is O1 first, and O4 when a program is written that wants it.

### What O1 decides

*Points 1, 2, 5 and 6 are what the notation that landed decides too; points 3 and
4 read differently for a free function, which is called by writing the operator
and has no path to be called by.*

1. **The arity says which operator is meant.**  `⌈` is both prefix and infix --
   `⌈xs` is the largest of several values and `a ⌈ b` is the larger of two -- and
   `fn Bag.⌈(b: Bag)` defines the first while `fn Colour.⌈(a: Colour, b: Colour)`
   defines the second.  One parameter is the prefix reading and two is the infix
   one, which needs nothing written to say so.
2. **Both operands are the type.**  `fn Colour.⊞(a: Colour, b: Colour)`, and no
   mixed-type operator.  That is a real restriction and it is deliberate: it is
   the shape a requirement asks for -- `T' ⊞ T' → T'` names one type twice -- so
   the restriction and the thing it exists to satisfy are the same shape.  What it
   answers for nothing is the question every language with mixed operands has to
   answer, which is *which side decides*.
3. **What it answers is the definition's business.**  `fn Point.⊞` answering a
   `Point` is the ordinary case, and one answering a `u64` is not refused: a
   requirement that wanted a `Point` back says so with its arrow.
4. **It is not called as a path.**  `a ⊞ b` is how it is written, which is the whole
   point; `Colour.⊞(a, b)` is not part of this and should probably be refused
   rather than given a meaning, since two spellings of one thing is what O2 was
   turned down for.
5. **Precedence and associativity are not the program's.**  The glyph set is fixed
   and so is its table: `a ⊞ b ⌈ c` groups the way it groups whatever `Colour`
   defines.  Swift lets a program declare precedence groups and new operators; that
   is a much larger language and this proposal does not approach it.
6. **A bracket pair is a name of two characters.**  `fn Table.⟦⟧` and
   `fn Table.⸨⸩`, with the index as a second parameter.  Writing the pair with
   nothing between it is what says the operator rather than an empty literal.
   *Taken as it stands, and with the part this point missed: the indices may be
   several, so a pair takes two parameters or more.*

### What may not be attached, and why

**`←`**, which is not an operator on values: it binds a name, and a function
that ran when a name was bound would make a program's assignments into calls.

**`∧`, `∨`, `and`, `or`**, which decide *whether* to evaluate.  A function
takes its arguments already worked out, so a program attaching one of these would
be changing when things happen and not what they mean -- which is the trap C++
left open in overloading `&&`, and every style guide since has said not to.

**`?` and `??`**, which are about a result and not about what a result holds.

**The lifting marks**, which are the grammar saying that a type follows.

**`=` and `≠`: attachable, and only together with a hash.**  A type whose `=` a
program wrote and whose hash it did not is a type that can be a dictionary key and
answer wrongly, which is the one failure here that is silent.  4429 already says
what the pair is -- *hashed and compared* -- so the rule is that a type providing
one provides both, and a type providing neither is refused as a key exactly as it
is today.  This is the one place where attaching code is not purely additive, and
it is why `=` is worth naming here rather than leaving to the general rule.

### What follows from one definition

**`≠` follows from `=`**, and the three other orderings follow from `<`.  A type
writes `fn T.=` and `fn T.<` and has six operators; letting a program write all
six is letting it write four that disagree with the other two, which is C++'s
experience before `operator<=>` and the reason that operator exists.  A type that
wants an ordering no negation of `<` describes is a type wanting something this
language should probably not offer.

That is a rule about what a *definition* gives, not about what a requirement may
ask: `pre(T' ≤ T' → bool)` is met by a type that wrote only `fn T.<`, because
the operator works on it.

### Operators in other languages

**Ada** writes `function "+" (Left, Right : Vector) return Vector` -- the operator
as a name in quotes, which is O1 with the quoting Ada needs because `+` is not a
legal identifier there.  A language whose operators are already glyphs does not
need the quotes.

**C++** has `operator+` as a member or a free function, chosen per operator, with
the free form there to answer the mixed-operand question O1 refuses to have.  Its
lesson is `operator<=>`: twenty-eight years of types whose four orderings did not
agree, fixed by making one definition give all of them.

**Rust** has one trait per operator -- `Add`, `Neg`, `Index` -- with the
right-hand type as a trait parameter, which answers mixed operands at the cost of
a trait system and orphan rules.  `PartialOrd` gives the four orderings from one
method, which is the rule above.

**D** has `opBinary!"+"`, one function over all binary operators, which is O4 and
which D reached after `opAdd`, which is O3.  That progression is the argument
against O3.

**Python** has `__add__` and `__radd__`, the second existing only to answer which
side decides -- and `NotImplemented` as a value meaning "ask the other one",
which is a protocol a language without it does not have to explain.

**Eiffel** writes `plus alias "+"`, which is O2, and is the language that shows
what a name beside the operator is worth: its own library reads `a.plus (b)` and
`a + b` interchangeably.

**Haskell** has no operator attachment at all, because an operator is an ordinary
function whose name happens to be symbols, and `instance Num Vector` is how a type
gets one.  That is the cleanest answer available and it needs classes.

**Swift** goes furthest: operators are functions, a program may declare *new*
ones, and precedence is declared in named groups.  It is the demonstration that
point 5 above is a real fork in the road and not a detail.

**Go**, **Odin** and **Zig** have no operator overloading, on purpose and with the
same reason: an operator whose meaning a reader cannot see is a cost paid on every
line to save characters on a few.  That argument is weaker here than in those
languages, and the reason is the fourth thing this document is for: without
attachable operators a bundle cannot be satisfied by anything a program defines,
so the feature is not sugar but the difference between generics that work over the
language's types and generics that work.

**APL**, **BQN** and **UIUA** are the other end: the glyph *is* the language, and
what a glyph means on a value is the interpreter's and not a program's.  BQN lets
a program name a function with a symbol of its own, which is Swift's answer in an
array language.

**Wolfram** answers "which side decides" with up-values: `f /: Plus[f[x], y] := …`
attaches a rule for `Plus` to `f` rather than to `Plus`.  It is the most general
answer on this list and it needs a term rewriter.

### What the operator question does not decide

- **Mixed-type operators.**  `Point ⊠ u8` is what a vector wants and neither O1 nor
  the notation that landed can say.  The answers are Rust's (the right type is a parameter of the bound),
  Python's (ask the other side) and Wolfram's (attach to either), and none of them
  is small.
- **Whether a program may attach an operator to a type it did not define.**  The
  same question design B leaves open, with the same answer available: no is
  smaller and can be relaxed.
- **A new operator.**  Swift's, and not this: the glyph set is the language's.
- ~~**A bracket pair.**~~  *Decided: `Ps` and `Pe` name one, and it takes as many
  operands as it likes.*
- **What a hash looks like.**  The `=`-and-hash pair needs a name for the hash and
  a shape for what it answers, and that is the decision that turns a record into a
  dictionary key.  It belongs with this and is not in it.
- **Whether an attached operator may be generic.**  `fn Vec.⊞` where `Vec` holds a
  `T'` is what a container wants, and a generic definition attached to a generic
  type is a second instantiation question.


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

- **The parser**: a definition's name may be `IDENT . IDENT`, and for an operator
  `IDENT . OPERATOR` and `IDENT . BRACKET-PAIR`.  One rule with three
  alternatives, and the same in `tree-sitter-pl4g/grammar.js` in the same commit.
  The operator alternatives are where the grammar is least certain: every operator
  token is otherwise only ever seen between or before operands, so a generalized
  parse may find a second reading of `fn T.⌈(` that an LR one would not.
- **The resolver**: a two-part path whose first part names a type in this file
  resolves to that type's function.  Beside the module-path rule that is there.
- **Mangling**: the symbol is the path.  Nothing new; a name with a dot is a name.
- **`foreach`**: one arm in `_iteration_of` -- a type with a `T.next` of the
  right shape is walked by calling it -- and the loop machinery already carries
  state across turns and tests a result, which is what it needs.
- **The operators**: where an operator is lowered, one arm before the refusal, for
  each of the three shapes: a binary operator on two values of one program-defined
  type, a prefix one on one, and a bracket pair.  Each of them is a place that
  today reports 4207 or its neighbours, so the arm goes where the refusal is and
  the refusal stays for a type that attached nothing.  Then `≠` from `=` and the
  three orderings from `<`, which is the same arm reading one definition and
  negating what it answered.
- **A requirement over an attached operator**: nothing.  A requirement is checked
  by lowering its expression, so an operator the lowering now accepts is an
  operator a requirement now accepts, and `pre(number(T'))` over a program's own
  type starts working the day the arms above do.  That is worth saying because it
  is the whole point and it costs nothing: the two features were not designed
  together and meet without a seam.
- **The specification**: a section under Definitions, and an entry in
  `decisions.md`.
- **Tests**: a type with two functions, two types each with a `next`, a call
  through the path, a `foreach` over a program's iterator, and the refusals -- a
  path naming something that is not a type, a `T.next` of the wrong shape.  For the
  operators: an infix one, a prefix one sharing its glyph with an infix one, a
  bracket pair, `≠` answered by a type that wrote only `=`, an ordering answered by
  a type that wrote only `<`, a bundle requiring an operator and met by a program's
  own type, and the refusals -- `∧` attached, `←` attached, a mixed-type operator,
  and `=` without a hash used as a key.

The `foreach` arm is the only part that touches anything delicate, and the piece
worth doing *before* any of it is smaller still: **`foreach` over a cursor**,
which is refused today (4438) although a cursor is the compiler's own iterator
value and the loop's shape already fits it.  That needs no decision from this
document at all, and it is the half of the iterator entry that is not blocked on
anything.

*And under E it is more than a first step: a rule that walks a cursor is the rule
that walks a program's own type, since what a cursor offers is the step operators.
See "How the evaluation changes" above for the order the work goes in now.*
