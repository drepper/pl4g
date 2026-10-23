// The program's entry point and the helpers it reports through.
#include "target/x86_64/startup.hh"

#include <array>
#include <set>

#include "ir/builder.hh"
#include "target/emit.hh"
#include "target/relocate.hh"
#include "target/started.hh"
#include "target/statuses.hh"
#include "target/x86_64/abi.hh"
#include "target/x86_64/levels.hh"
#include "target/x86_64/ops.hh"
#include "target/x86_64/regs.hh"


namespace pl4g::target::x86_64 {

  namespace {

    using mc::Condition;
    using mc::Reg;

    // Where the count of tests that did not pass is kept while they run, where
    // what the kernel set the process up with waits, and where the exit status
    // waits while the destructors run: registers a call leaves alone.
    const mc::PhysReg* count_reg() { return registers().reg("ebx"); }
    const mc::PhysReg* held_stack() { return registers().reg("r13"); }
    const mc::PhysReg* held_status() { return registers().reg("r12d"); }

    const CallConvDesc& system() { return lookup_cconv("cdecl"); }

    void enter_kernel(mc::Assembler& asm_) { asm_.op(syscall_op(), Reg(), {}); }

    void trap(mc::Assembler& asm_) { asm_.op(mc::ops::trap, Reg(), {}); }

    mc::MCImm unsigned_imm(mc::Assembler& asm_, __int128 value) { return asm_.imm(value, 32, false); }

    // Put the program on a stack of its own, where it asked for one.
    void make_stack(mc::Assembler& asm_, const ir::Module& module)
    {
      if (module.stack_size <= 0)
        return;
      emit_make(asm_, syscalls(), stack_abi(), module.stack_size, module.guard_size);
    }

    // Have the runtime read the arguments into the record, where one is taken:
    // what the kernel set the process up with, and where in the record the run
    // of them goes.  The system's convention, the callee being C.
    void read_arguments(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout)
    {
      auto at = arguments_at(module, layout);
      if (! at)
        return;
      const auto& theirs = system();
      asm_.loadreg(theirs.int_arg_regs[0], asm_.reg(held_stack()));
      asm_.address(theirs.int_arg_regs[1], std::string(init_symbol));
      if (*at != 0)
        asm_.op(mc::ops::plus, theirs.int_arg_regs[1],
                { asm_.reg(theirs.int_arg_regs[1]), asm_.imm(__int128(*at), 32, true) });
      asm_.call(std::string(reads_arguments));
    }

    // Wait for every request still outstanding, once the program is done.
    void drain(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout)
    {
      auto at = ring_at(module, layout);
      if (! at)
        return;
      const auto& theirs = system();
      asm_.address(theirs.int_arg_regs[0], std::string(init_symbol));
      if (*at != 0)
        asm_.op(mc::ops::plus, theirs.int_arg_regs[0],
                { asm_.reg(theirs.int_arg_regs[0]), asm_.imm(__int128(*at), 32, true) });
      asm_.call(std::string(drains));
    }

    // Read the auxiliary vector into its table, where the program asks it: the
    // function `sema/auxv` generated walks the stack the kernel set the process
    // up with, under the language's convention, first of everything called.
    void read_auxv(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv)
    {
      if (module.auxv == nullptr)
        return;
      auto reader = module.function(auxv_make);
      if (reader == nullptr) [[unlikely]]
        throw std::logic_error("the table is there and nothing fills it");
      asm_.loadreg(cconv.int_arg_regs[0], asm_.reg(held_stack()));
      asm_.call(ir::symbol_name(*reader));
    }

