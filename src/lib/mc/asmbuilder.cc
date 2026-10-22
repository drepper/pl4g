// The assembler builder API.
#include "mc/asmbuilder.hh"

#include <algorithm>
#include <tuple>
#include <unordered_map>


namespace pl4g::mc {

  namespace {

    [[noreturn]] void missing(std::string_view what)
    {
      throw std::logic_error("this target has no " + std::string(what));
    }

    // What stands for the read of an argument the caller left on the stack
    // until the frame is made: it writes its first operand and reads nothing,
    // and the three immediates after it are the slot's offset above the
    // caller's stack pointer, how many bits the value is, and whether it is
    // widened by its sign.  Nothing that encodes ever sees one.
    const InstDesc incoming_row = [] {
      InstDesc made;
      made.mnemonic = "incoming";
      made.operands = { OperandSpec{ OperandKind::reg }, OperandSpec{ OperandKind::imm },
                        OperandSpec{ OperandKind::imm }, OperandSpec{ OperandKind::imm } };
      return made;
    }();

  } // anonymous namespace


  // -- what a selector does not have ------------------------------------------

  Insts InstructionSelector::select_move(Reg, const MCOperand&, Span) { missing("select_move"); }
  Insts InstructionSelector::select_op(const Op*, Reg, std::span<const MCOperand>, Span) { missing("select_op"); }
  Insts InstructionSelector::select_store(const MCMem&, const MCOperand&, Span) { missing("select_store"); }
  Insts InstructionSelector::select_acquire(Reg, const MCMem&, Span) { missing("select_acquire"); }
  Insts InstructionSelector::select_release(const MCMem&, const MCOperand&, Span) { missing("select_release"); }
  Insts InstructionSelector::select_call(const MCOperand&, Span) { missing("select_call"); }
  Insts InstructionSelector::select_return(Span) { missing("select_return"); }
  Insts InstructionSelector::select_jump(const MCOperand&, Span) { missing("select_jump"); }
  Insts InstructionSelector::select_frame(std::int64_t, Span) { missing("select_frame"); }
  Insts InstructionSelector::select_unframe(std::int64_t, Span) { missing("select_unframe"); }
  Insts InstructionSelector::select_branch(Condition, const MCOperand&, const MCOperand&, const MCSymRef&, Span)
  {
    missing("select_branch");
  }
  Insts InstructionSelector::select_address(Reg, const MCSymRef&, Span) { missing("select_address"); }
  Insts InstructionSelector::select_widen(Reg, const MCOperand&, int, bool, Span) { missing("select_widen"); }
  Insts InstructionSelector::select_float_abs(Reg, const MCOperand&, int, Span) { missing("select_float_abs"); }
  Insts InstructionSelector::select_float_round(Rounding, Reg, const MCOperand&, int, Span)
  {
    missing("select_float_round");
  }
  Insts InstructionSelector::select_float_to_int(Rounding, Reg, const MCOperand&, int, Span)
  {
    missing("select_float_to_int");
  }
  Insts InstructionSelector::select_int_to_float(Reg, const MCOperand&, int, Span) { missing("select_int_to_float"); }
  Insts InstructionSelector::select_unsigned_to_float(Reg, const MCOperand&, int, Span)
  {
    missing("select_unsigned_to_float");
  }
  Insts InstructionSelector::select_truncate_to_int(Reg, const MCOperand&, int, bool, Span)
  {
    missing("select_truncate_to_int");
  }
  Insts InstructionSelector::select_move_bits(Reg, const MCOperand&, int, Span) { missing("select_move_bits"); }
  Insts InstructionSelector::select_float_copysign(Reg, const MCOperand&, const MCOperand&, int, Span)
  {
    missing("select_float_copysign");
  }
  Insts InstructionSelector::select_float_extend(Reg, const MCOperand&, int, int, Span)
  {
    missing("select_float_extend");
  }
  Insts InstructionSelector::select_branch_if_finite(Reg, int, const MCSymRef&, Span)
  {
    missing("select_branch_if_finite");
  }
  Insts InstructionSelector::select_op_at(const Op*, Reg, const MCOperand&, const MCOperand&, int, Span)
  {
    missing("select_op_at");
  }
  Insts InstructionSelector::select_branch_if_in_range(const Op*, bool, const MCSymRef&, Span)
  {
    missing("select_branch_if_in_range");
  }
  Insts InstructionSelector::select_run_move(const std::variant<Reg, MCMem>&, const MCOperand&, int, Span)
  {
    missing("select_run_move");
  }
  Insts InstructionSelector::select_run_op(const Op*, Reg, const MCOperand&, const MCOperand&, int, Span)
  {
    missing("select_run_op");
  }
  Insts InstructionSelector::select_run_any_lane(Reg, const MCOperand&, int, int, Span)
  {
    missing("select_run_any_lane");
  }
  Insts InstructionSelector::select_run_saturating(Reg, const MCOperand&, const MCOperand&, bool, bool, int, Span)
  {
    missing("select_run_saturating");
  }
  Insts InstructionSelector::select_run_splat(Reg, const MCOperand&, int, Span) { missing("select_run_splat"); }
  Insts InstructionSelector::select_run_ones(Reg, Span) { missing("select_run_ones"); }
  Insts InstructionSelector::select_run_compare(Condition, Reg, const MCOperand&, const MCOperand&, int, Span)
  {
    missing("select_run_compare");
  }
  Insts InstructionSelector::select_run_mask(Reg, const MCOperand&, int, Span) { missing("select_run_mask"); }
  Insts InstructionSelector::select_run_truths(Reg, const MCOperand&, Span) { missing("select_run_truths"); }
  Insts InstructionSelector::select_float_op(const Op*, Reg, const MCOperand&, const MCOperand&, int, Span)
  {
    missing("select_float_op");
  }
  Insts InstructionSelector::select_float_compare(Condition, Reg, const MCOperand&, const MCOperand&, int, Span)
  {
    missing("select_float_compare");
  }
  Insts InstructionSelector::select_shift(const Op*, Reg, const MCOperand&, const MCOperand&, int, Span)
  {
    missing("select_shift");
  }
  Insts InstructionSelector::select_divide(Reg, const MCOperand&, const MCOperand&, bool, bool, int, Span)
  {
    missing("select_divide");
  }
  Insts InstructionSelector::select_wide_product(Reg, Reg, const MCOperand&, const MCOperand&, bool, Span)
  {
    missing("select_wide_product");
  }
  std::int64_t InstructionSelector::link_slot_size() { missing("link_slot_size"); }
  std::int64_t InstructionSelector::frame_size(std::int64_t, bool) { missing("frame_size"); }
  std::pair<std::optional<std::int64_t>, std::int64_t> InstructionSelector::frame_walk(std::int64_t, std::int64_t,
                                                                                      std::int64_t)
  {
    missing("frame_walk");
  }
  Insts InstructionSelector::select_save_link(std::int64_t, Span) { missing("select_save_link"); }
  Insts InstructionSelector::select_restore_link(std::int64_t, Span) { missing("select_restore_link"); }
  Insts InstructionSelector::select_incoming(Reg, std::int64_t, int, bool, Span) { missing("select_incoming"); }
  Insts InstructionSelector::select_clamp(Condition, Reg, const MCOperand&, const MCOperand&, const MCOperand&, Span)
  {
    missing("select_clamp");
  }
  Insts InstructionSelector::select_select(Reg, const MCOperand&, const MCOperand&, const MCOperand&, bool, int, Span)
  {
    missing("select_select");
  }
  Insts InstructionSelector::select_set(Condition, Reg, const MCOperand&, const MCOperand&, Span)
  {
    missing("select_set");
  }
  Insts InstructionSelector::select_count_ones(Reg, const MCOperand&, Span) { missing("select_count_ones"); }
  Insts InstructionSelector::select_count_leading(Reg, const MCOperand&, Span) { missing("select_count_leading"); }
  Insts InstructionSelector::select_count_trailing(Reg, const MCOperand&, Span) { missing("select_count_trailing"); }


