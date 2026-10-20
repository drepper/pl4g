// The calling conventions the x86-64 back end knows: pypl4g's
// target/x86_64/abi.py.
//
// `pl4g` is the language's own and the one every function has that does not
// ask for another; it is not shaped like the system's and need not be.
// `sysv64` is the system's, and `cdecl` the name a program writes for it
// without having to know what this architecture calls it.
#pragma once

#include <string_view>

#include "target/callconv.hh"


namespace pl4g::target::x86_64 {

  const CallConvDesc& cc_pl4g();
  const CallConvDesc& cc_sysv();

  // The convention called `name`, falling back to the language's own.
  const CallConvDesc& lookup_cconv(std::string_view name);

  // Whether `name` is a convention this target knows by that name.
  bool knows_cconv(std::string_view name) noexcept;

} // namespace pl4g::target::x86_64
