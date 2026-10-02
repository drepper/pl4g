Handing an Arena Out with What It Made
=====================================

**Decided**, on 2026-10-02; see [decisions.md](decisions.md) and [spec.md](spec.md),
"Handing an arena out".  Alternative 1 was taken.  Of the open questions: a call bound without
`in NAME` is refused, as is `f(…) in NAME`; a lambda may answer this way, its type saying so.
What follows is the proposal as it was written.

How a function makes an arena, builds something in it, and hands the arena out to its caller
together with what it built, so that the value outlives the call without being copied.


Where the language stands
-------------------------

Every object knows how it is given back, and a function answers in the allocator its
signature names: `→ T in a` for an arena the caller handed over, `⎕heap` where it names none.
An answer made in an arena of the function's own is **copied** into that allocator before the
function leaves, since the arena is given back on the way out (4617):

    fn words(text: str) → [str]:
        let pool: mut arena = ⎕arena
        defer ⎕empty(pool)
        …build the list in pool…          ※ copied into ⎕heap, all the way down

That is correct, and for something built from many small pieces it is the wrong cost: every
piece is allocated twice, and the heap then gives back each of them one at a time when one
`⎕empty` would have done.  What is missing is a way to say **"the arena leaves with the
answer"**.  An arena is `@[unique]`, so it can be moved and never copied; what is missing is
the syntax, and a rule for who gives it back.


The proposal: `→ T in NAME` naming an arena of the body's own
--------------------------------------------------------------

Today `in` after the answer may name only a parameter (4618).  Let it name an arena the body
makes, and let that mean the arena is handed out:

    fn words(text: str) → [str] in pool:
        let pool: mut arena = ⎕arena      ※ no defer: it leaves with the answer
        …build the list in pool…
        list

**The caller binds both**, the answer and the arena, with the same `in`:

    let found: [str] in kept = words(line)
    defer ⎕empty(kept)
    …use found…

`kept` is a new arena of the caller's, holding what `words` built; `found` was made in it.  From
there everything is the rule the language already has: `found` is dead after `⎕empty(kept)`,
the caller gives `kept` back on every way out if it is pure (4617), and it may hand `kept` and
`found` on in turn with `→ [str] in kept`.

**What the body is held to**: the named arena is not given back on any way out -- no `⎕empty`,
no `defer ⎕empty` -- and nothing else the body made is in the answer, which is copied into
the named arena where it is not (as an answer is today).  The arena is moved, not copied: its
state is three words and what it made lives in its chunks, which do not move, so what was made
in it is still where it was.

**What the caller is held to**: the call is bound with `in NAME`, or its answer is used only
where it is copied out of the arena at once -- a call written where a value is wanted, with
nothing to bind the arena to, gives the arena back as soon as the value has been copied, which
is what today's answer does.


Alternatives
------------

1. **`→ T in NAME` on a local, `let v: T in r = f()` at the caller** (above).  The same `in`
   at both ends, no new type, and the caller's arena is an ordinary local.  *Recommended.*
2. **A record holding the arena and the value**: `type Words = pool: arena ; list: [str] in pool`,
   a field whose `in` names another field.  More general -- several values, one arena -- and
   it is what a program writes in Rust with a self-referential crate.  It needs `in` on a
   field, a record that is `@[unique]` because the arena is, and a rule for which field gives
   back which.
3. **A second answer**: `→ 〈arena, [str] in 0〉`, the arena as the first member of a tuple.
   No new rule for records, but a numeric reference into one's own answer type, and a tuple
   that has to be taken apart before either half is usable.


Comparison
----------

- **Zig**: the idiom is `var arena = std.heap.ArenaAllocator.init(…)` in the caller, handed down;
  a function that makes one and returns it with its data returns a struct holding both, which is
  alternative 2, checked by nobody.
- **Rust**: returning a `Bump` together with a `Vec<'bump, _>` is the self-referential struct the
  borrow checker cannot express; `ouroboros` and `yoke` are crates for exactly this, alternative 2.
- **C++**: a `pmr` container returned together with a `unique_ptr` to its `monotonic_buffer_resource`,
  in a struct whose member order decides which is destroyed first.
- **Cyclone**: dynamic regions are unique handles that can be returned and opened later, which is
  this proposal with the region opened explicitly.
- **ML Kit** / Tofte–Talpin: region polymorphism infers that a function's result region is created
  by the caller; nothing is handed out because nothing has to be.
- **Go**, **Java**, **D**: collected; the question does not arise.


Open questions
--------------

1. Alternative 1, 2 or 3.
2. Whether a call bound without `in NAME` should copy and give the arena back (as proposed) or be
   refused, so that the cost of the copy is never hidden.
3. Whether a lambda may answer this way, a function value's type saying nothing of `in`.