  RegisterAssignmentError::RegisterAssignmentError(std::string name, std::size_t count)
    : std::runtime_error("function '" + name + "' still has " + std::to_string(count)
                         + " unassigned registers after allocation"),
      function_name(std::move(name)),
      count(count)
  {
  }


  // -- the builder ------------------------------------------------------------

  Assembler::Assembler(InstructionSelector& selector, MCStreamer& streamer, AssemblerOptions options)
    : selector_(selector),
      streamer_(streamer),
      alignment(options.function_alignment),
      pad_byte(options.pad_byte),
      machine_passes(std::move(options.machine_passes)),
      registers(options.registers),
      default_order(std::move(options.allocation_order)),
      order(default_order),
      default_kept(std::move(options.callee_saved)),
      kept(default_kept)
  {
  }


  MCSection* Assembler::section(const std::string& name, const SectionOptions& options)
  {
    auto made = streamer_.get_section(name, options);
    streamer_.switch_section(made);
    return made;
  }


  void Assembler::align(std::uint64_t to)
  {
    streamer_.emit_align(to, pad_byte);
  }


  void Assembler::bytes(Bytes data, std::vector<MCFixup> fixups)
  {
    streamer_.emit_bytes(std::move(data), std::move(fixups));
  }


  MCSymbol* Assembler::symbol_named(const std::string& name)
  {
    return streamer_.symbol(name);
  }


