To Do List for the PL4g language
================================

`[ ]` open, `[x]` done, `[?]` needs a decision before it can be started -- such an entry carries a
`Question:` paragraph saying what is undecided and what the choices are.

[ ] expressions can use `(` and `)` for grouping, like most other languages.  Needs the expression parser in TODO-pypl4g.md.

[?] there is no way to call a function.  A function can be defined and is emitted, but nothing in the language names one in an
    expression, so every function other than the startup function, a constructor or a destructor is unreachable by construction.
    Question: the obvious syntax is `f(a, b)`, which the attribute argument list already uses and which nothing else claims.  What
    is not obvious is what goes with it: whether arguments may be named, as attribute arguments already are, which would fit the
    language but means overload resolution has to consider names; and whether a call may be written where its value is discarded,
    which interacts with the rule that a function's last expression is its result.

[?] there is no control flow.  A function body is a straight-line list of statements: there is no `if`, no loop and no way to
    choose between two values.  The intermediate representation has had branches and block parameters from the start and nothing
    generates them.
    Question: what shape, and how much?  Considered for the conditional: `if COND:` in the layout syntax with the same block rules
    as a function body, which needs no new ideas; a conditional expression, since the language already makes the last expression a
    function's result, so `if` yielding a value would be consistent and would remove the need for a separate ternary form; and a
    pattern-matching form, which the sum type will want anyway and which might be the only branching construct rather than a second
    one.  For repetition the question is whether a generated language needs a general loop at all, or whether iteration over
    something is enough -- a generator emitting a counted loop can emit whatever the language gives it.

[ ] add floating-point types `f16`, `f32`, `f64`, and `bfloat`.  `f16` and `bfloat` optional, if there is no hardware support.
    Allow both the decimal and the hexadecimal format as specified in the C standard.
    Where it stands: `f32` and `f64` already name a type and have a size and an alignment, but nothing can hold a value of one --
    there is no float literal, no float constant in the representation, and no backend rule.  Needs the register allocator, since
    the float register classes are declared on all three targets and none is ever used, and the calling convention has to say where
    a float is passed.  "Optional if there is no hardware support" is what `@[required(NAME)]` above is for.

[?] add a product type
    Question: no syntax has been given for declaring one, for naming its fields, or for writing a value of one, and the `type`
    keyword is lexed but has no parse rule.  Considered: `type Point = (x: i32, y: i32)` with a value written `(x: 1i32, y: 2i32)`,
    which reuses the parenthesis-and-colon shape the language already has for parameters and attribute arguments; a braced form
    after Rust, Go and Zig, which reads well but takes `{}` that is already the explicit block syntax; and a layout form, one
    field per line, which fits the rest of the language but makes an anonymous product awkward to write inside an expression.
    Field access is a second question: `p.x` after almost everything, or an index, given that the specification lets the compiler
    reorder fields and so has to keep names and positions apart.

[?] allowing definition member functions
    Question: this needs the product type first, and then a syntax for the receiver.  Considered: Go's `fn (p: Point) length() → f64`,
    which keeps functions at the top level and makes the receiver an ordinary parameter; a block nested inside the type after Rust's
    `impl`, which groups them; and the receiver being implicit, as in C++ and Java, which the language's preference for saying
    things outright argues against.  A second question rides on it: whether a member function is a different kind of thing from a
    function whose first parameter happens to be the type, or only a different way of writing one -- the second is simpler and is
    what Go does.

[?] add a sum type
    Question: no syntax has been given, and the specification already leans on sum types for fallible operations, so this decides
    how errors are written throughout.  Considered: `type Result = i32 | Error`, an untagged-looking union that is tagged
    underneath; a named-variant form after Rust and Swift, `type Result = Ok(i32) | Err(Error)`, which is what pattern matching
    wants; and Zig's tagged union, which names the tag type explicitly.  Whichever it is, reading one needs a way to ask which
    variant a value holds, and that is control flow -- so this also waits on the question about `if` below.

[ ] if an attribute definition is followed by an empty line or the end of the file, it is not attached to anything and the
    action is global.  Implementable now and needed by `@[required(NAME)]` below.  Note that the parser currently skips newlines
    after an attribute list precisely so that one can stand on its own line above the thing it belongs to, so the rule has to
    distinguish a blank line from a line break.

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

[ ] implement operations on numbers.  Needs the expression parser, the register allocator and conditional branches in
    TODO-pypl4g.md, and the overflow check needs the unwinder there: decided, a fault aborts with a real multi-frame backtrace
    rather than a bare trap.  Use `+` for addition, `-` for subtraction, `×` for multiplication, `÷` for division.
    Both sides of the operator need to have the same type or one side can be an untyped integer/float.  The result must fit into
    the respective type.  An overflow that can be detected at compile time is a compilation error.  Unless it can be proven to
    not be necessary (e.g., using value range propagation) all operation using the operators above need an overflow check.  The
    program aborts with a backtrace in case of an overflow/underflow.  For floating-point values the values like IEEE Inf and
    IEEE NaN cause the trap for an overflow/underflow.  Implement `↑` for exponentiation.

