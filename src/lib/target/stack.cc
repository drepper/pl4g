// The stack a program runs on, and the guard below it.
#include "target/stack.hh"
#include "target/emit.hh"

#include "target/statuses.hh"


namespace pl4g::target {

  namespace {

    // What the action asks for: the address that faulted, a stack of the
    // handler's own, and the return the architecture wants said.
    std::int64_t flags_of(const StackABI& abi)
    {
      auto flags = sa_siginfo | sa_onstack;
      return abi.rt_sigreturn ? flags | sa_restorer : flags;
    }

    // Put a number in a register, however many instructions that takes.
    void a_number(mc::Assembler& asm_, mc::Reg where, std::int64_t value)
    {
      asm_.loadreg(where, asm_.imm(value, value < (std::int64_t(1) << 31) ? 32 : 64, false));
    }

    // Bring `value` down to the next multiple of `grain` below it.
    void mask_down(mc::Assembler& asm_, mc::Reg value, std::int64_t grain, mc::Reg scratch)
    {
      a_number(asm_, scratch, grain - 1);
      asm_.op(mc::ops::not_, scratch, { asm_.reg(scratch) });
      asm_.op(mc::ops::and_, value, { asm_.reg(value), asm_.reg(scratch) });
    }

    mc::MCMem at(mc::Reg base, std::int64_t disp)
    {
      return memory(base, disp, 64);
    }

    mc::MCImm number(std::int64_t value)
    {
      return mc::MCImm{ value, 32, false };
    }

    void put(mc::Bytes& out, std::int64_t at, std::uint64_t value)
    {
      for (int i = 0; i < word; ++i)
        out[std::size_t(at + i)] = std::uint8_t(value >> (8 * i));
    }

    mc::MCFixup address_at(mc::Assembler& asm_, std::int64_t offset, std::string_view symbol)
    {
      mc::MCFixup fixup;
      fixup.offset = offset;
      fixup.kind = &mc::abs64;
      fixup.target = mc::sym_expr(asm_.symbol_named(std::string(symbol)));
      return fixup;
    }

    // The few instructions a handler returns through, for the kernel that
    // does not return from one itself.
    void emit_restorer(mc::Assembler& asm_, const SyscallABI& calls, const StackABI& abi)
    {
      asm_.begin_function(std::string(stack_restorer_symbol), false);
      a_number(asm_, calls.number, *abi.rt_sigreturn);
      calls.enter(asm_);
      asm_.op(mc::ops::trap, mc::Reg{}, {});
      asm_.end_function();
    }

  } // anonymous namespace


  void emit_make(mc::Assembler& asm_, const SyscallABI& calls, const StackABI& abi, std::int64_t stack_size,
                 std::int64_t guard_size)
  {
    auto guard = rounded(guard_size, abi.page);
    auto size = rounded(stack_size, abi.page);
    auto total = guard + size + alt_stack;
    auto first = calls.arguments[0], second = calls.arguments[1], third = calls.arguments[2];
    auto fourth = calls.arguments[3], fifth = calls.arguments[4], sixth = calls.arguments[5];
    auto base = abi.kept[0], other = abi.kept[1], top = abi.kept[2];
    auto keep = asm_.reserve_label("stack.as.it.was");

    // Below the stack the kernel made, by more than the gap it leaves.
    abi.read_sp(asm_, base);
    a_number(asm_, other, stack_gap + total);
    asm_.op(mc::ops::minus, base, { asm_.reg(base), asm_.reg(other) });
    mask_down(asm_, base, hint_grain, other);
    // mmap(hint, total, PROT_READ|PROT_WRITE, MAP_PRIVATE|ANONYMOUS|NORESERVE, -1, 0)
    asm_.loadreg(first, asm_.reg(base));
    a_number(asm_, second, total);
    a_number(asm_, third, prot_read_write);
    a_number(asm_, fourth, map_private_anon_noreserve);
    a_number(asm_, fifth, 0);
    asm_.op(mc::ops::not_, fifth, { asm_.reg(fifth) });
    a_number(asm_, sixth, 0);
    a_number(asm_, calls.number, calls.mmap);
    calls.enter(asm_);
    asm_.loadreg(base, asm_.reg(calls.answer));
    a_number(asm_, other, error_window);
    asm_.op(mc::ops::not_, other, { asm_.reg(other) });
    asm_.branch(mc::Condition::ugt, asm_.reg(base), asm_.reg(other), keep);
    // mprotect(base, guard, PROT_NONE)
    asm_.loadreg(first, asm_.reg(base));
    a_number(asm_, second, guard);
    a_number(asm_, third, prot_none);
    a_number(asm_, calls.number, abi.mprotect);
    calls.enter(asm_);
    auto guarded = asm_.reserve_label("stack.guarded");
    asm_.branch(mc::Condition::eq, asm_.reg(calls.answer), mc::MCImm{ 0, 12, false }, guarded);
    // A stack that cannot be guarded is given back, and not used.
    asm_.loadreg(first, asm_.reg(base));
    a_number(asm_, second, total);
    a_number(asm_, calls.number, calls.munmap);
    calls.enter(asm_);
    asm_.jump(keep);
    asm_.block(guarded);
    // What the handler reads: where the guard begins and where it ends.
    asm_.address(other, std::string(stack_state_symbol));
    asm_.store(at(other, state_low), asm_.reg(base));
    a_number(asm_, top, guard);
    asm_.op(mc::ops::plus, top, { asm_.reg(top), asm_.reg(base) });
    asm_.store(at(other, state_high), asm_.reg(top));
    // The top of the program's stack, the bottom of the handler's.
    a_number(asm_, base, size);
    asm_.op(mc::ops::plus, top, { asm_.reg(top), asm_.reg(base) });
    asm_.store(at(other, state_alt + alt_sp), asm_.reg(top));
    // sigaltstack(&it, 0)
    asm_.op(mc::ops::plus, first, { asm_.reg(other), number(state_alt) });
    a_number(asm_, second, 0);
    a_number(asm_, calls.number, abi.sigaltstack);
    calls.enter(asm_);
    // rt_sigaction(SIGSEGV, &action, 0, sizeof mask)
    asm_.op(mc::ops::plus, second, { asm_.reg(other), number(state_action) });
    a_number(asm_, first, sigsegv);
    a_number(asm_, third, 0);
    a_number(asm_, fourth, sigset_size);
    a_number(asm_, calls.number, abi.rt_sigaction);
    calls.enter(asm_);
    // Last of all, because everything above ran on the stack being left.
    abi.write_sp(asm_, top);
    asm_.falls_through(keep);
    asm_.block(keep);
  }


