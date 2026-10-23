// The RISC-V 64-bit back end.  A port of pypl4g/target/riscv64/target.py.
#include <format>
#include <memory>
#include <set>
#include <stdexcept>

#include "diag/ids.hh"
#include "ir/builder.hh"
#include "mc/fixedwidth.hh"
#include "mc/fragment.hh"
#include "mc/machine.hh"
#include "mc/regalloc.hh"
#include "opt/flow.hh"
#include "rt/blob.hh"
#include "target/allocator.hh"
#include "target/relocate.hh"
#include "target/relro.hh"
#include "target/backtrace.hh"
#include "target/emit.hh"
#include "target/faults.hh"
#include "target/frames.hh"
#include "target/globals.hh"
#include "target/pool.hh"
#include "target/registry.hh"
#include "target/riscv64/abi.hh"
#include "target/riscv64/attributes.hh"
#include "target/riscv64/fixups.hh"
#include "target/riscv64/isa.hh"
#include "target/riscv64/isel.hh"
#include "target/riscv64/peephole.hh"
#include "target/riscv64/opcodes.hh"
#include "target/riscv64/regs.hh"
#include "target/riscv64/startup.hh"
#include "target/runtime.hh"
#include "target/shared.hh"
#include "target/stack.hh"
#include "target/started.hh"
#include "target/target.hh"
#include "target/tests.hh"
#include "target/vectors.hh"


namespace pl4g::target {

  namespace riscv64 {

    namespace {

      // What the header's flag word says on this architecture: which
      // floating-point convention the functions follow.  DOUBLE is what is
      // emitted where the build has the D extension.
      constexpr std::uint32_t float_abi_soft = 0x0;
      constexpr std::uint32_t float_abi_single = 0x2;
      constexpr std::uint32_t float_abi_double = 0x4;

      // EM_RISCV.  The flag word is settled per build.
      constexpr ImageDefaults image_defaults_of{ 243, 0x400000, 0x1000, 16, 16, float_abi_double };

      // How far a conditional branch reaches, in bytes either way.
      constexpr std::int64_t branch_reach = 1 << 12;

      // The bits of a conditional branch that hold where it goes, and the one
      // that tells each comparison from its opposite.
      constexpr std::uint32_t branch_offset_bits = 0xFE000F80;
      constexpr std::uint32_t branch_opposite = 1 << 12;

      // `jal zero, 0`, the offset filled in by the jump's own fixup.
      constexpr std::uint32_t jump = 0x6F;


      // Whether a type is a floating-point one or is built out of them.
      bool is_floating(const ir::Type* ty)
      {
        if (ir::is<ir::FloatType>(ty))
          return true;
        if (auto array = ir::as<ir::ArrayType>(ty))
          return is_floating(array->element);
        if (auto list = ir::as<ir::ListType>(ty))
          return is_floating(list->element);
        if (auto run = ir::as<ir::VecType>(ty))
          return is_floating(run->element);
        if (auto ptr = ir::as<ir::PtrType>(ty))
          return is_floating(ptr->pointee);
        if (auto result = ir::as<ir::ResultType>(ty))
          return is_floating(result->ok);
        if (auto tuple = ir::as<ir::TupleType>(ty))
          return std::ranges::any_of(tuple->members, is_floating);
        return false;
      }

      // Whether anything about a function is a floating-point value: its own
      // type and every value it computes.
      bool touches_floats(const ir::Function& func)
      {
        if (std::ranges::any_of(func.fty->params, is_floating) || is_floating(func.fty->ret))
          return true;
        for (const auto& block : func.blocks) {
          for (const auto& param : block->params)
            if (is_floating(param->ty))
              return true;
          for (auto inst : block->insts)
            if (is_floating(inst->ty))
              return true;
        }
        return false;
      }

      std::uint32_t word_of(const mc::Bytes& bytes, std::size_t at)
      {
        return std::uint32_t(bytes[at]) | std::uint32_t(bytes[at + 1]) << 8 | std::uint32_t(bytes[at + 2]) << 16
               | std::uint32_t(bytes[at + 3]) << 24;
      }

      void put_word(mc::Bytes& bytes, std::uint32_t word)
      {
        for (int shift = 0; shift < 32; shift += 8)
          bytes.push_back(std::uint8_t(word >> shift));
      }