  MCSymbol* Assembler::label(const std::string& name, SymBinding binding, SymKind kind, SymVisibility visibility)
  {
    return streamer_.define_symbol(name, binding, kind, false, visibility);
  }


  void Assembler::end_label(MCSymbol* symbol)
  {
    streamer_.set_symbol_size(symbol);
  }


  MachineFunction* Assembler::begin_function(const std::string& name, bool exported, std::int64_t padding,
                                             std::optional<AllocationOrder> allocation_order,
                                             std::optional<std::set<const RegUnit*>> callee_saved)
  {
    auto made = std::make_unique<MachineFunction>(name);
    made->exported = exported;
    made->padding = padding;
    function_ = made.get();
    owned.push_back(std::move(made));
    order = allocation_order ? std::move(*allocation_order) : default_order;
    kept = callee_saved ? std::move(*callee_saved) : default_kept;
    block_ = function_->add_block(".L" + name + "_entry");
    functions.push_back(function_);
    return function_;
  }


  std::string Assembler::reserve_label(std::string_view hint)
  {
    ++reserved;
    return ".L" + function_->name + "." + std::string(hint) + "." + std::to_string(reserved);
  }


  MachineBasicBlock* Assembler::block(std::optional<std::string> label)
  {
    block_ = function_->add_block(std::move(label));
    return block_;
  }


  MCSymbol* Assembler::end_function()
  {
    auto& function = *function_;
    for (auto pass : machine_passes)
      pass->run(function);
    if (auto wrong = function.edges_are_complete()) [[unlikely]]
      throw MalformedGraph(*wrong);
    allocate_and_frame(function);
    streamer_.emit_align(alignment, pad_byte);
    auto binding = function.exported ? SymBinding::global : SymBinding::local;
    auto visibility = function.exported ? SymVisibility::default_ : SymVisibility::hidden;
    auto symbol = streamer_.define_symbol(function.name, binding, SymKind::func, false, visibility);
    for (const auto& block : function.blocks) {
      if (streamer_.symbols.contains(block->label) || is_branch_target(function, *block))
        streamer_.define_symbol(block->label, SymBinding::local, SymKind::notype, true);
      streamer_.emit_insts(block->insts);
    }
    streamer_.set_symbol_size(symbol);
    if (function.padding > 0)
      streamer_.emit_padding(std::uint64_t(function.padding), pad_byte);
    function_ = nullptr;
    block_ = nullptr;
    return symbol;
  }


