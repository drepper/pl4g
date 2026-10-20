// Which tests a binary runs, and what it says when one fails.
#include "target/tests.hh"


namespace pl4g::target {

  std::vector<const ir::Function*> run_by(const ir::Module& module)
  {
    if (! module.test_plan.empty())
      return { module.test_plan.begin(), module.test_plan.end() };
    std::vector<const ir::Function*> found;
    for (auto one : module.tests)
      if (one->attrs.special == ir::SpecialKind::test_always)
        found.push_back(one);
    return found;
  }


  bool observes(const ir::Module& module)
  {
    for (const auto& [_, func] : module.functions)
      for (const auto& block : func->blocks)
        for (auto inst : block->insts)
          if (inst->kind == ir::InstKind::AssertInst && inst->observing)
            return true;
    return false;
  }


  std::unordered_map<const ir::Function*, Failure> failures_of(const ir::Module& module, Messages& messages)
  {
    std::unordered_map<const ir::Function*, Failure> found;
    for (auto one : run_by(module)) {
      auto text = "test " + one->name + " failed\n";
      found[one] = Failure{ messages.symbol(text), text.size() };
    }
    return found;
  }

} // namespace pl4g::target
