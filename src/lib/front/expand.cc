// Expanding macros: a port of pypl4g's front/expand.py.
//
// Python rewrites a tree by `dataclasses.replace` over its fields; here that is
// `rebuild`, a walk by reflection over the members of a node.  Where Python
// would put a node where the C++ tree has no room for its kind -- a run of
// statements where an expression stands -- the run is put in a `Scoped`, which
// is the expression a run of statements is everywhere else.
#include "front/expand.hh"

#include <algorithm>
#include <cctype>
#include <format>
#include <map>
#include <meta>
#include <set>
#include <string>
#include <type_traits>
#include <unordered_map>

#include "diag/ids.hh"
#include "front/interpret.hh"
#include "front/lexer.hh"
#include "sema/tables.hh"
#include "target/allocator.hh"
#include "util/reflect.hh"


namespace pl4g {

  namespace {

    using ast::Node;
    using macro::MValue;

    // The names of what a macro may call, which the machine gives a meaning to.
    constexpr std::string_view quote_name = "\N{APL FUNCTIONAL SYMBOL QUAD}quote";
    constexpr std::string_view fill_name = "\N{APL FUNCTIONAL SYMBOL QUAD}fill";
    constexpr std::string_view fill_number_name = "\N{APL FUNCTIONAL SYMBOL QUAD}fillnumber";
    constexpr std::string_view fill_text_name = "\N{APL FUNCTIONAL SYMBOL QUAD}filltext";
    constexpr std::string_view piece_name = "\N{APL FUNCTIONAL SYMBOL QUAD}name";
    constexpr std::string_view refuse_name = "\N{APL FUNCTIONAL SYMBOL QUAD}refuse";
    constexpr std::string_view head_name = "\N{APL FUNCTIONAL SYMBOL QUAD}head";
    constexpr std::string_view kind_name = "\N{APL FUNCTIONAL SYMBOL QUAD}kind";
    constexpr std::string_view parts_name = "\N{APL FUNCTIONAL SYMBOL QUAD}parts";
    constexpr std::string_view part_name = "\N{APL FUNCTIONAL SYMBOL QUAD}part";
    constexpr std::string_view alike_pieces_name = "\N{APL FUNCTIONAL SYMBOL QUAD}alike";
    constexpr std::string_view caller_name_name = "\N{APL FUNCTIONAL SYMBOL QUAD}caller_name";

    // Which shape each piece of the program is, as the number `⎕kind` answers.
    std::int64_t kind_of(const Node* node)
    {
      using K = ast::NodeKind;
      switch (node->tag) {
      case K::IntLit: return 1;
      case K::FloatLit: return 2;
      case K::StringLit: return 3;
      case K::CharLit: return 4;
      case K::BoolLit: return 5;
      case K::NameRef: return 6;
      case K::Binary: return 7;
      case K::Unary: return 8;
      case K::Call: return 9;
      case K::ArrayLit: return 10;
      case K::TupleLit: return 11;
      case K::Lambda: return 12;
      case K::Block: return 13;
      case K::Member: return 14;
      case K::Element: return 15;
      case K::Index: return 16;
      case K::Fresh: return 17;
      default: return 0;
      }
    }

    // A macro refusing with its own message, which is about the invocation.
    struct Said {
      std::string detail;
    };

    // The name of a node's class, as Python's `type(node).__name__`.
    std::string_view class_name(const Node* node) { return enum_name(node->tag); }


    // -- walking and rebuilding trees by their members ----------------------

    template<typename T>
    inline constexpr bool is_node_pointer = false;
    template<typename T>
      requires std::is_base_of_v<Node, T>
    inline constexpr bool is_node_pointer<const T*> = true;

    template<typename T>
    inline constexpr bool is_list = false;
    template<typename T>
    inline constexpr bool is_list<std::span<const T>> = true;

    template<typename T>
    inline constexpr bool is_optional = false;
    template<typename T>
    inline constexpr bool is_optional<std::optional<T>> = true;

    consteval auto unchecked() { return std::meta::access_context::unchecked(); }

    // Whether `node` is of a kind derived from X.
    template<typename X>
    bool fits(const Node* node)
    {
      return ast::visit(*node, [](const auto& n) { return std::is_base_of_v<X, std::remove_cvref_t<decltype(n)>>; });
    }

    // `node` where the tree wants an X: itself, or a run of statements put in
    // a `Scoped` where an expression or a statement is wanted.
    template<typename X>
    const X* fit(ast::SyntaxArena& arena, const Node* node)
    {
      if (node == nullptr)
        return nullptr;
      if (fits<X>(node))
        return static_cast<const X*>(node);
      if (auto block = ast::as<ast::Block>(node)) {
        if constexpr (std::is_base_of_v<X, ast::Scoped>) {
          auto scoped = arena.make<ast::Scoped>(block->span);
          scoped->body = block;
          return scoped;
        } else if constexpr (std::is_base_of_v<X, ast::ExprStmt>) {
          auto scoped = arena.make<ast::Scoped>(block->span);
          scoped->body = block;
          auto stmt = arena.make<ast::ExprStmt>(block->span);
          stmt->value = scoped;
          return stmt;
        }
      }
      throw InternalError(std::format("a {} put where the tree has no room for one", class_name(node)));
    }

    // What becomes of a tree's children in one rewrite.
    struct Rewrite {
      // What a child becomes.
      std::function<const Node*(const Node*)> node;
      // What a list of children becomes; where unset, each child becomes what
      // `node` makes of it.
      std::function<std::vector<const Node*>(std::span<const Node* const>)> list;
      // Whether the pairs of a dictionary's entries and a unit's standing are
      // walked, as Python's walks over tuples do.
      bool pairs = true;
      // Whether a copy is made though nothing changed: Python's `replace` of
      // every field, which makes a new node whatever the fields come to.
      bool always = false;
    };

    // The children of a list, rewritten.
    std::vector<const Node*> rewritten(std::span<const Node* const> in, const Rewrite& rw)
    {
      if (rw.list)
        return rw.list(in);
      std::vector<const Node*> out;
      out.reserve(in.size());
      for (auto one : in)
        out.push_back(rw.node(one));
      return out;
    }

    // Rewrite one member, answering whether it changed.
    template<typename V>
    bool rewrite_member(ast::SyntaxArena& arena, V& v, const Rewrite& rw)
    {
      if constexpr (is_node_pointer<V>) {
        if (v == nullptr)
          return false;
        const Node* found = rw.node(v);
        if (found == v)
          return false;
        v = fit<std::remove_cvref_t<std::remove_pointer_t<V>>>(arena, found);
        return true;
      } else if constexpr (is_list<V>) {
        using E = std::remove_cv_t<typename V::element_type>;
        if constexpr (is_node_pointer<E>) {
          std::vector<const Node*> in(v.begin(), v.end());
          auto out = rewritten(in, rw);
          if (out == in)
            return false;
          std::vector<E> made;
          made.reserve(out.size());
          for (auto one : out)
            made.push_back(fit<std::remove_cvref_t<std::remove_pointer_t<E>>>(arena, one));
          v = arena.list(made);
          return true;
        } else if constexpr (std::is_same_v<E, ast::ExprPair>) {
          if (! rw.pairs)
            return false;
          std::vector<E> made(v.begin(), v.end());
          bool changed = false;
          for (auto& one : made)
            changed |= rewrite_member(arena, one, rw);
          if (changed)
            v = arena.list(made);
          return changed;
        } else
          return false;
      } else if constexpr (std::is_same_v<V, ast::ExprPair>) {
        bool changed = rewrite_member(arena, v.key, rw);
        changed |= rewrite_member(arena, v.value, rw);
        return changed;
      } else if constexpr (std::is_same_v<V, ast::UnitPair>) {
        if (! rw.pairs)
          return false;
        bool changed = rewrite_member(arena, v.what, rw);
        changed |= rewrite_member(arena, v.where, rw);
        return changed;
      } else if constexpr (is_optional<V>) {
        if (! v.has_value())
          return false;
        return rewrite_member(arena, *v, rw);
      } else
        return false;
    }

