// Instruction selection for x86-64: the lowering of one IR function to the
// machine form, the second half of pypl4g's target/x86_64/isel.py.
#include <format>
#include <unordered_map>

#include "ir/builder.hh"
#include "diag/engine.hh"
#include "ir/layout.hh"
#include "util/reflect.hh"
#include "target/bitcount.hh"
#include "target/carryless.hh"
#include "target/branches.hh"
#include "target/globals.hh"
#include "target/narrow.hh"
#include "target/ordering.hh"
#include "target/runs.hh"
#include "target/saturate.hh"
#include "target/scratch.hh"
#include "target/statuses.hh"
#include "target/x86_64/abi.hh"
#include "target/x86_64/isel.hh"
#include "target/x86_64/regs.hh"
#include "target/x86_64/startup.hh"


namespace pl4g::target::x86_64 {

  namespace {

    using mc::Condition;
    using mc::MCImm;
    using mc::MCMem;
    using mc::MCOperand;
    using mc::MCReg;
    using mc::Reg;
    using ir::InstKind;
    using ir::Value;

    // What each operation of the representation is called in the assembler;
    // the wrapping three are the same instructions with nothing asked after.
    const mc::Op* plain_op(ir::BinOp op)
    {
      switch (op) {
      case ir::BinOp::add:
      case ir::BinOp::wrap_add: return mc::ops::plus;
      case ir::BinOp::sub:
      case ir::BinOp::wrap_sub: return mc::ops::minus;
      case ir::BinOp::mul:
      case ir::BinOp::wrap_mul: return mc::ops::times;
      case ir::BinOp::and_: return mc::ops::and_;
      case ir::BinOp::or_: return mc::ops::or_;
      case ir::BinOp::xor_: return mc::ops::xor_;
      default: return nullptr;
      }
    }

    // What a branch compares against where its condition is a value rather
    // than a comparison.
    const MCImm zero_immediate{ 0, 32, false };

    // How wide a floating-point register is declared to be.
    constexpr int float_register_bits = 128;

    std::optional<Span> valid(Span span)
    {
      return span.is_valid() ? std::optional<Span>(span) : std::nullopt;
    }

    // How many bits a value of `ty` occupies in memory.
    int width_of(const ir::Type* ty)
    {
      if (auto integer = ir::as<ir::IntType>(ty))
        return int(integer->held());
      if (auto floating = ir::as<ir::FloatType>(ty))
        return int(floating->bits);
      if (auto character = ir::as<ir::CharType>(ty))
        return int(character->holder()->bits);
      if (auto enumeration = ir::as<ir::EnumType>(ty))
        return int(enumeration->holder->bits);
      if (ir::is<ir::ProductType>(ty)) {
        // A record of one value is that value.
        auto inner = ir::parts_of(ty);
        if (inner.size() == 1 && inner[0] != ty && ! ir::made_of_parts(inner[0]))
          return width_of(inner[0]);
      }
      return ir::is<ir::BoolType>(ty) ? 8 : 64;
    }

    // Whether a narrow value of `ty` is widened by its sign when it is read.
    bool is_signed(const ir::Type* ty)
    {
      if (auto character = ir::as<ir::CharType>(ty))
        return character->holder()->is_signed;
      if (auto enumeration = ir::as<ir::EnumType>(ty))
        return enumeration->holder->is_signed;
      auto integer = ir::as<ir::IntType>(ty);
      return integer != nullptr && integer->is_signed;
    }

    // The number a constant stands for and its type.
    std::optional<std::pair<__int128, const ir::Type*>> number_of(const Value* value)
    {
      switch (value->vkind) {
      case ir::ValueKind::int_const: return std::pair{ static_cast<const ir::IntConst*>(value)->value, value->ty };
      case ir::ValueKind::char_const:
        return std::pair{ __int128(static_cast<const ir::CharConst*>(value)->value), value->ty };
      case ir::ValueKind::bool_const:
        return std::pair{ __int128(static_cast<const ir::BoolConst*>(value)->value ? 1 : 0), value->ty };
      case ir::ValueKind::enum_const:
        return std::pair{ __int128(static_cast<const ir::EnumConst*>(value)->number()), value->ty };
      default: return std::nullopt;
      }
    }

    // The narrowest standard width that can carry `value` into an operation
    // `width` bits wide: an immediate narrower than the operation is
    // sign-extended.
    int immediate_width(__int128 value, int width = 64)
    {
      for (int bits : { 8, 16, 32 }) {
        if (bits >= width)
          break;
        if (-(__int128(1) << (bits - 1)) <= value && value < (__int128(1) << (bits - 1)))
          return bits;
      }
      return width;
    }

    bool is_floating(const ir::Type* ty)
    {
      if (auto result = ir::as<ir::ResultType>(ty))
        ty = result->ok;
      return ir::is<ir::FloatType>(ty);
    }

    int bits_of(const ir::Type* ty) { return int(ir::as<ir::FloatType>(ty)->bits); }

    // `place` named at the width a value of `ty` is passed at.
    const mc::PhysReg* as_argument(const mc::PhysReg* place, const ir::Type* ty)
    {
      auto& info = registers().info;
      if (ir::is<ir::FloatType>(ty))
        return info.view(place->unit, float_register_bits);
      return info.view(place->unit, std::max(32, width_of(ty)));
    }

    // Write one part of an argument into its slot at the bottom of the frame:
    // a whole number as the whole register, which is what it would have been
    // in a register of the convention, and a floating-point one at its own
    // width.  The callee reads only as much as the type holds.
    void pass_on_stack(mc::Assembler& asm_, const OnStack& place, const ir::Type* ty, const MCOperand& source,
                       Span span)
    {
      MCMem slot;
      slot.base = registers().rsp;
      slot.disp = place.offset;
      if (ir::is<ir::FloatType>(ty)) {
        slot.size_bits = bits_of(ty);
        asm_.store(slot, source, span);
        return;
      }
      slot.size_bits = 64;
      asm_.store(slot, MCReg{ std::get<MCReg>(source).reg, 64 }, span);
    }

    // The register an instruction's result is put in.
    const mc::PhysReg* result_register(const ir::Type* ty, const CallConvDesc& cconv, std::size_t index = 0)
    {
      auto& info = registers().info;
      if (ir::is<ir::FloatType>(ty))
        return info.view(cconv.float_ret_regs[index]->unit, float_register_bits);
      return info.view(cconv.int_ret_regs[index]->unit, std::max(32, width_of(ty)));
    }

    std::uint64_t run_covers(const ir::VecType* ty, const ir::DataLayout& layout)
    {
      return ty->lanes * ir::stride_of(ty->element, layout) * 8;
    }

    int run_bits(const ir::VecType* ty, const ir::DataLayout& layout)
    {
      return std::max(float_register_bits, int(ty->lanes * ir::stride_of(ty->element, layout) * 8));
    }

    // How many registers the answer of a result takes.
    std::size_t answer_registers(const Value* value)
    {
      auto result = ir::as<ir::ResultType>(value->ty);
      if (result == nullptr)
        return 1;
      return ir::leaves_of(result->ok).size();
    }

    // A register for a value the function computes: at least a word, of the
    // kind the value wants.
    const mc::VirtReg* new_value(const ir::Type* ty, const ir::DataLayout& layout, const mc::PhysReg* hint = nullptr)
    {
      auto& r = registers();
      if (ir::is<ir::ProductType>(ty) && ! ir::held_in_memory(ty)) {
        auto inner = ir::parts_of(ty);
        if (inner.size() == 1 && inner[0] != ty && ! ir::made_of_parts(inner[0]))
          return new_value(inner[0], layout, hint);
        throw UnsupportedOperation(std::format("a value of type '{}'", ty->written()));
      }
      if (auto run = ir::as<ir::VecType>(ty))
        return r.info.new_virtual(r.vec, run_bits(run, layout), hint);
      if (ir::is<ir::FloatType>(ty))
        return r.info.new_virtual(r.vec, float_register_bits, hint);
      return r.info.new_virtual(r.gpr, std::max(32, width_of(ty)), hint);
    }

