// The calling conventions the AArch64 back end knows.  A port of
// pypl4g/target/aarch64/abi.py.
//
// `pl4g` is the language's own convention.  It is shaped like the standard one
// for now, which costs nothing and keeps generated code readable in a
// debugger; the specification leaves the compiler free to change it.
#pragma once

#include <string_view>

#include "target/callconv.hh"


namespace pl4g::target::aarch64 {

  // The language's own, and the system's.
  const CallConvDesc& cc_pl4g();
  const CallConvDesc& cc_aapcs64();

  // The convention called `name`, falling back to the language's own;
  // `cdecl` is what a program writes for "the one this system uses".
  const CallConvDesc& lookup_cconv(std::string_view name);

  // The registers the standard says a callee hands back, and those a caller
  // may not expect back.
  inline constexpr std::string_view callee_saved_names[] = { "x19", "x20", "x21", "x22", "x23", "x24",
                                                             "x25", "x26", "x27", "x28", "x29" };

} // namespace pl4g::target::aarch64
