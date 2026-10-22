// The assembler builder API: what code generation calls, in order, to build a
// program -- there is no assembler text.  A port of pypl4g/mc/asmbuilder.py.
//
//     asm.begin_function("main");
//     asm.loadreg(eax, asm.imm(0));
//     asm.op(ops::plus, eax, { asm.reg(eax), asm.reg(ecx) });
//     asm.call("other");
//     asm.ret();
//     asm.end_function();
//
// A target plugs in as an `InstructionSelector` (a virtual interface: each
// builder call is one of its methods), an `Encoder` given to the streamer, and
// a `FixupApplier` given to the ELF writer.
#pragma once

#include <cstdint>
#include <functional>
#include <optional>
#include <set>
#include <span>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

#include "mc/machine.hh"
#include "mc/ops.hh"
#include "mc/regalloc.hh"
#include "mc/streamer.hh"


namespace pl4g::mc {

  // What a target must provide for the builder to work: every method turns
  // one builder call into that target's instructions.  A method a target does
  // not have refuses, as Python's missing attribute would.
  class InstructionSelector : public SpillSelector {
  public:
    // The register to set aside where a place in the frame turns out out of
    // an offset's reach, or nothing where every offset reaches: pypl4g's
    // `frame_spare`.
    virtual const PhysReg* frame_spare() const { return nullptr; }
    // The register set aside for the function being built, and setting it.
    virtual const PhysReg* held_spare() const { return nullptr; }
    virtual void hold_spare(const PhysReg* spare) { (void) spare; }
    // Save a register the convention keeps for the caller in the eight bytes
    // at `slot`, and put it back; and whether the whole of it is saved.  A
    // spill and a reload, and all of it, unless a back end says otherwise.
    virtual Insts select_keep(std::int64_t slot, Reg source, Span span) { return select_spill(slot, source, span); }
    virtual Insts select_unkeep(Reg destination, std::int64_t slot, Span span)
    {
      return select_reload(destination, slot, span);
    }
    virtual bool keeps_all_of(Reg reg) const
    {
      (void) reg;
      return true;
    }
    // Instructions that place `src` into `dst`.
    Insts select_move(Reg dst, const MCOperand& src, Span span) override;
    bool moves() const noexcept override { return true; }
    // Instructions that compute `op` over `sources` into `dst`.
    virtual Insts select_op(const Op* op, Reg dst, std::span<const MCOperand> sources, Span span);
    // Instructions that write `value` into the memory `address` names.
    virtual Insts select_store(const MCMem& address, const MCOperand& value, Span span);
    // Read `address` into `dst`, acquiring; write `value`, releasing.
    virtual Insts select_acquire(Reg dst, const MCMem& address, Span span);
    virtual Insts select_release(const MCMem& address, const MCOperand& value, Span span);
    virtual Insts select_call(const MCOperand& target, Span span);
    virtual Insts select_return(Span span);
    // Go to `target`: a symbol, or a register holding where to go.
    virtual Insts select_jump(const MCOperand& target, Span span);
    // Make room for `size` bytes on the stack, and give it back.
    virtual Insts select_frame(std::int64_t size, Span span);
    virtual Insts select_unframe(std::int64_t size, Span span);
    // Go to `target` when `lhs` and `rhs` stand in `cond`.
    virtual Insts select_branch(Condition cond, const MCOperand& lhs, const MCOperand& rhs, const MCSymRef& target,
                                Span span);
    // Put the address of `symbol` into `dst`.
    virtual Insts select_address(Reg dst, const MCSymRef& symbol, Span span);
    // Put a `bits`-wide value into the whole of `dst`.
    virtual Insts select_widen(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span);
    virtual Insts select_float_abs(Reg dst, const MCOperand& src, int bits, Span span);
    virtual Insts select_float_round(Rounding how, Reg dst, const MCOperand& src, int bits, Span span);
    virtual Insts select_float_to_int(Rounding how, Reg dst, const MCOperand& src, int bits, Span span);
    virtual Insts select_int_to_float(Reg dst, const MCOperand& src, int bits, Span span);
    // The unsigned 64-bit number `src` as the floating-point value of `bits`
    // nearest it, where the target has an instruction for it.
    virtual Insts select_unsigned_to_float(Reg dst, const MCOperand& src, int bits, Span span);
    // The floating-point value `src` of `bits` in the 64-bit `dst` as a whole
    // number, toward zero, read as signed or unsigned.
    virtual Insts select_truncate_to_int(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span);
    // The `bits` bits of `src` in `dst`, the one in an ordinary register and
    // the other in a floating-point one, with nothing converted.
    virtual Insts select_move_bits(Reg dst, const MCOperand& src, int bits, Span span);
    virtual Insts select_float_copysign(Reg dst, const MCOperand& magnitude, const MCOperand& sign, int bits,
                                        Span span);
    virtual Insts select_float_extend(Reg dst, const MCOperand& src, int from_bits, int to_bits, Span span);
    virtual Insts select_branch_if_finite(Reg value, int bits, const MCSymRef& target, Span span);
    // The widths whose arithmetic says for itself whether it went past.
    virtual std::set<int> flagged_widths() const { return {}; }
    virtual Insts select_op_at(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                               Span span);
    virtual Insts select_branch_if_in_range(const Op* op, bool is_signed, const MCSymRef& target, Span span);
    // Move `bits` of a run of elements into or out of a register.
    virtual Insts select_run_move(const std::variant<Reg, MCMem>& dst, const MCOperand& src, int bits, Span span);
    virtual Insts select_run_op(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                                Span span);
    virtual Insts select_run_any_lane(Reg dst, const MCOperand& src, int stride, int lanes, Span span);
    virtual Insts select_run_saturating(Reg dst, const MCOperand& left, const MCOperand& right, bool adding,
                                        bool is_signed, int bits, Span span);
    virtual Insts select_run_splat(Reg dst, const MCOperand& src, int bits, Span span);
    virtual Insts select_run_ones(Reg dst, Span span);
    // Compare every lane of two runs of `bits`-bit elements, every bit of a
    // lane of `dst` set where they stand in `cond` and every bit clear where not.
    virtual Insts select_run_compare(Condition cond, Reg dst, const MCOperand& left, const MCOperand& right,
                                     int bits, Span span);
    // One bit for each of the first `lanes` byte lanes of `src` in the ordinary
    // register `dst`, the first lane the lowest bit and every bit above clear.
    virtual Insts select_run_mask(Reg dst, const MCOperand& src, int lanes, Span span);
    // Every byte lane of `src` subtracted from nought, which takes a run of
    // truth values as memory holds them to one as a register does and back.
    virtual Insts select_run_truths(Reg dst, const MCOperand& src, Span span);
    virtual Insts select_float_op(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                                  Span span);
    virtual Insts select_float_compare(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, int bits,
                                       Span span);
    virtual Insts select_shift(const Op* op, Reg dst, const MCOperand& value, const MCOperand& amount, int bits,
                               Span span);
    virtual Insts select_divide(Reg dst, const MCOperand& left, const MCOperand& right, bool is_signed,
                                bool remainder, int bits, Span span);
    virtual Insts select_wide_product(Reg low, Reg high, const MCOperand& left, const MCOperand& right,
                                      bool is_signed, Span span);
    // How much room a function that calls sets aside for its return address.
    virtual std::int64_t link_slot_size();
    // How large a frame of `total` bytes has to be made, for the stack to be as
    // aligned as the system's convention says where it has to be; `calls` is
    // whether the function was called and calls in turn.
    virtual std::int64_t frame_size(std::int64_t total, bool calls);
    // Where a return address lies once the frame stands, and how far above
    // this function's stack pointer its caller's is.
    virtual std::pair<std::optional<std::int64_t>, std::int64_t> frame_walk(std::int64_t total, std::int64_t size,
                                                                            std::int64_t link);
    virtual Insts select_save_link(std::int64_t offset, Span span);
    virtual Insts select_restore_link(std::int64_t offset, Span span);
    // Read `bits` of an argument `offset` bytes above the stack pointer into
    // `dst`, widened by its sign where `is_signed` says.  Asked once the frame
    // stands, so the offset is the final one; a load whose offset the
    // instruction cannot carry builds the address in `dst` itself.
    virtual Insts select_incoming(Reg dst, std::int64_t offset, int bits, bool is_signed, Span span);
    // Put `bound` into `dst` where `lhs` and `rhs` stand in `cond`.
    virtual Insts select_clamp(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs,
                               const MCOperand& bound, Span span);
    // Put `if_true` into `dst` where `chooser` holds, `if_false` where not.
    virtual Insts select_select(Reg dst, const MCOperand& chooser, const MCOperand& if_true,
                                const MCOperand& if_false, bool floating, int bits, Span span);
    // Put one into `dst` when `lhs` and `rhs` stand in `cond`, zero otherwise.
    virtual Insts select_set(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, Span span);
    // Whether the target counts the bits set, the leading zeroes and the
    // trailing ones.
    virtual bool counts_ones() const { return false; }
    virtual bool counts_leading() const { return false; }
    virtual bool counts_trailing() const { return false; }
    // Whether the target converts an unsigned 64-bit number to and from a
    // floating-point one in one instruction.
    virtual bool converts_unsigned() const { return false; }
    virtual Insts select_count_ones(Reg dst, const MCOperand& src, Span span);
    virtual Insts select_count_leading(Reg dst, const MCOperand& src, Span span);
    virtual Insts select_count_trailing(Reg dst, const MCOperand& src, Span span);
  };


