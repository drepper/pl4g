// The program's entry point on AArch64.
#include "target/aarch64/startup.hh"

#include <array>
#include <cassert>
#include <string>

#include "ir/builder.hh"
#include "target/aarch64/abi.hh"
#include "target/aarch64/ops.hh"
#include "target/aarch64/regs.hh"
#include "target/emit.hh"
#include "target/relocate.hh"
#include "target/started.hh"
#include "target/statuses.hh"


namespace pl4g::target::aarch64 {

  namespace {

    using mc::Reg;

    // The register a system call number is passed in.
    Reg syscall_number_reg() { return regs().x8; }

    // Where the count of tests that did not pass is kept while they run: a
    // register a call leaves alone.
    Reg count_reg() { return reg("x20"); }

    // And where what the kernel set the process up with waits until the
    // runtime is asked to read it, the constructors running in between.
    Reg held_stack() { return reg("x21"); }

    mc::MCOperand register_(Reg r) { return mc::MCReg{ r }; }

    mc::MCImm immediate(__int128 value, int bits) { return mc::MCImm{ value, bits, false }; }

    void enter(mc::Assembler& asm_) { asm_.op(supervisor_call(), Reg{}, {}); }

    // Read the stack pointer, which an addition of nothing is the way to do: a
    // move between registers reads that encoding as the zero register, and
    // this is the form that reads it as the stack pointer.
    void read_sp(mc::Assembler& asm_, Reg dst)
    {
      asm_.op(mc::ops::plus, dst, { register_(regs().sp), immediate(0, 12) });
    }

    // And write it, for the same reason.
    void write_sp(mc::Assembler& asm_, Reg src)
    {
      asm_.op(mc::ops::plus, regs().sp, { register_(src), immediate(0, 12) });
    }


    // Put the program on a stack of its own, where it asked for one.
    void make_stack(mc::Assembler& asm_, const ir::Module& module)
    {
      if (module.stack_size <= 0)
        return;
      emit_make(asm_, syscalls(), stack_abi(), module.stack_size, module.guard_size);
    }


    // Have the runtime read the arguments into the record, where one is taken:
    // what the kernel set the process up with, and where in the record the run
    // of them goes.  The system's convention, the callee being the runtime.
    void read_arguments(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout)
    {
      auto at = arguments_at(module, layout);
      if (! at)
        return;
      const auto& theirs = lookup_cconv(ir::system_cconv);
      asm_.loadreg(theirs.int_arg_regs[0], register_(held_stack()));
      asm_.address(theirs.int_arg_regs[1], std::string(init_symbol));
      if (*at != 0)
        asm_.op(mc::ops::plus, theirs.int_arg_regs[1],
                { register_(theirs.int_arg_regs[1]), immediate(__int128(*at), 12) });
      asm_.call(std::string(reads_arguments));
    }


    // Wait for every request still outstanding, once the program is done: the
    // runtime is handed where in the record the ring is.
    void drain(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout)
    {
      auto at = ring_at(module, layout);
      if (! at)
        return;
      const auto& theirs = lookup_cconv(ir::system_cconv);
      asm_.address(theirs.int_arg_regs[0], std::string(init_symbol));
      if (*at != 0)
        asm_.op(mc::ops::plus, theirs.int_arg_regs[0],
                { register_(theirs.int_arg_regs[0]), immediate(__int128(*at), 12) });
      asm_.call(std::string(drains));
    }


    // Build the environment, where the program named it: the runtime is asked
    // for the strings, and they are handed to the function the checker
    // generated; what comes back is the word the variable holds.
    void read_environment(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv)
    {
      if (module.environ == nullptr)
        return;
      auto builder = module.function(environ_make);
      assert(builder != nullptr && "the variable is there and nothing fills it");
      const auto& theirs = lookup_cconv(ir::system_cconv);
      asm_.loadreg(theirs.int_arg_regs[0], register_(held_stack()));
      asm_.call(std::string(reads_environment));
      // What the runtime answers with is two words, and they go straight on as
      // the one argument the function takes.
      const mc::PhysReg* handed[2] = { cconv.int_arg_regs[0], cconv.int_arg_regs[1] };
      const mc::PhysReg* came[2] = { theirs.int_ret_regs[0], theirs.int_ret_regs[1] };
      assert(! (handed[0] == came[1] && handed[1] == came[0]) && "the two would have to be exchanged");
      int order[2] = { 0, 1 };
      if (handed[0] == came[1]) {
        order[0] = 1;
        order[1] = 0;
      }
      for (int which : order)
        if (handed[which] != came[which])
          asm_.loadreg(handed[which], register_(came[which]));
      asm_.call(ir::symbol_name(*builder));
      // The second argument register rather than the first: on one of these
      // targets the first is also the one an answer comes back in.
      auto where = cconv.int_arg_regs[1];
      asm_.address(where, std::string(environ_symbol));
      asm_.store(memory(where, 0, 64), register_(cconv.int_ret_regs[0]));
    }


