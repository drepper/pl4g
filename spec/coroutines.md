Coroutines
==========

**A proposal, not a decision.**  Nothing here is settled until the user says so.  What is here is what a coroutine would have to
be in this language, the ways it could be built, how each compares with what other languages did, a recommendation, and -- as
asked -- what building iterators as coroutines would gain and cost against the way they are built today.  The questions the user
has to answer are collected at the end.


What a coroutine is, and what wants one
---------------------------------------

**A coroutine is a function that can stop part way, hand something out, and be started again where it stopped**, with its locals
as it left them.  A plain call runs to its end before its caller goes on; a coroutine and whatever drives it take turns.

Three things in this language want it, in the order they would use it.

**A program writing its own iterator.**  `foreach` walks a range, an array, a pick, a list, a string, a set and a dictionary, and
the compiler is every one of them: `_Iteration` in the checker says, for each, how to test, take and step, and the loop is built
around that.  A program's own sequence -- the nodes of a tree, the lines of a buffer, the primes below a bound -- has no way in.
[Attaching code](attaching-code.md) found the place for a type's `next` (`fn Walk.next`), but a `next` written by hand is a state
machine written by hand: whatever the walk has to remember between two elements goes into a record, and the walk is broken up at
every element into "where was I".  A coroutine is that state machine written as the straight-line code it stands for:

```
fn evens(n: u64) ⇉ u64:
    foreach i := 0u64…n:
        if 2u64 ∣ i:
            yield i
```

**A program written by a generator.**  This language is emitted by programs more than it is typed, and an emitter producing a
lexer, a protocol handler or a parser produces a state machine.  Emitting one as a coroutine is emitting the control flow it
describes; emitting it as a record and a `match` over a state number is the emitter doing the compiler's job.

**I/O that waits without stopping everything.**  `std.write` already hands back a `Pending` and the ring is driven by the calls
that use it (spec, *The `std` module*); nothing can do other work while a request is outstanding, because there is nothing to
switch to.  A coroutine that stops at "wait for this completion" and a loop that resumes whichever coroutine's completion arrived is
how C++, Rust, C#, Python and Kotlin all do asynchronous I/O without threads -- and this language has no threads.

The first is the one asked about and the one this proposal finishes; the third is sketched so that nothing decided for the first
closes it off.


The ground this stands on
-------------------------

What the language already has decides a great deal, so it is collected first.

- **One stack**, mapped by the program itself with a guard below it (`--stack-size`, default 1 MiB; `--guard-size`, default
  64 KiB), and a `SIGSEGV` handler on an alternate stack that reports a fault in the guard as the stack running out (status 86).
  There are no threads (`decisions.md`: "the answer this language has no threads for yet").
- **Frames hold only spills.**  The register allocator is the only thing that puts anything in a frame; a function that spills
  nothing has none.  The prologue is one `sub sp`; there is no frame pointer; a backtrace walks per-function tables.
- **A lambda is three words** -- code, environment, allocator -- and **the compiler decides where the environment lives**: in the
  frame of the call that made it where it provably stays, in `⎕heap` otherwise, in `a` where `in a` says so.  A type says whether
  the environment lasts: `fn(u8) in ⎕heap → u8`.  The decision is in the report log.
- **References may not outlive their place** (4570, 4571), `&mut` is exclusive and borrows are lexical; a function does not answer
  with an array (4454), since its elements are in the frame that is gone.
- **Allocation is explicit and not an effect.**  `⎕heap`, arenas named with `in`, `⎕static`; an arena is emptied by its maker.
- **Functions are pure unless they say otherwise**, and purity is used: calls are dropped, reused, hoisted and folded.
- **`defer`** runs on every way out of a block and is lowered by writing it out at each exit.
- **A result `T?`** is how "no answer" is said, and an iterator's `next` is a result: failure ends the loop.
- **Self tail calls are loops**, guaranteed; mutual recursion takes stack.
- **The many-way branch** is lowered as a jump table where it is dense -- which is what resuming at one of several places is.

Two consequences follow before any design is chosen.  A coroutine whose stopped state lives somewhere has to say *where*, in the
same words a lambda's environment does, or it is the one allocation in the language a reader cannot see.  And a coroutine holding a
reference holds it across a stop, so it is subject to the same rule a lambda capturing by reference is: it may not outlive the place.


