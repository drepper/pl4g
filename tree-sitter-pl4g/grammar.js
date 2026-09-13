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
  ],

  // A comment may stand anywhere, so it is an extra; both kinds are single
  // tokens, because anything used as an extra has to be one.  The longer of
  // the two wins where both match, which is what makes `※※` a documentation
  // comment rather than an ordinary one that happens to start with a mark.
  extras: $ => [/[ \t\r]/, $.doc_comment, $.line_comment],

  word: $ => $.identifier,

  rules: {
    source_file: $ => seq(repeat($._newline), repeat($._item)),

    // -- items -------------------------------------------------------------

    _item: $ => seq(
      choice($.function_definition, $.variable_definition, $.module_import),
      repeat($._newline),
    ),

    function_definition: $ => seq(
      repeat($.attribute_list),
      'fn',
      field('name', $.identifier),
      field('parameters', $.parameter_list),
      $._return_arrow,
      field('return_type', $.type),
      field('body', $._block),
    ),

    parameter_list: $ => seq('(', optional(sepBy(',', $.parameter)), ')'),

    // The glyph is canonical and `->` is an accepted substitute, which the
    // compiler takes and warns about; a grammar that refused it would disagree
    // with the compiler about what is a program.
    _return_arrow: _ => choice('→', '->'),

    parameter: $ => seq(
      field('name', $.identifier), ':', field('type', $.type),
    ),

    variable_definition: $ => seq(
      repeat($.attribute_list),
      'let',
      field('name', $.identifier),
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
      repeat($.attribute_list),
      'let',
      field('name', $.identifier),
      ':', '=',
      'import', '(', field('source', $.string_literal), ')',
    ),

    mutable: _ => 'mut',

    type: $ => $.identifier,

    // -- attributes --------------------------------------------------------

    attribute_list: $ => seq('@[', sepBy(',', $.attribute), ']',
                             repeat($._newline)),

    attribute: $ => seq(
      field('name', $.identifier),
      optional(seq('(', optional(sepBy(',', $.attribute_argument)), ')')),
    ),

    attribute_argument: $ => choice(
      seq(field('name', $.identifier), '=', $._attribute_value),
      $._attribute_value,
    ),

    _attribute_value: $ => choice(
      $.integer_literal, $.string_literal, $.boolean_literal, $.identifier,
    ),

    // -- blocks and statements ---------------------------------------------

    _block: $ => choice($.layout_block, $.explicit_block),

    // A line holding nothing but a comment ends no statement and opens no
    // block, but it is still a line: the newline before it is given out, and
    // another follows the next line that has something on it.  Letting the
    // newlines repeat is what lets a comment stand anywhere a statement can.
    layout_block: $ => seq(
      ':', repeat1($._newline), $._indent,
      repeat1($._statement_line), $._dedent,
    ),

    explicit_block: $ => seq('{', sepBy(';', $._statement), '}'),

    _statement_line: $ => seq($._statement, repeat1($._newline)),

    _statement: $ => seq(repeat($.attribute_list), $._bare_statement),

    _bare_statement: $ => choice(
      $.variable_statement,
      $.assignment,
      $.return_statement,
      $.expression_statement,
    ),

    // A `let` inside a block, whose terminator the block supplies.
    variable_statement: $ => seq(
      'let',
      field('name', $.identifier),
      ':',
      optional($.mutable),
      optional(field('type', $.type)),
      '=',
      field('value', $._expression),
    ),

    assignment: $ => seq(
      field('target', $.identifier), '←', field('value', $._expression),
    ),

    return_statement: $ => seq('return', optional($._expression)),

    expression_statement: $ => $._expression,

    // -- expressions -------------------------------------------------------
    //
    // The precedences are the ones `spec/spec.md` states: bitwise "and" binds
    // tighter than "exclusive or", which binds tighter than "or", and an
    // operator written before its operand binds tighter than any of them.

    _expression: $ => choice(
      $.binary_expression,
      $.unary_expression,
      $.member_expression,
      $.parenthesized_expression,
      $.integer_literal,
      $.string_literal,
      $.boolean_literal,
      $.identifier,
    ),

    binary_expression: $ => choice(
      prec.left(1, seq($._expression, field('operator', '|'), $._expression)),
      prec.left(2, seq($._expression, field('operator', '^'), $._expression)),
      prec.left(3, seq($._expression, field('operator', '&'), $._expression)),
    ),

    unary_expression: $ => prec(4, seq(field('operator', '~'), $._expression)),

    // Something named through the module it belongs to, which binds tighter
    // than any operator: `a.b & c` is `(a.b) & c`.
    member_expression: $ => prec(5, seq(
      field('base', $._expression), '.', field('name', $.identifier),
    )),

    parenthesized_expression: $ => seq('(', $._expression, ')'),

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
      // This list grows with the types the language has.
      optional(/[iu](8|16|32|64)/),
    )),

    string_literal: _ => token(seq(
      '"',
      repeat(choice(/[^"\\\n]/, seq('\\', /[^\n]/))),
      '"',
    )),

    boolean_literal: _ => choice('true', 'false'),

    identifier: _ => /[A-Za-z_][A-Za-z0-9_]*/,

    // Neither kind swallows the newline after it: the layout depends on that
    // newline, and a comment that took it would end a block.
    doc_comment: _ => token(seq('※※', /[^\n]*/)),
    line_comment: _ => token(seq('※', /[^\n]*/)),
  },
});

function sepBy(separator, rule) {
  return optional(seq(rule, repeat(seq(separator, rule))));
}