  // A rewrite applied to a function before it is streamed.
  class MachinePass {
  public:
    virtual ~MachinePass() = default;
    virtual std::string_view name() const = 0;
    // Rewrite `function`, answering whether anything changed.
    virtual bool run(MachineFunction& function) = 0;
  };


  // A function still mentions registers the allocator has not assigned.
  // A place in the frame lies further from the stack pointer than an
  // instruction can say, and no register is set aside to build its address
  // in: raised by a selector once the frame stands, and caught by the builder,
  // which sets one aside and allocates the function again.
  struct FrameOutOfReach : std::runtime_error {
    using std::runtime_error::runtime_error;
  };


  struct RegisterAssignmentError : std::runtime_error {
    std::string function_name;
    std::size_t count;
    RegisterAssignmentError(std::string name, std::size_t count);
  };


  // What an `Assembler` is made with beside its selector and streamer.
  struct AssemblerOptions {
    std::uint64_t function_alignment = 16;
    std::uint8_t pad_byte = 0xCC;
    std::vector<MachinePass*> machine_passes;
    RegisterInfo* registers = nullptr;
    AllocationOrder allocation_order;
    std::set<const RegUnit*> callee_saved;
  };


  // Builds the symbolic representation of one image.
  class Assembler {
  public:
    Assembler(InstructionSelector& selector, MCStreamer& streamer, AssemblerOptions options = {});