The first choice: where a stopped coroutine is
----------------------------------------------

Everything else follows from this.  There are two answers, and every language that has coroutines picked one.

### Stackless: the state is a record

The compiler turns the coroutine into a **state machine**.  Every value live across a `yield` is moved into a record -- the
coroutine's *frame* -- together with a number saying which `yield` it stopped at.  Resuming is a call to one function that loads the
number, jumps to the code after that `yield` (a many-way branch), reloads what it needs from the record, runs to the next `yield`,
stores what is live, and returns what it yielded.

- **Size known while compiling.**  The record is exactly the values live across some `yield`, so its size is a constant -- the
  property that lets it live in the frame of whoever drives it, with no allocation at all, as a lambda's environment can.
- **Creation is a handful of stores**; a resume is a call, a jump through a table, and loads and stores of what is live.
- **Only the coroutine's own body can stop.**  A function it calls runs to its end; a callee cannot `yield` on its behalf.  A
  coroutine that wants another's elements delegates, element by element (`yield from` below) -- which is what is called *function
  colouring*: a function that may stop is a different kind of function.
- **Recursion has to be named.**  A coroutine that makes another of itself holds one inside its own record, whose size is then
  infinite; the inner one has to live elsewhere -- an arena -- exactly as Rust requires a `Box::pin` for a recursive `async fn`.

Chosen by C++20 (`co_yield`, `co_await`), Rust (`async`, and generators), C# (`yield return`, `async`), Python (`yield`,
`async def`), JavaScript (`function*`, `async`), Kotlin (`suspend`, compiled to a continuation-passing state machine) and F#.

### Stackful: the state is a stack

Each coroutine gets **a stack of its own**, and stopping is switching stacks: the registers the calling convention says survive a
call are saved, the stack pointer is pointed at the other stack, and its registers are restored.

- **Anything can stop, at any depth.**  A function called by the coroutine, and one called by that, may `yield`; there is no
  colouring, and a recursive walk of a tree yields from the bottom with no delegation.
- **A stack has a size nobody knows.**  It has to be large enough for the deepest call the coroutine makes, which is not known
  while compiling; here that means a mapping per coroutine -- at least a page, a guard page below it, and the `SIGSEGV` handler
  taught that there are now many guards.  Creation is two system calls (`mmap`, `mprotect`) and a page fault on first use: a few
  microseconds, against a few nanoseconds for a stackless one.
- **A switch saves and restores every callee-saved register**: 6 and the stack pointer on x86-64, 12 general and 8 floating-point
  ones on AArch64 (`x19`–`x29`, `lr`, `d8`–`d15`), 12 and 12 on RISC-V (`s0`–`s11`, `fs0`–`fs11`) -- twice per element, out and
  back.  Nothing across the switch can be inlined: the compiler does not see where control goes.
- **The backtrace walker** walks one stack and stops at its top; one per coroutine would have to be stitched together.

Chosen by Lua (`coroutine.create`), Go (goroutines, with a scheduler and growable stacks the runtime moves -- which needs precise
knowledge of every pointer on the stack), D (`core.thread.Fiber`, and `std.concurrency.Generator` over it), Ruby's `Fiber`, Boost
(`Boost.Context`/`Boost.Coroutine2`), and Java's virtual threads.  Zig had stackless `async` with an unusual twist -- the frame's
size known so the caller could hold it -- and removed it in 0.11 pending a redesign; it is the closest precedent to what this
language would do and the warning that the corner cases (cancellation, recursion, frame size across function pointers) are where
the work is.

### Further back: continuations

Scheme's `call/cc` captures *the rest of the program* as a value, and every coroutine is a special case of it.  It needs the stack to
be copyable or the language to be compiled in continuation-passing style throughout; Haskell gets the same effects from laziness (a
lazy list *is* a generator) and from the continuation monad.  Neither fits a language whose calls are plain calls on one stack and
whose every allocation is visible.  The array languages -- APL, BQN, Uiua -- have none of this and need none: what a generator would
produce is an array, made eagerly.  Wolfram has no coroutines either; `Sow`/`Reap` is the nearest thing, and it collects rather
than suspends.  Odin and C have none; Odin's iterators are procedures returning `(value, ok)` over a state the caller holds, which
is exactly the hand-written `next` this would replace.