  bool Assembler::is_branch_target(const MachineFunction& function, const MachineBasicBlock& block)
  {
    for (const auto& other : function.blocks)
      if (std::ranges::find(other->successors, block.label) != other->successors.end())
        return true;
    return false;
  }


  namespace {

    // An allocation order with `unit` left out of it.
    AllocationOrder without(const AllocationOrder& order, const RegUnit* unit)
    {
      AllocationOrder made = order;
      for (auto& [_, units] : made.classes)
        std::erase(units, unit);
      return made;
    }

  } // anonymous namespace


  void Assembler::allocate_and_frame(MachineFunction& function)
  {
    // The first attempt keeps every register; one set aside is wanted only
    // where the frame grew too large to reach, which the allocator's slots
    // are what decides.
    std::vector<Insts> blocks;
    for (const auto& block : function.blocks)
      blocks.push_back(block->insts);
    auto frame = function.frame;
    auto kept_order = order;
    auto made = assignments.size();
    try {
      allocate_once(function);
      return;
    } catch (const FrameOutOfReach&) {
      if (selector_.frame_spare() == nullptr || selector_.held_spare() != nullptr)
        throw;
    }
    auto spare = selector_.frame_spare();
    for (std::size_t at = 0; at < function.blocks.size() && at < blocks.size(); ++at)
      function.blocks[at]->insts = std::move(blocks[at]);
    function.frame = frame;
    assignments.resize(made);
    order = without(kept_order, spare->unit);
    selector_.hold_spare(spare);
    try {
      allocate_once(function);
    } catch (...) {
      selector_.hold_spare(nullptr);
      order = std::move(kept_order);
      throw;
    }
    selector_.hold_spare(nullptr);
    order = std::move(kept_order);
  }


  void Assembler::allocate_once(MachineFunction& function)
  {
    assign_registers(function);
    // And again, now that every value has a register.
    for (auto pass : machine_passes)
      pass->run(function);
    make_frame(function);
  }


  void Assembler::assign_registers(MachineFunction& function)
  {
    if (! function.virtual_registers().empty()) {
      if (registers == nullptr || order.empty()) [[unlikely]]
        throw RegisterAssignmentError(function.name, function.virtual_registers().size());
      assignments.push_back(allocate(function, *registers, order, selector_));
    }
    if (auto pending = function.virtual_registers(); ! pending.empty()) [[unlikely]]
      throw RegisterAssignmentError(function.name, pending.size());
  }


