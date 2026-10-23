// The program's entry point on RISC-V.
#include "target/riscv64/startup.hh"

#include <array>
#include <format>
#include <stdexcept>

#include "ir/builder.hh"
#include "target/emit.hh"
#include "target/riscv64/abi.hh"
#include "target/riscv64/opcodes.hh"
#include "target/riscv64/regs.hh"
#include "target/relocate.hh"
#include "target/started.hh"
#include "target/statuses.hh"


namespace pl4g::target::riscv64 {

  namespace {

    using mc::Reg;

    // Where the count of tests that did not pass is kept while they run, and
    // where what the kernel set the process up with waits until the runtime
    // is asked to read it: registers a call leaves alone.
    const mc::PhysReg* count_reg() { return registers().reg("s2"); }
    const mc::PhysReg* held_stack() { return registers().reg("s3"); }

    void enter_kernel(mc::Assembler& asm_) { asm_.op(environment_call(), Reg{}, {}); }

    // Put the program on a stack of its own, where it asked for one.
    void make_stack(mc::Assembler& asm_, const ir::Module& module)
    {
      if (module.stack_size <= 0)
        return;
      emit_make(asm_, syscalls(), stack_abi(), module.stack_size, module.guard_size);
    }

    // Have the runtime read the arguments into the record, where one is taken:
    // the system's convention, the runtime being the callee.
    void read_arguments(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout)
    {
      auto at = arguments_at(module, layout);
      if (! at)
        return;
      const auto& theirs = lookup_cconv(ir::system_cconv);
      asm_.loadreg(theirs.int_arg_regs[0], asm_.reg(held_stack()));
      asm_.address(theirs.int_arg_regs[1], std::string(init_symbol));
      if (*at != 0)
        asm_.op(mc::ops::plus, theirs.int_arg_regs[1],
                { asm_.reg(theirs.int_arg_regs[1]), asm_.imm(__int128(*at), 12, false) });
      asm_.call(std::string(reads_arguments));
    }

    // Wait for every request still outstanding, once the program is done.
    void drain(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout)
    {
      auto at = ring_at(module, layout);
      if (! at)
        return;
      const auto& theirs = lookup_cconv(ir::system_cconv);
      asm_.address(theirs.int_arg_regs[0], std::string(init_symbol));
      if (*at != 0)
        asm_.op(mc::ops::plus, theirs.int_arg_regs[0],
                { asm_.reg(theirs.int_arg_regs[0]), asm_.imm(__int128(*at), 12, false) });
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

    // Build the environment, where the program named it: the runtime reads
    // the strings, the function the front end generated builds the table.
    void read_environment(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv)
    {
      if (module.environ == nullptr)
        return;
      auto builder = module.function(environ_make);
      if (builder == nullptr) [[unlikely]]
        throw std::logic_error("the variable is there and nothing fills it");
      const auto& theirs = lookup_cconv(ir::system_cconv);
      asm_.loadreg(theirs.int_arg_regs[0], asm_.reg(held_stack()));
      asm_.call(std::string(reads_environment));
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
      // The second argument register: on one of these targets the first is
      // also the one an answer comes back in.
      const mc::PhysReg* where = cconv.int_arg_regs[1];
      asm_.address(where, std::string(environ_symbol));
      asm_.store(memory(where, 0, 64), asm_.reg(cconv.int_ret_regs[0]));
    }

    // Call each test this binary runs, and stop if any of them failed --
    // after every one of them has been run.
    void run_tests(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv,
                   const std::unordered_map<const ir::Function*, Failure>& failures)
    {
      std::vector<const ir::Function*> ran;
      for (auto one : run_by(module))
        if (failures.contains(one))
          ran.push_back(one);
      if (ran.empty())
        return;
      const auto& regs = registers();
      asm_.loadreg(count_reg(), asm_.reg(regs.zero));
      for (auto one : ran) {
        const auto& found = failures.at(one);
        asm_.call(ir::symbol_name(*one));
        auto passed = asm_.reserve_label("test.passed");
        asm_.branch(mc::Condition::ne, asm_.reg(cconv.int_ret_regs[0]), asm_.imm(0, 12), passed);
        asm_.address(cconv.int_arg_regs[0], found.symbol);
        asm_.loadreg(cconv.int_arg_regs[1], asm_.imm(__int128(found.length), 12));
        asm_.call(std::string(report_symbol));
        asm_.op(mc::ops::plus, count_reg(), { asm_.reg(count_reg()), asm_.imm(1, 12) });
        asm_.block(passed);
      }
      auto fit = asm_.reserve_label("tests.passed");
      asm_.branch(mc::Condition::eq, asm_.reg(count_reg()), asm_.reg(regs.zero), fit);
      asm_.loadreg(cconv.int_arg_regs[0], asm_.imm(statuses::TESTS_FAILED, 12));
      asm_.loadreg(regs.a7, asm_.imm(nr_exit_group, 12));
      enter_kernel(asm_);
      asm_.op(mc::ops::trap, Reg{}, {});
      asm_.block(fit);
    }

  } // anonymous namespace