    MCStreamer& streamer() const noexcept { return streamer_; }
    InstructionSelector& selector() const noexcept { return selector_; }

    // -- placement ----------------------------------------------------------

    // Select the section subsequent emission goes into.
    MCSection* section(const std::string& name, const SectionOptions& options = {});
    // Pad to the next multiple of `alignment`.
    void align(std::uint64_t alignment);
    // Emit literal bytes, with what has to be filled in once addresses are known.
    void bytes(Bytes data, std::vector<MCFixup> fixups = {});
    // The symbol `name`, whether or not anything has defined it yet.
    MCSymbol* symbol_named(const std::string& name);
    // Define a symbol at the current position of the current section.
    MCSymbol* label(const std::string& name, SymBinding binding = SymBinding::local, SymKind kind = SymKind::notype,
                    SymVisibility visibility = SymVisibility::default_);
    // Record how far the definition of `symbol` extends.
    void end_label(MCSymbol* symbol);

    // -- functions ----------------------------------------------------------

    // Start building the function `name`, with its own allocation order and
    // callee-saved registers where it follows another convention.
    MachineFunction* begin_function(const std::string& name, bool exported = false, std::int64_t padding = 0,
                                    std::optional<AllocationOrder> allocation_order = std::nullopt,
                                    std::optional<std::set<const RegUnit*>> callee_saved = std::nullopt);
    // A label no other block in this function has.
    std::string reserve_label(std::string_view hint);
    // Start a new block within the current function.
    MachineBasicBlock* block(std::optional<std::string> label = std::nullopt);
    // Finish the current function and stream it into the image.
    MCSymbol* end_function();
    MachineFunction* current_function() const noexcept { return function_; }
    MachineBasicBlock* current_block() const noexcept { return block_; }

