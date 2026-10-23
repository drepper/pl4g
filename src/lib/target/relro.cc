// Making the `@[relro]` variables read-only once the constructors have run.
#include "target/relro.hh"

#include <algorithm>

#include "target/relocate.hh"


namespace pl4g::target {

  namespace {

    // What mprotect is asked to leave of a page: reading it.
    constexpr std::int64_t prot_read = 1;

    // A small constant, as the other helpers here write one.
    mc::MCImm number(std::int64_t value)
    {
      return mc::MCImm{ value, 32, false };
    }

  } // anonymous namespace


  bool relro_wanted(const ir::Module& module)
  {
    return std::ranges::any_of(module.globals, [](const auto& one) { return one.second->relro; });
  }


  void emit_protect_relro(mc::Assembler& asm_, const SyscallABI& abi)
  {
    auto first = abi.arguments[0];
    auto second = abi.arguments[1];
    asm_.address(first, std::string(relro_start_symbol));
    asm_.address(second, std::string(relro_end_symbol));
    asm_.op(mc::ops::minus, second, { asm_.reg(second), asm_.reg(first) });
    asm_.loadreg(abi.arguments[2], number(prot_read));
    asm_.loadreg(abi.number, number(abi.mprotect));
    abi.enter(asm_);
  }

} // namespace pl4g::target
