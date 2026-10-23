// The names the runtime defines: what `runtime/io.c` offers the `std` module
// and the compiler, written down here as well as in the C so that a program
// naming one the runtime does not have is refused where it names it.  pypl4g's
// `runtime.names()`.
#pragma once

#include <algorithm>
#include <array>
#include <string_view>


namespace pl4g::rt {

  inline constexpr std::array<std::string_view, 16> names{
    "pl4g_args", "pl4g_env", "pl4g_hash", "pl4g_hash_seed", "pl4g_hash_word", "pl4g_heap_alloc",
    "pl4g_heap_free", "pl4g_io_close", "pl4g_io_drain", "pl4g_io_open", "pl4g_io_read_kept", "pl4g_io_ready",
    "pl4g_io_step", "pl4g_io_submit", "pl4g_io_taken", "pl4g_io_wait",
  };

  // Whether the runtime defines `symbol`.
  inline bool defines(std::string_view symbol) noexcept
  {
    return std::ranges::find(names, symbol) != names.end();
  }

} // namespace pl4g::rt
