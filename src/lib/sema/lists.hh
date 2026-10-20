// What a list needs beyond what it is.
//
// A port of pypl4g's sema/lists.py.  A list is three words -- where its
// elements are, how many there are, and the allocator they came from -- with
// how much room there is written in a header just before the elements.  Room
// is taken, grown and given back by the functions here and nowhere else.
#pragma once

#include <cstdint>
#include <string_view>

#include "ir/builder.hh"
#include "ir/ir.hh"


namespace pl4g::sema::lists {

  // How far before the elements the header is.
  inline constexpr std::int64_t header = 16;
  // How many elements a list's first room has room for, at the least.
  inline constexpr std::int64_t smallest = 4;

  inline constexpr std::string_view room_symbol = "__pl4g_list_room";
  inline constexpr std::string_view capacity_symbol = "__pl4g_list_capacity";
  inline constexpr std::string_view free_symbol = "__pl4g_list_free";
  inline constexpr std::string_view grow_symbol = "__pl4g_list_grow";
  inline constexpr std::string_view release_symbol = "__pl4g_list_release";
  inline constexpr std::string_view join_symbol = "__pl4g_list_join";
  inline constexpr std::string_view allocator_symbol = "__pl4g_list_allocator";
  inline constexpr std::string_view erase_symbol = "__pl4g_list_erase";

  // `__pl4g_list_erase(at, stride, count, which)`: one element out.
  ir::Function* erase_function(ir::Module& module);
  // `__pl4g_list_room(allocator, capacity, stride)`: room, the header written.
  ir::Function* room_function(ir::Module& module);
  // `__pl4g_list_capacity(where, allocator)`: how many elements there is room for.
  ir::Function* capacity_function(ir::Module& module);
  // `__pl4g_list_free(allocator, where, stride)`: the room given back.
  ir::Function* free_function(ir::Module& module);
  // `__pl4g_list_grow(where, count, allocator, into, more, stride)`.
  ir::Function* grow_function(ir::Module& module);
  // `__pl4g_list_release(allocator, old, new, stride)`.
  ir::Function* release_function(ir::Module& module);
  // `__pl4g_list_join(allocator, first, first_count, second, second_count, stride)`.
  ir::Function* join_function(ir::Module& module);
  // `__pl4g_list_allocator(allocator, otherwise)`.
  ir::Function* allocator_function(ir::Module& module);

} // namespace pl4g::sema::lists