    // Build the environment, where the program named it: the runtime is asked
    // for the strings, and they are handed to the function `std` generated.
    void read_environment(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv)
    {
      if (module.environ == nullptr)
        return;
      auto builder = module.function(environ_make);
      if (builder == nullptr) [[unlikely]]
        throw std::logic_error("the variable is there and nothing fills it");
      const auto& theirs = system();
      asm_.loadreg(theirs.int_arg_regs[0], asm_.reg(held_stack()));
      asm_.call(std::string(reads_environment));
      // A run of strings is two words, going straight on as the one argument;
      // the second is moved first where the first would land on it.
      const mc::PhysReg* handed[2] = { cconv.int_arg_regs[0], cconv.int_arg_regs[1] };
      const mc::PhysReg* came[2] = { theirs.int_ret_regs[0], theirs.int_ret_regs[1] };
      if (handed[0] == came[1] && handed[1] == came[0]) [[unlikely]]
        throw std::logic_error("the two would have to be exchanged");
      int order[2] = { 0, 1 };
      if (handed[0] == came[1]) {
        order[0] = 1;
        order[1] = 0;
      }
      for (int which : order)
        if (handed[which] != came[which])
          asm_.loadreg(handed[which], asm_.reg(came[which]));
      asm_.call(ir::symbol_name(*builder));
      // The second argument register rather than the first: on one of these
      // targets the first is also the one an answer comes back in.
      auto where = cconv.int_arg_regs[1];
      asm_.address(where, std::string(environ_symbol));
      asm_.store(memory(where, 0, 64), asm_.reg(cconv.int_ret_regs[0]));
    }

    // Call each test this binary runs, and stop if any of them failed --
    // every one of them run, so that all of what is wrong is said.
    void run_tests(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv,
                   const std::unordered_map<const ir::Function*, Failure>& failures)
    {
      std::vector<const ir::Function*> ran;
      for (auto one : run_by(module))
        if (failures.contains(one))
          ran.push_back(one);
      if (ran.empty())
        return;
      asm_.op(mc::ops::xor_, count_reg(), { asm_.reg(count_reg()), asm_.reg(count_reg()) });
      for (auto one : ran) {
        const auto& found = failures.at(one);
        asm_.call(ir::symbol_name(*one));
        auto passed = asm_.reserve_label("test.passed");
        auto answer = registers().info.view(cconv.int_ret_regs[0]->unit, 32);
        asm_.branch(Condition::ne, asm_.reg(answer), unsigned_imm(asm_, 0), passed);
        asm_.address(registers().rdi, found.symbol);
        asm_.loadreg(registers().esi, unsigned_imm(asm_, __int128(found.length)));
        asm_.call(std::string(report_symbol));
        asm_.op(mc::ops::plus, count_reg(), { asm_.reg(count_reg()), unsigned_imm(asm_, 1) });
        asm_.block(passed);
      }
      auto fit = asm_.reserve_label("tests.passed");
      asm_.branch(Condition::eq, asm_.reg(count_reg()), unsigned_imm(asm_, 0), fit);
      asm_.loadreg(registers().edi, unsigned_imm(asm_, statuses::TESTS_FAILED));
      asm_.loadreg(registers().eax, unsigned_imm(asm_, nr_exit_group));
      enter_kernel(asm_);
      trap(asm_);
      asm_.block(fit);
    }

    // Say `text`, which the symbol `message` holds, and stop.
    void refuse(mc::Assembler& asm_, const std::string& message, const std::string& text)
    {
      auto& r = registers();
      asm_.loadreg(r.edi, unsigned_imm(asm_, standard_error));
      asm_.address(r.rsi, message);
      asm_.loadreg(r.edx, unsigned_imm(asm_, __int128(text.size())));
      asm_.loadreg(r.eax, unsigned_imm(asm_, nr_write));
      enter_kernel(asm_);
      asm_.loadreg(r.edi, unsigned_imm(asm_, statuses::WRONG_PROCESSOR));
      asm_.loadreg(r.eax, unsigned_imm(asm_, nr_exit_group));
      enter_kernel(asm_);
      trap(asm_);
    }