    // -- operands -----------------------------------------------------------

    MCReg reg(Reg r) const { return MCReg{ r }; }
    MCImm imm(__int128 value, int bits = 32, bool is_signed = true) const { return MCImm{ value, bits, is_signed }; }
    MCSymRef symref(const std::string& name, RelocKind kind = RelocKind::pcrel, std::int64_t addend = 0);

    // -- the operations -----------------------------------------------------

    void loadreg(Reg dst, const MCOperand& src, Span span = invalid_span);
    void loadreg(Reg dst, __int128 src, Span span = invalid_span);
    void store(const MCMem& address, const MCOperand& value, Span span = invalid_span);
    void store(const MCMem& address, __int128 value, Span span = invalid_span);
    void acquire(Reg dst, const MCMem& address, Span span = invalid_span);
    void release(const MCMem& address, const MCOperand& value, Span span = invalid_span);
    void release(const MCMem& address, __int128 value, Span span = invalid_span);
    void op(const Op* op, Reg dst, std::vector<MCOperand> sources, Span span = invalid_span);
    void call(const MCOperand& target, Span span = invalid_span, std::vector<Reg> clobbers = {},
              std::vector<Reg> reads = {});
    void call(const std::string& target, Span span = invalid_span, std::vector<Reg> clobbers = {},
              std::vector<Reg> reads = {});
    // Enter the kernel with what `enter` emits, saying what it destroys and reads.
    void kernel(const std::function<void(Assembler&)>& enter, Span span = invalid_span,
                std::vector<Reg> clobbers = {}, std::vector<Reg> reads = {});
    void jump(const std::string& target, Span span = invalid_span);
    void branch(Condition cond, const MCOperand& lhs, const MCOperand& rhs, const std::string& target,
                Span span = invalid_span);
    void setcond(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, Span span = invalid_span);
    void address(Reg dst, const std::string& name, Span span = invalid_span);
    void widen(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span = invalid_span);
    void float_abs(Reg dst, const MCOperand& src, int bits, Span span = invalid_span);
    void float_round(Rounding how, Reg dst, const MCOperand& src, int bits, Span span = invalid_span);
    void float_to_int(Rounding how, Reg dst, const MCOperand& src, int bits, Span span = invalid_span);
    void int_to_float(Reg dst, const MCOperand& src, int bits, Span span = invalid_span);
    void unsigned_to_float(Reg dst, const MCOperand& src, int bits, Span span = invalid_span);
    void truncate_to_int(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span = invalid_span);
    void move_bits(Reg dst, const MCOperand& src, int bits, Span span = invalid_span);
    void float_copysign(Reg dst, const MCOperand& magnitude, const MCOperand& sign, int bits,
                        Span span = invalid_span);
    void float_extend(Reg dst, const MCOperand& src, int from_bits, int to_bits, Span span = invalid_span);
    void branch_if_finite(Reg value, int bits, const std::string& target, Span span = invalid_span);
    bool counts_ones() const { return selector_.counts_ones(); }
    bool counts_leading() const { return selector_.counts_leading(); }
    bool counts_trailing() const { return selector_.counts_trailing(); }
    bool converts_unsigned() const { return selector_.converts_unsigned(); }
    void count_ones(Reg dst, const MCOperand& src, Span span = invalid_span);
    void count_leading(Reg dst, const MCOperand& src, Span span = invalid_span);
    void count_trailing(Reg dst, const MCOperand& src, Span span = invalid_span);
    std::set<int> flagged_widths() const { return selector_.flagged_widths(); }
    void op_at(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
               Span span = invalid_span);
    void branch_if_in_range(const Op* op, bool is_signed, const std::string& target, Span span = invalid_span);
    void run_move(const std::variant<Reg, MCMem>& dst, const MCOperand& src, int bits, Span span = invalid_span);
    void run_store(const MCMem& place, const MCOperand& src, int bits, Span span = invalid_span);
    void run_op(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits = 0,
                Span span = invalid_span);
    void run_any_lane(Reg dst, const MCOperand& src, int stride, int lanes, Span span = invalid_span);
    void run_saturating(Reg dst, const MCOperand& left, const MCOperand& right, bool adding, bool is_signed,
                        int bits, Span span = invalid_span);
    void run_splat(Reg dst, const MCOperand& src, int bits, Span span = invalid_span);
    void run_ones(Reg dst, Span span = invalid_span);
    void run_compare(Condition cond, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                     Span span = invalid_span);
    void run_mask(Reg dst, const MCOperand& src, int lanes, Span span = invalid_span);
    void run_truths(Reg dst, const MCOperand& src, Span span = invalid_span);
    // Append instructions a backend's own selector chose, for what one
    // architecture does in a way no name the three share describes.
    void emit(const Insts& insts);
    void float_op(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                  Span span = invalid_span);
    void float_compare(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, int bits,
                       Span span = invalid_span);
    void shift(const Op* op, Reg dst, const MCOperand& value, const MCOperand& amount, int bits,
               Span span = invalid_span);
    void divide(Reg dst, const MCOperand& left, const MCOperand& right, bool is_signed, bool remainder, int bits,
                Span span = invalid_span);
    void wide_product(Reg low, Reg high, const MCOperand& left, const MCOperand& right, bool is_signed,
                      Span span = invalid_span);
    void select(Reg dst, const MCOperand& chooser, const MCOperand& if_true, const MCOperand& if_false,
                bool floating, int bits, Span span = invalid_span);
    void clamp(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, const MCOperand& bound,
               Span span = invalid_span);
    // Record an edge control takes by going on to the next block.
    void falls_through(const std::string& target);
    // Take `size` bytes of this function's frame and answer where they are.
    std::int64_t frame_slot(std::int64_t size, std::int64_t alignment);
    // A virtual register of the kind and width of `like`.
    Reg temporary(Reg like);
    // Go to another function, which returns in this one's place: a symbol, or
    // a register holding where to go.  `reads` is the registers the other
    // function's arguments are in, which keeps them wanted up to here, as a
    // call's are.
    void tail_jump(const std::string& target, Span span = invalid_span, std::vector<Reg> reads = {});
    void tail_jump(const MCOperand& target, Span span = invalid_span, std::vector<Reg> reads = {});
    void frame(std::int64_t size, Span span = invalid_span);
    void unframe(std::int64_t size, Span span = invalid_span);
    void put_aside(std::int64_t slot, Reg source, Span span = invalid_span);
    void take_back(Reg destination, std::int64_t slot, Span span = invalid_span);
    // Read into `dst` the argument the caller left `offset` bytes above its
    // stack pointer, `bits` wide and widened by its sign where `is_signed`
    // says: a stand-in (`incoming_desc`) the frame turns into a load once it
    // knows how far above this function's stack pointer the caller's is.
    void incoming(Reg dst, std::int64_t offset, int bits, bool is_signed, Span span = invalid_span);
    // Return from the current function; `reads` is where the answer was put.
    void ret(Span span = invalid_span, std::vector<Reg> reads = {});

