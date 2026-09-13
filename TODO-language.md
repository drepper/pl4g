To Do List for the PL4g language
================================

[ ] expressions can use `(` and `)` for grouping, like most other languages.

[ ] add floating-point types `f16`, `f32`, `f64`, and `bfloat`.  `f16` and `bfloat` optional, if there is no hardware support.
    Allow both the decimal and the hexadecimal format as specified in the C standard.

[ ] add a product type

[ ] allowing definition member functions

[ ] add a sum type

[ ] if an attribute definition is followed by an empty line or the end of the file, it is not attached to anything and the
    action is global.

[ ] add an attribute `@[required(NAME)]` which does not have to be attached to a variable, function, or statement but can be.
    The attribute checks whether the implementation or supported language version supports the feature named by NAME.  The
    supported values of NAME and their meaning must be documented.  Examples are the existence of types like `bfloat`.
    If the attribute is not attached to anything, the compilation is aborted with an error if the feature is not supported.
    If the attribute is attached to a function or variable, the respective definition is dropped without any further error.

[ ] specify the error path that exists before `io_uring` does.  The specification requires a message when a required CPU or
    operating system feature is missing, forbids depending on any system runtime, and routes all input and output through
    `io_uring` -- whose own setup can fail before there is any object to report through.  A raw system call is the only thing
    left; what it may assume needs saying.

[ ] the sentence "the grammar has to be context-free, there is no process definitions in order" in spec/spec.md is garbled.  It
    is read as "there is no *need to* process definitions in order", so a forward reference at the top level is legal, and the
    compiler collects every top-level definition before checking any body on that reading.  Confirm or correct the wording.

[x] there is no way to write a negative number.  Done by the entry below on `⁻`: the sign is part of the literal, so the most
    negative value of a type is a literal like any other and not a negation of one.

[ ] there is no way to say what symbol a function should be known by without also saying how it is called.  A function
    declaring a foreign calling convention keeps its bare name, which covers calling into another world; an attribute naming the
    symbol directly would cover the rest.  Nothing needs it yet.

[ ] decide whether a local variable nothing reads should be an error rather than a warning.  Go refuses to compile one, Rust and
    C warn.  PL4G warns (4006) and an optimized build drops the variable.  Since the language is meant to be generated rather than
    written, refusing one may catch a generator bug that a warning would let through.

[x] Add negative number literals with a leading `⁻`, no space.  Compare with what APL does in the documentation.  This allows more
    streamlined parsing in the presence of subtraction.  Done: the sign is read where the number is read, a space after it is an
    error (2008), and `-` stays free for subtraction alone.

[x] Allow thousand separators in integers and the whole part of float literals.  Use `_`.  Do not be strict wrt the rules of the
    thousand separator, just ignore the `_` characters.  Done for integers in every base, and tested; the float half follows when
    float literals exist, since there are none to put a separator in yet.

[ ] implement operations on numbers.  Use `+` for addition, `-` for subtraction, `×` for multiplication, `÷` for division.
    Both sides of the operator need to have the same type or one side can be an untyped integer/float.  The result must fit into
    the respective type.  An overflow that can be detected at compile time is a compilation error.  Unless it can be proven to
    not be necessary (e.g., using value range propagation) all operation using the operators above need an overflow check.  The
    program aborts with a backtrace in case of an overflow/underflow.  For floating-point values the values like IEEE Inf and
    IEEE NaN cause the trap for an overflow/underflow.  Implement `↑` for exponentiation.

[ ] Implement strings.  Literal strings are always encoded in UTF-8.  They are not mutable.  Variables can be defined as type `str`
    which can be initialized with a string literal.  If not marked mut a `str` object cannot be modified and has a fixed length and
    should be placed in `.rodata`.  A `mut str` object can be resized and changed and therefore has to be an object which can
    reference allocated memory elsewhere.  Small string optimizations are welcome.

[x] Implement boolean values.  Only the values `true` and `false` are defined.  Assigning any other value is an error.  Done and
    tested in both directions; a constant `bool` goes in `.rodata` and a `mut` one beside the variables, like any other value.

[ ] implement bitwise operations.  Use `&` for bitwise AND, `|` for bitwise OR, `^` for bitwise XOR, `~` for bitwise NOT.  Usable
    only on integer values.

[ ] implement shifting and rotating of integers.  Use `«` and `»` for shifting and `↺` and `↻` for rotation.

[ ] implement saturated operations.  Use `⊞` for saturated addition, `⊟` for saturated subtraction, `⊠` for saturated multiplication

[ ] implement exact comparisons, defined for numbers, strings, booleans.  Use `=`, `≠`, `<`, `>`, `≤`, `≥` as the operators.  Using
    `=` as a comparison of floats creates a warning that this is an unsafe operation which can be suppressed with `@[ignore(…)]`.
    it is possible to use untyped integer literals when comparing it with a float.  In this case the integer must have a representation
    which is exact with the numbe rof bits available in the float format.  Allow ASCII representations for ≤ and ≥.

[ ] implement comparisons for floating point values that are sensitive to small, accumulated errors.  Use this equivalency table:
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

[ ] implement binary logic operations.  Use this table for guidance:
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

[ ] Create a data type for input streams and one for output streams.  The former is used for standard input, the latter for
    standard output/error.  For now a simple
