// The calling conventions the RISC-V back end knows: pypl4g's
// target/riscv64/abi.py.
//
// `pl4g` is the language's own convention, shaped like the standard one for
// now.  The return address lives in a register the callee is free to
// overwrite, so a function that calls anything saves it.
#pragma once

#include <string_view>

#include "target/callconv.hh"


namespace pl4g::target::riscv64 {

  // The language's own convention, and the system's.
  const CallConvDesc& cc_pl4g();
  const CallConvDesc& cc_lp64();

  // The convention called `name`, falling back to the language's own; `cdecl`
  // is the system's, whatever this architecture calls it.
  const CallConvDesc& lookup_cconv(std::string_view name);

} // namespace pl4g::target::riscv64
