#include "ir/builder.hh"

#include <algorithm>

#include "ir/layout.hh"


namespace pl4g::ir {

  namespace {

    // How a copy is cut up: the widest piece that fits, and that the
    // alignment allows.
    constexpr std::pair<unsigned, std::uint64_t> chunks[] = { { 64, 8 }, { 32, 4 }, { 16, 2 }, { 8, 1 } };

    DataLayout host_layout(const Module& module)
    {
      DataLayout layout;
      layout.frames = &module.frame_sizes;
      return layout;
    }

  } // anonymous namespace


  Instruction* Builder::append(Instruction* inst)
  {
    if (block_ == nullptr || block_->terminator() != nullptr)
      return inst;
    inst->parent = block_;
    block_->insts.push_back(inst);
    return inst;
  }


  Instruction* Builder::make(InstKind kind, const Type* ty, std::initializer_list<Value*> operands, Span span)
  {
    auto inst = func.make(kind, ty);
    inst->operands.assign(operands.begin(), operands.end());
    inst->span = span;
    return inst;
  }


  Value* Builder::binary(BinOp op, Value* lhs, Value* rhs, Span span, const Type* ty)
  {
    auto inst = make(InstKind::BinaryInst, ty != nullptr ? ty : lhs->ty, { lhs, rhs }, span);
    inst->bin = op;
    return append(inst);
  }


  Value* Builder::wrap(Value* value, Value* failed, const Type* result, Span span, Value* error)
  {
    auto inst = make(InstKind::WrapInst, result, { value, failed }, span);
    if (error != nullptr)
      inst->operands.push_back(error);
    return append(inst);
  }


  Value* Builder::make_tuple(std::span<Value* const> values, const Type* ty, Span span)
  {
    auto inst = make(InstKind::TupleInst, ty, {}, span);
    inst->operands.assign(values.begin(), values.end());
    return append(inst);
  }


  Value* Builder::extract(Value* value, std::int64_t index, const Type* ty, Span span)
  {
    auto inst = make(InstKind::ExtractInst, ty, { value }, span);
    inst->index = index;
    return append(inst);
  }


  std::vector<Value*> Builder::leaves(Value* value, const Type* ty, Span span)
  {
    auto pieces = parts_of(ty);
    if (pieces.size() == 1 && pieces[0] == ty)
      return { value };
    if (auto result = as<ResultType>(ty)) {
      auto found = leaves(unwrap(value, result->ok, span), result->ok, span);
      found.push_back(failed(value, span));
      if (result->err != nullptr)
        std::ranges::copy(leaves(error(value, result->err, span), result->err, span), std::back_inserter(found));
      return found;
    }
    std::vector<Value*> out;
    for (std::size_t at = 0; at < pieces.size(); ++at)
      out.push_back(extract(value, std::int64_t(at), pieces[at], span));
    return out;
  }


  Value* Builder::whole(std::span<Value* const> parts, const Type* ty, Span span)
  {
    auto pieces = parts_of(ty);
    if (pieces.size() == 1 && pieces[0] == ty)
      return parts[0];
    if (auto result = as<ResultType>(ty)) {
      auto taken = parts_of(result->ok).size();
      auto answer = whole(parts.first(taken), result->ok, span);
      return wrap(answer, parts[taken], ty, span,
                  result->err == nullptr ? nullptr : whole(parts.subspan(taken + 1), result->err, span));
    }
    return make_tuple(parts, ty, span);
  }


  Value* Builder::unwrap(Value* value, const Type* ok, Span span)
  {
    return append(make(InstKind::UnwrapInst, ok, { value }, span));
  }


  Value* Builder::failed(Value* value, Span span)
  {
    return append(make(InstKind::FailedInst, bool_type(), { value }, span));
  }


  Value* Builder::error(Value* value, const Type* err, Span span)
  {
    return append(make(InstKind::ErrorInst, err, { value }, span));
  }


  Value* Builder::unary(UnOp op, Value* value, const Type* ty, Span span)
  {
    auto inst = make(InstKind::UnaryInst, ty != nullptr ? ty : value->ty, { value }, span);
    inst->un = op;
    return append(inst);
  }


  Value* Builder::select(Value* cond, Value* if_true, Value* if_false, Span span)
  {
    return append(make(InstKind::SelectInst, if_true->ty, { cond, if_true, if_false }, span));
  }


