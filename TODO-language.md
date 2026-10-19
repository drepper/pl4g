To Do List for the PL4g language
================================

`[ ]` open, `[x]` done, `[?]` needs a decision before it can be started -- such an entry carries a
`Question:` paragraph saying what is undecided and what the choices are.  `[-]` no longer applies: what it asked about was
removed or decided otherwise, which its last paragraph says.

[x] expressions can use `(` and `)` for grouping, like most other languages.  Done with the expression parser.

[x] there is no way to call a function.  Done: `f(a, b)`, positional, binding tighter than every operator and to whatever stands
    immediately before it.  A call hands over exactly what the function takes and each argument has the type of its parameter
    (4211, 4213); only a function can be called (4210); a call to a function that answers with nothing has no answer to use
    (4212), except as a statement of its own and after `return` in a function that also answers with nothing.
    Still open, and the only part of the original question that was: whether arguments may also be named, as an attribute's are.
    Nothing in what was built forecloses it.

[x] let a function say that its answer must not be dropped.  Done the other way round, on the user's direction: a call's answer
    must be taken by default (4474) and `@[can_ignore]` on the function says a caller need not take it.  Rust's `#[must_use]` and
    C++'s `[[nodiscard]]` default to silence because they are written by people; a generator that emits a call and drops its
    answer has a defect, so the rule is on for every function.  `_ ← f()` drops one call's answer, `_` being a place a value goes
    to and not a variable: no definition (4476), no reading (4475), and something with a value to drop (4477).

[x] the conditional half of the control flow: `if`, `elif` and `else`.  Done: a condition with no parentheses around it, which
    must be a `bool` -- a number is not a condition -- then a body in either notation, zero or more `elif` with the same shape and
    an optional `else`, which is the last arm there is.  It produces a value where one is wanted of it, and then needs an `else`.
    It needed no new machinery: the block the arms join at, the names it carries across, the memory token it merges and the value
    it hands out were all built for `match`.

[x] add tuple types.  Done: `〈a, b〉` is a tuple and `〈T, T〉` the type of one, and names written next to
    each other take one apart in a definition or an assignment.  In registers a tuple is one register per *leaf*, which is the
    result type's arrangement generalized: a member that is itself several values is that many registers, since what a register
    holds is one value.

[ ] what else a build may say.  Today a build describes executables, each built from a run of sources with the settings the
    object carries, and that is all: no library, no test artifact, no artifact that depends on another, no step to run by name
    (`pypl4g build test`), no per-artifact settings, and no installing anywhere.  Each of them is a field or a function on
    `std.Build` and an entry in the plan; what they want first is a way to name an artifact that has already been added, which is
    a value the build function holds rather than a name it repeats.

[ ] a growing run of records in the language, which is what would make `add_executable` an ordinary function rather than one the
    compiler provides.  It wants the heap and a list type whose elements are records, both of which exist for values that are not
    records; what is missing is the one that would let a build object hold its own artifacts.
    **Out of date (2026-10-04):** Lists of records exist now (`[Job]`, joined with `⧺`); what is still missing is copying a record
    holding text into a container (4623), and `add_executable` is still built in.

[ ] let a build function raise the ceiling on what the compiler will work out.  A hundred thousand steps is beyond anything a
    person writes and is not beyond what a generator writes; Zig has `@setEvalBranchQuota` for exactly this.

[ ] a documentation tool that reads what the comments say.  The parts of a documentation comment are read and checked (4600 to
    4604) and shown by the language server; nothing writes them out as a manual.  What it would need is already there -- the
    summary, the parameters, what is answered with -- and what it would have to decide is what a page looks like.

[x] answer with a tuple that wants more registers than the convention has.  Refused today (8501): a tuple is one register per
    member and these conventions answer in two per kind, so a tuple of three integers cannot be answered with.  What every ABI
    does instead is hand the callee a place to put it, which needs the caller to reserve one -- so it waits on holding a value in
    memory at all, which the entry in TODO-pypl4g.md carries.
    Found done on 2026-10-04: tests/language/tuple-answered-through-storage answers a five-member tuple through the caller's
    storage, and a result of several values goes the same way (result-of-several-values).

[x] add sets and dictionaries with Python's semantics and a lookup that does not grow with what is in the collection.  The front
    end is done: `⸨a, b⸩` is a set and `⸨k: v⸩` a dictionary, a type is written the same way, a lookup in a set answers with a
    `bool` and one in a dictionary with a result, `d⸨k⸩ ← v` puts a value under a key, and `|`, `&`, `^` and `-` join two sets.
    A key is a type `=` answers exactly -- integers, `bool`, enumerations -- and floating point is refused.  Nothing builds one:
    see the entry below.

[x] build a set and a dictionary at run time.  Done: open addressing with linear probing, a power-of-two capacity, growth at
    three quarters full, and Fibonacci hashing -- one multiplication, with the high bits folded down.  The table is a block that
    never moves, holding the arena it came out of, the mask, the count, the stride and where the entries are; the entries are a
    separate allocation so that growing does not move the block.  A collection is where its block is and nothing else, which is
    one word.  The three operations are generated as functions of the representation, which is the first thing the compiler
    generates for itself.
    A collection names the arena it comes out of, with `in`, and `⎕heap` is what one that says nothing comes out of.  A collection
    is shared and not copied when it is assigned: it is a handle, and copying it is what a `copy` written out would be for.

[x] take a key out of a collection.  Done on 2026-09-29 at the user's direction: `†d⸨k⸩` takes the key out and answers what the
    lookup it is written before would have answered.  An entry a key left is *given up* rather than emptied -- a probe walks past
    it and an insertion takes the first one it walked past -- and the table carries a second count, of what a probe must walk
    past, so that one crowded by entries given up is rebuilt at the size it has and only one crowded by keys is doubled.

[x] a dictionary whose value is of any type, and a key that is text.  Done on 2026-09-28 at the user's direction, and the two
    together: an entry is now shaped by what the table holds -- the key's words, then the value's -- and the three functions that
    depend on that shape are generated once per shape and named for it.  A key that is text is hashed with FNV-1a over its bytes
    and compared with the walk `=` already uses.  What the key's restriction bought (one table for every instantiation) is
    therefore gone, and what it cost is what it bought: a program that keys a dictionary by text and another by a number carries
    two probes.

[x] a hash a program cannot pick its keys to collide in.  FNV-1a is what the bytes of a key are hashed with and it is not
    keyed: a program fed keys chosen to land in one place degrades to a walk.  Every language that takes input from outside
    seeds its hash per run (Python, Go, Rust); this wants somewhere for the seed to live and a way for a program that wants a
    table it can reproduce to say so.  Done on 2026-10-18: rapidhash with a seed each table carries, taken from the kernel's
    random bytes or from `PL4G_HASH_SEED`.

[x] a collection at the top level.  Done on 2026-09-29: the front end generates one constructor per file that has any, in front
    of the program's own, and the variable holds a word of nought until it has run.

[x] there is no repetition.  Answered by the user: both a general `while` and an iteration over something, which is `foreach`.
    `while COND:` is done -- a statement, not an expression, with the same block rules as `if` and a condition that has to be a
    truth value (4437).  A name a turn changes is the loop's own parameter, so the value one turn leaves is the value the next
    one reads.  What it cost was in the backend rather than in the syntax: liveness that follows the graph, and a branch's
    arguments passed as a parallel copy.

[x] `foreach`, iterators and ranges.  Done: `foreach` shares `let`'s shape, and `while` written with a binding is the same
    statement.  An iterator is a value with a `next` answering the next value or a failure, and the failure is what ends the
    loop; the result type does not surface.  A range is the only thing that is one, written `A…B` or `A…B…C` with Python's
    meaning, and its `next` is lowered where it is asked rather than called -- a comparison against the end and an addition.
    `_` as the name binds nothing, as it does in a `match` arm.

[x] pre- and post-conditions on a function, with a constraint as one of them.  Done on 2026-09-30, together with the entry
    below, which is the same feature: one clause form, `pre(EXPR)` and `post(EXPR)`, and what it means is decided by whether the
    expression is over values or over types.  Over values it is a condition -- evaluated, a `bool`, pure whatever the function is,
    checked at the top of the body for a `pre` and before every return for a `post` with `⎕answer` standing for what was answered,
    and exiting 89 or 90 where it fails.  Over types it is a requirement -- nothing evaluated, and what is asked is that the
    expression can be written, which is checked by lowering it with one value of each type operand in scope into a function nothing
    will emit.  A bundle names a set of requirements and is asked for by applying it in a clause; `needs` does not exist.  A
    condition the checker settles is an error where it cannot hold and free where it must, and the folder drops a check that folded
    to a truth.  Eight refusals, seventeen language tests, two optimizer tests.
    `⎕entry(NAME)` in a `post` is what a parameter held at entry, for a condition about a parameter the body has since bound to
    something else.  It takes a parameter's name and nothing else, which is what makes it free: a parameter arrives in the entry
    block and SSA keeps it, so nothing is copied and what it costs is one live range.  `--conditions=check|ignore|observe` is what
    a build does about a condition, which is g++'s `-fcontract-evaluation-semantic`; a requirement is not among the choices, and
    every semantic asks the same questions of the clause, so nothing compiles only because the checks are off.
    Left over: `⎕old` over an arbitrary expression, which wants a copy and a decision about who sees its cost; `assume` as a fourth
    semantic, which is the only one that can make a program go wrong silently and which this language has nothing like anywhere
    else; levels on a clause, g++'s `default`/`audit`/`axiom`, which are a second axis and want an attribute on a clause.
    Left over: a compilation error for a condition the *optimizer* sees cannot hold -- the optimizer has no channel for a
    diagnostic about the language, so only what the checker settles is reported, which is a literal falsehood, a question about
    types, and what the checker folds.  That wants a diagnostics channel in the pass manager and is a compiler entry, not a
    language one.

[x] a language for constraining a generic.  Done on 2026-09-30 as the entry above: a bound is a `pre` clause whose expression is
    over types, so `needs A'⟦I'⟧ → E'` is written `pre(A'⟦I'⟧ → E')` and there is nothing to learn twice.  Layer 1 and
    layer 2 of [spec/constraining-generics.md](spec/constraining-generics.md) are in: the error is reported at the *call* rather
    than in the definition, and bundles are pure abbreviations with no conformance rule and no coherence.  A bound is a signature
    and not a name, as that document argued, and the expression notation says it with the operators and positions the expression
    grammar already has.  A bundle may be imported with `@[export]` and applied through the name the module was bound to, and its
    lines are read with the names its own file can see -- so it may require an operation the applying file cannot name, and a
    function of the same name there does not change what it asks for.
    Left over: **layer 3**, the body checked once against what it asked for, which is the valuable half and the one that makes
    programs that compile today stop compiling.  Nothing yet says a generic body may use only the operations its requirements
    name, so a body is still checked per instantiation; the requirement is what a caller is told.  That is a tightening of the
    same syntax and wants its own decision about how much it breaks.

