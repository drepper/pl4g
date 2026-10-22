// The calling conventions the AArch64 back end knows.
#include "target/aarch64/abi.hh"

#include <string>

#include "target/aarch64/regs.hh"


namespace pl4g::target::aarch64 {

  namespace {

    // What both conventions share; they differ in how many registers an
    // answer comes back in and in the chain register.
    CallConvDesc shaped(std::string name)
    {
      CallConvDesc made;
      made.name = std::move(name);
      for (auto one : callee_saved_names)
        made.callee_saved.insert(reg(one)->unit);
      for (int n = 0; n < 19; ++n)
        made.caller_saved.insert(reg("x" + std::to_string(n))->unit);
      for (int n = 0; n < 8; ++n)
        made.int_arg_regs.push_back(reg("x" + std::to_string(n)));
      // The registers the allocator may give out, the ones a call would
      // destroy first.  x18 is left alone because a platform may claim it,
      // x29 is the frame pointer and x30 holds the return address.
      for (int n = 0; n < 29; ++n)
        if (n != 18)
          made.allocation_order.push_back(reg("x" + std::to_string(n))->unit);
      // The first eight floating-point registers carry arguments and the first
      // carries the answer; the eight after them a function hands back as it
      // found them, so they come last in the order.
      for (int n = 0; n < 8; ++n)
        made.float_arg_regs.push_back(reg("d" + std::to_string(n)));
      made.float_ret_regs.push_back(reg("d0"));
      for (int n : { 0, 1, 2, 3, 4, 5, 6, 7 })
        made.float_allocation_order.push_back(reg("d" + std::to_string(n))->unit);
      for (int n = 16; n < 32; ++n)
        made.float_allocation_order.push_back(reg("d" + std::to_string(n))->unit);
      for (int n = 8; n < 16; ++n)
        made.float_allocation_order.push_back(reg("d" + std::to_string(n))->unit);
      // A floating-point register is one no function hands back as it found it
      // -- a prologue saves only the ordinary registers -- so a call whose
      // callee has not said what it destroys destroys every one of them.
      made.caller_saved.insert(made.float_allocation_order.begin(), made.float_allocation_order.end());
      made.stack_align = 16;
      // The standard defines no area below the stack pointer that a leaf
      // function may use, unlike the one on x86-64.
      made.red_zone = 0;
      return made;
    }

  } // anonymous namespace


  const CallConvDesc& cc_pl4g()
  {
    static const CallConvDesc made = [] {
      CallConvDesc cc = shaped("pl4g");
      cc.int_ret_regs = { reg("x0"), reg("x1"), reg("x2") };
      // As many as it answers values in, so that an answer of floating-point
      // numbers is answered in registers wherever one of whole numbers is.
      cc.float_ret_regs = { reg("d0"), reg("d1"), reg("d2") };
      // Not x18, GCC's: this back end leaves x18 alone.  x15 is a temporary no
      // argument uses, and x16 and x17 are the linker's.
      cc.chain_reg = reg("x15");
      return cc;
    }();
    return made;
  }


  const CallConvDesc& cc_aapcs64()
  {
    static const CallConvDesc made = [] {
      CallConvDesc cc = shaped("aapcs64");
      cc.int_ret_regs = { reg("x0"), reg("x1") };
      // What the system answers a pair of `double`s in, the first two of the
      // four a homogeneous aggregate may take.
      cc.float_ret_regs = { reg("d0"), reg("d1") };
      return cc;
    }();
    return made;
  }


  const CallConvDesc& lookup_cconv(std::string_view name)
  {
    if (name == "aapcs64" || name == "cdecl")
      return cc_aapcs64();
    return cc_pl4g();
  }

} // namespace pl4g::target::aarch64