### Recommendation: stackless

For this language the choice is not close.

- **It fits the allocation story.**  A record of known size is placed by the rule a lambda's environment already follows -- the
  driver's frame where it stays, `⎕heap` or `in a` where it leaves -- and is in the report log.  A stack of unknown size is a
  mapping nobody wrote.
- **It fits the back end.**  The transformation is a pass over the IR before register allocation, built from pieces that exist:
  liveness to find what crosses a `yield`, a record to keep it in, a many-way branch to resume.  A stack switch is a new kind of
  control transfer the register allocator, the frame tables and the backtrace walker would all have to learn.
- **It is fast where iterators are**, which is the first use: a resume the compiler can see is one it can inline, and a coroutine
  consumed by the loop that made it can disappear entirely (below).
- **What it costs is colouring and named recursion**, and both are visible in the program rather than surprises at run time: a
  delegation is written, a recursive coroutine says which arena its inner ones go in.

Stackful coroutines are worth a separate decision later, if ever: they are what a scheduler of many long-lived tasks wants, and
that is the third use, not the first.


The design, in stackless terms
------------------------------

### A function that yields

**A function whose answer is written with `⇉` is a generator**: calling it makes a coroutine and runs nothing, and its body hands
values out with `yield`.

```
fn evens(n: u64) ⇉ u64:
    foreach i := 0u64…n:
        if 2u64 ∣ i:
            yield i
```

- `⇉` (U+21C9, RIGHTWARDS PAIRED ARROWS) is the arrow of a signature doubled: what follows is what the function gives out, more
  than once.  It is free; nothing in the language uses it.
- **`yield v` hands out one value** of the type after `⇉` and stops; the next request starts after it.  A body that reaches its
  end, or a `return` with no value, is finished.  `return v` is refused: what a generator gives is its yields.
- **A generator's body may be any body**: loops, `match`, `defer`, calls -- and `yield` anywhere in it except inside a `defer`
  (which runs on the way out, when there is nobody to yield to) and inside a lambda (whose body is another function).
- **Calling it makes the coroutine and does nothing else.**  The arguments are copied into its frame; the body has not started.
  Python, C#, Rust and JavaScript all start lazily, and it is what makes a generator over an endless sequence a thing one can make.

The alternatives for the spelling are below, among the questions.

### What a coroutine is as a value

**`⇉T` is the type of a coroutine that yields `T`**, and like a function value it is three words: where its resume code is, where
its frame is, and the allocator that frame came from.  Every generator that yields `u64` makes a `⇉u64`, whichever body it runs,
which is what lets one be handed to a function that takes "something yielding `u64`" -- the same erasure a lambda's type has, and for
the same reason.

```
let g: mut ⇉u64 = evens(10u64)
foreach x := g:                      ※ walks it
let more: u64? = ⎕next(g)            ※ one element, or the failure that says it is finished
```

- **`foreach` walks a `⇉T`.**  It is the eighth thing a loop walks, and the first a program writes.
- **`⎕next(g)` resumes it once** and answers `T?`: the element, or the failure once it is finished.  It needs `g` to be `mut`,
  as a cursor's step does -- resuming changes what the coroutine is.
- **A finished coroutine stays finished**: `⎕next` answers the failure again, which costs one test of the state number.  Rust calls
  this a *fused* iterator and makes it optional; Python's generators are fused; C++'s `std::generator` makes resuming a finished one
  undefined.  Stopping the program instead is the alternative, and the one the cursor follows when it steps off an end.
- **A coroutine is moved, not copied.**  Copying one would copy a stopped computation -- two coroutines sharing every reference in
  the frame and both believing they own any `defer` pending in it.  It is `@[unique]` the way `std.Init` is: handed on, never
  duplicated.  Python's generators cannot be copied either; C++'s are move-only; Rust's are moved.
- **What it reaches, it may not outlive.**  A parameter that is a reference is held across every `yield`, so the coroutine is
  subject to 4570 as a lambda capturing by reference is: `fn lines(b: &Buffer) ⇉ str` makes a coroutine that may not leave the
  scope `b`'s place is in.

### Where the frame is

**The rule a lambda's environment follows, unchanged.**

