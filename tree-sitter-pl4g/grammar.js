/// The tree-sitter grammar for PL4G.
///
/// The language has two block syntaxes -- one where indentation delimits a
/// block and one where braces do -- and a block is written in one or the other,
/// never both.  Indentation cannot be expressed in a context-free grammar, so
/// the tokens that stand for it come from the external scanner in `src/scanner.c`:
/// it emits a newline where one ends a statement, and an indent or a dedent
/// where the column changes.  That is the arrangement tree-sitter-python uses,
/// and for the same reason.
///
/// Keep this in step with `spec/spec.md`.  A feature that changes the syntax
/// changes this file in the same commit, and `test/corpus` gains a case for it.

module.exports = grammar({
  name: 'pl4g',

  externals: $ => [
    $._newline,
    $._indent,
    $._dedent,
    // What a colon with something after it on the same line opens.  It stands
    // where the end of line and the indent stand in the other reading, and the
    // scanner produces one or the other: which of the two a block is, is a
    // question about the text after the colon, and the parser cannot ask it.
    $._inline_open,
  ],

  // A comment may stand anywhere, so it is an extra; both kinds are single
  // tokens, because anything used as an extra has to be one.  The longer of
  // the two wins where both match, which is what makes `※※` a documentation
  // comment rather than an ordinary one that happens to start with a mark.
  //
  // An end of line is an extra too, which is how a line break inside brackets
  // is read.  The external scanner is asked first at every position, and where
  // the parser is willing to end a statement it answers with `_newline`, an
  // indent or a dedent, so the layout rules are decided there and this never
  // sees those line breaks.  What is left is the line breaks the parser cannot
  // end a statement at, and inside brackets those are exactly the ones the
  // compiler's lexer suppresses while a bracket is open.  tree-sitter-python
  // reads Python's identical rule the identical way, and for the same reason:
  // the scanner cannot be asked how many brackets are open, because it is not
  // called between two tokens the parser is confident about.
  extras: $ => [/[ \t\r]/, /\n/, $.doc_comment, $.line_comment],

  // `while NAME :` is two statements until the token after the colon is seen:
  // a loop that binds a name to what an iterator gives, or a loop whose
  // condition is that name and whose body begins there.  The compiler's parser
  // asks the same question by looking one token further.
  conflicts: $ => [
    [$._binding_names, $._non_range],
    // How far a range's last end reaches.  An operator that binds tighter than
    // the range belongs to the end, which is what the compiler's precedence
    // table says; the generalized parse is what says it here.
    [$._non_comparison, $.range_expression],
    // What is lifted is a type or an expression, and a name is both.  Which it
    // is, is a question about the program and not about its syntax, so the
    // generalized parse carries both readings until one of them ends at the
    // closing bracket.
    [$._non_range, $._plain_type],
    // A statement may carry attributes and so may a lambda, so `@[...] \u03bb ...`
    // written as a whole statement has two readings.  The compiler takes the
    // statement's, the statement parser reading the list before it looks at
    // what follows; the dynamic precedence below is what says the same here.
    [$.lambda_expression],
  ],

  word: $ => $.identifier,

  rules: {
    source_file: $ => seq(repeat($._newline), repeat($._item)),

    // -- items -------------------------------------------------------------

    _item: $ => seq(
      choice($.function_definition, $.variable_definition, $.module_import,
             $.type_definition, $.enum_definition, $.unit_definition),
      repeat($._newline),
    ),

    // `type NAME = ` and then a sequence of `NAME : TYPE` pairs.  The separator
    // is what says which kind of type it is: `;` for a product, which holds all
    // of its parts at once, and `|` for a sum, which holds one of them.  One
    // pair with no separator to go by is a product.
    //
    // The sequence may be written over several lines in either of the two ways
    // the language already breaks a line -- inside braces, where the scanner
    // gives out no ends of lines at all, or indented under the definition,
    // where an end of line may fall between any two of its tokens.  Neither
    // changes what separates the pairs.
    type_definition: $ => seq(
      optional($.attribute_list),
      'type',
      field('name', $.identifier),
      '=',
      field('parts', $._type_parts),
    ),

    // One pair with nothing to separate it is a product: a record of one field
    // is a useful thing and a choice between one alternative is not.
    _type_parts: $ => choice(
      $._bare_parts,
      seq('{', $._bare_parts, '}'),
      seq($._newline, $._indent, $._bare_parts, repeat($._newline), $._dedent),
    ),

    _bare_parts: $ => choice($.product_parts, $.sum_parts, $.type_part),

    // The separator ends the line it is written on, where the definition is
    // written over several: a line may be broken after it and not before it,
    // which is what keeps "this pair is the last" decidable at the end of a
    // line rather than at the start of the next.
    // `enum NAME [: TYPE]` and the names of its values.  The type says how much
    // room a value takes and nothing else.  It is introduced by a colon, and so
    // is the indented form of the list, which is why a definition that names a
    // type and indents its values carries two of them.
    enum_definition: $ => seq(
      optional($.attribute_list),
      'enum',
      field('name', $.identifier),
      optional(seq(':', field('holder', $.type))),
      choice(
        seq('{', $.enum_values, '}'),
        seq(':', repeat1($._newline), $._indent, $.enum_values,
            repeat($._newline), $._dedent),
      ),
    ),

    enum_values: $ => seq(
      $.enum_value,
      repeat(seq(';', repeat($._newline), $.enum_value)),
    ),

    // A value may say which number it is stored as, or take the name of an
    // earlier one and be that.  Nothing written means the compiler chooses.
    enum_value: $ => seq(
      field('name', $.identifier),
      optional(seq('=', field('value', choice($.integer_literal, $.identifier)))),
    ),

    product_parts: $ => seq(
      $.type_part,
      repeat1(seq(';', repeat($._newline), $.type_part)),
    ),

    sum_parts: $ => seq(
      $.type_part,
      repeat1(seq('|', repeat($._newline), $.type_part)),
    ),

    // `mut` before the type of a field says of a collection what it says of a
    // variable's: entries may be put in it.  A field is never bound to
    // something else, so that is all it can say here.
    type_part: $ => seq(
      field('name', $.identifier), ':', optional($.mutable),
      field('type', $.type),
    ),

    // The arrow and what follows it say what the function answers with.
    // Leaving them off is how a function says it answers with nothing; there is
    // no name to write for that, which is what keeps the two from being two ways
    // of saying one thing.
    // A header with no body is the declaration of a function defined somewhere
    // else, which `@[external]` is what says: the line ends after the header,
    // there being nothing here to run.
    function_definition: $ => seq(
      optional($.attribute_list),
      'fn',
      field('name', $.identifier),
      field('parameters', $.parameter_list),
      optional(seq($._return_arrow, optional($.mutable),
                   field('return_type', $.type))),
      optional(field('body', $._block)),
    ),

    parameter_list: $ => seq('(', sepBy(',', $.parameter), ')'),

    // The glyph is canonical and `->` is an accepted substitute, which the
    // compiler takes and warns about; a grammar that refused it would disagree
    // with the compiler about what is a program.
    _return_arrow: _ => choice('→', '->'),

    // `mut` stands where it stands in a definition, before the type, and says
    // the same thing there: the name may be bound to something else later on.
    // It is no part of the type, so two functions differing only in it are one
    // signature.
    // A default follows the type, written the way a value is bound to a name
    // everywhere else.  It belongs to the function rather than to any call, so
    // it is settled while compiling and every call that leaves the argument out
    // hands over the same value.
    parameter: $ => seq(
      field('name', $.identifier), ':', optional($.mutable),
      field('type', $.type),
      optional(seq('\u2190', field('default', $._expression))),
    ),

    variable_definition: $ => seq(
      optional($.attribute_list),
      'let',
      field('name', $.identifier),
      repeat(seq(',', field('name', $.identifier))),
      ':',
      optional($.mutable),
      optional(field('type', $.type)),
      '=',
      field('value', $._expression),
    ),

    // A module is brought in by what looks like a definition, because that is
    // what it is: a name bound to something.  Nothing about it may be
    // qualified, so there is no place here for `mut` or for a type.
    module_import: $ => seq(
      optional($.attribute_list),
      'let',
      field('name', $.identifier),
      ':', '=',
      // One of the compiler's names and not a keyword, so that a program that
      // wants a variable called `import` may have one.
      '\u2395import', '(', field('source', $.string_literal), ')',
    ),

    mutable: _ => 'mut',

    // How long what a reference names lives, where the type can say it: as long
    // as the program.  It is no keyword -- a program may still have a type
    // called `static` -- and the compiler tells the two apart by looking at
    // what follows; here the word wins wherever a reference type may say it,
    // which is a difference only a type of that name could show.
    lasting: _ => 'static',

    // The other answer to the one question `static` answers: as long as
    // whatever else in this signature carries the same name.  It stands in the
    // same place, so at most one of the two is ever written.
    lifetime: $ => seq('\u29d6', field('name', $.identifier)),

    // A type is a name, and after it the mark that says a value of it may not
    // be there: `TYPE?` is a result whose error carries nothing, `TYPE?ERROR`
    // one whose error is a value of its own.
    // An array is written after what it holds -- `i32\u27e64\u27e7` -- and more than
    // one may follow, which is an array of arrays.  The element type comes
    // first because that is the order it is read in: four of these, not an
    // array of four whose elements are these.
    //
    // One entry per dimension, separated by commas: `i32\u27e63,4\u27e7` is a table.
    // An entry left out says the type does not carry how many there are along
    // that dimension, so `i32\u27e6,\u27e7` is a table of no stated shape.
    //
    // A reference takes the whole of what follows it, array suffixes and all,
    // so `&u8\u27e64\u27e7` is a reference to an array of four and never an array of
    // four references.  Nothing may follow one, which is what makes it
    // unambiguous: a suffix after it would have two readings and no way to
    // choose.
    type: $ => choice(
      seq($._plain_type, repeat($._array_suffix)),
      seq('&', optional($.mutable),
          optional(choice($.lasting, $.lifetime)),
          field('pointee', $.type)),
      // A function written where a value is wanted.  The keyword one is
      // defined with, and then what it takes and what it answers; the
      // parameter names are not here because a type is not a definition.  It
      // takes the whole of what follows it, as a reference does and for the
      // same reason: a suffix after one would have two readings.
      // Right associative because the arrow after one may be its own return
      // type or the return type of a lambda whose parameter it is, and the
      // inner reading is the one the compiler takes: the type is read first
      // and takes what follows it.
      prec.right(seq(optional($.attribute_list),
                     'fn', '(', sepBy(',', field('parameter', $.type)), ')',
                     optional(seq($._return_arrow,
                                  field('return_type', $.type))))),
    ),

    // What a number counts, written after the type it belongs to and before
    // any array suffix: `u8 \u00a4meter\u27e64\u27e7` is four lengths and not a length made
    // of four numbers.  Read left to right, `\u00d7` putting the next base unit above
    // the line and `\u00f7` below it, with a raised number for a power.
    unit_suffix: $ => seq('\u00a4', $._unit_product),

    _unit_product: $ => seq(
      $.unit_factor,
      repeat(seq(choice('\u00d7', '\u00f7'), $.unit_factor)),
    ),

    unit_factor: $ => seq(
      field('name', choice($.identifier, $.string_literal)),
      optional(field('exponent', $.exponent_literal)),
    ),

    // `unit NAME` introduces one measured in nothing but itself; `unit NAME =`
    // says what one of them is in terms of others, numbers and names in one
    // product; and `unit \u00a4FROM \u2192 \u00a4TO` says a value written in one unit may
    // stand where another is wanted, which is about what the program means and
    // not about arithmetic.
    unit_definition: $ => seq('unit', choice(
      seq(field('name', choice($.identifier, $.string_literal)),
          optional(seq('=', $._unit_measure))),
      seq(field('from', $.unit_suffix), $._return_arrow,
          field('to', $.unit_suffix)),
    )),

    _unit_measure: $ => seq(
      choice($.integer_literal, $.unit_factor),
      repeat(seq(choice('\u00d7', '\u00f7'),
                 choice($.integer_literal, $.unit_factor))),
    ),

    _array_suffix: $ => seq('\u27e6', sepBy(',', optional(field('length', $._expression))),
                            '\u27e7'),

    _plain_type: $ => choice(
      seq(
        field('module', optional(seq($.identifier, '.'))),
        $.identifier,
        // The unit comes before the mark that makes it a result, because it
        // belongs to the answer: a result of a length is a result whose answer
        // is a length, and there is nothing about a result for a unit to say.
        optional($.unit_suffix),
        optional(seq('?', optional($.identifier))),
      ),
      // A collection is written the way a value of one is, so that a type and
      // a value of it look alike -- which a parameter list and a call do.
      seq('\u2e28', field('element', $.type),
          optional(seq(':', field('value', $.type))), '\u2e29',
          optional($.unit_suffix)),
      // And a tuple likewise.
      seq('\u3008', sepBy1(',', field('member', $.type)), '\u3009',
          optional($.unit_suffix)),
      // And a list, whose type is written the way a value of one is.
      seq('[', field('element', $.type), ']', optional($.unit_suffix)),
    ),

    // -- attributes --------------------------------------------------------

    // A list holds at least one attribute, and an attribute at least one
    // argument where it is written with parentheses at all: an empty pair of
    // either would be a second spelling of something already spelled.
    attribute_list: $ => seq('@[', sepBy1(',', $.attribute), ']',
                             repeat($._newline)),

    attribute: $ => seq(
      field('name', $.identifier),
      // An attribute carrying no arguments is written without parentheses; an
      // empty pair would be a second spelling of the same thing.
      optional(seq('(', sepBy1(',', $.attribute_argument), ')')),
    ),

    attribute_argument: $ => choice(
      seq(field('name', $.identifier), '=', $._attribute_value),
      $._attribute_value,
    ),

    _attribute_value: $ => choice(
      $.integer_literal, $.string_literal, $.character_literal,
      $.boolean_literal, $.identifier,
    ),

    // -- blocks and statements ---------------------------------------------

    _block: $ => choice($.layout_block, $.explicit_block),

    // A line holding nothing but a comment ends no statement and opens no
    // block, but it is still a line: the newline before it is given out, and
    // another follows the next line that has something on it.  Letting the
    // newlines repeat is what lets a comment stand anywhere a statement can.
    // A block written on one line is the same block with its indentation left
    // out: what opens it is the colon and what closes it is the end of the
    // line, or an `else` or `elif` of the same chain, or a brace.  Both of
    // those are produced by the scanner, so the two readings differ by one
    // token and there is nothing here to decide between them.
    layout_block: $ => seq(
      ':',
      choice(seq(repeat1($._newline), $._indent), $._inline_open),
      repeat1($._statement_line), $._dedent,
    ),

    // There are no ends of lines inside braces, so a semicolon is the only
    // separator there; in the layout notation both separate.  A semicolon is a
    // separator and never a terminator, so what follows one is another
    // statement -- the empty one, where nothing else is written.
    explicit_block: $ => seq('{', optional($._statement_run), '}'),

    // A statement that ends with an indented block has taken the end of its own
    // last line with it -- the dedent comes after that newline, not before --
    // so there is none left for the line to end with.  `match` is the only one
    // of these so far and `if` will be the next.  The four shapes it can end
    // are written out rather than the line end being made optional: optional
    // would let two statements share a line with nothing between them, which
    // is not a program.
    _statement_line: $ => choice(
      seq($._statement_run, repeat1($._newline)),
      $._trailing_block_line,
    ),

    // Higher than the ordinary readings throughout, so that a line ending with
    // a `match` is read as the line that ends with a block rather than as one
    // that has yet to be finished.
    _trailing_block_line: $ => prec(1, seq(
      optional($.attribute_list),
      choice(
        alias($._trailing_variable, $.variable_statement),
        alias($._trailing_assignment, $.assignment),
        alias($._trailing_return, $.return_statement),
        // Higher than the ordinary expression statement, so that a `match`
        // standing alone on a line is read as the line that ends with a block
        // rather than as one that has yet to be finished.
        // A `match` standing as a statement of its own.  It has a node of its
        // own rather than an aliased expression statement so that the match
        // stays a child of it, which is what an editor wants to fold.
        $.match_statement,
        $.if_statement,
        $.while_statement,
        $.foreach_statement,
      ),
    )),

    // Higher than the ordinary readings, for the same reason the line that
    // ends with one is: a line ending here is a line that ended.
    _block_expression: $ => prec(1, choice($.match_expression,
                                           $.if_expression,
                                           $.while_statement,
                                           $.foreach_statement)),

    // The three statements a block expression can end, written as rules of
    // their own so that aliasing one keeps the shape it would have had: an
    // alias over an inline sequence flattens the fields inside it.
    _trailing_variable: $ => seq(
      'let', field('name', $.identifier),
      repeat(seq(',', field('name', $.identifier))), ':', optional($.mutable),
      optional(field('type', $.type)), '=', field('value', $._block_expression),
    ),

    _trailing_assignment: $ => seq(
      field('target', choice($.identifier, $.index_expression,
                             $.element_expression, $.deref_expression,
                             $.member_expression)),
      '\u2190', field('value', $._block_expression),
    ),

    _trailing_return: $ => seq('return', $._block_expression),

    match_statement: $ => prec(2, $.match_expression),

    // `if`, its `elif`s and its `else`.  The condition stands on its own --
    // there are no parentheses around it, because nothing needs them: what ends
    // it is the body, which begins with a colon or a brace, and neither can be
    // part of an expression.
    // `comptime` stands before the keyword of each arm it applies to, and not
    // once for the whole `if`: which arms the compiler settles is a property of
    // each condition rather than of the chain.
    // Right associative because `comptime` after an arm's body may begin the
    // next arm or a statement of its own, and continuing the chain is what it
    // means where an `elif` follows it.
    if_expression: $ => prec.right(seq(
      optional('comptime'), 'if',
      field('condition', $._expression), field('then', $._block),
      repeat(seq(optional('comptime'), 'elif',
                 field('condition', $._expression),
                 field('then', $._block))),
      optional(seq('else', field('else', $._block))),
    )),

    if_statement: $ => prec(2, $.if_expression),

    // `while` runs its body again for as long as the condition holds.  Its
    // condition stands on its own for the reason an `if`'s does, and it is a
    // statement outright: a loop has a way through that runs the body no times
    // at all, and there is nothing for that way to produce.
    // `unless` is the same loop with its condition read the other way round:
    // the body runs until the condition holds.  One word rather than a `\u00ac`,
    // because the conditions it is for are already the negative of what a
    // reader means -- a cursor asked whether a walk is over is the one there is.
    while_statement: $ => seq(
      choice('while', 'unless'), optional(field('label', $.label)),
      field('condition', $._expression), field('body', $._block),
      optional(field('alternative', $.loop_else)),
    ),

    // Where the loop ran out rather than being left by a `break`.  It is what
    // that way through the loop comes to, which is why a loop without one
    // comes to a result: the way that ran out has nothing to give.
    loop_else: $ => seq('else', $._block),

    // What a loop is called, so that `break` and `continue` can say which one
    // they mean.  It stands between the keyword and what the loop runs on,
    // where a reader looks to see which loop this is; the glyph is what keeps
    // it apart from a condition that is a bare name.
    label: $ => seq('\u00a7', field('name', $.identifier)),

    // `foreach` shares `let`'s shape: one or more names, an optional type, an
    // equal sign, and what the loop takes its values from.  `while` written
    // this way is the same statement; after `while` the colon has to be there,
    // because a name on its own followed by a colon is a condition with a body.
    // The colon is always there and the type may be left out, exactly as in a
    // variable: a `foreach` binds a name the way `let` does, so a type written
    // on that name says what a turn gives -- and what the loop takes its turns
    // from may take its own type from that.
    foreach_statement: $ => seq(
      choice(
        seq(optional('comptime'), 'foreach', optional(field('label', $.label)),
            $._binding_names, $._binding_type),
        seq('while', optional(field('label', $.label)),
            $._binding_names, $._binding_type),
      ),
      '=', field('iterable', $._expression), field('body', $._block),
      optional(field('alternative', $.loop_else)),
    ),

    _binding_names: $ => seq(field('name', $.identifier),
                             repeat(seq(',', field('name', $.identifier)))),

    _binding_type: $ => seq(':', optional(field('type', $.type))),

    // What follows a semicolon may be written or may be left out, and leaving
    // it out is the empty statement.  It has no node of its own: there is
    // nothing in the text to give one to, and what matters about it is only
    // that it is a statement, which the compiler is where that is said.
    _statement_run: $ => seq(
      $._statement, repeat(seq(';', optional($._statement))),
    ),

    _statement: $ => seq(optional($.attribute_list), $._bare_statement),

    _bare_statement: $ => choice(
      $.variable_statement,
      $.unit_definition,
      $.assignment,
      $.return_statement,
      $.break_statement,
      $.continue_statement,
      // A loop written with braces ends where the brace does, and the line it
      // stands on ends after it like any other; one written with a colon takes
      // that line ending with it and is read by the rule above instead.
      $.while_statement,
      $.foreach_statement,
      $.expression_statement,
    ),

    // Both name the loop they mean every time.  A jump with no label would mean
    // the loop nearest to it, which is a thing that changes when a loop is put
    // around it.
    break_statement: $ => seq('break', field('label', $.label),
                              optional(field('value', $._expression))),

    continue_statement: $ => seq('continue', field('label', $.label)),

    // `match` takes a value apart.  Its arms stand where the statements of a
    // body would, in either notation, and an arm is a pattern and then a body
    // written the way a function's is -- so that what a body looks like is one
    // thing wherever one appears.  Inside braces the arms follow one another
    // with nothing between them, each ending in the brace that closes it.
    match_expression: $ => seq(
      'match',
      field('subject', $._expression),
      choice(
        seq(':', repeat1($._newline), $._indent, repeat1($.match_arm), $._dedent),
        seq('{', repeat($.match_arm), '}'),
      ),
    ),

    match_arm: $ => seq(
      field('pattern', $.pattern),
      field('body', $._block),
      repeat($._newline),
    ),

    // `TYPE(NAME)` takes the alternative whose type is `TYPE` and binds its
    // value; `\u22a5` in place of the type is the error arm of a result, whose two
    // alternatives may name one type and so cannot both be said by naming one.
    // `_` takes every alternative no earlier arm took and binds nothing, so no
    // name follows it.  It is an ordinary identifier to the scanner; what makes
    // it the wildcard is where it is written, which the compiler is where that
    // is said.
    pattern: $ => choice(
      seq(choice('\u22a5', field('type', $.type)),
          optional(seq('(', field('name', $.identifier), ')'))),
    ),

    // A `let` inside a block, whose terminator the block supplies.
    variable_statement: $ => seq(
      'let',
      field('name', $.identifier),
      // Names next to each other take a tuple apart, one name per member.
      repeat(seq(',', field('name', $.identifier))),
      ':',
      optional($.mutable),
      optional(field('type', $.type)),
      '=',
      field('value', $._expression),
    ),

    // What may stand on the left is a place: a name, a field of a record, an
    // entry of a dictionary or an element of an array, each written the way one
    // is read.
    assignment: $ => seq(
      field('target', choice($.identifier, $.index_expression,
                             $.element_expression, $.deref_expression,
                             $.member_expression)),
      // Targets next to each other take a tuple apart, one name per member.
      repeat(seq(',', field('target', $.identifier))),
      '←', field('value', $._expression),
    ),

    // A line may end without an end of line where the statement took it, so
    // `return` and what may follow it has to say which reading wins: the one
    // that takes the expression, which is the only one that can be meant.
    return_statement: $ => prec.right(seq('return', optional($._expression))),

    expression_statement: $ => $._expression,

    // -- expressions -------------------------------------------------------
    //
    // The precedences are the ones `spec/spec.md` states, loosest first: the
    // logical operators, then the comparisons, then bitwise "or", "exclusive
    // or" and "and" in that order, and an operator written before its operand
    // binds tighter than any of them.
    //
    // Three layers, and the layering is what states the precedence between
    // them: a logical operator takes anything below it, a comparison takes
    // anything below *itself*, and the bitwise operators take only their own
    // kind.  That also says, in the shape of the rules rather than with
    // precedence numbers, which of them do not chain -- there is nowhere for a
    // bare comparison to appear inside another one except within parentheses.

    _expression: $ => choice(
      $.failure_expression,
      $.logical_expression,
      $._non_logical,
    ),

    // Everything an expression can be except a logical operator applied to two
    // things.  `\u22bc` and `\u22bd` take one of these on each side, which is how the
    // grammar says they do not associate.
    _non_logical: $ => choice(
      $.comparison_expression,
      $._non_comparison,
    ),

    // Everything an expression can be except a comparison.  A comparison takes
    // one of these on each side rather than an expression, which is how the
    // grammar says that `a < b < c` is not written: a comparison answers with a
    // truth value, so a second one beside it would be comparing that answer.
    _non_comparison: $ => choice(
      $.range_expression,
      $._non_range,
    ),

    // A range is written with two ends or with three.  It is not a binary
    // operator: three written with one would nest, which is not what `a\u2026b\u2026c`
    // means, so the ends are listed and there are never more than three.  They
    // are parsed one level in, so that what is written on either side of the
    // glyph binds to the end and not to the range.
    range_expression: $ => prec.right(4, seq(
      field('start', $._non_range), '\u2026', field('stop', $._non_range),
      optional(seq('\u2026', field('step', $._non_range))),
    )),

    // Everything an expression can be except a range.
    _non_range: $ => choice(
      $.array_literal,
      $.list_literal,
      $.element_expression,
      $.match_expression,
      $.if_expression,
      $.tuple_literal,
      $.set_literal,
      $.dictionary_literal,
      $.index_expression,
      $.take_expression,
      $.step_expression,
      $.or_else_expression,
      $.try_expression,
      $.raised_expression,
      $.binary_expression,
      $.unary_expression,
      $.lambda_expression,
      $.address_expression,
      $.deref_expression,
      $.call_expression,
      $.member_expression,
      $.parenthesized_expression,
      $.lifted_expression,
      $.float_literal,
      $.integer_literal,
      $.string_literal,
      $.character_literal,
      $.boolean_literal,
      $.identifier,
    ),

    // '\u2227' and 'and' bind alike, as do '\u2228' and 'or': the word and the glyph say
    // the same thing and differ only in what they evaluate.  '\u22bc' and '\u22bd' share a
    // level of their own and take a non-logical operand on each side, since
    // neither is associative and `a \u22bc b \u22bc c` has two meanings.
    logical_expression: $ => choice(
      prec.left(1, seq($._expression,
                       field('operator', choice('\u2228', 'or')), $._expression)),
      prec.left(2, seq($._expression, field('operator', '\u2295'), $._expression)),
      prec.left(3, seq($._expression,
                       field('operator', choice('\u2227', 'and')), $._expression)),
      seq(field('left', $._non_logical),
          field('operator', choice('\u22bc', '\u22bd')),
          field('right', $._non_logical)),
    ),

    // All six share one level.  '<=' and '>=' are the accepted substitutes for
    // the two glyphs, by the rule that a substitute is never one character.
    comparison_expression: $ => seq(
      field('left', $._non_comparison),
      field('operator', choice('=', '\u2260', '<', '>', '\u2264', '\u2265',
                               '<=', '>=',
                               // Whether one number divides another answers a
                               // truth value about two numbers, which is a
                               // comparison's shape and so a comparison's
                               // level.
                               '\u2223', '\u2224',
                               // The same six asked of the tolerance rather
                               // than of the values, for floating point.
                               '\u2245', '\u2247', '\u2a85', '\u2a86',
                               '\u2a89', '\u2a8a')),
      field('right', $._non_comparison),
    ),

    // The arithmetic binds tighter than the bitwise operators, multiplication
    // tighter than addition -- the order of writing, and the one place C's
    // order of operations was not a mistake.
    binary_expression: $ => choice(
      // Joining two arrays binds looser than everything that works out what
      // goes in one, so `a + 1u8 \u29fa b` joins what the two sides came to.
      // '++' is the accepted substitute, by the rule that a substitute is never
      // one character: this language has no operator that adds one to
      // something, so two plus signs begin nothing else.
      prec.left(4, seq($._non_comparison, field('operator', choice('\u29fa', '++')),
                       $._non_comparison)),
      // Making something of a shape, which is APL's rho doing APL's job.
      prec.left(5, seq($._non_comparison, field('operator', '\u2374'),
                       $._non_comparison)),
      prec.left(6, seq($._non_comparison, field('operator', '|'), $._non_comparison)),
      prec.left(7, seq($._non_comparison, field('operator', '^'), $._non_comparison)),
      prec.left(8, seq($._non_comparison, field('operator', '&'), $._non_comparison)),
      // The larger and the smaller of two, which bind looser than the
      // arithmetic: `a + 1u8 \u2308 b` is the larger of what the two sides came to,
      // which is how APL reads them and what makes them useful without
      // parentheses.
      prec.left(9, seq($._non_comparison,
                       field('operator', choice('\u2308', '\u230a')),
                       $._non_comparison)),
      prec.left(10, seq($._non_comparison,
                       field('operator', choice('+', '-', '\u229e', '\u229f')),
                       $._non_comparison)),
      prec.left(11, seq($._non_comparison,
                       field('operator', choice('\u00d7', '\u00f7', '%', '\u22a0',
                                                '\u00ab', '\u00bb', '\u21ba', '\u21bb')),
                       $._non_comparison)),
      // Raising to a power binds tighter than multiplying, as it does on paper
      // and in every language that has it, and is right associative for the
      // same reason: `a \u207f b \u207f c` is `a` raised to what `b \u207f c` came to, which
      // is the only reading that is not a longer way of writing `a \u207f (b \u00d7 c)`.
      prec.right(12, seq($._non_comparison, field('operator', '\u207f'),
                         $._non_comparison)),
    ),

    // Both bind tighter than every operator written between two operands, so
    // `\u03bb PARM: TYPE, \u2026 [CAPTURES] \u2192 TYPE` and a body: a function written
    // where a value is wanted.  The parameter list has no parentheses round
    // it, there being nothing before it for them to separate it from -- what
    // ends it is the capture list, the arrow or the body, and none of the
    // three can be part of a parameter.
    lambda_expression: $ => prec.dynamic(-1, prec.right(seq(
      // What is said about a lambda is said the way it is said about a
      // function, before the thing it describes.  Only what a caller reads off
      // the type may stand here, which the compiler checks and this does not.
      // The lower dynamic precedence is for the one place the list could
      // belong to either: written as a whole statement, it is the statement's.
      optional($.attribute_list),
      '\u03bb',
      sepBy(',', field('parameter', $.lambda_parameter)),
      optional(field('captures', $.capture_list)),
      optional(seq($._return_arrow, field('return_type', $.type))),
      field('body', $._block),
    ))),

    lambda_parameter: $ => seq(
      field('name', $.identifier), ':', optional($.mutable),
      field('type', $.type),
    ),

    // `&` says the variable itself rather than what it held, which is the same
    // `&` a reference type is written with and says the same thing.
    // `[=]` and `[&]` say of every name the body reaches what a list of names
    // says of the ones in it.  They are told from a list by what follows the
    // mark: a name follows `&` in a list, and the closing bracket follows it
    // here.
    capture_list: $ => seq('[', choice('=', '&', sepBy1(',', $.capture)), ']'),

    capture: $ => seq(optional('&'), field('name', $.identifier)),

    // `\u00ac ready \u2227 seen` is `(\u00ac ready) \u2227 seen` and `\u00ac (a < b)` needs its parentheses --
    // the same rule '!' follows in C, Go and Rust.
    unary_expression: $ => prec(13, seq(
      field('operator', choice('~', '\u00ac', '#', '\u2374', '\u2308', '\u230a',
                               '\u2193', '\u2191', '\u2195', '\u21d5',
                               '\u2223', '\u2224')),
      $._non_comparison,
    )),

    // `&` before an operand asks for a reference to the place it names; `&`
    // between two asks for their bits in common.  Which it is, is decided by
    // where it stands and by nothing else, as it is in C.
    address_expression: $ => prec(13, seq(
      '&', optional($.mutable), field('place', $._non_comparison),
    )),

    // What is at the place a reference names, written after it so that
    // reaching further into what it answers reads left to right.
    deref_expression: $ => prec(14, seq(
      field('reference', $._non_comparison), '\u2316',
    )),

    // A call and a member both bind tighter than any operator, and to whatever
    // stands immediately before them: `a.b(c)` calls `a.b`, and `f(x) & 1` ands
    // what the call answered with.  Arguments are positional.
    // `EXPR?` hands back the answer inside a result and leaves the function
    // with the error where there is none.  It binds as tightly as a call does,
    // to whatever stands immediately before it.
    // A number written raised is the power operator with that number on the
    // right.  It binds where a call and an index bind, which is to whatever
    // stands immediately before it: `a\u00b2\u00d7b` squares `a`, and `f(x)\u00b2` squares
    // what the call answered with.
    raised_expression: $ => prec(14, seq(
      field('value', $._non_comparison), field('exponent', $.exponent_literal),
    )),

    // The digits written raised, with a raised minus where one is written --
    // which is refused for what it would mean and not for how it is written,
    // so the grammar takes it.
    exponent_literal: _ => /\u207b?[\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+/,

    try_expression: $ => prec(14, seq(
      field('value', $._non_comparison), '?',
    )),

    // `EXPR ?? DEFAULT`: the answer, or the value written instead.  Tighter
    // than the comparisons and looser than everything that computes a number,
    // and right associative, so that `a ?? b ?? c` is "a, or else b, or else
    // c" -- the only reading of it that is well typed.
    or_else_expression: $ => prec.right(6, seq(
      field('value', $._non_comparison),
      '??',
      field('default', $._non_comparison),
    )),

    // Several values travelling as one, written between angle brackets rather
    // than parentheses so that a tuple of one thing is still a tuple and not
    // the thing with brackets round it.
    tuple_literal: $ => seq('\u3008', sepBy1(',', $._spreadable), '\u3009'),

    // `\u2e28a, b\u2e29` is a set and `\u2e28k: v\u2e29` a dictionary; which of the two a
    // collection is is decided by its first entry, and one written with nothing
    // in it is neither until the type it is wanted as says which.
    // An array written down, and a lookup in one.  The same brackets: a type,
    // a value of it and a lookup in it all look alike, as a collection's do.
    array_literal: $ => seq('\u27e6', sepBy(',', $._expression), '\u27e7'),

    // A list, whose elements need not be of one type once there is boxing
    // and which for now must be.  It is written the way its type is.
    list_literal: $ => seq('[', sepBy(',', $._expression), ']'),

    // Which element is wanted, or -- where a range stands there -- which run
    // of them.  One index per dimension, in the order the shape was written
    // in.  It binds as tightly as a call does, and to whatever stands
    // immediately before it.
    element_expression: $ => prec(14, seq(
      field('array', $._non_comparison),
      '\u27e6', sepBy1(',', field('index', $._expression)), '\u27e7',
    )),

    set_literal: $ => seq('\u2e28', sepBy(',', $._expression), '\u2e29',
                          optional($._in_arena)),

    dictionary_literal: $ => seq(
      '\u2e28',
      sepBy1(',', seq(field('key', $._expression), ':',
                      field('value', $._expression))),
      '\u2e29',
      optional($._in_arena),
    ),

    // Which allocator a collection comes out of.  A name and not an
    // expression: what goes here is a place the allocator keeps its state in,
    // and a place is named rather than computed.
    _in_arena: $ => seq('in', field('arena', $.identifier)),

    // Whether a set holds a key, or what a dictionary has for one.  It binds
    // as tightly as a call does, and to whatever stands immediately before it.
    index_expression: $ => prec(14, seq(
      field('collection', $._non_comparison),
      '\u2e28', field('key', $._expression), '\u2e29',
    )),

    // `\u2020` before a lookup takes the key out and answers what the lookup
    // would have: it undoes one, so it is written before one and before
    // nothing else.
    take_expression: $ => prec(13, seq(
      '\u2020', choice($.index_expression, $.element_expression,
                       $._non_comparison))),

    // `\u21e7` and `\u21e9` move a walk over a list along and back.  They bind where
    // every operator written before its operand binds.
    step_expression: $ => prec(13, seq(
      field('operator', choice('\u21e7', '\u21e9')), $._non_comparison)),

    call_expression: $ => prec(14, seq(
      field('function', $._non_comparison),
      '(', sepBy(',', field('argument', choice($._spreadable,
                                               $.named_argument))), ')',
    )),

    // `.NAME \u2190 VALUE`: an argument that says which parameter it is for.  The
    // dot cannot be a member access, there being nothing on its left for a
    // member to belong to, which is what lets it mark the name as a
    // parameter's.
    named_argument: $ => seq(
      '.', field('name', $.identifier),
      '\u2190', field('value', $._expression),
    ),

    // A tuple or a fixed-size array standing for several of the things around it
    // rather than for one.  It is a rule of the two lists that admit it -- a
    // call's arguments and a tuple's members -- rather than an expression,
    // because nowhere that wants exactly one value has room for it.
    _spreadable: $ => choice($._expression, $.spread),

    spread: $ => seq('\u2042', field('several', $._expression)),

    // Something named through the module it belongs to, which binds tighter
    // than any operator: `a.b & c` is `(a.b) & c`.
    member_expression: $ => prec(14, seq(
      field('base', $._non_comparison), '.', field('name', $.identifier),
    )),

    parenthesized_expression: $ => seq('(', $._expression, ')'),

    // `\u22a5` and `\u22a5 VALUE`: a result that has no answer, written out.  What it
    // is a failure *of* is not written with it -- it is whatever stands where
    // it stands -- so there is nothing here but the glyph and, where the
    // error carries something, what it carries.  Read the way `return` is
    // read, taking the whole of an expression where one begins there.
    // It is an expression and never an operand: what follows the glyph is the
    // whole of what comes after it, so `\u22a5 a + b` carries the sum and there is
    // no reading in which `\u22a5 a` is the left side of anything.  That is the
    // rule `return` follows, and it is why this stands at the top of the
    // hierarchy rather than among the operands.
    failure_expression: $ => prec.right(seq('\u22a5', optional($._expression))),

    // What is lifted out of the program and into the compiler.  The brackets
    // are what keeps this grammar context-free: a type's name and a value's
    // name are both identifiers, and a type written out in full -- `u8\u27e64\u27e7`,
    // `\u2e28u8: u16\u2e29` -- is not an expression at all, so without them what follows
    // `\u2395typeof` could not be read without knowing what the names meant.
    //
    // Both readings are listed and the generalized parse takes whichever fits;
    // for a bare name they both do, and which it is, is a question about the
    // program and not about its syntax.
    lifted_expression: $ => seq('\u231c', choice($.type, $._expression), '\u231d'),

    // -- tokens ------------------------------------------------------------

    // A literal may be written in any of four bases, may separate its digits
    // with underscores anywhere, may be made negative by a leading superscript
    // minus with nothing between, and may name its type with a suffix.
    integer_literal: _ => token(seq(
      optional('⁻'),
      choice(
        seq(/0[xX]/, /[0-9a-fA-F_]+/),
        seq(/0[oO]/, /[0-7_]+/),
        seq(/0[bB]/, /[01_]+/),
        /[0-9][0-9_]*/,
      ),
      // Only a real type name is a suffix.  The compiler reads any identifier
      // there and then reports one that is not a type, but it reports it as an
      // error, so a literal with a wrong suffix is not a program either way.
      // Any width up to thirty-two, and sixty-four: `u1` through `u32`, `i2`
      // through `i32`, and the two widest.  Which of those the signed side has
      // is the compiler's to refuse -- `i1` parses here and is not a type.
      optional(/[iu]([1-9]|[12][0-9]|3[0-2]|64)/),
    )),

    // A floating-point literal is written as C writes one: a decimal with a
    // point or an exponent, or a hexadecimal with `0x` and a `p`.  A point with
    // no digit after it is not part of one -- `1.x` is a member of something --
    // which is why the fraction requires a digit.  Whole digits with a
    // floating-point suffix are one too: `3f64` is the number three.
    float_literal: _ => token(seq(
      optional('⁻'),
      choice(
        seq(/0[xX]/, /[0-9a-fA-F_]+/,
            optional(seq('.', /[0-9a-fA-F_]*/)),
            /[pP][+-]?[0-9_]+/),
        seq(/[0-9][0-9_]*/, '.', /[0-9][0-9_]*/,
            optional(/[eE][+-]?[0-9_]+/)),
        seq(/[0-9][0-9_]*/, /[eE][+-]?[0-9_]+/),
        seq(/[0-9][0-9_]*/, /f(32|64)/),
      ),
      optional(/f(32|64)/),
    )),

    string_literal: _ => token(seq(
      '"',
      repeat(choice(/[^"\\\n]/, seq('\\', /[^\n]/))),
      '"',
    )),

    // One code point between apostrophes, with the escapes a string takes.  An
    // escape that names a code point by number carries its digits with it,
    // which is why the backslash is followed by a character and then by however
    // many hexadecimal digits that character called for.
    character_literal: _ => token(seq(
      "'",
      choice(/[^'\\\n]/, seq('\\', /[^\n]/, repeat(/[0-9a-fA-F]/))),
      "'",
    )),

    boolean_literal: _ => choice('true', 'false'),

    // A name the compiler provides begins with a quad, which is what keeps it
    // from ever being a name a program wrote.  A program may read and assign
    // the ones that exist and may not define one, which the grammar does not
    // say: it is a rule about what a name means, not about how one is written.
    // A trailing quotation mark is part of the name, which is what makes `T'`
    // a type a call settles rather than a name and a character literal.  It
    // cannot begin one, so `'a'` is still a character; the only thing it costs
    // is a name immediately followed by a character literal with nothing
    // between them, which nothing readable writes.
    identifier: _ => /[A-Za-z_][A-Za-z0-9_']*|\u2395[A-Za-z_][A-Za-z0-9_'@]*/,

    // Neither kind swallows the newline after it: the layout depends on that
    // newline, and a comment that took it would end a block.
    doc_comment: _ => token(seq('※※', /[^\n]*/)),
    line_comment: _ => token(seq('※', /[^\n]*/)),
  },
});

function sepBy(separator, rule) {
  return optional(sepBy1(separator, rule));
}

function sepBy1(separator, rule) {
  return seq(rule, repeat(seq(separator, rule)));
}