    // The value the function returns, where it computes one.
    const Value* returned_value(ir::Function& func)
    {
      for (const auto& block : func.blocks)
        for (auto inst : block->insts)
          if (inst->kind == InstKind::RetInst && ! inst->operands.empty())
            return inst->operands[0];
      return nullptr;
    }

    // Where a member of something placed in memory is, said beside the
    // address: Python's keyword arguments to `asm.mem`.
    struct Where {
      std::int64_t disp = 0;
      std::optional<int> size_bits;
      bool is_signed = false;
    };


    // One function being lowered, and what it has computed so far.
    class Lowering final : public Operands, public Scratch {
    public:
      Lowering(mc::Assembler& asm_, ir::Function& func, const CallConvDesc& cconv, Messages* messages,
               const SourceManager* sources, Constants* constants, const Destroyed* known_clobbers,
               const ir::DataLayout& layout)
        : asm_(asm_), func(func), cconv(cconv), messages(messages), sources(sources), constants(constants),
          known_clobbers(known_clobbers), layout(layout)
      {
      }

      void run();

      // -- the operands, as the shared lowerings ask for them ------------------

      MCOperand value(Value* value, Span span) override
      {
        if (value->vkind == ir::ValueKind::float_const)
          // No instruction carries a floating-point number, so one goes in the
          // image and is read from there.
          return MCReg{ floating(static_cast<const ir::FloatConst*>(value), span) };
        if (auto constant = number_of(value)) {
          auto [number, ty] = *constant;
          return MCImm{ number, immediate_width(number, std::max(32, width_of(ty))), is_signed(ty) };
        }
        return in_register(value, span);
      }

      MCOperand in_register(Value* value, Span span) override
      {
        if (value->vkind == ir::ValueKind::float_const)
          return MCReg{ floating(static_cast<const ir::FloatConst*>(value), span) };
        if (auto constant = number_of(value)) {
          // Every comparison here wants a register on its left.
          auto [number, ty] = *constant;
          auto carried = new_value(ty, layout);
          asm_.loadreg(carried, MCImm{ number, std::max(32, width_of(ty)), is_signed(ty) }, span);
          return MCReg{ carried };
        }
        auto found = held.find(value);
        if (found == held.end()) [[unlikely]]
          throw UnsupportedOperation("a value this backend did not compute", valid(span));
        return MCReg{ found->second };
      }

      const mc::VirtReg* register_of(const Value* value, std::optional<Span> span)
      {
        auto found = held.find(value);
        if (found == held.end()) [[unlikely]]
          throw UnsupportedOperation("a value this backend did not compute", span);
        return found->second;
      }

      // The register holding whether a result has an answer: after the
      // answer's own registers, of which there may be several.
      const mc::VirtReg* flag_of(const Value* value, std::optional<Span> span)
      {
        return part(value, answer_registers(value), span);
      }

      const mc::VirtReg* part(const Value* value, std::size_t index, std::optional<Span> span)
      {
        if (index == 0)
          return register_of(value, span);
        auto found = extra.find(value);
        if (found == extra.end() || index > found->second.size()) [[unlikely]]
          throw UnsupportedOperation("a value of several parts this backend did not compute", span);
        return found->second[index - 1];
      }

      Reg part_of(Value* value, std::size_t index, std::optional<Span> span) override
      {
        return part(value, index, span);
      }

      // The operand for `value`, where it may be one nothing may read: the
      // answer half of an error, written as nought.
      MCOperand undefined(Value* value, const ir::Type* ty, Span span)
      {
        if (value->vkind == ir::ValueKind::undef)
          return MCImm{ 0, std::max(32, width_of(ty)), false };
        return this->value(value, span);
      }

      Reg destination(Value* value) override
      {
        auto found = held.find(value);
        if (found == held.end()) [[unlikely]]
          throw UnsupportedOperation("a value this backend did not compute");
        return found->second;
      }

      // A register holding the floating-point constant of type `ty` that
      // `number` is.
      const mc::VirtReg* floating_constant(double number, const ir::Type* ty, Span span)
      {
        if (constants == nullptr) [[unlikely]]
          throw UnsupportedOperation("a floating-point constant, with nowhere to put it");
        int bits = width_of(ty);
        auto symbol = constants->symbol(ir::encode_float(number, ty, layout), std::uint64_t(bits / 8));
        auto into = new_value(ty, layout);
        asm_.loadreg(into, rip_relative(symbol, bits), span);
        return into;
      }

      const mc::VirtReg* floating(const ir::FloatConst* value, Span span)
      {
        return floating_constant(value->value, value->ty, span);
      }

      // A register holding every bit of a `bits`-wide float but the sign.
      const mc::VirtReg* mask(int bits, Span span)
      {
        if (constants == nullptr) [[unlikely]]
          throw UnsupportedOperation("a magnitude, with nowhere to put its mask");
        mc::Bytes data;
        unsigned __int128 pattern = (static_cast<unsigned __int128>(1) << (bits - 1)) - 1;
        for (int i = 0; i < bits / 8; ++i)
          data.push_back(std::uint8_t(pattern >> (8 * i)));
        auto symbol = constants->symbol(data, std::uint64_t(bits / 8));
        auto into = registers().info.new_virtual(registers().vec, float_register_bits);
        asm_.loadreg(into, rip_relative(symbol, bits), span);
        return into;
      }

      Reg scratch() override { return registers().info.new_virtual(registers().gpr, 64); }

      Reg run_scratch(int bits) override { return registers().info.new_virtual(registers().vec, bits); }

    private:
      // What is emitted where an answer will not fit its type.
      class Told final : public Fault {
      public:
        Told(Lowering& owner, std::string_view what, Span span, int status = statuses::OVERFLOW)
          : owner(owner), text(describe(what, owner.func.name, span, owner.sources)), status(status)
        {
        }

        // Report the fault and come back, for a build that observes; the call
        // is given everything the convention lets it destroy.
        void observed(mc::Assembler& asm_, Span span)
        {
          if (owner.messages == nullptr) [[unlikely]]
            throw UnsupportedOperation("an operation that can fault, with nowhere to report it");
          auto symbol = owner.messages->symbol(text);
          const auto& system = lookup_cconv(ir::system_cconv);
          asm_.address(system.int_arg_regs[0], symbol, span);
          asm_.loadreg(system.int_arg_regs[1], asm_.imm(__int128(text.size()), 32, false), span);
          auto destroyed = destroyed_by(nullptr, system, registers().info, nullptr);
          asm_.call(std::string(report_symbol), span, std::vector<Reg>(destroyed.begin(), destroyed.end()));
        }

        // Report the fault and stop; this does not come back.
        void out_of_range(mc::Assembler& asm_, Span span) override
        {
          if (owner.messages == nullptr) [[unlikely]]
            throw UnsupportedOperation("an operation that can fault, with nowhere to report it");
          auto symbol = owner.messages->symbol(text);
          // The runtime follows one settled convention whatever the function
          // reporting the fault follows.
          const auto& system = lookup_cconv(ir::system_cconv);
          asm_.address(system.int_arg_regs[0], symbol, span);
          asm_.loadreg(system.int_arg_regs[1], asm_.imm(__int128(text.size()), 32, false), span);
          asm_.loadreg(system.int_arg_regs[2], asm_.imm(status, 32, false), span);
          asm_.call(std::string(abort_symbol), span);
        }

