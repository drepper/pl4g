// Bringing an operation over a whole run of elements down to what a machine has.
#include "target/vectors.hh"
#include "target/emit.hh"

#include <unordered_map>


namespace pl4g::target {

  namespace {

    // How a run of `lanes` elements is cut up: pieces a power of two elements
    // long, greedily the largest that fits what is left.
    std::vector<std::uint64_t> pieces(std::uint64_t lanes, std::uint64_t stride, std::uint64_t at_once)
    {
      std::vector<std::uint64_t> found;
      auto left = lanes;
      while (left != 0) {
        std::uint64_t take = 1;
        for (auto size = at_once; size >= 1; size /= 2)
          if (size <= left && size * stride >= smallest_piece) {
            take = size;
            break;
          }
        found.push_back(take);
        left -= take;
      }
      return found;
    }

    bool is_vec(const ir::Value* value)
    {
      return ir::is<ir::VecType>(value->ty);
    }

    // Whether this instruction is about a run of elements held as one value.
    bool is_a_run(const ir::Instruction* inst)
    {
      if (ir::is<ir::VecType>(inst->ty))
        return true;
      for (auto operand : inst->operands)
        if (is_vec(operand))
          return true;
      return false;
    }


    // Rewrites one function's run-at-a-time operations.
    class Settler {
    public:
      Settler(ir::Module& module, ir::Function& func, const Vectors& able, const ir::DataLayout& layout)
        : module(module), func(func), able(able), layout(layout) {}

      void run()
      {
        for (auto& block : func.blocks)
          rewrite(*block);
      }

    private:
      using Value = ir::Value;
      using Inst = ir::Instruction;

      void rewrite(ir::BasicBlock& block)
      {
        made.clear();
        current = &block;
        plan(block);
        for (auto inst : block.insts) {
          if (one(inst))
            continue;
          settle_operands(inst);
          add(inst);
        }
        block.insts = std::move(made);
        made = {};
      }

      Inst* add(Inst* inst)
      {
        inst->parent = current;
        made.push_back(inst);
        return inst;
      }

      Inst* make(ir::InstKind kind, const ir::Type* ty, std::initializer_list<Value*> operands, Span span)
      {
        auto inst = func.make(kind, ty);
        inst->operands = operands;
        inst->span = span;
        return inst;
      }

      Value* fresh_u64(__int128 value)
      {
        return const_cast<ir::IntConst*>(module.constant<ir::IntConst>(ir::u64(), value));
      }

      void settle_operands(Inst* inst)
      {
        for (auto& operand : inst->operands)
          if (auto found = became.find(operand); found != became.end())
            operand = found->second;
      }

      void plan(const ir::BasicBlock& block)
      {
        cuts.clear();
        for (auto inst : block.insts)
          if ((inst->kind == ir::InstKind::BinaryInst || inst->kind == ir::InstKind::UnaryInst
               || inst->kind == ir::InstKind::CmpInst)
              && ir::is<ir::VecType>(inst->ty))
            spread(inst, cut_for(inst));
        // A run that reaches no operation is only being moved.
        for (auto inst : block.insts)
          if (inst->kind == ir::InstKind::StoreInst && is_vec(inst->operands[2]) && ! cuts.contains(inst->operands[2]))
            spread(inst, moving(ir::as<ir::VecType>(inst->operands[2]->ty)));
      }

      std::vector<std::uint64_t> moving(const ir::VecType* held)
      {
        auto stride = ir::stride_of(held->element, layout);
        std::uint64_t at_once = able.bits == 0 || stride == 0 ? 0 : able.bits / (stride * 8);
        return pieces(held->lanes, stride, at_once);
      }

      void spread(const Inst* inst, const std::vector<std::uint64_t>& cut)
      {
        cuts[inst] = cut;
        for (auto operand : inst->operands)
          if (is_vec(operand))
            cuts.emplace(operand, cut);
      }

      std::vector<std::uint64_t> cut_for(const Inst* inst)
      {
        auto held = ir::as<ir::VecType>(inst->ty);
        // A comparison reads something other than what it answers.
        auto read = ir::as<ir::VecType>(inst->operands[0]->ty);
        auto element = read != nullptr ? read->element : held->element;
        auto at_once = able.lanes_at_once(element, layout, widths(inst));
        return pieces(held->lanes, ir::stride_of(element, layout), at_once);
      }

      std::set<unsigned> widths(const Inst* inst) const
      {
        switch (inst->kind) {
        case ir::InstKind::BinaryInst:
          if (auto found = able.binary.find(inst->bin); found != able.binary.end())
            return found->second;
          return {};
        case ir::InstKind::UnaryInst:
          if (auto found = able.unary.find(inst->un); found != able.unary.end())
            return found->second;
          return {};
        case ir::InstKind::CmpInst:
          return able.compares;
        default:
          return {};
        }
      }

