// What a back end must provide: pypl4g's target/target.py.
//
// One `Target` per architecture (src/lib/target/<arch>/), found by the triple
// it generates for (`lookup_target`).  The driver hands it the module, an
// assembler built over a streamer that encodes with it, and the engine to say
// what is wrong with; the ELF writer then lays out what was streamed with the
// constants it gives and stores each fixup's value as it says.
#pragma once

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <span>
#include <string_view>

#include "diag/engine.hh"
#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "mc/fixup.hh"
#include "mc/layout.hh"
#include "mc/reg.hh"
#include "mc/streamer.hh"
#include "mc/symbol.hh"
#include "source/manager.hh"


namespace pl4g::target {

  // The target-specific constants the image writer needs.
  struct ImageDefaults {
    // The ELF machine number.
    std::uint16_t machine = 0;
    // Where a fixed-address executable is loaded; a position-independent one
    // uses nought.
    std::uint64_t base_vaddr = 0;
    std::uint64_t page_size = 0x1000;
    std::uint64_t text_alignment = 16;
    std::uint64_t function_alignment = 16;
    // What the ELF header's flag word says; only RISC-V has anything to say.
    std::uint32_t header_flags = 0;
  };


  // One code generator.
  class Target {
  public:
    virtual ~Target() = default;

    // The triple this back end generates for.
    virtual std::string_view triple() const = 0;
    // The width of a pointer.
    virtual unsigned pointer_bits() const = 0;
    // The target's register file.
    virtual const mc::RegisterInfo& registers() const = 0;
    // The symbol the image's entry point is.
    virtual std::string_view entry_symbol() const = 0;

    // Encode one instruction.
    virtual mc::Encoder encoder() const = 0;
    // How this target stores a fixup's value into the bytes that hold it.
    virtual mc::FixupApplier fixup_applier() const = 0;

    // An assembler that emits for this target into `streamer`.
    virtual std::unique_ptr<mc::Assembler> new_assembler(mc::MCStreamer& streamer, int opt_level) = 0;
    // Generate the whole image for `module` through `asm_`.  `sources` turns
    // a span into a place a person can find, for what a fault reports.
    virtual void generate(ir::Module& module, mc::Assembler& asm_, DiagEngine& diags, int opt_level,
                          const SourceManager* sources) = 0;

    // The layout constants the image writer needs.
    virtual ImageDefaults image_defaults() const = 0;
    // Grow any instruction whose displacement does not reach, once the code is
    // all there and before anything is laid out for good.
    virtual void relax(std::span<mc::MCSection* const> sections, std::span<mc::MCSymbol* const> symbols) = 0;

    // Whether this target can be built for more than one microarchitecture
    // level, and settling the one `--mclevel` named: what is wrong with a name
    // is thrown as `std::invalid_argument`, its text saying what, for the
    // driver to report.  A target with one level never asks.
    virtual bool has_mclevels() const { return false; }
    virtual void use_mclevel(std::string_view level) { (void) level; }

    // Whether the image is to be position-independent (`-fpie`): linked at
    // nought, loaded anywhere, and relocating itself before anything else runs
    // (target/relocate.hh).  Said before `generate`.
    void position_independent(bool on) noexcept { pie = on; }
    bool is_position_independent() const noexcept { return pie; }

  protected:
    bool pie = false;
  };


  // The back end for `triple`, or nothing where there is none.  Made once per
  // call: a target holds what one compilation settled about it.
  std::unique_ptr<Target> lookup_target(std::string_view triple);

} // namespace pl4g::target
