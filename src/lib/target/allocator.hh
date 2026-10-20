// The names of the allocator the back end writes as instructions: the bump of
// an arena and what it calls when an arena has no room left.  A port of the
// constants of pypl4g's target/allocator.py.
#pragma once

#include <string_view>


namespace pl4g::target {

  inline constexpr std::string_view alloc_symbol = "__pl4g_alloc";
  inline constexpr std::string_view grow_symbol = "__pl4g_grow";

} // namespace pl4g::target