  void Assembler::make_frame(MachineFunction& function)
  {
    auto size = function.frame.size();
    bool calls = false;
    bool returns = false;
    for (const auto& block : function.blocks)
      for (const auto& inst : block->insts) {
        calls = calls || has(inst->desc->flags, InstFlags::call);
        returns = returns || has(inst->desc->flags, InstFlags::return_);
      }
    std::int64_t link = calls && returns ? selector_.link_slot_size() : 0;
    auto keep = returns ? kept_registers(function) : std::vector<Reg>{};
    // And rounded, so that the stack is aligned at every call as the system's
    // convention has it: what is called may be C, and C keeps what it puts on
    // the stack aligned by assuming it found it so.  A function that does not
    // return was not called, or not as one that comes back -- the entry point
    // is begun by the kernel -- and says how its stack stands for itself.
    std::int64_t total = selector_.frame_size(size + link + 8 * std::int64_t(keep.size()), calls && returns);
    if (calls)
      std::tie(function.return_at, function.caller_at) = selector_.frame_walk(total, size, link);
    else {
      function.return_at = std::nullopt;
      function.caller_at = 0;
    }
    // The arguments the caller left on the stack are above this frame, and
    // above where the caller's stack pointer is, which is what the walk asks
    // as well -- so the same answer says where they are.
    place_incoming(function, selector_.frame_walk(total, size, link).second);
    if (total == 0)
      return;
    if (is_branch_target(function, *function.blocks[0])) [[unlikely]]
      throw MalformedGraph(function.name + ": control comes back to the block the frame is made in");
    auto opening = selector_.select_frame(total, invalid_span);
    if (link != 0) {
      auto saved = selector_.select_save_link(size, invalid_span);
      opening.insert(opening.end(), saved.begin(), saved.end());
    }
    for (std::size_t index = 0; index < keep.size(); ++index) {
      // Saved as the convention keeps it, which for a floating-point register
      // may be less than all of it: eight bytes a register.
      auto spilled = selector_.select_keep(size + link + 8 * std::int64_t(index), keep[index], invalid_span);
      opening.insert(opening.end(), spilled.begin(), spilled.end());
    }
    // What a caller this compilation knows may count on is what was saved
    // whole: a run of lanes in a register saved by half would lose the rest.
    function.preserved.clear();
    for (auto reg : keep)
      if (reg.is_physical() && selector_.keeps_all_of(reg))
        function.preserved.insert(reg.phys()->unit);
    auto& entry = *function.blocks[0];
    opening.insert(opening.end(), entry.insts.begin(), entry.insts.end());
    entry.insts = std::move(opening);
    for (auto& block : function.blocks) {
      Insts out;
      for (const auto& inst : block->insts) {
        if (has(inst->desc->flags, InstFlags::return_)) {
          for (std::size_t index = 0; index < keep.size(); ++index) {
            auto reloaded = selector_.select_unkeep(keep[index], size + link + 8 * std::int64_t(index), inst->span);
            out.insert(out.end(), reloaded.begin(), reloaded.end());
          }
          if (link != 0) {
            auto restored = selector_.select_restore_link(size, inst->span);
            out.insert(out.end(), restored.begin(), restored.end());
          }
          auto undone = selector_.select_unframe(total, inst->span);
          out.insert(out.end(), undone.begin(), undone.end());
        }
        out.push_back(inst);
      }
      block->insts = std::move(out);
    }
  }


  void Assembler::place_incoming(MachineFunction& function, std::int64_t above)
  {
    for (auto& block : function.blocks) {
      if (std::ranges::none_of(block->insts, [](const InstRef& inst) { return inst->desc == &incoming_row; }))
        continue;
      Insts out;
      for (const auto& inst : block->insts) {
        if (inst->desc != &incoming_row) {
          out.push_back(inst);
          continue;
        }
        auto into = std::get<MCReg>(inst->operands[0]).reg;
        auto offset = std::int64_t(std::get<MCImm>(inst->operands[1]).value);
        auto bits = int(std::get<MCImm>(inst->operands[2]).value);
        bool is_signed = std::get<MCImm>(inst->operands[3]).value != 0;
        auto loaded = selector_.select_incoming(into, above + offset, bits, is_signed, inst->span);
        out.insert(out.end(), loaded.begin(), loaded.end());
      }
      block->insts = std::move(out);
    }
  }


