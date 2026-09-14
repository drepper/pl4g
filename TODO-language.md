To Do List for the PL4g language
================================

`[ ]` open, `[x]` done, `[?]` needs a decision before it can be started -- such an entry carries a
`Question:` paragraph saying what is undecided and what the choices are.

[x] expressions can use `(` and `)` for grouping, like most other languages.  Done with the expression parser.

[x] there is no way to call a function.  Done: `f(a, b)`, positional, binding tighter than every operator and to whatever stands
    immediately before it.  A call hands over exactly what the function takes and each argument has the type of its parameter
    (4211, 4213); only a function can be called (4210); a call to a function that answers with nothing has no answer to use
    (4212), except as a statement of its own and after `return` in a function that also answers with nothing.
    Still open, and the only part of the original question that was: whether arguments may also be named, as an attribute's are.
    Nothing in what was built forecloses it.

[ ] let a function say that its answer must not be dropped, so that a call whose value is discarded can be reported where the
    answer was the point.  Rust has `#[must_use]` for this and reports it as a warning; the shape here would be an attribute, and
    the rule that a statement's value must be used is what it would switch back on for that one function.

[x] the conditional half of the control flow: `if`, `elif` and `else`.  Done: a condition with no parentheses around it, which
    must be a `bool` -- a number is not a condition -- then a body in either notation, zero or more `elif` with the same shape and
    an optional `else`, which is the last arm there is.  It produces a value where one is wanted of it, and then needs an `else`.
    It needed no new machinery: the block the arms join at, the names it carries across, the memory token it merges and the value
    it hands out were all built for `match`.

[x] add tuple types.  Done: `〈a, b〉` is a tuple and `〈T, T〉` the type of one, and names written next to
    each other take one apart in a definition or an assignment.  In registers a tuple is one register per member, which is the
    result type's arrangement generalized.

[ ] answer with a tuple that wants more registers than the convention has.  Refused today (8501): a tuple is one register per
    member and these conventions answer in two per kind, so a tuple of three integers cannot be answered with.  What every ABI
    does instead is hand the callee a place to put it, which needs the caller to reserve one -- so it waits on holding a value in
    memory at all, which the entry in TODO-pypl4g.md carries.

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

[ ] take a key out of a collection.  There are two states an entry has and not three, because nothing takes one out: a probe
    therefore stops at the first empty entry.  Taking one out needs a third state -- given up -- that a probe walks past and an
    insertion may use, and a count of those so that a table full of them is rebuilt rather than grown.  What it also needs is a
    spelling, and there is none: Python writes `del d[k]` and `s.discard(x)`, and this language has no statement that removes
    anything from anything.

[ ] a dictionary whose value is of any type.  Refused today (4445): an entry is words and what goes in one has to fit in one.
    What it needs is the entry stride to be computed per instantiation from the layout of the value, which the table already
    carries as a field, and a copy of that many bytes where a word is copied now.  The key's restriction is separate and stays:
    one word is what lets one table serve every instantiation.

[ ] a collection at the top level.  Refused today (9902), because a table is built by running code and a variable at the top level
    is bytes in the image.  The module already has constructors; what this needs is for the front end to emit one.

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

[ ] let a program write an iterator.  There is one implementor of the protocol and the compiler is it.  What a second one needs:
    a way to write a type with a `next` that answers a result, and a rule that says a `foreach` over a value of such a type calls
    it.  Neither is much on its own, and both wait on something to iterate over that is not a range -- a collection, most
    likely, whose iterator is the reason the protocol is a protocol.

[ ] a range as a value.  Refused today (4443): a range stands where a loop takes its values from and nowhere else.  Making it a
    value means a type for it, a layout, and a rule about what a range of one type compared with a range of another means; Rust
    has all of that and needs it because its ranges are iterators like any other.  Worth doing when something wants to pass one.

[ ] a step that is computed rather than written down.  Refused today (4441).  Which way a range runs follows from the sign of the
    step, and that decides which comparison ends the loop; a computed step needs both comparisons and a choice between them, or a
    loop written so that the choice is made once before it starts.  The second is what a real implementation does and is worth
    doing when a program wants it.

