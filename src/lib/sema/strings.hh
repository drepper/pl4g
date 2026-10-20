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

  // What the bytes of a string are reached by.
  const ir::PtrType* bytes_type(ir::Module& module);
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

} // namespace pl4g::sema::strings