      private:
        Lowering& owner;
        std::string text;
        int status;
      };

      MCMem rip_relative(const std::string& symbol, std::optional<int> size_bits)
      {
        MCMem made;
        made.disp_sym = mc::sym_expr(asm_.streamer().symbol(symbol));
        made.rip_relative = true;
        made.size_bits = size_bits;
        return made;
      }

      // Where in memory an address names, for a read or for a write: a
      // variable by its symbol, relative to the instruction, anything else as
      // the register holding it.
      MCMem place_of(const Value* address, std::optional<Span> span, const Where& where = {})
      {
        MCMem made;
        if (address->vkind == ir::ValueKind::global) {
          made.disp_sym = mc::sym_expr(asm_.streamer().symbol(symbol_of(static_cast<const ir::GlobalVar*>(address))));
          made.rip_relative = true;
        } else
          made.base = register_of(address, span);
        made.disp = where.disp;
        made.size_bits = where.size_bits;
        made.is_signed = where.is_signed;
        return made;
      }

      const mc::PhysReg* hint_if_returned(const Value* inst, const ir::Type* ty, std::size_t index = 0)
      {
        return inst == returned ? result_register(ty, cconv, index) : nullptr;
      }

      // A new register for `inst`'s value, wanted where the function answers
      // if that is what becomes of it.
      const mc::VirtReg* computed(ir::Instruction* inst, const ir::Type* ty)
      {
        auto made = new_value(ty, layout, hint_if_returned(inst, ty));
        held[inst] = made;
        return made;
      }

      MCOperand part_source(Value* value, std::size_t index, const ir::Type* ty, std::size_t how_many, Span span)
      {
        if (how_many == 1)
          return undefined(value, ty, span);
        if (value->vkind == ir::ValueKind::undef)
          return MCImm{ 0, std::max(32, width_of(ty)), false };
        return MCReg{ part(value, index, span) };
      }

      void lower(ir::Instruction* inst, std::size_t index);
      void lower_call(ir::Instruction* inst, std::optional<Span> span);
      // Put the whole number `given`, of `counted`, into `destination` as the
      // floating-point number of `bits` nearest it.
      void to_float(const MCOperand& given, const ir::IntType* counted, int bits, const mc::VirtReg* destination,
                    Span span);
      // Put the floating-point value `given` of `bits` into `destination` as
      // the whole number it is toward zero; `limit` holds 2⁶³ for a `u64`.
      void to_int(const MCOperand& given, int bits, const mc::VirtReg* limit, const mc::VirtReg* destination,
                  Span span);
      void lower_return(ir::Instruction* inst, std::optional<Span> span);
      void lower_store(ir::Instruction* inst, std::optional<Span> span);
      bool lower_binary(ir::Instruction* inst, std::optional<Span> span);

      mc::Assembler& asm_;
      ir::Function& func;
      const CallConvDesc& cconv;
      Messages* messages;
      const SourceManager* sources;
      Constants* constants;
      const Destroyed* known_clobbers;
      const ir::DataLayout& layout;
      // Where each value the function computes is held, and the registers of
      // a value that takes more than one, after the first.
      std::unordered_map<const Value*, const mc::VirtReg*> held;
      std::unordered_map<const Value*, std::vector<const mc::VirtReg*>> extra;
      const Value* returned = nullptr;
      std::vector<std::string> labels;
      // What each call passes where, asked once before any code is generated.
      CallsPlaced placed;
    };


    void Lowering::run()
    {
      // The convention is the function's and not the image's.
      asm_.begin_function(ir::symbol_name(func), func.linkage == ir::Linkage::visible, 0,
                          mc::AllocationOrder::per_class(cconv.orders(registers().gpr->name, registers().vec->name)),
                          cconv.callee_saved);
      returned = returned_value(func);
      auto symbol = ir::symbol_name(func);
      labels = labels_of(symbol, func);

      // Every block parameter gets its register before any block is walked: a
      // branch writes the parameters of a block that may come later.  The
      // entry block's are the function's own, arriving where the convention
      // says, and copied out at once.
      const ir::BasicBlock* entry = func.blocks.empty() ? nullptr : func.blocks.front().get();
      Places arriving;
      if (entry != nullptr) {
        std::vector<const ir::Type*> types;
        for (const auto& param : entry->params)
          types.push_back(param->ty);
        arriving = argument_places(cconv, types, func.attrs.chained, func.attrs.answer_first);
      }
      // What each call passes where: the room the arguments that go on the
      // stack are written into is at the bottom of this function's frame,
      // where the stack pointer is at every call, so it is taken before
      // anything else of the frame is, as large as the largest call wants it.
      placed = calls_placed(func, lookup_cconv);
      std::int64_t outgoing = 0;
      for (const auto& [_, one] : placed)
        outgoing = std::max(outgoing, stack_bytes(one.second));
      if (outgoing != 0 && asm_.frame_slot(outgoing, 16) != 0) [[unlikely]]
        throw InternalError("the room for outgoing arguments is not at the bottom");
      for (std::size_t index = 0; index < func.blocks.size(); ++index) {
        const auto& block = *func.blocks[index];
        for (std::size_t position = 0; position < block.params.size(); ++position) {
          const auto* param = block.params[position].get();
          if (index != 0) {
            if (param->ty == ir::mem_type())
              // Not a value: what it says is which path's ordering of memory
              // holds from here.
              continue;
            std::vector<const mc::VirtReg*> given;
            for (auto piece : ir::leaves_of(param->ty))
              given.push_back(new_value(piece, layout));
            held[param] = given.at(0);
            if (given.size() > 1)
              extra[param] = std::vector<const mc::VirtReg*>(given.begin() + 1, given.end());
            continue;
          }
          auto pieces = ir::leaves_of(param->ty);
          std::vector<const mc::VirtReg*> given;
          for (std::size_t at = 0; at < pieces.size() && at < arriving[position].size(); ++at) {
            auto place = std::get_if<const mc::PhysReg*>(&arriving[position][at]);
            given.push_back(new_value(pieces[at], layout, place == nullptr ? nullptr : as_argument(*place, pieces[at])));
          }
          held[param] = given.at(0);
          if (given.size() > 1)
            extra[param] = std::vector<const mc::VirtReg*>(given.begin() + 1, given.end());
        }
      }
      if (entry != nullptr)
        for (std::size_t position = 0; position < entry->params.size(); ++position) {
          auto param = entry->params[position].get();
          auto pieces = ir::leaves_of(param->ty);
          for (std::size_t at = 0; at < pieces.size() && at < arriving[position].size(); ++at) {
            if (auto slot = std::get_if<OnStack>(&arriving[position][at])) {
              // Read where the caller left it, in its own frame.
              asm_.incoming(part(param, at, std::nullopt), slot->offset, width_of(pieces[at]),
                            is_signed(pieces[at]), func.span);
              continue;
            }
            asm_.loadreg(part(param, at, std::nullopt),
                         MCReg{ as_argument(std::get<const mc::PhysReg*>(arriving[position][at]), pieces[at]) },
                         func.span);
          }
        }

      for (std::size_t index = 0; index < func.blocks.size(); ++index) {
        if (index > 0)
          asm_.block(labels[index]);
        for (auto inst : func.blocks[index]->insts)
          lower(inst, index);
      }
      asm_.end_function();
    }


