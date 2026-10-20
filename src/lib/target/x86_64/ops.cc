// Operations only x86-64 has.
#include "target/x86_64/ops.hh"


namespace pl4g::target::x86_64 {

  const mc::Op* syscall_op()
  {
    static const mc::Op* made = mc::register_op({ "x86.syscall", 0, false });
    return made;
  }


  const mc::Op* cpuid_op()
  {
    static const mc::Op* made = mc::register_op({ "x86.cpuid", 0, false });
    return made;
  }


  const mc::Op* xgetbv_op()
  {
    static const mc::Op* made = mc::register_op({ "x86.xgetbv", 0, false });
    return made;
  }

} // namespace pl4g::target::x86_64
