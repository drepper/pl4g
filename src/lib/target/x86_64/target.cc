// The x86-64 back end.  A port of pypl4g/target/x86_64/target.py and of
// fixups.py, which says which fixup kinds a packaged patch may name.
#include <format>
#include <memory>
#include <stdexcept>

#include "diag/ids.hh"
#include "ir/builder.hh"
#include "mc/machine.hh"
#include "mc/regalloc.hh"
#include "opt/flow.hh"
#include "rt/blob.hh"
#include "target/allocator.hh"
#include "target/backtrace.hh"
#include "target/emit.hh"
#include "target/faults.hh"
#include "target/frames.hh"
#include "target/globals.hh"
#include "target/pool.hh"
#include "target/registry.hh"
#include "target/runtime.hh"
#include "target/shared.hh"
#include "target/stack.hh"
#include "target/started.hh"
#include "target/relocate.hh"
#include "target/target.hh"
#include "target/tests.hh"
#include "target/vectors.hh"
#include "target/x86_64/abi.hh"
#include "target/x86_64/encoder.hh"
#include "target/x86_64/isel.hh"
#include "target/x86_64/levels.hh"
#include "target/x86_64/opcodes.hh"
#include "target/x86_64/peephole.hh"
#include "target/x86_64/regs.hh"
#include "target/x86_64/startup.hh"


namespace pl4g::target {

  namespace x86_64 {

    namespace {

      // EM_X86_64, loaded at the address a fixed-address executable
      // conventionally uses on this architecture.
      constexpr ImageDefaults image_defaults_of{ 62, 0x400000, 0x1000, 16, 16, 0 };

      // int3: falling into padding traps rather than drifting into the next
      // function.
      constexpr std::uint8_t pad_byte = 0xCC;

      // Every kind a packaged patch may name, by the name the patch carries:
      // a displacement and an address are the four and eight byte fields
      // every target has.
      const FixupKinds& by_name()
      {
        static const FixupKinds kinds{
          { std::string(mc::abs32.name), &mc::abs32 },
          { std::string(mc::abs64.name), &mc::abs64 },
          { std::string(mc::pcrel32.name), &mc::pcrel32 },
          { std::string(mc::pcrel32_at_field.name), &mc::pcrel32_at_field },
        };
        return kinds;
      }

      // Code generation for x86-64.
      class X86_64Target final : public Target {
      public:
        X86_64Target() : table(x86_instrs()) {}

        std::string_view triple() const override { return "x86_64-linux-none"; }
        unsigned pointer_bits() const override { return 64; }
        const mc::RegisterInfo& registers() const override { return x86_64::registers().info; }
        std::string_view entry_symbol() const override { return x86_64::entry_symbol; }

        mc::Encoder encoder() const override { return encode; }
        // A displacement occupies its whole field, so it is simply overwritten.
        mc::FixupApplier fixup_applier() const override { return mc::apply_little_endian; }

        std::unique_ptr<mc::Assembler> new_assembler(mc::MCStreamer& streamer, int opt_level) override
        {
          // Whether the level has SSE4.1, which is what rounds a
          // floating-point number without going through an integer.
          selector = std::make_unique<X86Selector>(table, mclevel != "v1", mclevel != "v1", levels::wide(mclevel));
          passes = passes_for(table, opt_level);
          auto& regs = x86_64::registers();
          mc::AssemblerOptions options;
          options.function_alignment = image_defaults_of.function_alignment;
          options.pad_byte = pad_byte;
          for (auto& pass : passes)
            options.machine_passes.push_back(pass.get());
          options.registers = &regs.info;
          options.allocation_order = mc::AllocationOrder::per_class(cc_pl4g().orders(regs.gpr->name, regs.vec->name));
          options.callee_saved = cc_pl4g().callee_saved;
          return std::make_unique<mc::Assembler>(*selector, streamer, std::move(options));
        }

        void generate(ir::Module& module, mc::Assembler& asm_, DiagEngine& diags, int opt_level,
                      const SourceManager* sources) override;

        ImageDefaults image_defaults() const override { return image_defaults_of; }