- **A coroutine that stays -- walked by the loop that made it, or bound by a `let` that only walks it and hands it to calls -- has
  its frame in the frame of the function that made it.**  No allocation, and nothing to give back: it is gone with the call.  This
  is the case every iterator is in.
- **One that leaves** -- answered, stored, handed to something that keeps it -- **has its frame in `⎕heap`**, or in the arena `in
  a` names: `evens(10u64) in pool`.  `⇉u64 in ⎕heap` is the type of one whose frame lasts, as `fn(u8) in ⎕heap → u8` is.
- **The report log says which**, under `allocator`, as it does for a lambda.

C++20 allocates every coroutine frame on the heap and relies on the optimizer to elide it (HALO), which it does only when the frame's
lifetime is provably nested -- an optimization a program cannot count on, so C++23's `std::generator` takes an allocator argument to
take control back.  Rust never allocates: the coroutine is a value of an anonymous type whose size the compiler knows, and the
program boxes it where it wants.  This is Rust's position for the case that stays and C++'s explicit allocator for the case that
leaves, decided by the compiler and reported, which is the arrangement lambdas already have.

### Stopping early: `break`, and `defer`

**A coroutine abandoned part way runs the `defer`s pending where it stopped.**  A loop left by a `break`, a coroutine that goes out
of scope unfinished, a frame whose arena is emptied -- each is the coroutine's last moment, and what its body deferred was written
to run on every way out.

- The compiler already writes a `defer` out at each exit; a `yield` adds one more exit per stop, the *abandon* path, reached by
  resuming with "finish now" instead of "go on".  Python does the same by raising `GeneratorExit` at the `yield`; C++ by
  `coroutine_handle::destroy`; Rust by dropping the generator, which runs destructors of what is live at its stopping point.
- **One without `defer` is abandoned for nothing**: its frame is storage like any other and goes with whatever holds it.
- **One whose frame is in an arena is abandoned when the arena is emptied**, which has to run those `defer`s first.  That is a new
  duty for `⎕empty` and the reason the recommendation below is to **refuse `defer` in a coroutine that leaves** in the first
  version: a coroutine kept in `⎕heap` has no moment the language can name as its last.

### Errors

**What a generator yields may be a result**, and that is how a failure part way is said: `fn lines(f: &File) ⇉ str?Error` yields a
line or the error that stopped it.  `?` inside a generator body, applied to a `T?E` where the generator yields `U?E`, **yields the
failure and finishes** -- the nearest thing to "leaving with the error" a coroutine has.  The loop over it gets each line as a
result and decides, which is what the language asks of every result elsewhere.

Python raises out of `next`; C# throws out of `MoveNext`; Rust's iterators yield `Result` items, which is this.

### Delegating: `yield ⇉`

**`yield ⇉ g` hands out every element of the coroutine `g`**, in order, and goes on when it is finished.  It is the one place
stackless coroutines are weaker than stackful ones: a recursive walk of a tree delegates at every level, so an element at depth *d*
passes through *d* resumes on its way out.

```
fn walk(t: &Tree, scratch: mut arena) ⇉ u64:      ※ a sketch: how a tree holds its children is beside the point
    foreach child := t⌖.children:
        yield ⇉ walk(child, scratch) in scratch
    yield t⌖.value
```

The recursive call says where its frame goes (`in scratch`), since a frame holding a frame of its own kind has no size.  Python's
`yield from` and C#'s nothing-at-all (a nested `foreach` + `yield return`) cost *O(d)* per element; C++23's
`std::ranges::elements_of` makes it *O(1)* by *symmetric transfer* -- the outer resume jumps straight to the innermost frame and
remembers the chain -- which is an optimization the first version can leave out and a later one can add without changing a program.

### Purity

**Making a coroutine is pure; resuming one is what its body is.**  A generator's body follows the rules every body does -- pure
unless marked -- so a pure function may make and walk a pure generator (it writes only the coroutine's frame, which is its own
storage).  Resuming an `@[impure]` generator is an impure act.  The purity passes treat `⎕next` as writing its operand, which it
does, and never drop, reuse or hoist one.

### What else it touches