    // Ask the processor whether it has what this program was built to use:
    // one `CPUID` per leaf, its answer masked down to the bits the level
    // wants.  A leaf has to exist before it can be asked, so the highest
    // ordinary and the highest extended leaf are asked first.
    void check_level(mc::Assembler& asm_, std::string_view level, const std::string& refused,
                     const std::optional<std::string>& turned_off)
    {
      auto wanted = levels::requirements(level);
      if (wanted.empty())
        return;
      auto& r = registers();
      auto state = levels::state(level);
      auto fails = asm_.reserve_label("mclevel.no");
      auto runs = asm_.reserve_label("mclevel.yes");
      std::set<std::uint32_t> highest;
      for (const auto& one : wanted)
        highest.insert(one.leaf >= 0x80000000 ? 0x80000000 : 0);
      for (auto top : highest) {
        asm_.loadreg(r.eax, unsigned_imm(asm_, top));
        asm_.op(cpuid_op(), Reg(), {});
        std::uint32_t needed = 0;
        for (const auto& one : wanted)
          if ((one.leaf >= 0x80000000) == (top != 0))
            needed = std::max(needed, one.leaf);
        asm_.branch(Condition::ult, asm_.reg(r.eax), unsigned_imm(asm_, needed), fails);
      }
      for (const auto& one : wanted) {
        asm_.loadreg(r.eax, unsigned_imm(asm_, one.leaf));
        asm_.loadreg(r.ecx, unsigned_imm(asm_, one.subleaf));
        asm_.op(cpuid_op(), Reg(), {});
        const mc::PhysReg* answer = one.reg == "eax" ? r.eax : one.reg == "ebx" ? r.ebx : one.reg == "ecx" ? r.ecx
                                                                                                           : r.edx;
        asm_.op(mc::ops::and_, answer, { asm_.reg(answer), unsigned_imm(asm_, one.bits) });
        asm_.branch(Condition::ne, asm_.reg(answer), unsigned_imm(asm_, one.bits), fails);
      }
      if (state != 0 && turned_off) {
        // And what the system has turned on, which `CPUID` does not answer.
        auto off = asm_.reserve_label("mcstate.no");
        asm_.op(mc::ops::xor_, r.ecx, { asm_.reg(r.ecx), asm_.reg(r.ecx) });
        asm_.op(xgetbv_op(), Reg(), {});
        asm_.op(mc::ops::and_, r.eax, { asm_.reg(r.eax), unsigned_imm(asm_, state) });
        asm_.branch(Condition::ne, asm_.reg(r.eax), unsigned_imm(asm_, state), off);
        asm_.jump(runs);
        asm_.block(off);
        refuse(asm_, *turned_off, levels::disabled(level));
      }
      asm_.jump(runs);
      asm_.block(fails);
      asm_.loadreg(r.edi, unsigned_imm(asm_, standard_error));
      asm_.address(r.rsi, refused);
      asm_.loadreg(r.edx, unsigned_imm(asm_, __int128(levels::described(level).size())));
      asm_.loadreg(r.eax, unsigned_imm(asm_, nr_write));
      enter_kernel(asm_);
      asm_.loadreg(r.edi, unsigned_imm(asm_, statuses::WRONG_PROCESSOR));
      asm_.loadreg(r.eax, unsigned_imm(asm_, nr_exit_group));
      enter_kernel(asm_);
      trap(asm_);
      asm_.block(runs);
    }

  } // anonymous namespace


  const SyscallABI& syscalls()
  {
    static const SyscallABI made = [] {
      auto& r = registers();
      SyscallABI abi;
      abi.mmap = nr_mmap;
      abi.munmap = nr_munmap;
      abi.mprotect = nr_mprotect;
      abi.number = r.eax;
      abi.arguments = { r.rdi, r.rsi, r.rdx, r.reg("r10"), r.reg("r8"), r.reg("r9") };
      abi.answer = r.rax;
      abi.enter = [](mc::Assembler& asm_) { enter_kernel(asm_); };
      // What `syscall` itself destroys: the return address goes in rcx and
      // the flags in r11.
      abi.clobbers = { r.rcx, r.reg("r11") };
      return abi;
    }();
    return made;
  }


