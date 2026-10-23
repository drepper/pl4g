// The AArch64 back end.  A port of pypl4g/target/aarch64/target.py.
#include <format>
#include <memory>

#include "diag/ids.hh"
#include "ir/builder.hh"
#include "mc/machine.hh"
#include "mc/regalloc.hh"
#include "opt/flow.hh"
#include "rt/blob.hh"
#include "target/aarch64/abi.hh"
#include "target/aarch64/desc.hh"
#include "target/aarch64/fixups.hh"
#include "target/aarch64/isel.hh"
#include "target/aarch64/opcodes.hh"
#include "target/aarch64/regs.hh"
#include "target/aarch64/startup.hh"
#include "target/allocator.hh"
#include "target/relocate.hh"
#include "target/relro.hh"
#include "target/backtrace.hh"
#include "target/faults.hh"
#include "target/frames.hh"
#include "target/emit.hh"
#include "target/globals.hh"
#include "target/pool.hh"
#include "target/registry.hh"
#include "target/runtime.hh"
#include "target/shared.hh"
#include "target/stack.hh"
#include "target/started.hh"
#include "target/target.hh"
#include "target/tests.hh"
#include "target/vectors.hh"


namespace pl4g::target {

  namespace aarch64 {

    namespace {

      // EM_AARCH64.  The page size is the largest a kernel may be configured
      // with, so that one image loads whatever the running kernel chose.
      constexpr ImageDefaults image_defaults_of{ 183, 0x400000, 0x10000, 16, 16, 0 };


      // Code generation for AArch64.
      class AArch64Target final : public Target {
      public:
        AArch64Target() : table(aarch64_instrs()) {}

        std::string_view triple() const override { return "aarch64-linux-none"; }
        unsigned pointer_bits() const override { return 64; }
        const mc::RegisterInfo& registers() const override { return regs().info; }
        std::string_view entry_symbol() const override { return aarch64::entry_symbol; }

        mc::Encoder encoder() const override { return encode; }
        // A relocation shares its word with the opcode, so it is inserted.
        mc::FixupApplier fixup_applier() const override { return apply_fixup; }

        std::unique_ptr<mc::Assembler> new_assembler(mc::MCStreamer& streamer, int opt_level) override
        {
          (void) opt_level;
          selector = std::make_unique<A64Selector>(table);
          mc::AssemblerOptions options;
          options.function_alignment = image_defaults_of.function_alignment;
          options.pad_byte = pad_byte;
          options.registers = &regs().info;
          options.allocation_order = mc::AllocationOrder::per_class(cc_pl4g().orders(regs().gpr->name,
                                                                                     regs().vec->name));
          options.callee_saved = cc_pl4g().callee_saved;
          return std::make_unique<mc::Assembler>(*selector, streamer, std::move(options));
        }

        void generate(ir::Module& module, mc::Assembler& asm_, DiagEngine& diags, int opt_level,
                      const SourceManager* sources) override;

        ImageDefaults image_defaults() const override { return image_defaults_of; }

        // Nothing: every displacement this target writes reaches far enough.
        void relax(std::span<mc::MCSection* const>, std::span<mc::MCSymbol* const>) override {}

        // What this machine can do to a run of elements at once: sixteen bytes
        // at a time, the Advanced SIMD instructions being in the base.  Bitwise
        // and adding and subtracting at every lane width, each with the check
        // that says whether any lane went past; the saturating pair at every
        // width; multiplying only where it may wrap, and not at the widest.
        static Vectors vectors()
        {
          Vectors made;
          made.bits = 128;
          for (auto op : { ir::BinOp::and_, ir::BinOp::or_, ir::BinOp::xor_, ir::BinOp::add, ir::BinOp::sub,
                           ir::BinOp::sat_add, ir::BinOp::sat_sub, ir::BinOp::wrap_add, ir::BinOp::wrap_sub })
            made.binary[op] = every_width;
          made.binary[ir::BinOp::wrap_mul] = { 8, 16, 32 };
          made.unary[ir::UnOp::not_] = every_width;
          // Comparing bytes, and gathering a register of truth values into the
          // bits of an ordinary one.
          made.compares = { 8 };
          made.masks = true;
          return made;
        }

      private:
        mc::InstrTable table;
        std::unique_ptr<A64Selector> selector;
      };


