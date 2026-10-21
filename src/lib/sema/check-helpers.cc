// The module level of pypl4g's check.py: the helpers that need no checker,
// the constants and tables, the two entry points, and the checker's
// construction.
#include "sema/check-helpers.hh"

#include <algorithm>
#include <bit>
#include <format>
#include <meta>
#include <unordered_map>

#include "diag/ids.hh"
#include "front/lexer.hh"
#include "ir/layout.hh"
#include "sema/check.hh"
#include "sema/owned.hh"
#include "sema/rapidhash.hh"
#include "sema/tables.hh"


namespace pl4g::sema {

  namespace {

    // `seen | {id(ty)}`.
    KeySet deeper_than(const KeySet& seen, const ir::Type* ty)
    {
      KeySet deeper = seen;
      deeper.insert(id_of(ty));
      return deeper;
    }

    // The callee of a call, where it is a bare name.
    const ast::NameRef* callee_name(const ast::Call* call)
    {
      return ast::as<ast::NameRef>(call->callee);
    }

    // What an argument is, a name given to it apart.
    const ast::Expr* argument_value(const ast::Expr* arg)
    {
      if (auto named = ast::as<ast::Named>(arg))
        return named->value;
      return arg;
    }

    // A text kept with the nodes, for a name a copy is given.
    ast::Text kept_text(ast::SyntaxArena& arena, std::string_view text)
    {
      return arena.text(text);
    }

    // Python's `Borrowed.leaves_of`.
    std::optional<std::vector<int>> leaves_of(const ir::Borrowed& borrowed, int param)
    {
      for (std::size_t at = 0; at < borrowed.params.size(); ++at)
        if (borrowed.params[at] == param)
          return at < borrowed.leaves.size() ? borrowed.leaves[at] : std::nullopt;
      return std::nullopt;
    }

    // Python's `FuncType.borrowed()`: `dict(self.borrows)`.
    std::map<int, ir::Borrowed> borrowed_table(const ir::FuncType* ty)
    {
      std::map<int, ir::Borrowed> table;
      for (const auto& [leaf, borrowed] : ty->borrows)
        table[leaf] = borrowed;
      return table;
    }

    // The path of the module `std` in a directory the system's modules are in.
    bool is_the_std_module(const std::string& origin)
    {
      if (origin.empty())
        return false;
      auto written = pure_path(origin);
      for (const auto& where : SearchPath::system_modules())
        if (written == pure_path(where / (std::string(std_module_name) + std::string(module_suffix))))
          return true;
      return false;
    }

    // Whether two types are the one object, element for element: Python's
    // `==` on tuples of types, which compares each by `==`.
    bool same_types(std::span<const ir::Type* const> one, std::span<const ir::Type* const> other)
    {
      return std::ranges::equal(one, other, [](auto a, auto b) { return ir::same(a, b); });
    }

  } // namespace


  // -- constants --------------------------------------------------------------

  const std::string lambda_prefix = std::string(names::builtin_glyph) + "lambda";
  const std::string shim_prefix = std::string(names::builtin_glyph) + "through";
  const std::string quote_name = std::string(names::builtin_glyph) + "quote";
  const std::string fill_name = std::string(names::builtin_glyph) + "fill";
  const std::string fill_number_name = std::string(names::builtin_glyph) + "fillnumber";
  const std::string fill_text_name = std::string(names::builtin_glyph) + "filltext";
  const std::string refuse_name = std::string(names::builtin_glyph) + "refuse";
  const std::string head_name = std::string(names::builtin_glyph) + "head";
  const std::string kind_name = std::string(names::builtin_glyph) + "kind";
  const std::string piece_name = std::string(names::builtin_glyph) + "name";
  const std::string parts_name = std::string(names::builtin_glyph) + "parts";
  const std::string part_name = std::string(names::builtin_glyph) + "part";
  const std::string apply_name = std::string(names::builtin_glyph) + "apply";
  const std::string alike_pieces_name = std::string(names::builtin_glyph) + "alike";
  const std::string caller_name_name = std::string(names::builtin_glyph) + "caller_name";
  const std::string array_pair = std::string(names::array_open_glyph) + std::string(names::array_close_glyph);
  const std::string table_pair = std::string(names::set_open_glyph) + std::string(names::set_close_glyph);
  const std::string dummy_prefix{ names::lift_open_glyph };

  // Python's `id(object())`: the identity of something nothing else is.
  namespace {
    const char image_marker = 0;
  }
  const std::int64_t image_key = id_of(&image_marker);

  const std::vector<std::string> si_units{ "ampere", "candela", "gram", "kelvin", "meter", "mole", "second" };
  const std::vector<std::string> counting_units{ "idx", "size" };

  const Dict<std::string, const ir::Unit*>& builtin_units()
  {
    static const Dict<std::string, const ir::Unit*> units = [] {
      Dict<std::string, const ir::Unit*> made;
      for (const auto& list : { &si_units, &counting_units })
        for (const auto& name : *list)
          made[name] = ir::Unit::make({ { name, 1 } }, ir::Rational{});
      return made;
    }();
    return units;
  }

  const ir::Unit* idx_unit() { return *builtin_units().get("idx"); }
  const ir::Unit* size_unit() { return *builtin_units().get("size"); }

  const ir::IntType* size_type()
  {
    static const ir::IntType* made = ir::int_type(64, false, size_unit());
    return made;
  }

  const ir::PtrType* environment_type()
  {
    static const ir::PtrType* made = ir::stray_ptr_type(ir::u8(), true);
    return made;
  }

  ir::GlobalVar* startup_record()
  {
    static const std::unique_ptr<ir::GlobalVar> made
      = std::make_unique<ir::GlobalVar>("__pl4g_init", ir::u8(), ir::stray_ptr_type(ir::u8(), true));
    return made.get();
  }

  const ir::DataLayout& layout()
  {
    static const ir::DataLayout made{ .pointer_size = 8 };
    return made;
  }

  const std::vector<std::pair<const ir::Type*, std::int64_t>>& chunks()
  {
    static const std::vector<std::pair<const ir::Type*, std::int64_t>> made{
      { ir::u64(), 8 }, { ir::u32(), 4 }, { ir::int_type(16, false), 2 }, { ir::u8(), 1 } };
    return made;
  }

  const NameSet& every_target_knows()
  {
    static const NameSet made{ std::string(ir::default_cconv), std::string(ir::system_cconv) };
    return made;
  }

  const std::set<DiagId>& purity()
  {
    static const std::set<DiagId> made{ D::LANG_PURE_CALLS_IMPURE, D::LANG_PURE_CHANGES_A_VARIABLE,
                                        D::LANG_IO_WITHOUT_A_DEVICE, D::LANG_PURE_READS_THE_ROUNDING_MODE,
                                        D::LANG_PURE_WRITES_ELSEWHERE };
    return made;
  }

  const Dict<std::string, std::vector<std::string>>& binds()
  {
    static const Dict<std::string, std::vector<std::string>> made{
      { "VarDef", { "name", "more" } }, { "ForEach", { "name", "more" } }, { "Param", { "name" } },
      { "Capture", { "name" } },        { "Pattern", { "name" } },
    };
    return made;
  }

  const Dict<std::string, std::vector<const ir::Type*>>& asks_about_a_piece()
  {
    static const Dict<std::string, std::vector<const ir::Type*>> made{
      { refuse_name, { ir::str_type() } },      { piece_name, { ir::syntax_type() } },
      { caller_name_name, { ir::str_type() } }, { head_name, { ir::syntax_type() } },
      { parts_name, { ir::syntax_type() } },    { part_name, { ir::syntax_type(), ir::u64() } },
    };
    return made;
  }

  const Dict<std::string, const ir::Type*>& syntax_answers()
  {
    static const Dict<std::string, const ir::Type*> made{
      { quote_name, ir::syntax_type() },       { fill_name, ir::syntax_type() },
      { fill_number_name, ir::syntax_type() }, { fill_text_name, ir::syntax_type() },
      { refuse_name, ir::syntax_type() },      { head_name, ir::syntax_type() },
      { kind_name, ir::u64() },                { piece_name, ir::str_type() },
      { parts_name, ir::u64() },               { part_name, ir::syntax_type() },
      { apply_name, ir::syntax_type() },       { alike_pieces_name, ir::bool_type() },
      { caller_name_name, ir::syntax_type() },
    };
    return made;
  }


  // -- the operator tables ----------------------------------------------------

  using ast::BinaryOp;
  using ast::UnaryOp;
  using ir::BinOp;
  using ir::CmpPred;

  const std::set<BinaryOp>& derives()
  {
    static const std::set<BinaryOp> made{ BinaryOp::multiply, BinaryOp::divide };
    return made;
  }

  const std::map<BinaryOp, std::pair<CmpPred, CmpPred>>& comparisons()
  {
    static const std::map<BinaryOp, std::pair<CmpPred, CmpPred>> made{
      { BinaryOp::equal, { CmpPred::eq, CmpPred::eq } },
      { BinaryOp::not_equal, { CmpPred::ne, CmpPred::ne } },
      { BinaryOp::less, { CmpPred::slt, CmpPred::ult } },
      { BinaryOp::greater, { CmpPred::sgt, CmpPred::ugt } },
      { BinaryOp::less_equal, { CmpPred::sle, CmpPred::ule } },
      { BinaryOp::greater_equal, { CmpPred::sge, CmpPred::uge } },
    };
    return made;
  }

  const std::map<BinaryOp, std::pair<BinOp, bool>>& logic_ops()
  {
    static const std::map<BinaryOp, std::pair<BinOp, bool>> made{
      { BinaryOp::logic_and, { BinOp::and_, false } }, { BinaryOp::logic_or, { BinOp::or_, false } },
      { BinaryOp::logic_xor, { BinOp::xor_, false } }, { BinaryOp::logic_nand, { BinOp::and_, true } },
      { BinaryOp::logic_nor, { BinOp::or_, true } },
    };
    return made;
  }

  const std::map<BinaryOp, bool>& short_circuit()
  {
    static const std::map<BinaryOp, bool> made{ { BinaryOp::short_and, false }, { BinaryOp::short_or, true } };
    return made;
  }

  const std::map<BinaryOp, std::pair<BinOp, BinOp>>& shifts()
  {
    static const std::map<BinaryOp, std::pair<BinOp, BinOp>> made{
      { BinaryOp::shift_left, { BinOp::shl, BinOp::shl } },
      { BinaryOp::shift_right, { BinOp::ashr, BinOp::lshr } },
      { BinaryOp::rotate_left, { BinOp::rotl, BinOp::rotl } },
      { BinaryOp::rotate_right, { BinOp::rotr, BinOp::rotr } },
    };
    return made;
  }

  const std::set<BinaryOp>& orderings()
  {
    static const std::set<BinaryOp> made{ BinaryOp::less, BinaryOp::greater, BinaryOp::less_equal,
                                          BinaryOp::greater_equal };
    return made;
  }

  const std::map<BinaryOp, std::tuple<bool, bool, CmpPred>>& approximate()
  {
    static const std::map<BinaryOp, std::tuple<bool, bool, CmpPred>> made{
      { BinaryOp::alike, { false, true, CmpPred::sle } },
      { BinaryOp::unalike, { false, true, CmpPred::sgt } },
      { BinaryOp::below_or_alike, { false, false, CmpPred::sle } },
      { BinaryOp::above_or_alike, { true, false, CmpPred::sle } },
      { BinaryOp::below_not_alike, { true, false, CmpPred::sgt } },
      { BinaryOp::above_not_alike, { false, false, CmpPred::sgt } },
    };
    return made;
  }

  const std::set<BinaryOp>& exact_on_floats()
  {
    static const std::set<BinaryOp> made{ BinaryOp::equal, BinaryOp::not_equal };
    return made;
  }

  const std::set<BinaryOp>& all_at_once()
  {
    static const std::set<BinaryOp> made{
      BinaryOp::bit_and,    BinaryOp::bit_or,       BinaryOp::bit_xor,     BinaryOp::add,
      BinaryOp::subtract,   BinaryOp::multiply,     BinaryOp::sat_add,     BinaryOp::sat_sub,
      BinaryOp::sat_mul,    BinaryOp::equal,        BinaryOp::not_equal,   BinaryOp::less,
      BinaryOp::greater,    BinaryOp::less_equal,   BinaryOp::greater_equal, BinaryOp::logic_and,
      BinaryOp::logic_or,   BinaryOp::logic_xor,    BinaryOp::logic_nand,  BinaryOp::logic_nor,
      BinaryOp::shift_left, BinaryOp::shift_right,  BinaryOp::rotate_left, BinaryOp::rotate_right,
      BinaryOp::max,        BinaryOp::min,
    };
    return made;
  }

  const std::set<UnaryOp>& all_at_once_unary()
  {
    static const std::set<UnaryOp> made{ UnaryOp::logic_not, UnaryOp::bit_not };
    return made;
  }

  const std::map<BinOp, BinOp>& wrapped()
  {
    static const std::map<BinOp, BinOp> made{
      { BinOp::add, BinOp::wrap_add },   { BinOp::sub, BinOp::wrap_sub },   { BinOp::mul, BinOp::wrap_mul },
      { BinOp::shl, BinOp::wrap_shl },   { BinOp::ashr, BinOp::wrap_ashr }, { BinOp::lshr, BinOp::wrap_lshr },
      { BinOp::rotl, BinOp::wrap_rotl }, { BinOp::rotr, BinOp::wrap_rotr },
    };
    return made;
  }

  const std::set<BinaryOp>& saturates()
  {
    static const std::set<BinaryOp> made{ BinaryOp::sat_add, BinaryOp::sat_sub, BinaryOp::sat_mul };
    return made;
  }

  const std::map<BinaryOp, std::pair<BinOp, BinOp>>& extrema()
  {
    static const std::map<BinaryOp, std::pair<BinOp, BinOp>> made{
      { BinaryOp::max, { BinOp::smax, BinOp::umax } },
      { BinaryOp::min, { BinOp::smin, BinOp::umin } },
    };
    return made;
  }

  const std::map<BinaryOp, BinOp>& binary_ops()
  {
    static const std::map<BinaryOp, BinOp> made{
      { BinaryOp::bit_and, BinOp::and_ },   { BinaryOp::bit_or, BinOp::or_ },       { BinaryOp::bit_xor, BinOp::xor_ },
      { BinaryOp::add, BinOp::add },        { BinaryOp::subtract, BinOp::sub },     { BinaryOp::multiply, BinOp::mul },
      { BinaryOp::sat_add, BinOp::sat_add }, { BinaryOp::sat_sub, BinOp::sat_sub }, { BinaryOp::sat_mul, BinOp::sat_mul },
    };
    return made;
  }

  const std::set<BinaryOp>& on_flags()
  {
    static const std::set<BinaryOp> made{ BinaryOp::bit_and, BinaryOp::bit_or, BinaryOp::bit_xor };
    return made;
  }

  const std::set<BinaryOp>& on_sets()
  {
    static const std::set<BinaryOp> made{ BinaryOp::bit_and, BinaryOp::bit_or, BinaryOp::bit_xor,
                                          BinaryOp::subtract };
    return made;
  }

  const std::set<BinaryOp>& on_floats()
  {
    static const std::set<BinaryOp> made{ BinaryOp::add, BinaryOp::subtract, BinaryOp::multiply,
                                          BinaryOp::divide };
    return made;
  }