  const AllocatorRegs& allocator_regs()
  {
    static const AllocatorRegs made = [] {
      auto& r = registers();
      AllocatorRegs regs;
      regs.arena = r.rdi;
      regs.size = r.rsi;
      regs.answer = r.rax;
      regs.scratch = { r.rdx, r.rcx, r.reg("r11") };
      return regs;
    }();
    return made;
  }


  const StackABI& stack_abi()
  {
    static const StackABI made = [] {
      auto& r = registers();
      StackABI abi;
      abi.mprotect = nr_mprotect;
      abi.sigaltstack = nr_sigaltstack;
      abi.rt_sigaction = nr_rt_sigaction;
      abi.write = nr_write;
      abi.exit_group = nr_exit_group;
      // Every processor this runs on has four-kilobyte pages.
      abi.page = 4096;
      abi.kept = { r.reg("rbx"), r.reg("r14"), r.reg("r15") };
      abi.scratch = { r.rdx, r.rcx, r.reg("r11") };
      abi.read_sp = [](mc::Assembler& asm_, Reg dst) { asm_.loadreg(dst, asm_.reg(registers().rsp)); };
      abi.write_sp = [](mc::Assembler& asm_, Reg src) { asm_.loadreg(registers().rsp, asm_.reg(src)); };
      // This kernel does not return from a handler by itself.
      abi.rt_sigreturn = nr_rt_sigreturn;
      return abi;
    }();
    return made;
  }


  const WalkABI& walk_abi()
  {
    static const WalkABI made = [] {
      auto& r = registers();
      WalkABI abi;
      abi.write = nr_write;
      abi.number = r.rax;
      abi.arguments = { r.rdi, r.rsi, r.rdx };
      abi.enter = [](mc::Assembler& asm_) { enter_kernel(asm_); };
      abi.kept = { r.reg("rbx"), r.reg("r12"), r.reg("r13"), r.reg("r14") };
      abi.scratch = { r.reg("rcx"), r.reg("r11") };
      return abi;
    }();
    return made;
  }


  const host::HostABI& host_abi()
  {
    // None of them an argument's.  The call to the finder pushes the return
    // address, and the eight bytes more the stub makes room for are what align
    // the stack for it.
    static const host::HostABI made = [] {
      auto& r = registers();
      host::HostABI abi;
      abi.slot = r.reg("r11");
      abi.name = r.reg("r10");
      abi.through = r.rax;
      abi.room = 8;
      return abi;
    }();
    return made;
  }


  void emit_start(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv, std::string_view level,
                  const std::optional<std::string>& refused, const std::optional<std::string>& turned_off,
                  const std::unordered_map<const ir::Function*, Failure>& failures, const ir::DataLayout& layout,
                  bool relocates)
  {
    auto& r = registers();
    auto startup = module.startup;
    auto status32 = r.info.view(cconv.int_ret_regs[0]->unit, 32);

    asm_.begin_function(std::string(entry_symbol), true);
    // A position-independent image makes its addresses true first of all.
    if (relocates)
      asm_.call(std::string(relocate_symbol));
    // What the kernel set the process up with is at the stack pointer, and
    // this is the only moment it is.
    if (holds_stack(module, layout))
      asm_.loadreg(held_stack(), asm_.reg(r.rsp));
    // The outermost frame is marked by a null frame pointer.
    asm_.op(mc::ops::xor_, r.ebp, { asm_.reg(r.ebp), asm_.reg(r.ebp) });
    if (refused)
      check_level(asm_, level, *refused, turned_off);
    read_auxv(asm_, module, cconv);
    read_environment(asm_, module, cconv);
    for (auto ctor : module.ctors)
      asm_.call(ir::symbol_name(*ctor));
    run_tests(asm_, module, cconv, failures);
    // Where the status waits: a register a call leaves alone where a
    // destructor is going to run, and the one the system call wants where none
    // is.
    bool draining = ring_at(module, layout).has_value();
    const mc::PhysReg* held = ! module.dtors.empty() || draining ? held_status() : r.edi;
    if (! module.test_plan.empty())
      asm_.loadreg(held, unsigned_imm(asm_, 0));
    else {
      make_stack(asm_, module);
      read_arguments(asm_, module, layout);
      if (wanted_by(module) != nullptr)
        asm_.address(cconv.int_arg_regs[0], std::string(init_symbol));
      asm_.call(ir::symbol_name(*startup));
      asm_.loadreg(held, asm_.reg(status32));
    }
    for (auto dtor : module.dtors)
      asm_.call(ir::symbol_name(*dtor));
    drain(asm_, module, layout);
    if (! module.dtors.empty() || draining)
      asm_.loadreg(r.edi, asm_.reg(held_status()));
    asm_.loadreg(r.eax, unsigned_imm(asm_, nr_exit_group));
    enter_kernel(asm_);
    // exit_group does not return; trapping makes that explicit.
    trap(asm_);
    asm_.end_function();
  }