      void AArch64Target::generate(ir::Module& module, mc::Assembler& asm_, DiagEngine& diags, int opt_level,
                                   const SourceManager* sources)
      {
        (void) opt_level;
        Messages messages;
        Constants constants;
        // Where each function keeps its return address, so that a fault can say
        // who called and not only where it happened.
        Frames walked;
        // The runtime compiled ahead of time, placed only where the program
        // reaches it.
        Runtime runtime;
        runtime.blob = rt::blob_for(architecture_of(triple()));
        if (reaches(module, runtime) || entry_wants_runtime(module))
          runtime.reached();
        ir::DataLayout layout;
        layout.pointer_size = pointer_bits() / 8;
        layout.frames = &module.frame_sizes;
        // What the front end asked of a whole run of elements at once, brought
        // down to what this machine has, before a single instruction is chosen.
        settle(module, vectors(), layout);
        emit_globals(asm_, module, layout);
        // What the program was started with, where it takes it.
        emit_started(asm_, module, layout);
        // And what the stack the program makes for itself is remembered in.
        if (module.stack_size > 0)
          emit_state(asm_, stack_abi());
        asm_.section(".text", placed(false, image_defaults_of.text_alignment, true));
        // What each function turned out to destroy, so that a call to one saves
        // only what it has to: the functions are built callees first.
        Destroyed clobbers;
        for (ir::Function* func : opt::in_call_order(module)) {
          if (func->is_declaration())
            continue;
          try {
            lower_function(asm_, *func, lookup_cconv(func->cconv), regs().info, &messages, sources, &constants,
                           &clobbers, layout);
            auto built = asm_.functions.back();
            clobbers[ir::symbol_name(*func)] = mc::clobbered_units(*built);
            walked.record(ir::symbol_name(*func), func->name, built->return_at, built->caller_at);
          } catch (const UnsupportedOperation& error) {
            diags.emit(D::IMPL_BACKEND_UNSUPPORTED, error.span.value_or(func->span), { { "construct", error.detail } });
            return;
          } catch (const mc::RegisterPressureError& error) {
            // Running out of registers is a limit of this compiler like any
            // other, reported where the function is.
            diags.emit(D::IMPL_BACKEND_UNSUPPORTED, func->span, { { "construct", std::string(error.what()) } });
            return;
          } catch (const mc::EncodingError& error) {
            diags.emit(D::IMPL_BACKEND_UNENCODABLE, error.span.value_or(func->span), { { "detail", error.detail } });
            return;
          }
        }
        // Where the last function ends, which is where the table's sentinel row
        // begins.
        asm_.label(std::string(frames_end_symbol));
        bool shared = module.output == ir::Output::shared;
        if (module.startup == nullptr && ! shared)
          return;
        if (shared)
          // What the host defines, reached through stubs, and the tables the
          // loader runs the constructors and destructors from.  Before the
          // question below: a stub says what it did not find through the same
          // helper a fault does.
          shared::emit(asm_, module, host_abi(), lookup_cconv(ir::system_cconv), messages, sources,
                       image_defaults_of.text_alignment, abort_symbol);
        if (allocator_wanted_by(module))
          // The allocator where something calls it and nowhere else.
          emit_allocator(asm_, syscalls(), allocator_regs(), abort_symbol, messages.symbol(std::string(out_of_memory)));
        // Before the question below: a binary whose only message is a failing
        // test needs the helper as much as one that divides by zero.
        auto failures = failures_of(module, messages);
        walked.wanted = messages.wanted();
        if (messages.wanted()) {
          // The runtime follows the system's convention whatever the function
          // that faults follows.
          emit_abort(asm_, lookup_cconv(ir::system_cconv), true);
          std::vector<mc::Reg> arguments(lookup_cconv(ir::system_cconv).int_arg_regs.begin(),
                                         lookup_cconv(ir::system_cconv).int_arg_regs.end());
          emit_backtrace(asm_, walk_abi(), arguments);
        }
        if (! failures.empty() || observes(module))
          // Only where something reports and goes on.
          emit_report(asm_, lookup_cconv(ir::system_cconv));
        if (module.stack_size > 0)
          // What the kernel calls where the program follows a bad address.
          emit_handler(asm_, syscalls(), stack_abi());
        if (module.startup != nullptr && ! shared) {
          // No entry point for a shared object: the program that loads it
          // calls what it offers.
          if (pie)
            // Where something is `@[relro]`, the entry point makes the pages
            // read-only after the constructors, and the relocation leaves them.
            emit_relocate(asm_, relocate_regs(), syscalls(), ! relro_wanted(module));
          emit_start(asm_, module, lookup_cconv(module.startup->cconv), failures, layout, pie);
        }
        runtime.emit(asm_, by_name());
        messages.emit(asm_);
        constants.emit(asm_);
        walked.emit(asm_);
      }

    } // anonymous namespace

  } // namespace aarch64


  std::unique_ptr<Target> make_aarch64(std::string_view triple)
  {
    (void) triple;
    return std::make_unique<aarch64::AArch64Target>();
  }

} // namespace pl4g::target