    void Lowering::lower(ir::Instruction* inst, std::size_t index)
    {
      auto span = valid(inst->span);
      if (auto refused = cannot_order(inst)) [[unlikely]]
        throw UnsupportedOperation(*refused, inst->span);
      auto operand = [&](std::size_t at) { return inst->operands[at]; };
      switch (inst->kind) {
      case InstKind::FrameInst: {
        // Room of this function's own: the stack pointer and how far in.
        auto destination = computed(inst, inst->ty);
        asm_.op(mc::ops::plus, destination,
                { MCReg{ registers().rsp },
                  MCImm{ asm_.frame_slot(std::int64_t(ir::size_of(inst->held, layout)),
                                         std::int64_t(ir::align_of(inst->held, layout))),
                         32, false } },
                inst->span);
        return;
      }
      case InstKind::AssertInst: {
        // Failing is the case that jumps, the case that does not come back.
        auto holds = asm_.reserve_label("holds");
        auto [condition, lhs, rhs] = condition_of(func, inst, *this, zero_immediate, inst->span);
        asm_.branch(condition, lhs, rhs, holds, inst->span);
        Told told(*this, inst->what, inst->span, inst->status);
        if (inst->observing)
          told.observed(asm_, inst->span);
        else
          told.out_of_range(asm_, inst->span);
        asm_.block(holds);
        return;
      }
      case InstKind::MemStartInst:
        // The token orders the operations that touch memory, and is held in
        // no register.
        return;
      case InstKind::CodeInst: {
        auto destination = computed(inst, inst->ty);
        asm_.address(destination, ir::symbol_name(*inst->callee), inst->span);
        return;
      }
      case InstKind::AddressInst: {
        auto destination = computed(inst, inst->ty);
        asm_.address(destination, symbol_of(static_cast<const ir::GlobalVar*>(operand(0))), inst->span);
        return;
      }
      default: break;
      }

      if (inst->kind == InstKind::LoadInst && ir::is<ir::VecType>(inst->ty)) {
        auto run = ir::as<ir::VecType>(inst->ty);
        auto bits = int(run_covers(run, layout));
        auto destination = new_value(inst->ty, layout);
        held[inst] = destination;
        auto read = destination;
        if (run->element == ir::bool_type())
          // A truth value is a byte of one or nought in memory and a lane of
          // every bit set or clear in a register.
          read = new_value(inst->ty, layout);
        asm_.run_move(Reg(read), place_of(operand(1), span, { 0, bits }), bits, inst->span);
        if (read != destination)
          asm_.run_truths(destination, MCReg{ read, run_bits(run, layout) }, inst->span);
        return;
      }
      if (inst->kind == InstKind::StoreInst && ir::is<ir::VecType>(operand(2)->ty)) {
        auto written = operand(2);
        auto run = ir::as<ir::VecType>(written->ty);
        auto source = register_of(written, inst->span);
        if (run->element == ir::bool_type()) {
          // And back to a byte of one or nought.
          auto bytes_of = new_value(written->ty, layout);
          asm_.run_truths(bytes_of, MCReg{ source, run_bits(run, layout) }, inst->span);
          source = bytes_of;
        }
        asm_.run_store(place_of(operand(1), span, { 0, int(run_covers(run, layout)) }),
                       MCReg{ source, run_bits(run, layout) }, int(run_covers(run, layout)), inst->span);
        return;
      }
      if (inst->kind == InstKind::SplatInst) {
        auto run = ir::as<ir::VecType>(inst->ty);
        auto destination = new_value(inst->ty, layout);
        held[inst] = destination;
        auto spread = destination;
        if (run->element == ir::bool_type())
          spread = new_value(inst->ty, layout);
        asm_.run_splat(spread, value(operand(0), inst->span), width_of(run->element), inst->span);
        if (spread != destination)
          // A truth value spread over every lane is a run of them as memory
          // holds one.
          asm_.run_truths(destination, MCReg{ spread, run_bits(run, layout) }, inst->span);
        return;
      }
      if (inst->kind == InstKind::CmpInst && ir::is<ir::VecType>(inst->ty)) {
        auto compared = ir::as<ir::VecType>(operand(0)->ty);
        auto destination = new_value(inst->ty, layout);
        held[inst] = destination;
        auto left = value(operand(0), inst->span);
        auto right = value(operand(1), inst->span);
        asm_.run_compare(condition_for(inst->pred), destination, left, right, width_of(compared->element),
                         inst->span);
        return;
      }
      if (inst->kind == InstKind::UnaryInst && inst->un == ir::UnOp::mask) {
        auto gathered = ir::as<ir::VecType>(operand(0)->ty);
        auto destination = computed(inst, inst->ty);
        asm_.run_mask(destination, MCReg{ register_of(operand(0), inst->span), run_bits(gathered, layout) },
                      int(gathered->lanes), inst->span);
        return;
      }
      if (inst->kind == InstKind::BinaryInst && ir::is<ir::VecType>(inst->ty)) {
        auto run = ir::as<ir::VecType>(inst->ty);
        auto destination = new_value(inst->ty, layout);
        held[inst] = destination;
        if (is_trapping(inst->bin)) {
          try {
            auto left = value(operand(0), inst->span);
            auto right = value(operand(1), inst->span);
            Told told(*this, std::format("{} that does not fit", operation_name(inst->bin)), inst->span);
            lower_trapping_run(asm_, inst->bin, run, left, right, destination, *this, told, layout, inst->span);
          } catch (const Unsupported& unsupported) {
            throw UnsupportedOperation(unsupported.what_, span);
          }
          return;
        }
        if (is_saturating(inst->bin)) {
          auto left = value(operand(0), inst->span);
          auto right = value(operand(1), inst->span);
          asm_.run_saturating(destination, left, right, inst->bin == ir::BinOp::sat_add, is_signed(run->element),
                              width_of(run->element), inst->span);
          return;
        }
        auto operation = plain_op(inst->bin);
        if (operation == nullptr) [[unlikely]]
          throw UnsupportedOperation(std::format("'{}' over a whole run", pl4g::describe(inst->bin)), span);
        auto left = value(operand(0), inst->span);
        auto right = value(operand(1), inst->span);
        asm_.run_op(operation, destination, left, right, width_of(run->element), inst->span);
        return;
      }
      if (inst->kind == InstKind::UnaryInst && ir::is<ir::VecType>(inst->ty)) {
        auto run = ir::as<ir::VecType>(inst->ty);
        if (inst->un != ir::UnOp::not_) [[unlikely]]
          throw UnsupportedOperation(std::format("'{}' over a whole run", pl4g::describe(inst->un)), span);
        auto destination = new_value(inst->ty, layout);
        held[inst] = destination;
        // Every bit set: every lane compared with itself is equal.
        auto ones = new_value(inst->ty, layout);
        asm_.run_ones(ones, inst->span);
        auto left = value(operand(0), inst->span);
        asm_.run_op(mc::ops::xor_, destination, left, MCReg{ ones, run_bits(run, layout) }, width_of(run->element),
                    inst->span);
        return;
      }
      if (inst->kind == InstKind::LoadInst && ir::made_of_parts(inst->ty)) {
        // Several values one after another in memory: one read per part at
        // the offset the layout gives it.
        auto address = operand(1);
        auto pieces = ir::leaves_of(inst->ty);
        auto offsets = ir::leaf_offsets_of(inst->ty, layout);
        std::vector<const mc::VirtReg*> taken;
        for (std::size_t at = 0; at < pieces.size() && at < offsets.size(); ++at) {
          auto into = new_value(pieces[at], layout, hint_if_returned(inst, pieces[at], at));
          asm_.loadreg(into,
                       place_of(address, span,
                                { std::int64_t(offsets[at]), width_of(pieces[at]), is_signed(pieces[at]) }),
                       inst->span);
          taken.push_back(into);
        }
        held[inst] = taken[0];
        extra[inst] = std::vector<const mc::VirtReg*>(taken.begin() + 1, taken.end());
        return;
      }
      if (inst->kind == InstKind::LoadInst) {
        auto destination = computed(inst, inst->ty);
        auto read = place_of(operand(1), span, { 0, width_of(inst->ty), is_signed(inst->ty) });
        if (inst->ordering == ir::Ordering::acquire)
          asm_.acquire(destination, read, inst->span);
        else
          asm_.loadreg(destination, read, inst->span);
        return;
      }
      if (inst->kind == InstKind::StoreInst) {
        lower_store(inst, span);
        return;
      }
      if (inst->kind == InstKind::RetInst) {
        lower_return(inst, span);
        return;
      }
      if (inst->kind == InstKind::BinaryInst && lower_binary(inst, span))
        return;
      switch (inst->kind) {
      case InstKind::UnaryInst: {
        if (is_counting(inst->un)) {
          auto destination = computed(inst, inst->ty);
          auto counted = operand(0)->ty;
          auto given = value(operand(0), inst->span);
          lower_count(asm_, inst->un, given, int(ir::as<ir::IntType>(counted)->bits), is_signed(counted), destination, *this, inst->span);
          return;
        }
        std::optional<mc::Rounding> rounding;
        switch (inst->un) {
        case ir::UnOp::floor: rounding = mc::Rounding::down; break;
        case ir::UnOp::ceil: rounding = mc::Rounding::up; break;
        case ir::UnOp::nearest: rounding = mc::Rounding::nearest; break;
        case ir::UnOp::rounded: rounding = mc::Rounding::current; break;
        default: break;
        }
        if (rounding) {
          auto destination = computed(inst, inst->ty);
          auto given = in_register(operand(0), inst->span);
          asm_.float_round(*rounding, destination, given, bits_of(inst->ty), inst->span);
          return;
        }
        if (inst->un == ir::UnOp::fabs) {
          // The magnitude is an `and` with a mask that has every bit but the
          // sign set, the mask a constant in the image.
          int bits = bits_of(inst->ty);
          auto destination = computed(inst, inst->ty);
          auto given = in_register(operand(0), inst->span);
          auto masked = mask(bits, inst->span);
          asm_.float_op(mc::ops::and_, destination, given, MCReg{ masked }, bits, inst->span);
          return;
        }
        const mc::Op* unary = inst->un == ir::UnOp::not_ ? mc::ops::not_
                              : inst->un == ir::UnOp::neg ? mc::ops::neg
                                                          : nullptr;
        if (unary == nullptr) [[unlikely]]
          throw UnsupportedOperation(std::format("the operation '{}'", pl4g::describe(inst->un)), span);
        auto destination = computed(inst, inst->ty);
        auto given = in_register(operand(0), inst->span);
        asm_.op(unary, destination, { given }, inst->span);
        // The complement of a narrow unsigned value sets the bits above it.
        normalize(asm_, inst->ty, destination, std::max(32, width_of(inst->ty)), inst->span);
        return;
      }
      case InstKind::TupleInst: {
        // Several values that travel together: several registers.
        std::vector<const mc::VirtReg*> taken;
        for (auto piece : ir::leaves_of(inst->ty))
          taken.push_back(new_value(piece, layout));
        held[inst] = taken[0];
        extra[inst] = std::vector<const mc::VirtReg*>(taken.begin() + 1, taken.end());
        std::vector<MCOperand> members;
        for (auto one : inst->operands) {
          auto count = ir::leaves_of(one->ty).size();
          if (count == 1)
            members.push_back(value(one, inst->span));
          else
            for (std::size_t at = 0; at < count; ++at)
              members.push_back(MCReg{ part(one, at, inst->span) });
        }
        for (std::size_t at = 0; at < taken.size() && at < members.size(); ++at)
          asm_.loadreg(taken[at], members[at], inst->span);
        return;
      }
      case InstKind::ExtractInst: {
        auto whole = operand(0);
        if (ir::is<ir::ResultType>(whole->ty)) {
          // A member of a result, by its place among the three.
          std::size_t first = inst->index == 0 ? 0 : answer_registers(whole) + std::size_t(inst->index) - 1;
          auto count = ir::leaves_of(inst->ty).size();
          held[inst] = part(whole, first, span);
          if (count > 1) {
            std::vector<const mc::VirtReg*> more;
            for (std::size_t at = 1; at < count; ++at)
              more.push_back(part(whole, first + at, span));
            extra[inst] = std::move(more);
          }
          return;
        }
        // Nothing to emit: the value is in a register of its own already.
        held[inst] = part(whole, std::size_t(inst->index), span);
        return;
      }
      case InstKind::WrapInst: {
        // The answer's own registers, the truth value after them, and what
        // the error carries after that.
        auto result = ir::as<ir::ResultType>(inst->ty);
        auto answer_parts = ir::parts_of(result->ok);
        auto slots = ir::leaves_of(inst->ty);
        bool simple = answer_parts.size() == 1;
        std::vector<const mc::VirtReg*> taken;
        for (std::size_t at = 0; at < slots.size(); ++at)
          taken.push_back(new_value(slots[at], layout,
                                    inst == returned && simple ? result_register(slots[at], cconv, at) : nullptr));
        held[inst] = taken[0];
        extra[inst] = std::vector<const mc::VirtReg*>(taken.begin() + 1, taken.end());
        for (std::size_t at = 0; at < answer_parts.size(); ++at)
          asm_.loadreg(taken[at], part_source(operand(0), at, answer_parts[at], answer_parts.size(), inst->span),
                       inst->span);
        asm_.loadreg(taken[answer_parts.size()], value(operand(1), inst->span), inst->span);
        if (inst->operands.size() > 2) {
          auto carried = ir::parts_of(result->err);
          for (std::size_t at = 0; at < carried.size(); ++at)
            asm_.loadreg(taken[answer_parts.size() + 1 + at],
                         part_source(operand(2), at, carried[at], carried.size(), inst->span), inst->span);
        }
        return;
      }
      case InstKind::UnwrapInst: {
        auto how_many = answer_registers(operand(0));
        held[inst] = register_of(operand(0), span);
        if (how_many > 1) {
          std::vector<const mc::VirtReg*> more;
          for (std::size_t at = 1; at < how_many; ++at)
            more.push_back(part(operand(0), at, span));
          extra[inst] = std::move(more);
        }
        return;
      }
      case InstKind::FailedInst:
        held[inst] = flag_of(operand(0), span);
        return;
      case InstKind::ErrorInst: {
        auto first = answer_registers(operand(0)) + 1;
        held[inst] = part(operand(0), first, span);
        if (ir::made_of_parts(inst->ty)) {
          std::vector<const mc::VirtReg*> more;
          for (std::size_t at = 1; at < ir::parts_of(inst->ty).size(); ++at)
            more.push_back(part(operand(0), first + at, span));
          extra[inst] = std::move(more);
        }
        return;
      }
      case InstKind::CastInst: {
        switch (inst->cast) {
        case ir::CastKind::bitcast: {
          // The bits already in the register, read as something else.
          auto found = in_register(operand(0), inst->span);
          held[inst] = std::get<MCReg>(found).reg.virt();
          return;
        }
        case ir::CastKind::zext:
        case ir::CastKind::sext: {
          auto destination = computed(inst, inst->ty);
          auto given = in_register(operand(0), inst->span);
          asm_.widen(destination, given, width_of(operand(0)->ty), inst->cast == ir::CastKind::sext, inst->span);
          return;
        }
        case ir::CastKind::trunc: {
          auto destination = computed(inst, inst->ty);
          auto given = in_register(operand(0), inst->span);
          asm_.widen(destination, given, width_of(inst->ty), is_signed(inst->ty), inst->span);
          return;
        }
        case ir::CastKind::fext: {
          auto destination = computed(inst, inst->ty);
          auto given = in_register(operand(0), inst->span);
          asm_.float_extend(destination, given, bits_of(operand(0)->ty), bits_of(inst->ty), inst->span);
          return;
        }
        case ir::CastKind::itof: {
          auto destination = computed(inst, inst->ty);
          auto counted = ir::as<ir::IntType>(operand(0)->ty);
          auto given = in_register(operand(0), inst->span);
          to_float(given, counted, bits_of(inst->ty), destination, inst->span);
          return;
        }
        case ir::CastKind::ftoi: {
          auto destination = computed(inst, inst->ty);
          auto into = ir::as<ir::IntType>(inst->ty);
          auto source = operand(0);
          const mc::VirtReg* limit = nullptr;
          if (! into->is_signed && width_of(into) == 64)
            // The one bound the unsigned sequence works from: the first number
            // the signed conversion has no room for.
            limit = floating_constant(0x1p63, source->ty, inst->span);
          auto given = in_register(source, inst->span);
          to_int(given, bits_of(source->ty), limit, destination, inst->span);
          return;
        }
        case ir::CastKind::fbits: {
          auto destination = computed(inst, inst->ty);
          auto given = in_register(operand(0), inst->span);
          int bits = ir::is<ir::FloatType>(inst->ty) ? bits_of(inst->ty) : int(ir::as<ir::IntType>(inst->ty)->bits);
          asm_.move_bits(destination, given, bits, inst->span);
          return;
        }
        }
        break;
      }
      case InstKind::SelectInst: {
        auto destination = computed(inst, inst->ty);
        bool floating = ir::is<ir::FloatType>(inst->ty);
        std::vector<MCOperand> chosen;
        for (std::size_t at = 1; at < inst->operands.size(); ++at)
          chosen.push_back(floating ? in_register(operand(at), inst->span) : value(operand(at), inst->span));
        auto chooser = in_register(operand(0), inst->span);
        asm_.select(destination, chooser, chosen[0], chosen[1], floating, floating ? bits_of(inst->ty) : 64,
                    inst->span);
        return;
      }
      case InstKind::CmpInst: {
        if (ir::is<ir::FloatType>(operand(0)->ty)) {
          // Never folded into a branch: what a not-a-number makes of the
          // question needs more than one reading of the flags.
          auto destination = computed(inst, inst->ty);
          auto left = in_register(operand(0), inst->span);
          auto right = in_register(operand(1), inst->span);
          asm_.float_compare(condition_for(inst->pred), destination, left, right, bits_of(operand(0)->ty),
                             inst->span);
          return;
        }
        // Read exactly once, a comparison is folded into the branch that reads
        // it; any other number of times, its answer is a value.
        if (folded_into_branch(func, inst))
          return;
        auto destination = computed(inst, inst->ty);
        lower_comparison(asm_, inst, *this, destination);
        return;
      }
      case InstKind::SyscallInst: {
        // Everything is a machine word here, and the kernel names the
        // registers.
        const auto& calls = syscalls();
        std::vector<Move> handed;
        handed.push_back(
          { registers().info.view(calls.number.phys()->unit, 64), value(operand(0), inst->span) });
        for (std::size_t at = 1; at < inst->operands.size(); ++at)
          handed.push_back({ calls.arguments[at - 1], value(operand(at), inst->span) });
        for (const auto& move : sequenced(handed, [&](Reg like) { return asm_.temporary(like); }))
          asm_.loadreg(move.into, move.source, inst->span);
        std::vector<Reg> clobbers{ calls.answer };
        clobbers.insert(clobbers.end(), calls.clobbers.begin(), calls.clobbers.end());
        std::vector<Reg> reads;
        for (const auto& move : handed)
          reads.push_back(move.into);
        asm_.kernel(calls.enter, inst->span, clobbers, reads);
        auto taken_back = new_value(inst->ty, layout);
        held[inst] = taken_back;
        asm_.loadreg(taken_back, MCReg{ calls.answer }, inst->span);
        return;
      }
      case InstKind::CallInst:
        lower_call(inst, span);
        return;
      case InstKind::UnreachableInst:
        asm_.op(mc::ops::trap, Reg(), {}, inst->span);
        return;
      case InstKind::BrInst:
      case InstKind::CondBrInst:
      case InstKind::SwitchInst:
        try {
          lower_branch(asm_, func, labels, index, inst, *this, zero_immediate);
        } catch (const UnsupportedBranch& unsupported) {
          throw UnsupportedOperation(unsupported.what_, valid(unsupported.span));
        }
        return;
      default: break;
      }
      throw UnsupportedOperation(std::format("the instruction '{}'", inst->opcode()), span);
    }


