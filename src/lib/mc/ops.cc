// The operations the assembler builder accepts.
#include "mc/ops.hh"

#include <deque>
#include <stdexcept>

#include "util/ordered.hh"


namespace pl4g::mc {

  namespace {

    // Where the operations live, and by which name; made on first use so that
    // an operation a target defines at the start of the program finds it.
    struct Registry {
      std::deque<Op> store;
      OrderedMap<std::string, const Op*> by_name;
    };

    Registry& registry()
    {
      static Registry kept;
      return kept;
    }

  } // anonymous namespace


  Condition inverted(Condition cond) noexcept
  {
    switch (cond) {
    case Condition::eq: return Condition::ne;
    case Condition::ne: return Condition::eq;
    case Condition::slt: return Condition::sge;
    case Condition::sge: return Condition::slt;
    case Condition::sle: return Condition::sgt;
    case Condition::sgt: return Condition::sle;
    case Condition::ult: return Condition::uge;
    case Condition::uge: return Condition::ult;
    case Condition::ule: return Condition::ugt;
    case Condition::ugt: return Condition::ule;
    }
    return cond;
  }


  Condition swapped(Condition cond) noexcept
  {
    switch (cond) {
    case Condition::eq: return Condition::eq;
    case Condition::ne: return Condition::ne;
    case Condition::slt: return Condition::sgt;
    case Condition::sgt: return Condition::slt;
    case Condition::sle: return Condition::sge;
    case Condition::sge: return Condition::sle;
    case Condition::ult: return Condition::ugt;
    case Condition::ugt: return Condition::ult;
    case Condition::ule: return Condition::uge;
    case Condition::uge: return Condition::ule;
    }
    return cond;
  }


  std::string_view name_of(Condition cond) noexcept
  {
    constexpr std::string_view names[] = { "eq", "ne", "slt", "sle", "sgt", "sge", "ult", "ule", "ugt", "uge" };
    return names[std::size_t(cond)];
  }


  std::string_view name_of(Rounding how) noexcept
  {
    constexpr std::string_view names[] = { "down", "up", "nearest", "current" };
    return names[std::size_t(how)];
  }


  const Op* register_op(Op op)
  {
    auto& kept = registry();
    if (kept.by_name.contains(op.name)) [[unlikely]]
      throw std::invalid_argument("operation '" + op.name + "' is already registered");
    const Op* made = &kept.store.emplace_back(std::move(op));
    kept.by_name[made->name] = made;
    return made;
  }


  const Op* lookup_op(std::string_view name)
  {
    auto found = registry().by_name.get(std::string(name));
    return found == nullptr ? nullptr : *found;
  }


  std::vector<const Op*> registered_ops()
  {
    std::vector<const Op*> all;
    for (const auto& [_, op] : registry().by_name)
      all.push_back(op);
    return all;
  }


  namespace ops {
    const Op* const move = register_op({ "move", 1 });
    const Op* const plus = register_op({ "plus", 2 });
    const Op* const minus = register_op({ "minus", 2 });
    const Op* const times = register_op({ "times", 2 });
    const Op* const and_ = register_op({ "and", 2 });
    const Op* const or_ = register_op({ "or", 2 });
    const Op* const xor_ = register_op({ "xor", 2 });
    const Op* const neg = register_op({ "neg", 1 });
    const Op* const not_ = register_op({ "not", 1 });
    const Op* const shift_left = register_op({ "shift_left", 2 });
    const Op* const shift_right = register_op({ "shift_right", 2 });
    const Op* const shift_right_signed = register_op({ "shift_right_signed", 2 });
    const Op* const divide = register_op({ "divide", 2 });
    const Op* const larger = register_op({ "larger", 2 });
    const Op* const smaller = register_op({ "smaller", 2 });
    const Op* const compare = register_op({ "compare", 2, false });
    const Op* const call = register_op({ "call", 1, false });
    const Op* const return_ = register_op({ "return", 0, false, true });
    const Op* const trap = register_op({ "trap", 0, false, true });
  }

} // namespace pl4g::mc
