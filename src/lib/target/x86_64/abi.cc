// The calling conventions the x86-64 back end knows.
#include "target/x86_64/abi.hh"

#include <format>

#include "target/x86_64/regs.hh"


namespace pl4g::target::x86_64 {

  namespace {

    std::set<const mc::RegUnit*> units_of(std::initializer_list<std::string_view> names)
    {
      std::set<const mc::RegUnit*> found;
      for (auto name : names)
        found.insert(registers().reg(name)->unit);
      return found;
    }

    std::vector<const mc::RegUnit*> order_of(std::initializer_list<std::string_view> names)
    {
      std::vector<const mc::RegUnit*> found;
      for (auto name : names)
        found.push_back(registers().reg(name)->unit);
      return found;
    }

    // Every vector register is destroyed by a call here, so the order is
    // simply the order they are numbered in.
    std::vector<const mc::RegUnit*> float_order()
    {
      std::vector<const mc::RegUnit*> found;
      for (int n = 0; n < 16; ++n)
        found.push_back(registers().reg(std::format("xmm{}", n))->unit);
      return found;
    }

    std::vector<const mc::PhysReg*> float_args()
    {
      std::vector<const mc::PhysReg*> found;
      for (int n = 0; n < 8; ++n)
        found.push_back(registers().reg(std::format("xmm{}", n)));
      return found;
    }

    // What both conventions have in common: which registers a call destroys
    // and which it leaves, and the order the allocator gives them out in --
    // the ones a call would destroy first, since using one of those costs a
    // leaf function nothing.  Neither the stack pointer nor the frame pointer
    // is among them.
    CallConvDesc common(std::string name)
    {
      auto& r = registers();
      CallConvDesc made;
      made.name = std::move(name);
      made.float_arg_regs = float_args();
      made.float_ret_regs = { r.reg("xmm0") };
      made.float_allocation_order = float_order();
      made.callee_saved = units_of({ "rbx", "rbp", "r12", "r13", "r14", "r15" });
      made.caller_saved = units_of({ "rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11" });
      made.stack_align = 16;
      made.allocation_order = order_of({ "rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11", "rbx", "r12",
                                         "r13", "r14", "r15" });
      made.red_zone = 128;
      return made;
    }

  } // anonymous namespace


  const CallConvDesc& cc_pl4g()
  {
    static const CallConvDesc made = [] {
      auto& r = registers();
      CallConvDesc cc = common("pl4g");
      // It begins at the registers an answer comes back in, so that a
      // function answering with what it was given is a bare `ret`.
      cc.int_arg_regs = { r.rax, r.rdx, r.rcx, r.rsi, r.rdi, r.reg("r8"), r.reg("r9"), r.reg("r10") };
      cc.int_ret_regs = { r.rax, r.rdx, r.rcx };
      // Not r10, the static chain of the system's convention: this one passes
      // an argument there.  r11 is the one caller-saved register no argument
      // uses.
      cc.chain_reg = r.reg("r11");
      return cc;
    }();
    return made;
  }


  const CallConvDesc& cc_sysv()
  {
    static const CallConvDesc made = [] {
      auto& r = registers();
      CallConvDesc cc = common("sysv64");
      cc.int_arg_regs = { r.rdi, r.rsi, r.rdx, r.rcx, r.reg("r8"), r.reg("r9") };
      cc.int_ret_regs = { r.rax, r.rdx };
      return cc;
    }();
    return made;
  }


  const CallConvDesc& lookup_cconv(std::string_view name)
  {
    if (name == "sysv64" || name == "cdecl")
      return cc_sysv();
    return cc_pl4g();
  }


  bool knows_cconv(std::string_view name) noexcept
  {
    return name == "pl4g" || name == "sysv64" || name == "cdecl";
  }

} // namespace pl4g::target::x86_64