      std::vector<std::uint64_t> cut(const Value* value, std::uint64_t lanes) const
      {
        if (auto found = cuts.find(value); found != cuts.end())
          return found->second;
        return std::vector<std::uint64_t>(lanes, 1);
      }

      bool one(Inst* inst)
      {
        auto vec = ir::as<ir::VecType>(inst->ty);
        switch (inst->kind) {
        case ir::InstKind::SplatInst:
          if (vec == nullptr)
            return false;
          splat(inst, vec);
          return true;
        case ir::InstKind::LoadInst:
          if (vec == nullptr)
            return false;
          load(inst, vec);
          return true;
        case ir::InstKind::StoreInst:
          if (! is_vec(inst->operands[2]))
            return false;
          store(inst);
          return true;
        case ir::InstKind::BinaryInst:
          if (vec == nullptr)
            return false;
          binary(inst);
          return true;
        case ir::InstKind::UnaryInst:
          if (vec == nullptr)
            return false;
          unary(inst);
          return true;
        case ir::InstKind::CmpInst:
          if (vec == nullptr)
            return false;
          compare(inst);
          return true;
        case ir::InstKind::AnyLaneInst:
          if (! is_vec(inst->operands[0]))
            return false;
          any_lane(inst);
          return true;
        default:
          return false;
        }
      }

      const std::vector<Value*>& held(const Value* value) const
      {
        auto found = pieces_of.find(value);
        if (found == pieces_of.end()) [[unlikely]]
          throw InternalError("a run whose pieces were never made");
        return found->second;
      }

      const ir::Type* piece_type(const ir::Type* element, std::uint64_t lanes) const
      {
        return lanes == 1 ? element : ir::vec_type(element, unsigned(lanes));
      }

      void splat(Inst* inst, const ir::VecType* ty)
      {
        auto source = inst->operands[0];
        std::vector<Value*> pieces;
        for (auto lanes : cut(inst, ty->lanes))
          pieces.push_back(lanes == 1 ? source
                                      : add(make(ir::InstKind::SplatInst, ir::vec_type(ty->element, unsigned(lanes)),
                                                 { source }, inst->span)));
        pieces_of[inst] = std::move(pieces);
      }

      // Where the piece that starts at the `at`-th element is.
      Value* piece_place(Value* base, const ir::Type* element, std::uint64_t at, const ir::Type* held_type, Span span)
      {
        auto start_cast = make(ir::InstKind::CastInst, ir::ptr_type(element, true), { base }, span);
        start_cast->cast = ir::CastKind::bitcast;
        Value* start = add(start_cast);
        if (at != 0) {
          auto offset = fresh_u64(at * ir::stride_of(element, layout));
          auto sum = make(ir::InstKind::BinaryInst, start->ty, { start, offset }, span);
          sum->bin = ir::BinOp::add;
          start = add(sum);
        }
        auto placed = make(ir::InstKind::CastInst, ir::ptr_type(held_type, true), { start }, span);
        placed->cast = ir::CastKind::bitcast;
        return add(placed);
      }

      void load(Inst* inst, const ir::VecType* ty)
      {
        auto token = inst->operands[0];
        auto address = inst->operands[1];
        std::vector<Value*> pieces;
        std::uint64_t at = 0;
        for (auto lanes : cut(inst, ty->lanes)) {
          auto piece = piece_type(ty->element, lanes);
          auto place = piece_place(address, ty->element, at, piece, inst->span);
          pieces.push_back(add(make(ir::InstKind::LoadInst, piece, { token, place }, inst->span)));
          at += lanes;
        }
        pieces_of[inst] = std::move(pieces);
      }

      Inst* stored(Value* written, Value* place, Value* value, Span span)
      {
        return add(make(ir::InstKind::StoreInst, ir::mem_type(), { written, place, value }, span));
      }

      void store(Inst* inst)
      {
        auto ty = ir::as<ir::VecType>(inst->operands[2]->ty);
        auto address = inst->operands[1];
        Value* written = inst->operands[0];
        auto cuts_here = cut(inst->operands[2], ty->lanes);
        if (std::ranges::all_of(cuts_here, [](auto lanes) { return lanes == 1; }))
          if (auto done = store_repeated(inst, ty)) {
            became[inst] = done;
            return;
          }
        std::uint64_t at = 0;
        const auto& parts = held(inst->operands[2]);
        for (std::size_t i = 0; i < std::min(cuts_here.size(), parts.size()); ++i) {
          auto place = piece_place(address, ty->element, at, parts[i]->ty, inst->span);
          written = stored(written, place, parts[i], inst->span);
          at += cuts_here[i];
        }
        became[inst] = written;
      }