  const SyscallABI& syscalls()
  {
    static const SyscallABI made = [] {
      const auto& regs = registers();
      SyscallABI abi;
      abi.mmap = nr_mmap;
      abi.munmap = nr_munmap;
      abi.mprotect = nr_mprotect;
      abi.number = regs.a7;
      for (int n = 0; n < 6; ++n)
        abi.arguments.emplace_back(regs.reg(std::format("a{}", n)));
      abi.answer = regs.a0;
      abi.enter = enter_kernel;
      return abi;
    }();
    return made;
  }


  const AllocatorRegs& allocator_regs()
  {
    static const AllocatorRegs made = [] {
      const auto& regs = registers();
      return AllocatorRegs{ regs.a0, regs.a1, regs.a0, { regs.reg("t0"), regs.reg("t1"), regs.reg("t2") } };
    }();
    return made;
  }


  const StackABI& stack_abi()
  {
    static const StackABI made = [] {
      const auto& regs = registers();
      StackABI abi;
      abi.mprotect = nr_mprotect;
      abi.sigaltstack = nr_sigaltstack;
      abi.rt_sigaction = nr_rt_sigaction;
      abi.write = nr_write;
      abi.exit_group = nr_exit_group;
      // The base page of every configuration of this architecture.
      abi.page = 4096;
      abi.kept = { regs.reg("s4"), regs.reg("s5"), regs.reg("s6") };
      abi.scratch = { regs.reg("t0"), regs.reg("t1"), regs.reg("t2") };
      const mc::PhysReg* sp = regs.sp;
      abi.read_sp = [sp](mc::Assembler& asm_, Reg dst) { asm_.loadreg(dst, asm_.reg(sp)); };
      abi.write_sp = [sp](mc::Assembler& asm_, Reg src) { asm_.loadreg(sp, asm_.reg(src)); };
      return abi;
    }();
    return made;
  }


  const WalkABI& walk_abi()
  {
    static const WalkABI made = [] {
      const auto& regs = registers();
      WalkABI abi;
      abi.write = nr_write;
      abi.number = regs.a7;
      abi.arguments = { regs.a0, regs.a1, regs.reg("a2") };
      abi.enter = enter_kernel;
      abi.kept = { regs.s1, regs.reg("s2"), regs.reg("s3"), regs.reg("s4") };
      abi.scratch = { regs.reg("t0"), regs.reg("t1") };
      return abi;
    }();
    return made;
  }


  const host::HostABI& host_abi()
  {
    // Temporaries no argument is in, and the return address a call to the
    // finder writes.
    static const host::HostABI made = [] {
      const auto& regs = registers();
      host::HostABI abi;
      abi.slot = regs.reg("t1");
      abi.name = regs.reg("t2");
      abi.through = regs.reg("t0");
      abi.link = mc::Reg(regs.ra);
      return abi;
    }();
    return made;
  }


  std::span<const mc::Reg> relocate_regs()
  {
    static const auto made = [] {
      const auto& r = registers();
      return std::array<mc::Reg, relocate_scratch>{ r.reg("t0"), r.reg("t1"), r.reg("t2"), r.reg("t3"), r.reg("t4"), r.reg("t5"), r.reg("t6") };
    }();
    return made;
  }