    // What the allocator decided, for each function in the order built.
    std::vector<Assignment> assignments;
    std::vector<MachineFunction*> functions;

  private:
    MCSymRef block_ref(const std::string& label);
    static bool is_branch_target(const MachineFunction& function, const MachineBasicBlock& block);
    void assign_registers(MachineFunction& function);
    // Allocate and make the frame -- again, with the selector's spare set
    // aside, where the frame turned out out of an offset's reach.
    void allocate_and_frame(MachineFunction& function);
    void allocate_once(MachineFunction& function);
    void make_frame(MachineFunction& function);
    void place_incoming(MachineFunction& function, std::int64_t above);
    std::vector<Reg> kept_registers(const MachineFunction& function);

    InstructionSelector& selector_;
    MCStreamer& streamer_;
    std::uint64_t alignment;
    std::uint8_t pad_byte;
    std::vector<MachinePass*> machine_passes;
    RegisterInfo* registers;
    AllocationOrder default_order;
    AllocationOrder order;
    std::set<const RegUnit*> default_kept;
    std::set<const RegUnit*> kept;
    std::vector<std::unique_ptr<MachineFunction>> owned;
    MachineFunction* function_ = nullptr;
    MachineBasicBlock* block_ = nullptr;
    int reserved = 0;
  };

} // namespace pl4g::mc