[ ] leaving a loop early.  There is no `break` and no `continue`, and no `loop` that answers with what a `break` hands it.  A
    loop is a statement for that reason: with nothing that leaves it early, every way out is the condition, and a construct whose
    only way out produces nothing produces nothing.  Rust's `loop` is the shape to compare against if `break` arrives.

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

[ ] work out a constant expression in the front end.  `t⟦1 + 1⟧` is refused (4460) although a reader can see what it
    comes to: the compiler folds after the front end, by which time every type has been settled -- and settling a type is what the
    index is needed for.  What it wants is the folding the optimizer already does, done over the syntax instead, which is a small
    evaluator over literals and the arithmetic operators.

[ ] let an array's length be a name.  `u8⟦SIZE⟧` is refused (4447) where `t⟦FIRST⟧` is accepted, and the difference is
    only when the question is asked: a tuple's index is settled while a body is being lowered, by which time every definition has
    been collected, and a type is resolved while they still are being.  Accepting a name there today would accept the ones written
    above and refuse the ones written below, which is order-dependence in a language that has none elsewhere.  What it wants is the
    constant value of every top-level variable settled in a pass of its own before any type is resolved.

[ ] assign to one member of a tuple.  Refused (4462), because a tuple is registers and not a place.  What it would mean is binding
    the name to a tuple made of the other members and the new value, which the language can already be told to do by writing that
    out; whether `←` should be a second way of saying it is a question about assignment rather than about tuples, and the same
    question the entry above asks about a row.

[ ] assign a whole row, or a whole array.  `m⟦0⟧ ← r` is refused (4457) and would be a copy of as many elements as the row
    holds.  Nothing in the language copies one aggregate into another yet, and the first thing that does should settle it for
    products and arrays together rather than for arrays alone.

[ ] give a collection or an array a length a program can read.  A loop can walk one and nothing can ask how many there are, which
    a program will want the moment it wants anything but a walk.  For an array whose type says its shape the answer is in the type
    and wants only a spelling; for one that does not, and for a table, it is a word already being carried and wants only a way to
    name it.

[ ] slice a table.  Refused (4459).  A row is a run and a column is not: its elements are a row apart.  A slice that could say so
    would carry a stride beside the place and the count, which is what NumPy does and what turns a slice into a view of any shape.
    Worth doing with the entry above and with the one about arrays that grow, since all three change what a `T⟦⟧` carries.

[ ] let a dimension be told and another not.  Refused (4456): an array either carries its whole shape in the type or none of it.
    A `T⟦,4⟧` -- a run-time number of rows of four -- is the useful case, and it wants a value carrying only the counts the
    type left out, which is a third shape of value for the sake of one case.  Worth it when something asks.

[ ] an array that can grow.  A `T⟦⟧` is a place and a count and owns nothing, so there is nothing to grow: appending
    wants a third thing beside them -- how much room there is -- and an arena to ask for more from, which is what Go's slice header
    carries and what this one deliberately does not while no operation appends.  What it needs first is the operation: there is no
    syntax for appending to anything, and inventing one for arrays alone would be inventing it twice.

[ ] say where the elements of an array live, so that one can be answered with.  Refused today (4454): the elements of an array a
    function made are in that function's own room.  A slice of something that outlives the call is safe and the language cannot say
    so.  Rust says it with a lifetime, Zig by leaving it to the programmer, Go by putting everything a slice can point at on a
    collected heap.  The arena is the shape of an answer here -- a value could name the arena its elements are in, which is what the
    entry about giving an arena back already wants for collections -- and the two should be answered together.

[x] iterate over an array, a set and a dictionary.  Done: four things are iterators and none of them is called.  An array gives
    each element where it has one dimension and each row of the outermost where it has more; a set gives each key; a dictionary
    gives a key and what it stands for as a tuple, which two names take apart.  The loop is written once around three questions --
    is there another, what does this turn give, what does the next turn start from -- which is a `next` answering a result said in
    the three places a loop has room for them.

[ ] a small-array optimization.  A `T⟦⟧` of a few small elements could hold them in the two words it already takes rather
    than pointing at them, the way a small string can.  It costs a check on every read, and it pays only once something allocates
    the elements -- which is the entry about growing.  Worth measuring when there is something to measure.

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