[~] a macro system.  **Both forms are done**, on 2026-09-30.  [spec/macros.md](spec/macros.md) took the design a related language settled on and asked
    what it becomes here: two forms -- a list of rewrite rules and a function over the program's text -- a parse tree handed over
    rather than a value, expansion between parsing and checking, hygiene by renaming with `#`, and every piece keeping the position
    it was written at.  Most of it transfers unchanged.
    Three of the original's four notations are already here under other names, which is the substance of the adjustment: the
    invocation is `f⌜…⌝` because `⟦⟧` is indexing, an array literal *and* a definable bracket pair, and because the lifting marks
    already mean "handed over as written rather than as what it evaluates to" -- which is the definition of a macro.  A quote is the
    same marks, so the invocation is a name applied to a quote and is not a notation of its own.  A reference is the same marks
    again, `⌜×⌝` for the operator, because `※` begins a comment here.  One keyword `macro` for both forms, told apart by whether a
    parameter list follows, which is the device a bundle's body and an operator's arity already are.  No methods yet, so the five
    questions are `⎕kind`, `⎕name`, `⎕head`, `⎕parts` and `⎕apply`, and `⎕kind` answers an enumeration rather than a string, which a
    `match` is then checked for covering.
    **And pl4g has the answer the original leaves open**: a name a macro *reads* resolves where the macro was written, which is the
    decision already made for a bundle's lines, for the same reason and with the same implementation.
    What landed for the rules form: the notation, a structural matcher, hygiene, a stage between parse and check with
    `--emit=expanded`, and twelve language tests.  Four things came out differently from the estimate and are written up in that
    document -- a template assigning to a hole needed a node of its own, a macro of no arguments had to be allowed, the refusal of
    the function form belonged to the parser and so to the syntax block, and a failed expansion has to stop the compilation before
    the checker sees what the macro could not write.
    What landed for the function form: `syntax` as a compile-time-only type, `⎕head`, `⎕parts` and `⎕part`, `=` between two pieces,
    `$(EXPR)`, `comptime fn`, and six more language tests.  **The estimate's one big item was the wrong shape**: rather than an
    interpreter over the AST -- a second implementation of the language -- the macros are checked and lowered into a module of their
    own by the same code the program goes through, and the IR is interpreted by 400 lines holding no language knowledge at all.  So
    every rule the language has applies to a macro because the same code applied it.
    Left over: **`⎕kind`, `⎕name` and `⎕apply`**, which have a type in the compiler and no lowering -- what they should answer is
    better settled by a macro that wants them than by guessing.  An array of `syntax`, which the design leaves out on purpose since
    `⎕part` exists so that a macro need not hold the parts at once.  Whether a macro may write a *definition*, which is what the
    feature is most worth and what would force expansion before the definitions are collected rather than merely before they are
    checked; the "nine operators from one line" case wants exactly that.  Following a reference to what it names, which expansion
    running before the definitions are installed leaves nothing to ask.  And whether a macro may be exported, which bundles answered
    with `@[export]`.
    **Out of date (2026-10-04):** Exporting a macro and `⎕name` are done (macro-imported, macro-reads-a-literal); `⎕kind` answers
    a number rather than the enumeration the spec wants; `⎕apply`, macros writing definitions and following references are still
    open.

