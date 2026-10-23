// What a shared object has that an executable has not.
#include "target/shared.hh"

#include <ranges>

#include "elf/const.hh"
#include "ir/builder.hh"
#include "target/emit.hh"
#include "target/globals.hh"


namespace pl4g::target::shared {

  void emit(mc::Assembler& asm_, const ir::Module& module, const host::HostABI& abi, const CallConvDesc& cconv,
            Messages& messages, const SourceManager* sources, std::uint64_t text_alignment, std::string_view abort)
  {
    host::emit(asm_, module, abi, cconv, messages, sources, text_alignment, abort);
    auto table = [&](std::string_view name, std::uint32_t sh_type, auto&& funcs) {
      if (funcs.empty())
        return;
      auto options = placed(true, 8);
      options.sh_type = sh_type;
      options.sh_entsize = 8;
      asm_.section(std::string(name), options);
      for (const ir::Function* func : funcs) {
        mc::MCFixup fixup;
        fixup.offset = 0;
        fixup.kind = &mc::abs64;
        fixup.target = mc::sym_expr(asm_.symbol_named(ir::symbol_name(*func)));
        asm_.bytes(mc::Bytes(8), { std::move(fixup) });
      }
    };
    // The loader runs the table in order: the `constinit` variables'
    // initializers first, as the entry point of an executable calls them, then
    // the constructors.
    std::vector<ir::Function*> starts;
    for (const auto& one : module.initializers)
      starts.push_back(one.func);
    starts.insert(starts.end(), module.ctors.begin(), module.ctors.end());
    table(init_array, elf::sht_init_array, starts);
    table(fini_array, elf::sht_fini_array, module.dtors | std::views::reverse);
    // And back to the code, which is where what comes after expects to be.
    asm_.section(".text", placed(false, text_alignment, true));
  }


  std::vector<std::string> exports(const ir::Module& module)
  {
    std::vector<std::string> offered;
    for (const auto& [key, func] : module.functions)
      if (func->linkage == ir::Linkage::visible && ! func->is_declaration())
        offered.push_back(ir::symbol_name(*func));
    for (const auto& [key, var] : module.globals)
      if (var->linkage == ir::Linkage::visible)
        offered.push_back(symbol_of(var.get()));
    return offered;
  }

} // namespace pl4g::target::shared