[?] write a value of a product or a sum.
    Question: no syntax has been given, and nothing else can be done with these types until there is one -- a function that takes
    or answers with one compiles as far as the code generator, which refuses it (8501).  Considered, for a product: `Point{x:
    1f64, y: 2f64}` after Rust, Go and Zig, which reads well and takes `{}` that is also the explicit block syntax, though never
    in a place an expression may stand; `Point(x: 1f64, y: 2f64)`, which reuses the call shape and the named-argument question
    that is open for calls anyway; and a bare `(x: 1f64, y: 2f64)` taking its type from the context, which is what the language
    already does for a literal without a suffix.  For a sum the value names the variant as well: `Colour.red`, `Colour{red: ...}`
    or a bare `.red` taking its type from the context, which is Zig's and Swift's.  Whichever is chosen, the two should be one
    shape, since the definitions are.

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

[?] read a field of a product.
    Question: `p.x` is the obvious spelling and is what the grammar already parses for a name reached through a module, so the
    two would be one syntax with two meanings decided by what the base is -- which is what Go, Rust and Zig all do.  A product's
    fields may be reordered by the compiler, so a field is reached by name and never by position.

[?] allowing definition member functions
    Question: this needs a syntax for the receiver.  Considered: Go's `fn (p: Point) length() → f64`, which keeps functions at the
    top level and makes the receiver an ordinary parameter; a block nested inside the type after Rust's `impl`, which groups them;
    and the receiver being implicit, as in C++ and Java, which the language's preference for saying things outright argues
    against.  A second question rides on it: whether a member function is a different kind of thing from a function whose first
    parameter happens to be the type, or only a different way of writing one -- the second is simpler and is what Go does.

[ ] if an attribute definition is followed by an empty line or the end of the file, it is not attached to anything and the
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

[ ] implement `↑` for exponentiation.  Not begun, and it needs a decision first: with a constant exponent it is a few
    multiplications and every one of them checks, which is straightforward; with an exponent that is not known until the program
    runs it is a loop, and the language has no way to write one, so the loop would have to be emitted -- or the operator
    restricted to constant exponents, which is what every language that has an integer `**` other than Python effectively does.

[ ] the same arithmetic on floating-point values, where IEEE infinity and IEEE not-a-number are what an overflow or an underflow
    produces and are what the check looks for.  Waits on the float entry above.

[ ] report an overflow that can be seen at compile time as a compilation error rather than leaving it to fault at run time.  The
    constant folder already computes the answer and declines to fold where it does not fit, which is where the report belongs;
    what it needs is somewhere to report from, the folder being an optimization that does not run at every level.

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

[ ] let the error of a result carry a value: `TYPE1?TYPE2`.  The syntax is parsed and refused (9902), and this is now the only
    part of the result type that is not implemented.  It is not a question about the result type any more: the sum type exists,
    and what is missing is a way to *write* a value of one, which is the `[?]` entry above.  Until something can construct an
    error, such a type is one no program could put anything in.  A decision rides on it: what the error type of a division should
    then be, the two cases a division cannot answer -- a zero divisor and the one overflowing signed pair -- not being told apart
    today.  In memory such a result is the larger of the two payloads and a tag, which is what a sum of two variants already is.

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


Runtime
-------

[?] Create a data type for for an output streams which is used for
    standard output/error.  For now a simple representation with a file descriptor and `write` functions is sufficient.

[x] decide whether `@[export]` should mean one thing or two.  Two: `export` says what a file importing this module may name,
    `visible` says whether the finished image offers the symbol, and neither implies the other.  A definition a module lends and
    nothing imports is now dropped, which is what the one attribute made impossible.

[x] make the use of attributes strict: one list per definition, and no empty argument parentheses.  Everything said about one
    definition is written in one list (3208), and an attribute carrying no arguments is written without parentheses (3209).  The
    principle is now stated in the specification and governs more than attributes: where two ways of writing something would mean
    the same thing in every respect, the language admits one of them.  Surveyed for other places it applies; the ASCII
    substitutes, the brace notation, an omitted type and a named argument all differ in meaning or in what they are for, so none
    of them is a second spelling of one thing.

[ ] consider warning where an attribute cannot have an effect where it stands.  `@[export]` in the file named on the command line
    is the case that prompted it: nothing imports that file, so the attribute says nothing.  It is not an error -- the file is a
    module like any other and may be imported later -- but it is the sort of thing a reader would want told.