  const std::map<UnaryOp, ir::UnOp>& unary_ops()
  {
    static const std::map<UnaryOp, ir::UnOp> made{ { UnaryOp::bit_not, ir::UnOp::not_ } };
    return made;
  }

  const std::set<BinaryOp>& divides()
  {
    static const std::set<BinaryOp> made{ BinaryOp::divides, BinaryOp::not_divides };
    return made;
  }

  const std::set<UnaryOp>& divides_unary()
  {
    static const std::set<UnaryOp> made{ UnaryOp::divides, UnaryOp::not_divides };
    return made;
  }

  const std::map<UnaryOp, ir::UnOp>& roundings()
  {
    static const std::map<UnaryOp, ir::UnOp> made{
      { UnaryOp::floor, ir::UnOp::floor },
      { UnaryOp::ceiling, ir::UnOp::ceil },
      { UnaryOp::nearest, ir::UnOp::nearest },
      { UnaryOp::rounded, ir::UnOp::rounded },
    };
    return made;
  }

  const NameSet& said()
  {
    static const NameSet made{ "fatal", "error", "warning", "note" };
    return made;
  }

  bool is_each_step(const ast::Node* node)
  {
    return ast::is<ast::Member>(node) || ast::is<ast::Element>(node) || ast::is<ast::Index>(node)
      || ast::is<ast::Deref>(node) || ast::is<ast::Raised>(node) || ast::is<ast::Try>(node);
  }

  bool is_calling(const ast::Node* node)
  {
    return ast::is<ast::Call>(node) || ast::is<ast::Fresh>(node) || ast::is<ast::Allocated>(node)
      || ast::is<ast::Binary>(node) || ast::is<ast::Unary>(node);
  }

  bool is_applying(const ast::Node* node)
  {
    return ast::is<ast::Binary>(node) || ast::is<ast::Unary>(node) || ast::is<ast::Fresh>(node)
      || ast::is<ast::Call>(node) || ast::is<ast::Element>(node) || ast::is<ast::Index>(node);
  }

  std::int64_t container_key(const void* local)
  {
    return -2 - 2 * id_of(local);
  }

  bool is_container_key(std::int64_t key)
  {
    return key <= -2 && key % 2 == 0;
  }

  std::int64_t element_key(std::int64_t key)
  {
    return key - 1;
  }

  bool is_element_key(std::int64_t key)
  {
    // Python's `%` takes the sign of the divisor, so `key % 2 != 0` is C++'s
    // remainder being anything but nought.
    return key <= -3 && key % 2 != 0;
  }


  // -- records ----------------------------------------------------------------

  bool overlap(std::span<const std::string> one, std::span<const std::string> other)
  {
    auto shorter = std::min(one.size(), other.size());
    return std::ranges::equal(one.first(shorter), other.first(shorter));
  }

  bool NamedType::takes_types() const
  {
    auto def = ast::as<ast::TypeDef>(node);
    return def != nullptr && ! def->params.empty() && ! bound.has_value();
  }


  // -- helpers ----------------------------------------------------------------

  Span doc_span(std::span<const Span> lines, doc::Part part)
  {
    if (lines.empty())
      return invalid_span;
    const Span& line = 0 <= part.line && std::size_t(part.line) < lines.size() ? lines[part.line] : lines[0];
    Loc start = line.start + part.column;
    // Python counts the word in characters; the span is in bytes.
    return { start, std::min<Loc>(start + Loc(part.word.size()) + 1, std::max<Loc>(line.end, start + 1)) };
  }

  std::string found_name(std::string_view prefix, std::string_view base)
  {
    return prefix.empty() ? std::string(base) : std::format("{}.{}", prefix, base);
  }

  std::vector<std::string> assigned_in(const ast::Block* block)
  {
    std::vector<std::string> found;
    collect_assigned(block, found);
    return found;
  }

  void collect_assigned(const ast::Block* block, std::vector<std::string>& into)
  {
    for (auto stmt : block->stmts()) {
      if (auto assign = ast::as<ast::AssignStmt>(stmt)) {
        into.emplace_back(assign->name);
        for (const auto& one : assign->more)
          into.emplace_back(one.name);
        continue;
      }
      auto expr = ast::as<ast::ExprStmt>(stmt);
      if (expr == nullptr)
        continue;
      if (auto looped = ast::as<ast::While>(expr->value)) {
        collect_assigned(looped->body, into);
        if (looped->alternative != nullptr)
          collect_assigned(looped->alternative, into);
      } else if (auto each = ast::as<ast::ForEach>(expr->value)) {
        collect_assigned(each->body, into);
        if (each->alternative != nullptr)
          collect_assigned(each->alternative, into);
      } else if (auto asked = ast::as<ast::If>(expr->value)) {
        for (auto arm : asked->arms)
          collect_assigned(arm->body, into);
      } else if (auto matched = ast::as<ast::Match>(expr->value)) {
        for (auto arm : matched->arms)
          collect_assigned(arm->body, into);
      }
    }
  }

  void addressed_in(const ast::Node* node, NameSet& into)
  {
    if (node == nullptr)
      return;
    if (auto taken = ast::as<ast::AddressOf>(node))
      if (auto name = ast::as<ast::NameRef>(taken->operand))
        into.emplace(name->name);
    if (auto taken = ast::as<ast::TakeAt>(node))
      if (auto name = ast::as<ast::NameRef>(taken->base))
        into.emplace(name->name);
    if (auto joined = ast::as<ast::JoinAssign>(node))
      if (auto name = ast::as<ast::NameRef>(joined->target))
        into.emplace(name->name);
    if (auto assigned = ast::as<ast::ElementAssign>(node))
      if (auto name = ast::as<ast::NameRef>(assigned->base))
        into.emplace(name->name);
    if (auto call = ast::as<ast::Call>(node)) {
      auto callee = callee_name(call);
      if (callee != nullptr && callee->name == names::reserve_name && ! call->args.empty())
        if (auto name = ast::as<ast::NameRef>(call->args[0]))
          into.emplace(name->name);
      if (callee != nullptr && callee->name == names::iter_name && call->args.size() == 1)
        if (auto name = ast::as<ast::NameRef>(call->args[0]))
          into.emplace(name->name);
    }
    if (auto capture = ast::as<ast::Capture>(node); capture != nullptr && capture->by_reference)
      into.emplace(capture->name);
    if (auto lambda = ast::as<ast::Lambda>(node);
        lambda != nullptr && lambda->brings_in == ast::CaptureAll::by_reference) {
      std::vector<std::string> written;
      named_in(lambda->body, written);
      into.insert(written.begin(), written.end());
    }
    ast::for_each_child(node, [&](const ast::Node* one) { addressed_in(one, into); });
  }

  void read_through(const ast::Node* node, KeySet& into)
  {
    if (node == nullptr)
      return;
    if (auto deref = ast::as<ast::Deref>(node))
      if (auto name = ast::as<ast::NameRef>(deref->operand))
        into.insert(id_of(name));
    if (auto assigned = ast::as<ast::DerefAssign>(node))
      if (auto name = ast::as<ast::NameRef>(assigned->target))
        into.insert(id_of(name));
    ast::for_each_child(node, [&](const ast::Node* one) { read_through(one, into); });
  }

  const ir::Type* shell_for(NamedType* defined)
  {
    auto node = ast::as<ast::TypeDef>(defined->node);
    if (node == nullptr)
      return nullptr;
    std::vector<const ir::Type*> args;
    if (defined->bound)
      for (const auto& [_, ty] : *defined->bound)
        args.push_back(ty);
    if (node->kind == ast::TypeKind::sum) {
      auto made = ir::new_sum();
      made->name = defined->name;
      made->origin = defined->origin;
      made->family = defined->template_;
      made->args = std::move(args);
      return made;
    }
    auto made = ir::new_product();
    made->name = defined->name;
    made->origin = defined->origin;
    made->abi = defined->abi;
    made->unique = defined->unique;
    made->device = defined->device;
    made->closed_by = defined->closed_by;
    // A frozenset in Python, whose order means nothing: kept sorted.
    auto fields = mutable_fields(node);
    made->mutable_fields.assign(fields.begin(), fields.end());
    made->family = defined->template_;
    made->args = std::move(args);
    return made;
  }

  const ir::Type* filled_in(const ir::Type* shell, const ir::Type* made)
  {
    auto product = ir::as<ir::ProductType>(shell);
    auto product_made = ir::as<ir::ProductType>(made);
    if (product != nullptr && product_made != nullptr) {
      const_cast<ir::ProductType*>(product)->fields = product_made->fields;
      return shell;
    }
    auto sum = ir::as<ir::SumType>(shell);
    auto sum_made = ir::as<ir::SumType>(made);
    if (sum != nullptr && sum_made != nullptr) {
      const_cast<ir::SumType*>(sum)->variants = sum_made->variants;
      return shell;
    }
    return made;
  }

  const ir::Unit* unit_of(const ir::Type* ty)
  {
    if (auto i = ir::as<ir::IntType>(ty))
      return i->unit;
    if (auto f = ir::as<ir::FloatType>(ty))
      return f->unit;
    return ir::Unit::none();
  }

  const ir::Type* carrying(const ir::Type* ty, const ir::Unit* unit)
  {
    if (auto i = ir::as<ir::IntType>(ty))
      return ir::int_type(i->bits, i->is_signed, unit);
    if (auto f = ir::as<ir::FloatType>(ty))
      return ir::float_type(f->bits, unit);
    return ty;
  }

