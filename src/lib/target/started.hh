// What the entry point and the rest of the compiler agree on: the names at the
// two ends of one arrangement.  A port of the constants of pypl4g's
// target/started.py that the checker's helpers need.
#pragma once

#include <string_view>


namespace pl4g::target {

  // What the runtime is called that reads the arguments off the stack, and
  // what the one that reads the environment off it is called.
  inline constexpr std::string_view reads_arguments = "pl4g_args";
  inline constexpr std::string_view reads_environment = "pl4g_env";

  // What the variable holding the environment is called in the image, and what
  // the generated function that fills it is called.
  inline constexpr std::string_view environ_symbol = "__pl4g_environ";
  inline constexpr std::string_view environ_make = "__pl4g_environ_make";

} // namespace pl4g::target
