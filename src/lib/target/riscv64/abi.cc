// The calling conventions the RISC-V back end knows.
#include "target/riscv64/abi.hh"

#include <format>

#include "target/riscv64/regs.hh"


namespace pl4g::target::riscv64 {

  namespace {

    // What both conventions share: the arguments, the floating-point
    // registers, what a callee keeps and what a call destroys.
    CallConvDesc shaped(std::string name)
    {
      const auto& regs = registers();
      CallConvDesc made;
      made.name = std::move(name);
      for (int n = 0; n < 8; ++n)
        made.int_arg_regs.push_back(regs.reg(std::format("a{}", n)));
      for (int n = 10; n < 18; ++n)
        made.float_arg_regs.push_back(regs.reg(std::format("f{}", n)));
      made.float_ret_regs.push_back(regs.reg("f10"));
      // f10 to f17 carry arguments and f10 the answer; f8, f9 and f18 upward
      // are handed back as found, so they come last.
      std::vector<int> floats;
      for (int n = 10; n < 18; ++n)
        floats.push_back(n);
      for (int n = 0; n < 8; ++n)
        floats.push_back(n);
      for (int n = 28; n < 32; ++n)
        floats.push_back(n);
      floats.push_back(8);
      floats.push_back(9);
      for (int n = 18; n < 28; ++n)
        floats.push_back(n);
      for (int n : floats)
        made.float_allocation_order.push_back(regs.reg(std::format("f{}", n))->unit);
      for (auto name : callee_saved_names)
        made.callee_saved.insert(regs.reg(name)->unit);
      for (auto name : caller_saved_names)
        made.caller_saved.insert(regs.reg(name)->unit);
      // Every register of the vector extension: the convention keeps none of
      // them across a call.
      for (auto one : regs.vector_registers)
        made.caller_saved.insert(one->unit);
      made.stack_align = 16;
      // `ra` holds the return address, which an unwinder follows; `sp` and `s0`,
      // the frame pointer, are left alone for the same reason, and `zero` can
      // hold nothing.
      for (auto name : { "t0", "t1", "t2", "t3", "t4", "t5", "t6", "a0", "a1", "a2", "a3", "a4", "a5",
                         "a6", "a7", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11" })
        made.allocation_order.push_back(regs.reg(name)->unit);
      // The standard defines no area below the stack pointer a leaf may use.
      made.red_zone = 0;
      return made;
    }

  } // anonymous namespace


  const CallConvDesc& cc_pl4g()
  {
    static const CallConvDesc made = [] {
      auto one = shaped("pl4g");
      const auto& regs = registers();
      one.int_ret_regs = { regs.reg("a0"), regs.reg("a1"), regs.reg("a2") };
      // As many as it answers values in, so that an answer of floating-point
      // numbers is answered in registers wherever one of whole numbers is.
      one.float_ret_regs = { regs.reg("f10"), regs.reg("f11"), regs.reg("f12") };
      // t2, as GCC has it: a temporary no argument uses.
      one.chain_reg = regs.reg("t2");
      return one;
    }();
    return made;
  }


  const CallConvDesc& cc_lp64()
  {
    static const CallConvDesc made = [] {
      auto one = shaped("lp64");
      const auto& regs = registers();
      one.int_ret_regs = { regs.reg("a0"), regs.reg("a1") };
      // What the system answers a pair of `double`s in, fa0 and fa1.
      one.float_ret_regs = { regs.reg("f10"), regs.reg("f11") };
      // A floating-point argument with no floating-point register left is
      // passed as the integer convention passes one, in an ordinary register
      // while there is one and on the stack after that.
      one.floats_in_integers = true;
      return one;
    }();
    return made;
  }


  const CallConvDesc& lookup_cconv(std::string_view name)
  {
    if (name == "lp64" || name == "cdecl")
      return cc_lp64();
    return cc_pl4g();
  }

} // namespace pl4g::target::riscv64