  std::vector<std::string> children(const ast::Node* node)
  {
    // The names of the fields a walk descends into, which is every field but
    // the span; asked of nothing in this port, `for_each_child` being the walk.
    std::vector<std::string> found;
    ast::visit(*node, [&]<typename T>(const T&) {
      constexpr auto ctx = std::meta::access_context::unchecked();
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, ctx)))
        if constexpr (std::meta::annotations_of_with_type(m, ^^ast::Hidden).size() == 0)
          found.emplace_back(std::meta::identifier_of(m));
    });
    return found;
  }

  void lambdas_handed_on(const ast::Node* node, const std::function<bool(const ast::Call*)>& calls,
                         NameSet& taken, std::vector<const ast::Lambda*>& handed,
                         std::vector<std::pair<std::string, const ast::Lambda*>>& bound)
  {
    if (node == nullptr)
      return;
    if (auto name = ast::as<ast::NameRef>(node)) {
      taken.emplace(name->name);
      return;
    }
    if (auto capture = ast::as<ast::Capture>(node)) {
      taken.emplace(capture->name);
      return;
    }
    if (ast::is<ast::Invoke>(node) || ast::is<ast::Quote>(node)) {
      std::vector<std::string> seen;
      named_in(node, seen);
      taken.insert(seen.begin(), seen.end());
      return;
    }
    if (auto assign = ast::as<ast::AssignStmt>(node)) {
      taken.emplace(assign->name);
      for (const auto& one : assign->more)
        taken.emplace(one.name);
    } else if (auto def = ast::as<ast::VarDef>(node)) {
      if (auto lambda = ast::as<ast::Lambda>(def->value);
          lambda != nullptr && ! def->mutable_ && def->more.empty() && ! def->made_in)
        bound.emplace_back(std::string(def->name), lambda);
    } else if (auto call = ast::as<ast::Call>(node)) {
      bool handing = calls(call);
      if (! ast::is<ast::NameRef>(call->callee))
        lambdas_handed_on(call->callee, calls, taken, handed, bound);
      for (auto one : call->args) {
        auto value = argument_value(one);
        if (handing && ast::is<ast::NameRef>(value))
          continue;
        if (auto lambda = ast::as<ast::Lambda>(value); handing && lambda != nullptr)
          handed.push_back(lambda);
        lambdas_handed_on(one, calls, taken, handed, bound);
      }
      return;
    }
    ast::for_each_child(node, [&](const ast::Node* one) { lambdas_handed_on(one, calls, taken, handed, bound); });
  }

  void named_in(const ast::Node* node, std::vector<std::string>& into)
  {
    if (node == nullptr)
      return;
    auto add = [&](std::string_view name) {
      if (std::ranges::find(into, name) == into.end())
        into.emplace_back(name);
    };
    if (auto name = ast::as<ast::NameRef>(node))
      add(name->name);
    if (auto capture = ast::as<ast::Capture>(node))
      add(capture->name);
    if (auto assign = ast::as<ast::AssignStmt>(node)) {
      add(assign->name);
      for (const auto& one : assign->more)
        add(one.name);
    }
    ast::for_each_child(node, [&](const ast::Node* one) { named_in(one, into); });
  }

  void bound_in(const ast::Node* node, NameSet& into)
  {
    if (node == nullptr)
      return;
    if (auto def = ast::as<ast::VarDef>(node)) {
      into.emplace(def->name);
      for (const auto& one : def->more)
        into.emplace(one.name);
    } else if (auto each = ast::as<ast::ForEach>(node)) {
      into.emplace(each->name);
      for (const auto& one : each->more)
        into.emplace(one.name);
    } else if (auto param = ast::as<ast::Param>(node)) {
      into.emplace(param->name);
    } else if (auto capture = ast::as<ast::Capture>(node)) {
      into.emplace(capture->name);
    } else if (auto pattern = ast::as<ast::Pattern>(node)) {
      if (pattern->name)
        into.emplace(*pattern->name);
    }
    ast::for_each_child(node, [&](const ast::Node* one) { bound_in(one, into); });
  }

  bool is_generic(std::string_view name)
  {
    return name.ends_with(generic_mark);
  }

  void parameters_in(const ast::Node* written, std::vector<std::string>& into)
  {
    if (written == nullptr)
      return;
    if (auto ref = ast::as<ast::TypeRef>(written);
        ref != nullptr && ! ref->module && is_generic(ref->name) && std::ranges::find(into, ref->name) == into.end())
      into.emplace_back(ref->name);
    ast::for_each_child(written, [&](const ast::Node* one) { parameters_in(one, into); });
  }

  void type_names_in(const ast::Node* written, std::vector<std::string>& into)
  {
    if (written == nullptr)
      return;
    if (auto ref = ast::as<ast::NameRef>(written);
        ref != nullptr && is_generic(ref->name) && std::ranges::find(into, ref->name) == into.end())
      into.emplace_back(ref->name);
    ast::for_each_child(written, [&](const ast::Node* one) { type_names_in(one, into); });
  }

  void holes_of(const ast::Node* node, std::vector<const ast::Hole*>& into)
  {
    if (node == nullptr)
      return;
    if (auto hole = ast::as<ast::Hole>(node)) {
      into.push_back(hole);
      return;
    }
    ast::for_each_child(node, [&](const ast::Node* one) { holes_of(one, into); });
  }

  std::string written_name(const ast::FuncDef* node)
  {
    return node->held ? std::format("{}.{}", *node->held, node->name) : std::string(node->name);
  }

  std::string declared_as(const ast::FuncDef* node)
  {
    if (! is_operator_name(node->name))
      return written_name(node);
    return std::format("{}/{}", written_name(node), node->params.size());
  }

  bool holds_a_piece(const ir::Type* ty, const KeySet& seen)
  {
    if (ir::is<ir::SyntaxType>(ty))
      return true;
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members,
                                 [&](auto m) { return holds_a_piece(m, deeper); });
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return holds_a_piece(r->ok, deeper) || (r->err != nullptr && holds_a_piece(r->err, deeper));
    }
    case ir::TypeKind::product:
      return std::ranges::any_of(ir::as<ir::ProductType>(ty)->fields,
                                 [&](const auto& f) { return holds_a_piece(f.second, deeper); });
    case ir::TypeKind::array:
      return holds_a_piece(ir::as<ir::ArrayType>(ty)->element, deeper);
    case ir::TypeKind::list:
      return holds_a_piece(ir::as<ir::ListType>(ty)->element, deeper);
    case ir::TypeKind::set:
      return holds_a_piece(ir::as<ir::SetType>(ty)->element, deeper);
    case ir::TypeKind::vec:
      return holds_a_piece(ir::as<ir::VecType>(ty)->element, deeper);
    case ir::TypeKind::ptr:
      return holds_a_piece(ir::as<ir::PtrType>(ty)->pointee, deeper);
    default:
      return false;
    }
  }

  bool of_its_own(const ir::Type* ty)
  {
    if (auto p = ir::as<ir::PtrType>(ty))
      return of_its_own(p->pointee);
    if (auto t = ir::as<ir::ProductType>(ty))
      return ! t->name.empty();
    if (auto t = ir::as<ir::SumType>(ty))
      return ! t->name.empty();
    if (auto t = ir::as<ir::EnumType>(ty))
      return ! t->name.empty();
    return false;
  }

  bool can_be_referred_to(const ir::Type* ty)
  {
    return ir::is<ir::IntType>(ty) || ir::is<ir::FloatType>(ty) || ir::is<ir::EnumType>(ty) || ir::is<ir::PtrType>(ty)
      || ir::is<ir::ProductType>(ty) || ir::is<ir::SumType>(ty) || ir::is<ir::OpaqueType>(ty)
      || ty == ir::bool_type() || ty == ir::char_type();
  }

  bool holds_a_lambda(const ir::Type* ty, const KeySet& seen)
  {
    if (ir::is<ir::FuncType>(ty))
      return true;
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members,
                                 [&](auto m) { return holds_a_lambda(m, deeper); });
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return holds_a_lambda(r->ok, deeper) || (r->err != nullptr && holds_a_lambda(r->err, deeper));
    }
    case ir::TypeKind::product:
      return std::ranges::any_of(ir::as<ir::ProductType>(ty)->fields,
                                 [&](const auto& f) { return holds_a_lambda(f.second, deeper); });
    case ir::TypeKind::array:
      return holds_a_lambda(ir::as<ir::ArrayType>(ty)->element, deeper);
    case ir::TypeKind::list:
      return holds_a_lambda(ir::as<ir::ListType>(ty)->element, deeper);
    case ir::TypeKind::set:
      return holds_a_lambda(ir::as<ir::SetType>(ty)->element, deeper);
    case ir::TypeKind::vec:
      return holds_a_lambda(ir::as<ir::VecType>(ty)->element, deeper);
    default:
      return false;
    }
  }

  bool kept_anywhere(const ir::Type* ty)
  {
    switch (ty->kind) {
    case ir::TypeKind::func:
      return ir::as<ir::FuncType>(ty)->lasting;
    case ir::TypeKind::ptr:
      return ir::as<ir::PtrType>(ty)->lasting;
    case ir::TypeKind::running:
      return ir::as<ir::RunningType>(ty)->lasting;
    case ir::TypeKind::iter:
      return false;
    case ir::TypeKind::tuple:
      return std::ranges::all_of(ir::as<ir::TupleType>(ty)->members, kept_anywhere);
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return kept_anywhere(r->ok) && (r->err == nullptr || kept_anywhere(r->err));
    }
    case ir::TypeKind::product:
      return std::ranges::all_of(ir::as<ir::ProductType>(ty)->fields,
                                 [](const auto& f) { return kept_anywhere(f.second); });
    case ir::TypeKind::sum:
      return std::ranges::all_of(ir::as<ir::SumType>(ty)->variants,
                                 [](const auto& v) { return kept_anywhere(v.second); });
    default:
      return ! owned::points_somewhere(ty);
    }
  }

  std::string closer_of(const ir::Type* ty)
  {
    auto product = ir::as<ir::ProductType>(ty);
    return product != nullptr ? product->closed_by : std::string();
  }

  const ir::ProductType* closable_in(const ir::Type* ty, const KeySet& seen)
  {
    if (seen.contains(id_of(ty)))
      return nullptr;
    auto deeper = deeper_than(seen, ty);
    std::vector<const ir::Type*> parts;
    switch (ty->kind) {
    case ir::TypeKind::product: {
      auto product = ir::as<ir::ProductType>(ty);
      if (! product->closed_by.empty())
        return product;
      for (const auto& [_, one] : product->fields)
        parts.push_back(one);
      break;
    }
    case ir::TypeKind::sum:
      for (const auto& [_, one] : ir::as<ir::SumType>(ty)->variants)
        parts.push_back(one);
      break;
    case ir::TypeKind::tuple:
      parts = ir::as<ir::TupleType>(ty)->members;
      break;
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      parts.push_back(r->ok);
      if (r->err != nullptr)
        parts.push_back(r->err);
      break;
    }
    case ir::TypeKind::array:
      parts.push_back(ir::as<ir::ArrayType>(ty)->element);
      break;
    case ir::TypeKind::list:
      parts.push_back(ir::as<ir::ListType>(ty)->element);
      break;
    default:
      return nullptr;
    }
    for (auto one : parts)
      if (auto found = closable_in(one, deeper))
        return found;
    return nullptr;
  }

  bool made_where_written(const ast::Expr* expr, const ir::Type* ty)
  {
    auto call = ast::as<ast::Call>(expr);
    auto product = ir::as<ir::ProductType>(ty);
    if (call == nullptr || product == nullptr)
      return false;
    std::string_view named;
    if (auto name = ast::as<ast::NameRef>(call->callee))
      named = name->name;
    else if (auto member = ast::as<ast::Member>(call->callee))
      named = member->name;
    return named == product->name;
  }

  bool lifts_a_parameter(const ast::Expr* expr)
  {
    auto lifted = ast::as<ast::Lifted>(expr);
    if (lifted == nullptr)
      return false;
    auto ref = ast::as<ast::TypeRef>(lifted->written);
    return ref != nullptr && ! ref->module && ! ref->result && is_generic(ref->name);
  }

  NameSet mutable_fields(const ast::Node* node)
  {
    auto def = ast::as<ast::TypeDef>(node);
    if (def == nullptr || def->kind != ast::TypeKind::product)
      return {};
    NameSet found;
    for (auto one : def->fields)
      if (one->mutable_)
        found.emplace(one->name);
    return found;
  }

  bool has_mutable_field(const ir::Type* ty, const KeySet& seen)
  {
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::product: {
      auto product = ir::as<ir::ProductType>(ty);
      return ! product->mutable_fields.empty()
        || std::ranges::any_of(product->fields, [&](const auto& f) { return has_mutable_field(f.second, deeper); });
    }
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members,
                                 [&](auto m) { return has_mutable_field(m, deeper); });
    case ir::TypeKind::array:
      return has_mutable_field(ir::as<ir::ArrayType>(ty)->element, deeper);
    default:
      return false;
    }
  }

  std::string path_of(const ast::Expr* expr)
  {
    if (auto member = ast::as<ast::Member>(expr))
      return std::format("{}.{}", path_of(member->base), member->name);
    if (auto name = ast::as<ast::NameRef>(expr))
      return std::string(name->name);
    return "what this names";
  }

  bool one_turn(const ast::Expr* iterable)
  {
    auto range = ast::as<ast::Range>(iterable);
    if (range == nullptr || range->step != nullptr)
      return false;
    auto start = ast::as<ast::IntLit>(range->start);
    auto stop = ast::as<ast::IntLit>(range->stop);
    return start != nullptr && stop != nullptr && ! start->value.too_large && ! stop->value.too_large
      && stop->value.value - start->value.value == 1;
  }

  const ir::Type* only_member(const ir::Type* ty)
  {
    if (auto product = ir::as<ir::ProductType>(ty); product != nullptr && product->fields.size() == 1)
      return product->fields[0].second;
    if (auto tuple = ir::as<ir::TupleType>(ty); tuple != nullptr && tuple->members.size() == 1)
      return tuple->members[0];
    return nullptr;
  }

  bool plainly_lasting(const ast::Expr* expr)
  {
    if (ast::is<ast::StringLit>(expr))
      return true;
    if (auto call = ast::as<ast::Call>(expr)) {
      auto name = callee_name(call);
      return name != nullptr && call->args.size() == 1 && ast::is<ast::StringLit>(call->args[0])
        && name->name == names::bytes_name;
    }
    if (auto allocated = ast::as<ast::Allocated>(expr))
      if (auto name = ast::as<ast::NameRef>(allocated->arena))
        return name->name == names::static_name || name->name == names::heap_name;
    return false;
  }

  const ir::Type* short_lived(const ir::Type* ty)
  {
    std::vector<const ir::Type*> members;
    switch (ty->kind) {
    case ir::TypeKind::iter:
      return ty;
    case ir::TypeKind::running:
      return ir::as<ir::RunningType>(ty)->lasting ? nullptr : ty;
    case ir::TypeKind::tuple:
      members = ir::as<ir::TupleType>(ty)->members;
      break;
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      members.push_back(r->ok);
      if (r->err != nullptr)
        members.push_back(r->err);
      break;
    }
    case ir::TypeKind::array:
      members.push_back(ir::as<ir::ArrayType>(ty)->element);
      break;
    case ir::TypeKind::list:
      members.push_back(ir::as<ir::ListType>(ty)->element);
      break;
    default:
      return nullptr;
    }
    for (auto one : members)
      if (auto found = short_lived(one))
        return found;
    return nullptr;
  }

  bool lambdas_last(const ir::Type* ty, const KeySet& seen)
  {
    if (auto f = ir::as<ir::FuncType>(ty))
      return f->lasting;
    if (seen.contains(id_of(ty)))
      return true;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::tuple:
      return std::ranges::all_of(ir::as<ir::TupleType>(ty)->members, [&](auto m) { return lambdas_last(m, deeper); });
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return lambdas_last(r->ok, deeper) && (r->err == nullptr || lambdas_last(r->err, deeper));
    }
    case ir::TypeKind::product:
      return std::ranges::all_of(ir::as<ir::ProductType>(ty)->fields,
                                 [&](const auto& f) { return lambdas_last(f.second, deeper); });
    case ir::TypeKind::array:
      return lambdas_last(ir::as<ir::ArrayType>(ty)->element, deeper);
    case ir::TypeKind::list:
      return lambdas_last(ir::as<ir::ListType>(ty)->element, deeper);
    case ir::TypeKind::set:
      return lambdas_last(ir::as<ir::SetType>(ty)->element, deeper);
    case ir::TypeKind::vec:
      return lambdas_last(ir::as<ir::VecType>(ty)->element, deeper);
    case ir::TypeKind::dict: {
      auto d = ir::as<ir::DictType>(ty);
      return lambdas_last(d->key, deeper) && lambdas_last(d->value, deeper);
    }
    default:
      return true;
    }
  }

  NameSet lifetimes_of(const ast::Node* node, NameSet* found)
  {
    NameSet own;
    if (found == nullptr)
      found = &own;
    if (auto ref = ast::as<ast::RefTypeRef>(node); ref != nullptr && ref->lifetime)
      found->emplace(*ref->lifetime);
    if (node != nullptr)
      ast::for_each_child(node, [&](const ast::Node* one) { lifetimes_of(one, found); });
    return *found;
  }

  bool promises_as_much(const ir::Type* expected, const ir::Type* found, std::optional<std::string> free)
  {
    if (found == expected)
      return false;
    auto set_e = ir::as<ir::SetType>(expected);
    auto set_f = ir::as<ir::SetType>(found);
    if (set_e != nullptr && set_f != nullptr)
      return set_f->is_mutable && ! set_e->is_mutable && set_f->element == set_e->element;
    auto dict_e = ir::as<ir::DictType>(expected);
    auto dict_f = ir::as<ir::DictType>(found);
    if (dict_e != nullptr && dict_f != nullptr)
      return dict_f->is_mutable && ! dict_e->is_mutable && dict_f->key == dict_e->key && dict_f->value == dict_e->value;
    auto func_e = ir::as<ir::FuncType>(expected);
    auto func_f = ir::as<ir::FuncType>(found);
    if (func_e != nullptr && func_f != nullptr)
      return (func_f->listable || ! func_e->listable) && (func_f->lasting || ! func_e->lasting)
        && (! func_f->impure || func_e->impure) && (func_f->can_ignore || ! func_e->can_ignore)
        && func_f->hands_out == func_e->hands_out && same_types(func_f->params, func_e->params)
        && func_f->ret == func_e->ret && borrows_as_much(func_e, func_f, free);
    auto run_e = ir::as<ir::RunningType>(expected);
    auto run_f = ir::as<ir::RunningType>(found);
    if (run_e != nullptr && run_f != nullptr)
      return run_f->element == run_e->element && run_f->given == run_e->given && run_f->lasting && ! run_e->lasting;
    auto ptr_e = ir::as<ir::PtrType>(expected);
    auto ptr_f = ir::as<ir::PtrType>(found);
    if (ptr_e == nullptr || ptr_f == nullptr || ptr_f->pointee != ptr_e->pointee)
      return false;
    return (ptr_f->lasting || ! ptr_e->lasting) && (ptr_f->is_mutable || ! ptr_e->is_mutable);
  }

  bool borrows_as_much(const ir::FuncType* expected, const ir::FuncType* found, std::optional<std::string> free)
  {
    auto wanted = borrowed_table(expected);
    for (const auto& [leaf, borrowed] : found->borrows) {
      auto said_at = wanted.find(leaf);
      if (said_at == wanted.end())
        return false;
      const auto& said = said_at->second;
      if (free && std::ranges::any_of(said.outer, [&](const auto& pair) { return pair.first == *free; }))
        continue;
      for (const auto& pair : borrowed.outer)
        if (std::ranges::find(said.outer, pair) == said.outer.end())
          return false;
      for (int param : borrowed.params) {
        if (std::ranges::find(said.params, param) == said.params.end())
          return false;
        auto mine = leaves_of(borrowed, param);
        auto theirs = leaves_of(said, param);
        if (theirs) {
          if (! mine)
            return false;
          for (int one : *mine)
            if (std::ranges::find(*theirs, one) == theirs->end())
              return false;
        }
      }
    }
    return true;
  }

  bool names_a_place(const ast::Expr* expr)
  {
    return ast::is<ast::NameRef>(expr) || ast::is<ast::Element>(expr) || ast::is<ast::Member>(expr)
      || ast::is<ast::Deref>(expr);
  }

  const ast::Expr* step_inner(const ast::Expr* node)
  {
    if (auto one = ast::as<ast::Member>(node))
      return one->base;
    if (auto one = ast::as<ast::Element>(node))
      return one->base;
    if (auto one = ast::as<ast::Index>(node))
      return one->base;
    if (auto one = ast::as<ast::Raised>(node))
      return one->base;
    if (auto one = ast::as<ast::Deref>(node))
      return one->operand;
    if (auto one = ast::as<ast::Try>(node))
      return one->operand;
    return nullptr;
  }

  namespace {

    // The same step, applied to something else, its copy made in `arena`.
    const ast::Expr* step_onto_in(ast::SyntaxArena& arena, const ast::Expr* node, const ast::Expr* inner)
    {
      if (auto one = ast::as<ast::Deref>(node)) {
        auto made = arena.copy(*one);
        made->operand = inner;
        return made;
      }
      if (auto one = ast::as<ast::Try>(node)) {
        auto made = arena.copy(*one);
        made->operand = inner;
        return made;
      }
      if (auto one = ast::as<ast::Member>(node)) {
        auto made = arena.copy(*one);
        made->base = inner;
        return made;
      }
      if (auto one = ast::as<ast::Element>(node)) {
        auto made = arena.copy(*one);
        made->base = inner;
        return made;
      }
      if (auto one = ast::as<ast::Index>(node)) {
        auto made = arena.copy(*one);
        made->base = inner;
        return made;
      }
      if (auto one = ast::as<ast::Raised>(node)) {
        auto made = arena.copy(*one);
        made->base = inner;
        return made;
      }
      throw InternalError("a step onto something that is no step");
    }

  } // namespace

  const ast::Expr* step_onto(const ast::Expr* node, const ast::Expr* inner)
  {
    // Python's `replace` makes a node nothing keeps; this one lives as long
    // as the process, which is what the checker's own nodes do.
    static ast::SyntaxArena arena;
    return step_onto_in(arena, node, inner);
  }

  std::optional<std::tuple<const ast::Expr*, std::int64_t, std::function<const ast::Expr*(ast::SyntaxArena&, const ast::Expr*)>>>
  marked(const ast::Expr* expr)
  {
    std::vector<const ast::Expr*> steps;
    auto node = expr;
    while (! ast::is<ast::Each>(node)) {
      auto inner = step_inner(node);
      if (inner == nullptr)
        return std::nullopt;
      steps.push_back(node);
      node = inner;
    }
    std::int64_t levels = 0;
    while (auto each = ast::as<ast::Each>(node)) {
      ++levels;
      node = each->operand;
    }
    auto apply = [steps = std::move(steps)](ast::SyntaxArena& arena, const ast::Expr* one) {
      for (auto step = steps.rbegin(); step != steps.rend(); ++step)
        one = step_onto_in(arena, *step, one);
      return one;
    };
    return std::tuple{ node, levels, std::function<const ast::Expr*(ast::SyntaxArena&, const ast::Expr*)>(apply) };
  }

  bool walks_per_place(const ast::Expr* expr)
  {
    if (ast::is<ast::Binary>(expr) || ast::is<ast::Unary>(expr))
      return true;
    if (auto call = ast::as<ast::Call>(expr))
      return std::ranges::any_of(call->args, [](auto one) { return marked(argument_value(one)).has_value(); });
    return marked(expr).has_value();
  }

  __int128 within(__int128 value, const ir::IntType* ty)
  {
    auto mask = ty->bits >= 128 ? ~__int128(0) : (__int128(1) << ty->bits) - 1;
    __int128 bits = value & mask;
    if (ty->is_signed && ((bits >> (ty->bits - 1)) & 1) != 0)
      bits -= __int128(1) << ty->bits;
    return bits;
  }

  bool contains(const ast::Node* node, ast::NodeKind kind)
  {
    if (node == nullptr)
      return false;
    if (node->tag == kind)
      return true;
    if (ast::is<ast::Lambda>(node))
      return false;
    bool found = false;
    ast::for_each_child(node, [&](const ast::Node* one) {
      if (! found)
        found = contains(one, kind);
    });
    return found;
  }

  ir::Value* is_nowhere(ir::Builder& builder, ir::Value* place, Span span)
  {
    auto bits = builder.cast(ir::CastKind::bitcast, place, ir::u64(), span);
    auto nought = builder.int_const(ir::u64(), 0);
    return builder.compare(ir::CmpPred::eq, bits, nought, span);
  }

  bool breaks(const ast::Node* node, std::string_view label)
  {
    if (node == nullptr)
      return false;
    if (auto jump = ast::as<ast::Break>(node); jump != nullptr && jump->label != nullptr && jump->label->name == label)
      return true;
    bool found = false;
    ast::for_each_child(node, [&](const ast::Node* one) {
      if (! found)
        found = breaks(one, label);
    });
    return found;
  }

  std::int64_t count(const ast::Node* node, ast::NodeKind kind)
  {
    if (node == nullptr)
      return 0;
    std::int64_t found = node->tag == kind ? 1 : 0;
    ast::for_each_child(node, [&](const ast::Node* one) { found += count(one, kind); });
    return found;
  }

  void mentioned(const ast::Node* node, NameSet& into)
  {
    if (node == nullptr)
      return;
    if (auto name = ast::as<ast::NameRef>(node))
      into.emplace(name->name);
    else if (auto member = ast::as<ast::Member>(node))
      into.emplace(member->name);
    else if (auto fresh = ast::as<ast::Fresh>(node))
      into.emplace(fresh->glyph);
    else if (ast::is<ast::ForEach>(node) || ast::is<ast::Yield>(node) || ast::is<ast::Call>(node))
      into.emplace(each_name);
    ast::for_each_child(node, [&](const ast::Node* one) { mentioned(one, into); });
  }

  void mentioned(std::span<const ast::SourceUnit* const> units, NameSet& into)
  {
    for (auto unit : units)
      mentioned(unit, into);
  }

  std::vector<std::string> called_in(const ast::Node* node)
  {
    std::vector<std::string> found;
    auto visit = [&](this auto& self, const ast::Node* one) -> void {
      if (one == nullptr)
        return;
      if (auto call = ast::as<ast::Call>(one))
        if (auto name = callee_name(call))
          found.emplace_back(name->name);
      ast::for_each_child(one, self);
    };
    visit(node);
    return found;
  }

  NameSet names_in(const ast::Node* node)
  {
    NameSet found;
    auto visit = [&](this auto& self, const ast::Node* one) -> void {
      if (one == nullptr)
        return;
      if (auto name = ast::as<ast::NameRef>(one))
        found.emplace(name->name);
      ast::for_each_child(one, self);
    };
    visit(node);
    return found;
  }

  NameSet names_bound_in(const ast::FuncDef* func)
  {
    NameSet found;
    for (auto param : func->params)
      found.emplace(param->name);
    auto visit = [&](this auto& self, const ast::Node* one) -> void {
      if (one == nullptr)
        return;
      if (auto def = ast::as<ast::VarDef>(one)) {
        found.emplace(def->name);
        for (const auto& more : def->more)
          found.emplace(more.name);
      } else if (auto each = ast::as<ast::ForEach>(one)) {
        found.emplace(each->name);
        for (const auto& more : each->more)
          found.emplace(more.name);
      } else if (auto pattern = ast::as<ast::Pattern>(one)) {
        if (pattern->name)
          found.emplace(*pattern->name);
      } else if (auto param = ast::as<ast::Param>(one)) {
        found.emplace(param->name);
      }
      ast::for_each_child(one, self);
    };
    visit(func->body);
    found.erase(std::string(names::wildcard_name));
    return found;
  }

  NameSet labels_in(const ast::Node* node)
  {
    NameSet found;
    auto visit = [&](this auto& self, const ast::Node* one) -> void {
      if (one == nullptr)
        return;
      if (auto label = ast::as<ast::Label>(one))
        found.emplace(label->name);
      ast::for_each_child(one, self);
    };
    visit(node);
    return found;
  }


  // -- rebuilding a tree --------------------------------------------------------

  namespace {

    using Change = std::function<const ast::Node*(const ast::Node*)>;

    const ast::Node* rebuild(ast::SyntaxArena& arena, const ast::Node* node, const Change& change, bool into_lambdas);

    // A member rebuilt: `value` with every node below it rebuilt, and whether
    // anything changed.  What is no node is left as it is, as Python leaves
    // what is neither a node nor a tuple.
    template<typename V>
    bool rebuild_value(ast::SyntaxArena& arena, V& value, const Change& change, bool into_lambdas)
    {
      if constexpr (std::is_pointer_v<V>) {
        using P = std::remove_cv_t<std::remove_pointer_t<V>>;
        if constexpr (std::is_base_of_v<ast::Node, P>) {
          if (value == nullptr)
            return false;
          auto made = rebuild(arena, value, change, into_lambdas);
          if (made == value)
            return false;
          value = static_cast<V>(made);
          return true;
        } else
          return false;
      } else if constexpr (std::is_same_v<V, ast::ExprPair>) {
        bool a = rebuild_value(arena, value.key, change, into_lambdas);
        bool b = rebuild_value(arena, value.value, change, into_lambdas);
        return a || b;
      } else if constexpr (std::is_same_v<V, ast::UnitPair>) {
        bool a = rebuild_value(arena, value.what, change, into_lambdas);
        bool b = rebuild_value(arena, value.where, change, into_lambdas);
        return a || b;
      } else if constexpr (requires { value.has_value(); *value; }) {
        if (! value.has_value())
          return false;
        return rebuild_value(arena, *value, change, into_lambdas);
      } else if constexpr (requires { typename V::element_type; value.size(); value.data(); }
                           && ! std::is_same_v<V, std::string_view>) {
        using E = std::remove_cv_t<typename V::element_type>;
        if constexpr (std::is_pointer_v<E> || std::is_same_v<E, ast::ExprPair> || std::is_same_v<E, ast::UnitPair>) {
          std::vector<E> items(value.begin(), value.end());
          bool changed = false;
          for (auto& one : items)
            changed |= rebuild_value(arena, one, change, into_lambdas);
          if (changed)
            value = arena.list(items);
          return changed;
        } else
          return false;
      } else
        return false;
    }

    // Every member of `node`, its bases' included, rebuilt in `copy`.
    template<typename T>
    bool rebuild_members(ast::SyntaxArena& arena, T& copy, const Change& change, bool into_lambdas)
    {
      constexpr auto ctx = std::meta::access_context::unchecked();
      bool changed = false;
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, ctx)))
        if constexpr (std::meta::annotations_of_with_type(m, ^^ast::Hidden).size() == 0)
          changed |= rebuild_value(arena, copy.[:m:], change, into_lambdas);
      template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, ctx)))
        changed |= rebuild_members(arena, static_cast<[:std::meta::type_of(b):]&>(copy), change, into_lambdas);
      return changed;
    }

    const ast::Node* rebuild(ast::SyntaxArena& arena, const ast::Node* node, const Change& change, bool into_lambdas)
    {
      if (ast::is<ast::Lambda>(node) && ! into_lambdas)
        return node;
      const ast::Node* now = node;
      if (auto block = ast::as<ast::Block>(node)) {
        std::vector<const ast::Stmt*> stmts;
        bool changed = false;
        for (auto stmt : block->stmts()) {
          auto made = rebuild(arena, stmt, change, into_lambdas);
          changed |= made != stmt;
          stmts.push_back(static_cast<const ast::Stmt*>(made));
        }
        if (changed) {
          auto copy = arena.copy(*block);
          copy->statements = arena.list(stmts);
          copy->job = nullptr;
          copy->done = true;
          now = copy;
        }
      } else
        now = ast::visit(*node, [&]<typename T>(const T& one) -> const ast::Node* {
          T copy = one;
          if (! rebuild_members(arena, copy, change, into_lambdas))
            return &one;
          return arena.copy(copy);
        });
      auto found = change(now);
      return found == nullptr ? now : found;
    }

  } // namespace

  const ast::Node* rebuilt(ast::SyntaxArena& arena, const ast::Node* node,
                           const std::function<const ast::Node*(const ast::Node*)>& change, bool into_lambdas)
  {
    if (node == nullptr)
      return nullptr;
    return rebuild(arena, node, change, into_lambdas);
  }

  const ast::TypeExpr* types_renamed(ast::SyntaxArena& arena, const ast::TypeExpr* written,
                                     const Dict<std::string, std::string>& types)
  {
    auto change = [&](const ast::Node* node) -> const ast::Node* {
      if (auto ref = ast::as<ast::TypeRef>(node); ref != nullptr && ! ref->module) {
        if (auto name = types.get(std::string(ref->name))) {
          auto made = arena.copy(*ref);
          made->name = kept_text(arena, *name);
          return made;
        }
      }
      return nullptr;
    };
    return static_cast<const ast::TypeExpr*>(rebuilt(arena, written, change));
  }

  const ast::Block* renamed(ast::SyntaxArena& arena, const ast::Block* body, const Dict<std::string, std::string>& names,
                            const Dict<std::string, std::string>& labels, const Dict<std::string, std::string>* types)
  {
    auto renamed_list = [&](ast::List<ast::NameAt> more) {
      std::vector<ast::NameAt> made;
      for (const auto& one : more) {
        auto name = names.get(std::string(one.name));
        made.push_back({ name != nullptr ? kept_text(arena, *name) : one.name, one.span });
      }
      return arena.list(made);
    };
    auto name_of = [&](ast::Text name) {
      auto found = names.get(std::string(name));
      return found != nullptr ? kept_text(arena, *found) : name;
    };
    auto change = [&](const ast::Node* node) -> const ast::Node* {
      if (auto ref = ast::as<ast::TypeRef>(node)) {
        if (types != nullptr && ! types->empty() && ! ref->module)
          if (auto name = types->get(std::string(ref->name))) {
            auto made = arena.copy(*ref);
            made->name = kept_text(arena, *name);
            return made;
          }
        return nullptr;
      }
      if (auto ref = ast::as<ast::NameRef>(node)) {
        if (! names.contains(std::string(ref->name)))
          return nullptr;
        auto made = arena.copy(*ref);
        made->name = name_of(ref->name);
        return made;
      }
      if (auto def = ast::as<ast::VarDef>(node)) {
        auto made = arena.copy(*def);
        made->more = renamed_list(def->more);
        made->name = name_of(def->name);
        return made;
      }
      if (auto each = ast::as<ast::ForEach>(node)) {
        auto made = arena.copy(*each);
        made->more = renamed_list(each->more);
        made->name = name_of(each->name);
        return made;
      }
      if (auto assign = ast::as<ast::AssignStmt>(node)) {
        auto made = arena.copy(*assign);
        made->more = renamed_list(assign->more);
        made->name = name_of(assign->name);
        return made;
      }
      if (auto pattern = ast::as<ast::Pattern>(node)) {
        if (! pattern->name || ! names.contains(std::string(*pattern->name)))
          return nullptr;
        auto made = arena.copy(*pattern);
        made->name = name_of(*pattern->name);
        return made;
      }
      if (auto param = ast::as<ast::Param>(node)) {
        if (! names.contains(std::string(param->name)))
          return nullptr;
        auto made = arena.copy(*param);
        made->name = name_of(param->name);
        return made;
      }
      if (auto capture = ast::as<ast::Capture>(node)) {
        if (! names.contains(std::string(capture->name)))
          return nullptr;
        auto made = arena.copy(*capture);
        made->name = name_of(capture->name);
        return made;
      }
      if (auto label = ast::as<ast::Label>(node)) {
        auto name = labels.get(std::string(label->name));
        if (name == nullptr)
          return nullptr;
        auto made = arena.copy(*label);
        made->name = kept_text(arena, *name);
        return made;
      }
      return nullptr;
    };
    return static_cast<const ast::Block*>(rebuilt(arena, body, change));
  }

  const ast::Block* continued(ast::SyntaxArena& arena, const ast::Block* body, std::string_view outer,
                              std::string_view inner)
  {
    auto change = [&](const ast::Node* node) -> const ast::Node* {
      auto jump = ast::as<ast::Continue>(node);
      if (jump == nullptr || jump->label == nullptr || jump->label->name != outer)
        return nullptr;
      auto label = arena.copy(*jump->label);
      label->name = kept_text(arena, inner);
      auto made = arena.copy(*jump);
      made->label = label;
      return made;
    };
    return static_cast<const ast::Block*>(rebuilt(arena, body, change));
  }

  const ast::Block* yields_replaced(ast::SyntaxArena& arena, const ast::Block* body,
                                    const std::function<const ast::Node*(const ast::Expr*)>& handed,
                                    std::string_view outer, const std::function<std::string()>& walk_name)
  {
    auto change = [&](const ast::Node* node) -> const ast::Node* {
      if (auto yield = ast::as<ast::Yield>(node)) {
        if (! yield->delegates)
          return handed(yield->value);
        auto name = kept_text(arena, walk_name());
        auto ref = arena.make<ast::NameRef>(yield->span);
        ref->name = name;
        auto inner = static_cast<const ast::Stmt*>(handed(ref));
        auto block = arena.make<ast::Block>(yield->span);
        block->style = ast::BlockStyle::layout;
        block->statements = arena.list(std::vector<const ast::Stmt*>{ inner });
        auto loop = arena.make<ast::ForEach>(yield->span);
        loop->name = name;
        loop->name_span = yield->span;
        loop->iterable = yield->value;
        loop->body = block;
        auto stmt = arena.make<ast::ExprStmt>(yield->span);
        stmt->value = loop;
        return stmt;
      }
      if (ast::is<ast::ReturnStmt>(node)) {
        auto label = arena.make<ast::Label>(node->span);
        label->name = kept_text(arena, outer);
        auto made = arena.make<ast::Continue>(node->span);
        made->label = label;
        return made;
      }
      return nullptr;
    };
    return static_cast<const ast::Block*>(rebuilt(arena, body, change, false));
  }

  bool stands_alone(const ast::Expr* expr)
  {
    return ast::is<ast::IntLit>(expr) || ast::is<ast::FloatLit>(expr) || ast::is<ast::BoolLit>(expr)
      || ast::is<ast::CharLit>(expr) || ast::is<ast::StringLit>(expr) || ast::is<ast::NameRef>(expr);
  }

  bool a_place_again(const ast::Expr* expr)
  {
    if (ast::is<ast::NameRef>(expr))
      return true;
    if (auto member = ast::as<ast::Member>(expr))
      return a_place_again(member->base);
    if (auto element = ast::as<ast::Element>(expr))
      return std::ranges::all_of(element->indices,
                                 [](auto one) { return ast::is<ast::NameRef>(one) || ast::is<ast::IntLit>(one); })
        && a_place_again(element->base);
    if (auto deref = ast::as<ast::Deref>(expr))
      return ast::is<ast::NameRef>(deref->operand);
    return false;
  }

  bool is_a_table(const ir::Type* ty)
  {
    return ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty);
  }

  bool read_only_table(const ir::Type* ty)
  {
    if (auto set = ir::as<ir::SetType>(ty))
      return ! set->is_mutable;
    if (auto dict = ir::as<ir::DictType>(ty))
      return ! dict->is_mutable;
    return false;
  }

  bool reaches_a_place(const ir::Type* ty)
  {
    return holds_a_reference(ty) || holds_a_lambda(ty);
  }

  std::string and_then(std::span<const std::string> names)
  {
    std::vector<std::string> quoted;
    for (const auto& one : names)
      quoted.push_back(std::format("'{}'", one));
    if (quoted.size() < 2)
      return quoted.empty() ? std::string() : quoted[0];
    std::string head;
    for (std::size_t at = 0; at + 1 < quoted.size(); ++at)
      head += (at == 0 ? "" : ", ") + quoted[at];
    return head + " and " + quoted.back();
  }

  void collect_written(const ast::Node* node, NameSet& into, NameSet& parts)
  {
    if (node == nullptr)
      return;
    if (auto assign = ast::as<ast::AssignStmt>(node)) {
      into.emplace(assign->name);
      for (const auto& one : assign->more)
        into.emplace(one.name);
    } else if (ast::is<ast::MemberAssign>(node) || ast::is<ast::ElementAssign>(node) || ast::is<ast::EntryAssign>(node)
               || ast::is<ast::JoinAssign>(node)) {
      const ast::Expr* root = nullptr;
      if (auto one = ast::as<ast::JoinAssign>(node))
        root = one->target;
      else if (auto one = ast::as<ast::MemberAssign>(node))
        root = one->base;
      else if (auto one = ast::as<ast::ElementAssign>(node))
        root = one->base;
      else
        root = ast::as<ast::EntryAssign>(node)->base;
      for (;;) {
        if (auto m = ast::as<ast::Member>(root))
          root = m->base;
        else if (auto e = ast::as<ast::Element>(root))
          root = e->base;
        else if (auto i = ast::as<ast::Index>(root))
          root = i->base;
        else
          break;
      }
      if (auto name = ast::as<ast::NameRef>(root))
        parts.emplace(name->name);
    } else if (ast::is<ast::Lambda>(node))
      return;
    ast::for_each_child(node, [&](const ast::Node* one) { collect_written(one, into, parts); });
  }

  bool holds_a_list(const ir::Type* ty)
  {
    if (ir::is<ir::ListType>(ty))
      return true;
    if (auto product = ir::as<ir::ProductType>(ty))
      return std::ranges::any_of(product->fields, [](const auto& f) { return holds_a_list(f.second); });
    if (auto tuple = ir::as<ir::TupleType>(ty))
      return std::ranges::any_of(tuple->members, holds_a_list);
    return false;
  }

  NameSet changed_names(const ast::Node* node)
  {
    NameSet found;
    auto root = [&](const ast::Expr* expr) {
      for (;;) {
        if (auto m = ast::as<ast::Member>(expr))
          expr = m->base;
        else if (auto e = ast::as<ast::Element>(expr))
          expr = e->base;
        else if (auto i = ast::as<ast::Index>(expr))
          expr = i->base;
        else if (auto each = ast::as<ast::Each>(expr))
          expr = each->operand;
        else
          break;
      }
      if (auto name = ast::as<ast::NameRef>(expr))
        found.emplace(name->name);
    };
    auto visit = [&](this auto& self, const ast::Node* one) -> void {
      if (one == nullptr)
        return;
      if (auto assign = ast::as<ast::AssignStmt>(one)) {
        found.emplace(assign->name);
        for (const auto& more : assign->more)
          found.emplace(more.name);
      } else if (auto s = ast::as<ast::MemberAssign>(one))
        root(s->base);
      else if (auto s = ast::as<ast::ElementAssign>(one))
        root(s->base);
      else if (auto s = ast::as<ast::EntryAssign>(one))
        root(s->base);
      else if (auto s = ast::as<ast::TakeAt>(one))
        root(s->base);
      else if (auto s = ast::as<ast::Take>(one))
        root(s->base);
      else if (auto s = ast::as<ast::TakeThrough>(one))
        root(s->operand);
      else if (auto s = ast::as<ast::JoinAssign>(one))
        root(s->target);
      else if (auto s = ast::as<ast::AddressOf>(one)) {
        if (s->mutable_)
          root(s->operand);
      } else if (auto s = ast::as<ast::Allocated>(one))
        root(s->arena);
      else if (auto call = ast::as<ast::Call>(one)) {
        auto name = callee_name(call);
        if (name != nullptr
            && (name->name == names::reserve_name || name->name == names::iter_name || name->name == names::empty_name)
            && ! call->args.empty())
          root(call->args[0]);
        else if (name != nullptr && name->name == names::address_name && ! call->args.empty()) {
          auto target = call->args[0];
          auto taken = ast::as<ast::AddressOf>(target);
          root(taken != nullptr ? taken->operand : target);
        } else if (auto member = ast::as<ast::Member>(call->callee))
          root(member->base);
      } else if (auto each = ast::as<ast::ForEach>(one)) {
        if (std::ranges::any_of(each->marks, [](auto mark) { return mark.find("mut") != std::string_view::npos; })) {
          auto walked = each->iterable;
          if (auto call = ast::as<ast::Call>(walked); call != nullptr && ! call->args.empty())
            walked = call->args[0];
          root(walked);
        }
      }
      ast::for_each_child(one, self);
    };
    visit(node);
    return found;
  }

  NameSet used_whole(const ast::Node* node)
  {
    std::unordered_set<const ast::Node*> reading;
    NameSet found;
    auto visit = [&](this auto& self, const ast::Node* one) -> void {
      if (one == nullptr)
        return;
      if (auto index = ast::as<ast::Index>(one))
        reading.insert(index->base);
      else if (auto unary = ast::as<ast::Unary>(one)) {
        if (unary->op == ast::UnaryOp::length)
          reading.insert(unary->operand);
      } else if (auto binary = ast::as<ast::Binary>(one)) {
        reading.insert(binary->left);
        reading.insert(binary->right);
      } else if (auto each = ast::as<ast::ForEach>(one))
        reading.insert(each->iterable);
      else if (auto name = ast::as<ast::NameRef>(one); name != nullptr && ! reading.contains(one))
        found.emplace(name->name);
      ast::for_each_child(one, self);
    };
    visit(node);
    return found;
  }

  std::vector<std::tuple<std::string, std::vector<std::int64_t>>> carried_lifetimes(const ast::FuncDef* node,
                                                                                     std::span<const ir::Type* const> types)
  {
    std::map<std::string, std::vector<std::int64_t>> found;
    for (std::size_t at = 0; at < node->params.size() && at < types.size(); ++at) {
      if (ir::is<ir::FuncType>(types[at]))
        continue;
      for (const auto& name : lifetimes_of(node->params[at]->type))
        found[name].push_back(std::int64_t(at));
    }
    std::vector<std::tuple<std::string, std::vector<std::int64_t>>> sorted;
    for (auto& [name, at] : found)
      sorted.emplace_back(name, std::move(at));
    return sorted;
  }

  const ast::FuncDef* kept_in_the_signature(const ast::FuncDef* node)
  {
    static ast::SyntaxArena arena;
    NameSet names;
    for (auto one : node->params)
      names.emplace(one->name);
    auto moved = [&](const ast::TypeExpr* ref) -> std::pair<const ast::TypeExpr*, std::optional<ast::NameAt>> {
      auto func = ast::as<ast::FuncTypeRef>(ref);
      if (func != nullptr && func->kept_in && names.contains(func->kept_in->name)) {
        auto made = arena.copy(*func);
        made->kept_in.reset();
        return { made, func->kept_in };
      }
      return { ref, std::nullopt };
    };
    bool changes = false;
    ast::FuncDef copy = *node;
    auto [ret, said] = moved(node->ret_type);
    if (said && node->made_in.empty()) {
      copy.ret_type = ret;
      copy.made_in = arena.list(std::vector<ast::NameAt>{ *said });
      changes = true;
    }
    std::vector<const ast::Param*> params;
    bool any_param = false;
    for (auto param : node->params) {
      auto [written, kept] = moved(param->type);
      if (kept && ! param->made_in) {
        auto made = arena.copy(*param);
        made->type = written;
        made->made_in = kept;
        params.push_back(made);
        any_param = true;
      } else
        params.push_back(param);
    }
    if (any_param) {
      copy.params = arena.list(params);
      changes = true;
    }
    return changes ? arena.copy(copy) : node;
  }

  bool changed_in_place(std::string_view name, const ast::Node* node)
  {
    auto rooted = [&](const ast::Expr* expr) {
      for (;;) {
        if (auto m = ast::as<ast::Member>(expr))
          expr = m->base;
        else if (auto e = ast::as<ast::Element>(expr))
          expr = e->base;
        else if (auto d = ast::as<ast::Deref>(expr))
          expr = d->operand;
        else
          break;
      }
      auto ref = ast::as<ast::NameRef>(expr);
      return ref != nullptr && ref->name == name;
    };
    bool found = false;
    auto visit = [&](this auto& self, const ast::Node* one) -> void {
      if (found || one == nullptr)
        return;
      if (auto join = ast::as<ast::JoinAssign>(one)) {
        if (rooted(join->target))
          found = true;
      } else if (auto assign = ast::as<ast::ElementAssign>(one)) {
        if (rooted(assign->base))
          found = true;
      } else if (auto take = ast::as<ast::TakeAt>(one)) {
        if (rooted(take->base))
          found = true;
      } else if (auto call = ast::as<ast::Call>(one)) {
        auto callee = callee_name(call);
        if (callee != nullptr && (callee->name == names::reserve_name || callee->name == names::iter_name)
            && ! call->args.empty() && rooted(call->args[0]))
          found = true;
      }
      ast::for_each_child(one, self);
    };
    visit(node);
    return found;
  }

  bool is_owning(const ir::Type* ty)
  {
    auto array = ir::as<ir::ArrayType>(ty);
    return array != nullptr && owned::points(array->element);
  }

  bool is_temporary(const ast::Expr* written)
  {
    while (auto allocated = ast::as<ast::Allocated>(written))
      written = allocated->value;
    switch (written->tag) {
    case ast::NodeKind::NameRef:
    case ast::NodeKind::Member:
    case ast::NodeKind::Element:
    case ast::NodeKind::Index:
    case ast::NodeKind::StringLit:
    case ast::NodeKind::Deref:
    case ast::NodeKind::Take:
    case ast::NodeKind::TakeAt:
    case ast::NodeKind::TakeThrough:
    case ast::NodeKind::If:
    case ast::NodeKind::Match:
    case ast::NodeKind::Scoped:
    case ast::NodeKind::Named:
      return false;
    default:
      return true;
    }
  }

  std::optional<std::string> hands_out_of(const ast::Node* node)
  {
    // A FuncDef's `made_in` is a run of names; a Lambda's one name or none.
    std::vector<ast::NameAt> made_in;
    std::span<const ast::Param* const> params;
    if (auto func = ast::as<ast::FuncDef>(node)) {
      made_in.assign(func->made_in.begin(), func->made_in.end());
      params = func->params;
    } else if (auto lambda = ast::as<ast::Lambda>(node)) {
      if (lambda->made_in)
        made_in.push_back(*lambda->made_in);
      params = lambda->params;
    }
    if (made_in.size() != 1)
      return std::nullopt;
    auto name = made_in[0].name;
    for (auto one : params)
      if (one->name == name)
        return std::nullopt;
    if (name == names::heap_name || name == names::static_name)
      return std::nullopt;
    return std::string(name);
  }

  bool thin_able(const ir::Type* ty)
  {
    return ir::is<ir::StrType>(ty) || ir::is<ir::ListType>(ty);
  }

  std::vector<Collected*> callees_first(std::span<Collected* const> collected)
  {
    Dict<std::string, std::vector<Collected*>> by_name;
    for (auto one : collected)
      by_name[std::string(one->node->name)].push_back(one);
    std::vector<Collected*> order;
    std::unordered_set<const Collected*> seen;
    auto called = [&](this auto& self, const ast::Node* node, std::vector<std::string>& into) -> void {
      if (node == nullptr)
        return;
      if (auto call = ast::as<ast::Call>(node)) {
        if (auto name = ast::as<ast::NameRef>(call->callee))
          into.emplace_back(name->name);
        else if (auto member = ast::as<ast::Member>(call->callee))
          into.emplace_back(member->name);
      } else if (auto fresh = ast::as<ast::Fresh>(node))
        into.emplace_back(fresh->glyph);
      ast::for_each_child(node, [&](const ast::Node* one) { self(one, into); });
    };
    auto visit = [&](this auto& self, Collected* entry) -> void {
      if (! seen.insert(entry).second)
        return;
      std::vector<std::string> names;
      called(entry->node->body, names);
      for (const auto& name : names)
        if (auto callees = by_name.get(name))
          for (auto callee : std::vector<Collected*>(*callees))
            self(callee);
      order.push_back(entry);
    };
    for (auto entry : collected)
      visit(entry);
    return order;
  }

  std::string alternatives(std::span<const std::string> names)
  {
    std::vector<std::string> quoted;
    for (const auto& one : names)
      quoted.push_back(std::format("'{}'", one));
    if (quoted.size() < 2)
      return quoted.empty() ? std::string("no arena") : quoted[0];
    std::string head;
    for (std::size_t at = 0; at + 1 < quoted.size(); ++at)
      head += (at == 0 ? "" : ", ") + quoted[at];
    return head + " or " + quoted.back();
  }

  std::optional<std::int64_t> field_index(const ir::ProductType* ty, std::string_view name)
  {
    for (std::size_t at = 0; at < ty->fields.size(); ++at)
      if (ty->fields[at].first == name)
        return std::int64_t(at);
    return std::nullopt;
  }

  std::string written_as(const ast::Expr* expr)
  {
    if (auto name = ast::as<ast::NameRef>(expr))
      return std::string(name->name);
    if (auto deref = ast::as<ast::Deref>(expr))
      return written_as(deref->operand) + std::string(names::deref_glyph);
    return "what this writes through";
  }

  bool reached_from(ir::Value* value, std::span<ir::Value* const> sources, const Incoming* incoming, KeySet* visited,
                    const Dict<std::int64_t, KeySet>* only,
                    const Dict<std::pair<std::string, std::string>, std::vector<ir::Value*>>* outer,
                    std::optional<std::int64_t> leaf, const KeySet& stored)
  {
    ir::Value* seen = value;
    bool loaded = false;
    for (;;) {
      for (auto one : sources)
        if (seen == one) {
          if (stored.contains(id_of(one)) && ! loaded)
            return false;
          const KeySet* allowed = only != nullptr ? only->get(id_of(one)) : nullptr;
          return allowed == nullptr || (leaf && allowed->contains(*leaf));
        }
      auto inst = seen->vkind == ir::ValueKind::instruction ? static_cast<ir::Instruction*>(seen) : nullptr;
      if (inst != nullptr && inst->kind == ir::InstKind::LoadInst) {
        seen = inst->operands[1];
        loaded = true;
        continue;
      }
      loaded = false;
      if (inst != nullptr && inst->kind == ir::InstKind::AddressInst) {
        seen = inst->operands[0];
        continue;
      }
      if (seen->vkind == ir::ValueKind::block_param && incoming != nullptr) {
        auto param = static_cast<ir::BlockParam*>(seen);
        KeySet own;
        if (visited == nullptr)
          visited = &own;
        if (visited->contains(id_of(seen)))
          return true;
        visited->insert(id_of(seen));
        auto found = incoming->find(param->block);
        if (found == incoming->end() || found->second.empty())
          return false;
        for (auto arm : found->second)
          if (param->index >= arm->args.size()
              || ! reached_from(arm->args[param->index], sources, incoming, visited, only, outer, leaf, stored))
            return false;
        return true;
      }
      if (inst != nullptr && inst->kind == ir::InstKind::CallInst) {
        auto [given, table] = call_borrows(inst);
        auto borrowed = table.get(leaf ? *leaf : -1);
        if (borrowed == nullptr)
          return false;
        for (auto [part, at] : borrowed_parts(given, *borrowed))
          if (reached_from(part, sources, incoming, visited, only, outer, at, stored))
            return true;
        if (outer != nullptr)
          for (const auto& pair : borrowed->outer)
            if (auto values = outer->get(pair))
              for (auto one : *values)
                for (auto source : sources)
                  if (one == source)
                    return true;
        return false;
      }
      if (inst != nullptr && inst->kind == ir::InstKind::TupleInst && leaf) {
        if (*leaf >= std::int64_t(inst->operands.size()))
          return false;
        seen = inst->operands[*leaf];
        leaf.reset();
        continue;
      }
      if (inst != nullptr && inst->kind == ir::InstKind::ExtractInst) {
        if (ir::is<ir::TupleType>(inst->operands[0]->ty))
          leaf = inst->index;
        seen = inst->operands[0];
        continue;
      }
      if (inst != nullptr
          && (inst->kind == ir::InstKind::CastInst
              || (inst->kind == ir::InstKind::BinaryInst && (inst->bin == ir::BinOp::add || inst->bin == ir::BinOp::sub)))) {
        seen = inst->operands[0];
        continue;
      }
      return false;
    }
  }

  bool could_be_an_arena(const ast::TypeExpr* written)
  {
    auto ref = ast::as<ast::RefTypeRef>(written);
    if (ref == nullptr || ! ref->mutable_)
      return false;
    auto pointee = ast::as<ast::TypeRef>(ref->pointee);
    return pointee != nullptr && pointee->name == "arena";
  }

  bool compares_types(const ast::Node* node)
  {
    if (node == nullptr)
      return false;
    if (ast::is<ast::Lifted>(node))
      return true;
    bool found = false;
    ast::for_each_child(node, [&](const ast::Node* one) {
      if (! found)
        found = compares_types(one);
    });
    return found;
  }

  bool is_an_arena_handed(const ir::Type* ty)
  {
    auto ptr = ir::as<ir::PtrType>(ty);
    return ptr != nullptr && ptr->pointee == ir::arena_type() && ptr->is_mutable;
  }

  std::optional<std::string> not_copied_because(const ir::Type* element)
  {
    if (owned::points(element) || holds_a_lambda(element))
      return "point into room something else keeps";
    if (holds_a_reference(element))
      return "hold references";
    return std::nullopt;
  }

  std::string shape_of_type(const ir::ArrayType* ty)
  {
    std::string said{ "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}" };
    for (std::size_t at = 0; at < ty->shape.size(); ++at) {
      if (at > 0)
        said += ",";
      if (ty->shape[at])
        said += std::to_string(*ty->shape[at]);
    }
    return said + "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}";
  }

  Local* deepest(std::span<Local* const> found)
  {
    Local* deepest = nullptr;
    for (auto one : found)
      if (one != nullptr && (deepest == nullptr || one->depth > deepest->depth))
        deepest = one;
    return deepest;
  }

  Incoming incoming_of(ir::Function* func)
  {
    Incoming incoming;
    for (const auto& block : func->blocks)
      if (auto last = block->terminator())
        for (const auto& arm : last->targets)
          incoming[arm.block].push_back(&arm);
    return incoming;
  }

  std::tuple<std::vector<ir::Value*>, Dict<std::int64_t, ir::Borrowed>> call_borrows(const ir::Instruction* call)
  {
    auto table = [](const ir::FuncType* ty) {
      Dict<std::int64_t, ir::Borrowed> made;
      if (ty != nullptr)
        for (const auto& [leaf, borrowed] : ty->borrows)
          made[leaf] = borrowed;
      return made;
    };
    auto args = call->arguments();
    std::vector<ir::Value*> given(args.begin(), args.end());
    if (call->callee != nullptr)
      return { given, table(ir::as<ir::FuncType>(call->callee->ty)) };
    auto code = call->operands[0];
    if (code->vkind == ir::ValueKind::instruction) {
      auto inst = static_cast<const ir::Instruction*>(code);
      if (inst->kind == ir::InstKind::ExtractInst)
        if (auto ty = ir::as<ir::FuncType>(inst->operands[0]->ty)) {
          if (! given.empty())
            given.erase(given.begin());
          return { given, table(ty) };
        }
    }
    return { given, {} };
  }

  std::vector<std::tuple<ir::Value*, std::optional<std::int64_t>>> borrowed_parts(std::span<ir::Value* const> given,
                                                                                  ir::Borrowed borrowed)
  {
    std::vector<std::tuple<ir::Value*, std::optional<std::int64_t>>> found;
    for (int param : borrowed.params) {
      if (param >= int(given.size()))
        continue;
      auto leaves = leaves_of(borrowed, param);
      if (! leaves) {
        found.emplace_back(given[param], std::nullopt);
        continue;
      }
      for (int one : *leaves) {
        auto part = leaf_of(given[param], one);
        if (part != nullptr)
          found.emplace_back(part, std::nullopt);
        else
          found.emplace_back(given[param], one);
      }
    }
    return found;
  }

  ir::Value* leaf_of(ir::Value* value, std::int64_t leaf)
  {
    if (value->vkind != ir::ValueKind::instruction)
      return nullptr;
    auto inst = static_cast<ir::Instruction*>(value);
    if (inst->kind == ir::InstKind::TupleInst && leaf < std::int64_t(inst->operands.size()))
      return inst->operands[leaf];
    if (inst->kind == ir::InstKind::CastInst)
      return leaf_of(inst->operands[0], leaf);
    return nullptr;
  }

  std::optional<std::vector<std::int64_t>> leaves_named(const ast::TypeExpr* ref, const ir::Type* ty,
                                                        const NameSet& names)
  {
    auto tuple = ir::as<ir::TupleType>(ty);
    auto written = ast::as<ast::TupleTypeRef>(ref);
    if (tuple == nullptr || ir::held_in_memory(ty) || written == nullptr
        || written->members.size() != tuple->members.size())
      return std::nullopt;
    std::vector<std::int64_t> found;
    auto within = ir::parts_within(ty);
    for (std::size_t at = 0; at < tuple->members.size(); ++at) {
      auto carried = lifetimes_of(written->members[at]);
      if (! std::ranges::any_of(carried, [&](const auto& one) { return names.contains(one); }))
        continue;
      auto [first, count] = within[at];
      auto deeper = leaves_named(written->members[at], tuple->members[at], names);
      if (! deeper)
        for (std::size_t one = first; one < first + count; ++one)
          found.push_back(std::int64_t(one));
      else
        for (auto one : *deeper)
          found.push_back(std::int64_t(first) + one);
    }
    return found;
  }

  bool points_somewhere(const ir::Type* ty, const KeySet& seen, bool lambdas, bool references)
  {
    // `owned::points_somewhere` walks with a set of its own; a walk started
    // part way through, with something already seen, is walked here.
    if (seen.empty())
      return owned::points_somewhere(ty, lambdas, references);
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::ptr:
      return references;
    case ir::TypeKind::str:
    case ir::TypeKind::list:
    case ir::TypeKind::set:
    case ir::TypeKind::dict:
    case ir::TypeKind::cursor:
      return true;
    case ir::TypeKind::func:
      return lambdas;
    case ir::TypeKind::array: {
      auto array = ir::as<ir::ArrayType>(ty);
      return ! array->fixed() || points_somewhere(array->element, deeper, lambdas, references);
    }
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members,
                                 [&](auto m) { return points_somewhere(m, deeper, lambdas, references); });
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return points_somewhere(r->ok, deeper, lambdas, references)
        || (r->err != nullptr && points_somewhere(r->err, deeper, lambdas, references));
    }
    case ir::TypeKind::product:
      return std::ranges::any_of(ir::as<ir::ProductType>(ty)->fields,
                                 [&](const auto& f) { return points_somewhere(f.second, deeper, lambdas, references); });
    case ir::TypeKind::sum:
      return std::ranges::any_of(ir::as<ir::SumType>(ty)->variants,
                                 [&](const auto& v) { return points_somewhere(v.second, deeper, lambdas, references); });
    default:
      return false;
    }
  }

  void empties_named(const ast::Node* node, NameSet& into)
  {
    if (node == nullptr)
      return;
    if (auto call = ast::as<ast::Call>(node)) {
      auto callee = callee_name(call);
      if (callee != nullptr && callee->name == names::empty_name && ! call->args.empty())
        if (auto name = ast::as<ast::NameRef>(call->args[0]))
          into.emplace(name->name);
    }
    ast::for_each_child(node, [&](const ast::Node* one) { empties_named(one, into); });
  }

  std::optional<std::string> held_lifetime(const ast::Node* ref)
  {
    auto outer = ast::as<ast::RefTypeRef>(ref);
    if (outer == nullptr)
      return std::nullopt;
    auto inner = ast::as<ast::RefTypeRef>(outer->pointee);
    if (inner == nullptr || ! inner->lifetime)
      return std::nullopt;
    return std::string(*inner->lifetime);
  }

  std::vector<std::tuple<std::int64_t, std::int64_t>> kept_pairs(const ast::FuncDef* node)
  {
    std::vector<std::tuple<std::int64_t, std::int64_t>> found;
    for (std::size_t at = 0; at < node->params.size(); ++at) {
      auto held = held_lifetime(node->params[at]->type);
      if (! held)
        continue;
      for (std::size_t other = 0; other < node->params.size(); ++other) {
        auto given = ast::as<ast::RefTypeRef>(node->params[other]->type);
        if (other != at && given != nullptr && given->lifetime && *given->lifetime == *held)
          found.emplace_back(std::int64_t(at), std::int64_t(other));
      }
    }
    return found;
  }

  bool holds_a_short_reference(const ir::Type* ty, const KeySet& seen)
  {
    if (auto ptr = ir::as<ir::PtrType>(ty))
      return ! ptr->lasting || holds_a_short_reference(ptr->pointee, seen);
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members,
                                 [&](auto m) { return holds_a_short_reference(m, deeper); });
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return holds_a_short_reference(r->ok, deeper) || (r->err != nullptr && holds_a_short_reference(r->err, deeper));
    }
    case ir::TypeKind::product:
      return std::ranges::any_of(ir::as<ir::ProductType>(ty)->fields,
                                 [&](const auto& f) { return holds_a_short_reference(f.second, deeper); });
    case ir::TypeKind::sum:
      return std::ranges::any_of(ir::as<ir::SumType>(ty)->variants,
                                 [&](const auto& v) { return holds_a_short_reference(v.second, deeper); });
    case ir::TypeKind::array:
      return holds_a_short_reference(ir::as<ir::ArrayType>(ty)->element, deeper);
    case ir::TypeKind::list:
      return holds_a_short_reference(ir::as<ir::ListType>(ty)->element, deeper);
    case ir::TypeKind::set:
      return holds_a_short_reference(ir::as<ir::SetType>(ty)->element, deeper);
    case ir::TypeKind::vec:
      return holds_a_short_reference(ir::as<ir::VecType>(ty)->element, deeper);
    case ir::TypeKind::dict: {
      auto d = ir::as<ir::DictType>(ty);
      return holds_a_short_reference(d->key, deeper) || holds_a_short_reference(d->value, deeper);
    }
    default:
      return false;
    }
  }

  bool holds_a_view(const ir::Type* ty)
  {
    switch (ty->kind) {
    case ir::TypeKind::array:
      return ! ir::as<ir::ArrayType>(ty)->fixed();
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members, [](auto m) { return holds_a_view(m); });
    case ir::TypeKind::result:
      return holds_a_view(ir::as<ir::ResultType>(ty)->ok);
    default:
      return false;
    }
  }


  bool holds_a_reference(const ir::Type* ty, const KeySet& seen)
  {
    if (ir::is<ir::PtrType>(ty))
      return true;
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members,
                                 [&](auto m) { return holds_a_reference(m, deeper); });
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return holds_a_reference(r->ok, deeper) || (r->err != nullptr && holds_a_reference(r->err, deeper));
    }
    case ir::TypeKind::product:
      return std::ranges::any_of(ir::as<ir::ProductType>(ty)->fields,
                                 [&](const auto& f) { return holds_a_reference(f.second, deeper); });
    case ir::TypeKind::sum:
      return std::ranges::any_of(ir::as<ir::SumType>(ty)->variants,
                                 [&](const auto& v) { return holds_a_reference(v.second, deeper); });
    case ir::TypeKind::array:
      return holds_a_reference(ir::as<ir::ArrayType>(ty)->element, deeper);
    case ir::TypeKind::list:
      return holds_a_reference(ir::as<ir::ListType>(ty)->element, deeper);
    case ir::TypeKind::set:
      return holds_a_reference(ir::as<ir::SetType>(ty)->element, deeper);
    case ir::TypeKind::vec:
      return holds_a_reference(ir::as<ir::VecType>(ty)->element, deeper);
    case ir::TypeKind::dict: {
      auto d = ir::as<ir::DictType>(ty);
      return holds_a_reference(d->key, deeper) || holds_a_reference(d->value, deeper);
    }
    default:
      return false;
    }
  }

  bool lets_go_of(const ir::ArrayType* found, const ir::ArrayType* wanted)
  {
    if (found->element != wanted->element || found->rank() != wanted->rank())
      return false;
    for (std::size_t at = 0; at < found->shape.size(); ++at)
      if (wanted->shape[at] && wanted->shape[at] != found->shape[at])
        return false;
    return true;
  }

  std::vector<std::int64_t> spread_out(std::int64_t at, std::span<const std::int64_t> shape)
  {
    std::vector<std::int64_t> found;
    auto left = at;
    for (auto along = shape.rbegin(); along != shape.rend(); ++along) {
      // Python's `%` and `//` round towards minus infinity; every count here
      // is positive, so C++'s are the same.
      found.push_back(left % *along);
      left /= *along;
    }
    std::ranges::reverse(found);
    return found;
  }

  bool says_its_type(const ast::Expr* expr)
  {
    if (auto lit = ast::as<ast::IntLit>(expr))
      return lit->type_name.has_value();
    if (auto lit = ast::as<ast::FloatLit>(expr))
      return lit->type_name.has_value();
    return ast::is<ast::BoolLit>(expr) || ast::is<ast::StringLit>(expr);
  }

  const ir::Type* taken_from(const ast::Expr* written, const ir::Type* said)
  {
    return says_its_type(written) ? nullptr : said;
  }

  bool can_be_a_key(const ir::Type* ty)
  {
    return ir::is<ir::IntType>(ty) || ir::is<ir::BoolType>(ty) || ir::is<ir::EnumType>(ty) || ir::is<ir::CharType>(ty)
      || ir::is<ir::StrType>(ty);
  }

  bool can_be_a_value(const ir::Type* ty)
  {
    return ! ir::is<ir::SetType>(ty) && ! ir::is<ir::DictType>(ty) && ty != ir::void_type() && ty != ir::error_type();
  }

  bool every_value_fits(const ir::IntType* found, const ir::IntType* into)
  {
    if (found->is_signed && ! into->is_signed)
      return false;
    if (! found->is_signed && into->is_signed)
      return found->bits < into->bits;
    return found->bits <= into->bits;
  }

  bool is_startup_argument(const ir::Type* ty)
  {
    auto ptr = ir::as<ir::PtrType>(ty);
    auto held = ir::as<ir::ProductType>(ptr != nullptr ? ptr->pointee : ty);
    if (held == nullptr || held->name != startup_argument_type_name)
      return false;
    return is_the_std_module(held->origin);
  }

  const ir::Type* held_by_the_startup(ir::Function* func, std::int64_t index, const ast::Param* written)
  {
    if (func->attrs.special != ir::SpecialKind::startup || index != 0)
      return nullptr;
    if (ast::is<ast::RefTypeRef>(written->type))
      return nullptr;
    if (func->fty->params.empty())
      return nullptr;
    auto held = ir::as<ir::PtrType>(func->fty->params[0]);
    if (held == nullptr)
      return nullptr;
    return ir::is<ir::ProductType>(held->pointee) ? held->pointee : nullptr;
  }

  bool reaches_a_device(const ir::Type* ty, const KeySet& seen)
  {
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::product: {
      auto product = ir::as<ir::ProductType>(ty);
      return product->device
        || std::ranges::any_of(product->fields, [&](const auto& f) { return reaches_a_device(f.second, deeper); });
    }
    case ir::TypeKind::ptr:
      return reaches_a_device(ir::as<ir::PtrType>(ty)->pointee, deeper);
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members,
                                 [&](auto m) { return reaches_a_device(m, deeper); });
    case ir::TypeKind::array:
      return reaches_a_device(ir::as<ir::ArrayType>(ty)->element, deeper);
    default:
      return false;
    }
  }

  std::string glyph_of(const ast::Expr* expr)
  {
    if (auto unary = ast::as<ast::Unary>(expr))
      return std::string(describe(unary->op));
    if (auto binary = ast::as<ast::Binary>(expr))
      return std::string(describe(binary->op));
    if (auto fresh = ast::as<ast::Fresh>(expr))
      return std::string(fresh->glyph);
    return "";
  }

  std::vector<const ast::Expr*> operands_of(const ast::Expr* expr)
  {
    if (auto unary = ast::as<ast::Unary>(expr))
      return { unary->operand };
    if (auto binary = ast::as<ast::Binary>(expr))
      return { binary->left, binary->right };
    if (auto fresh = ast::as<ast::Fresh>(expr))
      return { fresh->operands.begin(), fresh->operands.end() };
    return {};
  }

  bool takes_room(const ast::Expr* expr)
  {
    if (ast::is<ast::SetLit>(expr) || ast::is<ast::DictLit>(expr) || ast::is<ast::ListLit>(expr)
        || ast::is<ast::ArrayLit>(expr))
      return true;
    if (auto call = ast::as<ast::Call>(expr))
      if (auto name = callee_name(call);
          name != nullptr && (name->name == names::one_run_name || name->name == names::text_name))
        return true;
    auto binary = ast::as<ast::Binary>(expr);
    return binary != nullptr && binary->op == ast::BinaryOp::concat;
  }

  std::string about_the_shape(const ast::Expr* expr)
  {
    if (auto name = ast::as<ast::NameRef>(expr))
      return std::format("'{}'", name->name);
    if (auto binary = ast::as<ast::Binary>(expr))
      return std::format("'{}'", describe(binary->op));
    if (auto unary = ast::as<ast::Unary>(expr))
      return std::format("'{}'", describe(unary->op));
    return "this";
  }

  bool leaves(const ast::Node* node)
  {
    if (node == nullptr)
      return false;
    if (ast::is<ast::Try>(node))
      return true;
    bool found = false;
    ast::for_each_child(node, [&](const ast::Node* one) {
      if (! found)
        found = leaves(one);
    });
    return found;
  }

  bool is_build_object(const ir::Type* ty)
  {
    auto ptr = ir::as<ir::PtrType>(ty);
    if (ptr == nullptr || ! ptr->is_mutable)
      return false;
    auto held = ir::as<ir::ProductType>(ptr->pointee);
    if (held == nullptr || held->name != build_type_name)
      return false;
    return is_the_std_module(held->origin);
  }

  std::string path_text(const ast::Expr* expr)
  {
    if (auto name = ast::as<ast::NameRef>(expr))
      return std::string(name->name);
    if (auto member = ast::as<ast::Member>(expr))
      return std::format("{}.{}", path_text(member->base), member->name);
    return "an expression";
  }

  bool is_exported(Held what)
  {
    if (auto func = what.get<ir::Function>())
      return func->exported;
    if (auto var = what.get<ir::GlobalVar>())
      return var->exported;
    if (auto one = what.get<Generator>())
      return one->exported;
    if (auto one = what.get<Generic>())
      return one->exported;
    if (auto one = what.get<NamedType>())
      return one->exported;
    if (auto one = what.get<Bundle>())
      return one->exported;
    return false;
  }

  Held merged(std::span<const Held> found)
  {
    Held seen;
    for (const auto& one : found) {
      if (one.is_mixed())
        return Mixed{};
      if (one.empty())
        continue;
      if (seen.empty())
        seen = one;
      else if (! (seen == one))
        return Mixed{};
    }
    return seen;
  }

  std::optional<std::vector<std::int64_t>> shape_of_literal(const ast::ArrayLit* expr)
  {
    std::vector<const ast::ArrayLit*> rows;
    for (auto one : expr->elements)
      if (auto row = ast::as<ast::ArrayLit>(one))
        rows.push_back(row);
    if (rows.empty())
      return std::vector<std::int64_t>{ std::int64_t(expr->elements.size()) };
    if (rows.size() != expr->elements.size())
      return std::nullopt;
    std::optional<std::vector<std::int64_t>> inner;
    for (auto row : rows) {
      auto one = shape_of_literal(row);
      if (! one)
        return std::nullopt;
      if (inner && *inner != *one)
        return std::nullopt;
      inner = std::move(one);
    }
    std::vector<std::int64_t> shape{ std::int64_t(rows.size()) };
    shape.insert(shape.end(), inner->begin(), inner->end());
    return shape;
  }

  std::vector<std::int64_t> takes_owned(const ast::FuncDef* node, std::span<const ir::Type* const> params)
  {
    std::vector<std::int64_t> found;
    if (node->body == nullptr)
      return found;
    for (std::size_t at = 0; at < node->params.size() && at < params.size(); ++at) {
      auto param = node->params[at];
      if (param->mutable_ && holds_a_list(params[at]) && owned::can_own(params[at])
          && changed_in_place(param->name, node->body))
        found.push_back(std::int64_t(at));
    }
    return found;
  }

  std::int64_t mentions(const ast::Node* node, std::string_view name)
  {
    if (node == nullptr)
      return 0;
    if (auto ref = ast::as<ast::NameRef>(node))
      return ref->name == name ? 1 : 0;
    std::int64_t found = 0;
    ast::for_each_child(node, [&](const ast::Node* one) { found += mentions(one, name); });
    return found;
  }

  bool takes_an_answer(const ast::Node* node)
  {
    if (node == nullptr)
      return false;
    if (auto yield = ast::as<ast::YieldValue>(node); yield != nullptr && yield->delegates)
      return true;
    bool found = false;
    ast::for_each_child(node, [&](const ast::Node* one) {
      if (! found)
        found = takes_an_answer(one);
    });
    return found;
  }

  bool fills(const ir::Type* ty)
  {
    if (auto set = ir::as<ir::SetType>(ty))
      return set->is_mutable;
    if (auto dict = ir::as<ir::DictType>(ty))
      return dict->is_mutable;
    return false;
  }

  std::vector<std::int64_t> writes_arrays(const ast::Node* node, std::span<const ir::Type* const> params)
  {
    std::vector<std::int64_t> found;
    auto func = ast::as<ast::FuncDef>(node);
    std::span<const ast::Param* const> written;
    if (func != nullptr)
      written = func->params;
    else if (auto lambda = ast::as<ast::Lambda>(node))
      written = lambda->params;
    for (std::size_t at = 0; at < written.size() && at < params.size(); ++at)
      if (written[at]->mutable_ && ir::is<ir::ArrayType>(params[at]))
        found.push_back(std::int64_t(at));
    return found;
  }

  std::optional<std::tuple<std::string, std::string, std::vector<const ast::Expr*>>> applied(const ast::Node* expr)
  {
    if (auto binary = ast::as<ast::Binary>(expr))
      return std::tuple{ std::string("binary"), std::string(describe(binary->op)),
                         std::vector<const ast::Expr*>{ binary->left, binary->right } };
    if (auto unary = ast::as<ast::Unary>(expr))
      return std::tuple{ std::string("unary"), std::string(describe(unary->op)),
                         std::vector<const ast::Expr*>{ unary->operand } };
    if (auto fresh = ast::as<ast::Fresh>(expr))
      return std::tuple{ std::string("fresh"), std::string(fresh->glyph),
                         std::vector<const ast::Expr*>(fresh->operands.begin(), fresh->operands.end()) };
    if (auto call = ast::as<ast::Call>(expr)) {
      if (auto name = callee_name(call)) {
        std::vector<const ast::Expr*> args;
        for (auto one : call->args)
          args.push_back(argument_value(one));
        return std::tuple{ std::string("call"), std::string(name->name), std::move(args) };
      }
      return std::nullopt;
    }
    if (auto element = ast::as<ast::Element>(expr)) {
      std::vector<const ast::Expr*> operands{ element->base };
      operands.insert(operands.end(), element->indices.begin(), element->indices.end());
      return std::tuple{ std::string("element"),
                         std::string("\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}"),
                         std::move(operands) };
    }
    if (auto index = ast::as<ast::Index>(expr))
      return std::tuple{ std::string("index"), std::string("\N{LEFT DOUBLE PARENTHESIS}\N{RIGHT DOUBLE PARENTHESIS}"),
                         std::vector<const ast::Expr*>{ index->base, index->key } };
    return std::nullopt;
  }

  std::string requirement_written(std::string_view kind, std::string_view what, std::span<const ir::Type* const> types)
  {
    std::vector<std::string> said;
    for (auto one : types)
      said.push_back(ir::is<ir::OpaqueType>(one) ? one->written()
                                                 : std::format("\N{TOP LEFT CORNER}{}\N{TOP RIGHT CORNER}", one->written()));
    auto joined = [&](std::size_t from, std::string_view between) {
      std::string text;
      for (std::size_t at = from; at < said.size(); ++at)
        text += (at == from ? "" : std::string(between)) + said[at];
      return text;
    };
    if (kind == "binary")
      return std::format("{} {} {}", said[0], what, said[1]);
    if (kind == "unary")
      return std::format("{}{}", what, said[0]);
    if (kind == "fresh")
      return said.size() == 1 ? std::format("{}{}", what, said[0]) : std::format("{} {} {}", said[0], what, joined(1, " "));
    if (kind == "call")
      return std::format("{}({})", what, joined(0, ", "));
    if (kind == "element")
      return std::format("{}\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}{}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}",
                         said[0], joined(1, ", "));
    return std::format("{}\N{LEFT DOUBLE PARENTHESIS}{}\N{RIGHT DOUBLE PARENTHESIS}", said[0], joined(1, ", "));
  }

  const ast::Expr* with_operands(ast::SyntaxArena& arena, const ast::Expr* expr,
                                 std::span<const ast::Expr* const> operands)
  {
    if (auto binary = ast::as<ast::Binary>(expr)) {
      auto made = arena.copy(*binary);
      made->left = operands[0];
      made->right = operands[1];
      return made;
    }
    if (auto unary = ast::as<ast::Unary>(expr)) {
      auto made = arena.copy(*unary);
      made->operand = operands[0];
      return made;
    }
    if (auto fresh = ast::as<ast::Fresh>(expr)) {
      auto made = arena.copy(*fresh);
      made->operands = arena.list(std::vector<const ast::Expr*>(operands.begin(), operands.end()));
      return made;
    }
    if (auto call = ast::as<ast::Call>(expr)) {
      std::vector<const ast::Expr*> args;
      for (std::size_t at = 0; at < call->args.size() && at < operands.size(); ++at)
        if (auto named = ast::as<ast::Named>(call->args[at])) {
          auto one = arena.copy(*named);
          one->value = operands[at];
          args.push_back(one);
        } else
          args.push_back(operands[at]);
      auto made = arena.copy(*call);
      made->args = arena.list(args);
      return made;
    }
    if (auto element = ast::as<ast::Element>(expr)) {
      auto made = arena.copy(*element);
      made->base = operands[0];
      made->indices = arena.list(std::vector<const ast::Expr*>(operands.begin() + 1, operands.end()));
      return made;
    }
    if (auto index = ast::as<ast::Index>(expr)) {
      auto made = arena.copy(*index);
      made->base = operands[0];
      made->key = operands[1];
      return made;
    }
    return expr;
  }

  bool in_the_image(const ast::Expr* expr)
  {
    const ast::Expr* where = nullptr;
    if (auto one = ast::as<ast::Allocated>(expr))
      where = one->arena;
    else if (auto one = ast::as<ast::SetLit>(expr))
      where = one->arena;
    else if (auto one = ast::as<ast::DictLit>(expr))
      where = one->arena;
    auto name = ast::as<ast::NameRef>(where);
    return name != nullptr && name->name == names::static_name;
  }

  bool empty_in_the_heap(const ast::Expr* expr)
  {
    bool held = false;
    const ast::Expr* where = nullptr;
    if (auto set = ast::as<ast::SetLit>(expr)) {
      held = ! set->elements.empty();
      where = set->arena;
    } else if (auto dict = ast::as<ast::DictLit>(expr)) {
      held = ! dict->entries.empty();
      where = dict->arena;
    } else
      return false;
    if (held)
      return false;
    if (where == nullptr)
      return true;
    auto name = ast::as<ast::NameRef>(where);
    return name != nullptr && name->name == names::heap_name;
  }

  bool a_container_written(const ast::Expr* expr)
  {
    while (auto allocated = ast::as<ast::Allocated>(expr))
      expr = allocated->value;
    return ast::is<ast::ArrayLit>(expr) || ast::is<ast::ListLit>(expr) || ast::is<ast::SetLit>(expr)
      || ast::is<ast::DictLit>(expr);
  }

  bool known_while_compiling(const ast::Expr* expr, const std::function<bool(const ast::Member*)>& enum_named)
  {
    switch (expr->tag) {
    case ast::NodeKind::IntLit:
    case ast::NodeKind::FloatLit:
    case ast::NodeKind::BoolLit:
    case ast::NodeKind::CharLit:
    case ast::NodeKind::StringLit:
      return true;
    case ast::NodeKind::Member:
      return enum_named(ast::as<ast::Member>(expr));
    case ast::NodeKind::Allocated:
      return in_the_image(expr) && known_while_compiling(ast::as<ast::Allocated>(expr)->value, enum_named);
    case ast::NodeKind::ArrayLit:
      return std::ranges::all_of(ast::as<ast::ArrayLit>(expr)->elements,
                                 [&](auto one) { return known_while_compiling(one, enum_named); });
    case ast::NodeKind::ListLit:
      return std::ranges::all_of(ast::as<ast::ListLit>(expr)->elements,
                                 [&](auto one) { return known_while_compiling(one, enum_named); });
    case ast::NodeKind::SetLit: {
      auto set = ast::as<ast::SetLit>(expr);
      return (set->arena == nullptr || in_the_image(expr))
        && std::ranges::all_of(set->elements, [&](auto one) { return known_while_compiling(one, enum_named); });
    }
    case ast::NodeKind::DictLit: {
      auto dict = ast::as<ast::DictLit>(expr);
      return (dict->arena == nullptr || in_the_image(expr))
        && std::ranges::all_of(dict->entries, [&](const auto& one) {
             return known_while_compiling(one.key, enum_named) && known_while_compiling(one.value, enum_named);
           });
    }
    case ast::NodeKind::Named:
      return known_while_compiling(ast::as<ast::Named>(expr)->value, enum_named);
    default:
      return false;
    }
  }

  std::tuple<std::int64_t, std::vector<std::uint64_t>, std::vector<std::tuple<const ir::Value*, const ir::Value*>>>
  table_laid_out(std::span<const std::tuple<Held, const ir::Value*, const ir::Value*>> keys,
                 const std::function<std::uint64_t(const Held&)>& hashed, std::int64_t stride)
  {
    std::int64_t capacity = tables::first_capacity;
    std::vector<std::tuple<const ir::Value*, const ir::Value*>> dense;
    std::vector<Held> written;
    std::vector<std::uint64_t> full;
    // Where each key written so far is among the entries, by its hash, so that
    // one written twice is found without comparing it with every other.
    std::unordered_multimap<std::uint64_t, std::size_t> seen;
    for (const auto& [bits, key, value] : keys) {
      // Nothing is taken out of a table written down, so every rebuild is one
      // that doubles; a key written again asks for room as a new one does.
      if ((std::int64_t(dense.size()) + 1) * tables::load_denominator > capacity * tables::load_numerator)
        capacity *= 2;
      auto hash = hashed(bits);
      auto [from, to] = seen.equal_range(hash);
      auto found = std::find_if(from, to, [&](const auto& one) { return written[one.second] == bits; });
      if (found != to)
        std::get<1>(dense[found->second]) = value;
      else {
        seen.emplace(hash, dense.size());
        dense.emplace_back(key, value);
        written.push_back(bits);
        full.push_back(hash);
      }
    }
    // The index, filled in the order the entries are in, which is the order
    // the program's own insertions -- and every rebuild -- fill it in.
    std::vector<std::uint64_t> words(capacity, 0);
    auto mask = std::uint64_t(capacity - 1);
    for (std::size_t at = 0; at < dense.size(); ++at) {
      auto where = full[at] & mask;
      while (words[where] != 0)
        where = (where + 1) & mask;
      words[where] = (full[at] & tables::hash_top) | std::uint64_t(capacity * tables::word + std::int64_t(at) * stride);
    }
    return { capacity, std::move(words), std::move(dense) };
  }

  std::uint64_t word_hash(Held bits)
  {
    auto number = bits.number();
    if (number == nullptr) [[unlikely]]
      throw InternalError("a word key that is no number");
    return sema::word_hash(std::uint64_t(*number), tables::image_seed);
  }

  std::uint64_t text_hash(Held bits)
  {
    auto text = bits.text();
    if (text == nullptr) [[unlikely]]
      throw InternalError("a text key that is no bytes");
    auto bytes = std::span(reinterpret_cast<const unsigned char*>(text->data()), text->size());
    return rapidhash(bytes, tables::image_seed);
  }

  __int128 counted_bits(ir::UnOp op, __int128 value, const ir::IntType* ty)
  {
    auto mask = ty->bits >= 128 ? ~__int128(0) : (__int128(1) << ty->bits) - 1;
    auto bits = static_cast<unsigned __int128>(value & mask);
    auto low = std::uint64_t(bits);
    auto high = std::uint64_t(bits >> 64);
    if (op == ir::UnOp::count_ones)
      return std::popcount(low) + std::popcount(high);
    int length = high != 0 ? 128 - std::countl_zero(high) : 64 - std::countl_zero(low);
    return ty->bits - length;
  }

  ir::GlobalVar* tolerance(ir::Module& module)
  {
    if (auto found = module.global(names::tolerance_name))
      return found;
    auto made = std::make_unique<ir::GlobalVar>(std::string(tolerance_symbol), ir::f64(), ir::ptr_type(ir::f64(), true));
    made->initializer = module.float_const(ir::f64(), names::tolerance_default);
    made->linkage = ir::Linkage::internal;
    return module.add_global(std::move(made), std::string(names::tolerance_name));
  }

  ir::GlobalVar* heap(ir::Module& module)
  {
    return tables::heap_global(module);
  }

  bool lowered_first(const ast::Node* expr)
  {
    switch (expr->tag) {
    case ast::NodeKind::NameRef:
    case ast::NodeKind::Binary:
    case ast::NodeKind::Unary:
    case ast::NodeKind::Call:
    case ast::NodeKind::Element:
    case ast::NodeKind::Index:
    case ast::NodeKind::Member:
    case ast::NodeKind::Deref:
    case ast::NodeKind::Fresh:
      return true;
    default:
      return false;
    }
  }

  bool holds_opaque(const ir::Type* ty, const KeySet& seen)
  {
    if (ir::is<ir::OpaqueType>(ty))
      return true;
    if (seen.contains(id_of(ty)))
      return false;
    auto deeper = deeper_than(seen, ty);
    switch (ty->kind) {
    case ir::TypeKind::tuple:
      return std::ranges::any_of(ir::as<ir::TupleType>(ty)->members, [&](auto m) { return holds_opaque(m, deeper); });
    case ir::TypeKind::result: {
      auto r = ir::as<ir::ResultType>(ty);
      return holds_opaque(r->ok, deeper) || (r->err != nullptr && holds_opaque(r->err, deeper));
    }
    case ir::TypeKind::array:
      return holds_opaque(ir::as<ir::ArrayType>(ty)->element, deeper);
    case ir::TypeKind::list:
      return holds_opaque(ir::as<ir::ListType>(ty)->element, deeper);
    case ir::TypeKind::set:
      return holds_opaque(ir::as<ir::SetType>(ty)->element, deeper);
    case ir::TypeKind::vec:
      return holds_opaque(ir::as<ir::VecType>(ty)->element, deeper);
    case ir::TypeKind::dict: {
      auto d = ir::as<ir::DictType>(ty);
      return holds_opaque(d->key, deeper) || holds_opaque(d->value, deeper);
    }
    case ir::TypeKind::ptr:
      return holds_opaque(ir::as<ir::PtrType>(ty)->pointee, deeper);
    case ir::TypeKind::func: {
      auto f = ir::as<ir::FuncType>(ty);
      return holds_opaque(f->ret, deeper)
        || std::ranges::any_of(f->params, [&](auto one) { return holds_opaque(one, deeper); });
    }
    default:
      return false;
    }
  }


  // -- what the evaluator is told -----------------------------------------------

  std::optional<comptime::Value> ReflectHere::fields(const ast::Expr* written)
  {
    auto lifted = ast::as<ast::Lifted>(written);
    if (lifted == nullptr || lifted->written == nullptr)
      return std::nullopt;
    auto ty = ir::as<ir::EnumType>(checker.resolve_type(lifted->written));
    if (ty == nullptr)
      return std::nullopt;
    comptime::Run found;
    for (std::size_t at = 0; at < ty->members.size(); ++at)
      found.push_back(comptime::Value::run(
        { comptime::Value(ty->members[at]), comptime::Value(static_cast<const ir::Value*>(checker.module.enum_const(ty, at))) }));
    return comptime::Value::run(std::move(found));
  }

  std::optional<comptime::Value> ReflectHere::enum_value(const ast::Member* written)
  {
    auto base = ast::as<ast::NameRef>(written->base);
    const Held* held = base != nullptr ? checker.top->get(std::string(base->name)) : nullptr;
    auto defined = held != nullptr ? held->get<NamedType>() : nullptr;
    if (defined == nullptr || ! ir::is<ir::EnumType>(checker.resolved(defined)))
      return std::nullopt;
    auto found = checker.enum_value(defined, written);
    if (found == nullptr || found->vkind != ir::ValueKind::enum_const)
      return std::nullopt;
    return comptime::Value(static_cast<const ir::Value*>(found));
  }


  // -- the checker made, and the scope a definition is checked in ---------------

  Checker::Checker(ir::Module& module, DiagEngine& diags, ModuleRegistry* registry, std::filesystem::path path,
                   std::string prefix, SourceManager* sources, std::vector<Global*>* top_level, Notes* notes,
                   Conditions conditions, bool for_macros, std::vector<const ast::Quote*>* quoted, bool whole_modules,
                   bool ignore_system)
    : module(module), diags(diags), ignore_system(ignore_system), whole_modules(whole_modules), conditions(conditions),
      for_macros(for_macros), quoted(quoted != nullptr ? *quoted : own_quoted), notes(notes), path(std::move(path)),
      prefix(std::move(prefix)), behind_a_reference(0), lambdas(0),
      top_level(top_level != nullptr ? *top_level : own_top_level)
  {
    if (registry == nullptr) {
      own_registry = std::make_unique<ModuleRegistry>();
      registry = own_registry.get();
    }
    this->registry = registry;
    if (sources == nullptr) {
      own_sources = std::make_unique<SourceManager>();
      sources = own_sources.get();
    }
    this->sources = sources;
  }

  Checker::~Checker() = default;

  InScope::InScope(Checker& checker, Dict<std::string, Held>* top, Dict<std::string, Bundle*>* bundles)
    : checker(checker), kept_top(checker.top), kept_bundles(checker.bundles)
  {
    if (top != nullptr && bundles != nullptr) {
      checker.top = top;
      checker.bundles = bundles;
    }
  }

  InScope::~InScope()
  {
    checker.top = kept_top;
    checker.bundles = kept_bundles;
  }


  // -- the entry points -----------------------------------------------------------

  CheckedMacros check_macros(std::span<const ast::SourceUnit* const> units, DiagEngine& diags, SourceManager* sources,
                             ModuleRegistry* registry, std::optional<std::filesystem::path> path)
  {
    auto module = std::make_unique<ir::Module>();
    module->name = "macros";
    module->triple = "";
    // The quotes are trees the checker and the modules it read hold, and the
    // expander reads them after this returns: all of it is kept with them.
    struct Kept {
      std::unique_ptr<ModuleRegistry> registry;
      std::vector<const ast::Quote*> quoted;
      std::unique_ptr<Checker> checker;
    };
    auto kept = std::make_shared<Kept>();
    if (registry == nullptr) {
      kept->registry = std::make_unique<ModuleRegistry>();
      registry = kept->registry.get();
    }
    kept->checker = std::make_unique<Checker>(*module, diags, registry, path.value_or(std::filesystem::path()), "",
                                              sources, nullptr, nullptr, Conditions::check, true, &kept->quoted);
    kept->checker->run(units, false);
    auto quoted = kept->quoted;
    return { std::move(module), std::move(quoted), std::move(kept) };
  }

  ir::Module& check(ir::Module& module, std::span<const ast::SourceUnit* const> units, DiagEngine& diags,
                    ModuleRegistry* registry, SourceManager* sources, Notes* notes, Conditions conditions,
                    bool whole_modules, bool ignore_system, Session* session)
  {
    std::unique_ptr<ModuleRegistry> own;
    if (registry == nullptr) {
      own = std::make_unique<ModuleRegistry>();
      registry = own.get();
    }
    std::filesystem::path path;
    if (! units.empty())
      path = std::filesystem::path(std::string(units[0]->path));
    Checker found(module, diags, registry, path, "", sources, nullptr, notes, conditions, false, nullptr, whole_modules,
                  ignore_system);
    found.session = session;
    auto& result = found.run(units);
    registry->settle_names();
    return result;
  }

} // namespace pl4g::sema