    void Lowering::lower_store(ir::Instruction* inst, std::optional<Span> span)
    {
      auto address = inst->operands[1];
      auto written = inst->operands[2];
      if (ir::made_of_parts(written->ty)) {
        // One write per part, at the offsets a read of one reads at.
        auto pieces = ir::leaves_of(written->ty);
        auto offsets = ir::leaf_offsets_of(written->ty, layout);
        for (std::size_t at = 0; at < pieces.size() && at < offsets.size(); ++at) {
          auto place = place_of(address, span, { std::int64_t(offsets[at]), width_of(pieces[at]), is_signed(pieces[at]) });
          asm_.store(place, MCReg{ part(written, at, span), width_of(pieces[at]) }, inst->span);
        }
        return;
      }
      if (ir::is<ir::FloatType>(written->ty)) {
        // From a register of its own kind; a constant is read out of the image
        // first.
        auto place = place_of(address, span, { 0, bits_of(written->ty) });
        asm_.store(place, in_register(written, inst->span), inst->span);
        return;
      }
      auto place = place_of(address, span, { 0, width_of(written->ty), is_signed(written->ty) });
      MCOperand put;
      if (auto constant = number_of(written))
        put = MCImm{ constant->first, immediate_width(constant->first, width_of(written->ty)), is_signed(written->ty) };
      else
        // A store names how much of memory it writes, so it reads the view of
        // that width of wherever the value is.
        put = MCReg{ register_of(written, span), width_of(written->ty) };
      if (inst->ordering == ir::Ordering::release)
        asm_.release(place, put, inst->span);
      else
        asm_.store(place, put, inst->span);
    }