- **Generics**: a generator may take type parameters, and is compiled once per set as any generic function is.
- **`comptime`**: the compile-time evaluator can run a generator -- a coroutine there is a Python generator, which the evaluator is
  written in -- so `comptime foreach` over one, and a constant worked out from one, come almost free.
- **Tail calls**: a `yield` is never in tail position, and a generator's body is not a candidate for the tail-call pass across one.
- **`pre` and `post`**: `pre` is checked when the coroutine is made, with the arguments; `post` is checked when it finishes, and
  only then.
- **The backtrace**: a fault inside a resumed coroutine walks the resume function's frame, which is an ordinary frame, then the
  driver's.  It shows the driver as the caller, which is the truth about where control came from.

### How it is built

1. **The checker** accepts `⇉` and `yield`, types `⇉T`, `⎕next` and `foreach` over one, and places the frame (`_environment`'s
   rule, reused).  The body is lowered as an ordinary function with a `yield` instruction in the IR -- a terminator with one
   successor (where to go on) and the yielded value.
2. **A pass, `coroutines`, after inlining and before register allocation**, splits each generator:
   - which values are live across each `yield` (SSA liveness over the IR);
   - a record type holding them, the state number, and the arguments;
   - a *resume* function: load the state, a many-way branch to the block after each `yield` (and to the start, and to a finished
     block), reload the live values, run, and at each `yield` store what is live, store the next state and return the value as a
     successful `T?`;
   - an *abandon* entry that runs the `defer`s pending at each stop;
   - the *make* function the call site calls: store the arguments, state 0.
3. **The back ends need nothing new.**  A resume function is a function; a frame record is storage; the jump table exists.

The pass is the size of the tail-call pass and the many-way branch together, and it is the whole of the risk.


Iterators as coroutines
-----------------------

This is the comparison asked for.  Four ways a `foreach` over a sequence can be compiled, and what each gains and costs.  The
measurements of the first are the timing samples the suite already has; the rest are costed by what they execute per element,
since nothing is built yet -- every claim below is a prediction to be checked against `bin/pl4g-timing` and a benchmark when a
prototype exists.

### A. What there is now: the compiler is the iterator

For each kind it knows, the compiler lowers *test*, *take* and *step* straight into the loop: a range is a comparison and an add, an
array a comparison, a load and an add, a pick one test more.  What the loop carries is one or two registers.

- **Per element**: two to four instructions, all in registers.  No call, no memory but the element.
- **What the optimizer sees**: everything -- the bound, the stride, the element -- which is what lets later work (unrolling, the
  vectorizing this compiler does not do yet) apply.
- **What it cannot do**: anything a program defines.  The set is closed.

### B. A coroutine resumed once per element

A `foreach` over a `⇉T` whose body the compiler does not see -- one handed in as a parameter, say -- calls the resume function each
turn.

- **Per element**: an indirect call and a return (the code is the first of the three words); in the resume function a load of the
  state and a jump through the table; a load of each value live across the `yield` and a store of each at the next one; the
  element handed back as a result -- two registers -- and a test of it in the loop.  Ten to twenty instructions plus two per live
  value, where A has three.  All of it predictable: the same state each time in a simple generator, so the table jump and the
  return predict well.
- **In memory**: every value live across the `yield` makes a round trip through the frame per element.  In A the loop counter is a
  register for the whole loop; here it is stored and loaded every turn.
- **What the optimizer sees**: nothing across the call -- the loop cannot be unrolled or vectorized, a pure computation in the body
  cannot be hoisted past the resume.
- **Where the cost is known**: an indirect call per element is what Go 1.23's range-over-func costs without inlining and what the
  [attaching code](attaching-code.md) design H was turned down for.  It is the price of not knowing the body.

### C. A coroutine resumed directly, and inlined

Where the compiler sees which generator was called -- `foreach x := evens(n):`, the case almost every loop is -- the call is direct,
the frame is in the loop's own frame, and the resume can be inlined into the loop.

- Once inlined, the state number is a value the loop carries, the many-way branch is a branch on it that constant propagation can
  often resolve (a generator with one `yield` inside one loop has one state after the first turn), and the live values the frame
  held are block parameters the register allocator keeps in registers -- the frame's loads and stores fold away, because nothing
  else can see the frame.