  std::vector<Reg> Assembler::kept_registers(const MachineFunction& function)
  {
    if (kept.empty() || registers == nullptr)
      return {};
    std::set<const RegUnit*> used;
    for (const auto& block : function.blocks)
      for (const auto& inst : block->insts) {
        for (const auto& operand : inst->operands)
          for (auto [reg, _] : registers_of(operand))
            if (reg.is_physical())
              used.insert(reg.phys()->unit);
        for (auto reg : inst->desc->implicit_defs)
          used.insert(reg->unit);
        for (auto reg : inst->desc->implicit_uses)
          used.insert(reg->unit);
      }
    // In the order the convention prefers them; a unit named twice counts
    // where it is named last, as a dict made from the list counts it.
    auto preferred = order.preferred();
    std::unordered_map<const RegUnit*, std::size_t> rank;
    for (std::size_t index = 0; index < preferred.size(); ++index)
      rank[preferred[index]] = index;
    std::vector<const RegUnit*> wanted;
    for (auto unit : used)
      if (kept.contains(unit))
        wanted.push_back(unit);
    auto rank_of = [&](const RegUnit* unit) {
      auto it = rank.find(unit);
      return it == rank.end() ? rank.size() : it->second;
    };
    std::ranges::stable_sort(wanted, [&](auto a, auto b) { return rank_of(a) < rank_of(b); });
    std::vector<Reg> found;
    for (auto unit : wanted)
      found.emplace_back(registers->view(unit, 64));
    return found;
  }


  MCSymRef Assembler::symref(const std::string& name, RelocKind kind, std::int64_t addend)
  {
    return MCSymRef{ sym_expr(streamer_.symbol(name)), kind, addend };
  }


  void Assembler::emit(const Insts& insts)
  {
    for (const auto& inst : insts)
      block_->append(inst);
  }


  void Assembler::loadreg(Reg dst, const MCOperand& src, Span span) { emit(selector_.select_move(dst, src, span)); }
  void Assembler::loadreg(Reg dst, __int128 src, Span span) { loadreg(dst, imm(src), span); }

  void Assembler::store(const MCMem& address, const MCOperand& value, Span span)
  {
    emit(selector_.select_store(address, value, span));
  }
  void Assembler::store(const MCMem& address, __int128 value, Span span) { store(address, imm(value), span); }

  void Assembler::acquire(Reg dst, const MCMem& address, Span span)
  {
    emit(selector_.select_acquire(dst, address, span));
  }

  void Assembler::release(const MCMem& address, const MCOperand& value, Span span)
  {
    emit(selector_.select_release(address, value, span));
  }
  void Assembler::release(const MCMem& address, __int128 value, Span span) { release(address, imm(value), span); }

  void Assembler::op(const Op* op, Reg dst, std::vector<MCOperand> sources, Span span)
  {
    emit(selector_.select_op(op, dst, sources, span));
  }


  void Assembler::call(const MCOperand& target, Span span, std::vector<Reg> clobbers, std::vector<Reg> reads)
  {
    auto selected = selector_.select_call(target, span);
    for (const auto& inst : selected)
      if (has(inst->desc->flags, InstFlags::call)) {
        inst->clobbers = clobbers;
        inst->reads = reads;
      }
    emit(selected);
  }


  void Assembler::call(const std::string& target, Span span, std::vector<Reg> clobbers, std::vector<Reg> reads)
  {
    call(MCOperand(symref(target)), span, std::move(clobbers), std::move(reads));
  }


  void Assembler::kernel(const std::function<void(Assembler&)>& enter, Span, std::vector<Reg> clobbers,
                         std::vector<Reg> reads)
  {
    auto before = block_->insts.size();
    enter(*this);
    for (auto i = before; i < block_->insts.size(); ++i) {
      block_->insts[i]->clobbers = clobbers;
      block_->insts[i]->reads = reads;
    }
  }


  void Assembler::jump(const std::string& target, Span span)
  {
    block_->successors.push_back(target);
    emit(selector_.select_jump(block_ref(target), span));
  }


  void Assembler::branch(Condition cond, const MCOperand& lhs, const MCOperand& rhs, const std::string& target,
                         Span span)
  {
    block_->successors.push_back(target);
    emit(selector_.select_branch(cond, lhs, rhs, block_ref(target), span));
  }


  void Assembler::setcond(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, Span span)
  {
    emit(selector_.select_set(cond, dst, lhs, rhs, span));
  }


  void Assembler::address(Reg dst, const std::string& name, Span span)
  {
    emit(selector_.select_address(dst, symref(name), span));
  }