    // Call each test this binary runs, and stop if any of them failed.  Every
    // one of them is run: one that answers false names itself and the next is
    // tried.
    void run_tests(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv,
                   const std::unordered_map<const ir::Function*, Failure>& failures)
    {
      std::vector<const ir::Function*> ran;
      for (auto one : run_by(module))
        if (failures.contains(one))
          ran.push_back(one);
      if (ran.empty())
        return;
      asm_.loadreg(count_reg(), register_(regs().xzr));
      for (auto one : ran) {
        const Failure& found = failures.at(one);
        asm_.call(ir::symbol_name(*one));
        auto passed = asm_.reserve_label("test.passed");
        asm_.branch(mc::Condition::ne, register_(cconv.int_ret_regs[0]), immediate(0, 32), passed);
        asm_.address(cconv.int_arg_regs[0], found.symbol);
        asm_.loadreg(cconv.int_arg_regs[1], immediate(__int128(found.length), 16));
        asm_.call(std::string(report_symbol));
        asm_.op(mc::ops::plus, count_reg(), { register_(count_reg()), immediate(1, 12) });
        asm_.block(passed);
      }
      auto fit = asm_.reserve_label("tests.passed");
      asm_.branch(mc::Condition::eq, register_(count_reg()), register_(regs().xzr), fit);
      asm_.loadreg(cconv.int_ret_regs[0], immediate(statuses::TESTS_FAILED, 16));
      asm_.loadreg(syscall_number_reg(), immediate(nr_exit_group, 16));
      enter(asm_);
      asm_.op(mc::ops::trap, Reg{}, {});
      asm_.block(fit);
    }

  } // anonymous namespace


  const SyscallABI& syscalls()
  {
    static const SyscallABI made = [] {
      SyscallABI abi;
      abi.mmap = nr_mmap;
      abi.munmap = nr_munmap;
      abi.mprotect = nr_mprotect;
      abi.number = syscall_number_reg();
      for (int n = 0; n < 6; ++n)
        abi.arguments.push_back(reg("x" + std::to_string(n)));
      abi.answer = regs().x0;
      abi.enter = enter;
      return abi;
    }();
    return made;
  }


  const AllocatorRegs& allocator_regs()
  {
    static const AllocatorRegs made{ regs().x0, regs().x1, regs().x0, { reg("x9"), reg("x10"), reg("x11") } };
    return made;
  }


  const StackABI& stack_abi()
  {
    static const StackABI made = [] {
      StackABI abi;
      abi.mprotect = nr_mprotect;
      abi.sigaltstack = nr_sigaltstack;
      abi.rt_sigaction = nr_rt_sigaction;
      abi.write = nr_write;
      abi.exit_group = nr_exit_group;
      // The one of the three architectures whose page size is configured rather
      // than fixed: sixty-four kilobytes is the largest a kernel may choose.
      abi.page = 64 * 1024;
      abi.kept = { reg("x22"), reg("x23"), reg("x24") };
      abi.scratch = { reg("x9"), reg("x10"), reg("x11") };
      abi.read_sp = read_sp;
      abi.write_sp = write_sp;
      return abi;
    }();
    return made;
  }


  const WalkABI& walk_abi()
  {
    // The kept four are callee-saved, which is what the system call leaves
    // alone; nothing will miss them, the helper that calls the walk being
    // about to end the program.
    static const WalkABI made = [] {
      WalkABI abi;
      abi.write = nr_write;
      abi.number = syscall_number_reg();
      abi.arguments = { regs().x0, regs().x1, reg("x2") };
      abi.enter = enter;
      abi.kept = { regs().x19, reg("x20"), reg("x21"), reg("x22") };
      abi.scratch = { reg("x9"), reg("x10") };
      return abi;
    }();
    return made;
  }


  const host::HostABI& host_abi()
  {
    // The two the architecture keeps for code between a call and what it
    // calls, which is what a stub is, and the link register a call to the
    // finder writes.
    static const host::HostABI made = [] {
      host::HostABI abi;
      abi.slot = reg("x16");
      abi.name = reg("x17");
      abi.through = reg("x9");
      abi.link = mc::Reg(regs().x30);
      return abi;
    }();
    return made;
  }


  std::span<const mc::Reg> relocate_regs()
  {
    static const auto made = [] {
      return std::array<mc::Reg, relocate_scratch>{ reg("x9"), reg("x10"), reg("x11"), reg("x12"), reg("x13"), reg("x14"), reg("x15") };
    }();
    return made;
  }