[?] Implement strings.  Literal strings are always encoded in UTF-8.  They are not mutable.  Variables can be defined as type `str`
    which can be initialized with a string literal.  If not marked mut a `str` object cannot be modified and has a fixed length and
    should be placed in `.rodata`.  A `mut str` object can be resized and changed and therefore has to be an object which can
    reference allocated memory elsewhere.  Small string optimizations are welcome.
    The immutable half is implementable as soon as there is a product type to hold the address and the length; literals already
    lex, and `.rodata` already exists.  The `mut` half is not.
    Question: a `mut str` can be resized, so it needs a heap, and the specification forbids depending on any system runtime -- so
    the allocator is the compiler's to emit.  Which?  A `mmap`-backed bump allocator that never frees is a page of code and is
    enough for a program that builds strings and exits; a real size-class allocator is a great deal more and is the thing every
    later feature will also want; using the system `malloc` is out by the no-runtime rule.  A second question: what happens when
    an allocation fails, given that the language routes errors through sum types but a string being appended to is not obviously a
    fallible operation the way a read is.  Small-string optimization is an implementation choice underneath whichever answer.

[x] Implement boolean values.  Only the values `true` and `false` are defined.  Assigning any other value is an error.  Done and
    tested in both directions; a constant `bool` goes in `.rodata` and a `mut` one beside the variables, like any other value.

[ ] implement bitwise operations.  Use `&` for bitwise AND, `|` for bitwise OR, `^` for bitwise XOR, `~` for bitwise NOT.  Usable
    only on integer values.  Needs the expression parser and the register allocator; no overflow can arise, so this is the
    cheapest of the operator entries and a good first use of the parser.

[ ] implement shifting and rotating of integers.  Use `«` and `»` for shifting and `↺` and `↻` for rotation.  Needs the
    expression parser and the register allocator.  One thing to settle while writing it: what a shift by more than the width does,
    since the three architectures disagree and leaving it to the hardware would be exactly the kind of surprise the language
    refuses.

[ ] implement saturated operations.  Use `⊞` for saturated addition, `⊟` for saturated subtraction, `⊠` for saturated multiplication.
    Needs conditional branches as well as the parser and the allocator, since saturating is choosing between the result and the
    bound.  These are the operations that do not trap, so they are what a program uses where an overflow is meant.

[ ] implement exact comparisons, defined for numbers, strings, booleans.  Needs the expression parser and the register allocator;
    comparing needs no branch, since the result is a value.  Strings need the string entry first and floats the float entry.  Use
    `=`, `≠`, `<`, `>`, `≤`, `≥` as the operators.  Using
    `=` as a comparison of floats creates a warning that this is an unsafe operation which can be suppressed with `@[ignore(…)]`.
    it is possible to use untyped integer literals when comparing it with a float.  In this case the integer must have a representation
    which is exact with the numbe rof bits available in the float format.  Allow ASCII representations for ≤ and ≥.

[ ] implement comparisons for floating point values that are sensitive to small, accumulated errors.  Needs the float entry above
    and the exact comparisons.  Use this equivalency table:
    | Tolerant | Exact | Reads as |
    |----------|-------|----------|
    | `≅` | `=` | alike |
    | `≇` | `≠` | not alike |
    | `⪅` | `≤` | less than or alike |
    | `⪆` | `≥` | greater than or alike |
    | `⪉` | `<` | less than and not alike |
    | `⪊` | `>` | greater than and not alike |

    The comparison here is true as long as the difference is not larger than a limit.  For the time being, make this a builtin
    variable with the value 1e-13.  In future this will be a runtime-time variable.  Compare this with `⎕CT` in APL.

[ ] implement binary logic operations.  The boolean type they work on exists.  The six glyph operators need the expression parser
    and the register allocator; `and` and `or` short-circuit, so those two also need conditional branches, which is the only place
    the language will generate a branch before there is an `if`.  Use this table for guidance:
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


Runtime
-------

[?] Create a data type for input streams and one for output streams.  The former is used for standard input, the latter for
    standard output/error.  For now a simple
    Question: this entry ends mid-sentence -- "For now a simple" -- so what it asks for is not yet written down.  What was the rest?
    It also needs the product type, since a stream is at least a descriptor and a buffer, and it needs the decision about the
    error path above, since a stream is where a failed write would be reported and the first stream has to exist before there is
    anywhere to report that it could not be made.
