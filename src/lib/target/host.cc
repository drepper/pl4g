// Finding what the host defines, the first time it is called.
#include "target/host.hh"

#include <set>
#include <string>
#include <unordered_map>

#include "ir/builder.hh"
#include "target/emit.hh"
#include "target/statuses.hh"


namespace pl4g::target::host {

  namespace {

    // Nought, as a stub and the finder compare and pass it.
    mc::MCImm nought()
    {
      return mc::MCImm{ 0, 32, false };
    }

    // Put `data` into the current section under the local symbol `name`.
    void object(mc::Assembler& asm_, const std::string& name, mc::Bytes data)
    {
      auto symbol = asm_.label(name, mc::SymBinding::local, mc::SymKind::object, mc::SymVisibility::hidden);
      asm_.bytes(std::move(data));
      asm_.end_label(symbol);
    }

    // The registers a function of `cconv` is handed its arguments in, the
    // integers first.
    std::vector<mc::Reg> argument_regs(const CallConvDesc& cconv)
    {
      std::vector<mc::Reg> regs(cconv.int_arg_regs.begin(), cconv.int_arg_regs.end());
      regs.insert(regs.end(), cconv.float_arg_regs.begin(), cconv.float_arg_regs.end());
      return regs;
    }

    // Emit the stub a call to `func` reaches.
    void emit_stub(mc::Assembler& asm_, const ir::Function& func, const HostABI& abi, const CallConvDesc& cconv,
                   Messages& messages, const SourceManager* sources, std::string_view abort)
    {
      const auto& symbol = *func.attrs.host;
      std::string_view name = func.name;
      if (name.ends_with(crossing_suffix))
        name.remove_suffix(crossing_suffix.size());
      auto text = describe("the host defines no '" + symbol + "'", name, func.span, sources);
      auto message = messages.symbol(text);
      auto arguments = argument_regs(cconv);
      asm_.begin_function(ir::symbol_name(func), false);
      auto find = asm_.reserve_label(std::string(ir::host_prefix) + symbol + ".find");
      auto missing = asm_.reserve_label(std::string(ir::host_prefix) + symbol + ".missing");
      asm_.address(abi.slot, std::string(slot_prefix) + symbol);
      asm_.loadreg(abi.name, memory(abi.slot, 0, 64));
      asm_.branch(mc::Condition::eq, asm_.reg(abi.name), nought(), find);
      asm_.tail_jump(mc::MCOperand(asm_.reg(abi.name)), invalid_span, arguments);
      asm_.block(find);
      asm_.address(abi.name, std::string(name_prefix) + symbol);
      // The call below writes the return address where this stub's own is, on
      // an architecture that keeps it in a register, which the function it goes
      // to will want; and it is made with the stack aligned.
      asm_.frame(abi.room);
      if (abi.link)
        asm_.put_aside(0, *abi.link);
      auto reads = arguments;
      reads.push_back(abi.slot);
      reads.push_back(abi.name);
      asm_.call(std::string(find_symbol), invalid_span, {}, std::move(reads));
      if (abi.link)
        asm_.take_back(*abi.link, 0);
      asm_.unframe(abi.room);
      asm_.branch(mc::Condition::eq, asm_.reg(abi.name), nought(), missing);
      asm_.tail_jump(mc::MCOperand(asm_.reg(abi.name)), invalid_span, arguments);
      asm_.block(missing);
      mc::Reg first = cconv.int_arg_regs[0], second = cconv.int_arg_regs[1], third = cconv.int_arg_regs[2];
      asm_.address(first, message);
      asm_.loadreg(second, asm_.imm(__int128(text.size()), 32, false));
      asm_.loadreg(third, asm_.imm(statuses::HOST_SYMBOL_MISSING, 32, false));
      asm_.call(std::string(abort));
      asm_.op(mc::ops::trap, mc::Reg{}, {});
      asm_.end_function();
    }

    // Emit the finder: `dlsym(RTLD_DEFAULT, name)`, kept in the slot.  Every
    // argument register is kept across the call, and the slot; each has a room
    // of sixteen bytes, which holds the whole of a floating-point register on
    // every architecture and keeps the stack as aligned as it came.
    void emit_find(mc::Assembler& asm_, const HostABI& abi, const CallConvDesc& cconv)
    {
      auto kept = argument_regs(cconv);
      kept.push_back(abi.slot);
      auto room = 16 * std::int64_t(kept.size());
      mc::Reg first = cconv.int_arg_regs[0], second = cconv.int_arg_regs[1];
      asm_.begin_function(std::string(find_symbol), false);
      asm_.frame(room);
      for (std::size_t at = 0; at < kept.size(); ++at)
        asm_.put_aside(16 * std::int64_t(at), kept[at]);
      asm_.loadreg(second, asm_.reg(abi.name));
      asm_.loadreg(first, asm_.imm(0, 32, false));
      asm_.address(abi.through, std::string(dlsym_slot));
      asm_.loadreg(abi.through, memory(abi.through, 0, 64));
      asm_.call(mc::MCOperand(asm_.reg(abi.through)), invalid_span, {}, { first, second });
      asm_.loadreg(abi.name, asm_.reg(cconv.int_ret_regs[0]));
      asm_.take_back(abi.slot, 16 * (std::int64_t(kept.size()) - 1));
      asm_.store(memory(abi.slot, 0, 64), asm_.reg(abi.name));
      for (std::size_t at = 0; at + 1 < kept.size(); ++at)
        asm_.take_back(kept[at], 16 * std::int64_t(at));
      asm_.unframe(room);
      asm_.ret(invalid_span, { abi.name });
      asm_.end_function();
    }

  } // anonymous namespace


  std::vector<const ir::Function*> hosted(const ir::Module& module)
  {
    std::set<std::string> called;
    for (const auto& [_, func] : module.functions)
      for (const auto& block : func->blocks)
        for (const auto* inst : block->insts)
          if (inst->kind == ir::InstKind::CallInst && inst->callee != nullptr && inst->callee->attrs.host)
            called.insert(*inst->callee->attrs.host);
    std::vector<const ir::Function*> found;
    std::set<std::string> seen;
    for (const auto& [_, func] : module.functions)
      if (func->attrs.host && called.contains(*func->attrs.host) && seen.insert(*func->attrs.host).second)
        found.push_back(func.get());
    return found;
  }


  bool emit(mc::Assembler& asm_, const ir::Module& module, const HostABI& abi, const CallConvDesc& cconv,
            Messages& messages, const SourceManager* sources, std::uint64_t text_alignment, std::string_view abort)
  {
    auto found = hosted(module);
    if (found.empty())
      return false;
    asm_.section(std::string(got_section), placed(true, 8));
    object(asm_, std::string(dlsym_slot), mc::Bytes(8));
    asm_.section(".data", placed(true, 8));
    // After whatever the program's own variables left, which need not end on
    // a word.
    asm_.align(8);
    for (auto func : found)
      object(asm_, std::string(slot_prefix) + *func->attrs.host, mc::Bytes(8));
    asm_.section(".rodata", placed(false, 1));
    for (auto func : found) {
      const auto& symbol = *func->attrs.host;
      mc::Bytes text(symbol.begin(), symbol.end());
      text.push_back(0);
      object(asm_, std::string(name_prefix) + symbol, std::move(text));
    }
    asm_.section(".text", placed(false, text_alignment, true));
    for (auto func : found)
      emit_stub(asm_, *func, abi, cconv, messages, sources, abort);
    emit_find(asm_, abi, cconv);
    return true;
  }

} // namespace pl4g::target::host
