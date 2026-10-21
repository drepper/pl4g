#include "opt/passes.hh"

#include <chrono>

#include <algorithm>
#include <unordered_map>
#include <unordered_set>

#include "diag/engine.hh"
#include "ir/builder.hh"
#include "ir/layout.hh"
#include "ir/verify.hh"
#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // How things are laid out on the machine the passes lay them out for,
    // with the sizes of the generators' frames measured so far.
    const Module* laid_out = nullptr;

    DataLayout host()
    {
      DataLayout layout;
      if (laid_out != nullptr)
        layout.frames = &laid_out->frame_sizes;
      return layout;
    }

    // Whether a call does something beyond answering: what its callee says,
    // and for a call through a value, what has to be assumed.
    bool call_has_effects(const Instruction* call)
    {
      if (call->callee == nullptr)
        return true;
      const auto& attrs = call->callee->attrs;
      return attrs.answer_in_storage || attrs.impure || attrs.opaque;
    }

    // Every value read anywhere in `func`.
    std::unordered_set<const Value*> read_in(const Function& func)
    {
      std::unordered_set<const Value*> used;
      for (const auto& block : func.blocks)
        for (auto inst : block->insts) {
          used.insert(inst->operands.begin(), inst->operands.end());
          for (const auto& target : inst->targets)
            used.insert(target.args.begin(), target.args.end());
        }
      return used;
    }

    // Splice `made` into `block` in place of the instruction at `at`.
    void splice(BasicBlock* block, std::size_t at, const std::vector<Instruction*>& made)
    {
      for (auto one : made)
        one->parent = block;
      block->insts.erase(block->insts.begin() + std::ptrdiff_t(at));
      block->insts.insert(block->insts.begin() + std::ptrdiff_t(at), made.begin(), made.end());
    }

    void prepend(BasicBlock* block, const std::vector<Instruction*>& made)
    {
      for (auto one : made)
        one->parent = block;
      block->insts.insert(block->insts.begin(), made.begin(), made.end());
    }

    std::size_t index_of(const BasicBlock* block, const Instruction* inst)
    {
      return std::size_t(std::ranges::find(block->insts, inst) - block->insts.begin());
    }

    // A new instruction of `func`, its operands given.
    Instruction* made(Function& func, InstKind kind, const Type* ty, std::vector<Value*> operands, Span span = {})
    {
      auto inst = func.make(kind, ty);
      inst->operands = std::move(operands);
      inst->span = span;
      return inst;
    }

    Instruction* bitcast(Function& func, Value* value, const Type* ty, Span span = {})
    {
      auto inst = made(func, InstKind::CastInst, ty, { value }, span);
      inst->cast = CastKind::bitcast;
      return inst;
    }

    Instruction* extract(Function& func, Value* value, std::int64_t index, const Type* ty, Span span = {})
    {
      auto inst = made(func, InstKind::ExtractInst, ty, { value }, span);
      inst->index = index;
      return inst;
    }

    std::uint64_t round_up(std::uint64_t value, std::uint64_t to)
    {
      return (value + to - 1) / to * to;
    }

  } // anonymous namespace


  // -- dropignored --------------------------------------------------------------

  bool drop_ignored_calls(Module& module)
  {
    // A call whose answer nothing reads and which changes nothing that
    // outlives it is not made; what it was the only reader of may go next.
    bool touched = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      while (true) {
        auto used = read_in(*func);
        bool gone = false;
        for (auto& block : func->blocks) {
          std::vector<Instruction*> kept;
          for (auto inst : block->insts)
            if (inst->kind != InstKind::CallInst || call_has_effects(inst) || used.contains(inst))
              kept.push_back(inst);
            else
              module.record("drop-call", inst->callee != nullptr ? inst->callee->name : "a call",
                            "nothing reads what it answers with and it changes nothing that outlives the call, so "
                              + func->name + " does not make it",
                            inst->span);
          if (kept.size() != block->insts.size()) {
            block->insts = std::move(kept);
            gone = true;
          }
        }
        if (! gone)
          break;
        touched = true;
      }
    }
    return touched;
  }


  // -- dropunreached ------------------------------------------------------------

  namespace {

    void addresses_in(const Value* held, std::vector<const GlobalVar*>& out)
    {
      if (held == nullptr)
        return;
      switch (held->vkind) {
      case ValueKind::address_const:
        if (auto target = static_cast<const AddressConst*>(held)->target;
            target != nullptr && target->vkind == ValueKind::global)
          out.push_back(static_cast<const GlobalVar*>(target));
        return;
      case ValueKind::parts_const:
        for (auto part : static_cast<const PartsConst*>(held)->parts)
          addresses_in(part, out);
        return;
      case ValueKind::array_const:
        for (auto part : static_cast<const ArrayConst*>(held)->elements)
          addresses_in(part, out);
        return;
      case ValueKind::record_const:
        for (auto part : static_cast<const RecordConst*>(held)->fields)
          addresses_in(part, out);
        return;
      case ValueKind::result_const:
        addresses_in(static_cast<const ResultConst*>(held)->answer, out);
        return;
      case ValueKind::sum_const:
        addresses_in(static_cast<const SumConst*>(held)->part, out);
        return;
      default:
        return;
      }
    }

  } // anonymous namespace


  bool drop_unreached(Module& module)
  {
    // The roots: what the entry point calls, and what is exported.
    std::vector<Function*> queue = roots(module);
    std::unordered_set<const Function*> seen;
    while (! queue.empty()) {
      auto func = queue.back();
      queue.pop_back();
      if (! seen.insert(func).second)
        continue;
      for (auto& block : func->blocks)
        for (auto inst : block->insts)
          if (auto named = named_by(inst))
            queue.push_back(named);
    }
    bool changed = false;
    std::vector<std::pair<std::string, std::unique_ptr<Function>>> kept;
    for (auto& [key, func] : module.functions)
      if (seen.contains(func.get()))
        kept.emplace_back(key, std::move(func));
      else {
        changed = true;
        if (! func->is_declaration())
          module.record("drop-function", func->name,
                        "nothing the program can run reaches it, and it is not exported, so nothing outside can "
                        "reach it either",
                        func->name_span.is_valid() ? func->name_span : func->span);
        // Kept alive, if not listed: a dropped function may still be named by
        // a constant somewhere.
        module.unlisted.push_back(std::move(func));
      }
    module.functions = std::move(kept);

    // The variables what is left names, and what their values point at.
    std::unordered_set<const GlobalVar*> named;
    for (auto& [_, func] : module.functions)
      for (auto& block : func->blocks)
        for (auto inst : block->insts)
          for (auto place : inst->operands)
            if (place->vkind == ValueKind::global)
              named.insert(static_cast<const GlobalVar*>(place));
    std::vector<const GlobalVar*> waiting;
    for (auto& [_, var] : module.globals)
      if (named.contains(var.get()) || var->linkage == Linkage::visible)
        waiting.push_back(var.get());
    while (! waiting.empty()) {
      auto var = waiting.back();
      waiting.pop_back();
      std::vector<const GlobalVar*> targets;
      addresses_in(var->initializer, targets);
      for (auto target : targets)
        if (named.insert(target).second)
          waiting.push_back(target);
    }
    std::vector<std::pair<std::string, std::unique_ptr<GlobalVar>>> variables;
    bool dropped = false;
    for (auto& [key, var] : module.globals)
      if (named.contains(var.get()) || var->linkage == Linkage::visible)
        variables.emplace_back(key, std::move(var));
      else {
        dropped = true;
        if (! var->name.starts_with("__pl4g_"))
          module.record("drop-variable", var->name, "no function that is itself reached names it, and it is not exported",
                        var->name_span.is_valid() ? var->name_span : var->span);
        module.retired_globals.push_back(std::move(var));
      }
    if (dropped) {
      changed = true;
      module.globals = std::move(variables);
    } else
      module.globals = std::move(variables);
    return changed;
  }


  // -- tailcalls ----------------------------------------------------------------

  namespace {

    // Whether the branch or return `terminator` ends the function answering
    // `value`, through blocks that do nothing but hand it on.
    bool returns(const Instruction* terminator, const Value* value, std::unordered_set<const BasicBlock*>& seen)
    {
      if (terminator->kind == InstKind::RetInst) {
        if (terminator->operands.empty())
          return is<VoidType>(value->ty);
        return terminator->operands[0] == value;
      }
      if (terminator->kind != InstKind::BrInst)
        return false;
      const auto& target = terminator->targets[0];
      auto block = target.block;
      if (seen.contains(block) || block->insts.size() != 1)
        return false;
      seen.insert(block);
      if (is<VoidType>(value->ty))
        return returns(block->insts[0], value, seen);
      for (std::size_t at = 0; at < target.args.size(); ++at)
        if (target.args[at] == value && at < block->params.size()
            && returns(block->insts[0], block->params[at].get(), seen))
          return true;
      return false;
    }

    bool reaches_frame(const Value* value, std::unordered_set<const Value*>& seen)
    {
      if (! seen.insert(value).second)
        return false;
      if (value->vkind != ValueKind::instruction)
        return false;
      auto inst = static_cast<const Instruction*>(value);
      if (inst->kind == InstKind::FrameInst)
        return true;
      return std::ranges::any_of(inst->operands, [&](auto one) { return reaches_frame(one, seen); });
    }

    const char* why_not(const BasicBlock* block, std::size_t at, const Instruction* call)
    {
      if (block->insts.size() - at - 1 != 1)
        return "something is done after it before the function returns";
      std::unordered_set<const BasicBlock*> seen;
      if (! returns(block->insts[at + 1], call, seen))
        return "something is done with what it answers, or runs after it before the function returns -- a deferred "
               "statement, a post-condition";
      std::unordered_set<const Value*> visited;
      if (std::ranges::any_of(call->operands, [&](auto arg) { return reaches_frame(arg, visited); }))
        return "it hands over a reference into the call's own storage, which the next turn would reuse";
      return nullptr;
    }

    // The memory token in force at `at` in `block`, looking back through the
    // blocks that branch to it as far as the head the loop goes back to.
    Value* token_at(Function& func, BasicBlock* block, std::size_t at, Value* head_token)
    {
      std::unordered_set<const BasicBlock*> seen;
      while (true) {
        for (std::size_t i = at; i-- > 0;)
          if (is<MemType>(block->insts[i]->ty))
            return block->insts[i];
        for (std::size_t i = block->params.size(); i-- > 0;)
          if (is<MemType>(block->params[i]->ty))
            return block->params[i].get();
        if (block == func.blocks[1].get() || seen.contains(block))
          return head_token;
        seen.insert(block);
        BasicBlock* before = nullptr;
        for (auto& other : func.blocks) {
          if (other->insts.empty())
            continue;
          auto last = other->insts.back();
          if (std::ranges::any_of(last->targets, [&](const auto& t) { return t.block == block; })) {
            before = other.get();
            break;
          }
        }
        if (before == nullptr)
          return head_token;
        block = before;
        at = before->insts.size() - 1;
      }
    }

  } // anonymous namespace


  bool tail_calls(Module& module)
  {
    // A function that calls itself last does so by jumping back to its start.
    bool touched = false;
    for (auto& [_, func] : module.functions) {
      if (func->blocks.empty())
        continue;
      std::vector<std::pair<BasicBlock*, Instruction*>> found;
      for (auto& block : func->blocks)
        for (std::size_t at = 0; at < block->insts.size(); ++at) {
          auto inst = block->insts[at];
          if (inst->kind != InstKind::CallInst || inst->callee != func.get())
            continue;
          if (auto why = why_not(block.get(), at, inst))
            module.record("self-call", func->name,
                          "'" + func->name + "' calls itself here and the call takes stack: " + why,
                          inst->span.is_valid() ? inst->span : func->span);
          else
            found.emplace_back(block.get(), inst);
        }
      if (found.empty())
        continue;
      touched = true;
      BasicBlock* head = func->blocks[0].get();
      auto entry_owner = func->detached_block(func->unique_label("entry"));
      auto entry = entry_owner.get();
      std::vector<Value*> handed;
      for (const auto& param : head->params) {
        auto made_param = entry->add_param(param->ty);
        made_param->name_hint = param->name_hint;
        made_param->name_span = param->name_span;
        handed.push_back(made_param);
      }
      std::vector<Instruction*> made_insts;
      Value* token = nullptr;
      auto start = std::ranges::find_if(head->insts, [](auto i) { return i->kind == InstKind::MemStartInst; });
      Instruction* start_inst = nullptr;
      if (start != head->insts.end()) {
        start_inst = *start;
        head->insts.erase(start);
        auto mem = head->add_param(mem_type());
        mem->name_hint = "mem";
        token = mem;
        start_inst->parent = entry;
        made_insts.push_back(start_inst);
        stands_for(*func, start_inst, mem);
      }
      std::vector<Value*> args = handed;
      if (start_inst != nullptr)
        args.push_back(start_inst);
      auto jump = func->make(InstKind::BrInst, void_type());
      jump->targets.push_back({ head, args });
      made_insts.push_back(jump);
      for (auto one : made_insts)
        one->parent = entry;
      entry->insts = std::move(made_insts);
      func->blocks.insert(func->blocks.begin(), std::move(entry_owner));
      for (auto [block, call] : found) {
        auto at = index_of(block, call);
        std::vector<Value*> carried = call->operands;
        if (token != nullptr)
          carried.push_back(token_at(*func, block, at, token));
        auto back = func->make(InstKind::BrInst, void_type());
        back->span = call->span;
        back->targets.push_back({ head, carried });
        back->parent = block;
        block->insts.resize(at);
        block->insts.push_back(back);
        module.record("tail-call", func->name,
                      "'" + func->name + "' calls itself last here: the call is a jump back to its start, and takes "
                      "no stack",
                      call->span.is_valid() ? call->span : func->span);
      }
    }
    return touched;
  }


  // -- largeanswers -------------------------------------------------------------

  namespace {

    bool in_registers(ReturnStyle style, const Type* ty)
    {
      if (held_in_memory(ty))
        return false;
      return leaves_of(ty).size() <= (style == ReturnStyle::two_in_registers ? 2 : 3);
    }

    bool through_storage(const Function& func)
    {
      const Type* ret = func.fty->ret;
      if (is<VoidType>(ret) || in_registers(func.answer_style(), ret))
        return false;
      if (held_in_memory(ret))
        return true;
      return std::ranges::all_of(leaves_of(ret), [](auto leaf) { return parts_of(leaf).size() == 1; });
    }

    // The memory token in force before `at`, starting the chain at the top
    // of the function where nothing has.
    Value* token_before(Function& func, BasicBlock* block, std::size_t at)
    {
      for (std::size_t i = at; i-- > 0;)
        if (is<MemType>(block->insts[i]->ty))
          return block->insts[i];
      for (const auto& param : block->params)
        if (is<MemType>(param->ty))
          return param.get();
      auto entry = func.blocks[0].get();
      if (! entry->insts.empty() && entry->insts[0]->kind == InstKind::MemStartInst)
        return entry->insts[0];
      auto start = func.make(InstKind::MemStartInst, mem_type());
      start->parent = entry;
      entry->insts.insert(entry->insts.begin(), start);
      return start;
    }

    std::vector<std::uint64_t> answer_offsets(const Type* answer)
    {
      if (auto result = as<ResultType>(answer))
        return { 0, tag_offset_of(result, host()), error_offset_of(result, host()) };
      if (! is<TupleType>(answer) && ! is<ProductType>(answer)) {
        std::vector<std::uint64_t> out;
        for (std::size_t at = 0; at < parts_of(answer).size(); ++at)
          out.push_back(8 * at);
        return out;
      }
      return part_offsets_of(answer, host());
    }

    // `place`, as a pointer to `part`, `offset` bytes in.
    Value* at_in(Function& func, Value* place, const Type* part, std::uint64_t offset, std::vector<Instruction*>& out,
                 Span span, Module& module)
    {
      auto base = bitcast(func, place, ptr_type(part, true), span);
      out.push_back(base);
      if (offset == 0)
        return base;
      auto moved = made(func, InstKind::BinaryInst, base->ty,
                        { base, const_cast<IntConst*>(module.int_const(u64(), offset)) }, span);
      moved->bin = BinOp::add;
      out.push_back(moved);
      return moved;
    }

    Value* copied(Function& func, Module& module, Value* into, Value* from, std::uint64_t size, Value* token,
                  std::vector<Instruction*>& out, Span span)
    {
      static constexpr std::pair<unsigned, std::uint64_t> chunks[] = { { 64, 8 }, { 32, 4 }, { 16, 2 }, { 8, 1 } };
      std::uint64_t at = 0;
      while (at < size) {
        auto [bits, width] = *std::ranges::find_if(chunks, [&](auto one) { return one.second <= size - at; });
        const Type* part = int_type(bits, false);
        auto source = at_in(func, from, part, at, out, span, module);
        auto read = made(func, InstKind::LoadInst, part, { token, source }, span);
        out.push_back(read);
        auto target = at_in(func, into, part, at, out, span, module);
        auto write = made(func, InstKind::StoreInst, mem_type(), { token, target, read }, span);
        out.push_back(write);
        token = write;
        at += width;
      }
      return token;
    }

    Instruction* part_of(Function& func, Value* value, std::size_t index, const Type* part, const Type* answer, Span span)
    {
      if (! is<ResultType>(answer))
        return extract(func, value, std::int64_t(index), part, span);
      InstKind kind = index == 0 ? InstKind::UnwrapInst : index == 1 ? InstKind::FailedInst : InstKind::ErrorInst;
      return made(func, kind, part, { value }, span);
    }

    Instruction* whole_of(Function& func, const std::vector<Value*>& taken, const Type* answer, Span span)
    {
      if (! is<ResultType>(answer))
        return made(func, InstKind::TupleInst, answer, taken, span);
      return made(func, InstKind::WrapInst, answer, { taken[0], taken[1], taken[2] }, span);
    }


    // The answers a function makes in a known place -- the heap, the image, a
    // parameter -- travelling without their allocator, which the caller adds.
    bool thin_answers(Module& module)
    {
      std::vector<Function*> every;
      for (auto& [_, func] : module.functions)
        every.push_back(func.get());
      std::vector<Function*> wanted;
      for (auto func : every)
        if (func->answer_from && ! func->blocks.empty() && func->linkage == Linkage::internal
            && (is<StrType>(func->fty->ret) || is<ListType>(func->fty->ret)))
          wanted.push_back(func);
      if (wanted.empty())
        return false;
      // One whose code is taken as a value keeps its shape: what calls it
      // through the value knows nothing of a thinner one.
      for (auto func : every)
        for (auto& block : func->blocks)
          for (auto inst : block->insts) {
            if (inst->kind == InstKind::CallInst && inst->callee != nullptr)
              continue;
            if (auto named = named_by(inst))
              std::erase(wanted, named);
          }
      if (wanted.empty())
        return false;
      std::unordered_map<const Function*, const Type*> fat;
      for (auto func : wanted) {
        fat[func] = func->fty->ret;
        auto pieces = parts_of(func->fty->ret);
        auto thin = tuple_type({ pieces[0], pieces[1] });
        FuncType shape;
        shape.listable = func->fty->listable;
        func->set_type(func_type(func->fty->params, thin, shape));
        for (auto& block : func->blocks)
          for (std::size_t index = 0; index < block->insts.size();) {
            auto inst = block->insts[index];
            if (inst->kind != InstKind::RetInst || inst->operands.empty()) {
              ++index;
              continue;
            }
            auto value = inst->operands[0];
            auto value_pieces = parts_of(value->ty);
            std::vector<Instruction*> made_insts{ extract(*func, value, 0, value_pieces[0], inst->span),
                                                  extract(*func, value, 1, value_pieces[1], inst->span) };
            auto two = made(*func, InstKind::TupleInst, thin, { made_insts[0], made_insts[1] }, inst->span);
            made_insts.push_back(two);
            made_insts.push_back(made(*func, InstKind::RetInst, void_type(), { two }, inst->span));
            splice(block.get(), index, made_insts);
            index += made_insts.size();
          }
      }
      for (auto func : every) {
        std::unordered_map<const Value*, Value*> instead;
        for (auto& block : func->blocks)
          for (std::size_t index = 0; index < block->insts.size();) {
            auto inst = block->insts[index];
            auto callee = inst->callee;
            if (inst->kind != InstKind::CallInst || callee == nullptr
                || std::ranges::find(wanted, callee) == wanted.end() || inst->ty == callee->fty->ret) {
              ++index;
              continue;
            }
            auto answer = fat[callee];
            auto pieces = parts_of(answer);
            auto [kind, at] = *callee->answer_from;
            std::vector<Instruction*> made_insts;
            auto call = made(*func, InstKind::CallInst, callee->fty->ret, inst->operands, inst->span);
            call->callee = callee;
            made_insts.push_back(call);
            Value* allocator;
            if (kind == "heap") {
              GlobalVar* heap = module.global("\N{APL FUNCTIONAL SYMBOL QUAD}heap");
              if (heap == nullptr) {
                auto var = std::make_unique<GlobalVar>("__pl4g_heap", arena_type(), ptr_type(arena_type(), true));
                heap = module.add_global(std::move(var), "\N{APL FUNCTIONAL SYMBOL QUAD}heap");
              }
              auto a = made(*func, InstKind::AddressInst, heap->ty, { heap }, inst->span);
              made_insts.push_back(a);
              allocator = a;
            } else if (kind == "static") {
              auto c = bitcast(*func, const_cast<IntConst*>(module.int_const(u64(), 0)), pieces[2], inst->span);
              made_insts.push_back(c);
              allocator = c;
            } else
              allocator = inst->operands[std::size_t(at)];
            auto first = extract(*func, call, 0, pieces[0], inst->span);
            auto second = extract(*func, call, 1, pieces[1], inst->span);
            auto whole = made(*func, InstKind::TupleInst, answer, { first, second, allocator }, inst->span);
            made_insts.insert(made_insts.end(), { first, second, whole });
            splice(block.get(), index, made_insts);
            instead[inst] = whole;
            index += made_insts.size();
          }
        all_stand_for(*func, instead);
      }
      for (auto func : wanted) {
        auto from = func->answer_from->first;
        std::string where = from == "heap" ? "\N{APL FUNCTIONAL SYMBOL QUAD}heap"
                            : from == "static" ? "none -- the answer is in the image --"
                                               : "what parameter " + std::to_string(func->answer_from->second + 1)
                                                   + " is given";
        module.record("answer-thin", func->name,
                      "'" + fat[func]->written() + "' is answered without its allocator, which is " + where
                        + " and which the caller adds",
                      func->name_span.is_valid() ? func->name_span : func->span);
      }
      return true;
    }

  } // anonymous namespace


  bool large_answers(Module& module)
  {
    laid_out = &module;
    bool thinned = thin_answers(module);
    std::vector<Function*> every;
    for (auto& [_, func] : module.functions)
      every.push_back(func.get());
    std::vector<Function*> wanted;
    std::unordered_map<const Function*, const Type*> answers;
    for (auto func : every)
      if (through_storage(*func)) {
        wanted.push_back(func);
        answers[func] = func->fty->ret;
      }
    if (wanted.empty())
      return thinned;
    // What it answers goes where its last parameter, added here, says.
    for (auto func : wanted) {
      auto place = ptr_type(func->fty->ret, true);
      auto params = func->fty->params;
      params.push_back(place);
      func->set_type(func_type(params, void_type()));
      func->attrs.answer_in_storage = true;
      if (! func->blocks.empty())
        func->blocks[0]->add_param(place)->name_hint = "answer";
    }
    for (auto func : every)
      for (auto& block : func->blocks) {
        for (std::size_t index = 0; index < block->insts.size();) {
          auto inst = block->insts[index];
          auto callee = inst->callee;
          if (inst->kind != InstKind::CallInst || callee == nullptr || ! answers.contains(callee)
              || is<VoidType>(inst->ty)) {
            ++index;
            continue;
          }
          auto answer = answers[callee];
          auto span = inst->span;
          auto token = token_before(*func, block.get(), index);
          index = index_of(block.get(), inst);
          std::vector<Instruction*> made_insts;
          auto slot = made(*func, InstKind::FrameInst, ptr_type(answer, true), {}, span);
          slot->held = answer;
          made_insts.push_back(slot);
          auto args = inst->operands;
          args.push_back(slot);
          auto call = made(*func, InstKind::CallInst, void_type(), args, span);
          call->callee = callee;
          made_insts.push_back(call);
          Instruction* rebuilt;
          if (held_in_memory(answer)) {
            rebuilt = bitcast(*func, slot, answer, span);
            made_insts.push_back(rebuilt);
          } else {
            std::vector<Value*> taken;
            auto pieces = parts_of(answer);
            auto offsets = answer_offsets(answer);
            for (std::size_t i = 0; i < pieces.size(); ++i) {
              auto where = at_in(*func, slot, pieces[i], offsets[i], made_insts, span, module);
              auto read = made(*func, InstKind::LoadInst, pieces[i], { token, where }, span);
              made_insts.push_back(read);
              taken.push_back(read);
            }
            rebuilt = whole_of(*func, taken, answer, span);
            made_insts.push_back(rebuilt);
          }
          splice(block.get(), index, made_insts);
          stands_for(*func, inst, rebuilt);
          index += made_insts.size();
        }
      }
    for (auto func : wanted) {
      if (func->blocks.empty())
        continue;
      auto answer = answers[func];
      auto place = func->blocks[0]->params.back().get();
      for (auto& block : func->blocks) {
        auto last = block->terminator();
        if (last == nullptr || last->kind != InstKind::RetInst || last->operands.empty())
          continue;
        std::vector<Instruction*> made_insts;
        auto token = token_before(*func, block.get(), block->insts.size() - 1);
        auto at = index_of(block.get(), last);
        auto value = last->operands[0];
        if (held_in_memory(answer))
          copied(*func, module, place, value, size_of(answer, host()), token, made_insts, last->span);
        else {
          auto pieces = parts_of(answer);
          auto offsets = answer_offsets(answer);
          for (std::size_t i = 0; i < pieces.size(); ++i) {
            auto taken = part_of(*func, value, i, pieces[i], answer, last->span);
            made_insts.push_back(taken);
            auto where = at_in(*func, place, pieces[i], offsets[i], made_insts, last->span, module);
            auto write = made(*func, InstKind::StoreInst, mem_type(), { token, where, taken }, last->span);
            made_insts.push_back(write);
            token = write;
          }
        }
        made_insts.push_back(made(*func, InstKind::RetInst, void_type(), {}, last->span));
        splice(block.get(), at, made_insts);
      }
    }
    for (auto func : wanted)
      module.record("answer-in-storage", func->name,
                    "'" + answers[func]->written()
                      + "' is more values than the style answers in registers, so the caller provides the place",
                    func->name_span.is_valid() ? func->name_span : func->span);
    return true;
  }


  // -- coroutines ---------------------------------------------------------------

  namespace {

    constexpr __int128 finished = (__int128(1) << 64) - 1;

    std::vector<const Type*> words_of(const Type* ty)
    {
      if (held_in_memory(ty))
        return { ptr_type(u8(), true) };
      return parts_of(ty);
    }

    void rename_in(BasicBlock* block, const std::unordered_map<const Value*, Value*>& renamed,
                   const std::unordered_set<const Instruction*>* skip = nullptr)
    {
      for (auto inst : block->insts) {
        if (skip != nullptr && skip->contains(inst))
          continue;
        for (auto& one : inst->operands)
          if (auto it = renamed.find(one); it != renamed.end())
            one = it->second;
        for (auto& target : inst->targets)
          for (auto& one : target.args)
            if (auto it = renamed.find(one); it != renamed.end())
              one = it->second;
      }
    }

    void rename(Function& func, const std::unordered_map<const Value*, Value*>& renamed)
    {
      for (auto& block : func.blocks)
        rename_in(block.get(), renamed);
    }

    // Where a value kept across a stop is put in the frame, and how it is
    // taken back out.
    void put(Builder& builder, Value* raw, std::uint64_t at, const std::vector<const Type*>& words, Value* value)
    {
      std::vector<Value*> pieces;
      if (held_in_memory(value->ty))
        pieces.push_back(builder.cast(CastKind::bitcast, value, words[0]));
      else if (words.size() == 1)
        pieces.push_back(value);
      else
        for (std::size_t i = 0; i < words.size(); ++i)
          pieces.push_back(builder.extract(value, std::int64_t(i), words[i]));
      auto offset = at;
      for (std::size_t i = 0; i < pieces.size(); ++i) {
        offset = round_up(offset, std::max<std::uint64_t>(align_of(words[i], host()), 1));
        auto here = builder.cast(CastKind::bitcast, builder.binary(BinOp::add, raw, builder.int_const(u64(), offset)),
                                 ptr_type(words[i], true));
        builder.store(here, pieces[i]);
        offset += size_of(words[i], host());
      }
    }

    Value* took(Builder& builder, Value* raw, std::uint64_t at, const std::vector<const Type*>& words, const Type* ty)
    {
      std::vector<Value*> pieces;
      auto offset = at;
      for (auto word : words) {
        offset = round_up(offset, std::max<std::uint64_t>(align_of(word, host()), 1));
        auto here = builder.cast(CastKind::bitcast, builder.binary(BinOp::add, raw, builder.int_const(u64(), offset)),
                                 ptr_type(word, true));
        pieces.push_back(builder.load(here));
        offset += size_of(word, host());
      }
      if (held_in_memory(ty))
        return builder.cast(CastKind::bitcast, pieces[0], ty);
      if (pieces.size() == 1)
        return pieces[0];
      return builder.make_tuple(pieces, ty);
    }

    void split(Module& module, Function& func)
    {
      BasicBlock* old_entry = func.blocks[0].get();
      auto old_params = std::move(old_entry->params);
      old_entry->params.clear();
      auto entry_owner = func.detached_block(func.unique_label("resume"));
      auto entry = entry_owner.get();
      std::vector<BlockParam*> fresh;
      for (const auto& one : old_params) {
        auto param = entry->add_param(one->ty);
        param->name_hint = one->name_hint;
        fresh.push_back(param);
      }
      func.blocks.insert(func.blocks.begin(), std::move(entry_owner));
      Builder builder(module, func);
      builder.position_at(entry);
      auto token = builder.append(func.make(InstKind::MemStartInst, mem_type()));
      builder.set_memory(token);
      std::unordered_map<const Value*, Value*> renamed;
      for (std::size_t i = 0; i < old_params.size(); ++i)
        renamed[old_params[i].get()] = fresh[i];
      for (auto& one : old_params)
        func.retired.push_back(std::move(one));
      auto raw = builder.cast(CastKind::bitcast, fresh[0], ptr_type(u8(), true));
      std::uint64_t offset = 16;
      auto place = [&](std::uint64_t at, const Type* ty) {
        auto here = builder.binary(BinOp::add, raw, builder.int_const(u64(), at));
        return builder.cast(CastKind::bitcast, here, ptr_type(ty, true));
      };
      auto state = place(0, u64());
      // What the body keeps in its own frame is in the generator's.
      for (std::size_t b = 1; b < func.blocks.size(); ++b) {
        auto block = func.blocks[b].get();
        for (auto inst : std::vector<Instruction*>(block->insts)) {
          if (inst->kind == InstKind::FrameInst) {
            offset = round_up(offset, std::max<std::uint64_t>(align_of(inst->held, host()), 8));
            auto found = builder.cast(CastKind::bitcast,
                                      builder.binary(BinOp::add, raw, builder.int_const(u64(), offset)), inst->ty);
            offset += size_of(inst->held, host());
            renamed[inst] = found;
            std::erase(block->insts, inst);
          } else if (inst->kind == InstKind::MemStartInst) {
            renamed[inst] = token;
            std::erase(block->insts, inst);
          }
        }
      }
      rename(func, renamed);
      // Which values are read in a block other than the one that made them:
      // what has to cross a stop, which the frame keeps.
      std::unordered_map<const Value*, BasicBlock*> home;
      for (std::size_t b = 1; b < func.blocks.size(); ++b) {
        auto block = func.blocks[b].get();
        for (const auto& param : block->params)
          if (! is<MemType>(param->ty))
            home[param.get()] = block;
        for (auto inst : block->insts)
          if (inst->has_result() && ! is<MemType>(inst->ty))
            home[inst] = block;
      }
      std::vector<Value*> wanted_order;
      std::unordered_map<const Value*, std::vector<BasicBlock*>> wanted;
      for (std::size_t b = 1; b < func.blocks.size(); ++b) {
        auto block = func.blocks[b].get();
        for (auto inst : block->insts) {
          std::vector<Value*> used = inst->operands;
          for (const auto& target : inst->targets)
            used.insert(used.end(), target.args.begin(), target.args.end());
          for (auto one : used) {
            auto it = home.find(one);
            if (it == home.end() || it->second == block)
              continue;
            auto [readers, fresh_entry] = wanted.try_emplace(one);
            if (fresh_entry)
              wanted_order.push_back(one);
            if (std::ranges::find(readers->second, block) == readers->second.end())
              readers->second.push_back(block);
          }
        }
      }
      std::unordered_map<const Value*, std::pair<std::uint64_t, std::vector<const Type*>>> rooms;
      for (auto key : wanted_order) {
        auto words = words_of(key->ty);
        std::uint64_t at = offset = round_up(offset, 8);
        for (auto word : words) {
          offset = round_up(offset, std::max<std::uint64_t>(align_of(word, host()), 1));
          offset += size_of(word, host());
        }
        rooms[key] = { at, words };
      }
      for (auto key : wanted_order) {
        const auto& [at, words] = rooms[key];
        auto block = home[key];
        auto scratch = func.detached_block("scratch");
        Builder inner(module, func);
        inner.position_at(scratch.get());
        inner.set_memory(token);
        put(inner, raw, at, words, key);
        std::size_t where = 0;
        if (key->vkind != ValueKind::block_param)
          where = index_of(block, static_cast<Instruction*>(key)) + 1;
        for (auto one : scratch->insts)
          one->parent = block;
        block->insts.insert(block->insts.begin() + std::ptrdiff_t(where), scratch->insts.begin(), scratch->insts.end());
      }
      for (auto key : wanted_order) {
        const auto& [at, words] = rooms[key];
        for (auto block : wanted[key]) {
          auto scratch = func.detached_block("scratch");
          Builder inner(module, func);
          inner.position_at(scratch.get());
          inner.set_memory(token);
          auto back = took(inner, raw, at, words, key->ty);
          for (auto one : scratch->insts)
            one->parent = block;
          block->insts.insert(block->insts.begin(), scratch->insts.begin(), scratch->insts.end());
          std::unordered_set<const Instruction*> skip(scratch->insts.begin(), scratch->insts.end());
          rename_in(block, { { key, back } }, &skip);
        }
      }
      // A memory token from another block is the one the resume starts with:
      // what the body knew of memory before a stop does not survive it.
      for (std::size_t b = 1; b < func.blocks.size(); ++b) {
        auto block = func.blocks[b].get();
        std::unordered_set<const Value*> made_here(block->insts.begin(), block->insts.end());
        auto foreign = [&](const Value* one) {
          return is<MemType>(one->ty) && ! made_here.contains(one)
                 && ! (one->vkind == ValueKind::block_param && static_cast<const BlockParam*>(one)->block == block);
        };
        for (auto inst : block->insts) {
          for (auto& one : inst->operands)
            if (foreign(one))
              one = token;
          for (auto& target : inst->targets)
            for (auto& one : target.args)
              if (foreign(one))
                one = token;
        }
      }
      // Each stop says where it goes on, and each return that it is over.
      std::vector<BasicBlock*> stops;
      for (std::size_t b = 1; b < func.blocks.size(); ++b) {
        auto block = func.blocks[b].get();
        auto last = block->insts.empty() ? nullptr : block->insts.back();
        if (last == nullptr)
          continue;
        if (last->kind == InstKind::YieldInst) {
          stops.push_back(last->targets[0].block);
          auto number = stops.size();
          block->insts.pop_back();
          Builder tail(module, func);
          tail.position_at(block);
          tail.set_memory(token);
          tail.store(state, tail.int_const(u64(), number));
          tail.ret(tail.int_const(u8(), last->delegates ? 2 : 1));
        } else if (last->kind == InstKind::RetInst) {
          block->insts.pop_back();
          Builder tail(module, func);
          tail.position_at(block);
          tail.set_memory(token);
          tail.store(state, tail.int_const(u64(), finished));
          tail.ret(tail.int_const(u8(), 0));
        }
      }
      auto done = func.add_block("finished");
      Builder ending(module, func);
      ending.position_at(done);
      ending.ret(ending.int_const(u8(), 0));
      auto at = builder.load(state);
      std::vector<std::pair<__int128, BasicBlock*>> cases{ { 0, old_entry } };
      for (std::size_t i = 0; i < stops.size(); ++i)
        cases.emplace_back(__int128(i + 1), stops[i]);
      builder.switch_(at, cases, done);
      auto size = round_up(offset, 16);
      if (func.coroutine != nullptr)
        module.frame_sizes[func.coroutine] = size;
      module.record("iterator", func.name,
                    "a generator split at its " + std::to_string(stops.size()) + " stop"
                      + (stops.size() == 1 ? "" : "s") + ": " + std::to_string(rooms.size()) + " value"
                      + (rooms.size() == 1 ? "" : "s") + " kept across them, in a frame of " + std::to_string(size)
                      + " bytes",
                    func.span);
    }

  } // anonymous namespace


  bool coroutines(Module& module)
  {
    laid_out = &module;
    bool changed = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      bool stops = std::ranges::any_of(func->blocks, [](const auto& b) {
        return std::ranges::any_of(b->insts, [](auto i) { return i->kind == InstKind::YieldInst; });
      });
      if (stops) {
        split(module, *func);
        changed = true;
      }
    }
    // How large each thing measured is, now that every frame is.
    DataLayout layout;
    layout.frames = &module.frame_sizes;
    for (auto& [_, func] : module.functions) {
      std::unordered_map<const Value*, Value*> numbers;
      for (auto& block : func->blocks)
        for (auto inst : block->insts)
          if (inst->kind == InstKind::SizeOfInst)
            numbers[inst] = const_cast<IntConst*>(module.int_const(u64(), size_of(inst->held, layout)));
      if (numbers.empty())
        continue;
      rename(*func, numbers);
      for (auto& block : func->blocks)
        std::erase_if(block->insts, [&](auto inst) { return numbers.contains(inst); });
      changed = true;
    }
    return changed;
  }


  // -- leanvalues ---------------------------------------------------------------

  namespace {

    // What is known of where a value's allocator is: nothing yet, more than
    // one thing, or one place -- a variable, none at all, a parameter.
    struct Allocator {
      enum class Kind : std::uint8_t { unknown, carried, global, none, param } kind = Kind::unknown;
      const Value* what = nullptr;
      bool operator==(const Allocator&) const = default;
    };

    std::optional<Allocator> key_of(const Value* value)
    {
      if (value->vkind == ValueKind::instruction) {
        auto inst = static_cast<const Instruction*>(value);
        if (inst->kind == InstKind::AddressInst && inst->operands[0]->vkind == ValueKind::global)
          return Allocator{ Allocator::Kind::global, inst->operands[0] };
        if (inst->kind == InstKind::CastInst && inst->cast == CastKind::bitcast
            && inst->operands[0]->vkind == ValueKind::int_const
            && static_cast<const IntConst*>(inst->operands[0])->value == 0)
          return Allocator{ Allocator::Kind::none, nullptr };
      }
      if (value->vkind == ValueKind::block_param) {
        auto param = static_cast<const BlockParam*>(value);
        if (param->block->parent != nullptr && param->block == param->block->parent->blocks[0].get())
          return Allocator{ Allocator::Kind::param, param };
      }
      return std::nullopt;
    }

    Allocator meet(Allocator one, Allocator other)
    {
      if (one.kind == Allocator::Kind::unknown)
        return other;
      if (other.kind == Allocator::Kind::unknown)
        return one;
      if (one == other)
        return one;
      return { Allocator::Kind::carried, nullptr };
    }

    bool lean_able(const Type* ty)
    {
      if (is<StrType>(ty) || is<ListType>(ty))
        return true;
      auto tuple = as<TupleType>(ty);
      return tuple != nullptr && tuple->members.size() == 2 && is<PtrType>(tuple->members[0])
             && tuple->members[1] == u64();
    }

    bool ours(const Instruction* inst)
    {
      if (inst->kind == InstKind::TupleInst)
        return lean_able(inst->ty);
      if (inst->kind == InstKind::ExtractInst)
        return lean_able(inst->operands[0]->ty);
      if (inst->kind == InstKind::AddressInst || inst->kind == InstKind::CastInst) {
        auto ptr = as<PtrType>(inst->ty);
        return ptr != nullptr && ptr->pointee == arena_type()
               && (inst->kind == InstKind::AddressInst || inst->cast == CastKind::bitcast);
      }
      return false;
    }

    bool holds_text(const Function& func)
    {
      for (const auto& block : func.blocks) {
        if (std::ranges::any_of(block->params, [](const auto& p) { return lean_able(p->ty); }))
          return true;
        if (std::ranges::any_of(block->insts, [](auto i) { return lean_able(i->ty); }))
          return true;
      }
      return false;
    }

    bool fold(Function& func)
    {
      std::unordered_map<const Value*, Value*> instead;
      for (auto& block : func.blocks)
        for (auto inst : block->insts) {
          if (inst->kind != InstKind::ExtractInst)
            continue;
          Value* whole = inst->operands[0];
          while (instead.contains(whole))
            whole = instead[whole];
          if (whole->vkind != ValueKind::instruction)
            continue;
          auto tuple = static_cast<Instruction*>(whole);
          if (tuple->kind == InstKind::TupleInst && lean_able(tuple->ty)
              && std::size_t(inst->index) < tuple->operands.size() && tuple->operands[std::size_t(inst->index)]->ty == inst->ty)
            instead[inst] = tuple->operands[std::size_t(inst->index)];
        }
      for (auto& [key, found] : instead)
        while (instead.contains(found))
          found = instead[found];
      all_stand_for(func, instead);
      return ! instead.empty();
    }

    bool sweep(Function& func)
    {
      bool changed = false;
      while (true) {
        auto used = read_in(func);
        bool gone = false;
        for (auto& block : func.blocks) {
          auto before = block->insts.size();
          std::erase_if(block->insts, [&](auto inst) { return ! used.contains(inst) && ours(inst); });
          if (block->insts.size() != before)
            gone = true;
        }
        if (! gone)
          return changed;
        changed = true;
      }
    }

    std::string named(const BlockParam* param)
    {
      return param->name_hint ? "'" + *param->name_hint + "'" : std::string("a value");
    }

    bool narrow(Module& module, Function& func)
    {
      auto entry = func.blocks[0].get();
      std::vector<BlockParam*> wanted;
      for (auto& block : func.blocks)
        if (block.get() != entry)
          for (const auto& param : block->params)
            if (is<StrType>(param->ty) || is<ListType>(param->ty))
              wanted.push_back(param.get());
      if (wanted.empty())
        return false;
      // What each of them is handed, by every branch to its block.
      std::map<std::pair<const BasicBlock*, std::size_t>, std::vector<Value*>> incoming;
      for (auto& block : func.blocks) {
        auto last = block->insts.empty() ? nullptr : block->insts.back();
        if (last == nullptr)
          continue;
        for (auto& target : last->targets)
          for (std::size_t at = 0; at < target.args.size(); ++at)
            incoming[{ target.block, at }].push_back(target.args[at]);
      }
      std::unordered_map<const Value*, Allocator> held;
      for (auto param : wanted)
        held[param] = {};
      auto allocator_of = [&](const Value* arg, const BlockParam* param) -> Allocator {
        if (arg == param)
          return {};
        if (auto it = held.find(arg); it != held.end())
          return it->second;
        if (arg->vkind == ValueKind::instruction) {
          auto inst = static_cast<const Instruction*>(arg);
          if (inst->kind == InstKind::TupleInst && inst->operands.size() == 3) {
            if (auto found = key_of(inst->operands[2]))
              return *found;
          }
        }
        return { Allocator::Kind::carried, nullptr };
      };
      for (bool settled = false; ! settled;) {
        settled = true;
        for (auto param : wanted) {
          Allocator found;
          for (auto arg : incoming[{ param->block, param->index }])
            found = meet(found, allocator_of(arg, param));
          if (found != held[param]) {
            held[param] = found;
            settled = false;
          }
        }
      }
      std::vector<BlockParam*> lean;
      for (auto param : wanted) {
        auto kind = held[param].kind;
        if (kind != Allocator::Kind::unknown && kind != Allocator::Kind::carried)
          lean.push_back(param);
        if (kind == Allocator::Kind::carried)
          module.record("fat-value", func.name,
                        named(param) + " carries its allocator where " + param->block->label
                          + " joins: what it is given there is made in more than one allocator, or in one known only "
                            "at run time",
                        param->name_span.is_valid() ? param->name_span : func.span);
      }
      if (lean.empty())
        return false;
      std::unordered_map<const Value*, Value*> instead;
      std::unordered_set<const BlockParam*> thin_ones;
      for (auto param : lean) {
        auto block = param->block;
        auto pieces = parts_of(param->ty);
        auto thin = tuple_type({ pieces[0], pieces[1] });
        auto narrow_param = std::make_unique<BlockParam>(thin, block, param->index);
        narrow_param->name_hint = param->name_hint;
        narrow_param->name_span = param->name_span;
        auto narrowed = narrow_param.get();
        auto index = param->index;
        func.retired.push_back(std::move(block->params[index]));
        block->params[index] = std::move(narrow_param);
        thin_ones.insert(narrowed);
        std::vector<Instruction*> made_insts;
        Value* allocator;
        const auto& key = held[param];
        if (key.kind == Allocator::Kind::global) {
          auto var = const_cast<Value*>(key.what);
          auto a = made(func, InstKind::AddressInst, var->ty, { var });
          made_insts.push_back(a);
          allocator = a;
        } else if (key.kind == Allocator::Kind::none) {
          auto c = bitcast(func, const_cast<IntConst*>(module.int_const(u64(), 0)), pieces[2]);
          made_insts.push_back(c);
          allocator = c;
        } else
          allocator = const_cast<Value*>(key.what);
        auto first = extract(func, narrowed, 0, pieces[0]);
        auto second = extract(func, narrowed, 1, pieces[1]);
        auto whole = made(func, InstKind::TupleInst, param->ty, { first, second, allocator });
        made_insts.insert(made_insts.end(), { first, second, whole });
        prepend(block, made_insts);
        instead[param] = whole;
        std::string said = key.kind == Allocator::Kind::global
                             ? (static_cast<const GlobalVar*>(key.what)->name == "__pl4g_heap"
                                  ? std::string("\N{APL FUNCTIONAL SYMBOL QUAD}heap")
                                  : "'" + static_cast<const GlobalVar*>(key.what)->name + "'")
                           : key.kind == Allocator::Kind::none
                             ? std::string("no allocator: text in the image")
                             : "what parameter '" + static_cast<const BlockParam*>(key.what)->name_hint.value_or("?")
                                 + "' is given";
        module.record("lean-value", func.name,
                      named(narrowed) + " stays two words where " + block->label
                        + " joins: everything it is given there is made in " + said
                        + ", which the compiler holds and adds where the value is needed whole",
                      narrowed->name_span.is_valid() ? narrowed->name_span : func.span);
      }
      all_stand_for(func, instead);
      // Every branch to such a block hands it the two words.
      for (auto& block : func.blocks) {
        auto last = block->insts.empty() ? nullptr : block->insts.back();
        if (last == nullptr)
          continue;
        for (auto& target : last->targets)
          for (std::size_t at = 0; at < target.args.size(); ++at) {
            auto arg = target.args[at];
            BlockParam* narrowed = at < target.block->params.size() ? target.block->params[at].get() : nullptr;
            if (narrowed == nullptr || narrowed->ty == arg->ty || ! thin_ones.contains(narrowed))
              continue;
            auto pieces = parts_of(arg->ty);
            std::vector<Value*> parts;
            std::vector<Instruction*> made_insts;
            if (arg->vkind == ValueKind::instruction && static_cast<Instruction*>(arg)->kind == InstKind::TupleInst) {
              auto tuple = static_cast<Instruction*>(arg);
              parts = { tuple->operands[0], tuple->operands[1] };
            } else {
              auto first = extract(func, arg, 0, pieces[0]);
              auto second = extract(func, arg, 1, pieces[1]);
              parts = { first, second };
              made_insts = { first, second };
            }
            auto two = made(func, InstKind::TupleInst, narrowed->ty, parts);
            made_insts.push_back(two);
            for (auto one : made_insts)
              one->parent = block.get();
            block->insts.insert(block->insts.end() - 1, made_insts.begin(), made_insts.end());
            target.args[at] = two;
          }
      }
      return true;
    }

  } // anonymous namespace


  bool lean_values(Module& module)
  {
    bool touched = false;
    for (auto& [_, func] : module.functions) {
      if (func->blocks.empty() || ! holds_text(*func))
        continue;
      bool changed = fold(*func);
      changed |= narrow(module, *func);
      changed |= sweep(*func);
      touched |= changed;
    }
    return touched;
  }


  // -- splitedges ---------------------------------------------------------------

  bool split_edges(Module& module)
  {
    // A branch of several ways that hands values over gets a block of its own
    // on each edge that does, where the moves go.
    bool touched = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      bool changed = false;
      for (std::size_t at = 0; at < func->blocks.size();) {
        auto block = func->blocks[at].get();
        auto last = block->terminator();
        std::size_t made_count = 0;
        if (last != nullptr && last->targets.size() > 1)
          for (auto& edge : last->targets) {
            if (carried_values(edge).empty())
              continue;
            ++made_count;
            auto made_block = func->detached_block(func->unique_label("edge"));
            auto jump = func->make(InstKind::BrInst, void_type());
            jump->span = block->insts.back()->span;
            jump->targets.push_back({ edge.block, edge.args });
            jump->parent = made_block.get();
            made_block->insts.push_back(jump);
            edge.block = made_block.get();
            edge.args.clear();
            func->blocks.insert(func->blocks.begin() + std::ptrdiff_t(at + made_count), std::move(made_block));
          }
        changed |= made_count > 0;
        at += 1 + made_count;
      }
      touched |= changed;
    }
    return touched;
  }


  bool settle(Module& module)
  {
    // How many times folding and simplifying are taken in turn at most.
    constexpr int rounds = 8;
    bool changed = false;
    for (int round = 0; round < rounds; ++round) {
      bool one = false;
      one |= constant_folding(module);
      one |= simplify_cfg(module);
      one |= known_arguments(module);
      changed |= one;
      if (! one)
        break;
    }
    return changed;
  }


  namespace {

    constexpr std::string_view level_nought[] = {
      "dropignored", "dropunreached", "tailcalls", "largeanswers", "coroutines", "leanvalues", "dropunreached",
      "splitedges",
    };

    constexpr std::string_view optimizing[] = {
      "dropignored", "dropunreached", "tailcalls", "largeanswers", "coroutines", "inline", "leanvalues",
      "constfold", "samevalues", "purecalls", "simplifycfg", "constfold", "conditions", "facts", "settle", "dce",
      "dropunreached", "splitedges",
    };

    bool run_one(std::string_view name, Module& module)
    {
      if (name == "dropignored") return drop_ignored_calls(module);
      if (name == "dropunreached") return drop_unreached(module);
      if (name == "tailcalls") return tail_calls(module);
      if (name == "largeanswers") return large_answers(module);
      if (name == "coroutines") return coroutines(module);
      if (name == "leanvalues") return lean_values(module);
      if (name == "splitedges") return split_edges(module);
      if (name == "inline") return inlining(module);
      if (name == "constfold") return constant_folding(module);
      if (name == "samevalues") return same_values(module);
      if (name == "purecalls") return pure_calls(module);
      if (name == "simplifycfg") return simplify_cfg(module);
      if (name == "facts") return facts(module);
      if (name == "conditions") return branch_conditions(module);
      if (name == "settle") return settle(module);
      if (name == "dce") return dead_code(module);
      throw InternalError("no pass is called " + std::string(name));
    }

  } // anonymous namespace


  std::span<const std::string_view> pipeline_for(int level)
  {
    if (level <= 0)
      return level_nought;
    return optimizing;
  }


  void run_pipeline(Module& module, int level)
  {
    run_pipeline(module, level, verifying_asked(), nullptr);
  }


  void run_pipeline(Module& module, int level, bool verifying, std::vector<PassTiming>* timings)
  {
    laid_out = &module;
    auto names = pipeline_for(level);
    for (std::size_t at = 0; at < names.size(); ++at) {
      auto start = std::chrono::steady_clock::now();
      bool changed = run_one(names[at], module);
      if (timings != nullptr)
        timings->push_back({ names[at], std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(),
                             changed });
      // After each pass that changed something, and after the last whatever
      // it says: what reaches the back end has always been verified.
      if (verifying && (changed || at + 1 == names.size()))
        verify(module);
    }
  }

} // namespace pl4g::opt