    void Lowering::lower_return(ir::Instruction* inst, std::optional<Span> span)
    {
      if (inst->operands.empty()) {
        asm_.ret(inst->span);
        return;
      }
      auto given = inst->operands[0];
      auto ty = given->ty;
      auto pieces = ir::leaves_of(ty);
      if (ir::made_of_parts(ty)) {
        // One register per part.
        std::vector<const mc::PhysReg*> places;
        try {
          places = result_places(cconv, ty);
        } catch (const TooManyArguments&) {
          throw UnsupportedOperation(std::format("answering with '{}', which wants more registers than the "
                                                 "convention answers in",
                                                 ty->written()),
                                     span);
        }
        std::vector<Reg> answering;
        for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at)
          answering.push_back(as_argument(places[at], pieces[at]));
        for (std::size_t at = 0; at < answering.size(); ++at)
          asm_.loadreg(answering[at], MCReg{ part(given, at, span) }, inst->span);
        asm_.ret(inst->span, answering);
        return;
      }
      if (ir::is<ir::FloatType>(ty)) {
        auto answer = result_register(ty, cconv);
        asm_.loadreg(answer, in_register(given, inst->span), inst->span);
        asm_.ret(inst->span, { answer });
        return;
      }
      if (! (ir::is<ir::IntType>(ty) || ir::is<ir::BoolType>(ty) || ir::is<ir::CharType>(ty) || ir::is<ir::EnumType>(ty)
             || ir::is<ir::PtrType>(ty)
             || ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty))) [[unlikely]]
        throw UnsupportedOperation(std::format("returning a value of type '{}'", ty->written()), span);
      auto result = result_register(ty, cconv);
      if (auto constant = number_of(given))
        asm_.loadreg(result, MCImm{ constant->first, std::max(32, width_of(ty)), is_signed(ty) }, inst->span);
      else
        asm_.loadreg(result, MCReg{ register_of(given, span) }, inst->span);
      asm_.ret(inst->span, { result });
    }


    void Lowering::to_float(const MCOperand& given, const ir::IntType* counted, int bits,
                            const mc::VirtReg* destination, Span span)
    {
      // The conversion takes a signed word, so a narrower number is widened by
      // its own sign first; a `u64` with its top bit set is halved, the bit
      // shifted out kept in the lowest place, converted and doubled.
      auto& r = registers();
      int width = width_of(counted);
      auto reg = std::get<MCReg>(given).reg;
      MCReg number{ reg, 64 };
      if (width < 64) {
        auto wide = r.info.new_virtual(r.gpr, 64);
        asm_.widen(wide, given, width, counted->is_signed, span);
        number = MCReg{ wide, 64 };
      }
      asm_.int_to_float(destination, number, bits, span);
      if (counted->is_signed || width < 64)
        return;
      auto done = asm_.reserve_label("fits.signed");
      asm_.branch(mc::Condition::sge, number, zero_immediate, done, span);
      auto halved = r.info.new_virtual(r.gpr, 64);
      auto lowest = r.info.new_virtual(r.gpr, 64);
      asm_.shift(mc::ops::shift_right, halved, number, MCImm{ 1, 32, false }, 64, span);
      asm_.op(mc::ops::and_, lowest, { number, MCImm{ 1, 32, false } }, span);
      asm_.op(mc::ops::or_, halved, { MCReg{ halved }, MCReg{ lowest } }, span);
      asm_.int_to_float(destination, MCReg{ halved, 64 }, bits, span);
      asm_.float_op(mc::ops::plus, destination, MCReg{ destination }, MCReg{ destination }, bits, span);
      asm_.block(done);
    }


    void Lowering::to_int(const MCOperand& given, int bits, const mc::VirtReg* limit,
                          const mc::VirtReg* destination, Span span)
    {
      // Asked only of a value with room, so the signed conversion to a word
      // answers every type but `u64`; from 2⁶³ up that one answers the smallest
      // signed word, and the number less 2⁶³ converted as well is kept, by a
      // mask made of that answer's sign, exactly where it was.  No branch.
      auto& r = registers();
      auto answer = r.info.new_virtual(r.gpr, 64);
      asm_.truncate_to_int(answer, given, bits, true, span);
      if (limit != nullptr) {
        auto moved = r.info.new_virtual(r.vec, float_register_bits);
        asm_.float_op(mc::ops::minus, moved, given, MCReg{ limit }, bits, span);
        auto upper = r.info.new_virtual(r.gpr, 64);
        asm_.truncate_to_int(upper, MCReg{ moved }, bits, true, span);
        auto sign = r.info.new_virtual(r.gpr, 64);
        asm_.shift(mc::ops::shift_right_signed, sign, MCReg{ answer }, MCImm{ 63, 32, false }, 64, span);
        asm_.op(mc::ops::and_, upper, { MCReg{ upper }, MCReg{ sign } }, span);
        asm_.op(mc::ops::or_, answer, { MCReg{ answer }, MCReg{ upper } }, span);
      }
      asm_.loadreg(destination, MCReg{ answer, destination->bits }, span);
    }


    bool Lowering::lower_binary(ir::Instruction* inst, std::optional<Span> span)
    {
      auto op = inst->bin;
      auto operand = [&](std::size_t at) { return inst->operands[at]; };
      if (is_carryless(op)) {
        // The same two halves of a product with no carries in it, with the
        // instruction where the program was built for one.
        auto& r = registers();
        auto high = r.info.new_virtual(r.gpr, 64);
        auto low = r.info.new_virtual(r.gpr, 64);
        held[inst] = high;
        extra[inst] = { low };
        auto left = value(operand(0), inst->span);
        auto right = value(operand(1), inst->span);
        lower_carryless(asm_, low, high, left, right, *this, inst->span);
        return true;
      }
      if (op == ir::BinOp::wide_mul) {
        // Both halves at once, the upper one first as the tuple has them: one
        // instruction answers both here.
        auto& r = registers();
        auto high = r.info.new_virtual(r.gpr, 64);
        auto low = r.info.new_virtual(r.gpr, 64);
        held[inst] = high;
        extra[inst] = { low };
        auto left = value(operand(0), inst->span);
        auto right = value(operand(1), inst->span);
        asm_.wide_product(low, high, left, right, is_signed(operand(0)->ty), inst->span);
        return true;
      }
      if (is_floating(inst->ty)) {
        auto result = ir::as<ir::ResultType>(inst->ty);
        auto answer = result != nullptr ? result->ok : inst->ty;
        const mc::Op* operation = nullptr;
        switch (op) {
        case ir::BinOp::add: operation = mc::ops::plus; break;
        case ir::BinOp::sub: operation = mc::ops::minus; break;
        case ir::BinOp::mul: operation = mc::ops::times; break;
        case ir::BinOp::fdiv: operation = mc::ops::divide; break;
        case ir::BinOp::smax: operation = mc::ops::larger; break;
        case ir::BinOp::smin: operation = mc::ops::smaller; break;
        default: break;
        }
        if (operation == nullptr) [[unlikely]]
          throw UnsupportedOperation(std::format("'{}' on a floating-point value", pl4g::describe(op)), span);
        auto destination = computed(inst, answer);
        auto divisor = in_register(operand(1), inst->span);
        const mc::VirtReg* failed = nullptr;
        if (result != nullptr) {
          // The one pair a floating-point division has no answer for is a
          // divisor of nought.
          failed = new_value(ir::bool_type(), layout, hint_if_returned(inst, ir::bool_type(), 1));
          extra[inst] = { failed };
          auto zero = floating_constant(0.0, answer, inst->span);
          asm_.float_compare(Condition::eq, failed, divisor, MCReg{ zero }, int(ir::as<ir::FloatType>(answer)->bits),
                             inst->span);
        }
        auto left = in_register(operand(0), inst->span);
        asm_.float_op(operation, destination, left, divisor, int(ir::as<ir::FloatType>(answer)->bits), inst->span);
        if (operation == mc::ops::larger || operation == mc::ops::smaller)
          return true;
        // An answer that is an infinity or a not-a-number is one the operation
        // did not have, and the program stops as for a sum that will not fit.
        auto carry_on = asm_.reserve_label("is.finite");
        if (failed != nullptr)
          asm_.branch(Condition::ne, MCReg{ failed }, zero_immediate, carry_on, inst->span);
        asm_.branch_if_finite(destination, int(ir::as<ir::FloatType>(answer)->bits), carry_on, inst->span);
        Told(*this, std::format("{} with no number for an answer", operation_name(op)), inst->span,
             statuses::NO_ANSWER)
          .out_of_range(asm_, inst->span);
        asm_.block(carry_on);
        return true;
      }
      if (ir::is<ir::PtrType>(inst->ty)) {
        // Arithmetic on an address is never checked: past its place is not a
        // place.
        auto destination = computed(inst, inst->ty);
        auto left = value(operand(0), inst->span);
        auto right = value(operand(1), inst->span);
        asm_.op(plain_op(op), destination, { left, right }, inst->span);
        return true;
      }
      int register_bits = std::max(32, width_of(inst->ty));
      if (is_trapping(op)) {
        auto destination = computed(inst, inst->ty);
        try {
          auto left = value(operand(0), inst->span);
          auto right = value(operand(1), inst->span);
          Told told(*this, std::format("{} that does not fit", operation_name(op)), inst->span);
          lower_trapping(asm_, op, inst->ty, left, right, destination, *this, register_bits, told, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return true;
      }
      if (is_saturating(op)) {
        auto destination = computed(inst, inst->ty);
        try {
          auto left = value(operand(0), inst->span);
          auto right = value(operand(1), inst->span);
          lower_saturating(asm_, op, inst->ty, left, right, destination, *this, register_bits, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return true;
      }
      if (is_shift(op)) {
        auto destination = computed(inst, inst->ty);
        try {
          auto left = value(operand(0), inst->span);
          auto right = value(operand(1), inst->span);
          Told told(*this, std::format("{} by more than the width of the type", operation_name(op)), inst->span);
          lower_shift(asm_, op, inst->ty, left, right, destination, *this, register_bits, told, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return true;
      }
      if (is_division(op)) {
        // A result: the number where there is one, and the truth value beside
        // it that says whether there is.
        auto result = ir::as<ir::ResultType>(inst->ty);
        auto answer = result != nullptr ? result->ok : inst->ty;
        auto destination = new_value(answer, layout, hint_if_returned(inst, answer));
        auto failed = new_value(ir::bool_type(), layout, hint_if_returned(inst, ir::bool_type(), 1));
        held[inst] = destination;
        extra[inst] = { failed };
        try {
          auto left = value(operand(0), inst->span);
          auto right = value(operand(1), inst->span);
          lower_division_result(asm_, op, answer, left, right, destination, failed, *this,
                                std::max(32, width_of(answer)), inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return true;
      }
      if (extremum_condition(op)) {
        auto destination = computed(inst, inst->ty);
        auto left = value(operand(0), inst->span);
        auto right = value(operand(1), inst->span);
        lower_extremum(asm_, op, left, right, destination, *this, inst->span);
        return true;
      }
      if (is_wrapping(op)) {
        auto destination = computed(inst, inst->ty);
        auto left = value(operand(0), inst->span);
        auto right = value(operand(1), inst->span);
        lower_wrapping(asm_, op, inst->ty, left, right, destination, register_bits, inst->span);
        return true;
      }
      auto operation = plain_op(op);
      if (operation == nullptr) [[unlikely]]
        throw UnsupportedOperation(std::format("the operation '{}'", pl4g::describe(op)), span);
      auto destination = computed(inst, inst->ty);
      auto left = value(operand(0), inst->span);
      auto right = value(operand(1), inst->span);
      asm_.op(operation, destination, { left, right }, inst->span);
      return true;
    }


    void Lowering::lower_call(ir::Instruction* inst, std::optional<Span> span)
    {
      auto callee = inst->callee;
      // A call through a value: the convention is the one the call carries,
      // which the value's type said, nothing here knowing which callee it is.
      ir::Value* through = callee == nullptr ? inst->operands[0] : nullptr;
      auto given = inst->arguments();
      const auto& [placed_by, going] = placed.at(inst);
      const auto& theirs = *placed_by;
      // The arguments are a parallel copy, for the reason a branch's are.
      // What goes on the stack is written first, into the room at the bottom
      // of the frame; that reads values and writes no register, so it is no
      // part of the copy.
      std::vector<Move> handed;
      for (std::size_t position = 0; position < given.size(); ++position) {
        auto argument = given[position];
        auto pieces = ir::leaves_of(argument->ty);
        if (ir::made_of_parts(argument->ty)) {
          for (std::size_t at = 0; at < pieces.size() && at < going[position].size(); ++at) {
            MCOperand source = MCReg{ part(argument, at, span) };
            if (auto slot = std::get_if<OnStack>(&going[position][at])) {
              pass_on_stack(asm_, *slot, pieces[at], source, inst->span);
              continue;
            }
            handed.push_back({ as_argument(std::get<const mc::PhysReg*>(going[position][at]), pieces[at]), source });
          }
          continue;
        }
        if (auto slot = std::get_if<OnStack>(&going[position][0])) {
          pass_on_stack(asm_, *slot, argument->ty, in_register(argument, inst->span), inst->span);
          continue;
        }
        handed.push_back({ as_argument(std::get<const mc::PhysReg*>(going[position][0]), argument->ty),
                           value(argument, inst->span) });
      }
      Reg called;
      if (through != nullptr) {
        // The register the callee is in is read by the call, so it goes into
        // the parallel copy with the arguments.
        called = asm_.temporary(register_of(through, inst->span));
        handed.push_back({ called, value(through, inst->span) });
      }
      for (const auto& move : sequenced(handed, [&](Reg like) { return asm_.temporary(like); }))
        asm_.loadreg(move.into, move.source, inst->span);
      auto destroyed = destroyed_by(through == nullptr ? callee : nullptr, theirs, registers().info, known_clobbers);
      std::vector<Reg> reads;
      for (const auto& move : handed)
        if (through == nullptr || move.into != called)
          reads.push_back(move.into);
      std::vector<Reg> clobbers(destroyed.begin(), destroyed.end());
      if (through != nullptr)
        asm_.call(MCOperand(MCReg{ called }), inst->span, clobbers, reads);
      else
        asm_.call(ir::symbol_name(*callee), inst->span, clobbers, reads);
      if (ir::made_of_parts(inst->ty)) {
        auto pieces = ir::leaves_of(inst->ty);
        std::vector<const mc::PhysReg*> places;
        try {
          places = result_places(theirs, inst->ty);
        } catch (const TooManyArguments&) {
          throw UnsupportedOperation(std::format("a call answering with '{}', which wants more registers than the "
                                                 "convention answers in",
                                                 inst->ty->written()),
                                     span);
        }
        std::vector<const mc::VirtReg*> taken;
        for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at) {
          auto into = new_value(pieces[at], layout);
          asm_.loadreg(into, MCReg{ as_argument(places[at], pieces[at]) }, inst->span);
          taken.push_back(into);
        }
        held[inst] = taken[0];
        extra[inst] = std::vector<const mc::VirtReg*>(taken.begin() + 1, taken.end());
      } else if (inst->ty != ir::void_type()) {
        // Wanted where this function answers if that is what becomes of it,
        // and otherwise where the call left it.
        auto destination = new_value(inst->ty, layout, result_register(inst->ty, inst == returned ? cconv : theirs));
        held[inst] = destination;
        asm_.loadreg(destination, MCReg{ result_register(inst->ty, theirs) }, inst->span);
      }
    }

  } // anonymous namespace


  void lower_function(mc::Assembler& asm_, ir::Function& func, const CallConvDesc& cconv,
                      mc::RegisterInfo& registers, Messages* messages, const SourceManager* sources,
                      Constants* constants, const Destroyed* known_clobbers, const ir::DataLayout& layout)
  {
    (void) registers;
    Lowering(asm_, func, cconv, messages, sources, constants, known_clobbers, layout).run();
  }

} // namespace pl4g::target::x86_64
