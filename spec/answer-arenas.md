Saying Where an Answer Was Made
==============================

**Decided**, on 2026-10-01; see [decisions.md](decisions.md) for the entry and
[spec.md](spec.md), "Where an answer was made", for the rule.  Alternative 1 was taken:
`→ T in a, b` on the answer and `s: T in a` on a parameter, with no warning where a
signature says nothing.  What follows is the proposal as it was written.

How a function's signature says that what it answers was made in one particular arena it
was handed -- so that a caller knows how long the answer lives without seeing into the
function.


Where the language stands
-------------------------

The compiler keeps, for every name, the arenas its value was made in, and refuses reading
it after one of them is given back (4615), answering it from the function that made the
arena (4613), and putting it where it outlives the arena (4614).  Inside one body that is
read off what is written.  **Across a call it is a guess**: the answer is taken to be made in
*every* arena the call was handed, directly or inside an argument.

    fn label(a: &mut arena, b: &mut arena, v: u16) → str:
        let scratch: str = ⍕v in b              ※ working space
        "#" ⧺ scratch in a                      ※ the answer lives in a

    let name: str = label(&mut long, &mut short, 7u16)
    ⎕empty(short)
    name                                        ※ refused today (4615), and correct

The program is right and the refusal is the cautious guess being wrong.  What the caller
is missing is one fact the function knows: *the answer comes out of `a`*.


The proposal: `→ T in a`
------------------------

Write on the answer the same `in` an expression is written with:

    fn label(a: &mut arena, b: &mut arena, v: u16) → str in a:
        let scratch: str = ⍕v in b
        "#" ⧺ scratch in a

    fn text(v: T', a: &mut arena) → str in a:   ※ std.text, as it would read

**What it promises**: the answer was made in the arena `a` names, or in something that lasts
at least as long -- `⎕heap`, a literal, a variable at the top level.  Nothing about any other
arena the call was handed.

**What the caller does with it**: the call's answer carries exactly the arenas of the
arguments the named parameters were given.  `label(&mut long, &mut short, 7u16)` answers
something made in `long`, so emptying `short` leaves `name` alive, and emptying `long`
kills it.

**What the body is held to** (a new diagnostic): the compiler walks the answer back the way
it already does for 4613 and refuses one made in an arena the signature did not name --
`⍕v in b` answered from `label` above, or an argument's text answered where the argument's
own arena was not named.

**Several arenas**: `→ str in a, b` -- the answer may come from either, so it lives as long as
the shorter of the two, which is the rule a lifetime name on several parameters already
has.

**A parameter may say it too**: `s: str in a` promises the caller hands text made in `a` (or
something lasting longer), which is what lets a body answer `s` under `→ str in a`.  Without
it, a `str` parameter's provenance is unknown inside the body, and answering it under an
`in` is refused.

**Nothing written** keeps today's meaning -- the answer may come from any arena handed --
so every existing program means what it meant, and adding `in` only ever narrows.  Unlike a
reference, where writing neither `static` nor a lifetime name is refused (4562), a default
here is safe: the cautious reading never lets a program read freed memory, it only refuses
some correct ones.

**What counts as "an arena" here** is the parameter: `a: &mut arena`.  A local arena cannot be
named -- the function gives it back before returning (4613) -- and `in ⎕heap` is pointless,
`⎕heap` being what nothing written already allows.

**The whole answer, for now.**  `→ (str, str) in a` says it of both.  Saying it per component
(`(str in a, str in b)`) wants `in` inside a type, which is the larger step below.


How it relates to lifetime names
--------------------------------

The language already has `⧖x` for references: `fn first(v: &⧖x u8) → &⧖x u8`.  An arena-made
value *is* a reference in all but spelling -- `str` is a pointer and a length -- and "made in
`a`" is "lives as long as what `a` names".  So `→ str in a` is short for a lifetime name that
the arena parameter and the answer share, and the checker can treat it that way: the same
walk back (4561), the same "shortest of them" rule, the same report-log line saying which
argument the answer took its lifetime from.

**Why not write the lifetime name itself** -- `a: &mut ⧖x arena`, `→ str ⧖x`?  Because it says
the same thing with two extra symbols and a name to keep in step, and `in` is already how a
program says "made in this arena" on the line that makes something.  The signature reads the
way the body's last line does.  Should per-component answers or record fields holding
arena-made text ever be wanted, `⧖` names on non-reference types are the general form, and
`in a` stays as its shorthand.


Alternatives considered
-----------------------

1. **`→ T in a`** (above).  Reuses the expression syntax; a reader sees the arena on the
   answer.  *Recommended.*
2. **Lifetime names**: `a: &mut ⧖x arena` and `→ str ⧖x`.  More general -- it extends to
   fields and components -- but heavier for the common case, and it puts a lifetime on a
   type that does not look like a reference.
3. **An attribute on the parameter**: `@[answers] a: &mut arena`, D's `return scope` in
   spirit.  Says it where the arena is rather than where the answer is; cannot say "either
   of two" without the attribute on both, and a reader of the answer type sees nothing.
4. **Inference**: work out from the body which arenas the answer can come from and record it
   in the compiled interface.  No syntax at all, but the signature stops being the contract:
   changing the body changes what callers may do, and an external or imported function
   cannot be inferred.  Could still be offered as a *suggestion* in a diagnostic.


Comparison
----------

- **Rust** with an arena crate (`bumpalo`): `fn label<'b>(a: &'b Bump, …) -> &'b str`.  This
  proposal is that, with the lifetime implied by naming the arena -- Rust has to name the
  lifetime because a reference and its arena are different things there.
- **Cyclone**'s regions: `char *ρ label(region_t<ρ> r)`.  The closest ancestor: a region handle
  as a parameter and the answer's pointer type carrying the region.  Cyclone writes the
  region on the pointer type, which is alternative 2.
- **ML Kit** and **Tofte–Talpin** regions: the same information, entirely inferred --
  alternative 4, with the cost that the region annotations a programmer reads are the
  compiler's output.
- **D**'s DIP1000: `return scope` on a parameter says the answer may refer to what that
  parameter refers to -- alternative 3.
- **Zig**: an `Allocator` parameter and a documentation convention ("caller owns the
  returned memory, allocated with `allocator`"), checked by nobody.  **Odin**'s
  `allocator := context.allocator` parameter is the same convention.
- **C++** `std::pmr`: a container remembers its `memory_resource`; a function returning one
  says nothing in its signature about which.
- **Go**, **Java**, **D** with its collector: the question does not arise.


What it costs
-------------

- **Grammar**: an optional `in NAME (, NAME)*` after a function's answer type and after a
  parameter's type; the tree-sitter grammar the same.
- **Checker**: the annotation stored on the function; at a call, the answer's arenas
  computed from the named arguments instead of from all of them; in the body, the answer's
  provenance checked against the names (a new diagnostic); a `str in a` parameter carrying
  `a`'s identity as its provenance.
- **`std`**: `text`, two-operand `⍕` and `formatted`'s output marked `in a`; `format` needs
  nothing, its answer being in `⎕heap`.


Open questions
--------------

1. `in` or the hourglass (alternative 1 or 2) -- the one decision that shapes the rest.
2. Whether a body that answers something made in a named arena should be *warned* when its
   signature says nothing, nudging toward the narrower contract.
3. Whether a parameter's `in a` should be allowed at all in the first step, or added only
   once a program wants to answer one of its own text arguments.