- **Per element**: for a generator with one `yield` in a loop, what A costs plus one test of the state, if the folding works.
  Rust's generators and iterator chains reach hand-written speed by exactly this route -- the state machine is a value of known
  type, everything is inlined, LLVM does the rest; C++20 coroutines reach it less reliably, because the frame starts on the heap and
  has to be proven not to escape first.
- **What it needs**: the inliner to inline a resume function into a loop, and constant folding over the state.  Both exist; neither
  has been asked to do this.

### D. A coroutine written into the loop: `yield` becomes the body

The strongest form, and one stackless coroutines make possible.  For `foreach x := g(args): BODY` where `g` is a generator the
compiler sees, **lower the generator's body in place and put `BODY` where each `yield v` is**, with `x` bound to `v`.  There is no
coroutine left: no frame, no state, no resume.

- **Per element**: exactly what the generator's own loop costs, plus the body.  `foreach x := evens(n):` becomes the range loop it
  was written over, with the test inside -- what A is for a range, by construction.
- **`break` and `continue`** in `BODY` become jumps out of the inlined generator -- to after it, running the generator's pending
  `defer`s on the way, which is the abandon path made into ordinary code -- and to the instruction after the `yield`.
- **Costs**: the generator's body is copied into every loop that walks it, as an inlined function is.  A generator with several
  `yield`s copies `BODY` several times -- a body of a hundred instructions and a generator with four yields is four hundred.  It
  does not apply to a coroutine handed in as a value, nor to a recursive one.
- **Precedent**: Ruby's blocks and Kotlin's `inline` higher-order functions do exactly this -- the loop body is substituted where
  the iterator calls it; Go 1.23's range-over-func reaches it when the compiler inlines the iterator; Zig's `inline for`; Python and
  C# never do, and pay a call per element always.  It is also what design H in [attaching code](attaching-code.md) would have
  needed and did not have: a lambda per element is an indirect call unless its body can be put in place, and here it can.

### E. Stackful, for completeness

Two stack switches per element -- out with the value, back for the next -- each saving and restoring every callee-saved register:
fifty to a hundred instructions per element on AArch64 and RISC-V, half that on x86-64, and nothing across a switch inlined.  Making
one is a mapping.  What it buys over C is a recursive walk with no delegation: an element deep in a tree comes out in one switch
where the stackless walk resumes once per level.  For iterators, that is the only case it wins.

### Side by side

| | A (now) | B (resumed, unseen) | C (resumed, inlined) | D (written into the loop) | E (stackful) |
|---|---|---|---|---|---|
| Who can write one | the compiler | a program | a program | a program | a program |
| Work per element, beyond the body | 2–4 instructions | ~10–20 + 2 per live value, an indirect call | ~A + a state test, if folding works | = the generator's own loop | 2 stack switches |
| Memory per element | registers | frame round trip per live value | registers | registers | registers and both stacks |
| Making one | nothing | a few stores, frame placed | a few stores | nothing | `mmap` + `mprotect` |
| Frame size | — | known | known | none | a stack, guessed |
| Optimizer sees through | everything | nothing | everything | everything | nothing |
| Code size | small | one resume function | one per inlining | the body copied per `yield` | one function |
| Handed in as a value | — | yes | no | no | yes |
| Recursive | — | with an arena, *O(depth)* per element | with an arena, *O(depth)* | no | yes, *O(1)* |

### What building iterators as coroutines buys

- **Iterators a program can write**, which is the hole there is: a tree, a parser, a lazy pipeline, a sequence with no end.  And
  written as the loop they are rather than as a record and a state number -- which for a language written by generators means the
  emitter writes the control flow, not a state machine encoding it.
- **One mechanism instead of a protocol.**  The alternative for user iterators is the `T.next` protocol of attaching code: a type,
  a record of state, a `next` that answers `T?`.  A generator is that, written by the compiler.  Both can exist -- a `⇉T` is a
  thing with a `next` -- and the protocol stays the way to make a type *also* walkable.
- **Composition**: a generator that walks another and yields some of it is a filter; one that yields from two in turn is a merge.
  Each is a few lines, where a hand-written `next` for a merge is a page.

### What it costs

- **The built-in iterators should not become coroutines.**  A, C and D cost the same for a range or an array only when the folding
  works perfectly; when it does not, B costs five times what A does per element.  The compiler knows what a range is and gains
  nothing by forgetting it.  So: **the seven built-in kinds stay as they are, and a coroutine is the eighth.**