      // One number written into every element eight bytes at a time.
      Value* store_repeated(Inst* inst, const ir::VecType* ty)
      {
        auto source = inst->operands[2];
        auto element = ir::as<ir::IntType>(ty->element);
        if (source->vkind != ir::ValueKind::instruction)
          return nullptr;
        auto splatted = static_cast<Inst*>(source);
        if (splatted->kind != ir::InstKind::SplatInst || splatted->operands[0]->vkind != ir::ValueKind::int_const
            || element == nullptr || (element->bits != 8 && element->bits != 16 && element->bits != 32)
            || ir::stride_of(element, layout) * 8 != element->bits)
          return nullptr;
        std::uint64_t per = 64 / element->bits;
        std::uint64_t whole = ty->lanes / per;
        if (whole < 2)
          return nullptr;
        auto one = static_cast<std::uint64_t>(static_cast<const ir::IntConst*>(splatted->operands[0])->value)
                   & ((std::uint64_t(1) << element->bits) - 1);
        std::uint64_t pattern = 0;
        for (std::uint64_t lane = 0; lane < per; ++lane)
          pattern |= one << (lane * element->bits);
        Value* written = inst->operands[0];
        auto address = inst->operands[1];
        for (std::uint64_t word = 0; word < whole; ++word) {
          auto place = piece_place(address, element, word * per, ir::u64(), inst->span);
          written = stored(written, place, fresh_u64(pattern), inst->span);
        }
        for (std::uint64_t at = whole * per; at < ty->lanes; ++at) {
          auto place = piece_place(address, element, at, element, inst->span);
          auto value = const_cast<ir::IntConst*>(module.constant<ir::IntConst>(element, one));
          written = stored(written, place, value, inst->span);
        }
        return written;
      }

      void binary(Inst* inst)
      {
        const auto& left = held(inst->operands[0]);
        const auto& right = held(inst->operands[1]);
        std::vector<Value*> pieces;
        for (std::size_t i = 0; i < std::min(left.size(), right.size()); ++i) {
          auto made_one = make(ir::InstKind::BinaryInst, left[i]->ty, { left[i], right[i] }, inst->span);
          made_one->bin = inst->bin;
          pieces.push_back(add(made_one));
        }
        pieces_of[inst] = std::move(pieces);
      }

      void unary(Inst* inst)
      {
        std::vector<Value*> pieces;
        for (auto one : held(inst->operands[0])) {
          auto made_one = make(ir::InstKind::UnaryInst, one->ty, { one }, inst->span);
          made_one->un = inst->un;
          pieces.push_back(add(made_one));
        }
        pieces_of[inst] = std::move(pieces);
      }

      void compare(Inst* inst)
      {
        const auto& left = held(inst->operands[0]);
        const auto& right = held(inst->operands[1]);
        std::vector<Value*> pieces;
        for (std::size_t i = 0; i < std::min(left.size(), right.size()); ++i) {
          auto lanes = ir::as<ir::VecType>(left[i]->ty);
          const ir::Type* answer = lanes == nullptr ? static_cast<const ir::Type*>(ir::bool_type())
                                                    : ir::vec_type(ir::bool_type(), lanes->lanes);
          auto made_one = make(ir::InstKind::CmpInst, answer, { left[i], right[i] }, inst->span);
          made_one->pred = inst->pred;
          pieces.push_back(add(made_one));
        }
        pieces_of[inst] = std::move(pieces);
      }

      void any_lane(Inst* inst)
      {
        Value* found = nullptr;
        for (auto piece : held(inst->operands[0])) {
          Value* one = ! is_vec(piece) ? piece
                                       : add(make(ir::InstKind::AnyLaneInst, ir::bool_type(), { piece }, inst->span));
          if (found == nullptr)
            found = one;
          else {
            auto either = make(ir::InstKind::BinaryInst, found->ty, { found, one }, inst->span);
            either->bin = ir::BinOp::or_;
            found = add(either);
          }
        }
        if (found == nullptr) [[unlikely]]
          throw InternalError("a run of no pieces");
        became[inst] = found;
      }

      ir::Module& module;
      ir::Function& func;
      const Vectors& able;
      const ir::DataLayout& layout;
      std::unordered_map<const Value*, std::vector<Value*>> pieces_of;
      std::unordered_map<const Value*, Value*> became;
      std::vector<Inst*> made;
      ir::BasicBlock* current = nullptr;
      std::unordered_map<const Value*, std::vector<std::uint64_t>> cuts;
    };

  } // anonymous namespace


  std::uint64_t Vectors::lanes_at_once(const ir::Type* element, const ir::DataLayout& layout,
                                       const std::set<unsigned>& widths) const
  {
    if (bits == 0 || widths.empty())
      return 0;
    // A type narrower than what holds it is not a lane.
    if (auto integer = ir::as<ir::IntType>(element); integer != nullptr && ! integer->whole())
      return 0;
    auto wide = ir::stride_of(element, layout) * 8;
    if (! widths.contains(unsigned(wide)))
      return 0;
    return bits / wide;
  }


  void settle(ir::Module& module, const Vectors& able, const ir::DataLayout& layout)
  {
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      bool any = false;
      for (const auto& block : func->blocks)
        for (auto inst : block->insts)
          any = any || is_a_run(inst);
      if (any)
        Settler(module, *func, able, layout).run();
    }
  }

} // namespace pl4g::target