    template<typename T>
    bool rewrite_members(ast::SyntaxArena& arena, T& node, const Rewrite& rw)
    {
      bool changed = false;
      template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, unchecked())))
        changed |= rewrite_members(arena, static_cast<[:std::meta::type_of(b):]&>(node), rw);
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, unchecked())))
        if constexpr (std::meta::annotations_of_with_type(m, ^^ast::Hidden).size() == 0)
          changed |= rewrite_member(arena, node.[:m:], rw);
      return changed;
    }

    // A block with these statements, as it was written otherwise.
    const ast::Block* block_with(ast::SyntaxArena& arena, const ast::Block* block, std::span<const Node* const> stmts)
    {
      auto made = arena.make<ast::Block>(block->span);
      made->style = block->style;
      std::vector<const ast::Stmt*> held;
      held.reserve(stmts.size());
      for (auto one : stmts)
        held.push_back(fit<ast::Stmt>(arena, one));
      made->statements = arena.list(held);
      return made;
    }

    // `node` with its children rewritten: itself where none changed and no
    // copy is asked for.
    const Node* rebuild(ast::SyntaxArena& arena, const Node* node, const Rewrite& rw)
    {
      if (node == nullptr)
        return nullptr;
      if (auto block = ast::as<ast::Block>(node)) {
        auto stmts = block->stmts();
        std::vector<const Node*> in(stmts.begin(), stmts.end());
        auto out = rewritten(in, rw);
        if (out == in && ! rw.always)
          return node;
        return block_with(arena, block, out);
      }
      return ast::visit(*node, [&](const auto& n) -> const Node* {
        using T = std::remove_cvref_t<decltype(n)>;
        T copy = n;
        bool changed = rewrite_members(arena, copy, rw);
        if (! changed && ! rw.always)
          return node;
        return arena.copy(copy);
      });
    }

    // A node the rewrite just made, to be changed further.
    template<typename T>
    T* mutable_node(const Node* node)
    {
      return const_cast<T*>(static_cast<const T*>(node));
    }

    // Call `f` with every child of `node`; with `pairs` false, a dictionary's
    // entries are not walked, as a walk over tuples of nodes alone does not.
    template<typename F>
    void children(const Node* node, F&& f, bool pairs = true);

    template<typename V, typename F>
    void child_member(const V& v, F& f, bool pairs)
    {
      if constexpr (is_node_pointer<V>) {
        if (v != nullptr)
          f(static_cast<const Node*>(v));
      } else if constexpr (is_list<V>) {
        using E = std::remove_cv_t<typename V::element_type>;
        if constexpr (is_node_pointer<E>)
          for (auto one : v)
            child_member(one, f, pairs);
        else if constexpr (std::is_same_v<E, ast::ExprPair>) {
          if (pairs)
            for (const auto& one : v) {
              child_member(one.key, f, pairs);
              child_member(one.value, f, pairs);
            }
        }
      } else if constexpr (std::is_same_v<V, ast::UnitPair>) {
        child_member(v.what, f, pairs);
        child_member(v.where, f, pairs);
      } else if constexpr (is_optional<V>) {
        if (v.has_value())
          child_member(*v, f, pairs);
      }
    }

    template<typename T, typename F>
    void child_members(const T& node, F& f, bool pairs)
    {
      template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, unchecked())))
        child_members(static_cast<const [:std::meta::type_of(b):]&>(node), f, pairs);
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, unchecked())))
        if constexpr (std::meta::annotations_of_with_type(m, ^^ast::Hidden).size() == 0)
          child_member(node.[:m:], f, pairs);
    }

    template<typename F>
    void children(const Node* node, F&& f, bool pairs)
    {
      if (node == nullptr)
        return;
      if (auto block = ast::as<ast::Block>(node)) {
        for (auto stmt : block->stmts())
          f(static_cast<const Node*>(stmt));
        return;
      }
      ast::visit(*node, [&](const auto& n) { child_members(n, f, pairs); });
    }


    // -- comparing what is written ------------------------------------------

    bool written_alike(const Node* one, const Node* other);

    template<typename V>
    bool alike_value(const V& one, const V& other)
    {
      if constexpr (is_node_pointer<V>)
        return written_alike(one, other);
      else if constexpr (is_list<V>) {
        if (one.size() != other.size())
          return false;
        for (std::size_t i = 0; i < one.size(); ++i)
          if (! alike_value(one[i], other[i]))
            return false;
        return true;
      } else if constexpr (std::is_same_v<V, ast::ExprPair>)
        return alike_value(one.key, other.key) && alike_value(one.value, other.value);
      else if constexpr (std::is_same_v<V, ast::UnitPair>)
        return alike_value(one.what, other.what) && alike_value(one.where, other.where);
      else if constexpr (std::is_same_v<V, ast::NameAt>)
        return one.name == other.name && one.span == other.span;
      else if constexpr (std::is_same_v<V, ast::Integer>)
        return one.value == other.value && one.too_large == other.too_large;
      else if constexpr (std::is_same_v<V, ast::Scale>)
        return alike_value(one.over, other.over) && alike_value(one.under, other.under);
      else if constexpr (is_optional<V>) {
        if (one.has_value() != other.has_value())
          return false;
        return ! one.has_value() || alike_value(*one, *other);
      } else
        return one == other;
    }

    // The members compared: every one but where it was written.
    consteval bool compared(std::meta::info m)
    {
      auto name = std::meta::identifier_of(m);
      return name != "span" && name != "tag" && ! name.ends_with("_span");
    }

    template<typename T>
    bool alike_members(const T& one, const T& other)
    {
      template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, unchecked()))) {
        using B = [:std::meta::type_of(b):];
        if (! alike_members(static_cast<const B&>(one), static_cast<const B&>(other)))
          return false;
      }
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, unchecked())))
        if constexpr (std::meta::annotations_of_with_type(m, ^^ast::Hidden).size() == 0 && compared(m))
          if (! alike_value(one.[:m:], other.[:m:]))
            return false;
      return true;
    }

    // Whether two pieces of a program are the same thing written: the trees
    // compared, the spans not.
    bool written_alike(const Node* one, const Node* other)
    {
      if (one == nullptr || other == nullptr)
        return one == other;
      if (one->tag != other->tag)
        return false;
      if (auto block = ast::as<ast::Block>(one)) {
        auto theirs = static_cast<const ast::Block*>(other);
        return block->style == theirs->style && alike_value(block->stmts(), theirs->stmts());
      }
      return ast::visit(*one, [&](const auto& n) {
        using T = std::remove_cvref_t<decltype(n)>;
        return alike_members(n, static_cast<const T&>(*other));
      });
    }


    // -- what is written below a node ---------------------------------------

    // Whether a type as written names a piece of the program.
    bool names_syntax(const Node* node)
    {
      if (node == nullptr)
        return false;
      if (auto ref = ast::as<ast::TypeRef>(node); ref != nullptr && ! ref->module && ref->name == "syntax")
        return true;
      bool found = false;
      children(node, [&](const Node* one) { found = found || names_syntax(one); });
      return found;
    }

    // Whether a signature names a piece of the program anywhere.
    bool about_a_piece(const ast::FuncDef* node)
    {
      for (auto one : node->params)
        if (names_syntax(one->type))
          return true;
      return node->ret_type != nullptr && names_syntax(node->ret_type);
    }

    // Whether a definition belongs to the macros and not to the program.
    bool only_for_the_macros(const Node* item)
    {
      auto func = ast::as<ast::FuncDef>(item);
      if (func == nullptr)
        return false;
      return func->is_macro || (func->at_compile_time && about_a_piece(func));
    }

    // Whether a definition goes into the unit the macros are checked in.
    bool belongs_to_the_macros(const Node* item)
    {
      if (auto func = ast::as<ast::FuncDef>(item))
        return func->at_compile_time;
      return ast::is<ast::TypeDef>(item) || ast::is<ast::EnumDef>(item) || ast::is<ast::UnitDef>(item)
        || ast::is<ast::ModuleImport>(item);
    }

    // Every module a macro is invoked through below `node`.  A dictionary's
    // entries are not looked in, as pypl4g's walk does not.
    void invoked_through(const Node* node, std::set<std::string, std::less<>>& into)
    {
      if (auto invoke = ast::as<ast::Invoke>(node); invoke != nullptr && invoke->through)
        into.emplace(*invoke->through);
      children(node, [&](const Node* one) { invoked_through(one, into); }, false);
    }

    // Every hole below `node`, in the order they are written.
    void holes_of(const Node* node, std::vector<const ast::Hole*>& into)
    {
      if (auto hole = ast::as<ast::Hole>(node)) {
        into.push_back(hole);
        return;
      }
      children(node, [&](const Node* one) { holes_of(one, into); });
    }

    // The name of every hole written below `node`, once each, in order.
    void holes_in(const Node* node, std::vector<std::string>& into)
    {
      if (auto hole = ast::as<ast::Hole>(node); hole != nullptr && std::ranges::find(into, hole->name) == into.end())
        into.emplace_back(hole->name);
      children(node, [&](const Node* one) { holes_in(one, into); });
    }

    using Names = std::set<std::string, std::less<>>;

    // Every name a template binds, which is what hygiene renames.  Sorted:
    // pypl4g walks a set of strings, whose order is the hash's.
    void bound_in(const Node* node, Names& into)
    {
      if (auto def = ast::as<ast::VarDef>(node)) {
        into.emplace(def->name);
        for (const auto& one : def->more)
          into.emplace(one.name);
      }
      children(node, [&](const Node* one) { bound_in(one, into); });
    }

    // Every name a quote written below `node` binds.
    void bound_in_quotes(const Node* node, Names& into)
    {
      if (ast::is<ast::Quote>(node)) {
        bound_in(node, into);
        return;
      }
      children(node, [&](const Node* one) { bound_in_quotes(one, into); });
    }

    // Whether `inner` is written inside `outer`.
    bool within(Span inner, Span outer) noexcept
    {
      return inner.is_valid() && outer.start <= inner.start && inner.end <= outer.end;
    }

    // Whether a run of statements ends in an expression.
    bool comes_to_a_value(const ast::Block* block)
    {
      auto stmts = block->stmts();
      return ! stmts.empty() && ast::is<ast::ExprStmt>(stmts.back());
    }

    // Whether the last parameter stands for all the arguments from there on.
    bool has_several(const ast::FuncDef* written)
    {
      return ! written->params.empty() && written->params.back()->several;
    }

    // How many arguments a macro takes, in words a message can use.
    std::string takes(const ast::FuncDef* written)
    {
      if (! has_several(written))
        return std::to_string(written->params.size());
      return std::format("{} or more", written->params.size() - 1);
    }

    // What a piece applies its head to.
    std::vector<const Node*> parts_of(const Node* node)
    {
      if (auto binary = ast::as<ast::Binary>(node))
        return { binary->left, binary->right };
      if (auto unary = ast::as<ast::Unary>(node))
        return { unary->operand };
      if (auto fresh = ast::as<ast::Fresh>(node))
        return { fresh->operands.begin(), fresh->operands.end() };
      if (auto call = ast::as<ast::Call>(node))
        return { call->args.begin(), call->args.end() };
      if (auto quote = ast::as<ast::Quote>(node))
        return { quote->pieces.begin(), quote->pieces.end() };
      return {};
    }

    // The name the macro's function goes by in the module it was lowered into.
    std::string macro_symbol(const ast::FuncDef* node, const MacroModule& made)
    {
      for (const auto& [name, func] : made.module->functions)
        if (func->name == node->name)
          return name;
      return std::string(node->name);
    }

    // A hole's number, as the machine handed it over.
    std::int64_t counted(const MValue& at)
    {
      auto n = at.number();
      if (n == nullptr || *n < 0) [[unlikely]]
        throw macro::Refused("a hole that is not one");
      return std::int64_t(*n);
    }

    // What the machine handed a builtin as its `i`th argument.
    MValue arg(std::span<const MValue> args, std::size_t i) { return i < args.size() ? args[i] : MValue{}; }


    class Expander {
    public:
      Expander(ast::SyntaxArena& arena, std::span<const ast::SourceUnit* const> units, DiagEngine& diags,
               CheckMacros checked, ImportedUnit imported)
        : arena(arena), units(units), diags(diags), checked(std::move(checked)), imported(std::move(imported)) {}

      // Collect the macros, then rewrite every unit without them.
      std::vector<const ast::SourceUnit*> run();

      // What keeps the trees the macros quoted alive, where any were.
      std::shared_ptr<void> keeper() const { return made ? made->keeper : nullptr; }

    private:
      void collect_written(const ast::FuncDef* node);
      void collect_imported(const ast::ModuleImport* node);
      void collect(const ast::MacroDef* node);
      void check_rule(const ast::MacroDef* node, const ast::Rule* rule);

      const Node* walked(const Node* node);
      std::vector<const Node*> statements(std::span<const Node* const> stmts);
      const Node* found_macro(const ast::Invoke* node) const;
      const Node* expanded(const ast::Invoke* node, bool as_statement);
      const Node* expanded_as_written(const ast::Invoke* node, bool as_statement);
      const Node* by_rule(const ast::Invoke* node, bool as_statement, const ast::MacroDef* macro);
      const Node* ran(const ast::Invoke* node, bool as_statement, const ast::FuncDef* written);
      const MacroModule* prepared();
      macro::Builtins builtins();

      MValue refuses(const MValue& text);
      MValue caller_name(const MValue& text);
      Span written_at(std::string_view name) const;
      std::string read_text(const MValue& held) const;

      std::int64_t handle(const Node* tree);
      const Node* tree(const MValue& handle) const;

      MValue ran_named(const ir::Function* func, std::span<const Node* const> given, const ast::FuncDef* written);
      const Node* reached(const Node* tree);
      const Node* requalified(const Node* node, std::string_view through, const Names& named);
      MValue quoted(const MValue& at);
      const Node* renamed(const Node* tree, const ast::Quote* quote);
      MValue put_into(const MValue& tree, const MValue& at, const Node* put);
      MValue spelling(const MValue& handle);

      bool matches(const ast::Quote* pattern, const ast::Quote* given, std::map<std::string, const ast::Expr*>& bound);
      bool alike(const Node* pattern, const Node* given, std::map<std::string, const ast::Expr*>& bound);
      const ast::Quote* template_of(const ast::Quote* written);
      const Node* filled(const ast::Quote* written, const std::map<std::string, const ast::Expr*>& bound);
      std::string fresh(std::string_view name);
      const Node* put(const Node* node, const std::map<std::string, const ast::Expr*>& bound,
                      const std::map<std::string, std::string, std::less<>>& renamed);

      const Node* with_names(const Node* node, const std::map<std::string, std::string, std::less<>>& wanted);
      const Node* fill_counting(const Node* node, const ast::Hole* at, const Node* put);
      const Node* assignment(const ast::Expr* target, const Node* value, Span span);
      const Node* head_of(const Node* node);
      const Node* spread_out(const Node* node);
      std::optional<std::vector<const Node*>> handed_over(const ast::Quote* arguments, const ast::FuncDef* written);

      const ast::NameRef* name_ref(Span span, std::string_view name)
      {
        auto ref = arena.make<ast::NameRef>(span);
        ref->name = arena.text(name);
        return ref;
      }

      ast::SyntaxArena& arena;
      std::span<const ast::SourceUnit* const> units;
      DiagEngine& diags;
      CheckMacros checked;
      ImportedUnit imported;
      // The macros written as functions, by name, and the module they were
      // lowered into once anything asked for one.
      std::map<std::string, const ast::FuncDef*, std::less<>> written;
      std::optional<MacroModule> made;
      std::unique_ptr<macro::Machine> machine;
      // Where the macro written as a function that is running was written,
      // and what the names its quotes bind are called in this run of it.
      std::optional<Span> running;
      std::map<std::string, std::string, std::less<>> run_names;
      // Every piece of the program a handle stands for, the handle each tree
      // has, and the holes of the quote each handle came from.
      std::vector<const Node*> trees;
      std::unordered_map<const Node*, std::int64_t> handles;
      std::unordered_map<std::int64_t, std::vector<const ast::Hole*>> holes;
      // The macros, by name, and those of a module imported, by the name the
      // import gave the module and the macro's.
      std::map<std::string, const ast::MacroDef*, std::less<>> macros;
      std::map<std::pair<std::string, std::string>, const Node*> through;
      // What each of those modules defines at the top level, and the module a
      // macro is being run out of just now.
      std::map<std::string, Names, std::less<>> defines;
      std::optional<std::string> invoking;
      // The invocation a macro is being run for.
      const ast::Invoke* invocation = nullptr;
      std::int64_t renames = 0;
      int deep = 0;
    };


    std::vector<const ast::SourceUnit*> Expander::run()
    {
      Names wanted;
      for (auto unit : units)
        for (auto item : unit->items)
          invoked_through(item, wanted);
      for (auto unit : units)
        for (auto item : unit->items)
          if (auto macro = ast::as<ast::MacroDef>(item))
            collect(macro);
          else if (auto func = ast::as<ast::FuncDef>(item); func != nullptr && func->is_macro)
            collect_written(func);
          else if (auto use = ast::as<ast::ModuleImport>(item); use != nullptr && wanted.contains(use->name))
            collect_imported(use);
      if (macros.empty() && written.empty() && through.empty())
        // A program with no macro is handed back as it is.
        return { units.begin(), units.end() };
      std::vector<const ast::SourceUnit*> answer;
      for (auto unit : units) {
        std::vector<const Node*> items;
        for (auto item : unit->items)
          if (! ast::is<ast::MacroDef>(item) && ! only_for_the_macros(item))
            items.push_back(walked(item));
        auto copy = arena.copy(*unit);
        copy->items = arena.list(items);
        answer.push_back(copy);
      }
      return answer;
    }


    void Expander::collect_written(const ast::FuncDef* node)
    {
      Span previous = invalid_span;
      bool seen = false;
      if (auto m = macros.find(node->name); m != macros.end()) {
        previous = m->second->name_span;
        seen = true;
      } else if (auto w = written.find(node->name); w != written.end()) {
        previous = w->second->name_span;
        seen = true;
      }
      if (seen) {
        diags.emit(D::LANG_FILESTRUCT_DUPLICATE_DEFINITION, node->name_span, { { "name", node->name } })
          .note(D::LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous, { { "name", node->name } });
        return;
      }
      if (node->ret_type == nullptr || ! names_syntax(node->ret_type)) {
        // What replaces the invocation is a piece of the program, so that is
        // what a macro answers.
        diags.emit(D::LANG_MACRO_ANSWERS_OTHERWISE, node->name_span,
                   { { "name", node->name }, { "found", node->ret_type == nullptr ? "nothing" : "something else" } });
        return;
      }
      written.emplace(std::string(node->name), node);
    }


    void Expander::collect_imported(const ast::ModuleImport* node)
    {
      if (! imported)
        return;
      const ast::SourceUnit* unit = imported(node);
      if (unit == nullptr)
        return;
      Names named;
      for (auto item : unit->items) {
        auto func = ast::as<ast::FuncDef>(item);
        if (ast::is<ast::MacroDef>(item) || (func != nullptr && func->is_macro)) {
          std::string_view name = func != nullptr ? func->name : ast::as<ast::MacroDef>(item)->name;
          through[{ std::string(node->name), std::string(name) }] = item;
        }
        // Every name the module writes at the top level.
        ast::visit(*item, [&](const auto& n) {
          if constexpr (requires { { n.name } -> std::convertible_to<std::string_view>; })
            named.emplace(n.name);
        });
      }
      defines[std::string(node->name)] = std::move(named);
    }


    void Expander::collect(const ast::MacroDef* node)
    {
      if (auto previous = macros.find(node->name); previous != macros.end()) {
        diags.emit(D::LANG_FILESTRUCT_DUPLICATE_DEFINITION, node->name_span, { { "name", node->name } })
          .note(D::LANG_FILESTRUCT_PREVIOUS_DEFINITION, previous->second->name_span, { { "name", node->name } });
        return;
      }
      for (auto rule : node->rules)
        check_rule(node, rule);
      macros.emplace(std::string(node->name), node);
    }


    void Expander::check_rule(const ast::MacroDef* node, const ast::Rule* rule)
    {
      std::vector<std::string> have;
      holes_in(rule->pattern, have);
      std::vector<std::string> wanted;
      holes_in(rule->template_, wanted);
      for (const auto& name : wanted)
        if (std::ranges::find(have, name) == have.end())
          diags.emit(D::LANG_MACRO_HOLE_UNKNOWN, rule->template_->span, { { "name", name }, { "macro", node->name } });
    }


    // -- rewriting ------------------------------------------------------------

    const Node* Expander::walked(const Node* node)
    {
      if (auto invoke = ast::as<ast::Invoke>(node))
        return expanded(invoke, false);
      if (auto stmt = ast::as<ast::ExprStmt>(node))
        if (auto invoke = ast::as<ast::Invoke>(stmt->value)) {
          // A line of its own, where a macro that writes statements may stand.
          const Node* found = expanded(invoke, true);
          if (ast::is<ast::Block>(found))
            return found;
          auto copy = arena.copy(*stmt);
          copy->value = fit<ast::Expr>(arena, found);
          return copy;
        }
      if (ast::is<ast::Hole>(node)) {
        diags.emit(D::LANG_MACRO_HOLE_OUTSIDE, node->span);
        return node;
      }
      if (auto block = ast::as<ast::Block>(node)) {
        auto stmts = block->stmts();
        std::vector<const Node*> in(stmts.begin(), stmts.end());
        return block_with(arena, block, statements(in));
      }
      // Rebuilt only where something below it changed.
      return rebuild(arena, node, { .node = [this](const Node* one) { return walked(one); }, .list = {} });
    }


    std::vector<const Node*> Expander::statements(std::span<const Node* const> stmts)
    {
      std::vector<const Node*> found;
      for (auto one : stmts) {
        const Node* done = walked(one);
        if (auto block = ast::as<ast::Block>(done))
          for (auto stmt : block->stmts())
            found.push_back(stmt);
        else
          found.push_back(done);
      }
      return found;
    }


    const Node* Expander::found_macro(const ast::Invoke* node) const
    {
      if (node->through) {
        auto found = through.find({ std::string(*node->through), std::string(node->name) });
        return found == through.end() ? nullptr : found->second;
      }
      if (auto m = macros.find(node->name); m != macros.end())
        return m->second;
      if (auto w = written.find(node->name); w != written.end())
        return w->second;
      return nullptr;
    }


    const Node* Expander::expanded(const ast::Invoke* node, bool as_statement)
    {
      return spread_out(expanded_as_written(node, as_statement));
    }


    const Node* Expander::expanded_as_written(const ast::Invoke* node, bool as_statement)
    {
      if (deep >= macro_rounds) {
        diags.emit(D::LANG_MACRO_TOO_DEEP, node->span, { { "name", node->name } });
        return name_ref(node->span, node->name);
      }
      const Node* macro = found_macro(node);
      if (macro == nullptr) {
        diags.emit(D::LANG_MACRO_UNKNOWN, node->name_span, { { "name", node->name } });
        return name_ref(node->span, node->name);
      }
      if (auto func = ast::as<ast::FuncDef>(macro))
        return ran(node, as_statement, func);
      auto outer = invoking;
      invoking = node->through ? std::optional<std::string>(*node->through) : std::nullopt;
      struct Restore {
        std::optional<std::string>& at;
        std::optional<std::string> was;
        ~Restore() { at = std::move(was); }
      } restore{ invoking, std::move(outer) };
      return by_rule(node, as_statement, ast::as<ast::MacroDef>(macro));
    }


    const Node* Expander::by_rule(const ast::Invoke* node, bool as_statement, const ast::MacroDef* macro)
    {
      for (auto rule : macro->rules) {
        std::map<std::string, const ast::Expr*> bound;
        if (! matches(rule->pattern, node->arguments, bound))
          continue;
        const Node* done = filled(template_of(rule->template_), bound);
        if (auto block = ast::as<ast::Block>(done); block != nullptr && ! as_statement) {
          if (! comes_to_a_value(block)) {
            diags.emit(D::LANG_MACRO_WRITES_STATEMENTS, node->span, { { "name", node->name } });
            return name_ref(node->span, node->name);
          }
          auto scoped = arena.make<ast::Scoped>(node->span);
          scoped->body = block;
          done = scoped;
        }
        // What came out is expanded in turn, so a macro may write another.
        ++deep;
        struct Leave {
          int& deep;
          ~Leave() { --deep; }
        } leave{ deep };
        return walked(done);
      }
      diags.emit(D::LANG_MACRO_NO_RULE, node->span, { { "name", node->name } });
      return name_ref(node->span, node->name);
    }


    // -- running a macro written as a function --------------------------------

    const Node* Expander::ran(const ast::Invoke* node, bool as_statement, const ast::FuncDef* written)
    {
      const MacroModule* lowered = prepared();
      if (lowered == nullptr)
        return name_ref(node->span, node->name);
      const ir::Function* func = lowered->module->function(macro_symbol(written, *lowered));
      if (func == nullptr)
        return name_ref(node->span, node->name);
      auto given = handed_over(node->arguments, written);
      if (! given) {
        diags.emit(D::LANG_MACRO_ARGUMENT_COUNT, node->span,
                   { { "name", node->name }, { "expected", takes(written) },
                     { "found", std::to_string(node->arguments->pieces.size()) } });
        return name_ref(node->span, node->name);
      }
      auto outer = invoking;
      invoking = node->through ? std::optional<std::string>(*node->through) : std::nullopt;
      const ast::Invoke* was = invocation;
      invocation = node;
      MValue answer;
      try {
        answer = ran_named(func, *given, written);
      } catch (const Said& said) {
        // The macro would not write what it was asked for and said why.
        invocation = was;
        invoking = outer;
        diags.emit(D::LANG_MACRO_REFUSED, node->span, { { "detail", said.detail } });
        return name_ref(node->span, node->name);
      } catch (const macro::Stopped& stopped) {
        invocation = was;
        invoking = outer;
        diags.emit(D::LANG_MACRO_RAN_BADLY, node->span, { { "name", node->name }, { "detail", stopped.detail } });
        return name_ref(node->span, node->name);
      } catch (const macro::Refused& refused) {
        invocation = was;
        invoking = outer;
        diags.emit(D::LANG_MACRO_CANNOT_RUN, node->span, { { "name", node->name }, { "detail", refused.detail } });
        return name_ref(node->span, node->name);
      }
      invocation = was;
      invoking = outer;
      auto n = answer.number();
      if (n == nullptr || *n < 0 || *n >= __int128(trees.size())) {
        diags.emit(D::LANG_MACRO_ANSWERS_OTHERWISE, node->span,
                   { { "name", node->name }, { "found", "something that is not one" } });
        return name_ref(node->span, node->name);
      }
      const Node* found = trees[std::size_t(*n)];
      if (auto block = ast::as<ast::Block>(found); block != nullptr && ! as_statement) {
        if (! comes_to_a_value(block)) {
          diags.emit(D::LANG_MACRO_WRITES_STATEMENTS, node->span, { { "name", node->name } });
          return name_ref(node->span, node->name);
        }
        auto scoped = arena.make<ast::Scoped>(node->span);
        scoped->body = block;
        found = scoped;
      }
      ++deep;
      struct Leave {
        int& deep;
        ~Leave() { --deep; }
      } leave{ deep };
      return walked(found);
    }


    const MacroModule* Expander::prepared()
    {
      if (made)
        return &*made;
      if (! checked)
        return nullptr;
      std::vector<const ast::SourceUnit*> wanted;
      for (auto unit : units) {
        std::vector<const Node*> kept;
        for (auto item : unit->items)
          if (belongs_to_the_macros(item))
            kept.push_back(item);
        auto copy = arena.copy(*unit);
        copy->items = arena.list(kept);
        wanted.push_back(copy);
      }
      auto found = checked(wanted);
      if (! found)
        return nullptr;
      made = std::move(*found);
      machine = std::make_unique<macro::Machine>();
      machine->knows(builtins());
      return &*made;
    }


    macro::Builtins Expander::builtins()
    {
      macro::Builtins found;
      found[std::string(quote_name)] = [this](std::span<const MValue> a) { return quoted(arg(a, 0)); };
      found[std::string(fill_name)] = [this](std::span<const MValue> a) {
        // The piece put in is asked for first, as Python works out its
        // arguments before the call.
        const Node* with = tree(arg(a, 2));
        return put_into(arg(a, 0), arg(a, 1), with);
      };
      found[std::string(fill_number_name)] = [this](std::span<const MValue> a) {
        MValue number = arg(a, 2);
        auto n = number.number();
        if (n == nullptr) [[unlikely]]
          throw macro::Refused("a number wanted");
        if (*n < 0) [[unlikely]]
          // pypl4g writes `ast.UnaryOp.NEGATE`, which there is none of, and
          // stops with an internal error; so does this.
          throw InternalError("a macro filled a hole with a negative number, which pypl4g cannot write");
        auto written = arena.make<ast::IntLit>(invalid_span);
        written->value = ast::Integer{ *n, false };
        written->type_name = arena.text("i64");
        return put_into(arg(a, 0), arg(a, 1), written);
      };
      found[std::string(fill_text_name)] = [this](std::span<const MValue> a) {
        auto text = arena.make<ast::StringLit>(invalid_span);
        text->value = arena.text(read_text(arg(a, 2)));
        return put_into(arg(a, 0), arg(a, 1), text);
      };
      found[std::string(piece_name)] = [this](std::span<const MValue> a) { return spelling(arg(a, 0)); };
      found[std::string(refuse_name)] = [this](std::span<const MValue> a) { return refuses(arg(a, 0)); };
      found[std::string(head_name)] = [this](std::span<const MValue> a) {
        return MValue(__int128(handle(head_of(tree(arg(a, 0))))));
      };
      found[std::string(kind_name)] = [this](std::span<const MValue> a) {
        return MValue(__int128(kind_of(tree(arg(a, 0)))));
      };
      found[std::string(parts_name)] = [this](std::span<const MValue> a) {
        return MValue(__int128(parts_of(tree(arg(a, 0))).size()));
      };
      found[std::string(part_name)] = [this](std::span<const MValue> a) {
        auto parts = parts_of(tree(arg(a, 0)));
        auto number = counted(arg(a, 1));
        if (std::size_t(number) >= parts.size()) [[unlikely]]
          throw macro::Refused("a part that is not there");
        return MValue(__int128(handle(parts[std::size_t(number)])));
      };
      found[std::string(alike_pieces_name)] = [this](std::span<const MValue> a) {
        const Node* one = tree(arg(a, 0));
        const Node* other = tree(arg(a, 1));
        return MValue(written_alike(one, other));
      };
      found[std::string(caller_name_name)] = [this](std::span<const MValue> a) { return caller_name(arg(a, 0)); };
      // And the allocator: the machine is the allocator while a macro runs.
      auto allocates = [this](const MValue& size) {
        auto n = size.number();
        if (n == nullptr) [[unlikely]]
          throw InternalError("a macro asked for room of no size");
        return MValue(machine->allocate(*n));
      };
      auto in_arena = [allocates](std::span<const MValue> a) { return allocates(arg(a, 1)); };
      found[std::string(target::alloc_symbol)] = in_arena;
      found[std::string(target::grow_symbol)] = in_arena;
      found[std::string(sema::tables::heap_alloc_symbol)] = [allocates](std::span<const MValue> a) {
        return allocates(arg(a, 0));
      };
      found[std::string(sema::tables::heap_free_symbol)] = [](std::span<const MValue>) { return MValue{}; };
      return found;
    }


    MValue Expander::refuses(const MValue& text)
    {
      throw Said{ read_text(text) };
    }


    MValue Expander::caller_name(const MValue& text)
    {
      std::string name = read_text(text);
      if (! is_a_name(name)) [[unlikely]]
        throw Said{ std::format("'{}' is not a name, and a name is all a macro may read out of the caller's text",
                                name) };
      return MValue(__int128(handle(name_ref(written_at(name), name))));
    }


    Span Expander::written_at(std::string_view name) const
    {
      if (invocation == nullptr)
        return invalid_span;
      std::string braced = std::format("{{{}}}", name);
      for (auto piece : invocation->arguments->pieces) {
        auto literal = ast::as<ast::StringLit>(piece);
        if (literal == nullptr)
          continue;
        auto at = literal->value.find(braced);
        if (at == std::string_view::npos)
          continue;
        // pypl4g counts the literal in characters and what it holds in bytes:
        // the two agree, and say there is no escape, only where the literal is
        // ASCII and holds what was written.
        bool ascii = std::ranges::all_of(literal->value, [](char c) { return (unsigned char) c < 0x80; });
        if (! ascii || literal->span.end - literal->span.start != Loc(literal->value.size()) + 2)
          return literal->span;
        Loc start = literal->span.start + 1 + Loc(at) + 1;
        return { start, start + Loc(name.size()) };
      }
      return invocation->span;
    }


    std::string Expander::read_text(const MValue& held) const
    {
      auto parts = held.parts();
      if (parts == nullptr || parts->size() != 3) [[unlikely]]
        throw macro::Refused("text wanted");
      return machine->read_text(held);
    }


    // -- handles --------------------------------------------------------------

    std::int64_t Expander::handle(const Node* tree)
    {
      if (auto found = handles.find(tree); found != handles.end())
        return found->second;
      trees.push_back(tree);
      auto at = std::int64_t(trees.size()) - 1;
      handles.emplace(tree, at);
      return at;
    }


    const Node* Expander::tree(const MValue& handle) const
    {
      auto n = handle.number();
      if (n == nullptr || *n < 0 || *n >= __int128(trees.size())) [[unlikely]]
        throw macro::Refused("a piece of the program that is not one");
      return trees[std::size_t(*n)];
    }


    // -- what the builtins do -------------------------------------------------

    MValue Expander::ran_named(const ir::Function* func, std::span<const Node* const> given,
                               const ast::FuncDef* written)
    {
      auto was_running = running;
      auto was_names = std::move(run_names);
      struct Restore {
        Expander& self;
        std::optional<Span> running;
        std::map<std::string, std::string, std::less<>> names;
        ~Restore()
        {
          self.running = running;
          self.run_names = std::move(names);
        }
      } restore{ *this, was_running, std::move(was_names) };
      running = func->span;
      Names bound;
      bound_in_quotes(written, bound);
      run_names.clear();
      for (const auto& name : bound)
        run_names.emplace(name, fresh(name));
      std::vector<MValue> args;
      for (auto one : given)
        args.push_back(__int128(handle(one)));
      return machine->call(func, args);
    }


    const Node* Expander::reached(const Node* tree)
    {
      if (! invoking)
        return tree;
      auto named = defines.find(*invoking);
      if (named == defines.end() || named->second.empty())
        return tree;
      return requalified(tree, *invoking, named->second);
    }


    const Node* Expander::requalified(const Node* node, std::string_view through, const Names& named)
    {
      if (auto ref = ast::as<ast::NameRef>(node); ref != nullptr && named.contains(ref->name)) {
        auto member = arena.make<ast::Member>(ref->span);
        member->base = name_ref(ref->span, through);
        member->name = ref->name;
        member->name_span = ref->span;
        return member;
      }
      Rewrite rw{
        .node = [&](const Node* one) { return requalified(one, through, named); }, .list = {}, .pairs = false
      };
      return rebuild(arena, node, rw);
    }


    MValue Expander::quoted(const MValue& at)
    {
      auto n = at.number();
      if (n == nullptr || *n < 0 || *n >= __int128(made->quoted.size())) [[unlikely]]
        throw macro::Refused("a quote that was never written");
      const ast::Quote* quote = made->quoted[std::size_t(*n)];
      if (quote->body == nullptr && quote->pieces.size() != 1) [[unlikely]]
        throw macro::Refused("a quote of more than one expression");
      const Node* tree = quote->body != nullptr ? static_cast<const Node*>(quote->body) : quote->pieces[0];
      // What the quote binds is renamed, once per run of the macro that wrote
      // it; the holes come through as the same nodes.
      tree = renamed(tree, quote);
      tree = reached(tree);
      std::vector<const ast::Hole*> found_holes;
      holes_of(tree, found_holes);
      auto found = handle(tree);
      holes[found] = std::move(found_holes);
      return __int128(found);
    }


    const Node* Expander::renamed(const Node* tree, const ast::Quote* quote)
    {
      Names bound;
      bound_in(tree, bound);
      std::map<std::string, std::string, std::less<>> wanted;
      for (const auto& name : bound)
        wanted.emplace(name, fresh(name));
      if (running && within(quote->span, *running))
        wanted = run_names;
      return wanted.empty() ? tree : with_names(tree, wanted);
    }


    MValue Expander::put_into(const MValue& tree_at, const MValue& at, const Node* put)
    {
      auto which = counted(tree_at);
      auto found_holes = holes.find(which);
      auto number = counted(at);
      if (found_holes == holes.end() || std::size_t(number) >= found_holes->second.size()) [[unlikely]]
        throw macro::Refused("a hole that the quote does not have");
      auto kept = found_holes->second;
      auto found = handle(fill_counting(tree(tree_at), kept[std::size_t(number)], put));
      holes[found] = std::move(kept);
      return __int128(found);
    }


    MValue Expander::spelling(const MValue& at)
    {
      const Node* found = tree(at);
      if (auto literal = ast::as<ast::StringLit>(found))
        return machine->text(literal->value);
      if (auto ref = ast::as<ast::NameRef>(found))
        return machine->text(ref->name);
      std::string name(class_name(found));
      std::ranges::transform(name, name.begin(), [](char c) { return char(std::tolower((unsigned char) c)); });
      throw macro::Refused(std::format("{} is written as no one word", name));
    }


    // -- matching -------------------------------------------------------------

    bool Expander::matches(const ast::Quote* pattern, const ast::Quote* given,
                           std::map<std::string, const ast::Expr*>& bound)
    {
      if (pattern->body != nullptr || given->body != nullptr)
        return false;
      if (pattern->pieces.size() != given->pieces.size())
        return false;
      for (std::size_t i = 0; i < pattern->pieces.size(); ++i)
        if (! alike(pattern->pieces[i], given->pieces[i], bound))
          return false;
      return true;
    }


    bool Expander::alike(const Node* pattern, const Node* given, std::map<std::string, const ast::Expr*>& bound)
    {
      if (auto hole = ast::as<ast::Hole>(pattern)) {
        if (auto earlier = bound.find(std::string(hole->name)); earlier != bound.end())
          return written_alike(earlier->second, given);
        bound[std::string(hole->name)] = static_cast<const ast::Expr*>(given);
        return true;
      }
      return written_alike(pattern, given);
    }


    // -- filling --------------------------------------------------------------

    const ast::Quote* Expander::template_of(const ast::Quote* written)
    {
      return static_cast<const ast::Quote*>(reached(written));
    }


    const Node* Expander::filled(const ast::Quote* written, const std::map<std::string, const ast::Expr*>& bound)
    {
      Names binds;
      bound_in(written, binds);
      std::map<std::string, std::string, std::less<>> renamed;
      for (const auto& name : binds)
        renamed.emplace(name, fresh(name));
      if (written->body != nullptr)
        return put(written->body, bound, renamed);
      if (written->pieces.size() != 1) {
        // A template holds one expression, since an invocation stands where
        // one value does.
        diags.emit(D::LANG_MACRO_TEMPLATE_NOT_ONE, written->span,
                   { { "count", std::to_string(written->pieces.size()) } });
        return name_ref(written->span, "");
      }
      return put(written->pieces[0], bound, renamed);
    }


    std::string Expander::fresh(std::string_view name)
    {
      ++renames;
      return std::format("{}{}{}", name, hygiene_mark, renames);
    }


    const Node* Expander::put(const Node* node, const std::map<std::string, const ast::Expr*>& bound,
                              const std::map<std::string, std::string, std::less<>>& renamed)
    {
      if (auto hole = ast::as<ast::Hole>(node)) {
        auto found = bound.find(std::string(hole->name));
        // A hole the pattern does not have was reported where the rule is.
        return found == bound.end() ? node : found->second;
      }
      if (auto ref = ast::as<ast::NameRef>(node))
        if (auto to = renamed.find(ref->name); to != renamed.end()) {
          auto copy = arena.copy(*ref);
          copy->name = arena.text(to->second);
          return copy;
        }
      if (auto assign = ast::as<ast::HoleAssign>(node)) {
        // Now that the target is filled, which assignment this is can be said.
        const Node* target = put(assign->target, bound, renamed);
        const Node* value = put(assign->value, bound, renamed);
        const Node* found = fits<ast::Expr>(target)
          ? assignment(static_cast<const ast::Expr*>(target), value, assign->span) : nullptr;
        if (found == nullptr) {
          diags.emit(D::LANG_MACRO_HOLE_NOT_A_PLACE, assign->target->span);
          return arena.make<ast::EmptyStmt>(assign->span);
        }
        return found;
      }
      Rewrite rw{ .node = [&](const Node* one) { return put(one, bound, renamed); }, .list = {}, .always = true };
      const Node* copy = rebuild(arena, node, rw);
      if (auto def = ast::as<ast::VarDef>(node))
        if (auto to = renamed.find(def->name); to != renamed.end())
          mutable_node<ast::VarDef>(copy)->name = arena.text(to->second);
      if (auto stmt = ast::as<ast::AssignStmt>(node))
        if (auto to = renamed.find(stmt->name); to != renamed.end())
          mutable_node<ast::AssignStmt>(copy)->name = arena.text(to->second);
      return copy;
    }


    const Node* Expander::assignment(const ast::Expr* target, const Node* value, Span span)
    {
      const ast::Expr* given = fit<ast::Expr>(arena, value);
      if (auto ref = ast::as<ast::NameRef>(target)) {
        auto stmt = arena.make<ast::AssignStmt>(span);
        stmt->name = ref->name;
        stmt->name_span = ref->span;
        stmt->value = given;
        return stmt;
      }
      if (auto index = ast::as<ast::Index>(target)) {
        auto stmt = arena.make<ast::EntryAssign>(span);
        stmt->base = index->base;
        stmt->key = index->key;
        stmt->value = given;
        return stmt;
      }
      if (auto element = ast::as<ast::Element>(target)) {
        auto stmt = arena.make<ast::ElementAssign>(span);
        stmt->base = element->base;
        stmt->indices = element->indices;
        stmt->value = given;
        return stmt;
      }
      if (auto deref = ast::as<ast::Deref>(target)) {
        auto stmt = arena.make<ast::DerefAssign>(span);
        stmt->target = deref->operand;
        stmt->value = given;
        return stmt;
      }
      if (auto member = ast::as<ast::Member>(target)) {
        auto stmt = arena.make<ast::MemberAssign>(span);
        stmt->base = member->base;
        stmt->name = member->name;
        stmt->name_span = member->name_span;
        stmt->value = given;
        return stmt;
      }
      return nullptr;
    }


    const Node* Expander::with_names(const Node* node, const std::map<std::string, std::string, std::less<>>& wanted)
    {
      if (ast::is<ast::Hole>(node))
        return node;
      if (auto ref = ast::as<ast::NameRef>(node))
        if (auto to = wanted.find(ref->name); to != wanted.end()) {
          auto copy = arena.copy(*ref);
          copy->name = arena.text(to->second);
          return copy;
        }
      Rewrite rw{ .node = [&](const Node* one) { return with_names(one, wanted); }, .list = {}, .always = true };
      const Node* copy = rebuild(arena, node, rw);
      if (auto def = ast::as<ast::VarDef>(node))
        if (auto to = wanted.find(def->name); to != wanted.end())
          mutable_node<ast::VarDef>(copy)->name = arena.text(to->second);
      if (auto stmt = ast::as<ast::AssignStmt>(node))
        if (auto to = wanted.find(stmt->name); to != wanted.end())
          mutable_node<ast::AssignStmt>(copy)->name = arena.text(to->second);
      return copy;
    }


    const Node* Expander::fill_counting(const Node* node, const ast::Hole* at, const Node* put)
    {
      if (ast::is<ast::Hole>(node))
        return node == at ? put : node;
      if (auto assign = ast::as<ast::HoleAssign>(node)) {
        const Node* target = fill_counting(assign->target, at, put);
        const Node* value = fill_counting(assign->value, at, put);
        if (fits<ast::Expr>(target) && ! ast::is<ast::Hole>(target))
          if (auto found = assignment(static_cast<const ast::Expr*>(target), value, assign->span))
            return found;
        if (! fits<ast::Expr>(target)) [[unlikely]]
          throw InternalError("a macro filled the place an assignment writes with what is no expression");
        auto copy = arena.copy(*assign);
        copy->target = static_cast<const ast::Expr*>(target);
        copy->value = fit<ast::Expr>(arena, value);
        return copy;
      }
      Rewrite rw{ .node = [&](const Node* one) { return fill_counting(one, at, put); }, .list = {}, .always = true };
      return rebuild(arena, node, rw);
    }


    const Node* Expander::head_of(const Node* node)
    {
      if (auto binary = ast::as<ast::Binary>(node))
        return name_ref(node->span, describe(binary->op));
      if (auto unary = ast::as<ast::Unary>(node))
        return name_ref(node->span, describe(unary->op));
      if (auto fresh = ast::as<ast::Fresh>(node))
        return name_ref(node->span, fresh->glyph);
      if (auto call = ast::as<ast::Call>(node))
        return call->callee;
      if (auto literal = ast::as<ast::IntLit>(node))
        return name_ref(node->span, literal->type_name.value_or("int"));
      // A literal of any other kind is made by its type.
      if (ast::is<ast::StringLit>(node))
        return name_ref(node->span, "str");
      if (ast::is<ast::CharLit>(node))
        return name_ref(node->span, "char");
      if (ast::is<ast::BoolLit>(node))
        return name_ref(node->span, "bool");
      if (auto literal = ast::as<ast::FloatLit>(node))
        return name_ref(node->span, literal->type_name.value_or("float"));
      if (ast::is<ast::NameRef>(node))
        return node;
      return name_ref(node->span, class_name(node));
    }


    const Node* Expander::spread_out(const Node* node)
    {
      Rewrite rw;
      rw.node = [this](const Node* one) { return spread_out(one); };
      rw.list = [this](std::span<const Node* const> in) {
        std::vector<const Node*> out;
        for (auto one : in) {
          auto spread = ast::as<ast::Spread>(one);
          auto quote = spread == nullptr ? nullptr : ast::as<ast::Quote>(spread->operand);
          if (quote != nullptr && quote->body == nullptr)
            for (auto piece : quote->pieces)
              out.push_back(spread_out(piece));
          else
            out.push_back(spread_out(one));
        }
        return out;
      };
      const Node* found = rebuild(arena, node, rw);
      if (auto set = ast::as<ast::SetLit>(node)) {
        bool spread = std::ranges::any_of(set->elements, [](const ast::Expr* one) {
          auto s = ast::as<ast::Spread>(one);
          return s != nullptr && ast::is<ast::Quote>(s->operand);
        });
        if (spread) {
          auto copy = arena.copy(*static_cast<const ast::SetLit*>(found));
          copy->spread = true;
          return copy;
        }
      }
      return found;
    }


    std::optional<std::vector<const Node*>> Expander::handed_over(const ast::Quote* arguments,
                                                                 const ast::FuncDef* written)
    {
      if (arguments->body != nullptr)
        return std::nullopt;
      auto pieces = arguments->pieces;
      if (! has_several(written)) {
        if (pieces.size() != written->params.size())
          return std::nullopt;
        return std::vector<const Node*>(pieces.begin(), pieces.end());
      }
      std::size_t fixed = written->params.size() - 1;
      if (pieces.size() < fixed)
        return std::nullopt;
      std::vector<const Node*> given(pieces.begin(), pieces.begin() + std::ptrdiff_t(fixed));
      auto rest = arena.make<ast::Quote>(arguments->span);
      rest->pieces = arena.list(std::vector<const ast::Expr*>(pieces.begin() + std::ptrdiff_t(fixed), pieces.end()));
      given.push_back(rest);
      return given;
    }

  } // anonymous namespace


  std::vector<const ast::SourceUnit*> expand(ast::SyntaxArena& arena, std::span<const ast::SourceUnit* const> units,
                                             DiagEngine& diags, CheckMacros checked, ImportedUnit imported)
  {
    Expander expander(arena, units, diags, std::move(checked), std::move(imported));
    auto expanded = expander.run();
    // What a macro wrote may share nodes with the trees it quoted, which are
    // the macro checker's: those live as long as what was expanded.
    if (auto kept = expander.keeper())
      arena.hold<std::shared_ptr<void>>(std::move(kept));
    return expanded;
  }


  bool only_for_macros(const ast::Node* item)
  {
    return ast::is<ast::MacroDef>(item) || only_for_the_macros(item);
  }

} // namespace pl4g