  Value* Builder::compare(CmpPred pred, Value* lhs, Value* rhs, Span span)
  {
    const Type* answer = bool_type();
    if (auto vec = as<VecType>(lhs->ty))
      answer = vec_type(bool_type(), vec->lanes);
    auto inst = make(InstKind::CmpInst, answer, { lhs, rhs }, span);
    inst->pred = pred;
    return append(inst);
  }


  Value* Builder::splat(Value* value, const Type* target, Span span)
  {
    return append(make(InstKind::SplatInst, target, { value }, span));
  }


  Value* Builder::any_lane(Value* value, Span span)
  {
    return append(make(InstKind::AnyLaneInst, bool_type(), { value }, span));
  }


  Value* Builder::call(Function* callee, std::span<Value* const> args, const Type* result, Span span)
  {
    auto inst = make(InstKind::CallInst, result, {}, span);
    inst->callee = callee;
    inst->operands.assign(args.begin(), args.end());
    return append(inst);
  }


  Value* Builder::cast(CastKind kind, Value* value, const Type* target, Span span)
  {
    auto inst = make(InstKind::CastInst, target, { value }, span);
    inst->cast = kind;
    return append(inst);
  }


  Value* Builder::memory()
  {
    if (memory_ == nullptr) {
      auto start = func.make(InstKind::MemStartInst, mem_type());
      auto entry = func.entry();
      start->parent = entry;
      entry->insts.insert(entry->insts.begin(), start);
      memory_ = start;
    }
    return memory_;
  }


  Value* Builder::frame(const Type* held, Span span)
  {
    auto inst = make(InstKind::FrameInst, ptr_type(held, true), {}, span);
    inst->held = held;
    return append(inst);
  }


  Value* Builder::size_of(const Type* held, Span span)
  {
    auto inst = make(InstKind::SizeOfInst, u64(), {}, span);
    inst->held = held;
    return append(inst);
  }


  Value* Builder::check(Value* condition, std::string what, int status, Span span, bool observing)
  {
    auto inst = make(InstKind::AssertInst, void_type(), { condition }, span);
    inst->what = std::move(what);
    inst->status = status;
    inst->observing = observing;
    return append(inst);
  }


  Value* Builder::address(Value* var, Span span)
  {
    return append(make(InstKind::AddressInst, var->ty, { var }, span));
  }


  Value* Builder::code_address(Function* callee, Span span)
  {
    auto inst = make(InstKind::CodeInst, ptr_type(u8(), true), {}, span);
    inst->callee = callee;
    return append(inst);
  }


  Value* Builder::load(Value* address, Span span, Ordering ordering)
  {
    auto pointee = static_cast<const PtrType*>(address->ty)->pointee;
    if (held_in_memory(pointee)) {
      auto room = frame(pointee, span);
      copy(room, address, pointee, span);
      return cast(CastKind::bitcast, room, pointee, span);
    }
    auto inst = make(InstKind::LoadInst, pointee, { memory(), address }, span);
    inst->ordering = ordering;
    return append(inst);
  }


  Value* Builder::store(Value* address, Value* value, Span span, Ordering ordering)
  {
    if (held_in_memory(value->ty))
      return copy(address, value, value->ty, span);
    auto inst = make(InstKind::StoreInst, mem_type(), { memory(), address, value }, span);
    inst->ordering = ordering;
    auto written = append(inst);
    memory_ = written;
    return written;
  }


  Value* Builder::copy(Value* into, Value* from, const Type* ty, Span span)
  {
    auto size = ir::size_of(ty, host_layout(module_));
    auto widest = std::max<std::uint64_t>(1, align_of(ty, host_layout(module_)));
    std::uint64_t at = 0;
    while (at < size) {
      auto [bits, width] = *std::ranges::find_if(chunks, [&](auto one) {
        return one.second <= size - at && one.second <= widest;
      });
      const Type* part = int_type(bits, false);
      auto read = append(make(InstKind::LoadInst, part, { memory(), piece(from, part, at, span) }, span));
      memory_ = append(make(InstKind::StoreInst, mem_type(), { memory(), piece(into, part, at, span), read }, span));
      at += width;
    }
    return memory();
  }


  Value* Builder::piece(Value* place, const Type* part, std::uint64_t offset, Span span)
  {
    auto where = cast(CastKind::bitcast, place, ptr_type(part, true), span);
    if (offset == 0)
      return where;
    return binary(BinOp::add, where, int_const(u64(), offset), span);
  }


  Value* Builder::syscall(Value* number, std::span<Value* const> args, const Type* result, Span span)
  {
    auto inst = make(InstKind::SyscallInst, result, { number }, span);
    inst->operands.insert(inst->operands.end(), args.begin(), args.end());
    return append(inst);
  }