[ ] turn a value into text, and format.  **Decided** on 2026-10-01, nothing implemented;
    [spec/formatting.md](spec/formatting.md) has the proposal and
    [spec/decisions.md](spec/decisions.md) the entry.  `⍕` (U+2355, APL's format
    primitive) is a value's text, an operator like any other and so dispatched by the
    operand's type -- which the language forces, having no overloading.  `std.format` is
    a macro invoked with the marks, `std.print` the same macro writing instead of
    joining, and the template holds `{}` counted against the arguments and `{name}`
    naming something in scope.
    In dependency order, and each of the first two is a gap in something that already
    landed rather than new work:
    [x] **an exported operator is in force where its module is imported.**  Done: an
        operator a module exports is written without naming the module, two modules
        exporting one glyph for one number of operands are refused at the second import
        (4924), and a definition in the importing file wins silently.  An operator a
        module does not export cannot be told from one it never wrote, there being no
        name to ask about, which is where this differs from a bundle.
    [x] **a macro may be exported and invoked through a module's name.**  Measured:
        `m.twice⌜3u8⌝` does not parse, and `@[export]` on a macro crashes the compiler
        with 9901 from the module loader.  The macros entry left this open; `std.format`
        closes it, and the internal error is a bug either way.
        Found done on 2026-10-04: `@[export] macro format` in `std`, invoked as `std.format⌜…⌝`, and
        tests/language/macro-imported.
    [x] **a generic function may be exported.**  Done, and it was three things: a
        `_Generic` had no export flag at all, so `@[export]` on one was silently ignored
        and a module could ship no generic function whatever; an instance made for an
        importing file now resolves its body where the function was written, which is the
        bundle rule; and a generic is now reached through a module's name.  With it went
        the larger question of how a type says what an operator means for it: **not by
        overloading** -- one generic definition tests the type with `comptime if`, and a
        program overrides by writing the definition for its own file and handing the rest
        back to the module's under its name.
    [x] **`⍕` for the built-in types**, in `std`.  Done: `std.text` and `std.⍕`, one body
        under two names, covering `str`, `bool`, every integer type and -- by reflection
        over its fields -- any record, recursively, in the notation a program writes one
        in.  Nothing in the number arm names a width, and nothing is ever negated, so the
        smallest value a signed type has comes out right.
        A `char` is covered too, `⎕str` being what makes a string of one.
        Left over: a floating-point number, whose shortest round-trip text is an algorithm
        of its own; and an enumeration, whose names do not reach run time.
    [x] **reflection over a record.**  Done: `⎕isrecord(⌜T⌝)`, a truth the compiler
        settles and so the first predicate a compile-time condition admits;
        `⎕typename(⌜T⌝)`, the type written out; and `⎕fields(v)`, the fields as a tuple of
        pairs in declaration order, walked by `comptime foreach`.  A generic definition can
        now write the text of a type it was never told about, recursively and in the
        language's own notation.  It also found a bug: `comptime foreach` took one part per
        member, which is wrong for a member that is several values to the machine.
        Left over: a general question naming *which* kind of type something is, which wants
        a compile-time condition comparing something other than two types; and reflection
        over a sum, which would have to answer which part is held first.
    [x] **the five pieces the macro machine needs** to read a template.  Done: globals
        materialized into its memory, `__pl4g_alloc` as a native bump, `⎕name` of a
        literal answering its text, `$(EXPR)` taking text, and `⎕refuse` for a macro's own
        message.  With them went `⎕str(CHAR)`, a string of one character, which is how a
        run between two holes is built and which closed one of `⍕`'s three gaps.
    [x] **`std.format`, `std.print` and `std.println`**, all three macros over one
        `comptime fn`.  Four things landed under them, three of them gaps: a macro may be
        exported and invoked as `m.f⌜…⌝`; a name a macro reads now really does mean what
        it means where the macro was written, which both documents claimed and neither
        form implemented; and `⁂` before a macro's last parameter makes it stand for all
        the remaining arguments, so a macro is variadic without the language being.
    [x] **`{name}` in a template.**  Done on 2026-10-18: `{name}` is `⍕` of what the name
        means at the caller, mixing with `{}`, which alone is counted; only a name, never an
        expression.  The exception to hygiene is `⎕caller_name(TEXT)`.
    [x] **a width and a base.**  Done on 2026-10-18: `std.text(v, .base ← 16u8, .upper ←
        true)`, and `std.padded(v, .width ← 8u64, .fill ← '0', .align ← std.Align.right)`,
        a width counting characters and noughts going after a sign.  A call in a hole takes
        the template's arena: an `&mut arena` a call leaves out is the one in force.
        Left over: **a precision**, which waits for floating-point text.
    [x] **a pool allocator for the formatting.**  Done: `EXPR in NAME` says which arena
        an expression takes room from, `⎕empty(NAME)` gives the whole of one back, `⎕arena`
        works inside a function, `std.text` takes a reference to the arena, `⍕v in a` is the
        operator form, and `print`/`println` build in a pool of the expansion's own and give
        it back with `defer` once the write has finished.  So a program that prints a
        formatted line declares nothing.
    [x] **memory as a capability.**  Done with the pool above and by the same rule:
        taking room from any arena, `⎕heap` included and named or not, is no effect at all:
        what is made is new.  `@[impure]` is left for changing what somebody else holds.
        And what an arena holds dies with it: read after `⎕empty`, answered by the function
        that made the arena, or put where it outlives it, is refused (4613-4615); only the
        maker empties an arena (4616); a pure function gives back all it made (4617).
    [x] **a signature saying which arena its answer was made in.**  Done: `→ T in a, b`
        and `s: T in a` (4618-4621); `std.text` and two-operand `⍕` say it.
    [x] **every object knows how it is given back.**  Done: `str` and lists carry their
        allocator, `⎕heap` frees one object by size, answers are copied into the allocator
        their signature names (default `⎕heap`), pool containers never free elements, heap
        containers own what they hold (4622-4626).
    [x] **handing an arena out with what it made.**  Done: `→ T in pool`, received with
        `let v: T in kept = f(…)` (4627-4631), for functions and lambdas.
    [ ] **giving back what a name held when the name goes**: a value of the heap's no
        container owns lives until the program ends.  Wants ownership of locals -- a move
        out of a name, and a drop where the last one goes.
    [ ] **sets and dictionaries holding text** are not yet held to the element rules: their
        keys and values go in as they are, and nothing is given back.
    [ ] **copying records, tuples and nested arrays into an allocator** (4623), which a container
        of them needs.  Records and tuples done on 2026-10-19, part by part, with the list growing; a sum and an array
        held inside something are what is left.
    [x] **a top-level array of text** is refused (9902), which is what a table of literal text
        such as `std.text`'s digits would want.
        Found done on 2026-10-04: `in ⎕static` (2026-10-02T20:00): `std.text`'s digits are a `str⟦10⟧ … in ⎕static`, and
        tests/language/static-data.
    [ ] **tail calls between functions**: a cycle `f` → `g` → `f` takes stack; the guarantee
        covers a function calling itself.  Wants frames and arguments arranged alike across
        the cycle.
    [ ] **`in` per component** -- `(str in a, str in b)`, or a record field holding text made
        in an arena -- which wants `⧖` names on types that are not references.
    [-] **a type `T⟦3,⟧` that states some dimensions and not others** is accepted, though
        the spec says every dimension says how many or none does; nothing refuses it yet.
        No longer applies (2026-10-04): decided the other way: a shape may be stated in part (spec.md, "A shape may be stated in
        part"; tests/language/array-shape-told-in-part).
    [ ] **`errdefer`**, Zig's defer that runs only on the way out with a failure, once
        something wants it.
    Left over and named in the proposal: float text, which is the large piece; whether an
    enumeration's names reach run time; the dyadic `⍕`, which is APL's
    format-by-specification and which nothing yet needs; and a chain of joins folded into
    one allocation, which every join wants and which is measured at three allocations for
    four pieces today.
    **Out of date (2026-10-04):** "Nothing implemented" is no longer true: `⍕`, `std.text`, `std.format`, `print`, `println` and
    their pools are done (std-format, print-and-println, std-text, format-in-a-pool); what is left is in the items below.

[x] let a program write an iterator.  Done on 2026-10-18 at the user's direction: a program writes a walk as a generator, and
    attaches one to a type as `fn T.each(v: T) ⇉ E`, which `foreach x := v` walks -- and so does everything else that wants a
    walk: a `⇉E` parameter, `let r: ⇉ = v`, `⎕enumerate`, `yield ⇉ v`, `⎕start`, and `v in a`.  Nothing else is asked of the
    type.  The operator protocol the entry used to weigh (`⇧`, `⌖`, a test for being over) is not needed for it.
    Found and fixed on the way: two generators' frames compared equal, so a walk handed on in one step without `in` wrote
    past its description and crashed; and a generator walking itself through a value's `each` was not seen as one.
    Left over:
    [x] **an `each` of a type with type parameters.**  Done on 2026-10-18: `fn Two.each(t: Two(T'), a: &mut arena) ⇉ T'`,
        compiled per instance walked.  With it, `v.f(args)` on a value of such a type calls the function written under its
        definition, which no call through a value could reach before.
    [x] **the arena of an `each`.**  Done on 2026-10-18 at the user's direction: every `each` takes `a: &mut arena` second
        (4926), and the loop hands it the arena in force -- the `in` the walk is written with, or `⎕heap`.  A generator's
        call leaves out a trailing arena as any call may.
    [ ] **named arguments and defaults for a generator's call** (`_generator_arguments`), as an ordinary generic call now
        takes them.
    [ ] **`foreach` over a cursor**, `⎕iter(l)`, still refused (4438).  Nothing wants it now that a walk is written as a
        generator, but it is the compiler's own iterator and walking one from where it is would be the obvious meaning.

[ ] let a result's answer be any type.  `u8 ? ⎕narrowing` is written today and `〈u8, u8〉 ? ⎕narrowing` is not: the `?` is read
    only after a named type, in `_parse_named_type` and in the grammar's `_plain_type` alike, so a tuple, a list, a set or an
    array cannot be the answer of one.  Nothing in the representation minds -- `parts_of` spreads a result's answer out whatever
    it is, and a tuple holding a result already works -- so this is the type grammar and nothing else.  What wants it first is a
    function that answers several values and may fail, which is the shape a pure iterator's `next` has.
    **Out of date (2026-10-04):** An array may be a result's answer now (`u8⟦⟧ ? i32`), and strings and records compile
    (result-of-several-values); tuples, lists and sets still cannot take `?`, and generators have made the pure `next` less
    pressing.

[ ] a range as a value.  Refused today (4443): a range stands where a loop takes its values from and nowhere else.  Making it a
    value means a type for it, a layout, and a rule about what a range of one type compared with a range of another means; Rust
    has all of that and needs it because its ranges are iterators like any other.  Worth doing when something wants to pass one.

[x] a step that is computed rather than written down.  Done on 2026-10-18 at the user's direction: how many turns there are is
    worked out once before the loop and the loop counts them; the step is of the ends' type or its signed twin (4927), and one
    that comes to nought stops the program (status 91).  4441, which refused it, is gone from the catalog.

[x] leaving a loop early.  Done on 2026-09-14: `break §name` leaves a loop and `continue §name` begins its next turn, the loop
    named by `§` after its keyword and the label written on every jump (3033), so that a jump says which loop it means rather than
    where it stands.  A `break` hands a value over, which makes a loop an expression, with an `else` arm for where it ran out --
    the entry further down.  There is no separate `loop` keyword.  `while true:` with a `break` serves as a statement, but as a
    value it answers a result, as every loop without an `else` arm does: the compiler does not treat a condition that is always
    true as one that never ends the loop, which Rust's `loop` is for.

[x] add support for arrays.  Done: an array has a shape, one entry per dimension -- `T⟦4⟧` a vector, `T⟦2,3⟧` a table -- and
    either every dimension says how many or none does.  A type that says carries nothing but the elements; one that does not is
    where they are and one count per dimension, and the first stands where the second is wanted.  `a⟦i,j⟧` reads an element,
    one index per dimension in row-major order, and `a⟦i…j⟧` takes a run out of a vector; every access is checked,
    while compiling where both ends are written down and while running where either is not.  Fewer indices than dimensions names a
    row.  A variable at the top level holds its elements itself and one inside a function holds them in the frame.  A variable marked `@[cdecl]` is laid out the way the system
    would and every other one whichever way is better.

[x] take a row out of a table.  Done, and iterating over the outermost dimension is what asked for it: `m⟦i⟧` of a `T⟦2,3⟧` is a
    `T⟦3⟧` and costs one multiplication and no copy.  Writing still wants an index per dimension: what an assignment
    writes is one element, and copying a whole row is not what it means anywhere else.

[x] work out a constant expression in the front end.  Done on 2026-10-03: wherever a number has to be known while compiling --
    a tuple's index, an array's length, the shape before `⍴`, a constant at the top level -- it may be an operator over constants
    or a call to a pure function of the program with constant arguments, which the compile-time evaluator runs.  The arithmetic is
    the program's, suffixes and all: what would stop the program is an error (4214, 4664), and a division answers a result that
    `?` or `??` takes apart (4663).  An array's index is worked out the same way where it can be and is then checked against the
    length while compiling.  The evaluator divided as Python does, toward minus infinity, and now truncates as the program does.

[x] let an array's length be a name.  Done on 2026-10-03, without the pass of its own this entry expected: a constant asked
    for before its definition has been reached is worked out from what was written for it there and then, as the compile-time
    evaluator works out a variable on first ask.  So `u8⟦SIZE⟧` in a signature names a `SIZE` written anywhere, and a constant may
    be defined in terms of one below it; one that reaches itself is refused (4666).  Folding constant expressions had briefly
    accepted only the names written above, which is the order-dependence this entry warned of.

[x] assign to one member of a tuple.  Refused (4462), because a tuple is registers and not a place.  What it would mean is binding
    the name to a tuple made of the other members and the new value, which the language can already be told to do by writing that
    out; whether `←` should be a second way of saying it is a question about assignment rather than about tuples, and the same
    question the entry above asks about a row.  **Decided** on 2026-10-17: it stays refused.  A tuple is a value, and a new one
    is made by writing it out; a member to be changed belongs in a record.

[x] assign a whole row, or a whole array.  `m⟦0⟧ ← r` is refused (4457) and would be a copy of as many elements as the row
    holds.  Nothing in the language copies one aggregate into another yet, and the first thing that does should settle it for
    products and arrays together rather than for arrays alone.  Done on 2026-10-17: giving an array to an array, a row or a
    name copies the elements, lengths known only while running checked then.

[ ] copy an array whose elements point somewhere or hold references.  Refused (4710): a copy of text, a list or a lambda is a
    second holder of the room it points into, which the allocator rules answer for one value (`_stored`) and not yet for a run,
    and a copy of a reference is held to the lifetime rules one at a time.  A name bound to such an array without `mut`, from
    one that may be written, is still a second name for its elements rather than a copy.

[x] give a collection or an array a length a program can read.  Done on 2026-09-15: `#` answers how many, as a `u64`, of an
    array (its outermost dimension), a list, a string (its characters), a tuple, a set and a dictionary -- from the type where the
    type says it and from the word carried beside the elements where it does not.  `⍴` answers an array's whole shape.

[x] slice a table.  Refused (4459).  A row is a run and a column is not: its elements are a row apart.  A slice that could say so
    would carry a stride beside the place and the count, which is what NumPy does and what turns a slice into a view of any shape.
    Worth doing with the entry about arrays that grow, since both change what a `T⟦⟧` carries.  Done on 2026-10-17: a `T⟦⟧`
    carries a stride per dimension, a table is sliced along any of its dimensions, a slice takes a positive step, and
    `⎕one_run` is the elements as a run where one is wanted.

[ ] take a run going the other way.  A step is at least one (4453): `a⟦9…0…⁻1⟧`, NumPy's `a[::-1]`, would be a view whose stride
    is negative, which every copy and walk would have to take into account -- the order `_move_elements` copies in among them.

[x] leave out the copy of a view where it is known to be one run.  Every program doing I/O carries `std`'s copy of a view that is
    not one run -- the question, `__pl4g_one_run`, the allocator, the giving back: some 2 KB and up to half again the time to
    compile the smallest of them -- because nothing folds it away where the view is a run while compiling: neither a branch on a
    constant after `@[inline]`, nor `(p + 1) - p`, nor a stride read out of a tuple made in the caller.  An optimizer that
    did, with the entry points written as small wrappers that are inlined, would leave a run's way through as it was.  Done on
    2026-10-17 for the code: a stride every caller hands alike is written in (`knownargs`), addresses fold, and a branch settled
    late is taken out; a program handing `std` runs carries none of the copy, and its image is within a few hundred bytes of what
    it was.  What is left is in the entry below.

[ ] compile `std`'s copy of a view that is not one run only where it is reached.  Its code is lowered and optimized for every program
    doing I/O before it folds away, which leaves the smallest of them a third slower to compile than before views carried strides --
    semantic analysis lowering more of `std`, every pass working on more until the branches settle.  Building `__pl4g_one_run`'s body
    only for a program still naming it after optimization, and `std`'s view paths as functions of their own that are lowered only
    where called, would leave that work to the programs that have such a view.
    **Measured (2026-10-18):** most of it was not the copy.  A local `run` in `std` named `std.run`, which lowering-by-name then
    lowered for every program doing I/O, the scheduler and all; renamed, the semantic analysis is back where it was.  Building the
    helper's body lazily would save about 1.5 ms of optimizing, and needs it built before `largeanswers` has settled how it answers,
    so it is not done.  What is left, a fifth to two fifths on the smallest programs doing I/O, is every pass working on `std`'s
    view paths before they fold.  Verifying, which was the other half, is now asked for (`--verify`) and not done otherwise.

[x] walk a view of a table without dividing.  Done on 2026-10-19: a pick and a copy walked a place at a time carry an index along
    each dimension with the loop (`_odometer`), carrying from one to the next without a branch, so no turn divides; `¨` is written
    out a place at a time, where what it divides by is folded.  With it, a division of two constants folds where the divisor is
    not nought and the answer fits.

[x] let a dimension be told and another not.  Done on 2026-09-15, with picking by a mask, which was what asked: rows picked out
    of a table are however many, each as wide as the table, and that is a `T⟦,4⟧`.  `u6⟦2,⟧` works the same way.  A value of
    such a type carries a count for every dimension, the stated ones included, so it is one shape of value however much of the
    shape the type says; 4456, which refused it, is gone.

[x] an array that can grow.  Done on 2026-10-19 at the user's direction: the list grows, its room's size in a header before the
    elements, by `l ⧺← more`; `l⟦i⟧`, `l⟦i⟧ ← v` and `⎕reserve(l, n)` came with it.  A list is now a value: read into another
    place it is copied where either may change it in place, unless its source is done with.
    Left over:
    [ ] **an array that is not written out on the right of `⧺←`**, `l ⧺← a` with `a` a name; an array literal is the list of
        its elements, and anything else is refused as not a list.
    [ ] **a run of a list**, `l⟦i…j⟧`, and **a reference to an element**, `&l⟦i⟧` -- both still refused (4449), the second
        because a list may move its elements when it grows.
    [ ] **giving a list's room back when a name stops holding it** -- a heap list replaced by `←`, or a copy made for a
        binding, is not given back; an arena's goes with the arena.  The entry about giving back what a name held is this.
    [ ] **a list inside a sum, or an array of lists, copied** -- the rule above copies lists, and records and tuples
        holding them, part by part; a sum would need its tag asked and an array a walk.

[x] say where the elements of an array live, so that one can be answered with.  Done on 2026-10-19 at the user's direction:
    `→ u8⟦⟧ in a` -- an array handed over, an arena, `⎕static` -- and `→ u8⟦3⟧` by value; a view the body made is copied into
    the arena named, or `⎕heap`.  With it, a view of a local array carries where it points and may not outlive it (4570).
    Left over:
    [ ] **a view of more than one dimension copied** into an allocator, which `__pl4g_own` does for one dimension only.
    [ ] **a lambda and a function type answering a view** (4454), which want an `in` of their own.
    [ ] **what a generator yields**, a view of its own frame as `std.read_lines` yields one, is still unchecked: a loop's name
        bound to it may be kept past the walk.

[x] iterate over an array, a set and a dictionary.  Done: four things are iterators and none of them is called.  An array gives
    each element where it has one dimension and each row of the outermost where it has more; a set gives each key; a dictionary
    gives a key and what it stands for as a tuple, which two names take apart.  The loop is written once around three questions --
    is there another, what does this turn give, what does the next turn start from -- which is a `next` answering a result said in
    the three places a loop has room for them.

[ ] a small-array optimization.  Looked at and **not** done, because as written it is not an optimization.  A `T⟦⟧` **owns
    nothing**: it is where some elements are and how many, and the elements are an array's or part of one.  Holding them in the two
    words instead of pointing at them makes the value own them, which turns a view into a copy -- and a slice is a view a program
    writes through:

        let a: mut u8⟦4⟧ = ⟦1u8, 2u8, 3u8, 4u8⟧
        let view: u8⟦⟧ = a⟦1…3⟧   → two elements, as small as a small array gets
        a⟦1⟧ ← 9u8
        view⟦0⟧ + view⟦1⟧            → 12 as a view, 5 as a copy

    which answers 12 today.  So this is a change to what a program *means* and not to how fast it runs, and the flag that would say
    which form a value is in cannot help: whoever made the value chose, and a slice may not choose the inline one.
    Its other premise is also false.  There is no small-string optimization to copy: a `str` is a pointer and a count exactly as a
    `T⟦⟧` is, and nothing anywhere inlines anything.
    What it really waits on is not measurement but **possibility**: only a `T⟦⟧` that owns its elements can hold them, which is the
    entry about an array that can grow -- and nothing constructs an owning one today, so the inline form would never be built.  It
    also wants the three entries that each change what a `T⟦⟧` carries -- a stride for a table's slice, room for growing, the
    counts a half-told shape left out -- to be settled first, since it would be rewritten by each of them.

[ ] add floating-point types `f16` and `bfloat`, optional if there is no hardware support.
    `f32` and `f64` are done: a value can be written, held, passed, returned, computed with and compared, and the hardware's
    floating point is assumed on all three targets with the requirement recorded in the binary -- on RISC-V the header's flag word
    saying the double-precision convention, and on x86-64 and AArch64 nothing, floating point being in the base of both ABIs.
    Both rules that were decided beforehand are in: an answer that is an infinity or a not-a-number stops the program, and `=` on
    a floating-point value warns.
    What is left is the two narrow formats.  `f16` is in the base of neither x86-64 nor RISC-V and is an extension on AArch64, so
    this is the first thing that will want `@[required(NAME)]`; `bfloat` is narrower still and is in none of the three bases.
    Neither has a literal suffix yet and neither names a type.

[x] add a product type and a sum type.  Done, as one construct: `type NAME = ` and then a sequence of `NAME : TYPE` pairs,
    separated by `;` for a product and by `|` for a sum.  The sequence may be written over several lines inside braces or indented
    under the definition.  One pair with no separator is a product.  A variant of a sum may be `void`, which is how an
    enumeration is written; a field of a product may not.  A defined type is nominal, may name one defined below it or one
    another module exports, and may not reach itself.  Layout is computed: a product is its fields in declaration order, a sum is
    its largest variant with a one-byte tag after it.

[x] write a value of a product: `Point(.x ← 1f64, .y ← 2f64)`, read a field with `p.x`, and hand one to a call and
    answer with one.  Done for a record whose fields are each one value: to everything below the checker it is what a tuple is,
    which is what `parts_of` now says, so a convention places it with no rule of its own.

[x] write a value of a sum.  Done on 2026-09-29, as the decision said: `Colour(.red ← …)`, one part where a product writes every
    field, and `.name ← true` where the part carries nothing.  A value of one is room holding the part and a tag, and what
    travels is where that room is.

[x] let a record hold a record.  Both are refused by the code generator today
    (8501): a record travels as its fields, so a field that is itself several values has no one register to go in, and a load of
    a multi-part value is not a thing the back ends do.  `std.Init` needs both: it holds an `Io`, and `&mut init.io.output` is a
    reference into it.

    Done on 2026-09-17: a record local is given storage of its own, a field is read at an offset, `&p.x` is an address, a field
    behind a reference is read without reading the whole record, and a literal is written into the place that will hold it --
    which is what lets a field be a record, there being no register such a value could be made in.

[x] hand a record holding a record to a call, or answer with one.  Done on 2026-09-17 by flattening: `parts_of` walks a
    nested field down to its leaves, so every part of a record is one value and a `Line` travels as four `u32`s.  The cost named
    when the two ways out were written down is the one paid -- a whole nested field is not one extract -- and where one is
    wanted from a value the record is put in a frame and the field read from there.

[x] decide whether a fixed array is copied or shared when it is bound to a name.  Decided on 2026-10-19 by the user: copied, as
    a record is.  Found already so -- giving an array to an array copies it, and binding one is giving it -- and written down in
    the specification; what was wrong was writing an element of an array held in a field, which went into a copy of the field
    and was lost, and is fixed.

[x] the `std` module, and I/O through io_uring.  Done on 2026-09-18: `modules/std.pl4g` defines the descriptor types and
    `Init`, the startup function may take `std.Init`, and `read` and `write` reach the runtime the compiler carries.  Written in
    pl4g first, which is where the language learned what a ring needs; moved to C the same day at the user's direction, reached
    through `@[external]` and handed the ring as an `@[abi]` record.  qemu-user has no `io_uring`, so where one cannot be made the
    calls go straight to the kernel and every test still runs on all three targets.

[x] the environment.  `⎕environ` is a `⸨str: str⸩` built before anything of the program runs, and is the compiler's own
    environment while the compiler is working something out -- which is what `std.Build.env` holds.  Read-only by its type.
    Done 2026-09-28.

[ ] the auxiliary vector, which is what the kernel leaves on the stack after the environment.  The record is shaped so that it
    can be added without any signature changing, and nothing wants it yet: what is in it is page size, hardware capabilities and
    where the vdso is, and the language reaches none of the three.

[x] walk a collection whose keys or values take more than one word.  `foreach k, v := d` over a `⸨u8: u8⸩` works and over a
    `⸨str: str⸩` is refused by the backend (8501): the loop variable is bound to a value of several parts, which the instruction
    selectors do not build.  Reading by key, writing and counting work whatever the table holds; only the walk is waiting, and
    the environment is the first thing anybody would want to walk.
    **Out of date (2026-10-04):** Measured: the 8501 is gone, and walking a `⸨str: str⸩` now ends in an internal compiler error
    (9901, "making a 〈str, str〉 out of 2 values"), which is worse.
    Done 2026-10-19: the pair a turn gives is made of the key's and the value's words, which is what the IR wants of a tuple;
    the 9901 was the verifier's, so a build without it compiled a walk that ran.

[ ] give the room back.  A table that has lost most of its keys keeps the array it grew into: nothing shrinks one, and the
    arena it came out of hands nothing back either.  What would decide it is the same two counts the growth already reads.

[ ] a read that does not wait.  `std.write` answers a handle and `std.read` does not: what a read is for is the bytes, and a
    program that has not waited does not have them.  A program that wants several reads in flight -- from several descriptors --
    wants the same pair `write` and `flush` are, and wants somewhere to say which buffer each filled.
    **Out of date (2026-10-04):** `std.io.Reader.start` answers a `Pending` without waiting and `std.flush` waits for it; a `std`
    read that does not wait is what is missing.

[x] use `ReadWriter`.  Done on 2026-10-03: `std.Io.open(name, ⌜std.ReadWriter⌝)` answers one.  The decision of 2026-09-18 that
    there is no `open` call was reversed by the user's asking for `std.Io.open`, with the kind of device handed as a type.

[ ] let a device answer why its close failed.  `Reader.close` and its siblings answer nothing: a refused close of a file that was
    written may mean the data never reached the disk, which Go's `Close` answers an error for and Rust's `Drop` ignores.  Answering
    a result would make every `defer` of one a statement whose answer nothing takes, which is the part to decide.

[ ] let a value that must be closed live inside something.  A record, a tuple, a list or a variable at the top level may not hold
    one moved there (4687), because nothing asks whether what is inside is closed when the whole goes.  A record whose type is
    itself `@[closed_by]`, closing what it holds, is the shape that would make it safe.

[ ] say how long a buffer handed to the runtime must last.  `std` hands over the buffer's address as a number, so nothing relates
    the two at all: the request is complete before the call returns and that is a property of the runtime's design rather than of
    the type.  A ring that answered later would need the language to say it, and `⎕address` is where it stops being able to.
    **Out of date (2026-10-04):** "The request is complete before the call returns" is no longer true: `std.write` and
    `Reader.start`/`Writer.start` answer a `Pending`, and tasks run under `std.run`, so the lifetime question is a real one now.

[ ] let `@[external]` reach something the compiler does not carry.  What it may name today is what the packaged runtime defines,
    there being no linker.  A program that wants to call something else wants either a linker or a way to add to what is
    packaged, and neither is decided.

[x] check a reference written into a field.  Done on 2026-09-18, together with `⎕address(REF)` -- which is what the ring needed
    once the rule applied to it as well, its submission entry now holding the `u64` the kernel's field actually is.

[x] a way to write an ordering.  Done on 2026-09-17, the user choosing the compiler-provided functions over a reference that
    carries the promise: `⎕acquire(REF)` and `⎕release(REF, VALUE)`, so the same place read the ordinary way elsewhere
    is an ordinary read.

[x] a way to reach memory at an address the program worked out.  Done on 2026-09-17, the user choosing two names over one and
    over a mapping that answers bytes: `⎕at(ADDRESS, ⌜TYPE⌝)` for one value and `⎕span(ADDRESS, COUNT, ⌜TYPE⌝)` for a run
    of them.  They are the only way, which is what keeps that door in one place.

[x] a record at the top level.  Done on 2026-09-17: a record literal of constants is laid out into the bytes a global holds,
    field by field where `offsets_of` puts each, and the global's address stands where a placed local's does -- so reading a
    field, writing one and taking a reference into one all work as they do for a local.

[x] a way to widen.  Done on 2026-09-18, at the user's direction: `⎕widen(EXPR, ⌜TYPE⌝)`, answering the value and not a
    result because it cannot fail, and refusing anything that is not "every value of the one type is a value of the other".

[x] answer a result rather than the kernel's number.  Done on 2026-09-18: `→ u64 ¤size ? Error`, where `Error` is an
    enumeration `std` exports whose numbers are the kernel's own.

[ ] name the whole errno table, or carry the number beside the name.  `std.Error` names eighteen of them and everything else is
    `other`, which loses which one it was.  The tidy answer is a record of a named kind and the raw number -- but a record as a
    result's error is a part that is itself several values, which `parts_of` and `through_storage` would both have to be taught.
    **Out of date (2026-10-04):** `std.Error` names 21 values now, and every result carries the kernel's own number (`? i32`);
    only `as_error` and `error_name` fold what they do not name into `other`.

[x] write a `str`.  Done on 2026-09-18: `⎕bytes(TEXT)` answers what a string is made of, which is what a device takes.

[ ] stop a program writing through the bytes of a literal.  `⎕bytes` answers a `u8⟦⟧` over bytes that may be the image's, and
    nothing says an array may not be written; bound to a `mut` name and assigned into, it would write where the image is and the
    program would fault.  It is the fixed-array question below asked of something that is definitely not writable, and the two
    want one answer.
    **Out of date (2026-10-04):** Measured: the program does not fault -- the literal's bytes are in writable `.data` -- and
    writing through `⎕bytes("abc")` changes the literal itself, so the next `⎕bytes("abc")` reads `Abc`.  Worse than a fault.

[x] ask which variant a sum holds: `match`.  Done, and it takes an enumeration and a result apart as well.  A result and an
    enumeration run; for a sum every rule about the arms is checked and the compiler then says it cannot generate for one, what is
    missing being a value of a sum rather than the match.  An arm names the type of the alternative it takes -- or, for an
    enumeration, the name of a value -- `⊥` is the error arm of a result, `_` takes every alternative left, and every
    alternative must be taken and none twice.  A name assigned inside an arm is carried past the match by a parameter of the block
    the arms join at, which is the machinery `if` will want.

[x] decide how an enumeration is written.  Done, the second way: an enumeration is a construct of its own, `enum NAME [: TYPE]`
    and then the names of its values.  The type is the representation and nothing else -- no conversion in either direction -- and
    where it is left out the compiler takes the smallest unsigned type that holds every value.  A value is written `TYPE.NAME`; a
    `match` over one names a value in each arm.  A sum every one of whose alternatives carries nothing is still not writable, and
    no longer needs to be.

[x] compare two values of one enumeration with `=`.  Done, `=` and `≠` and nothing else.  Ordering stays refused: the order
    of the values is the declaration order, and the language promises nothing about it.

[x] give the values of an enumeration numbers, and a `@[flag]` enumeration whose values combine.  Done: `NAME = NUMBER` says which
    number a value is stored as and `NAME = OTHER` takes an earlier value's, which is how an alias is written; two numbers written
    down may not be alike.  `@[flag]` makes the numbers the compiler chooses powers of two and defines `&`, `|`, `^` and `~` on
    two values of the type, and a `match` over one needs an arm taking the rest, its values combining into ones no name stands
    for.

[ ] decide whether a flag enumeration should have a bitwise nand and nor.  `⊼` and `⊽` are the logical ones and sit at the
    logical precedence level, so giving them a bitwise meaning would make `a ⊼ b` bind looser than `a & b`, which is wrong for an
    operator on bits.  Today a "neither" is written `~(a | b)`.  The choices: leave it so; give the two glyphs a second meaning
    and accept the precedence; or add two glyphs of their own at the bitwise level, which is two more characters spent.

[x] allow `match` to produce a value.  Done: where one is wanted of it every arm ends in a statement that has one and they are all
    of one type, and the block the arms join at carries it out -- which is what lets an assignment be pulled out of the arms and
    written once.  The same question for `if` is still open below and should be answered the same way.

[x] read a field of a product.
    Question: `p.x` is the obvious spelling and is what the grammar already parses for a name reached through a module, so the
    two would be one syntax with two meanings decided by what the base is -- which is what Go, Rust and Zig all do.  A product's
    fields may be reordered by the compiler, so a field is reached by name and never by position.
    Found done on 2026-10-04: `p.x` is done (the `[x]` entry on reading a field), and the layout keeps the order the fields were
    declared in.

[x] allowing definition member functions
    Question: this needs a syntax for the receiver.  Considered: Go's `fn (p: Point) length() → f64`, which keeps functions at the
    top level and makes the receiver an ordinary parameter; a block nested inside the type after Rust's `impl`, which groups them;
    and the receiver being implicit, as in C++ and Java, which the language's preference for saying things outright argues
    against.  A second question rides on it: whether a member function is a different kind of thing from a function whose first
    parameter happens to be the type, or only a different way of writing one -- the second is simpler and is what Go does.
    Found done on 2026-10-04: decided 2026-10-01T03:30 ("Attaching code to a type: design B"), and `v.f(args)` added
    2026-10-04T13:00.

[x] if an attribute definition is followed by an empty line or the end of the file, it is not attached to anything and the
    action is global.  Implementable now and needed by `@[required(NAME)]` below.  Note that the parser currently skips newlines
    after an attribute list precisely so that one can stand on its own line above the thing it belongs to, so the rule has to
    distinguish a blank line from a line break.
    This is now also the one exception the one-list-per-definition rule leaves room for: a list attached to nothing is an
    instruction to the compiler rather than a description of what follows, so one of those standing before a list is two lists in
    a row with nothing wrong about it.  `Parser._parse_attributes` counts lists and reports the second (3208); whatever decides
    that a list is attached to nothing has to reset that count, and the check is written at the one place that would have to ask.
    A blank line between two lists deliberately does *not* make them two things today, so this entry changes that reading: the
    blank line will mean the earlier list attached to nothing rather than meaning nothing at all, and the diagnostic for two lists
    before one definition then only arises where they really are both attached.
    Done on 2026-10-04: such a list is about the file (`SourceUnit.attrs`, target `a file`), the lexer noting a blank line on the
    end of line before it; `@[system]` is the first attribute it takes.

[?] add an attribute `@[required(NAME)]` which does not have to be attached to a variable, function, or statement but can be.
    The attribute checks whether the implementation or supported language version supports the feature named by NAME.  The
    supported values of NAME and their meaning must be documented.  Examples are the existence of types like `bfloat`.
    If the attribute is not attached to anything, the compilation is aborted with an error if the feature is not supported.
    If the attribute is attached to a function or variable, the respective definition is dropped without any further error.
    The mechanism needs the entry above about an attribute attached to nothing.
    Question: what is the set of names, and what does a name assert?  Three kinds are mixed together in the description: a type
    exists (`bfloat`), the target has an instruction (a rotate, a fused multiply-add), and the language is of at least some
    version.  They answer to different things -- the second is per target and known only at code generation, the third wants an
    ordering rather than a set.  Considered: one flat namespace with a documented list, as the C++ feature-test macros are; a
    namespaced form, `@[required(type.bfloat)]` and `@[required(cpu.fma)]`, which keeps the kinds apart and says which are per
    target; and a version form, `@[required(lang.0.2)]`, alongside either.  Nothing can be implemented until the shape is chosen,
    because the shape is what a program will have written down and cannot be changed afterwards.

[?] specify the error path that exists before `io_uring` does.  The specification requires a message when a required CPU or
    operating system feature is missing, forbids depending on any system runtime, and routes all input and output through
    `io_uring` -- whose own setup can fail before there is any object to report through.  A raw system call is the only thing
    left; what it may assume needs saying.  Partly settled: the decision to abort a fault with a real backtrace means the compiler
    emits such a path anyway, and the two should be one facility rather than two.
    Question: what may that path assume?  Whether file descriptor 2 is open at all -- a program started with it closed would have
    its message go to whatever is opened next, so the path either checks or does not care; whether it may allocate, which it
    cannot before there is a heap; and what it does when the write itself fails, since there is nowhere left to report that.
    Also whether the message is a fixed string, which needs no formatting and so no runtime, or may carry a number, which needs
    the smallest possible integer-to-text routine in every binary that might fault.
    **Out of date (2026-10-04):** A ring that cannot be made is no longer an error path: the runtime falls back to plain system
    calls (details.md); the questions about descriptor 2 and the message remain.

[?] the sentence "the grammar has to be context-free, there is no process definitions in order" in spec/spec.md is garbled.  It
    is read as "there is no *need to* process definitions in order", so a forward reference at the top level is legal, and the
    compiler collects every top-level definition before checking any body on that reading.  Confirm or correct the wording.
    Question: is that reading right?  The compiler is built on it -- semantic analysis collects every top-level definition in one
    pass before checking any body in a second -- so if the sentence meant something else, that is the thing to change.  The other
    reading, that definitions must be processed in order, would be C's rule and would make a forward reference an error.

[x] there is no way to write a negative number.  Done by the entry below on `⁻`: the sign is part of the literal, so the most
    negative value of a type is a literal like any other and not a negation of one.

[?] there is no way to say what symbol a function should be known by without also saying how it is called.  A function
    declaring a foreign calling convention keeps its bare name, which covers calling into another world; an attribute naming the
    symbol directly would cover the rest.  Nothing needs it yet.
    Question: `@[symbol("name")]` is the obvious form, taking the name verbatim.  What is not obvious is what it is allowed to do:
    whether two definitions may be given the same symbol, which the image cannot hold; whether it may name something the mangling
    scheme would otherwise produce, which would collide silently; and whether it implies export, since naming a symbol exactly is
    usually done so that something outside can find it.

[?] decide whether a local variable nothing reads should be an error rather than a warning.  Go refuses to compile one, Rust and
    C warn.  PL4G warns (4006) and an optimized build drops the variable.  Since the language is meant to be generated rather than
    written, refusing one may catch a generator bug that a warning would let through.
    Question: error or warning, and does the same answer apply to the new 4007 about a variable at the top level that the program
    only writes?  Making both errors is the strictest reading of the no-surprises rule and matches Go for locals; the argument
    against is that a generator emitting a template may legitimately produce a definition its particular instantiation does not
    read, and `@[ignore(4006)]` on every such line is noise.  A middle answer is to make it an error by default and leave the
    attribute as the way out, which is what the attribute exists for.

[x] Add negative number literals with a leading `⁻`, no space.  Compare with what APL does in the documentation.  This allows more
    streamlined parsing in the presence of subtraction.  Done: the sign is read where the number is read, a space after it is an
    error (2008), and `-` stays free for subtraction alone.

[x] Allow thousand separators in integers and the whole part of float literals.  Use `_`.  Do not be strict wrt the rules of the
    thousand separator, just ignore the `_` characters.  Done for integers in every base, and tested; the float half follows when
    float literals exist, since there are none to put a separator in yet.

[x] implement operations on numbers, for `+`, `-` and `×` on integers.  Every one of them checks, and an answer that will not
    fit stops the program with a message saying which operation it was, in which function, at which line.  The message is built
    whole at compile time, so what runs at the moment of the fault is a write and a trap.  The three shapes the check takes are
    the ones the saturating operations use, the question being the same and only the reply differing.
    Left open, each with an entry of its own below: division, exponentiation, and the same for floating point.

[x] implement `÷` and `%`.  Done, truncating toward zero, so that what is left over carries the sign of what was divided and
    `a` is always `(a ÷ b) × b + a % b`.  Dividing by zero and dividing the most negative number by `⁻1` are the two
    divisions with no answer, and both are asked about before the instruction runs: the three architectures do three different
    things about each, so leaving it to them would mean a program meaning three things.
    The fixed pair of registers x86-64 divides through needed nothing new after all: the instruction declares that it writes them,
    and the allocator already keeps a value out of a register whose life overlaps its own.

[-] implement `↑` for exponentiation.  Not begun, and it needs a decision first: with a constant exponent it is a few
    multiplications and every one of them checks, which is straightforward; with an exponent that is not known until the program
    runs it is a loop, and the language has no way to write one, so the loop would have to be emitted -- or the operator
    restricted to constant exponents, which is what every language that has an integer `**` other than Python effectively does.
    No longer applies (2026-10-04): exponentiation is `a ⁿ b` and written raised, `a²` (tests/language/power-operator and its
    siblings), and `↑` rounds up.

[x] decide what a string is, now that there is a character.  Done: `str` is where its bytes are and how many there are, always
    well-formed UTF-8, walked by `foreach` a character at a time and joined with `⧺`.  There is no index, the n-th byte of
    UTF-8 not being the n-th character; a grapheme cluster is the larger question and stays open.

[x] take the type a set or a dictionary holds down to what is written inside it.  `let s: ⸨u8⸩ = ⸨1, 2, 4⸩` does not
    compile: the numbers have nothing to say what they are, where an array and a list in the same place do take it.  Found while
    making `foreach x: T = ...` hand the declared type to what the loop walks.
    Found done on 2026-10-04: `_entries_written` takes the type from the declaration where no entry says one.

[ ] decide what else a condition settled while compiling may ask.  Today it asks whether two types are the one type and joins
    those answers; whether a number written down is what it is, whether a function has an attribute, and whether a type is one
    of a kind -- an integer, an array -- are the obvious next ones, and each is a decision about what the compiler is willing to
    promise rather than about how to compute it.

[ ] decide whether `⎕typeof` may name a type in a declaration: `let y: ⎕typeof(x) = ...`.  That is a type in a type position
    rather than in a condition, and it is where a type first has to be more than a thing two conditions compare.

[ ] box a list's elements, so that they need not be of one type.  The type records what they agree on today, which is what
    makes the agreeing case the one worth not boxing; what is missing is the boxed value -- a value held with enough beside it
    to say what it is -- and the rule for which of the two a list is.

[ ] read one element of a list.  `l⟦i⟧` is still refused (4449); what reads one is a cursor.  The question the entry used to
    hold -- what an index past the end means -- was answered on 2026-09-29 for `†l⟦i⟧`: it stops the program, as an array's
    does.  So what is left is whether a read is worth having beside the cursor, and it is the same check either way.

[ ] a spelling for a cursor's type.  `let it: mut = ⎕iter(l)` works because the name takes its type from the value; a parameter,
    a field or a function's answer cannot say `cursor over [u8]` at all, so a walk cannot be handed to anything.  What it waits
    on is the same question a range as a value waits on: a type the compiler provides needs a name a program can write.

[x] decide whether a list may be changed in place.  Answered on 2026-09-29 for taking one element out: `†l⟦i⟧` and `†it` shift
    what follows down and write the shorter list back where the list was, so a list stays a *value* and another name for it keeps
    what it had.  What that leaves open is putting one *in* -- an element assigned, one appended -- which wants the room to grow
    and is the entry below.

[ ] put an element in a list: one assigned, one appended, one inserted.  Taking one out needs no room and no allocator; putting
    one in needs both, and a rule for what happens to a name holding the list when the elements move.  The place-and-count that
    `†` already writes back is what such a rule would be written in terms of.

[x] compare two strings.  `"a" = "a"` is refused today, which is honest and not enough: what two strings being equal means is
    that their bytes are equal, UTF-8 being a one-to-one encoding of a sequence of code points -- so it is a length test and a
    comparison of the bytes, and the run-at-a-time machinery is what would do the second.  Ordering is a different question and
    a much larger one: which order is a collation, and there is more than one.
    Found done on 2026-10-04: all six comparisons are defined on strings, in code-point order (tests/language/string-compare).

[x] say how long a string is.  Done as `#`, which answers characters -- the number that needs a walk, on the grounds that the
    number that does not is the one nobody wants.  What is still open is how the *byte* count is spelled, it being in the value
    already and wanted by anything that writes a string out.  `#` is taken and should stay taken by the useful one.

[ ] count all the elements of a multi-dimensional array.  `#` answers its outermost dimension, which is what makes it agree with
    `foreach`; how many there are in all is a product of the shape and is a different question.  Nothing asks it yet.

[ ] take a piece of a string.  A slice of one has to begin and end where a character does, so it is not the same operation an
    array's slice is -- either the ends are found by walking, or the type says they were.

[ ] `mut str`, the owning, growable one.  `str` owns nothing and a join makes a new one; what is missing is the one a program
    builds up in place, which wants the arena in its type the way a collection does.

[ ] decide whether `char` may be a key of a set or a dictionary, and whether it may be the tag of an enumeration.  Both are
    questions about which types are alike enough to be hashed and compared, and a code point is a number as far as either is
    concerned -- but a type that is deliberately not an integer type should say so once rather than have every such place ask.
    **Out of date (2026-10-04):** The key half is done (`char` is a key); an enumeration held as a `char` is refused by 4420, with
    no decision written down.

[x] the same arithmetic on floating-point values, where IEEE infinity and IEEE not-a-number are what an overflow or an underflow
    produces and are what the check looks for.  Waits on the float entry above.
    Found done on 2026-10-04: an infinity or a NaN as an answer stops the program (float-arithmetic,
    float-overflow-stops-the-program, float-division-overflow-stops-the-program).

[ ] report an overflow that can be seen at compile time as a compilation error rather than leaving it to fault at run time.  The
    constant folder already computes the answer and declines to fold where it does not fit, which is where the report belongs;
    what it needs is somewhere to report from, the folder being an optimization that does not run at every level.
    **Out of date (2026-10-04):** An operation whose operands are both written down is reported already (4214, 4215); only an
    overflow the optimizer's folder finds is not.

[?] Implement strings.  Literal strings are always encoded in UTF-8.  They are not mutable.  Variables can be defined as type `str`
    which can be initialized with a string literal.  If not marked mut a `str` object cannot be modified and has a fixed length and
    should be placed in `.rodata`.  A `mut str` object can be resized and changed and therefore has to be an object which can
    reference allocated memory elsewhere.  Small string optimizations are welcome.
    The immutable half now has the product type it was waiting for -- a pair of an address and a length -- and waits on the two
    things that type still lacks: a way to write a value of one, and a pointer, which nothing in the language yet is.  Literals
    already lex and `.rodata` already exists.  The `mut` half waits on the allocator below.
    Both questions are answered.  The heap is a `mmap`-backed bump allocator over a list of chunks -- GNU's obstacks -- and the
    compiler emits it; a size-class allocator is the thing every later feature will also want and is in the compiler's list, but
    it waits on a program that runs long enough for the difference to show.  An allocation that cannot be met stops the program
    through the same path an arithmetic fault takes, rather than answering with a result: a result would put a `?` on every value
    a program builds rather than computes.  What is left for the `mut` half is the spelling -- naming which arena a value lives in
    -- which is the entry below.  Small-string optimization is an implementation choice underneath either answer.
    **Out of date (2026-10-04):** The immutable `str` is done -- join, walk, compare, `#`, `⎕bytes`, `in ⎕static`, `in a` -- and
    nothing waits on a pointer any more; what is left is the growable `mut str`, which is its own entry.

[x] Implement boolean values.  Only the values `true` and `false` are defined.  Assigning any other value is an error.  Done and
    tested in both directions; a constant `bool` goes in `.rodata` and a `mut` one beside the variables, like any other value.

[x] implement bitwise operations.  Use `&` for bitwise AND, `|` for bitwise OR, `^` for bitwise XOR, `~` for bitwise NOT.  Usable
    only on integer values.  Done, with C's relative binding, which Rust, Go and Zig kept: `&` tighter than `^` tighter than `|`.
    A truth value is refused (4205) and two different integer types are refused (4206).

[x] implement shifting and rotating of integers.  Done: `«` `»` `↺` `↻`, binding where multiplication does.  A distance of the
    width of the type or more stops the program, decided by the user by the rule of least surprises: the three architectures
    answer it three different ways, and two take it modulo the width of the *register* rather than of the type.  A right shift
    brings in copies of the sign for a signed type and zeros for an unsigned one; bits that fall off the end of a shift are gone,
    a shift being how a bit pattern is built.  A rotation turns the bits of the type rather than of the register, which is why it
    is built from two shifts rather than from the rotate instruction, and is defined on unsigned types only (4216).

[x] implement saturated operations.  Done: `⊞`, `⊟` and `⊠`, binding tighter than the bitwise operators with
    multiplication tighter than addition.  A type narrower than its register is computed as it stands, the answer being exact,
    and compared with the two ends of the type; a thirty-two bit type on the two architectures with thirty-two bit registers has
    its operands widened first; a type as wide as the widest register is allowed to wrap and the wrapped answer is asked what
    happened.  No branch is needed anywhere: the clamp is a conditional move on two targets and a mask on the third.
    One case is refused rather than guessed (8501): a saturating multiplication of `u64` or `i64`, which needs the upper half of
    the product.  That is one instruction on AArch64 and RISC-V and on x86-64 only in a form with a fixed pair of registers, so
    it waits on fixed-register operands in the allocator -- the same thing division will need.

[x] implement exact comparisons, defined for numbers, strings, booleans.  Done for numbers and truth values: `=`, `≠`, `<`,
    `>`, `≤`, `≥`, with `<=` and `>=` accepted for the last two.  All six share one level, bind looser
    than the bitwise operators, and do not chain (3014).  Equality is defined on truth values and ordering is not (4205).  A
    comparison of constants folds.  `=` compares rather than assigns, and one whose answer is discarded is refused (5005), which
    replaces the syntax error 3008 that existed while `=` had no meaning in an expression.
    Left open, in the entries they belong to: strings, once there are strings; floats, once there are floats -- and with them the
    warning that `=` on floating point is an unsafe question, and the rule that an integer literal compared with a float must be
    exactly representable in it.

[x] decide whether `≠` should have an ASCII substitute.  No.  `!=`, `/=` and `<>` all pass the two rules a substitute has to
    pass, so the question was which of three to bless, and blessing none leaves the glyph as the one way to write it.  `<=` and
    `>=` earn theirs by already being what every keyboard and every reader produces for those two glyphs, which is true of none
    of the three candidates here.

[x] a statement whose value is not used is an error (5005), with a note (5006) where the value is a comparison written with `=`.
    Every expression computes a value and does nothing else, so a statement that is only an expression does nothing unless
    something takes the value -- and the last statement of a body, which is the body's result, is the one place anything does.
    A bare literal and a bare name count as much as anything computed.  `@[ignore(5005)]` on the statement accepts the line,
    which needed a new kind of error: the shared catalog now marks with `well_formed` an error the construct is still well formed
    despite, and quieting one of those leaves it in the program where quieting any other error discards it.

[x] implement comparisons for floating point values that are sensitive to small, accumulated errors.  Done: `≅ ≇ ⪅ ⪆ ⪉ ⪊`,
    each of the exact comparisons with the question asked of `⎕tolerance` rather than of the values.  One subtraction, a read of
    that variable and one ordinary comparison, with the magnitude in between for the two that ask about likeness either way; an
    `f32` difference is widened to the tolerance's own type, which holds it exactly.  `⎕tolerance` is the first name the compiler
    provides, and a name beginning with `⎕` is the compiler's: a program may read and assign the ones that exist and may not
    define one.

[ ] decide whether the tolerance should be relative rather than absolute.  Today `a ≅ b` asks whether |a-b| is at most the
    tolerance, which is one subtraction and one comparison and is wrong for values of widely differing size: two numbers near
    10^20 that agree to fifteen digits are not alike by it, and two near 10^-20 are alike whatever they are.  APL's `⎕CT`, which
    this is modelled on, is relative -- |a-b| ≤ t × max(|a|,|b|) -- which costs two magnitudes and a multiplication and a
    comparison, and answers both of those correctly.  The question is whether that cost is worth paying by default, whether both
    should exist under separate spellings, or whether the tolerance itself should say which kind it is.
    **Out of date (2026-10-04):** `⎕tolerance` exists, assignable, at 1e-13 (float-tolerance); only relative against absolute is
    open.

    The comparison here is true as long as the difference is not larger than a limit.  For the time being, make this a builtin
    variable with the value 1e-13.  In future this will be a runtime-time variable.  Compare this with `⎕CT` in APL.

[x] implement binary logic operations.  Done: `∧` `∨` `⊕` `⊼` `⊽` `¬`, which always compute both operands, and `and`
    and `or`, which do not.  They bind looser than everything else, and among themselves take the order of the three bitwise
    operators they mirror; `⊼` and `⊽` share a level and do not associate (3014).  They work on truth values and on nothing else
    (4208), and no bitwise operator takes one.  None has an ASCII substitute, `&&` and `||` being the one confusion the language
    exists not to have.
    `and` and `or` made the first branch the front end has ever generated, which needed block parameters to reach the backends:
    every parameter gets a register before any block is walked, and a branch's arguments become moves before the jump.  Only an
    unconditional branch may carry arguments; the other shape needs the edge split and is refused rather than got wrong.
    Worth recording: the difference between `and` and `∧` is unobservable today, since no expression can have an effect, fail, or
    fail to finish.  They are separate so that a program says which it meant before that changes.  Use this table for guidance:
    | Glyph | Name | Arity  | Definition |
    |-------|------|--------|------------|
    | `∧`   | AND  | binary | true when both operands are true |
    | `∨`   | OR   | binary | true when at least one operand is true |
    | `⊕`   | XOR  | binary | true when exactly one operand is true |
    | `⊼`   | NAND | binary | true when not both operands are true |
    | `⊽`   | NOR  | binary | true when neither operand is true |
    | `¬`   | NOT  | unary  | true when the operand is false |

    In addition use the infix operations `and` and `or` similar to logic AND and OR except that they short-circuit.  These
    operators only work and binary values.

[x] the language has a builtin type result, similar to Rust's `Result` type and C++'s `std::expected`.  Done: `TYPE?` is a
    result whose error carries nothing, `?` hands the answer back and leaves the function with the error where there is none, and
    `EXPR ?? DEFVAL` hands the answer back or the value written instead.  A value of the answer type written where a result is
    wanted is the successful one, which is Zig's arrangement and C++'s and the only way to write one, there being no constructor.
    `÷` and `%` are the operations that answer with one, on every numeric type.

[x] let the error of a result carry a value: `TYPE1?TYPE2`.  The syntax is parsed and refused (9902), and this is now the only
    part of the result type that is not implemented.  It is not a question about the result type any more: the sum type exists,
    and what is missing is a way to *write* a value of one, which is the `[?]` entry above.  Until something can construct an
    error, such a type is one no program could put anything in.  A decision rides on it: what the error type of a division should
    then be, the two cases a division cannot answer -- a zero divisor and the one overflowing signed pair -- not being told apart
    today.  In memory such a result is the larger of the two payloads and a tag, which is what a sum of two variants already is.
    Found done on 2026-10-04: 2026-09-16T06:30, with `⊥(x)`; `std` answers `? i32` throughout.

[x] let a variable at the top level hold a result, and a parameter take one.  Done: in memory a result is its answer and one byte
    beside it saying whether there is one, so reading such a variable is two reads of one place and writing it is two writes; in
    registers it is two, and passing one takes one register of the answer's kind and one ordinary one, which is what the three
    ABIs already do with a two-word answer.  The argument mapping now counts a register out of the list its kind comes from
    rather than by position, which was already wrong for a floating-point argument standing beside an integer one.

[x] let `⁂` spread a fixed-size array.  Done: its length is in its type exactly as a tuple's is, so the expansion is the same
    one, done by the same code, with the elements read where they lie rather than taken apart.  An array of rank greater than one
    gives its outermost dimension, which is what `foreach` over one already does.  A dynamic array cannot and is refused (4465):
    its length is a thing the program works out and not a thing the type says.

[x] `〈⁂a, ⁂b〉`, spreading into a tuple literal rather than into a call.  Done: it is how two tuples are joined and how one is
    extended, it costs nothing at runtime for the reason the call case does not, and it cost a parser rule rather than a
    mechanism.  A tuple still has at least one member, so spreading an array of no elements into one is refused (4466).

[x] let a `break` hand a value over, making a loop an expression.  Done, and with it the `else` arm, the two being one decision:
    the way through that runs the body no times at all is what the arm gives a value to, and a loop with no arm comes to a result
    whose failure is that way.  Everything a loop may come to is of one type, checked across every `break` and the arm.

[x] make the two ways out of a loop distinguishable, as Python's `while ... else` does.  Done: the `else` arm runs where the loop
    ran out and not where a `break` left it, and gives that way's value.

[x] let a `break` hand over an unsuffixed literal where the loop's type is written but the loop has no `else` arm.  Done, and
    it was general: the result path handed nothing down, so an unsuffixed literal failed in every position a result is wanted --
    a definition, an assignment, an argument, what a function answers with, an `if`'s arms, and what a loop comes to.  What is
    wanted now travels down whole, with `_aiming_at` answering it for things that can only be an answer and `_accepts` for the
    checks.

[x] say what order a call's arguments are worked out in.  Done, and said of everything rather than of arguments: a call's
    arguments, a tuple's members, an array's elements, a collection's entries and an operator's two sides are all worked out in
    the order they are written, each exactly once, and precedence moves nothing.  Two things had to be fixed to make it true --
    a spread operand ran before an argument written to its left, and a collection's entries were each worked out twice, a
    dictionary's keys all before its values.  Nothing was needed to ask an expression its type before lowering it; what was
    needed was that whatever lowers one to learn its type hands the value back.


[x] use purity for more than dropping a call nothing reads.  The `purecalls` pass, at `-O1`: for a pure function reading no
    memory but its own frame and answering a value, a call made twice with the same arguments is worked out once, one whose
    arguments do not change is moved out of a loop's head, and one whose arguments are constants is worked out while compiling
    by the macros' machine.

[ ] widen what `purecalls` reaches: a pure function that reads memory it was handed, reused where nothing between the two calls
    writes; a call moved out of a loop from a block other than the head that runs on every turn; floating point worked out while
    compiling, with the target's rounding.

[ ] consider whether a pure function may write through an array it was handed.  It may not today (4479), there being no way to
    say that a parameter is not shared with anything the caller can still see.  A type that said so -- the parameter is this
    call's alone -- would make such a function pure and is what C's `restrict` gestures at.

[x] the built-in operators walk arrays, so that `v + w` is element by element.  Done: the arithmetic, the comparisons exact and
    approximate, the bitwise operators and the shifts, the logical ones written with glyphs, and the two written before their
    operand.  No attribute, an operator being no definition and every one that would be marked being marked.  `and` and `or` are
    not walked and could not be, which side is worked out being what they are about.

[ ] pick out of an array whose type does not say its shape.  Refused today (4485): what picking answers with is held in room
    enough for all of it, and that is what the shape says.  It wants the room taken while the program runs, which is the same
    allocation a dynamic array wants and would make picking impure.

[x] write through a mask into an array of floating-point elements.  Done on 2026-10-19 at the user's direction: a `select`
    instruction in the representation, lowered as `csel`/`fcsel` on AArch64 and `cmov` on x86-64, and through the bits with a
    mask where the machine has no select for the register -- a floating-point one on x86-64, either on RISC-V.  Every element
    type that is one value is written through a mask now.
    Left over: **a `match` over two constants**, and an `if` whose arms are plain values, as a `select` rather than a branch.

[ ] walk an array whose type does not say its length.  Refused today (4482): the answer's shape is the shape walked, and the room
    for it is taken before the calls are written.  It wants what a dynamic array wants anyway -- storage worked out while the
    program runs -- and the calls in a loop rather than written out, which is the same piece of work.

[ ] give a program a way to choose how a function answers with more than one value.  The choice is a property of the function
    already and there is one style, so there is nothing yet to choose between; when there is a second, an attribute beside
    `@[cdecl]` is where it goes, and `ReturnStyle` is what it sets.

[x] say what order a call's arguments are worked out in.
    Found done on 2026-10-04: it is the `[x]` entry above it: every argument is worked out left to right.

Runtime
-------

[x] Create a data type for for an output streams which is used for
    standard output/error.  For now a simple representation with a file descriptor and `write` functions is sufficient.
    Found done on 2026-10-04: `std.io.Writer` is that type, and `init.io.output` and `init.io.error` are of it (2026-10-04T09:00
    and 10:00).

[x] decide whether `@[export]` should mean one thing or two.  Two: `export` says what a file importing this module may name,
    `visible` says whether the finished image offers the symbol, and neither implies the other.  A definition a module lends and
    nothing imports is now dropped, which is what the one attribute made impossible.

[x] make the use of attributes strict: one list per definition, and no empty argument parentheses.  Everything said about one
    definition is written in one list (3208), and an attribute carrying no arguments is written without parentheses (3209).  The
    principle is now stated in the specification and governs more than attributes: where two ways of writing something would mean
    the same thing in every respect, the language admits one of them.  Surveyed for other places it applies; the ASCII
    substitutes, the brace notation, an omitted type and a named argument all differ in meaning or in what they are for, so none
    of them is a second spelling of one thing.

[x] let a lambda leave the call that made it.  Where what it brought in is kept is the compiler's choice -- the frame where the
    lambda provably stays, `⎕heap` otherwise, the arena `λ … [n] in a →` names -- and the value is three words.  What it reaches is
    provenance in the arena machinery's keys, so one leaving the call reaches only what lasts (4632); writing through a place
    from outside the call is checked the same way for everything (4633).  4549 is retired.

[x] let a lambda a function was handed leave it.  `fn(u8) in ⎕heap → u8` is the type of one whose environment lasts, made with
    `λ … [n] in ⎕heap → T` -- `in` before the arrow saying where what was brought in is kept, after it where the answer is made
    -- or by a lambda that brings nothing in; a plain value where it is wanted is refused.  `compose(f, g)` can be written.

[ ] let a function type say its lambda is kept in an arena a parameter names, `fn(u8) in a → u8`, as `→ str in a` says of an
    answer; only `⎕heap` may be written there now (4642).

[ ] let a variable at the top level hold a lambda (4550).  It needs the image to hold an address of code, which is what a string
    at the top level needs as well (9902).
    **Out of date (2026-10-04):** Text at the top level is no longer refused: it is in the image with `in ⎕static` (static-data);
    the lambda is still refused.

[ ] give a lambda's environment back.  One kept in `⎕heap` stays there, as a string a name held does; the third word says which
    allocator it came from, and a sized free needs the size, which a header word before the environment would carry.

[x] let `std.text` answer `"true"` and `"false"` from the image.  `→ str in a, ⎕static` lets them through as they are, the answer
    carrying its allocator; `std.text` and both `⍕` say it.

[x] let a parameter say it holds what is in the image, `s: str in ⎕static`, so that a body may keep it anywhere without copying.
    A caller hands it only what is in the image (4649).

[ ] say in a function type which parameters write the arrays they are handed, so that a function that writes one can be a value
    (4641).

[x] answer a lambda in an arena a parameter names.  Done on 2026-10-19 at the user's direction: `→ fn(u8) in a → u8`, where a
    function type says where a lambda keeps what it brought in, `a` one of the signature's parameters -- `→ T in a` of the
    answer, and `g: fn(u8) in a → u8` is `s: T in a` of a parameter.

[x] decide whether a type parameter may say what it must support.  It does, in `pre` clauses and bundles, and since 2026-10-02
    the body is checked against them where it is written (4645, 4646), `comptime` arms being left to the call.  What follows was
    the question.  The body of a generic function is checked for each set of
    types, so a mistake in one is found by whoever calls it and a generic function nobody calls is never checked at all.  What
    would change that is a language for constraints -- a name for "can be added", "can be ordered" -- which is a design of its
    own and is what Rust, Swift and Go each spend one on.

[x] let a generic function call itself with the types it was given.  It could already, the instance being registered before its
    body is lowered; what was missing was a bound on one calling itself with types that keep changing (4650), which compiled for
    ever.  What it would call is the instance being made, which is not
    finished, so it is refused; what it needs is the function to be registered before its body is lowered, which is a
    rearrangement of the instantiation and nothing deeper.

[x] let a type definition take type parameters.  `type Pair(T') = first: T' ; second: T'`, named `Pair(u8)`, records and sums,
    with requirements between the list and the `=`; a value written out takes its types from its fields or from what is wanted.

[x] let a variable at the top level be *given* a lasting reference.  `&g`, `&g.field` and `&g⟦2⟧` of another variable at the
    top level are addresses known once the image is laid out, written into it as a relocation the linker settles (4656 for
    anything else).

[ ] produce a position-independent image.  The image is loaded at the address the linker chose, which is what lets every address
    written into it -- a reference at the top level, a string's bytes, a table's entries -- be settled while linking.  A
    position-independent one would carry each such word as a relative relocation (`R_X86_64_RELATIVE`, `R_AARCH64_RELATIVE`,
    `R_RISCV_RELATIVE`) and apply them itself at startup, there being no dynamic loader: the words are already collected per
    variable (`target/globals.py`), so what is missing is the dynamic section and the code that walks it.

[x] say how long a reference held in a product lives.  A function may not answer with one inside anything else (4531) because
    `static` belongs to one reference and a tuple may hold several, so a name on the whole answer would say nothing about
    which of them it was about.  Writing
    `static` on the field is the obvious half; borrowing per field would need what was deliberately not built, a way of naming
    several lifetimes at once.  Done on 2026-10-17, as the user decided: `static` on the field, a record whose every reference
    is `static` going where such a reference goes, a lifetime name on a field refused (4707); and a tuple written out in an
    answer carrying a lifetime for each reference in it, each held to it.  A lifetime as a parameter of a type is not done.

[ ] let a lifetime name relate two things that are not the answer and a parameter.  A name may stand on several parameters and on
    the answer, which is what a function handing one of its arguments back needs.  What it cannot yet say is that one parameter
    outlives another, which is the thing Rust's `'a: 'b` is for and the thing a type holding a reference would want.

[x] call a named function handed over as a value without going through a shim.  Such a value is two addresses and a definition
    takes no environment, so what the pair points at is code that drops the environment and hands the rest on -- one call more
    than the program wrote.  Rust's answer is to tell the two apart in the type system; the answer here would be a tag, and a
    tag costs a branch at every indirect call to save a call at some of them.  Done on 2026-10-17, neither of those: asked, the
    user chose the static-chain register -- r11, x15, t2 -- which a named function never reads, so the value points at it.

[x] say more about a lambda than `listable`.  Everything else a function writes is about its body, and a body is not what a name
    holds -- but `can_ignore` is about a caller, and `impure` would let a pure function call one that is not.  Both want the type
    to carry them, which is the same machinery `listable` just gained.  Done on 2026-10-17, as the user decided: a function type
    is pure unless it says `@[impure]`, a lambda's purity is worked out from its body, and `@[can_ignore]` is carried too.

[x] say that two parameters live as long as each other, so that `fn keep(slot: &mut &mut u32, r: &mut u32)` writing
    `slot⌖ ← r` can be allowed.  It is refused outright today (4571), which is sound and costs the programs that
    would have been right.  Rust writes it as `'a` on both, and the call is then what is refused; the lifetime names here cannot
    say it, `⧖a` relating a parameter to the answer and not a parameter to a parameter.  Done on 2026-10-17, as the user decided:
    one lifetime name on the reference a place holds and on another parameter, the body allowed and each call held to it.

[x] count a reference copied into a second name.  The aliasing rule (4563) watches every place a reference is *made* and not
    the ones that are merely handed on, so `let other: &mut u8 = r` is a second way to the place that nothing reports.  Rust
    closes it with moves and reborrows, which need an ownership rule this language does not have.  Done on 2026-10-17, as the
    user decided: a `&mut` copied is a reborrow, lent until the copy's scope ends, shared where the copy is a `&`.

[x] end a reference where it is last used rather than where its scope does.  A reference a name kept lives as long as the name
    (4563), which refuses programs that are plainly fine -- a `&mut` written through and then never mentioned again, followed by
    a second one.  Rust's non-lexical lifetimes are the design to compare against, and what it costs is a liveness analysis where
    today there is a list.  Done on 2026-10-17: `sema/liveness.py`, a borrow counted while its keeper is live, and each end
    written to the report log (`borrow-ended`) where the checker stopped counting it.

[x] release a reference when the name that kept it is bound to something else.  `moving ← &mut m` makes a second row and leaves
    the first, so what `moving` used to name stays lent for the rest of the scope.  Nothing is wrong with the programs that
    result; what is refused is a place that could have been lent again.  Done on 2026-10-17: a borrow is kept by a value of
    the name, which another value does not keep, each end logged with its cause.

[x] tell two elements of one array apart when lending them.  An element is part of the array, so lending one lends the array
    (4563); `&mut v⟦0⟧` and `&mut v⟦1⟧` are two places and are refused as one.  Rust offers `split_at_mut` for
    exactly this, and what it needs here is a way of saying that two indices differ.  Done on 2026-10-17 for indices known
    while compiling; one worked out while running still lends the array.

[x] let a lifetime name stand on something that is not a parameter of this call -- a field of a parameter, or a parameter of an
    enclosing lambda.  A name is looked for in the parameters' types (4567) and provenance is checked against those (4561); both
    would follow a longer path, and what stops it today is that nothing needs it yet.  Done on 2026-10-17: a name on a member of
    a tuple is that member's, a lambda's may be one of the signature around it, and a function's type carries them.

[x] hand a function a lambda whose answer lives as long as a name of the caller's.  Done on 2026-10-19 at the user's direction:
    a callee's lifetime name stands, at each call, for what the arguments carrying it live as long as, and a lambda written as
    the argument may leave its answer's lifetime out and take the parameter's (4930 where it does not live long enough).
    Left over:
    [ ] **a call through a function value, and one of a generic function**, which still compare lifetime names as written.
    [ ] **a lambda answering a tuple of references** inheriting the parameter's names member by member; only one reference
        answered is taken from the parameter.

[ ] let a reference name an array, a list or a string.  A reference names a place holding one value (4536); those are already
    several values or already a place, and what a reference to one would mean -- the place the value is, or the place the elements
    are -- is a question worth answering rather than guessing.

[ ] report a narrowing that is written down and cannot fit.  `⎕narrow(300u32, ⌜u8⌝)` is a result that is always the error, and
    both operands are written down -- which is exactly the shape a division by a written zero is warned about in.  What stops it
    being the same warning is that a generator emitting a constant narrow is not obviously making a mistake.

[ ] decide what narrowing a floating-point number to a whole one means (4548).  The three conditions are about the ends of a
    type; what is after the point is a fourth question, and which way it goes -- towards zero, to nearest, refused -- is a
    decision of its own beside the four roundings the language already has.

[ ] decide what converts between two units that measure the same thing.  `unit mph = 1609344 ÷ 3600000 × meter ÷ second` records
    the scale and nothing applies it, so a speed in miles an hour reaches metres a second through `⎕drop` and `⎕unit` and keeps
    its number.  A conversion would generate code, which is the one thing units do not do today, so what has to be settled is
    whether crossing between two units of one dimension is a different operation from crossing between two of different ones.

[ ] let a unit stand on something that is not a number.  A unit belongs to a number (4540), so a `char` and a `bool` cannot carry
    one; whether anything wants to is not clear, and an array carries its unit on what it holds already.

[ ] decide whether a unit reaches across a module boundary.  A `unit` at the top level reaches the file, and a module that
    exports a function taking `u32 ¤meter` names a unit the importing file may never have introduced.  Nothing tests it either
    way yet.

[ ] widen what a parameter's default may be.  Today it is a literal or a value of an enumeration, which is what a call can hand
    over in registers.  A run of elements, a string or a collection is a value in memory, so a default of one would have to say
    whose memory -- the caller's, or one copy the image holds -- and nothing yet needs it.

[ ] consider warning where an attribute cannot have an effect where it stands.  `@[export]` in the file named on the command line
    is the case that prompted it: nothing imports that file, so the attribute says nothing.  It is not an error -- the file is a
    module like any other and may be imported later -- but it is the sort of thing a reader would want told.

[ ] let a walk through `¨` answer an array of arrays.  `ps¨.coords`, where `coords` is a `u8⟦2⟧`, would be a `u8⟦3,2⟧`; today
    an answer that is itself an array is refused, as is a walk over an array with nothing in it, where there is no element to
    say what the answer holds.

[ ] let `¨` write through more than one dimension, `m¨¨.x ← 0u8`, and through a mask of more than one.  A write is a loop over
    the outermost dimension today.

[ ] let a mask a loop picks with be asked a place at a time along more than one dimension.  Along one it is; a mask over a
    table's elements is built, and so is one whose arrays walked have more than one dimension.

[ ] let a read through `¨` walk an array whose length its type does not state.  The answer needs room for all of it, which is
    an allocation -- `in a` on the walk would be the way to say whose.  A write and a loop's mask take no room and already do.

[ ] let `¨` write through a slice, `v⟦1…3⟧¨.x ← 0u8`.  What is written to is named again each turn, so it has to be a place
    named by names and indices (4661), and a slice is not one; giving it a name first is the workaround.

[?] let a loop that can only be left by a `break` answer what the `break` hands over, not a result.  `while §l true:` is
    left by nothing but `break §l v`, so its value is the type of `v` -- yet today it answers `T?`, as every loop without an
    `else` arm does, because nothing asks whether the condition can ever be false.
    Question: how that is said.  Rust has a keyword for it, `loop`, whose value is what its `break`s hand over and which needs
    no `else`; Zig and Go write `while (true)` and `for {}` and have no loop expression to type.  The choices here are a `loop`
    keyword of the same kind, or recognising a condition the compiler knows is always true -- `true` itself, or any constant --
    and typing `while` that way, which needs no new word but makes the loop's type depend on whether its condition folds.

[x] let the compile-time evaluator work out a power written raised, `n²`.  A constant expression can (it folds over the syntax
    itself), but a function it runs cannot: the evaluator refuses it (7000), and a call to such a function in a constant is
    refused with it.  Done on 2026-10-17, with the same rule for `ⁿ` and for folding: a negative exponent is one divided by the
    positive power, and nought to one has no answer -- which folding had crashed on and `ⁿ` had answered as a fraction.  A
    division with no answer in a call the compiler runs is read by `??`, as it is when the program runs.

[x] coroutines.  Done on 2026-10-03, stackless, as [spec/coroutines.md](spec/coroutines.md) proposed and with its seven
    questions taken at their recommendations: `fn g(…) ⇉ T` with `yield` and `yield ⇉`, written into a loop that can see it and
    otherwise split at its stops and walked through calls.  What is left is in the entries below.

[x] a cursor over an iterator.  Done on 2026-10-03 as `⎕start(q)` and `⎕resume(c)`, which make and step a walk under way of
    anything a loop walks.  What was asked, `⎕iter(q)`: part of the decision that an iterator is a description each walk starts afresh, so that
    stepping one by hand has something of its own to step.  The cursor a list has is built for a list -- where the list is and how
    far along -- and has no spelling as a type; an iterator's would be its description and a state, and wants the cursor's
    operators (`⌖`, `⇧`) and its truth value, and a type to hold it in.

[ ] let an iterator leave the call that made it, where what it walks does: its description in `⎕heap` or `in a` instead of the
    frame, and the lifetime of what it reads in its type -- `⇉u8 in a`, as a lambda's environment says where it lasts.  Refused
    today (4670).

[ ] hand on a pick whose mask is asked a place at a time over an array of no stated length.  Handing on builds the mask, which needs
    room for one truth value per element; describing the mask's operands instead, and asking them in `⎕nextN`, would need the
    expression lowered again inside that function.

[ ] hand on a walk whose state is more than one word: a range, whose state is a number of its own type, and `⎕enumerate`, which
    adds a count.  Neither can be a value yet (4443, 4515), so nothing asks.

[ ] read a field of a record taken out of a tuple by several names.  `foreach i, p := ⎕enumerate(ps)` over an array of records
    refuses `p.age` (4103, "not a module"), the name taken apart not counting as one holding a record; a tuple member of a record
    type read through a name has the same gap.

[x] let a variable at the top level be an array of whatever it is given, `let g: ⟦⟧ = …`.  A top-level variable's type is read
    before its value is worked out, so the element has nothing to come from yet; it is refused as not implemented.  Done on
    2026-10-17: the value there is something the compiler knows, so its type is read off the writing -- the shape from how deep
    and wide it goes, the element from an entry with a suffix or else the first entry.

[x] let a generator take type parameters.  Done on 2026-10-03: compiled once per set of types, written into a loop with them
    renamed and bound, or walked through calls.  What follows was the reason it was not done at first.  Refused as not implemented: its body is compiled once, as a function, and one per set
    of types is what a generic function's instances already are -- the two have to be put together.

[x] let a generator from another module be called.  Done on 2026-10-03: through the module's name, walked through calls.  A call resolves only a generator of the same file: one written into a loop
    would be its syntax lowered with this file's names, and one walked through calls a function of the other module.

[x] let a generator with `defer` be walked through calls.  Done on 2026-10-03; 4674 is retired.  What was wanted: a walk that stops early has to finish the generator, which wants
    the loop's way out to resume it once more with "finish now" -- Python's `GeneratorExit`, C++'s `destroy`.

[x] let a generator walk itself.  Done on 2026-10-03, with the inner frames in the arena `in` names.  What was wanted: the inner frames in an arena the generator names, `yield ⇉ walk(child) in scratch`.

[x] hand out what `yield ⇉` delegates to in one step.  Done on 2026-10-03 for a generator's call.  What was wanted, C++23's
    `elements_of`: a walk of a tree through
    calls resumes each level between the leaf and the root for every element.

[x] a scheduler over the `io_uring` ring, on coroutines that take something back.  Done on 2026-10-03: `⌖⇉T ⇇ U in ⎕heap`,
    `⎕await`, `std.spawn` and `std.run`, the third use in [spec/coroutines.md](spec/coroutines.md).

[x] let a task read.  `std.read` waits where it is called, so a task reading holds up every other; it wants a `read` answering a
    `Pending` as `write` does, and the buffer it reads into kept where the task is -- in `⎕heap`, or the task's own frame.
    **Out of date (2026-10-04):** `std.io.Reader.start` starts a read without waiting; a `std` read for tasks, with the buffer
    where the task is, is what remains.
    Done 2026-10-20: `std.read_start` reads into room the ring keeps, and `std.received` hands the bytes to the task as a list
    of its own once it has the answer -- the ring's room, adopted with `⎕span(at, n, ⌜[u8]⌝)`.

[x] let a task wait in a function it calls.  `⎕await` is a `yield`, so only a task's own body waits; a helper that waits is a
    generator delegated to with `yield ⇉`.  Whether that is enough, or the language wants a function that may wait without being
    a generator -- Kotlin's `suspend`, Rust's `async fn` -- is open.
    Done 2026-10-20: delegation, as the user chose.  `yield ⇉` now passes what the walk is resumed with on to the delegate,
    which it did not, and a generator may answer something when it ends (`→ T`), which `yield ⇉` written as a value takes.


[ ] open a directory.  `std.fs.Dir` is made only by `std.fs.State.cwd`, which answers `AT_FDCWD`; a `Dir.open_dir` answering a
    `Dir` with a descriptor of its own, closed like the devices it opens (`@[closed_by]`), is what makes `openat` worth having
    beyond the one directory.  Zig's `Dir.openDir` and `Dir.close` are the shape.
    **Out of date (2026-10-04):** `std.fs.State.cwd` is a singleton now, reached by `std.fs.cwd()`, and `Dir.open` takes `&Dir`.

[ ] make a device out of a descriptor without spelling out the ring.  A program that has a descriptor from somewhere else -- a
    pipe made with `⎕syscall` -- writes `std.io.Writer(.fd ← n, .ring ← init.io.error.ring)`, copying the address from a device
    it holds.  A `State.adopt(&init.fs, n, ⌜Writer⌝)` would say it once.

[x] work out a table in the image while compiling.  `std.error_names` is written out beside `Error` because neither way of building
    it from `⎕fields(⌜Error⌝)` works: an initializer `in ⎕static` that calls a function is refused (4636), there being no
    evaluation of one while compiling, and a macro cannot splice a run of entries of any length into a literal.  Either would do.
    Done 2026-10-20, both: a call in an initializer `in ⎕static` is run while compiling, which `std.error_names` now is, and
    `⁂$args` spreads what a macro holds into a collection it writes, a set's pieces taken in pairs where a dictionary is wanted.

[x] decide whether filling a container a function made itself is impure.  A function that makes `let names: mut ⸨Colour: str⸩ =
    ⸨⸩` and puts entries in it is refused as writing memory it did not make (4479): the entries go into `⎕heap`, which outlives the
    call.  What it changes is nothing anyone else can see until it answers the table, which is what D's weakly pure functions and
    Rust's `&mut`-free functions allow.
    Done 2026-10-19: the user decided it is not impure.  A table this call made -- a literal or what a set operator answers --
    may be filled and taken from by a pure function; one handed in or at the top level may not.

[x] a table a pure function made and then rebound.  `d ← ⸨…⸩` in one arm of an `if` leaves `d` a block parameter where the arms
    meet, and whether the table is the function's own is asked as the entry is written, before the function is complete: a loop's
    back edge might still bring in somebody else's.  So that `d` is taken as not made here (4479).  Asking at the end of the body,
    with every edge in place, would answer it.
    Done 2026-10-20: a write whose table arrives where branches meet is asked once the body is complete, by every edge into the
    block.

[x] hand a table a pure function made to a function that fills it.  The callee writes a table it was handed, so it is
    `@[impure]` and so is every caller -- even one whose table nobody else can reach.  It is the same question the TODO entry on
    a pure function writing through an array it was handed asks, of a table.
    Done 2026-10-20: the user decided a function filling a table it was handed is not impure, at most opaque.  Filling a
    writable table parameter is no effect; the caller is asked whether what it handed over is its own (4934).

[x] ask a lambda's and a function type's `@[impure]` whether anything needs it.  4933 asks it of a definition's body; one written
    before `λ` or in a parameter's type says what a value may do rather than what a body does, and nothing checks that the value
    handed over ever does it.
    Done 2026-10-20: before `λ` it was already refused (3204).  A function type's is asked of every slot it stands in, over the
    whole unit, with the functions calling through it (4935).

[x] a set operator into a writable set.  `let s: mut ⸨u8⸩ = a | b` is refused as `|` on integers (4205), where the same with `s`
    read-only is accepted: the writable type wanted is not matched against the read-only operands when the operator is looked up.
    Done 2026-10-20: the operands are asked for the set, not for one that may be written, and what the operator answers is of
    the writable type, being new.