      // Whether `fragment` is a conditional branch, not yet grown, to a place
      // in `section` further than one reaches.
      bool too_far(const mc::MCInstFragment& fragment, const mc::MCSection* section)
      {
        if (fragment.fixups.size() != 1 || fragment.encoded.size() != 4)
          return false;
        const auto& fixup = fragment.fixups[0];
        if (fixup.kind != &branch_fixup || fixup.target == nullptr || fixup.target->kind != mc::MCExpr::Kind::symbol)
          return false;
        auto symbol = static_cast<const mc::SymExpr*>(fixup.target.get())->symbol;
        if (symbol->section != section || ! symbol->defined)
          return false;
        auto distance = std::int64_t(symbol->offset) - (std::int64_t(fragment.offset) + fixup.offset);
        return ! (-branch_reach <= distance && distance < branch_reach);
      }

      // Turn a conditional branch into its opposite over eight bytes, and a
      // jump.
      void jump_around(mc::MCInstFragment& fragment)
      {
        auto word = word_of(fragment.encoded, 0);
        // Eight bytes on: imm[4:1] is 0b0100 in bits 11..8, everything else
        // nought.
        auto opposite = ((word & ~branch_offset_bits) ^ branch_opposite) | (4 << 8);
        mc::Bytes grown;
        put_word(grown, opposite);
        put_word(grown, jump);
        fragment.encoded = std::move(grown);
        auto fixup = fragment.fixups[0];
        fixup.kind = &jal_fixup;
        fixup.offset += 4;
        fragment.fixups = { fixup };
      }


      // Code generation for RISC-V, sixty-four bit.
      class RISCV64Target final : public Target {
      public:
        RISCV64Target() : isa(isa::parse(isa::default_name)) {}

        std::string_view triple() const override { return "riscv64-linux-none"; }
        unsigned pointer_bits() const override { return 64; }
        const mc::RegisterInfo& registers() const override { return riscv64::registers().info; }
        std::string_view entry_symbol() const override { return riscv64::entry_symbol; }

        mc::Encoder encoder() const override
        {
          return [](const mc::MCInst& inst) { return mc::encode_fixed_width(inst, instruction_size); };
        }
        // A jump offset is scattered through its word, so it is inserted.
        mc::FixupApplier fixup_applier() const override { return apply_fixup; }

        std::unique_ptr<mc::Assembler> new_assembler(mc::MCStreamer& streamer, int opt_level) override
        {
          (void) opt_level;
          selector = std::make_unique<RVSelector>(isa);
          auto& regs = riscv64::registers();
          mc::AssemblerOptions options;
          options.function_alignment = image_defaults_of.function_alignment;
          options.pad_byte = pad_byte;
          options.registers = &regs.info;
          options.allocation_order = mc::AllocationOrder::per_class(cc_pl4g().orders(regs.gpr->name, regs.fpr->name));
          options.callee_saved = cc_pl4g().callee_saved;
          options.machine_passes.push_back(&said_once);
          return std::make_unique<mc::Assembler>(*selector, streamer, std::move(options));
        }

        // What this machine can do to a run of elements at once: what the build
        // says.  Without the vector extension nothing; with it sixteen bytes at
        // a time -- the narrowest register the extension allows -- moved, the
        // bitwise three, bytes compared and what they said gathered into a mask.
        Vectors vectors() const
        {
          Vectors made;
          if (! isa.has("v"))
            return made;
          made.bits = 128;
          for (auto op : { ir::BinOp::and_, ir::BinOp::or_, ir::BinOp::xor_ })
            made.binary[op] = every_width;
          made.unary[ir::UnOp::not_] = every_width;
          made.compares = { 8 };
          made.masks = true;
          return made;
        }

        void generate(ir::Module& module, mc::Assembler& asm_, DiagEngine& diags, int opt_level,
                      const SourceManager* sources) override;

        // The floating-point convention is the one thing here a build settles
        // rather than the architecture.
        ImageDefaults image_defaults() const override
        {
          auto made = image_defaults_of;
          if (! isa.has("d"))
            made.header_flags = isa.has("f") ? float_abi_single : float_abi_soft;
          return made;
        }

        // Make a conditional branch whose target is further than it reaches
        // into the opposite branch over one instruction and a jump, laid out
        // again after each round until a round grows none.
        void relax(std::span<mc::MCSection* const> sections, std::span<mc::MCSymbol* const> symbols) override
        {
          for (int round = 0; round < 32; ++round) {
            for (auto section : sections) {
              mc::layout_section(*section);
              mc::resolve_symbol_offsets(*section, symbols);
            }
            bool grew = false;
            for (auto section : sections)
              for (auto& fragment : section->fragments)
                if (fragment->kind == mc::MCFragment::Kind::inst) {
                  auto& inst = static_cast<mc::MCInstFragment&>(*fragment);
                  if (too_far(inst, section)) {
                    jump_around(inst);
                    grew = true;
                  }
                }
            if (! grew)
              return;
          }
          throw std::runtime_error("branch relaxation did not settle");
        }