  Instruction* Builder::ret(Value* value, Span span)
  {
    auto inst = make(InstKind::RetInst, void_type(), {}, span);
    if (value != nullptr)
      inst->operands.push_back(value);
    return append(inst);
  }


  Instruction* Builder::br(BasicBlock* block, std::span<Value* const> args, Span span)
  {
    auto inst = make(InstKind::BrInst, void_type(), {}, span);
    inst->targets.push_back({ block, { args.begin(), args.end() } });
    return append(inst);
  }


  Instruction* Builder::condbr(Value* cond, BasicBlock* if_true, BasicBlock* if_false,
                               std::span<Value* const> true_args, std::span<Value* const> false_args, Span span)
  {
    auto inst = make(InstKind::CondBrInst, void_type(), { cond }, span);
    inst->targets.push_back({ if_true, { true_args.begin(), true_args.end() } });
    inst->targets.push_back({ if_false, { false_args.begin(), false_args.end() } });
    return append(inst);
  }


  Instruction* Builder::switch_(Value* value, std::span<const std::pair<__int128, BasicBlock*>> cases,
                                BasicBlock* otherwise, Span span)
  {
    auto inst = make(InstKind::SwitchInst, void_type(), { value }, span);
    for (auto [number, block] : cases) {
      inst->cases.push_back(number);
      inst->targets.push_back({ block, {} });
    }
    inst->targets.push_back({ otherwise, {} });
    return append(inst);
  }


  Instruction* Builder::yield(BasicBlock* block, Span span, bool delegates)
  {
    auto inst = make(InstKind::YieldInst, void_type(), {}, span);
    inst->targets.push_back({ block, {} });
    inst->delegates = delegates;
    return append(inst);
  }


  Instruction* Builder::unreachable(Span span)
  {
    return append(make(InstKind::UnreachableInst, void_type(), {}, span));
  }


  // -- rewriting --------------------------------------------------------------

  void stands_for(Function& func, Value* gone, Value* instead)
  {
    for (auto& block : func.blocks)
      for (auto inst : block->insts) {
        std::ranges::replace(inst->operands, gone, instead);
        for (auto& target : inst->targets)
          std::ranges::replace(target.args, gone, instead);
      }
  }


  void all_stand_for(Function& func, const std::unordered_map<const Value*, Value*>& instead)
  {
    if (instead.empty())
      return;
    auto replace = [&](Value*& v) {
      if (auto it = instead.find(v); it != instead.end())
        v = it->second;
    };
    for (auto& block : func.blocks)
      for (auto inst : block->insts) {
        for (auto& one : inst->operands)
          replace(one);
        for (auto& target : inst->targets)
          for (auto& arg : target.args)
            replace(arg);
      }
  }


  std::unordered_map<const BasicBlock*, std::vector<std::pair<BasicBlock*, BlockTarget*>>>
  incoming_edges(Function& func)
  {
    std::unordered_map<const BasicBlock*, std::vector<std::pair<BasicBlock*, BlockTarget*>>> found;
    for (auto& block : func.blocks)
      found[block.get()];
    for (auto& block : func.blocks) {
      auto last = block->terminator();
      if (last == nullptr)
        continue;
      for (auto& target : last->targets)
        found[target.block].emplace_back(block.get(), &target);
    }
    return found;
  }


  std::vector<std::pair<BlockParam*, Value*>> carried_values(const BlockTarget& target)
  {
    std::vector<std::pair<BlockParam*, Value*>> out;
    for (std::size_t i = 0; i < target.block->params.size() && i < target.args.size(); ++i)
      if (! is<MemType>(target.block->params[i]->ty))
        out.emplace_back(target.block->params[i].get(), target.args[i]);
    return out;
  }


  std::string mangle(std::string_view name, const FuncType& ty, std::string_view module)
  {
    std::string out;
    if (! module.empty()) {
      out += module;
      out += '.';
    }
    out += name;
    out += '(';
    for (std::size_t i = 0; i < ty.params.size(); ++i)
      out += (i > 0 ? "," : "") + ty.params[i]->mangled();
    out += ')';
    return out + ty.ret->mangled();
  }


  std::string symbol_name(const Function& func)
  {
    if (func.attrs.external)
      return *func.attrs.external;
    if (func.attrs.abi)
      return func.name;
    return mangle(func.name, *func.fty, func.module);
  }

} // namespace pl4g::ir