- **A coroutine handed in as a value is a call per element** (B).  That is the honest cost of not knowing the body, the same cost a
  lambda called in a loop has, and it is in the program for a reader to see: a parameter of type `⇉T`.
- **Code size, under D.**  Every loop over a generator gets a copy of the generator.  The inliner's existing size limits apply, and
  past them C is the fallback; the report log says which was done for each loop, as it says where each lambda's environment went.
- **A pass that is all corner cases** -- liveness across a `yield` inside a loop inside a `match`, the abandon path through nested
  `defer`s, a generator with no `yield` at all.  Zig's removal of its stackless `async` is the reminder.

### Recommendation for iterators

**Keep A for the built-in kinds; make every `foreach` over a generator call it can see D where the body is small enough, C where
it is not, and B for a coroutine handed in as a value; report which.**  A program then pays for a coroutine exactly where it asked
for one that the compiler cannot see through, and nowhere else.


### A worked case: walking what a mask picks

The comparison above is in general terms.  Picking with a mask is the case where the two can be set side by side instruction by
instruction, because the compiler's own iterator for it exists and the coroutine is three lines:

```
fn picked(v: u8⟦⟧, m: bool⟦⟧) ⇉ u8:                  ※ the coroutine
    foreach i: u64 ¤idx = 0…⎕unit(⎕drop(#v), ⌜idx⌝):
        if m⟦i⟧:
            yield v⟦i⟧

foreach x := v⟦m⟧:                                     ※ the compiler's iterator (A)
foreach x := picked(v, m):                             ※ the coroutine, walked
```

**What A compiles to today** -- x86-64, `-O1`, a loop adding with `⊞`, measured from `--emit=asm`.  Before the loop: the two
lengths compared once, and the program stopped if they differ.  Per element: the test (`cmp`, `jb`), the mask byte read and tested
(eight instructions), and where it picks, the element read and added (sixteen).  **14 instructions for a place not picked, 26 for one
picked.**

**What D compiles to** -- the coroutine written into the loop, which is what the loop above is when written out by hand, so the code
can be measured today.  Per element: the test; `m⟦i⟧` checked against the mask's length and read; where it picks, `v⟦i⟧` checked
against the array's length and read; the add; and the range's step, which saturates.  **25 for a place not picked, 41 for one
picked.**  The difference from A is not the coroutine at all.  It is two bounds checks per element that A does not need -- A
compares the lengths once, before the loop, where the coroutine indexes each array on its own -- and a range step that saturates
where A's counter cannot overflow.