  void emit_handler(mc::Assembler& asm_, const SyscallABI& calls, const StackABI& abi)
  {
    auto first = calls.arguments[0], second = calls.arguments[1], third = calls.arguments[2];
    auto fourth = calls.arguments[3];
    auto where = abi.scratch[0], state = abi.scratch[1], edge = abi.scratch[2];
    asm_.begin_function(std::string(stack_handler_symbol), false);
    asm_.loadreg(where, at(calls.arguments[1], si_addr));
    asm_.address(state, std::string(stack_state_symbol));
    auto elsewhere = asm_.reserve_label("not.the.guard");
    asm_.loadreg(edge, at(state, state_low));
    asm_.branch(mc::Condition::ult, asm_.reg(where), asm_.reg(edge), elsewhere);
    asm_.loadreg(edge, at(state, state_high));
    asm_.branch(mc::Condition::uge, asm_.reg(where), asm_.reg(edge), elsewhere);
    // write(2, message, length)
    a_number(asm_, first, 2);
    asm_.address(second, std::string(stack_message_symbol));
    a_number(asm_, third, std::int64_t(stack_message.size()));
    a_number(asm_, calls.number, abi.write);
    calls.enter(asm_);
    // exit_group(the status that says the stack ran out)
    a_number(asm_, first, statuses::STACK_OVERFLOW);
    a_number(asm_, calls.number, abi.exit_group);
    calls.enter(asm_);
    asm_.op(mc::ops::trap, mc::Reg{}, {});
    asm_.block(elsewhere);
    // rt_sigaction(SIGSEGV, &nothing, 0, sizeof mask): the default back.
    asm_.op(mc::ops::plus, second, { asm_.reg(state), number(state_plain) });
    a_number(asm_, first, sigsegv);
    a_number(asm_, third, 0);
    a_number(asm_, fourth, sigset_size);
    a_number(asm_, calls.number, abi.rt_sigaction);
    calls.enter(asm_);
    asm_.ret();
    asm_.end_function();
    if (abi.rt_sigreturn)
      emit_restorer(asm_, calls, abi);
  }


  void emit_state(mc::Assembler& asm_, const StackABI& abi)
  {
    asm_.section(std::string(stack_state_section), placed(true, word));
    asm_.align(word);
    auto symbol = asm_.label(std::string(stack_state_symbol), mc::SymBinding::local, mc::SymKind::object,
                             mc::SymVisibility::hidden);
    mc::Bytes out(state_size, 0);
    put(out, state_action + action_flags, std::uint64_t(flags_of(abi)));
    put(out, state_alt + alt_size, std::uint64_t(alt_stack));
    std::vector<mc::MCFixup> fixups{ address_at(asm_, state_action + action_handler, stack_handler_symbol) };
    if (abi.rt_sigreturn)
      fixups.push_back(address_at(asm_, state_action + action_restorer, stack_restorer_symbol));
    asm_.bytes(std::move(out), std::move(fixups));
    asm_.end_label(symbol);
    asm_.section(std::string(stack_message_section), placed(false, 1));
    auto said = asm_.label(std::string(stack_message_symbol), mc::SymBinding::local, mc::SymKind::object,
                           mc::SymVisibility::hidden);
    asm_.bytes(mc::Bytes(stack_message.begin(), stack_message.end()));
    asm_.end_label(said);
  }

} // namespace pl4g::target
