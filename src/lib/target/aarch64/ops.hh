// Operations that only AArch64 has.  A port of pypl4g/target/aarch64/ops.py.
#pragma once

#include "mc/ops.hh"


namespace pl4g::target::aarch64 {

  // Enters the kernel.  The call number is in x8 and the arguments in x0
  // onwards, which is a different convention from the one an ordinary call
  // uses.
  inline const mc::Op* supervisor_call()
  {
    static const mc::Op* const made = mc::register_op({ "aarch64.svc", 0, false });
    return made;
  }

} // namespace pl4g::target::aarch64
