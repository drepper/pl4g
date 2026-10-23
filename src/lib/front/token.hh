// Tokens.
//
// The language is meant to be generated rather than typed, so several operators
// are glyphs outside ASCII.  An ASCII substitute exists only where it is more
// than one character, so a single character stays free for the language.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>

#include "source/location.hh"
#include "util/reflect.hh"


namespace pl4g {

  // The glyphs the lexer knows by name.
  namespace glyph {
    inline constexpr std::string_view comment = "\N{REFERENCE MARK}";
    inline constexpr std::string_view doc_comment = "\N{REFERENCE MARK}\N{REFERENCE MARK}";
    inline constexpr char32_t negative = U'\N{SUPERSCRIPT MINUS}';
    inline constexpr char32_t builtin = U'\N{APL FUNCTIONAL SYMBOL QUAD}';
    inline constexpr char32_t power = U'\N{SUPERSCRIPT LATIN SMALL LETTER N}';
    inline constexpr char32_t opname = U'`';
    inline constexpr char32_t key = U'@';
  } // namespace glyph

  // What the text of an end of line says when blank lines followed it.
  inline constexpr std::string_view blank_line = "blank";


  // The kinds of token, each annotated with how a diagnostic names it.
  enum class TokKind : std::uint8_t {
    ident [[=Describe("identifier")]],
    int_lit [[=Describe("integer literal")]],
    float_lit [[=Describe("floating-point literal")]],
    string_lit [[=Describe("string literal")]],
    char_lit [[=Describe("character literal")]],
    kw_fn [[=Describe("'fn'")]],
    kw_return [[=Describe("'return'")]],
    kw_yield [[=Describe("'yield'")]],
    kw_break [[=Describe("'break'")]],
    kw_continue [[=Describe("'continue'")]],
    kw_defer [[=Describe("'defer'")]],
    kw_let [[=Describe("'let'")]],
    // Before `let` at the top level: a variable worked out once, before the
    // program starts, and read-only from then on.
    kw_constinit [[=Describe("'constinit'")]],
    kw_mut [[=Describe("'mut'")]],
    kw_singleton [[=Describe("'singleton'")]],
    kw_unit [[=Describe("'unit'")]],
    kw_type [[=Describe("'type'")]],
    dollar [[=Describe("'$'")]],
    opname [[=Describe("an operator name")]],
    op [[=Describe("an operator")]],
    open_op [[=Describe("an opening bracket")]],
    close_op [[=Describe("a closing bracket")]],
    kw_pre [[=Describe("'pre'")]],
    kw_post [[=Describe("'post'")]],
    kw_bundle [[=Describe("'bundle'")]],
    kw_macro [[=Describe("'macro'")]],
    kw_match [[=Describe("'match'")]],
    kw_enum [[=Describe("'enum'")]],
    kw_if [[=Describe("'if'")]],
    kw_elif [[=Describe("'elif'")]],
    kw_else [[=Describe("'else'")]],
    kw_while [[=Describe("'while'")]],
    kw_unless [[=Describe("'unless'")]],
    kw_foreach [[=Describe("'foreach'")]],
    kw_comptime [[=Describe("'comptime'")]],
    kw_in [[=Describe("'in'")]],
    kw_true [[=Describe("'true'")]],
    kw_false [[=Describe("'false'")]],
    kw_and [[=Describe("'and'")]],
    kw_or [[=Describe("'or'")]],
    at_lbracket [[=Describe("'@['")]],
    lparen [[=Describe("'('")]],
    rparen [[=Describe("')'")]],
    lbracket [[=Describe("'['")]],
    rbracket [[=Describe("']'")]],
    lbrace [[=Describe("'{'")]],
    rbrace [[=Describe("'}'")]],
    comma [[=Describe("','")]],
    colon [[=Describe("':'")]],
    semicolon [[=Describe("';'")]],
    equals [[=Describe("'='")]],
    arrow [[=Describe("'\N{RIGHTWARDS ARROW}'")]],
    assign [[=Describe("'\N{LEFTWARDS ARROW}'")]],
    dot [[=Describe("'.'")]],
    ampersand [[=Describe("'&'")]],
    pipe [[=Describe("'|'")]],
    caret [[=Describe("'^'")]],
    tilde [[=Describe("'~'")]],
    not_equal [[=Describe("'\N{NOT EQUAL TO}'")]],
    less [[=Describe("'<'")]],
    greater [[=Describe("'>'")]],
    less_equal [[=Describe("'\N{LESS-THAN OR EQUAL TO}'")]],
    greater_equal [[=Describe("'\N{GREATER-THAN OR EQUAL TO}'")]],
    tuple_open [[=Describe("'\N{LEFT ANGLE BRACKET}'")]],
    tuple_close [[=Describe("'\N{RIGHT ANGLE BRACKET}'")]],
    take [[=Describe("'\N{DAGGER}'")]],
    next [[=Describe("'\N{UPWARDS WHITE ARROW}'")]],
    prev [[=Describe("'\N{DOWNWARDS WHITE ARROW}'")]],
    set_open [[=Describe("'\N{LEFT DOUBLE PARENTHESIS}'")]],
    set_close [[=Describe("'\N{RIGHT DOUBLE PARENTHESIS}'")]],
    spread [[=Describe("'\N{ASTERISM}'")]],
    label [[=Describe("'\N{SECTION SIGN}'")]],
    array_open [[=Describe("'\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}'")]],
    array_close [[=Describe("'\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}'")]],
    range [[=Describe("'\N{HORIZONTAL ELLIPSIS}'")]],
    question [[=Describe("'?'")]],
    bottom [[=Describe("'\N{UP TACK}'")]],
    or_else [[=Describe("'??'")]],
    alike [[=Describe("'\N{APPROXIMATELY EQUAL TO}'")]],
    unalike [[=Describe("'\N{NEITHER APPROXIMATELY NOR ACTUALLY EQUAL TO}'")]],
    below_or_alike [[=Describe("'\N{LESS-THAN OR APPROXIMATE}'")]],
    above_or_alike [[=Describe("'\N{GREATER-THAN OR APPROXIMATE}'")]],
    below_not_alike [[=Describe("'\N{LESS-THAN AND NOT APPROXIMATE}'")]],
    above_not_alike [[=Describe("'\N{GREATER-THAN AND NOT APPROXIMATE}'")]],
    logic_and [[=Describe("'\N{LOGICAL AND}'")]],
    logic_or [[=Describe("'\N{LOGICAL OR}'")]],
    logic_xor [[=Describe("'\N{CIRCLED PLUS}'")]],
    logic_nand [[=Describe("'\N{NAND}'")]],
    logic_nor [[=Describe("'\N{NOR}'")]],
    logic_not [[=Describe("'\N{NOT SIGN}'")]],
    plus [[=Describe("'+'")]],
    minus [[=Describe("'-'")]],
    times [[=Describe("'\N{MULTIPLICATION SIGN}'")]],
    divide [[=Describe("'\N{DIVISION SIGN}'")]],
    percent [[=Describe("'%'")]],
    shift_left [[=Describe("'\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}'")]],
    shift_right [[=Describe("'\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}'")]],
    rotate_left [[=Describe("'\N{ANTICLOCKWISE OPEN CIRCLE ARROW}'")]],
    rotate_right [[=Describe("'\N{CLOCKWISE OPEN CIRCLE ARROW}'")]],
    sat_add [[=Describe("'\N{SQUARED PLUS}'")]],
    sat_sub [[=Describe("'\N{SQUARED MINUS}'")]],
    sat_mul [[=Describe("'\N{SQUARED TIMES}'")]],
    concat [[=Describe("'\N{DOUBLE PLUS}'")]],
    length [[=Describe("'#'")]],
    shape [[=Describe("'\N{APL FUNCTIONAL SYMBOL RHO}'")]],
    max [[=Describe("'\N{LEFT CEILING}'")]],
    min [[=Describe("'\N{LEFT FLOOR}'")]],
    floor [[=Describe("'\N{DOWNWARDS ARROW}'")]],
    ceiling [[=Describe("'\N{UPWARDS ARROW}'")]],
    nearest [[=Describe("'\N{UP DOWN ARROW}'")]],
    rounded [[=Describe("'\N{UP DOWN DOUBLE ARROW}'")]],
    power [[=Describe("'\N{SUPERSCRIPT LATIN SMALL LETTER N}'")]],
    divides [[=Describe("'\N{DIVIDES}'")]],
    not_divides [[=Describe("'\N{DOES NOT DIVIDE}'")]],
    lift_open [[=Describe("'\N{TOP LEFT CORNER}'")]],
    lift_close [[=Describe("'\N{TOP RIGHT CORNER}'")]],
    deref [[=Describe("'\N{POSITION INDICATOR}'")]],
    each [[=Describe("'\N{DIAERESIS}'")]],
    iter [[=Describe("'\N{RIGHTWARDS PAIRED ARROWS}'")]],
    takes [[=Describe("'\N{LEFTWARDS PAIRED ARROWS}'")]],
    unit [[=Describe("'\N{CURRENCY SIGN}'")]],
    lambda [[=Describe("'\N{GREEK SMALL LETTER LAMDA}'")]],
    lifetime [[=Describe("'\N{WHITE HOURGLASS}'")]],
    exponent [[=Describe("a raised number")]],
    doc_comment [[=Describe("documentation comment")]],
    newline [[=Describe("end of line")]],
    indent [[=Describe("indentation")]],
    dedent [[=Describe("end of indented block")]],
    eof [[=Describe("end of file")]],
    body [[=Describe("a function's body")]],
  };


  // One token, with the source text it came from.
  struct Token {
    TokKind kind = TokKind::eof;
    Span span;
    // The text it was written as; for an operator name, what stands between
    // the accents; for an end of line, `blank_line` where blank lines followed.
    std::string_view text;
    // An integer's value, a character's code point, an exponent.  A literal
    // too large for 127 bits says so in `int_too_large`.
    __int128 int_value = 0;
    bool int_too_large = false;
    double float_value = 0.0;
    // The type a literal names with a suffix, or empty.
    std::string_view type_suffix;
    // A string literal's value, its escapes resolved.
    std::string str_value;

    // How the token is named in a diagnostic.
    std::string describe() const;
  };


  // The keyword `text` is, if it is one; `TokKind::ident` otherwise.
  TokKind keyword_kind(std::string_view text) noexcept;

  // Whether `name` names an integer type: `u1`..`u32`, `u64`, `i2`..`i32`, `i64`.
  bool is_integer_type_name(std::string_view name) noexcept;

  // Whether `name` names a floating-point type.
  inline bool is_float_type_name(std::string_view name) noexcept { return name == "f32" || name == "f64"; }

} // namespace pl4g
