// Text, written in the representation.
//
// A port of pypl4g's sema/strings.py.  A `str` is where its bytes are and how
// many there are, and the bytes are always well-formed UTF-8, so a walk over
// the characters is a decoder and not a validator.  The functions over text are
// generated as functions of the IR, once per module, on first ask.
#pragma once

#include <span>
#include <string_view>

#include "ir/builder.hh"
#include "ir/ir.hh"


namespace pl4g::sema::strings {

  // What each generated function is called.
  inline constexpr std::string_view next_symbol = "__pl4g_str_next";
  inline constexpr std::string_view join_symbol = "__pl4g_str_join";
  inline constexpr std::string_view length_symbol = "__pl4g_str_length";
  inline constexpr std::string_view compare_symbol = "__pl4g_str_compare";
  inline constexpr std::string_view char_symbol = "__pl4g_str_of_char";
  inline constexpr std::string_view byte_count_symbol = "__pl4g_str_char_bytes";
  inline constexpr std::string_view c_string_symbol = "__pl4g_str_c";

  // What the bytes of a string are reached by.
  const ir::PtrType* bytes_type(ir::Module& module);
  // What the record a host answers text with is called in the textual form.
  inline constexpr std::string_view host_text_name = "host_text";
  // What a host function answering text answers, as C sees it:
  // `struct { const char* text; uint64_t length; }`, laid out as C lays it
  // out, two words every one of the three systems answers in two registers.
  // The one record of that name, filled in on first ask.
  const ir::ProductType* host_text_type(ir::Module& module);
  // What taking one character answers with: the character and where the next
  // one begins.
  const ir::Type* taken_type(ir::Module& module);

  // The one that takes the character at a place, built on first ask.
  ir::Function* next_function(ir::Module& module);
  // The one that puts two strings' bytes end to end: taking the allocator, or,
  // with `heap`, the one for `⎕heap`.
  ir::Function* join_function(ir::Module& module, bool heap = false);
  // A call of the join, the one for `⎕heap` where `arena` is known to be it.
  ir::Value* joined(ir::Builder& builder, ir::Value* arena, std::span<ir::Value* const> pieces,
                    const ir::Type* answer, Span span = invalid_span);
  // The one that makes C's text of a string -- a copy in the heap with a nul
  // after it, which whoever asked gives back -- built on first ask.
  ir::Function* c_string_function(ir::Module& module);
  // The one that makes a string of one character.
  ir::Function* char_function(ir::Module& module);
  // How many bytes a code point takes.
  ir::Function* length_in_bytes(ir::Module& module);
  // The one that counts the characters of a string.
  ir::Function* length_function(ir::Module& module);
  // The one that says which of two strings comes first.
  ir::Function* compare_function(ir::Module& module);

  // Copy `count` bytes, one at a time, into the place `offset` bytes along.
  void copy_bytes(ir::Builder& builder, ir::Value* into, ir::Value* offset, ir::Value* source, ir::Value* count);

  // The two that let text in from bytes: one asking whether bytes are UTF-8,
  // and one moving a run of bytes a word at a time.
  inline constexpr std::string_view valid_symbol = "__pl4g_str_valid";
  inline constexpr std::string_view move_symbol = "__pl4g_bytes_move";

  /// ``__pl4g_str_valid(bytes, length)``: how far `bytes` are well-formed
  /// UTF-8 -- `length` where all of them are, and otherwise where the first
  /// sequence that is not begins.  Built on first ask.
  ir::Function* valid_function(ir::Module& module);
  /// ``__pl4g_bytes_move(into, source, count)``: `count` bytes copied, a word
  /// at a time and the few left over one at a time.  Built on first ask.
  ir::Function* move_function(ir::Module& module);
  /// Call the move: `count` bytes from `source` to `into`, which do not
  /// overlap.
  void moved(ir::Builder& builder, ir::Value* into, ir::Value* source, ir::Value* count, Span span = invalid_span);

} // namespace pl4g::sema::strings