  void emit_start(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv,
                  const std::unordered_map<const ir::Function*, Failure>& failures, const ir::DataLayout& layout,
                  bool relocates)
  {
    const ir::Function* startup = module.startup;
    assert(startup != nullptr);
    Reg status = cconv.int_ret_regs[0];

    asm_.begin_function(std::string(entry_symbol), true);
    // A position-independent image makes its addresses true first of all.
    if (relocates)
      asm_.call(std::string(relocate_symbol));
    // What the kernel set the process up with is at the stack pointer, and this
    // is the only moment it is.  An addition of nothing and not a move: the
    // move between registers reads that encoding as the zero.
    if (arguments_at(module, layout) || module.environ != nullptr)
      asm_.op(mc::ops::plus, held_stack(), { register_(regs().sp), immediate(0, 12) });
    // The outermost frame is marked by a null frame pointer and a null return
    // address, so that a debugger unwinding the stack knows where to stop.
    asm_.loadreg(regs().x29, register_(regs().xzr));
    asm_.loadreg(regs().x30, register_(regs().xzr));
    read_environment(asm_, module, cconv);
    for (auto ctor : module.ctors)
      asm_.call(ir::symbol_name(*ctor));
    run_tests(asm_, module, cconv, failures);
    if (! module.test_plan.empty()) {
      // A binary built to run tests and nothing else: every one of them passed,
      // or it left through the helper above and never arrived here.
      asm_.loadreg(status, immediate(0, 16));
    } else {
      make_stack(asm_, module);
      read_arguments(asm_, module, layout);
      if (wanted_by(module) != nullptr)
        // Where the record the program was started with is, which is the whole
        // of what is handed over.
        asm_.address(cconv.int_arg_regs[0], std::string(init_symbol));
      asm_.call(ir::symbol_name(*startup));
    }
    // The value a function returns is already in the register a system call
    // takes its first argument in -- unless a destructor or the drain runs in
    // between and is free to clobber it.
    bool draining = ring_at(module, layout).has_value();
    if (! module.dtors.empty() || draining) {
      asm_.loadreg(regs().x19, register_(status));
      for (auto dtor : module.dtors)
        asm_.call(ir::symbol_name(*dtor));
      drain(asm_, module, layout);
      asm_.loadreg(status, register_(regs().x19));
    }
    asm_.loadreg(syscall_number_reg(), immediate(nr_exit_group, 16));
    enter(asm_);
    // exit_group does not return; trapping makes that explicit rather than
    // letting control run off the end of the section.
    asm_.op(mc::ops::trap, Reg{}, {});
    asm_.end_function();
  }


  void emit_abort(mc::Assembler& asm_, const CallConvDesc& cconv, bool walk)
  {
    Reg first = cconv.int_arg_regs[0];
    Reg second = cconv.int_arg_regs[1];
    Reg third = cconv.int_arg_regs[2];
    asm_.begin_function(std::string(abort_symbol), false);
    // Which kind of stop this is, put somewhere the write below will not touch
    // and the walk of the stack keeps nothing in.  It arrives in the register
    // the write's own third argument goes in, so it moves first.
    Reg held = reg("x23");
    asm_.loadreg(held, register_(third));
    // The three arguments of the system call are the two this was given,
    // moved up one place, with the descriptor in front of them.
    asm_.loadreg(third, register_(second));
    asm_.loadreg(second, register_(first));
    asm_.loadreg(first, immediate(standard_error, 32));
    asm_.loadreg(syscall_number_reg(), immediate(nr_write, 16));
    enter(asm_);
    if (walk) {
      // The call that reached here left the return address in a register and
      // touched nothing else, so where the faulting function was and where its
      // stack stands are both to hand.
      asm_.loadreg(first, register_(regs().x30));
      asm_.op(mc::ops::plus, second, { register_(regs().sp), immediate(0, 12) });
      asm_.call(std::string(backtrace_symbol));
    }
    asm_.loadreg(cconv.int_arg_regs[0], register_(held));
    asm_.loadreg(syscall_number_reg(), immediate(nr_exit_group, 16));
    enter(asm_);
    asm_.op(mc::ops::trap, Reg{}, {});
    asm_.end_function();
  }


  void emit_report(mc::Assembler& asm_, const CallConvDesc& cconv)
  {
    Reg first = cconv.int_arg_regs[0];
    Reg second = cconv.int_arg_regs[1];
    Reg third = cconv.int_arg_regs[2];
    asm_.begin_function(std::string(report_symbol), false);
    asm_.loadreg(third, register_(second));
    asm_.loadreg(second, register_(first));
    asm_.loadreg(first, immediate(standard_error, 16));
    asm_.loadreg(syscall_number_reg(), immediate(nr_write, 16));
    enter(asm_);
    asm_.ret();
    asm_.end_function();
  }

} // namespace pl4g::target::aarch64