        bool has_mclevels() const override { return true; }

        // Build for this from now on, which here is an ISA string or a profile.
        void use_mclevel(std::string_view name) override
        {
          isa::ISA found;
          try {
            found = isa::parse(name);
          } catch (const isa::BadName& error) {
            throw std::invalid_argument(error.what());
          }
          if (found.bits != int(pointer_bits())) [[unlikely]]
            throw std::invalid_argument(std::format(
              "it names a base whose addresses are {} bits wide, and this target's are {}", found.bits,
              pointer_bits()));
          if (found.embedded) [[unlikely]]
            throw std::invalid_argument("it names the reduced base, which has sixteen registers and a calling "
                                        "convention of its own, and this compiler has neither");
          isa = std::move(found);
        }

      private:
        // What a program is built for: the base and every extension of it.
        isa::ISA isa;
        std::unique_ptr<RVSelector> selector;
        SaidOnce said_once;
      };


      void RISCV64Target::generate(ir::Module& module, mc::Assembler& asm_, DiagEngine& diags, int opt_level,
                                   const SourceManager* sources)
      {
        (void) opt_level;
        auto& regs = riscv64::registers();
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
        // Which functions work on runs is asked first: on a build without the
        // vector extension none does once they are settled, and it is those that
        // keep a register to themselves for the frame a lane apiece makes.
        std::set<const ir::Function*> on_runs;
        for (auto& [_, func] : module.functions)
          if (! func->is_declaration() && works_on_runs(*func))
            on_runs.insert(func.get());
        settle(module, vectors(), layout);
        emit_globals(asm_, module, layout);
        emit_started(asm_, module, layout);
        if (module.stack_size > 0)
          emit_state(asm_, stack_abi());
        asm_.section(".text", placed(false, image_defaults_of.text_alignment, true));
        // What each function turned out to destroy, so that a call to one saves
        // only what it has to: the functions are built callees first.
        Destroyed clobbers;
        for (ir::Function* func : opt::in_call_order(module)) {
          if (func->is_declaration())
            continue;
          if (! isa.floats() && touches_floats(*func)) [[unlikely]] {
            // Not a limit of this compiler but of what it was asked to build
            // for.
            diags.emit(D::IMPL_BACKEND_NEEDS_AN_EXTENSION, func->span,
                       { { "extension", std::string("F and D") }, { "level", isa.named } });
            return;
          }
          try {
            lower_function(asm_, *func, lookup_cconv(func->cconv), regs.info, &messages, sources, &constants,
                           &clobbers, layout, on_runs.contains(func));
            auto built = asm_.functions.back();
            clobbers[ir::symbol_name(*func)] = mc::clobbered_units(*built);
            walked.record(ir::symbol_name(*func), func->name, built->return_at, built->caller_at);
          } catch (const UnsupportedOperation& error) {
            diags.emit(D::IMPL_BACKEND_UNSUPPORTED, error.span.value_or(func->span), { { "construct", error.detail } });
            return;
          } catch (const mc::RegisterPressureError& error) {
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
          emit_allocator(asm_, syscalls(), allocator_regs(), abort_symbol, messages.symbol(std::string(out_of_memory)));
        // Before the question below: a binary whose only message is a failing
        // test needs the helper as much as one that divides by zero.
        auto failures = failures_of(module, messages);
        walked.wanted = messages.wanted();
        const auto& system = lookup_cconv(ir::system_cconv);
        if (messages.wanted()) {
          emit_abort(asm_, system, true);
          std::vector<mc::Reg> arguments(system.int_arg_regs.begin(), system.int_arg_regs.end());
          emit_backtrace(asm_, walk_abi(), arguments);
        }
        if (! failures.empty() || observes(module))
          emit_report(asm_, system);
        if (module.stack_size > 0)
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
        // What the image was built for, said in the file for whatever reads it:
        // this architecture's base is small and everything else an extension.
        mc::SectionOptions options;
        options.alloc = false;
        options.alignment = 1;
        options.sh_type = sht_riscv_attributes;
        asm_.section(std::string(attributes_section), options);
        asm_.bytes(build_attributes(isa.normalized()));
      }

    } // anonymous namespace

  } // namespace riscv64


  std::unique_ptr<Target> make_riscv64(std::string_view triple)
  {
    (void) triple;
    return std::make_unique<riscv64::RISCV64Target>();
  }

} // namespace pl4g::target