  void emit_start(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv,
                  const std::unordered_map<const ir::Function*, Failure>& failures, const ir::DataLayout& layout,
                  bool relocates)
  {
    const ir::Function* startup = module.startup;
    if (startup == nullptr) [[unlikely]]
      throw std::logic_error("an entry point for a module with no startup function");
    const auto& regs = registers();
    const mc::PhysReg* status = cconv.int_ret_regs[0];

    asm_.begin_function(std::string(entry_symbol), true);
    // A position-independent image makes its addresses true first of all.
    if (relocates)
      asm_.call(std::string(relocate_symbol));
    // What the kernel set the process up with is at the stack pointer, and this
    // is the only moment it is.
    if (holds_stack(module, layout))
      asm_.loadreg(held_stack(), asm_.reg(regs.sp));
    // The outermost frame is marked by a null frame pointer and return address.
    asm_.loadreg(regs.fp, asm_.reg(regs.zero));
    asm_.loadreg(regs.ra, asm_.reg(regs.zero));
    read_auxv(asm_, module, cconv);
    read_environment(asm_, module, cconv);
    for (auto ctor : module.ctors)
      asm_.call(ir::symbol_name(*ctor));
    run_tests(asm_, module, cconv, failures);
    if (! module.test_plan.empty())
      // A binary built to run tests and nothing else: every one passed.
      asm_.loadreg(status, asm_.imm(0, 12));
    else {
      make_stack(asm_, module);
      read_arguments(asm_, module, layout);
      if (wanted_by(module) != nullptr)
        asm_.address(cconv.int_arg_regs[0], std::string(init_symbol));
      asm_.call(ir::symbol_name(*startup));
    }
    // The answer is already where the system call takes its first argument --
    // unless a destructor runs in between and is free to clobber it.
    bool drains = ring_at(module, layout).has_value();
    if (! module.dtors.empty() || drains) {
      asm_.loadreg(regs.s1, asm_.reg(status));
      for (auto dtor : module.dtors)
        asm_.call(ir::symbol_name(*dtor));
      drain(asm_, module, layout);
      asm_.loadreg(status, asm_.reg(regs.s1));
    }
    asm_.loadreg(regs.a7, asm_.imm(nr_exit_group, 12));
    enter_kernel(asm_);
    // exit_group does not return; trapping makes that explicit.
    asm_.op(mc::ops::trap, Reg{}, {});
    asm_.end_function();
  }


  void emit_abort(mc::Assembler& asm_, const CallConvDesc& cconv, bool walk)
  {
    const auto& regs = registers();
    const mc::PhysReg* first = cconv.int_arg_regs[0];
    const mc::PhysReg* second = cconv.int_arg_regs[1];
    const mc::PhysReg* third = cconv.int_arg_regs[2];
    asm_.begin_function(std::string(abort_symbol), false);
    // Which kind of stop this is, put where the write and the walk leave it.
    const mc::PhysReg* held = regs.reg("s5");
    asm_.loadreg(held, asm_.reg(third));
    // The two arguments moved up one place, the descriptor in front of them.
    asm_.loadreg(third, asm_.reg(second));
    asm_.loadreg(second, asm_.reg(first));
    asm_.loadreg(first, asm_.imm(standard_error, 32, false));
    asm_.loadreg(regs.a7, asm_.imm(nr_write, 12));
    enter_kernel(asm_);
    if (walk) {
      // Where the faulting function was and where its stack stands, taken out
      // of their registers before the call below writes the first.
      asm_.loadreg(first, asm_.reg(regs.ra));
      asm_.loadreg(second, asm_.reg(regs.sp));
      asm_.call(std::string(backtrace_symbol));
    }
    asm_.loadreg(cconv.int_arg_regs[0], asm_.reg(held));
    asm_.loadreg(regs.a7, asm_.imm(nr_exit_group, 12));
    enter_kernel(asm_);
    asm_.op(mc::ops::trap, Reg{}, {});
    asm_.end_function();
  }


  void emit_report(mc::Assembler& asm_, const CallConvDesc& cconv)
  {
    const auto& regs = registers();
    const mc::PhysReg* first = cconv.int_arg_regs[0];
    const mc::PhysReg* second = cconv.int_arg_regs[1];
    const mc::PhysReg* third = cconv.int_arg_regs[2];
    asm_.begin_function(std::string(report_symbol), false);
    asm_.loadreg(third, asm_.reg(second));
    asm_.loadreg(second, asm_.reg(first));
    asm_.loadreg(first, asm_.imm(standard_error, 32, false));
    asm_.loadreg(regs.a7, asm_.imm(nr_write, 12));
    enter_kernel(asm_);
    asm_.ret();
    asm_.end_function();
  }

} // namespace pl4g::target::riscv64
