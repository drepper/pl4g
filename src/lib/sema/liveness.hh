// Which names are still to be used, at each statement of a function.
//
// A port of pypl4g's sema/liveness.py.  A name is *live* at a statement where
// some way on from there uses it; a reference a name holds is out until the
// name's last use, not until its scope ends.  Worked backwards over the tree --
// a block is its statements in reverse, an `if` and a `match` join their arms,
// a loop is worked to a fixed point -- and then forwards, over the same
// answers, for the *values* a name holds: the binding and the statement that
// gave it.
//
// Representation: a binding is where it is bound (`Loc`, the start of its
// span), as pypl4g has it.  A value is that and the identity of the statement
// that gave it, `identity(stmt)` -- the node's address as an integer, `0` for
// a parameter -- so that the checker can make the same pairs and negate the
// identity as pypl4g does with `id()`.  Sets are sorted vectors, which keeps
// every walk over one in one order.  The answers are by the node itself.
#pragma once

#include <algorithm>
#include <iterator>
#include <cstdint>
#include <initializer_list>
#include <map>
#include <optional>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include "front/ast.hh"
#include "source/location.hh"


namespace pl4g::sema {

  // A set kept as a sorted vector: what pypl4g's frozensets are here.
  template<typename T>
  class FlatSet {
  public:
    FlatSet() = default;
    FlatSet(std::initializer_list<T> items) : items(items) { settle(); }
    explicit FlatSet(std::vector<T> items) : items(std::move(items)) { settle(); }

    bool empty() const noexcept { return items.empty(); }
    std::size_t size() const noexcept { return items.size(); }
    auto begin() const noexcept { return items.begin(); }
    auto end() const noexcept { return items.end(); }
    bool contains(const T& one) const { return std::ranges::binary_search(items, one); }

    void insert(const T& one)
    {
      auto at = std::ranges::lower_bound(items, one);
      if (at == items.end() || *at != one)
        items.insert(at, one);
    }

    FlatSet& operator|=(const FlatSet& other)
    {
      if (! other.empty()) {
        std::vector<T> both;
        both.reserve(items.size() + other.items.size());
        std::ranges::set_union(items, other.items, std::back_inserter(both));
        items = std::move(both);
      }
      return *this;
    }

    friend FlatSet operator|(FlatSet a, const FlatSet& b) { return a |= b; }

    friend FlatSet operator-(const FlatSet& a, const FlatSet& b)
    {
      FlatSet out;
      std::ranges::set_difference(a.items, b.items, std::back_inserter(out.items));
      return out;
    }

    // Whether every member is one of `other`'s.
    bool subset_of(const FlatSet& other) const { return std::ranges::includes(other.items, items); }

    // The members `keep` says yes to.
    template<typename F>
    FlatSet filter(F&& keep) const
    {
      FlatSet out;
      for (const auto& one : items)
        if (keep(one))
          out.items.push_back(one);
      return out;
    }

    bool operator==(const FlatSet&) const = default;

  private:
    void settle()
    {
      std::ranges::sort(items);
      auto [first, last] = std::ranges::unique(items);
      items.erase(first, last);
    }

    std::vector<T> items;
  };


  // The identity of a node as pypl4g's `id()` gives it: an integer, for the
  // values below; `0` for none.
  inline std::intptr_t identity(const ast::Node* node) noexcept { return reinterpret_cast<std::intptr_t>(node); }

  // A binding: where it is bound.
  using Binding = Loc;
  // A value a name holds: the binding, and the identity of the statement that
  // gave it -- `0` for a parameter, which the call gave.  pypl4g's `Value`.
  using LiveValue = std::pair<Loc, std::intptr_t>;

  using Bindings = FlatSet<Binding>;
  using LiveValues = FlatSet<LiveValue>;


  // The liveness of every binding of the functions handed to it: pypl4g's
  // `Liveness`.
  class Liveness {
  public:
    // The bindings live as a statement begins and once it is done.
    std::unordered_map<const ast::Node*, Bindings> live_in;
    std::unordered_map<const ast::Node*, Bindings> live_out;
    // The values that reach a statement.
    std::unordered_map<const ast::Node*, LiveValues> reach_in;
    // The values that stop counting as a statement begins, and why: "unused"
    // or "given".
    std::unordered_map<const ast::Node*, std::map<LiveValue, std::string_view>> ends;
    // The values a statement gives another value to after using them.
    std::unordered_map<const ast::Node*, LiveValues> ends_after;
    // Those that stop counting where a `return`, `break` or `continue` leaves.
    std::unordered_map<const ast::Node*, LiveValues> leave_ends;
    // Those bound in a block still counted where it ends, by the block.
    std::unordered_map<const ast::Node*, LiveValues> exit_ends;

    // Work out the liveness of `node`'s bindings.
    void function(const ast::FuncDef* node);
    // And of a lambda's, whose body is a function of its own.
    void lambda_(const ast::Lambda* node);

  private:
    struct Loop {
      std::optional<std::string_view> label;
      Bindings after;
      Bindings head;
    };

    using Way = std::pair<LiveValues, LiveValues>;

    template<typename P>
    void start(const P* node);
    std::optional<Binding> bind(std::string_view name, Span at);
    std::optional<Binding> find(std::string_view name) const;
    void use(const void* key, std::string_view name);
    void resolve(const ast::Node* node, bool scoped = true);
    void resolve_free(const ast::Node* node);
    Bindings used(const ast::Node* node);
    void gather(const ast::Node* node, std::vector<Binding>& into, bool deep);
    void gather_all(const ast::Node* node, std::vector<Binding>& into);
    void known(const void* key, std::vector<Binding>& into) const;
    const Bindings& defs(const ast::Node* node) const;

    Bindings block(const ast::Block* block, const Bindings& out);
    Bindings stmt(const ast::Stmt* stmt, const Bindings& out);
    Bindings expr(const ast::Node* expr, const Bindings& out);
    void nested(const ast::Node* node, const Bindings& out);
    Bindings held(const ast::Expr* iterable) const;
    Bindings loop(const ast::Node* node, const ast::Block* body, const ast::Block* alternative,
                  const ast::Label* label, const Bindings& out, const Bindings& asked, const Bindings& bound);
    Bindings leaving() const;
    const Loop* loop_for(const ast::Label* label) const;

    Way forward(const ast::Block* block, LiveValues reach, LiveValues active);
    Way enter(const ast::Stmt* stmt, LiveValues reach, const LiveValues& active);
    Way gives(const ast::Stmt* stmt, LiveValues reach, LiveValues active);
    Way through(const ast::Node* node, LiveValues reach, LiveValues active);
    Way loop_forward(const ast::Node* node, LiveValues reach, LiveValues active);

    // Whether the forward walk writes down what it finds: only the last walk
    // of a loop does.
    bool noting = true;
    // The bindings each loop being walked forward binds, innermost last.
    std::vector<Bindings> turns;
    // What each name written resolves to, by the name's identity: a node, or
    // a name written with where it was.
    std::unordered_map<const void*, Binding> refs;
    // What each statement or turn binds, by the node.
    std::unordered_map<const ast::Node*, Bindings> defs_of;
    std::unordered_map<const ast::Node*, Bindings> uses;
    std::vector<std::unordered_map<std::string_view, Binding>> scopes;
    std::vector<Loop> loops;
    // What the deferred statements of each block being walked use.
    std::vector<Bindings> deferred;
  };

} // namespace pl4g::sema