  void Assembler::widen(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    emit(selector_.select_widen(dst, src, bits, is_signed, span));
  }


  void Assembler::float_abs(Reg dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_float_abs(dst, src, bits, span));
  }


  void Assembler::float_round(Rounding how, Reg dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_float_round(how, dst, src, bits, span));
  }


  void Assembler::float_to_int(Rounding how, Reg dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_float_to_int(how, dst, src, bits, span));
  }


  void Assembler::int_to_float(Reg dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_int_to_float(dst, src, bits, span));
  }


  void Assembler::unsigned_to_float(Reg dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_unsigned_to_float(dst, src, bits, span));
  }


  void Assembler::truncate_to_int(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    emit(selector_.select_truncate_to_int(dst, src, bits, is_signed, span));
  }


  void Assembler::move_bits(Reg dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_move_bits(dst, src, bits, span));
  }


  void Assembler::float_copysign(Reg dst, const MCOperand& magnitude, const MCOperand& sign, int bits, Span span)
  {
    emit(selector_.select_float_copysign(dst, magnitude, sign, bits, span));
  }


  void Assembler::float_extend(Reg dst, const MCOperand& src, int from_bits, int to_bits, Span span)
  {
    emit(selector_.select_float_extend(dst, src, from_bits, to_bits, span));
  }


  void Assembler::branch_if_finite(Reg value, int bits, const std::string& target, Span span)
  {
    block_->successors.push_back(target);
    emit(selector_.select_branch_if_finite(value, bits, block_ref(target), span));
  }


  void Assembler::count_ones(Reg dst, const MCOperand& src, Span span)
  {
    emit(selector_.select_count_ones(dst, src, span));
  }


  void Assembler::count_leading(Reg dst, const MCOperand& src, Span span)
  {
    emit(selector_.select_count_leading(dst, src, span));
  }


  void Assembler::count_trailing(Reg dst, const MCOperand& src, Span span)
  {
    emit(selector_.select_count_trailing(dst, src, span));
  }


  void Assembler::op_at(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits, Span span)
  {
    emit(selector_.select_op_at(op, dst, left, right, bits, span));
  }


  void Assembler::branch_if_in_range(const Op* op, bool is_signed, const std::string& target, Span span)
  {
    block_->successors.push_back(target);
    emit(selector_.select_branch_if_in_range(op, is_signed, block_ref(target), span));
  }


  void Assembler::run_move(const std::variant<Reg, MCMem>& dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_run_move(dst, src, bits, span));
  }


  void Assembler::run_store(const MCMem& place, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_run_move(place, src, bits, span));
  }


  void Assembler::run_op(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits, Span span)
  {
    emit(selector_.select_run_op(op, dst, left, right, bits, span));
  }


  void Assembler::run_any_lane(Reg dst, const MCOperand& src, int stride, int lanes, Span span)
  {
    emit(selector_.select_run_any_lane(dst, src, stride, lanes, span));
  }


  void Assembler::run_saturating(Reg dst, const MCOperand& left, const MCOperand& right, bool adding, bool is_signed,
                                 int bits, Span span)
  {
    emit(selector_.select_run_saturating(dst, left, right, adding, is_signed, bits, span));
  }


  void Assembler::run_splat(Reg dst, const MCOperand& src, int bits, Span span)
  {
    emit(selector_.select_run_splat(dst, src, bits, span));
  }


  void Assembler::run_ones(Reg dst, Span span)
  {
    emit(selector_.select_run_ones(dst, span));
  }

  void Assembler::run_compare(Condition cond, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                              Span span)
  {
    emit(selector_.select_run_compare(cond, dst, left, right, bits, span));
  }

  void Assembler::run_mask(Reg dst, const MCOperand& src, int lanes, Span span)
  {
    emit(selector_.select_run_mask(dst, src, lanes, span));
  }

  void Assembler::run_truths(Reg dst, const MCOperand& src, Span span)
  {
    emit(selector_.select_run_truths(dst, src, span));
  }


  void Assembler::float_op(const Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits, Span span)
  {
    emit(selector_.select_float_op(op, dst, left, right, bits, span));
  }


  void Assembler::float_compare(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, int bits,
                                Span span)
  {
    emit(selector_.select_float_compare(cond, dst, lhs, rhs, bits, span));
  }


  void Assembler::shift(const Op* op, Reg dst, const MCOperand& value, const MCOperand& amount, int bits, Span span)
  {
    emit(selector_.select_shift(op, dst, value, amount, bits, span));
  }


  void Assembler::divide(Reg dst, const MCOperand& left, const MCOperand& right, bool is_signed, bool remainder,
                         int bits, Span span)
  {
    emit(selector_.select_divide(dst, left, right, is_signed, remainder, bits, span));
  }


  void Assembler::wide_product(Reg low, Reg high, const MCOperand& left, const MCOperand& right, bool is_signed,
                               Span span)
  {
    emit(selector_.select_wide_product(low, high, left, right, is_signed, span));
  }


  void Assembler::select(Reg dst, const MCOperand& chooser, const MCOperand& if_true, const MCOperand& if_false,
                         bool floating, int bits, Span span)
  {
    emit(selector_.select_select(dst, chooser, if_true, if_false, floating, bits, span));
  }


  void Assembler::clamp(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, const MCOperand& bound,
                        Span span)
  {
    emit(selector_.select_clamp(cond, dst, lhs, rhs, bound, span));
  }


  void Assembler::falls_through(const std::string& target)
  {
    block_->successors.push_back(target);
  }


  MCSymRef Assembler::block_ref(const std::string& label)
  {
    return MCSymRef{ sym_expr(streamer_.symbol(label)), RelocKind::pcrel };
  }


  std::int64_t Assembler::frame_slot(std::int64_t size, std::int64_t to)
  {
    return function_->frame.reserve(size, to);
  }


  Reg Assembler::temporary(Reg like)
  {
    if (registers == nullptr) [[unlikely]]
      throw RegisterAssignmentError("a function with no register file", 1);
    return Reg(registers->new_virtual(like.cls(), like.bits()));
  }


  void Assembler::tail_jump(const std::string& target, Span span, std::vector<Reg> reads)
  {
    tail_jump(symref(target), span, std::move(reads));
  }


  void Assembler::tail_jump(const MCOperand& target, Span span, std::vector<Reg> reads)
  {
    auto selected = selector_.select_jump(target, span);
    for (const auto& inst : selected)
      inst->reads = reads;
    emit(selected);
  }


  void Assembler::frame(std::int64_t size, Span span)
  {
    emit(selector_.select_frame(size, span));
  }


  void Assembler::unframe(std::int64_t size, Span span)
  {
    emit(selector_.select_unframe(size, span));
  }


  void Assembler::put_aside(std::int64_t slot, Reg source, Span span)
  {
    emit(selector_.select_spill(slot, source, span));
  }


  void Assembler::take_back(Reg destination, std::int64_t slot, Span span)
  {
    emit(selector_.select_reload(destination, slot, span));
  }


  void Assembler::incoming(Reg dst, std::int64_t offset, int bits, bool is_signed, Span span)
  {
    emit({ make_inst(&incoming_row,
                     { MCReg{ dst }, MCImm{ offset, 32, false }, MCImm{ bits, 32, false },
                       MCImm{ is_signed ? 1 : 0, 32, false } },
                     span) });
  }


  void Assembler::ret(Span span, std::vector<Reg> reads)
  {
    auto selected = selector_.select_return(span);
    for (const auto& inst : selected)
      if (has(inst->desc->flags, InstFlags::return_))
        inst->reads = reads;
    emit(selected);
  }

} // namespace pl4g::mc
