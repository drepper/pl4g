// The names and glyphs the compiler gives a meaning to, as pypl4g's
// front/token.py has them: `HEAP_NAME` is `names::heap_name`.  Generated from
// that file; a name added there is added here the same way.
#pragma once

#include <string_view>


namespace pl4g::names {

  inline constexpr std::string_view comment_glyph = "\N{REFERENCE MARK}";
  inline constexpr std::string_view doc_comment_glyph = "\N{REFERENCE MARK}\N{REFERENCE MARK}";
  inline constexpr std::string_view arrow_glyph = "\N{RIGHTWARDS ARROW}";
  inline constexpr std::string_view assign_glyph = "\N{LEFTWARDS ARROW}";
  inline constexpr std::string_view negative_glyph = "\N{SUPERSCRIPT MINUS}";
  inline constexpr std::string_view not_equal_glyph = "\N{NOT EQUAL TO}";
  inline constexpr std::string_view less_equal_glyph = "\N{LESS-THAN OR EQUAL TO}";
  inline constexpr std::string_view greater_equal_glyph = "\N{GREATER-THAN OR EQUAL TO}";
  inline constexpr std::string_view alike_glyph = "\N{APPROXIMATELY EQUAL TO}";
  inline constexpr std::string_view unalike_glyph = "\N{NEITHER APPROXIMATELY NOR ACTUALLY EQUAL TO}";
  inline constexpr std::string_view below_or_alike_glyph = "\N{LESS-THAN OR APPROXIMATE}";
  inline constexpr std::string_view above_or_alike_glyph = "\N{GREATER-THAN OR APPROXIMATE}";
  inline constexpr std::string_view below_not_alike_glyph = "\N{LESS-THAN AND NOT APPROXIMATE}";
  inline constexpr std::string_view above_not_alike_glyph = "\N{GREATER-THAN AND NOT APPROXIMATE}";
  inline constexpr std::string_view tuple_open_glyph = "\N{LEFT ANGLE BRACKET}";
  inline constexpr std::string_view tuple_close_glyph = "\N{RIGHT ANGLE BRACKET}";
  inline constexpr std::string_view set_open_glyph = "\N{LEFT DOUBLE PARENTHESIS}";
  inline constexpr std::string_view set_close_glyph = "\N{RIGHT DOUBLE PARENTHESIS}";
  inline constexpr std::string_view next_glyph = "\N{UPWARDS WHITE ARROW}";
  inline constexpr std::string_view prev_glyph = "\N{DOWNWARDS WHITE ARROW}";
  inline constexpr std::string_view take_glyph = "\N{DAGGER}";
  inline constexpr std::string_view spread_glyph = "\N{ASTERISM}";
  inline constexpr std::string_view label_glyph = "\N{SECTION SIGN}";
  inline constexpr std::string_view array_open_glyph = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}";
  inline constexpr std::string_view array_close_glyph = "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}";
  inline constexpr std::string_view range_glyph = "\N{HORIZONTAL ELLIPSIS}";
  inline constexpr std::string_view heap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}heap";
  inline constexpr std::string_view empty_arena_name = "\N{APL FUNCTIONAL SYMBOL QUAD}arena";
  inline constexpr std::string_view static_name = "\N{APL FUNCTIONAL SYMBOL QUAD}static";
  inline constexpr std::string_view opname_glyph = "`";
  inline constexpr std::string_view hole_glyph = "$";
  inline constexpr std::string_view opener_category = "Ps";
  inline constexpr std::string_view closer_category = "Pe";
  inline constexpr std::string_view answer_name = "\N{APL FUNCTIONAL SYMBOL QUAD}answer";
  inline constexpr std::string_view entry_name = "\N{APL FUNCTIONAL SYMBOL QUAD}entry";
  inline constexpr std::string_view wildcard_name = "_";
  inline constexpr std::string_view question_glyph = "?";
  inline constexpr std::string_view or_else_glyph = "??";
  inline constexpr std::string_view bottom_glyph = "\N{UP TACK}";
  inline constexpr std::string_view builtin_glyph = "\N{APL FUNCTIONAL SYMBOL QUAD}";
  inline constexpr std::string_view wrap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}wrap";
  inline constexpr std::string_view typeof_name = "\N{APL FUNCTIONAL SYMBOL QUAD}typeof";
  inline constexpr std::string_view enumerate_name = "\N{APL FUNCTIONAL SYMBOL QUAD}enumerate";
  inline constexpr std::string_view narrow_name = "\N{APL FUNCTIONAL SYMBOL QUAD}narrow";
  inline constexpr std::string_view widen_name = "\N{APL FUNCTIONAL SYMBOL QUAD}widen";
  inline constexpr std::string_view import_name = "\N{APL FUNCTIONAL SYMBOL QUAD}import";
  inline constexpr std::string_view syscall_name = "\N{APL FUNCTIONAL SYMBOL QUAD}syscall";
  inline constexpr std::string_view auxv_name = "\N{APL FUNCTIONAL SYMBOL QUAD}auxv";
  inline constexpr std::string_view acquire_name = "\N{APL FUNCTIONAL SYMBOL QUAD}acquire";
  inline constexpr std::string_view release_name = "\N{APL FUNCTIONAL SYMBOL QUAD}release";
  inline constexpr std::string_view at_name = "\N{APL FUNCTIONAL SYMBOL QUAD}at";
  inline constexpr std::string_view span_name = "\N{APL FUNCTIONAL SYMBOL QUAD}span";
  inline constexpr std::string_view bytes_name = "\N{APL FUNCTIONAL SYMBOL QUAD}bytes";
  inline constexpr std::string_view is_record_name = "\N{APL FUNCTIONAL SYMBOL QUAD}isrecord";
  // What kind of type a type is, as text the compiler settles; and a sum's parts.
  inline constexpr std::string_view kind_of_name = "\N{APL FUNCTIONAL SYMBOL QUAD}kindof";
  inline constexpr std::string_view alternatives_name = "\N{APL FUNCTIONAL SYMBOL QUAD}alternatives";
  inline constexpr std::string_view fields_name = "\N{APL FUNCTIONAL SYMBOL QUAD}fields";
  inline constexpr std::string_view typename_name = "\N{APL FUNCTIONAL SYMBOL QUAD}typename";
  inline constexpr std::string_view str_of_name = "\N{APL FUNCTIONAL SYMBOL QUAD}str";
  // Text from bytes, checked to be well-formed UTF-8: the way in `⎕bytes` is the way out of.
  inline constexpr std::string_view text_name = "\N{APL FUNCTIONAL SYMBOL QUAD}text";
  inline constexpr std::string_view one_run_name = "\N{APL FUNCTIONAL SYMBOL QUAD}one_run";
  inline constexpr std::string_view empty_name = "\N{APL FUNCTIONAL SYMBOL QUAD}empty";
  inline constexpr std::string_view address_name = "\N{APL FUNCTIONAL SYMBOL QUAD}address";
  inline constexpr std::string_view key_glyph = "@";
  inline constexpr std::string_view syscall_number_prefix = "\N{APL FUNCTIONAL SYMBOL QUAD}sc@";
  inline constexpr std::string_view drop_name = "\N{APL FUNCTIONAL SYMBOL QUAD}drop";
  inline constexpr std::string_view unit_name = "\N{APL FUNCTIONAL SYMBOL QUAD}unit";
  inline constexpr std::string_view ord_name = "\N{APL FUNCTIONAL SYMBOL QUAD}ord";
  inline constexpr std::string_view chr_name = "\N{APL FUNCTIONAL SYMBOL QUAD}chr";
  inline constexpr std::string_view ones_name = "\N{APL FUNCTIONAL SYMBOL QUAD}ones";
  inline constexpr std::string_view lead_name = "\N{APL FUNCTIONAL SYMBOL QUAD}lead";
  inline constexpr std::string_view trail_name = "\N{APL FUNCTIONAL SYMBOL QUAD}trail";
  inline constexpr std::string_view wide_mul_name = "\N{APL FUNCTIONAL SYMBOL QUAD}wide_mul";
  inline constexpr std::string_view float_name = "\N{APL FUNCTIONAL SYMBOL QUAD}float";
  inline constexpr std::string_view trunc_name = "\N{APL FUNCTIONAL SYMBOL QUAD}trunc";
  inline constexpr std::string_view bits_name = "\N{APL FUNCTIONAL SYMBOL QUAD}bits";
  inline constexpr std::string_view from_bits_name = "\N{APL FUNCTIONAL SYMBOL QUAD}from_bits";
  inline constexpr std::string_view tolerance_name = "\N{APL FUNCTIONAL SYMBOL QUAD}tolerance";
  inline constexpr double tolerance_default = 1e-13;
  inline constexpr std::string_view environ_name = "\N{APL FUNCTIONAL SYMBOL QUAD}environ";
  inline constexpr std::string_view iter_name = "\N{APL FUNCTIONAL SYMBOL QUAD}iter";
  inline constexpr std::string_view reserve_name = "\N{APL FUNCTIONAL SYMBOL QUAD}reserve";
  inline constexpr std::string_view start_name = "\N{APL FUNCTIONAL SYMBOL QUAD}start";
  inline constexpr std::string_view resume_name = "\N{APL FUNCTIONAL SYMBOL QUAD}resume";
  inline constexpr std::string_view await_name = "\N{APL FUNCTIONAL SYMBOL QUAD}await";
  inline constexpr std::string_view and_glyph = "\N{LOGICAL AND}";
  inline constexpr std::string_view or_glyph = "\N{LOGICAL OR}";
  inline constexpr std::string_view xor_glyph = "\N{CIRCLED PLUS}";
  inline constexpr std::string_view nand_glyph = "\N{NAND}";
  inline constexpr std::string_view nor_glyph = "\N{NOR}";
  inline constexpr std::string_view not_glyph = "\N{NOT SIGN}";
  inline constexpr std::string_view times_glyph = "\N{MULTIPLICATION SIGN}";
  inline constexpr std::string_view divide_glyph = "\N{DIVISION SIGN}";
  inline constexpr std::string_view shift_left_glyph = "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}";
  inline constexpr std::string_view shift_right_glyph = "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}";
  inline constexpr std::string_view rotate_left_glyph = "\N{ANTICLOCKWISE OPEN CIRCLE ARROW}";
  inline constexpr std::string_view rotate_right_glyph = "\N{CLOCKWISE OPEN CIRCLE ARROW}";
  inline constexpr std::string_view sat_add_glyph = "\N{SQUARED PLUS}";
  inline constexpr std::string_view sat_sub_glyph = "\N{SQUARED MINUS}";
  inline constexpr std::string_view sat_mul_glyph = "\N{SQUARED TIMES}";
  inline constexpr std::string_view concat_glyph = "\N{DOUBLE PLUS}";
  inline constexpr std::string_view shape_glyph = "\N{APL FUNCTIONAL SYMBOL RHO}";
  inline constexpr std::string_view max_glyph = "\N{LEFT CEILING}";
  inline constexpr std::string_view min_glyph = "\N{LEFT FLOOR}";
  inline constexpr std::string_view floor_glyph = "\N{DOWNWARDS ARROW}";
  inline constexpr std::string_view ceiling_glyph = "\N{UPWARDS ARROW}";
  inline constexpr std::string_view nearest_glyph = "\N{UP DOWN ARROW}";
  inline constexpr std::string_view rounded_glyph = "\N{UP DOWN DOUBLE ARROW}";
  inline constexpr std::string_view power_glyph = "\N{SUPERSCRIPT LATIN SMALL LETTER N}";
  inline constexpr std::string_view divides_glyph = "\N{DIVIDES}";
  inline constexpr std::string_view not_divides_glyph = "\N{DOES NOT DIVIDE}";
  inline constexpr std::string_view lift_open_glyph = "\N{TOP LEFT CORNER}";
  inline constexpr std::string_view lift_close_glyph = "\N{TOP RIGHT CORNER}";
  inline constexpr std::string_view deref_glyph = "\N{POSITION INDICATOR}";
  inline constexpr std::string_view each_glyph = "\N{DIAERESIS}";
  inline constexpr std::string_view iter_glyph = "\N{RIGHTWARDS PAIRED ARROWS}";
  inline constexpr std::string_view takes_glyph = "\N{LEFTWARDS PAIRED ARROWS}";
  inline constexpr std::string_view unit_glyph = "\N{CURRENCY SIGN}";
  inline constexpr std::string_view lambda_glyph = "\N{GREEK SMALL LETTER LAMDA}";
  inline constexpr std::string_view lifetime_glyph = "\N{WHITE HOURGLASS}";
  inline constexpr std::string_view lasting_word = "static";
  inline constexpr std::string_view arrow_ascii = "->";
  inline constexpr std::string_view blank_line = "blank";

} // namespace pl4g::names