**What B would cost** -- the coroutine handed in as a value and resumed once per element.  A place not picked costs what it costs in
D: the coroutine's own loop steps past it without returning, which is the one way a coroutine is *cheaper* per element than an
iterator that answers every place.  A place picked adds the resume: a call and a return, the state loaded and dispatched, the five
words the loop needs (`i`, and each array's place and length) reloaded from the frame, `i` and the state stored back, and the
element answered as a result and tested -- about twenty instructions.  **25 for a place not picked, about 60 for one picked**, an
estimate until there is a pass to measure.

**What E would cost** -- stackful: B's work without the reloads, plus two switches each saving and restoring seven registers on
x86-64.  **About 25 and 75.**

| Per element, x86-64 | not picked | picked | measured |
|---|---|---|---|
| A, the compiler's iterator | 14 | 26 | yes |
| D, the coroutine written into the loop | 25 | 41 | yes, as the equivalent loop |
| B, the coroutine resumed through a value | 25 | ~60 | estimated |
| E, a stackful coroutine | 25 | ~75 | estimated |

**Where they differ in what they do, not only in what they cost:**

- **A wrong shape stops the program at a different moment.**  A compares the lengths before the first turn, so a mask of the wrong
  length runs no turn at all.  The coroutine finds it at the first `m⟦i⟧` past the mask's end -- after the turns before it have
  run and done whatever they do.  A `pre(#m = #v)` on the generator would restore A's behaviour, checked when the coroutine is
  made; the per-element checks stay unless the compiler learns that the `pre` implies them.
- **A mask worked out a place at a time has no coroutine counterpart.**  `foreach p := ps⟦ps¨.age ≥ 18u8⟧` and
  `foreach x := v⟦v > k⟧` build no mask: A asks each place as it reaches it.  A coroutine takes its mask as an argument, so the mask
  has to be built first -- room in the frame for one truth value per element, and the array's length stated, which is the
  restriction A lifted.  Handing the coroutine a predicate instead, `fn kept(v: u8⟦⟧, keep: fn(u8) → bool) ⇉ u8`, costs a call
  through a function value at every place, picked or not, unless D puts both the coroutine and the lambda in place.
- **A is the only one that reads the shape.**  A mask over a table's rows gives rows by arithmetic on the place; a coroutine can
  yield a row as well, but has to be written for each rank, where A is one rule for all of them.

**What this says about the recommendation:** keeping A for picking was right, and the gap between A and D is not the price of
coroutines.  It is three optimizations this compiler does not do yet: removing a bounds check the loop's own test already
implies, not saturating a step that cannot overflow, and reading a value once where it is read twice.  With them, D comes to within
a few instructions of A, which is what *D written into the loop* promises.  The same measurements found missed work in A itself --
a multiplication by one for every element address, a length compared with itself, and the element of `v⟦v > k⟧` read twice
(once for the mask, once for the turn) -- all now in `TODO-pypl4g.md`.

**After those six were done** (2026-10-03: `samevalues`, `facts`, two folds and a check that branches on its comparison), A is
**11 instructions for a place not picked and 20 for one picked**, and D is **17 and 25**.  What D still pays over A is the check of
`m⟦i⟧` against the mask's own length, which nothing in the loop settles -- A compared the two lengths once, before it, where the
coroutine's body does not -- and the join after its `if`.  A `pre(#m = #v)` on the generator, used as a fact, would take the first.


The order to build it in
------------------------

1. Generators: `⇉`, `yield`, `⇉T`, `⎕next`, `foreach` over one; the frame placed by the lambda rule; the `coroutines` pass (B);
   no `defer` in one that leaves; `yield ⇉` without symmetric transfer.  The language test suite covers each on all three targets.
2. D: `yield` written into the loop, for generators called where they are walked, under a size limit.  Then C, by letting the
   inliner take a resume function.
3. `defer` in a coroutine that leaves, once `⎕empty` and scope exit can run an abandon path.
4. Two-way coroutines -- `yield` answering what the next resume hands in -- and on them a `std` scheduler over the `io_uring` ring:
   a task stops at "wait for this completion" and a loop resumes whichever task's completion arrived.  That is a separate proposal;
   nothing in 1–3 closes it off, and stackful coroutines are reconsidered there if anywhere.


Questions for the user
----------------------

1. **Stackless or stackful.**  Recommended: stackless, for the reasons in *The first choice*; stackful revisited only for a scheduler
   of long-lived tasks.
2. **How a generator is written.**
   - `fn evens(n: u64) ⇉ u64:` with `yield` in the body (recommended): the signature says it, which is what a reader and a tool see
     first; the glyph is free.
   - `fn evens(n: u64) → ⇉u64:` -- the answer is a coroutine, written as a type.  More regular (the arrow means what it always
     means) and one glyph more; C++'s `std::generator<T>` as a return type is this.
   - A keyword, `gen fn` (Rust's proposed `gen` blocks and functions) -- a word where the language uses glyphs.
   - Inferred from a `yield` in the body (Python, JavaScript needs `function*`) -- the signature then lies about what a call does.
3. **What a finished coroutine does when resumed again**: answer the failure again (recommended, Python and fused Rust) or stop the
   program (C++ leaves it undefined; the cursor stops).
4. **`defer` in a coroutine whose frame leaves**: refused in the first version (recommended) or run when its arena is emptied.
5. **Errors**: a generator yields results and `?` yields the failure and finishes (recommended), or a generator has a separate
   error type that `⎕next` answers alongside the end.
6. **Recursion**: a generator that makes one of its own kind says which arena the inner frame is in (recommended, Rust's `Box::pin`),
   or every recursive one goes in `⎕heap` without being told.
7. **D, writing the generator into the loop**: wanted (recommended), with the inliner's size limit and a report, or left out so a
   coroutine is always a coroutine and code size is predictable.
