// Operations only x86-64 has: pypl4g's target/x86_64/ops.py.  They are built
// exactly like the architecture-neutral ones, which is what keeps the set of
// operations open.
#pragma once

#include "mc/ops.hh"


namespace pl4g::target::x86_64 {

  // Enters the kernel; what it reads and writes is the system call
  // interface's to say.
  const mc::Op* syscall_op();
  // Asks the processor what it can do: the leaf in EAX and the subleaf in ECX,
  // all four of EAX, EBX, ECX and EDX written.
  const mc::Op* cpuid_op();
  // Asks what the operating system has enabled.
  const mc::Op* xgetbv_op();

} // namespace pl4g::target::x86_64
