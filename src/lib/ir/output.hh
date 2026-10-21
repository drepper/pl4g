// What a program is made into, which decides whether there is a host.
//
// A port of pypl4g's ir/module.py `Output`: an executable is started by the
// kernel and loaded into nothing, so nothing outside it can be asked for a
// function; a shared object is loaded into a program, and so is a program an
// interpreter embedded in one runs.
#pragma once

#include <cstdint>


namespace pl4g::ir {

  enum class Output : std::uint8_t { executable, shared, interpreted };

  // Whether there is a program this one is loaded into.
  constexpr bool hosted(Output output) noexcept
  {
    return output != Output::executable;
  }

} // namespace pl4g::ir