  std::span<const mc::Reg> relocate_regs()
  {
    static const auto made = [] {
      auto& r = registers();
      return std::array<mc::Reg, relocate_scratch>{ r.rax, r.rcx, r.rdx, r.rsi, r.rdi, r.reg("r8"), r.reg("r9") };
    }();
    return made;
  }


  void emit_abort(mc::Assembler& asm_, const CallConvDesc& cconv, bool walk)
  {
    auto& r = registers();
    auto first = cconv.int_arg_regs[0];
    auto second = cconv.int_arg_regs[1];
    auto third = cconv.int_arg_regs[2];
    asm_.begin_function(std::string(abort_symbol), false);
    // Which kind of stop this is, put somewhere the write will not touch and
    // the walk keeps nothing in; it arrives where the write's third argument
    // goes, so it moves first.
    auto held = r.reg("r15");
    asm_.loadreg(held, asm_.reg(third));
    // The write's three arguments are the two this was given, moved up one
    // place, with the descriptor in front.
    asm_.loadreg(third, asm_.reg(second));
    asm_.loadreg(second, asm_.reg(first));
    asm_.loadreg(first, unsigned_imm(asm_, standard_error));
    asm_.loadreg(r.eax, unsigned_imm(asm_, nr_write));
    enter_kernel(asm_);
    if (walk) {
      // Where the faulting function was and where its stack stood: the call
      // that reached here pushed the first, eight bytes below the second.
      asm_.loadreg(first, memory(r.rsp, 0, 64));
      asm_.loadreg(second, asm_.reg(r.rsp));
      asm_.op(mc::ops::plus, second, { asm_.reg(second), unsigned_imm(asm_, 8) });
      asm_.call(std::string(backtrace_symbol));
    }
    asm_.loadreg(cconv.int_arg_regs[0], asm_.reg(held));
    asm_.loadreg(r.eax, unsigned_imm(asm_, nr_exit_group));
    enter_kernel(asm_);
    trap(asm_);
    asm_.end_function();
  }


  void emit_report(mc::Assembler& asm_, const CallConvDesc& cconv)
  {
    auto& r = registers();
    auto first = cconv.int_arg_regs[0];
    auto second = cconv.int_arg_regs[1];
    auto third = cconv.int_arg_regs[2];
    asm_.begin_function(std::string(report_symbol), false);
    asm_.loadreg(third, asm_.reg(second));
    asm_.loadreg(second, asm_.reg(first));
    asm_.loadreg(first, unsigned_imm(asm_, standard_error));
    asm_.loadreg(r.eax, unsigned_imm(asm_, nr_write));
    enter_kernel(asm_);
    asm_.ret();
    asm_.end_function();
  }

} // namespace pl4g::target::x86_64
