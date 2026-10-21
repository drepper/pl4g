// One value for what is worked out twice.  A port of pypl4g's
// opt/passes/samevalues.py: value numbering over the dominator tree.
//
// What may be shared is what has no effect -- but a frame, every one room of
// its own, and the memory chain's beginning -- and never a call.  A read is
// shared where nothing could have written between: two loads taking one token
// read alike, a store making a token of its own, unless a call or a request to
// the kernel lies on a way from the one to the other.
#include "opt/passes.hh"

#include <algorithm>
#include <unordered_map>
#include <unordered_set>

#include "opt/flow.hh"
#include "util/ordered.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // Whether `inst` computes a value another like it could stand for.
    bool shareable(const Instruction* inst)
    {
      if (! inst->has_result() || has_effects(inst))
        return false;
      switch (inst->kind) {
      case InstKind::FrameInst: case InstKind::MemStartInst: case InstKind::CallInst: case InstKind::SyscallInst:
        return false;
      default:
        return ! inst->is_terminator();
      }
    }

    // Whether `inst` may change memory a later read with the same token reads.
    bool writes_unseen(const Instruction* inst)
    {
      return inst->kind == InstKind::CallInst || inst->kind == InstKind::SyscallInst
             || (inst->kind == InstKind::LoadInst && inst->ordering != Ordering::plain);
    }

    // What an instruction computes, as what two of them share where they
    // compute the same: its shape, its operation, its type by identity, its
    // operands as they stand, and what of its own its shape carries -- told
    // by value but for a function, by which one, and a type, by structure.
    struct Key {
      InstKind kind{};
      std::string opcode;
      const Type* ty = nullptr;
      // Which value each operand is -- and for an undefined one, which type,
      // every undefined value of one type being the same nothing.
      std::vector<const void*> operands;
      // What the shape carries beside: an operation, a position, a kind of cast.
      int what = 0;
      std::int64_t index = 0;
      const Function* callee = nullptr;
      const Type* held = nullptr;

      bool operator==(const Key& other) const
      {
        return kind == other.kind && opcode == other.opcode && ty == other.ty && operands == other.operands
               && what == other.what && index == other.index && callee == other.callee
               && (held == other.held || (held != nullptr && other.held != nullptr && same(held, other.held)));
      }
    };

    struct KeyHash {
      std::size_t operator()(const Key& key) const noexcept
      {
        std::size_t seed = std::hash<std::string>{}(key.opcode);
        seed = hash_combine(seed, std::size_t(key.kind));
        seed = hash_combine(seed, std::hash<const void*>{}(key.ty));
        for (auto one : key.operands)
          seed = hash_combine(seed, std::hash<const void*>{}(one));
        seed = hash_combine(seed, std::size_t(key.what));
        seed = hash_combine(seed, std::size_t(key.index));
        seed = hash_combine(seed, std::hash<const void*>{}(key.callee));
        if (key.held != nullptr)
          seed = hash_combine(seed, structural_hash(key.held));
        return seed;
      }
    };

    Key key_of(const Instruction* inst, const std::unordered_map<const Value*, Value*>& instead)
    {
      auto standing = [&](const Value* value) {
        for (auto it = instead.find(value); it != instead.end(); it = instead.find(value))
          value = it->second;
        return value;
      };
      Key key{ inst->kind, inst->opcode(), inst->ty, {} };
      for (auto one : inst->operands) {
        auto found = standing(one);
        key.operands.push_back(found->vkind == ValueKind::undef ? static_cast<const void*>(found->ty)
                                                                : static_cast<const void*>(found));
      }
      // The slots pypl4g's class of each shape has beside the common ones.
      switch (inst->kind) {
      case InstKind::BinaryInst: key.what = int(inst->bin); break;
      case InstKind::UnaryInst: key.what = int(inst->un); break;
      case InstKind::CmpInst: key.what = int(inst->pred); break;
      case InstKind::CastInst: key.what = int(inst->cast); break;
      case InstKind::LoadInst: key.what = int(inst->ordering); break;
      case InstKind::ExtractInst: case InstKind::FieldInst: case InstKind::SumMakeInst: case InstKind::SumGetInst:
        key.index = inst->index;
        break;
      case InstKind::CodeInst: key.callee = inst->callee; break;
      case InstKind::SizeOfInst: key.held = inst->held; break;
      default: break;
      }
      return key;
    }

    using Place = std::pair<BasicBlock*, std::size_t>;

    // Whether something that writes unseen lies on a way from `first` to
    // `second`.
    bool written_between(const Function& func, Place first, Place second)
    {
      auto [start, at] = first;
      auto [end, upto] = second;
      if (start == end && at < upto) {
        for (std::size_t i = at + 1; i < upto; ++i)
          if (writes_unseen(start->insts[i]))
            return true;
        return false;
      }
      std::unordered_set<const BasicBlock*> reached;
      std::vector<BasicBlock*> waiting = successors(start);
      while (! waiting.empty()) {
        auto one = waiting.back();
        waiting.pop_back();
        if (reached.insert(one).second)
          for (auto next : successors(one))
            waiting.push_back(next);
      }
      auto preds = predecessors(func);
      std::unordered_set<const BasicBlock*> reaching;
      waiting = preds[end];
      while (! waiting.empty()) {
        auto one = waiting.back();
        waiting.pop_back();
        if (reaching.insert(one).second)
          for (auto next : preds[one])
            waiting.push_back(next);
      }
      for (std::size_t i = at + 1; i < start->insts.size(); ++i)
        if (writes_unseen(start->insts[i]))
          return true;
      for (std::size_t i = 0; i < upto; ++i)
        if (writes_unseen(end->insts[i]))
          return true;
      for (const auto& block : func.blocks)
        if (reached.contains(block.get()) && reaching.contains(block.get()))
          for (auto one : block->insts)
            if (writes_unseen(one))
              return true;
      return false;
    }

    bool share(Function& func)
    {
      auto tree = dominators(func);
      std::unordered_map<const Value*, Value*> instead;
      bool writers = false;
      for (const auto& block : func.blocks)
        for (auto inst : block->insts)
          writers |= writes_unseen(inst);
      std::unordered_map<const Instruction*, Place> where;
      std::unordered_map<const BasicBlock*, std::size_t> layout;
      for (std::size_t at = 0; at < func.blocks.size(); ++at)
        layout[func.blocks[at].get()] = at;
      std::unordered_map<Key, Instruction*, KeyHash> seen;
      std::vector<std::vector<Key>> added;
      std::vector<std::pair<BasicBlock*, bool>> stack{ { tree.order.front(), false } };
      while (! stack.empty()) {
        auto [block, done] = stack.back();
        stack.pop_back();
        if (done) {
          for (const auto& key : added.back())
            seen.erase(key);
          added.pop_back();
          continue;
        }
        std::vector<Key> mine;
        for (std::size_t at = 0; at < block->insts.size(); ++at) {
          auto inst = block->insts[at];
          where[inst] = { block, at };
          if (! shareable(inst))
            continue;
          auto key = key_of(inst, instead);
          auto found = seen.find(key);
          if (found == seen.end()) {
            seen.emplace(key, inst);
            mine.push_back(std::move(key));
            continue;
          }
          auto earlier = found->second;
          auto earlier_block = where[earlier].first;
          if (earlier_block != block && layout[earlier_block] > layout[block])
            // The back ends want a value worked out before it is read, so one
            // laid out later is not shared however it dominates.
            continue;
          if (inst->kind == InstKind::LoadInst && writers && written_between(func, where[earlier], { block, at }))
            continue;
          instead[inst] = earlier;
        }
        added.push_back(std::move(mine));
        stack.emplace_back(block, true);
        const auto& below = tree.children[block];
        for (auto it = below.rbegin(); it != below.rend(); ++it)
          stack.emplace_back(*it, false);
      }
      if (instead.empty())
        return false;
      replace_uses(func, instead);
      return true;
    }

  } // anonymous namespace


  bool same_values(Module& module)
  {
    bool changed = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration() || func->blocks.empty())
        continue;
      changed |= share(*func);
    }
    return changed;
  }

} // namespace pl4g::opt