        // Nothing: every displacement this target writes reaches far enough.
        void relax(std::span<mc::MCSection* const>, std::span<mc::MCSymbol* const>) override {}

        bool has_mclevels() const override { return true; }

        // Generate for this level from now on; what is and is not a name is
        // this target's to say.
        void use_mclevel(std::string_view name) override
        {
          if (! levels::known(name)) [[unlikely]] {
            std::string all;
            for (auto one : levels::names)
              all += (all.empty() ? "" : ", ") + std::string(one);
            throw std::invalid_argument("its levels are " + all);
          }
          mclevel = name;
        }

      private:
        // What this machine can do to a run of elements at once: sixteen bytes
        // on the two older levels and thirty-two on the two newer ones.  The
        // bitwise three, adding and subtracting at every lane width; the
        // saturating pair at a byte and a halfword; multiplying at a halfword,
        // and a word where the level promises SSE4.1.
        Vectors vectors() const
        {
          Vectors made;
          made.bits = levels::wide(mclevel) ? 256 : 128;
          for (auto op : { ir::BinOp::and_, ir::BinOp::or_, ir::BinOp::xor_, ir::BinOp::add, ir::BinOp::sub,
                           ir::BinOp::wrap_add, ir::BinOp::wrap_sub })
            made.binary[op] = every_width;
          made.binary[ir::BinOp::sat_add] = { 8, 16 };
          made.binary[ir::BinOp::sat_sub] = { 8, 16 };
          made.binary[ir::BinOp::wrap_mul] = mclevel == "v1" ? std::set<unsigned>{ 16 } : std::set<unsigned>{ 16, 32 };
          made.unary[ir::UnOp::not_] = every_width;
          // Comparing bytes, and gathering a register of truth values into the
          // bits of an ordinary one.
          made.compares = { 8 };
          made.masks = true;
          return made;
        }

        mc::InstrTable table;
        std::string mclevel{ levels::default_level };
        std::unique_ptr<X86Selector> selector;
        std::vector<std::unique_ptr<mc::MachinePass>> passes;
      };


      void X86_64Target::generate(ir::Module& module, mc::Assembler& asm_, DiagEngine& diags, int opt_level,
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
            lower_function(asm_, *func, lookup_cconv(func->cconv), x86_64::registers().info, &messages, sources,
                           &constants, &clobbers, layout);
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
        const auto& system = lookup_cconv(ir::system_cconv);
        if (messages.wanted()) {
          // The runtime follows the system's convention whatever the function
          // that faults follows.
          emit_abort(asm_, system, true);
          std::vector<mc::Reg> arguments(system.int_arg_regs.begin(), system.int_arg_regs.end());
          emit_backtrace(asm_, walk_abi(), arguments);
        }
        if (! failures.empty() || observes(module))
          // Only where something reports and goes on.
          emit_report(asm_, system);
        if (module.stack_size > 0)
          // What the kernel calls where the program follows a bad address.
          emit_handler(asm_, syscalls(), stack_abi());
        if (module.startup != nullptr && ! shared) {
          // What the processor is asked, and told, only where there is an
          // entry point to ask it: a shared object runs in a program that was
          // started on this machine already.
          std::optional<std::string> refused;
          if (! levels::requirements(mclevel).empty())
            refused = messages.symbol(levels::described(mclevel));
          // And what a system that has the processor and has turned off the
          // registers this program uses is told.
          std::optional<std::string> turned_off;
          if (levels::state(mclevel) != 0)
            turned_off = messages.symbol(levels::disabled(mclevel));
          if (pie)
            emit_relocate(asm_, relocate_regs(), syscalls());
          emit_start(asm_, module, lookup_cconv(module.startup->cconv), mclevel, refused, turned_off, failures,
                     layout, pie);
        }
        runtime.emit(asm_, by_name());
        messages.emit(asm_);
        constants.emit(asm_);
        walked.emit(asm_);
      }

    } // anonymous namespace

  } // namespace x86_64


  std::unique_ptr<Target> make_x86_64(std::string_view triple)
  {
    (void) triple;
    return std::make_unique<x86_64::X86_64Target>();
  }

} // namespace pl4g::target
