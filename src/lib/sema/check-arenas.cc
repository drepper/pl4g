// The checker, from what an arena holds to the start of the arrays: where what
// a name holds was made and what may still be read of it, how a function
// answers in the allocator its signature names, what a block put off, the
// lowering of bodies, blocks and statements, tuples, and arrays written down,
// laid out and indexed.  A port of pypl4g's sema/check.py, lines 9482 to 12195.
#include "sema/check.hh"

#include <algorithm>
#include <exception>
#include <format>
#include <ranges>
#include <utility>

#include "diag/ids.hh"
#include "front/names.hh"
#include "ir/printer.hh"
#include "sema/lastuse.hh"
#include "sema/owned.hh"
#include "target/statuses.hh"


namespace pl4g::sema {

  namespace {

    // Run `cleanup` when the scope is left, however it is left: Python's
    // `finally`.  A cleanup failing while something else is already being
    // thrown is the first failure's to report.
    template<typename F>
    class Finally {
    public:
      explicit Finally(F cleanup) : cleanup(std::move(cleanup)), unwinding(std::uncaught_exceptions()) {}
      Finally(const Finally&) = delete;
      Finally& operator=(const Finally&) = delete;
      ~Finally() noexcept(false)
      {
        if (std::uncaught_exceptions() > unwinding) {
          try {
            cleanup();
          } catch (...) {
          }
        } else
          cleanup();
      }

    private:
      F cleanup;
      int unwinding;
    };

    // A value standing for nothing, of type `ty`: Python's `UndefConst(ty)`.
    ir::Value* undef(ir::Module& module, const ir::Type* ty)
    {
      return const_cast<ir::UndefConst*>(module.undef(ty));
    }

    // `a | b`, `a - b` and `a & b` of two sets of keys, and `a <= b`.
    KeySet unite(KeySet a, const KeySet& b)
    {
      a.insert(b.begin(), b.end());
      return a;
    }

    KeySet without(const KeySet& a, const KeySet& b)
    {
      KeySet found;
      std::ranges::set_difference(a, b, std::inserter(found, found.end()));
      return found;
    }

    KeySet common(const KeySet& a, const KeySet& b)
    {
      KeySet found;
      std::ranges::set_intersection(a, b, std::inserter(found, found.end()));
      return found;
    }

    bool within_keys(const KeySet& a, const KeySet& b)
    {
      return std::ranges::includes(b, a);
    }

    // Whether `keys` is `{one}`.
    bool only(const KeySet& keys, std::int64_t one)
    {
      return keys.size() == 1 && *keys.begin() == one;
    }

    // `sep.join(parts)`.
    std::string joined_with(const auto& parts, std::string_view sep)
    {
      std::string text;
      bool first = true;
      for (const auto& one : parts) {
        if (! first)
          text += sep;
        text += one;
        first = false;
      }
      return text;
    }

    // Hand `f` a node a field holds, or each node of a sequence a field holds
    // -- and nothing of a pair inside one, which is a tuple and no node.
    template<typename F>
    void field_nodes(const auto& held, F& f)
    {
      using H = std::remove_cvref_t<decltype(held)>;
      if constexpr (std::is_pointer_v<H>) {
        if constexpr (std::is_base_of_v<ast::Node, std::remove_cv_t<std::remove_pointer_t<H>>>)
          if (held != nullptr)
            f(static_cast<const ast::Node*>(held));
      } else if constexpr (requires { held.begin(); held.end(); } && ! std::is_same_v<H, std::string_view>)
        for (const auto& each : held)
          if constexpr (std::is_pointer_v<std::remove_cvref_t<decltype(each)>>)
            field_nodes(each, f);
    }

    template<typename T, typename F>
    void fields_of(const T& node, F& f)
    {
      constexpr auto ctx = std::meta::access_context::unchecked();
      template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, ctx)))
        fields_of(static_cast<const [:std::meta::type_of(b):]&>(node), f);
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, ctx)))
        if constexpr (std::meta::annotations_of_with_type(m, ^^ast::Hidden).size() == 0)
          field_nodes(node.[:m:], f);
    }

    // Hand `f` every node a field of `node` holds, a field at a time, as a
    // walk over `dataclasses.fields` does.  A block's field is its statements.
    template<typename F>
    void each_field_node(const ast::Node* node, F&& f)
    {
      if (node == nullptr)
        return;
      if (auto block = ast::as<ast::Block>(node)) {
        for (auto stmt : block->stmts())
          f(static_cast<const ast::Node*>(stmt));
        return;
      }
      ast::visit(*node, [&](const auto& n) { fields_of(n, f); });
    }

    // Where a definition's name is, or the node's own span where it has none.
    Span name_span_of(const ast::Node* node)
    {
      if (auto def = ast::as<ast::FuncDef>(node))
        return def->name_span;
      return node->span;
    }

    // `func.made_in == (at,)`.
    bool made_in_only(const ir::Function* func, std::int64_t at)
    {
      return func->made_in && func->made_in->size() == 1 && (*func->made_in)[0] == at;
    }

    // `FuncType.borrowed()`: the borrows as a table.
    Dict<std::int64_t, ir::Borrowed> borrowed_of(const ir::FuncType* ty)
    {
      Dict<std::int64_t, ir::Borrowed> table;
      for (const auto& [leaf, one] : ty->borrows)
        table[leaf] = one;
      return table;
    }

    // `stack[:depth]` in place, the rest handed back to be put back by
    // `put_back_tail`: what slicing a list of scopes and putting the list back
    // comes to, the scopes kept being the same ones.
    template<typename T>
    std::vector<T> cut_to(std::vector<T>& stack, std::size_t depth)
    {
      std::vector<T> tail;
      if (depth < stack.size()) {
        tail.assign(std::make_move_iterator(stack.begin() + depth), std::make_move_iterator(stack.end()));
        stack.resize(depth);
      }
      return tail;
    }

    template<typename T>
    void put_back_tail(std::vector<T>& stack, std::size_t depth, std::vector<T>& tail)
    {
      if (stack.size() > depth)
        stack.resize(depth);
      for (auto& one : tail)
        stack.push_back(std::move(one));
    }

  } // namespace


  // -- what an arena holds ----------------------------------------------------

  bool Checker::is_arena(Local* local)
  {
    const ir::Type* held = held_by(local);
    auto ptr = ir::as<ir::PtrType>(held);
    bool found = held == ir::arena_type() || (ptr != nullptr && ptr->pointee == ir::arena_type());
    if (found)
      arena_by_id[id_of(local)] = local;
    return found;
  }


  KeySet Checker::arenas_in(const ast::Node* node)
  {
    KeySet found;
    collect_arenas(node, found);
    return found;
  }


  void Checker::collect_arenas(const ast::Node* node, KeySet& into)
  {
    if (node == nullptr)
      return;
    if (auto exact = made_from.get(id_of(node)); exact != nullptr
        && static_cast<const ast::Node*>(std::get<0>(*exact)) == node) {
      into.insert(std::get<1>(*exact).begin(), std::get<1>(*exact).end());
      return;
    }
    if (auto alias = made_alias.get(id_of(node)); alias != nullptr
        && static_cast<const ast::Node*>(std::get<0>(*alias)) == node) {
      collect_arenas(std::get<1>(*alias), into);
      return;
    }
    switch (node->tag) {
    case ast::NodeKind::Allocated: {
      // The arena named, and what the operands were made in: the room the
      // expression itself takes is the arena's and not `⎕heap`'s.
      auto allocated = static_cast<const ast::Allocated*>(node);
      collect_arenas(allocated->arena, into);
      if (auto exact = made_from.get(id_of(allocated->value)); exact != nullptr
          && std::get<0>(*exact) == allocated->value) {
        into.insert(std::get<1>(*exact).begin(), std::get<1>(*exact).end());
        return;
      }
      each_field_node(allocated->value, [&](const ast::Node* each) { collect_arenas(each, into); });
      return;
    }
    case ast::NodeKind::Binary:
      // Not lowered yet -- a body read ahead of time -- so what it will be made
      // in is what one naming no arena is made in.
      if (static_cast<const ast::Binary*>(node)->op == ast::BinaryOp::concat)
        into.insert(heap_key);
      break;
    case ast::NodeKind::ListLit:
      into.insert(heap_key);
      break;
    case ast::NodeKind::JoinAssign: {
      // A list written out and joined onto a pool's list is never made: its
      // elements go in as they are, with what they were made in, and the list
      // joined onto keeps what it had.
      auto join = static_cast<const ast::JoinAssign*>(node);
      if (auto list = ast::as<ast::ListLit>(join->value); list != nullptr && pool_joined_onto(join->target)) {
        collect_arenas(join->target, into);
        for (auto one : list->elements)
          collect_arenas(one, into);
        return;
      }
      break;
    }
    case ast::NodeKind::SetLit:
      // Made in the arena it names, which the walk below finds.
      if (static_cast<const ast::SetLit*>(node)->arena == nullptr)
        into.insert(heap_key);
      break;
    case ast::NodeKind::DictLit:
      if (static_cast<const ast::DictLit*>(node)->arena == nullptr)
        into.insert(heap_key);
      break;
    case ast::NodeKind::Element: {
      // Where the container's elements were made, without the keys that name
      // the container -- an element is not the container -- and, if any may be
      // the heap's, a key saying it was read out of this one.
      auto element = static_cast<const ast::Element*>(node);
      KeySet inner;
      collect_arenas(element->base, inner);
      KeySet keys;
      for (auto one : inner)
        if (is_container_key(one))
          keys.insert(one);
      if (! a_view_of(element))
        // One element is a value, and not where the array is.
        keys = unite(std::move(keys), common(inner, array_frames));
      auto rest = without(inner, keys);
      into.insert(rest.begin(), rest.end());
      if (inner.contains(heap_key))
        for (auto one : keys)
          if (is_container_key(one))
            into.insert(element_key(one));
      return;
    }
    case ast::NodeKind::NameRef: {
      auto ref = static_cast<const ast::NameRef*>(node);
      Local* local = find_local(ref->name);
      if (local == nullptr) {
        if (ref->name == names::heap_name)
          into.insert(heap_key);
        return;
      }
      if (is_arena(local))
        into.insert(id_of(local));
      else if (! local->gone_at) {
        // A name already dead is refused where it is read (4615), and that is
        // the one thing to say about it.
        into.insert(local->arenas.begin(), local->arenas.end());
        auto frame = array_frame_of(local);
        into.insert(frame.begin(), frame.end());
      }
      return;
    }
    case ast::NodeKind::Lambda: {
      // Read ahead of being lowered, it is taken to keep what it brings in in
      // `⎕heap` and to reach what the names it brings in were made in.
      auto lambda = static_cast<const ast::Lambda*>(node);
      into.insert(heap_key);
      std::vector<std::string> named;
      for (auto one : lambda->captures)
        named.emplace_back(one->name);
      if (lambda->brings_in)
        named_in(lambda->body, named);
      for (const auto& one : named) {
        Local* local = find_local(one);
        if (local != nullptr && ! is_arena(local) && ! local->gone_at)
          into.insert(local->arenas.begin(), local->arenas.end());
      }
      return;
    }
    default:
      break;
    }
    each_field_node(node, [&](const ast::Node* each) { collect_arenas(each, into); });
  }


  KeySet Checker::array_frame_of(Local* local)
  {
    if (! ir::is<ir::ArrayType>(held_by(local)) || is_arena(local))
      return {};
    if (local->is_parameter) {
      unknown_by_id.setdefault(id_of(local), local);
      array_frames.insert(id_of(local));
      return { id_of(local) };
    }
    if (! made_here(local->value))
      // Not this call's room: an array `in ⎕static` is in the image.
      return {};
    auto key = storage_key(local);
    array_frames.insert(key);
    return { key };
  }


  bool Checker::a_view_of(const ast::Element* node)
  {
    if (std::ranges::any_of(node->indices, [](const ast::Expr* one) { return ast::is<ast::Range>(one); }))
      return true;
    const ir::Type* held = said_to_be(node->base);
    auto array = ir::as<ir::ArrayType>(held);
    if (array == nullptr)
      return held == nullptr;
    return node->indices.size() < array->rank();
  }


  void Checker::made_in(std::string_view name, const ast::Expr* value, Span span, bool assigning)
  {
    Local* local = find_local(name);
    if (local == nullptr || value == nullptr)
      return;
    if (! points_somewhere(held_by(local))) {
      local->arenas.clear();
      return;
    }
    KeySet made = arenas_in(value);
    const ir::Type* held = held_by(local);
    if (auto array = ir::as<ir::ArrayType>(held); array != nullptr && (array->fixed() || assigning))
      // Giving an array to an array copies the elements: into the name's own
      // room, or into the elements a view names, which are where they were.
      made = unite(without(made, array_frames), assigning ? common(local->arenas, array_frames) : KeySet{});
    if (is_owning(held)) {
      // A container whose elements point somewhere: which allocator they are
      // kept in, and -- for `⎕heap`'s, which gives back what is replaced -- a
      // key naming the container, unless it is another name for one that
      // already has one.
      local->holds_in = holder_of(value);
      if (local->holds_in == heap_key && std::ranges::none_of(made, is_container_key))
        made.insert(container_key(local));
    }
    local->arenas = made;
    local->gone_at.reset();
    report_allocator(local, made, span);
    if (! assigning)
      return;
    for (auto one : made) {
      Local* const* arena = arena_by_id.get(one);
      if (arena != nullptr && (*arena)->depth > local->depth) {
        outlives_arena(name, *arena, span);
        // Said once: what the name holds is not also reported as gone every
        // time it is read after the arena is.
        local->arenas.clear();
        return;
      }
    }
  }


  void Checker::outlives_arena(std::string_view name, Local* arena, Span span)
  {
    if (stand_ins.contains(id_of(arena))) [[unlikely]] {
      diags.emit(D::LANG_REF_OUTLIVES_PLACE, span, { { "name", name }, { "place", arena->name } })
        .note(D::LANG_VARDEF_DEFINED_HERE, arena->span, { { "name", arena->name } });
      return;
    }
    diags.emit(D::LANG_ARENA_VALUE_OUTLIVES, span, { { "name", name }, { "arena", arena->name } });
  }


  std::optional<std::int64_t> Checker::holder_of(const ast::Expr* value)
  {
    switch (value->tag) {
    case ast::NodeKind::Allocated:
      return owner_of(arenas_in(static_cast<const ast::Allocated*>(value)->arena));
    case ast::NodeKind::ArrayLit:
    case ast::NodeKind::ListLit:
      return heap_key;
    case ast::NodeKind::Binary:
      if (static_cast<const ast::Binary*>(value)->op == ast::BinaryOp::concat)
        return heap_key;
      return std::nullopt;
    case ast::NodeKind::NameRef: {
      Local* local = find_local(static_cast<const ast::NameRef*>(value)->name);
      return local != nullptr ? local->holds_in : std::nullopt;
    }
    case ast::NodeKind::Call:
      if (auto found = holds_from.get(id_of(value)))
        return *found;
      return std::nullopt;
    case ast::NodeKind::Member:
      // A container reached through a field says nothing of where its
      // elements are, and saying nothing is `⎕heap`.
      return heap_key;
    default:
      return std::nullopt;
    }
  }


  void Checker::report_allocator(Local* local, const KeySet& made, Span span)
  {
    ir::Function* func = std::get<1>(demanding);
    if (func == nullptr || ! owned::points(held_by(local)))
      return;
    Span where = local->span.is_valid() ? local->span : span;
    if (local->holds_in && ir::is<ir::ArrayType>(held_by(local))) {
      // An array is room in the frame and carries no allocator of its own;
      // what it says is where its elements are kept.
      bool pool = *local->holds_in != heap_key;
      module.record("allocator", func->name,
                    "'" + local->name + "' keeps its elements in " + owner_written(*local->holds_in)
                      + (pool ? ", a pool: nothing is given back when one is replaced"
                              : ", which gives back what is replaced: each element carries the allocator it came "
                                "from and is asked"),
                    where);
      return;
    }
    std::vector<std::int64_t> allocators;
    for (auto one : made)
      if (one == heap_key || (arena_by_id.contains(one) && ! stand_ins.contains(one)))
        allocators.push_back(one);
    bool unknown = std::ranges::any_of(made, [this](std::int64_t one) {
      return unknown_by_id.contains(one) || is_element_key(one);
    });
    std::string said;
    if (made.empty())
      said = "is in the image, which nothing gives back: its allocator is none";
    else if (allocators.size() == 1 && ! unknown)
      said = "is made in " + owner_written(allocators[0])
             + ", known while compiling: the compiler holds the allocator, and the value need not carry it";
    else {
      std::vector<std::string> places;
      for (auto one : allocators)
        places.push_back(owner_written(one));
      if (std::ranges::any_of(made, [this](std::int64_t one) { return unknown_by_id.contains(one); }))
        places.emplace_back("whatever the caller handed over");
      if (std::ranges::any_of(made, is_element_key))
        places.emplace_back("a container it was read out of");
      said = "carries its allocator, which is asked of it where it is given back: it may be made in "
             + joined_with(places, " or ");
    }
    module.record("allocator", func->name, "'" + local->name + "' " + said, where);
  }


  void Checker::leaves_no_arena_behind(const ast::Expr* value, std::int64_t opened_at, ir::Function*, Span span,
                                       const ir::Type* ty)
  {
    if (ty != nullptr && ! points_somewhere(ty))
      return;
    for (auto one : arenas_in(value)) {
      Local* const* arena = arena_by_id.get(one);
      if (arena == nullptr || (*arena)->is_parameter)
        continue;
      if ((*arena)->depth >= opened_at) {
        outlives_arena("what the block comes to", *arena, span);
        return;
      }
    }
  }


  void Checker::given_back(Local* arena, Span span)
  {
    if (! emptied.empty())
      emptied.back().insert(id_of(arena));
    for (auto& scope : scopes)
      for (auto& [_, local] : scope)
        if (local->arenas.contains(id_of(arena)) && ! local->gone_at) {
          local->gone_at = span;
          local->gone_with = arena->name;
        }
  }


  Provenance Checker::provenance()
  {
    Provenance found;
    for (auto& scope : scopes)
      for (auto& [_, local] : scope)
        found[id_of(local)] = NameState{ local,         local->arenas,  local->gone_at,  local->gone_with,
                                         local->owes,   local->ended_at, local->ended_how };
    return found;
  }


  void Checker::put_back(const Provenance& state)
  {
    for (const auto& [_, entry] : state) {
      Local* local = entry.local;
      local->arenas = entry.arenas;
      local->gone_at = entry.gone_at;
      local->gone_with = entry.gone_with;
      local->owes = entry.owes;
      local->ended_at = entry.ended_at;
      local->ended_how = entry.ended_how;
    }
  }


  Provenance Checker::joined(const Provenance& before, std::span<const Provenance> ways)
  {
    Provenance found;
    for (const auto& [key, entry] : before) {
      KeySet made;
      std::optional<Span> dead_at;
      std::string dead_with;
      // A variable that must be closed owes it where it owes it along any way,
      // and its lifetime has ended where it ended along any.
      bool owes = false;
      std::optional<Span> ended_at;
      std::string ended_how;
      for (const auto& way : ways) {
        const NameState* there = way.get(key);
        if (there == nullptr)
          there = &entry;
        made.insert(there->arenas.begin(), there->arenas.end());
        if (there->gone_at && ! dead_at) {
          dead_at = there->gone_at;
          dead_with = there->gone_with;
        }
        owes = owes || there->owes;
        if (there->ended_at && ! ended_at) {
          ended_at = there->ended_at;
          ended_how = there->ended_how;
        }
      }
      found[key] = NameState{ entry.local, std::move(made), dead_at, std::move(dead_with), owes, ended_at,
                              std::move(ended_how) };
    }
    return found;
  }


  std::tuple<Provenance, std::vector<KeySet>> Checker::on_the_way_out()
  {
    return { provenance(), emptied };
  }


  void Checker::left(const std::tuple<Provenance, std::vector<KeySet>>& kept)
  {
    put_back(std::get<0>(kept));
    const auto& was = std::get<1>(kept);
    for (std::size_t at = 0; at < emptied.size() && at < was.size(); ++at)
      emptied[at] = was[at];
  }


  void Checker::taken_out_of(const ast::Expr* whole, std::span<const std::string> names)
  {
    KeySet made = arenas_in(whole);
    if (made.empty())
      return;
    for (const auto& name : names) {
      Local* local = find_local(name);
      if (local != nullptr && ! is_arena(local) && points_somewhere(held_by(local)))
        local->arenas = unite(local->arenas, made);
    }
  }


  Provenance Checker::a_turn_begins(const ast::Block* body)
  {
    NameSet assigned;
    NameSet parts;
    collect_written(body, assigned, parts);
    if (! assigned.empty() || ! parts.empty()) {
      KeySet anything = arenas_in(body);
      NameSet all = assigned;
      all.insert(parts.begin(), parts.end());
      for (const auto& name : all) {
        Local* local = find_local(name);
        if (local == nullptr || is_arena(local) || ! points_somewhere(held_by(local)))
          continue;
        // An array whose elements the body writes may, at the top of a turn,
        // hold what a later line of the body put in it -- but nothing that
        // holds no view holds where an array's elements are.
        if (may_hold_a_view(held_by(local)))
          local->arenas = unite(local->arenas, anything);
        else {
          KeySet reach;
          for (auto one : anything)
            if (! array_frames.contains(one))
              reach.insert(one);
          local->arenas = unite(local->arenas, reach);
        }
      }
    }
    before_a_turn(body);
    return provenance();
  }


  void Checker::after_the_turns(const Provenance& head, const std::optional<Provenance>& end, Loop* loop,
                                bool goes_round_, bool test_leaves)
  {
    std::vector<Provenance> ways{ head };
    if (end) {
      ways.push_back(*end);
      if (goes_round_)
        goes_round(head, std::nullopt, end);
    }
    if (! test_leaves)
      // `while true`: the test never ends it, so only a `break` does.
      ways.clear();
    if (loop != nullptr)
      ways.insert(ways.end(), loop->left_with.begin(), loop->left_with.end());
    if (ways.empty())
      ways.push_back(head);
    put_back(joined(head, ways));
  }


  void Checker::goes_round(const Provenance& head, std::optional<Span> span, const std::optional<Provenance>& end)
  {
    Provenance now_held;
    const Provenance& now = end ? *end : (now_held = provenance());
    for (const auto& [key, entry] : head) {
      Local* local = entry.local;
      if (local->closer.empty() || entry.ended_at)
        continue;
      const NameState* there = now.get(key);
      if (there == nullptr || ! there->ended_at)
        continue;
      if (told_unclosed.contains(id_of(local)))
        continue;
      told_unclosed.insert(id_of(local));
      diags.emit(D::LANG_CLOSABLE_MOVED_IN_LOOP, span ? *span : *there->ended_at, { { "name", local->name } })
        .note(D::LANG_CLOSABLE_ENDED_HERE, *there->ended_at,
              { { "name", local->name }, { "how", "its lifetime ended here: " + there->ended_how } });
    }
  }


  void Checker::made_in_part(const ast::Expr* base, const ast::Expr* value, Span span, bool element)
  {
    const ast::Expr* root = base;
    while (true) {
      if (auto member = ast::as<ast::Member>(root))
        root = member->base;
      else if (auto elem = ast::as<ast::Element>(root))
        root = elem->base;
      else if (auto index = ast::as<ast::Index>(root))
        root = index->base;
      else
        break;
    }
    auto ref = ast::as<ast::NameRef>(root);
    if (ref == nullptr)
      return;
    Local* local = find_local(ref->name);
    if (local == nullptr || is_arena(local) || ! points_somewhere(held_by(local)))
      return;
    if (is_owning(held_by(local)))
      // An element of an array was copied into the array's allocator on the
      // way in, so what the array was made in is what it was.
      return;
    KeySet made = arenas_in(value);
    if (auto given = ir::as<ir::ArrayType>(said_to_be(value)); given != nullptr && (given->fixed() || element))
      // Copied: a fixed array into the part's own room, and anything given to
      // an element of an array into the element's.  A view given to a field is
      // the view.
      made = without(made, array_frames);
    for (auto one : made) {
      Local* const* arena = arena_by_id.get(one);
      if (arena != nullptr && (*arena)->depth > local->depth) {
        outlives_arena(ref->name, *arena, span);
        return;
      }
    }
    local->arenas = unite(local->arenas, made);
  }


  std::optional<std::int64_t> Checker::owner_of(const KeySet& made)
  {
    if (made.size() != 1)
      return std::nullopt;
    std::int64_t one = *made.begin();
    if (one == heap_key)
      return one;
    Local* const* arena = arena_by_id.get(one);
    if (arena == nullptr || find_local((*arena)->name) != *arena)
      return std::nullopt;
    return one;
  }


  ir::Value* Checker::owner_place(ir::Builder& builder, std::int64_t owner, Span span)
  {
    if (owner == heap_key) {
      auto heap = provided(names::heap_name).get<ir::GlobalVar>();
      if (heap == nullptr) [[unlikely]]
        throw InternalError("the heap is no variable");
      return builder.address(heap, span);
    }
    return arena_by_id[owner]->value;
  }


  std::string Checker::owner_written(std::int64_t owner)
  {
    if (owner == heap_key)
      return std::string(names::heap_name);
    Local* const* arena = arena_by_id.get(owner);
    return "'" + (arena != nullptr ? (*arena)->name : std::string("?")) + "'";
  }


  bool Checker::lasts(std::int64_t key, std::int64_t holder, std::int64_t depth, bool local)
  {
    if (key == holder || key == image_key)
      return true;
    Local* const* arena = arena_by_id.get(key);
    if (arena == nullptr)
      return false;
    return ((*arena)->is_parameter && local) || (*arena)->depth < depth;
  }


  std::tuple<ir::Value*, KeySet, ir::Value*> Checker::stored(ir::Builder& builder, const ast::Expr* written,
                                                            ir::Value* value, const ir::Type* ty, std::int64_t holder,
                                                            std::int64_t depth, bool local, Span span)
  {
    if (! owned::points(ty))
      return { value, {}, nullptr };
    if (is_owning(ty) && ! is_temporary(written)) [[unlikely]] {
      // An array of text is its elements, so putting one in by value would
      // make two owners of each: only one nobody else names may go in.
      diags.emit(D::LANG_CONTAINER_CANNOT_COPY, written->span, { { "type", ty->written() } });
      return { undef(module, ir::error_type()), {}, nullptr };
    }
    KeySet made = arenas_in(written);
    if (made.empty())
      return { value, made, nullptr };
    bool pool = holder != heap_key;
    bool fits;
    if (pool)
      fits = std::ranges::all_of(made, [&](std::int64_t one) {
        return lasts(one, holder, depth, local) || one == heap_key || is_element_key(one) || is_container_key(one)
               || (unknown_by_id.contains(one) && local);
      });
    else {
      if (only(made, heap_key) && is_temporary(written))
        return { value, made, nullptr };
      // The heap is the holder here, and what else holds the heap's would give
      // it back too, or read it after this one had: it is copied.
      fits = std::ranges::all_of(made,
                                 [&](std::int64_t one) { return one != heap_key && lasts(one, holder, depth, local); });
    }
    if (fits)
      return { value, made, nullptr };
    if (! owned::can_own(ty)) [[unlikely]] {
      diags.emit(D::LANG_CONTAINER_CANNOT_COPY, written->span, { { "type", ty->written() } });
      return { undef(module, ir::error_type()), {}, nullptr };
    }
    ir::Function* own = owned::own_function(module, ty);
    ir::Value* into = owner_place(builder, holder, span);
    ir::Value* copied = builder.call(own, std::vector<ir::Value*>{ into, value }, ty, span);
    ir::Function* func = std::get<1>(demanding);
    module.record("copy-into-allocator", func != nullptr ? func->name : std::string(),
                  "what is put in is copied into " + owner_written(holder)
                    + ", its allocator not being provably the container's or long enough lived",
                  written->span);
    ir::Value* spare = only(made, heap_key) && is_temporary(written) ? value : nullptr;
    return { copied, { holder }, spare };
  }


  void Checker::elements_replaced(const KeySet& keys, std::string_view name, Span span)
  {
    KeySet wanted;
    for (auto one : keys)
      if (is_container_key(one))
        wanted.insert(element_key(one));
    if (wanted.empty())
      return;
    for (auto& scope : scopes)
      for (auto& [_, local] : scope)
        if (! common(wanted, local->arenas).empty() && ! local->gone_at) {
          local->gone_at = span;
          local->gone_with = std::string(replaced) + std::string(name);
        }
  }


  ir::Value* Checker::answered(ir::Builder& builder, const ast::Expr* written, ir::Value* value, ir::Function* func,
                               Span span)
  {
    const ir::Type* ty = func->fty->ret;
    if (auto answering_node = node_of_func.get(id_of(func)); answering_node != nullptr)
      flow(slot_key("answer", id_of(*answering_node)), written);
    if (! answer_made_in || value_type_of(value) == ir::error_type())
      return value;
    const KeySet allowed = std::get<0>(*answer_made_in);
    const std::string named = std::get<1>(*answer_made_in);
    if (holds_a_lambda(ty) && ! lambdas_last(ty)
        && ! lambda_may_leave(written, unite(allowed, { heap_key }), func->name, span))
      return undef(module, ir::error_type());
    if (! owned::points(ty))
      return value;
    KeySet made = arenas_in(written);
    std::optional<std::int64_t> thin = thin_to;
    if (made_in_only(func, static_at)) {
      // Nothing is copied into the image, which is made while compiling.
      if (! made.empty()) [[unlikely]] {
        NameSet found;
        for (auto one : made)
          found.insert(reach_written(one));
        diags.emit(D::LANG_ANSWER_NOT_STATIC, span, { { "name", func->name }, { "found", joined_with(found, ", ") } });
        return undef(module, ir::error_type());
      }
      answer_kinds.emplace_back("static");
      return value;
    }
    if (func->made_in && std::ranges::contains(*func->made_in, static_at)) {
      // The image beside something else: what is in the image goes as it is,
      // and so does what may be -- the answer carries its allocator, which says
      // which of the two it is.
      if (made.empty()) {
        answer_kinds.emplace_back("static");
        return value;
      }
      if (made.contains(image_key) && within_keys(without(made, { image_key }), allowed)) {
        answer_kinds.emplace_back("maybe");
        return value;
      }
    }
    if (allowed.empty() && made_in_only(func, hands_at)) {
      // Answering before the arena it hands out is made: there is nowhere the
      // answer could be, the arena being the body's own.  Said once.
      if (! hands_out_told.contains(id_of(func))) {
        hands_out_told.insert(id_of(func));
        diags.emit(D::LANG_HANDED_OUT_NOT_MADE, span, { { "name", func->hands_out.value_or("") } });
      }
      return value;
    }
    if (ast::is<ast::Call>(written) && callee_of.get_or(id_of(written), nullptr) == func
        && (within_keys(made, allowed) || only(allowed, heap_key)))
      // The function answering what it answers itself: by the same rule, one
      // call down, so nothing is copied.
      return value;
    if (made.empty() && ! thin) {
      answer_kinds.emplace_back("static");
      return value;
    }
    if (! made.empty() && only(allowed, heap_key)) {
      if (freshly_heap(written)) {
        answer_kinds.emplace_back("heap");
        return value;
      }
      if (! thin && only(made, heap_key) && is_temporary(written)) {
        // Of the heap's, or perhaps text in the image an answer came back as:
        // as it is, the value saying which.
        answer_kinds.emplace_back("maybe");
        return value;
      }
    } else if (! made.empty() && within_keys(made, allowed)) {
      answer_kinds.emplace_back("param");
      return value;
    }
    // Something to copy into: `⎕heap`, or an arena -- an array the answer may
    // be some of is no place to copy anything to.
    std::vector<std::int64_t> into;
    for (auto one : allowed)
      if (! array_frames.contains(one))
        into.push_back(one);
    std::optional<std::int64_t> target = thin ? thin : into.empty() ? std::nullopt : std::optional(into.front());
    if (! target || ! owned::can_own(ty)) [[unlikely]] {
      std::vector<std::string> found;
      if (! made.empty() && within_keys(made, allowed))
        found.emplace_back("what another name still holds");
      else {
        for (auto one : made)
          if (one == heap_key || arena_by_id.contains(one))
            found.push_back(owner_written(one));
        std::ranges::sort(found);
        if (found.empty())
          found.emplace_back("somewhere it cannot say");
      }
      diags.emit(D::LANG_ANSWER_CANNOT_BE_COPIED, span,
                 { { "name", func->name }, { "found", joined_with(found, ", ") }, { "said", named },
                   { "type", ty->written() } });
      return undef(module, ir::error_type());
    }
    if (*target == heap_key && ir::is<ir::ListType>(ty) && owned_parameter(written, func)) {
      // A parameter taken as this call's own is nobody else's room and goes
      // when the call does: where it is the heap's already it is answered as
      // it is, which is how `v ← f(v)` moves both ways.
      answer_kinds.emplace_back("heap");
      return heaps_or_copied(builder, value, ir::as<ir::ListType>(ty), span);
    }
    ir::Function* own = owned::own_function(module, ty);
    ir::Value* where = owner_place(builder, *target, span);
    ir::Value* copied = builder.call(own, std::vector<ir::Value*>{ where, value }, ty, span);
    answer_kinds.emplace_back(*target == heap_key ? "heap" : "param");
    module.record("copy-into-allocator", func->name,
                  "what '" + func->name + "' answers is copied into " + named + ", where its signature says it is made",
                  span);
    if (only(made, heap_key) && is_temporary(written))
      // The original goes back to where it came from, which a value only
      // perhaps of the heap's is asked.
      owned::give_back(builder, value, ty,
                       freshly_heap(written) ? std::optional<std::string_view>("heap") : std::nullopt);
    return copied;
  }


  bool Checker::lambda_may_leave(const ast::Expr* written, const KeySet& lasting, std::string_view name, Span span)
  {
    KeySet made = arenas_in(written);
    std::vector<std::int64_t> stays;
    for (auto one : made)
      if (! lasting.contains(one) && one != image_key)
        stays.push_back(one);
    if (stays.empty())
      return true;
    NameSet found;
    for (auto one : stays)
      found.insert(reach_written(one));
    diags.emit(D::LANG_LAMBDA_REACHES_THE_CALL, span, { { "name", name }, { "found", joined_with(found, ", ") } });
    return false;
  }


  std::string Checker::reach_written(std::int64_t key)
  {
    if (Local* const* reaching = stand_ins.get(key))
      return "'" + (*reaching)->name + "'";
    if (key == image_key)
      return "the image";
    if (key == heap_key || arena_by_id.contains(key))
      return owner_written(key);
    if (Local* const* unknown = unknown_by_id.get(key))
      return "what '" + (*unknown)->name + "' was handed";
    if (is_element_key(key))
      return "a container it was read out of";
    return "something of this call's";
  }


  bool Checker::freshly_heap(const ast::Expr* written)
  {
    while (auto named = ast::as<ast::Named>(written))
      written = named->value;
    if (auto allocated = ast::as<ast::Allocated>(written))
      return only(arenas_in(allocated->arena), heap_key)
             && (ast::is<ast::Binary>(allocated->value) || ast::is<ast::ListLit>(allocated->value));
    if (ast::is<ast::Call>(written))
      return fresh_heap.contains(id_of(written));
    auto binary = ast::as<ast::Binary>(written);
    if (ast::is<ast::ListLit>(written) || (binary != nullptr && binary->op == ast::BinaryOp::concat))
      return only(arenas_in(written), heap_key);
    return false;
  }


  void Checker::handed_out_made(ir::Function* func, const ast::Node* node)
  {
    if (! func->hands_out || hands_out_local != nullptr || hands_out_told.contains(id_of(func)))
      return;
    hands_out_told.insert(id_of(func));
    diags.emit(D::LANG_HANDED_OUT_NOT_MADE, name_span_of(node), { { "name", *func->hands_out } });
  }


  void Checker::settle_answer(ir::Function* func, const ast::FuncDef* node)
  {
    func->answer_from.reset();
    if (! thin_able(func->fty->ret))
      return;
    auto all_are = [this](std::string_view kind) {
      return ! answer_kinds.empty() && std::ranges::all_of(answer_kinds, [&](const auto& one) { return one == kind; });
    };
    if (func->made_in) {
      if (func->made_in->size() != 1)
        return;
      std::int64_t at = (*func->made_in)[0];
      if (at == hands_at)
        // The arena handed out is where the caller keeps it: the last
        // parameter, which is what the caller puts back.
        at = static_cast<std::int64_t>(func->fty->params.size()) - 1;
      if (at == heap_at)
        func->answer_from = std::pair<std::string, int>("heap", -1);
      else if (at == static_at)
        func->answer_from = std::pair<std::string, int>("static", -1);
      else if (all_are("param"))
        func->answer_from = std::pair<std::string, int>("param", static_cast<int>(at));
      else
        return;
    } else if (all_are("heap"))
      func->answer_from = std::pair<std::string, int>("heap", -1);
    else if (all_are("static"))
      func->answer_from = std::pair<std::string, int>("static", -1);
    else
      return;
    const auto& [kind, index] = *func->answer_from;
    std::string where;
    if (kind == "heap")
      where = names::heap_name;
    else if (kind == "static")
      where = std::string(names::static_name) + ", the image,";
    else if (index < static_cast<int>(node->params.size()))
      where = "what '" + std::string(node->params[index]->name) + "' is given";
    else
      where = "'" + func->hands_out.value_or("") + "', handed out to the caller";
    bool said = (kind == "heap" || kind == "static") && func->made_in && ! func->made_in->empty();
    module.record("allocator", func->name,
                  "what '" + func->name + "' answers is made in " + where + " on every way out"
                    + (said ? "" : ", which its body shows") + ": it travels without its allocator, which the caller adds",
                  func->name_span.is_valid() ? func->name_span : func->span);
  }


  void Checker::before_a_turn(const ast::Block* body)
  {
    NameSet named;
    empties_named(body, named);
    for (const auto& name : named) {
      Local* arena = find_local(name);
      if (arena == nullptr || ! is_arena(arena))
        continue;
      for (auto& scope : scopes)
        for (auto& [_, local] : scope)
          if (local->arenas.contains(id_of(arena)) && ! local->gone_at) {
            local->gone_at = body->span;
            local->gone_with = arena->name;
          }
    }
  }


  void Checker::lower_defer(const ast::Defer* stmt)
  {
    const ast::Stmt* later = stmt->stmt;
    std::optional<std::string> why;
    switch (later->tag) {
    case ast::NodeKind::ReturnStmt:
    case ast::NodeKind::Break:
    case ast::NodeKind::Continue:
      why = "it would leave a block that is already being left";
      break;
    case ast::NodeKind::Defer:
      why = "it would put off something already being put off";
      break;
    case ast::NodeKind::VarDef:
      why = "what it would name is gone as soon as it is named";
      break;
    case ast::NodeKind::ExprStmt:
    case ast::NodeKind::AssignStmt:
    case ast::NodeKind::MemberAssign:
    case ast::NodeKind::ElementAssign:
    case ast::NodeKind::DerefAssign:
    case ast::NodeKind::JoinAssign:
      if (leaves(later))
        why = "a '?' in it would leave a block already being left";
      break;
    default:
      why = "it is not something to do";
      break;
    }
    if (why) [[unlikely]] {
      diags.emit(D::LANG_DEFER_NOT_A_THING_TO_DO, later->span, { { "why", *why } });
      return;
    }
    auto entry = make<Deferred>();
    entry->node = stmt;
    entry->depth = static_cast<std::int64_t>(scopes.size());
    entry->block = block_spans.back();
    deferred.back().push_back(entry);
  }


  void Checker::give_back_on_leaving(const ast::VarDef* node)
  {
    auto callee = syntax.make<ast::NameRef>(node->span);
    callee->name = names::empty_name;
    auto arena = syntax.make<ast::NameRef>(node->name_span);
    arena->name = node->name;
    auto call = syntax.make<ast::Call>(node->span);
    call->callee = callee;
    call->args = syntax.list(std::vector<const ast::Expr*>{ arena });
    implicit_empties.insert(id_of(call));
    auto stmt = syntax.make<ast::ExprStmt>(node->span);
    stmt->value = call;
    auto defer = syntax.make<ast::Defer>(node->span);
    defer->stmt = stmt;
    auto entry = make<Deferred>();
    entry->node = defer;
    entry->depth = static_cast<std::int64_t>(scopes.size());
    entry->block = block_spans.back();
    deferred.back().push_back(entry);
  }


  void Checker::run_deferred(ir::Builder& builder, ir::Function* func, std::int64_t down_to)
  {
    if (func == nullptr)
      return;
    for (auto frame = static_cast<std::int64_t>(deferred.size()) - 1; frame >= down_to; --frame) {
      // A copy: lowering one may open blocks of its own, which moves the rest.
      auto entries = deferred[frame];
      for (auto entry : entries | std::views::reverse) {
        if (builder.is_terminated())
          return;
        lower_deferred(builder, func, entry);
      }
    }
  }


  void Checker::lower_deferred(ir::Builder& builder, ir::Function* func, Deferred* entry)
  {
    bool told = defer_told.contains(id_of(entry->node));
    defer_told.insert(id_of(entry->node));
    auto kept_in_deferred = in_deferred;
    auto kept_deferred_at = deferred_at;
    in_deferred = told ? "again" : "first";
    deferred_at = std::tuple<const ast::Defer*, Span>(entry->node, entry->block);
    auto depth = static_cast<std::size_t>(entry->depth);
    auto scopes_tail = cut_to(scopes, depth);
    auto units_tail = cut_to(unit_scopes, depth);
    std::optional<DiagEngine::Diverted> loud;
    if (told)
      loud = diags.divert([](const Diagnostic&) {});
    Finally restore([&] {
      put_back_tail(scopes, depth, scopes_tail);
      put_back_tail(unit_scopes, depth, units_tail);
      if (loud)
        diags.restore(std::move(*loud));
      in_deferred = kept_in_deferred;
      deferred_at = kept_deferred_at;
    });
    lower_stmt(builder, entry->node->stmt, func, false);
    // A statement of its own, so what it lent out for itself is given back
    // before the next one runs -- as at the end of any statement.
    statement_ended();
  }


  void Checker::unrun_deferred(ir::Function* func, std::int64_t frame)
  {
    std::vector<Deferred*> pending;
    for (auto entry : deferred[frame])
      if (! defer_told.contains(id_of(entry->node)))
        pending.push_back(entry);
    if (pending.empty())
      return;
    ir::BasicBlock* nowhere = func->add_block("deferred.unreached");
    ir::Builder builder(module, *func);
    builder.position_at(nowhere);
    for (auto entry : pending) {
      if (builder.is_terminated())
        break;
      lower_deferred(builder, func, entry);
    }
    if (! builder.is_terminated())
      builder.unreachable();
  }


  void Checker::returning(ir::Builder& builder, ir::Value* value, Span span)
  {
    auto [node, func] = demanding;
    if (func != nullptr) {
      // What every open block put off, innermost first, after the answer was
      // worked out and before anything is said about it.
      auto kept = on_the_way_out();
      Finally restore([&] { left(kept); });
      run_deferred(builder, func, 0);
      if (builder.is_terminated())
        return;
      all_closed(scopes, span);
    }
    if (node != nullptr && func != nullptr)
      for (auto clause : node->clauses) {
        if (clause->kind != ast::ClauseKind::post)
          continue;
        if (auto reading = reading_of(clause); ! reading || *reading)
          continue;
        push_scope();
        if (value != nullptr)
          bind_local(names::answer_name, value, clause->span);
        bool outer = std::exchange(after, true);
        Finally restore([&] {
          after = outer;
          scopes.pop_back();
          unit_scopes.pop_back();
        });
        demanded(builder, clause, func, "a post-condition that does not hold", target::statuses::POST_CONDITION);
      }
    builder.ret(value, span);
  }


  void Checker::demanded(ir::Builder& builder, const ast::Clause* clause, ir::Function* func, std::string_view what,
                         std::int64_t status)
  {
    // A question the compiler settles is settled here and lowered nowhere.
    if (auto known = settled(clause->expr)) {
      already(clause, func, *known);
      return;
    }
    // Pure whatever the function is: what makes a condition removable is that
    // removing it changes nothing.
    bool outer = std::exchange(impure, false);
    std::size_t mark = diags.because(D::LANG_CLAUSE_IMPURE, clause->span, {},
                                     std::vector<DiagId>(purity().begin(), purity().end()));
    ir::Value* value;
    {
      Finally restore([&] {
        diags.and_no_longer(mark);
        impure = outer;
      });
      value = lower_expr(builder, clause->expr, nullptr);
    }
    const ir::Type* found = value_type_of(value);
    if (found == ir::error_type())
      return;
    if (found != ir::bool_type()) [[unlikely]] {
      diags.emit(D::LANG_CLAUSE_NOT_A_TRUTH, clause->span, { { "found", found->written() } });
      return;
    }
    if (value->vkind == ir::ValueKind::bool_const) {
      already(clause, func, static_cast<ir::BoolConst*>(value)->value);
      return;
    }
    if (conditions == Conditions::ignore) {
      // Everything above still happened: what a build turns off is the check
      // and not the checking.
      module.record("condition-dropped", func->name,
                    "'" + as_written(clause) + "' was not emitted, this build having asked for none", clause->span);
      return;
    }
    builder.check(value, std::string(what), static_cast<int>(status), clause->span, conditions == Conditions::observe);
  }


  void Checker::already(const ast::Clause* clause, ir::Function* func, bool holds)
  {
    if (! holds) [[unlikely]] {
      diags.emit(D::LANG_CLAUSE_CANNOT_HOLD, clause->span, { { "function", func->name } });
      return;
    }
    module.record("condition-holds", func->name,
                  "'" + as_written(clause) + "' was settled while compiling, so nothing of it reaches the binary",
                  clause->span);
  }


  // -- bodies -----------------------------------------------------------------

  void Checker::lower_function(Collected* entry)
  {
    const ast::FuncDef* node = entry->node;
    ir::Function* func = entry->func;
    if (node->body == nullptr)
      return;
    if (node->body->failed()) {
      // Its body could not be read, which was said where it could not.
      discard(func);
      return;
    }
    bool previous = std::exchange(discard_function, false);
    // Which function this is, for the one rule that is about where a call is
    // written rather than about what it calls.
    ir::Function* was_building = std::exchange(
      in_build_function, func->attrs.special == ir::SpecialKind::build ? func : nullptr);
    if (entry->expectation != nullptr)
      diags.resume(*entry->expectation);
    Finally restore([&] {
      std::vector<Expected> pairs;
      if (auto found = expected_pairs.get(std::string(node->name)))
        pairs = *found;
      end_expecting(entry->expectation);
      if (settle_expecting(entry->expectation, pairs))
        discard_function = true;
      if (discard_function)
        discard(func);
      discard_function = previous;
      in_build_function = was_building;
    });
    lower_body(entry);
    report_scopes(node);
  }


  void Checker::report_scopes(const ast::FuncDef* node)
  {
    if (scopes_told.contains(id_of(node)))
      return;
    scopes_told.insert(id_of(node));
    LastUses::Lends lent;
    for (const auto& [keeper, lenders] : lends)
      lent[keeper].insert(lenders.begin(), lenders.end());
    LastUses::Moves moves(moved_from.begin(), moved_from.end());
    LastUses(*sources, lent, moves, [this](std::string_view name, std::string reason, Span span) {
      module.record("scope-ended", std::string(name), std::move(reason), span);
    }).function(node);
  }


  void Checker::discard(ir::Function* func)
  {
    std::string filed = key(func->name);
    auto& listed = module.functions;
    for (auto it = listed.begin(); it != listed.end();)
      if (it->first == filed) {
        discarded.push_back(std::move(it->second));
        it = listed.erase(it);
      } else
        ++it;
    top->erase(func->name);
    if (module.startup == func)
      module.startup = nullptr;
    for (auto cache : { &module.ctors, &module.dtors, &module.tests })
      if (auto it = std::ranges::find(*cache, func); it != cache->end())
        cache->erase(it);
  }


  void Checker::lower_body(Collected* entry)
  {
    const ast::FuncDef* node = entry->node;
    ir::Function* func = entry->func;
    borrows.clear();
    places.clear();
    ir::BasicBlock* block = func->add_block();
    ir::Builder builder(module, *func);
    auto outer_answer = std::exchange(answering, func->fty->ret);
    auto outer_impure = std::exchange(impure, func->attrs.impure);
    // What a device travels with: a function handed one may read or write it
    // without saying anything.  A generator's body is handed its frame, and
    // what the generator was handed is what it says.
    Generator* walked = generator_bodies.get_or(id_of(func), nullptr);
    const auto& handed = walked != nullptr ? walked->params : func->fty->params;
    auto outer_io = std::exchange(
      may_do_io, func->attrs.io || std::ranges::any_of(handed, [](const ir::Type* one) { return reaches_a_device(one); }));
    auto outer_did = std::exchange(did_io, false);
    auto outer_demanding = std::exchange(demanding, { node, func });
    auto outer_marking = std::exchange(marking, marks_for(node, func, walked));
    auto outer_unsettled = std::exchange(unsettled_tables, {});
    push_scope();
    auto outer_addressed = std::exchange(addressed, {});
    auto outer_unscanned = std::exchange(unscanned, node->body);
    auto outer_changed_here = changed_here;
    auto outer_whole_here = whole_here;
    if (node->body != nullptr) {
      addressed_in(node->body, addressed);
      read_through(node->body, read_through_ids);
      live_for(node);
      changed_here = changed_names(node->body);
      whole_here = used_whole(node->body);
    }
    // Every parameter gets its register before any of them is given storage.
    Generator* generating_here = generator_bodies.get_or(id_of(func), nullptr);
    auto outer_generating = generating;
    auto outer_generating_out = generating_out;
    std::vector<ir::Value*> arriving;
    if (generating_here != nullptr) {
      // A generator's body: where its frame is, where what it hands out goes,
      // and where its arguments are, which are read from there.
      ir::Value* own = block->add_param(environment_type(), "frame");
      ir::Value* out = block->add_param(environment_type(), "out");
      ir::Value* args = block->add_param(environment_type(), "args");
      arriving = arguments_from(builder, args, generating_here->params, node->span);
      generating = generating_here;
      generating_out = out;
      generating_frame = own;
      generating_args = args;
    } else
      for (std::size_t index = 0; index < node->params.size(); ++index)
        arriving.push_back(block->add_param(func->fty->params[index], std::string(node->params[index]->name)));
    auto outer_hands = hands_out;
    auto outer_hands_local = hands_out_local;
    hands_out.reset();
    hands_out_local = nullptr;
    if (func->hands_out)
      // Where the caller keeps the arena this body hands out, last.
      hands_out = std::tuple<std::string, ir::Value*, std::int64_t>(
        *func->hands_out, block->add_param(func->fty->params.back(), *func->hands_out + ".out"),
        static_cast<std::int64_t>(scopes.size()));
    auto outer_entry = std::exchange(at_entry, {});
    for (std::size_t index = 0; index < node->params.size() && index < arriving.size(); ++index) {
      const ast::Param* param = node->params[index];
      ir::Value* value = arriving[index];
      // What arrived, before anything is done about storage.
      at_entry[std::string(param->name)] = value;
      if (const ir::Type* held = held_by_the_startup(func, static_cast<std::int64_t>(index), param)) {
        // The record the process was started with: the name stands for that
        // storage and not for a copy of what is in it.
        bind_local(param->name, value, param->span, param->mutable_, invalid_span, true, &builder, held);
        continue;
      }
      if (! std::ranges::contains(func->takes_owned, static_cast<int>(index)))
        // Where it is taken as its own, the caller has seen to it.
        value = own_when_changed(builder, param, value, node->body);
      bind_local(param->name, value, param->span, param->mutable_, invalid_span, true, &builder);
    }
    if (generating_here == nullptr)
      parameter_slots(node, func);
    std::span<const ir::Type* const> param_types(func->fty->params);
    auto outer_lifetimes = enter_lifetimes(lifetime_key(node), node->params,
                                           param_types.first(std::min(param_types.size(), node->params.size())),
                                           arriving);
    auto outer_made_in = answer_made_in;
    auto outer_made_from = std::exchange(made_from, {});
    auto outer_kinds = answer_kinds;
    auto outer_thin = thin_to;
    params_made_in(node, func);
    // A function with no type parameters has nothing a requirement could
    // constrain; its pre-conditions are conditions and nothing else.
    if (! no_requirement_here(node)) {
      check_clause_shapes(node->clauses);
      Dict<std::string, const ir::Type*> none;
      check_clauses(node->clauses, none, func->name, node->name_span);
    }
    // Before the first statement: a condition is about what the function was
    // called with.
    lower_conditions(builder, node, func);
    const ast::Block* body = node->body;
    if (generating_here != nullptr && generating_here->finishes != nullptr) {
      auto stmts = body->stmts();
      if (! stmts.empty())
        if (auto last = ast::as<ast::ExprStmt>(stmts.back())) {
          // What a generator answers when it ends is its last expression, as a
          // function's answer is: the same as a `return` of it.
          auto ret = syntax.make<ast::ReturnStmt>(last->span);
          ret->value = last->value;
          ret->explicit_ = false;
          std::vector<const ast::Stmt*> changed(stmts.begin(), stmts.end() - 1);
          changed.push_back(ret);
          auto copy = syntax.copy(*body);
          copy->job = nullptr;
          copy->done = true;
          copy->did_fail = false;
          copy->statements = syntax.list(changed);
          body = copy;
        }
    }
    lower_block(builder, body, func, generating_here == nullptr);
    // Before the scope goes: a parameter a reference was taken of lives in the
    // frame, and the name is what still knows where.
    std::vector<std::string> param_names;
    for (auto one : node->params)
      param_names.emplace_back(one->name);
    answered_as_said(func, borrowed_of(func->fty), param_names, arriving);
    pop_scope(builder.is_terminated() ? std::nullopt : std::optional(node->span));
    settle_tables(func);
    unsettled_tables = std::move(outer_unsettled);
    lifetime_scopes = std::move(outer_lifetimes.scopes);
    lifetime_sources = std::move(outer_lifetimes.sources);
    addressed = std::move(outer_addressed);
    answering = outer_answer;
    impure = outer_impure;
    may_do_io = outer_io;
    if (did_io && ! func->attrs.io) {
      // Written down now that the body has said so: everything below the
      // checker reads one flag to know a call may not be dropped.
      func->attrs.io = true;
      func->attrs.impure = true;
    }
    did_io = outer_did;
    if (! builder.is_terminated()) {
      if (generating_here != nullptr) {
        // The end of a generator's body is where it has nothing more -- and no
        // answer, which one saying it answers something lacks.
        if (generating_here->finishes != nullptr) [[unlikely]]
          diags.emit(D::LANG_FUNCDEF_RETURN_MISSING, node->name_span,
                     { { "name", node->name }, { "type", generating_here->finishes->written() } });
        returning(builder, builder.int_const(ir::u8(), 0), node->span);
      } else if (func->fty->ret == ir::void_type())
        returning(builder, nullptr, node->span);
      else {
        diags.emit(D::LANG_FUNCDEF_RETURN_MISSING, node->name_span,
                   { { "name", func->name }, { "type", func->fty->ret->written() } });
        builder.unreachable();
      }
    }
    generating = outer_generating;
    generating_out = outer_generating_out;
    if (generating_here == nullptr)
      settle_answer(func, node);
    handed_out_made(func, node);
    hands_out = std::move(outer_hands);
    hands_out_local = outer_hands_local;
    demanding = outer_demanding;
    marking = outer_marking;
    unscanned = outer_unscanned;
    changed_here = std::move(outer_changed_here);
    whole_here = std::move(outer_whole_here);
    at_entry = std::move(outer_entry);
    answer_made_in = std::move(outer_made_in);
    made_from = std::move(outer_made_from);
    answer_kinds = std::move(outer_kinds);
    thin_to = outer_thin;
  }


  ir::Value* Checker::lower_block(ir::Builder& builder, const ast::Block* block, ir::Function* func, bool as_result,
                                  const ir::Type* wanted, bool produces)
  {
    deferred.emplace_back();
    auto outer_live = current_live;
    auto outer_reach = current_reach;
    auto outer_stmt = current_stmt;
    // To the end of its last statement: a block's own span runs on to the
    // next statement.
    block_spans.push_back(Span{ block->span.start, std::max(block->span.start, end_of(block)) });
    emptied.emplace_back();
    auto frame = static_cast<std::int64_t>(deferred.size()) - 1;
    auto opened_at = static_cast<std::int64_t>(scopes.size());
    Finally restore([&] {
      unrun_deferred(func, frame);
      deferred.pop_back();
      block_spans.pop_back();
      current_live = std::move(outer_live);
      current_reach = std::move(outer_reach);
      current_stmt = outer_stmt;
      last_emptied = std::move(emptied.back());
      emptied.pop_back();
    });
    auto stmts = block->stmts();
    std::size_t count = stmts.size();
    ir::Value* answer = nullptr;
    for (std::size_t index = 0; index < count; ++index) {
      const ast::Stmt* stmt = stmts[index];
      bool is_last = index == count - 1;
      if (builder.is_terminated()) [[unlikely]] {
        diags.emit(D::LANG_STMT_UNREACHABLE, stmt->span);
        return nullptr;
      }
      // What is live here is what a borrow kept by a name needs to be out:
      // the name's last use is where it ends.
      std::optional<KeySet> live;
      if (auto found = live_in.get(id_of(stmt)))
        live = *found;
      auto reach_here = [&] {
        auto found = live_reach.get(id_of(stmt));
        current_reach = found != nullptr ? std::optional(*found) : std::nullopt;
      };
      if (live) {
        borrows_ended_at(stmt);
        current_live = live;
        reach_here();
      }
      current_stmt = stmt;
      if (is_last && produces)
        answer = lower_yielding(builder, stmt, func, wanted);
      else
        lower_attributed_stmt(builder, stmt, func, as_result && is_last);
      current_stmt = stmt;
      if (live) {
        borrows_ended_after(stmt);
        current_live = live;
        reach_here();
      }
      statement_ended();
    }
    if (produces && answer == nullptr && ! builder.is_terminated()) [[unlikely]]
      diags.emit(D::LANG_MATCH_ARM_HAS_NO_VALUE, block->span);
    if (produces && answer != nullptr && ! stmts.empty())
      // What the block comes to leaves it, and an arena made inside it does
      // not: it is given back on the way out.
      if (auto last = ast::as<ast::ExprStmt>(stmts.back()))
        leaves_no_arena_behind(last->value, opened_at, nullptr, last->span, value_type_of(answer));
    // Run off the end: what this block put off is done now, after what it
    // comes to was worked out and before anything after it runs.
    borrows_ended_with(block);
    if (! builder.is_terminated())
      run_deferred(builder, func, frame);
    return answer;
  }


  ir::Value* Checker::lower_yielding(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func,
                                     const ir::Type* wanted)
  {
    auto pairs = expected_numbers(bind_attributes(stmt->attrs, AttrTarget::statement));
    Expectation* expectation = begin_expecting(pairs);
    // An arm of what the function answers ending in another `if` or `match`
    // hands the answering down to that one's arms.
    ir::Function* answering_here = std::exchange(yield_answer, nullptr);
    ir::Function* outer_arms = answer_arms;
    if (auto expr = ast::as<ast::ExprStmt>(stmt); answering_here != nullptr && expr != nullptr
        && (ast::is<ast::If>(expr->value) || ast::is<ast::Match>(expr->value)))
      answer_arms = answering_here;
    Finally restore([&] {
      answer_arms = outer_arms;
      end_expecting(expectation);
      if (settle_expecting(expectation, pairs))
        discard_function = true;
    });
    switch (stmt->tag) {
    case ast::NodeKind::ExprStmt: {
      auto expr = static_cast<const ast::ExprStmt*>(stmt);
      if (wanted != nullptr)
        return lower_into(builder, expr->value, wanted, stmt->span);
      return lower_expr(builder, expr->value, nullptr);
    }
    case ast::NodeKind::AssignStmt: {
      ir::Value* found = lower_assignment(builder, static_cast<const ast::AssignStmt*>(stmt), true);
      if (found != nullptr && wanted != nullptr && found->ty != wanted && found->ty != ir::error_type()) [[unlikely]] {
        report_mismatch(stmt->span, found->ty, wanted);
        return undef(module, wanted);
      }
      return found;
    }
    case ast::NodeKind::ReturnStmt:
      lower_return(builder, static_cast<const ast::ReturnStmt*>(stmt), func);
      return nullptr;
    default:
      diags.emit(D::LANG_MATCH_ARM_HAS_NO_VALUE, stmt->span);
      return nullptr;
    }
  }


  void Checker::lower_attributed_stmt(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func, bool is_last)
  {
    auto pairs = expected_numbers(bind_attributes(stmt->attrs, AttrTarget::statement));
    Expectation* expectation = begin_expecting(pairs);
    bool carried = false;
    Finally restore([&] {
      end_expecting(expectation);
      if (! carried && settle_expecting(expectation, pairs))
        discard_function = true;
    });
    lower_stmt(builder, stmt, func, is_last);
    if (auto def = ast::as<ast::VarDef>(stmt); def != nullptr && expectation != nullptr)
      if (Local* local = find_local(def->name); local != nullptr) {
        local->expectation = expectation;
        local->expected_pairs = pairs;
        carried = true;
      }
  }


  void Checker::lower_stmt(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func, bool is_last)
  {
    if (auto one = ast::as<ast::MemberAssign>(stmt))
      stored_into[id_of(one->value)] = "a field";
    else if (auto one = ast::as<ast::ElementAssign>(stmt))
      stored_into[id_of(one->value)] = "an element";
    else if (auto one = ast::as<ast::DerefAssign>(stmt))
      stored_into[id_of(one->value)] = "what a reference names";
    auto inferred = [](const ast::VarDef* def) {
      std::vector<std::tuple<std::string, Span>> names{ { std::string(def->name), def->name_span } };
      for (const auto& more : def->more)
        names.emplace_back(std::string(more.name), more.span);
      return names;
    };
    switch (stmt->tag) {
    case ast::NodeKind::ReturnStmt: {
      // Only where the statement is one the function could have wanted.
      auto ret = static_cast<const ast::ReturnStmt*>(stmt);
      bool agrees = (ret->value == nullptr) == (func->fty->ret == ir::void_type());
      if (ret->explicit_ && is_last && agrees)
        diags.emit(D::LANG_FUNCDEF_RETURN_REDUNDANT, stmt->span);
      lower_return(builder, ret, func);
      break;
    }
    case ast::NodeKind::VarDef: {
      auto def = static_cast<const ast::VarDef*>(stmt);
      if (def->made_in) {
        lower_receiving(builder, def);
        report_inferred(inferred(def), def->type);
        break;
      }
      copied_where_shared(def->value, def->mutable_);
      lower_local(builder, def);
      variable_slot(def);
      made_in(def->name, def->value, def->span);
      report_inferred(inferred(def), def->type);
      if (session != nullptr && session->kept.contains(def->name_span.start))
        for (const auto& [name, span] : inferred(def))
          if (auto alias = session->stores.get(name))
            kept_by_the_session(builder, *alias, name, span, func);
      break;
    }
    case ast::NodeKind::AssignStmt: {
      // An assignment stands for the variable it changed, so it can be a
      // function's result the way any other last statement can.
      auto assign = static_cast<const ast::AssignStmt*>(stmt);
      bool wants_value = is_last && func->fty->ret != ir::void_type();
      if (assign->more.empty())
        copied_where_shared(assign->value, true);
      ir::Value* result = lower_assignment(builder, assign, wants_value);
      if (assign->more.empty())
        made_in(assign->name, assign->value, assign->span, true);
      if (wants_value && result != nullptr) {
        // The mismatch here is between what the statement produced and what
        // the function returns, which is what a return mismatch says.
        if (result->ty != func->fty->ret)
          report_mismatch(stmt->span, result->ty, func->fty->ret);
        auto name = syntax.make<ast::NameRef>(assign->name_span);
        name->name = assign->name;
        result = answered(builder, name, result, func, stmt->span);
        returning(builder, result, stmt->span);
      }
      break;
    }
    case ast::NodeKind::EntryAssign: {
      auto assign = static_cast<const ast::EntryAssign*>(stmt);
      lower_entry_assign(builder, assign);
      made_in_part(assign->base, assign->value, assign->span);
      break;
    }
    case ast::NodeKind::UnitDef:
      // Nothing is lowered: a unit is part of a type and a type is nothing the
      // program runs.  What it does is exist from here to the end of the body.
      define_unit(static_cast<const ast::UnitDef*>(stmt));
      break;
    case ast::NodeKind::ElementAssign: {
      auto assign = static_cast<const ast::ElementAssign*>(stmt);
      if (marked(assign->base)) {
        assign_each(builder, stmt, func);
        break;
      }
      copied_where_shared(assign->value, true);
      lower_element_assign(builder, assign);
      made_in_part(assign->base, assign->value, assign->span, true);
      break;
    }
    case ast::NodeKind::JoinAssign:
      lower_join_assign(builder, static_cast<const ast::JoinAssign*>(stmt));
      break;
    case ast::NodeKind::MemberAssign: {
      auto assign = static_cast<const ast::MemberAssign*>(stmt);
      if (marked(assign->base)) {
        assign_each(builder, stmt, func);
        break;
      }
      copied_where_shared(assign->value, true);
      lower_member_assign(builder, assign);
      made_in_part(assign->base, assign->value, assign->span);
      break;
    }
    case ast::NodeKind::Yield:
      lower_yield(builder, static_cast<const ast::Yield*>(stmt), func);
      break;
    case ast::NodeKind::DerefAssign:
      lower_deref_assign(builder, static_cast<const ast::DerefAssign*>(stmt));
      break;
    case ast::NodeKind::Defer:
      lower_defer(static_cast<const ast::Defer*>(stmt));
      break;
    case ast::NodeKind::Break:
      lower_break(builder, static_cast<const ast::Break*>(stmt));
      break;
    case ast::NodeKind::Continue:
      lower_continue(builder, static_cast<const ast::Continue*>(stmt));
      break;
    case ast::NodeKind::EmptyStmt:
      // Nothing to lower.  What it does is be a statement, so that a body
      // ending in a semicolon ends in one that produces no value.
      break;
    case ast::NodeKind::ExprStmt: {
      // The value of the last statement is the function's result, which is
      // why the canonical form of the language omits the keyword.
      const ast::Expr* value = static_cast<const ast::ExprStmt*>(stmt)->value;
      if (is_last && func->fty->ret != ir::void_type()) {
        // An `if` or a `match` answered is answered arm by arm, the copy into
        // the signature's allocator made where each arm ends.
        bool by_arm = ast::is<ast::If>(value) || ast::is<ast::Match>(value);
        ir::Value* found;
        {
          ir::Function* outer_arms = std::exchange(answer_arms, by_arm ? func : nullptr);
          Finally restore([&] { answer_arms = outer_arms; });
          found = lower_into(builder, value, func->fty->ret, stmt->span);
        }
        if (! by_arm)
          found = answered(builder, value, found, func, stmt->span);
        returning(builder, found, stmt->span);
      } else if (auto one = ast::as<ast::If>(value))
        // An `if` written as a statement of its own produces no value, and
        // needs no `else` for that reason.
        lower_if(builder, one, func, nullptr, false);
      else if (auto one = ast::as<ast::While>(value))
        // A loop written as a statement of its own produces no value either.
        lower_while(builder, one, func, nullptr, false);
      else if (auto one = ast::as<ast::ForEach>(value))
        lower_foreach(builder, one, func, nullptr, false);
      else if (auto one = ast::as<ast::Match>(value))
        // A `match` written as a statement of its own produces no value, and
        // its arms are runs of statements like any other body.
        lower_match(builder, one, func, nullptr, false);
      else if (session != nullptr && stmt == session->answer) {
        // What the session shows: lowered for what it is, and kept.
        ir::Value* found = lower_expr(builder, value, nullptr);
        session->answer_type = found != nullptr ? value_type_of(found) : nullptr;
        if (found != nullptr && ! session->answer_variable.empty() && session->answer_type != ir::void_type()
            && session->answer_type != ir::error_type()) {
          auto ready = syntax.make<ast::Ready>(value->span);
          ready->value = found;
          auto assign = syntax.make<ast::AssignStmt>(value->span);
          assign->name = syntax.text(session->answer_variable);
          assign->name_span = value->span;
          assign->value = ready;
          lower_assignment(builder, assign, false);
        }
      } else {
        check_value_is_used(value);
        lower_expr(builder, value, nullptr);
      }
      break;
    }
    default:
      diags.internal("unknown statement kind in lowering");
      break;
    }
  }


  void Checker::kept_by_the_session(ir::Builder& builder, std::string_view variable, std::string_view name, Span span,
                                    ir::Function* func)
  {
    auto read = syntax.make<ast::NameRef>(span);
    read->name = syntax.text(name);
    auto assign = syntax.make<ast::AssignStmt>(span);
    assign->name = syntax.text(variable);
    assign->name_span = span;
    assign->value = read;
    lower_stmt(builder, assign, func, false);
  }


  // -- tuples -----------------------------------------------------------------

  ir::Value* Checker::lower_tuple(ir::Builder& builder, const ast::TupleLit* expr, const ir::Type* expected)
  {
    const ir::Type* aim = aiming_at(expected);
    std::optional<std::span<const ir::Type* const>> wanted;
    if (auto tuple = ir::as<ir::TupleType>(aim))
      wanted = std::span<const ir::Type* const>(tuple->members);
    auto values = one_by_one(builder, expr->members, wanted);
    if (! values)
      return undef(module, ir::error_type());
    if (values->empty()) [[unlikely]] {
      // Everything written was spread, and all of it was empty.  A tuple of no
      // members is not a type this language has.
      diags.emit(D::LANG_SPREAD_NOTHING_LEFT, expr->span);
      return undef(module, ir::error_type());
    }
    std::vector<const ir::Type*> types;
    for (auto value : *values)
      types.push_back(value_type_of(value));
    if (std::ranges::contains(types, ir::error_type()))
      return undef(module, ir::error_type());
    auto ty = ir::tuple_type(types);
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return undef(module, ir::error_type());
    }
    // A tuple travels as its leaves and not as its members, the same way a
    // record does -- unless it is held in memory, when it is bytes in a place.
    return made_tuple(builder, *values, ty, expr->span);
  }


  std::optional<std::vector<const ir::Type*>> Checker::taken_apart(ir::Value* value,
                                                                   std::span<const std::tuple<std::string, Span>> names,
                                                                   Span span)
  {
    const ir::Type* ty = value_type_of(value);
    if (ty == ir::error_type())
      return std::nullopt;
    auto tuple = ir::as<ir::TupleType>(ty);
    if (tuple == nullptr) [[unlikely]] {
      diags.emit(D::LANG_TUPLE_NOT_A_TUPLE, span, { { "found", ty->written() } });
      return std::nullopt;
    }
    if (tuple->members.size() != names.size()) [[unlikely]] {
      diags.emit(D::LANG_TUPLE_WRONG_COUNT, span,
                 { { "wanted", std::to_string(names.size()) }, { "found", ty->written() },
                   { "count", std::to_string(tuple->members.size()) } });
      return std::nullopt;
    }
    return tuple->members;
  }


  ir::Value* Checker::member_place(ir::Builder& builder, ir::Value* place, const ir::Type* ty, std::int64_t at,
                                   Span span)
  {
    if (auto product = ir::as<ir::ProductType>(ty))
      return field_place(builder, place, product, at, span);
    auto tuple = ir::as<ir::TupleType>(ty);
    const ir::Type* member = tuple->members[at];
    ir::Value* where = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(ir::u8(), true), span);
    std::uint64_t offset = ir::member_offsets_of(tuple, layout())[at];
    if (offset != 0)
      where = builder.binary(ir::BinOp::add, where, builder.int_const(ir::u64(), offset), span);
    return builder.cast(ir::CastKind::bitcast, where, ir::ptr_type(member, true), span);
  }


  ir::Value* Checker::member(ir::Builder& builder, ir::Value* value, const ir::Type* ty, std::int64_t at, Span span)
  {
    auto tuple = ir::as<ir::TupleType>(ty);
    const ir::Type* held = tuple != nullptr ? tuple->members[at] : ir::as<ir::ProductType>(ty)->fields[at].second;
    if (ir::held_in_memory(ty)) {
      ir::Value* place = builder.cast(ir::CastKind::bitcast, value, ir::ptr_type(ty, true), span);
      return builder.load(member_place(builder, place, ty, at, span), span);
    }
    auto [first, count] = ir::parts_within(ty)[at];
    auto pieces = ir::parts_of(ty);
    std::vector<ir::Value*> parts;
    for (std::size_t which = 0; which < count; ++which)
      parts.push_back(builder.extract(value, static_cast<std::int64_t>(first + which), pieces[first + which], span));
    return builder.whole(parts, held, span);
  }


  ir::Value* Checker::made_tuple(ir::Builder& builder, std::span<ir::Value* const> values, const ir::TupleType* ty,
                                 Span span)
  {
    if (ir::held_in_memory(ty)) {
      ir::Value* room = builder.frame(ty, span);
      for (std::size_t at = 0; at < values.size(); ++at)
        builder.store(member_place(builder, room, ty, static_cast<std::int64_t>(at), span), values[at], span);
      return builder.cast(ir::CastKind::bitcast, room, ty, span);
    }
    std::vector<ir::Value*> leaves;
    for (std::size_t at = 0; at < values.size() && at < ty->members.size(); ++at)
      std::ranges::copy(builder.leaves(values[at], ty->members[at], span), std::back_inserter(leaves));
    return builder.make_tuple(leaves, ty, span);
  }


  // -- arrays -----------------------------------------------------------------

  ir::Value* Checker::lower_array(ir::Builder& builder, const ast::ArrayLit* expr, const ir::Type* expected)
  {
    auto [ty, ready] = array_written(builder, expr, expected);
    if (ty == nullptr)
      return undef(module, ir::error_type());
    if (ready && (! ty->count() || ready->size() != *ty->count()))
      // The writing is not the same width all the way along, so what was
      // lowered does not line up with the run: `fill` says which list is the
      // wrong length.
      ready.reset();
    ir::Value* place = builder.frame(ty, expr->span);
    KeySet made;
    {
      auto outer = std::exchange(element_made, {});
      Finally restore([&] { element_made = std::move(outer); });
      if (! fill(builder, expr, ty, place, 0, ty->shape, ready ? &*ready : nullptr))
        return undef(module, ir::error_type());
      made = element_made;
    }
    if (owned::points(ty->element))
      // The array is room in the frame: what it was made in is what its
      // elements were.
      made_from[id_of(expr)] = std::tuple<const ast::Expr*, KeySet>(expr, made);
    return builder.cast(ir::CastKind::bitcast, place, ty, expr->span);
  }


  std::tuple<const ir::ArrayType*, std::optional<std::vector<ir::Value*>>>
  Checker::array_written(ir::Builder& builder, const ast::ArrayLit* expr, const ir::Type* expected)
  {
    auto wanted = ir::as<ir::ArrayType>(aiming_at(expected));
    if (wanted != nullptr && wanted->fixed()) {
      if (expr->elements.size() != *wanted->shape[0]) [[unlikely]] {
        diags.emit(D::LANG_ARRAY_WRONG_LENGTH, expr->span,
                   { { "given", expr->elements.size() }, { "wanted", *wanted->shape[0] } });
        return { nullptr, std::nullopt };
      }
      return { wanted, std::nullopt };
    }
    auto [shape, inner] = shape_written(expr);
    if (wanted != nullptr)
      // An array of no stated length is wanted: what the writing makes is the
      // array with the length in it, the elements its element type.
      return { ir::array_type(wanted->element, shape), std::nullopt };
    // Nothing says what these are, so they say it themselves, lowered here
    // rather than twice.
    std::vector<ir::Value*> values;
    const ir::Type* element = one_type(builder, inner, nullptr, &values);
    if (element == ir::error_type())
      return { nullptr, std::nullopt };
    return { ir::array_type(element, shape), std::move(values) };
  }


  std::tuple<std::vector<std::optional<std::uint64_t>>, std::vector<const ast::Expr*>>
  Checker::shape_written(const ast::ArrayLit* expr)
  {
    std::vector<std::optional<std::uint64_t>> shape{ expr->elements.size() };
    std::vector<const ast::Expr*> inner(expr->elements.begin(), expr->elements.end());
    while (! inner.empty()
           && std::ranges::all_of(inner, [](const ast::Expr* one) { return ast::is<ast::ArrayLit>(one); })) {
      shape.push_back(static_cast<const ast::ArrayLit*>(inner.front())->elements.size());
      std::vector<const ast::Expr*> spread;
      for (auto written : inner)
        std::ranges::copy(static_cast<const ast::ArrayLit*>(written)->elements, std::back_inserter(spread));
      inner = std::move(spread);
    }
    return { std::move(shape), std::move(inner) };
  }


  bool Checker::fill(ir::Builder& builder, const ast::ArrayLit* expr, const ir::ArrayType* ty, ir::Value* place,
                     std::int64_t at, std::span<const std::optional<std::uint64_t>> shape,
                     const std::vector<ir::Value*>* ready)
  {
    if (! shape[0] || expr->elements.size() != *shape[0]) [[unlikely]] {
      DiagArgs args{ { "given", expr->elements.size() } };
      if (shape[0])
        args.emplace_back("wanted", *shape[0]);
      else
        args.emplace_back("wanted", "None");
      diags.emit(D::LANG_ARRAY_WRONG_LENGTH, expr->span, std::move(args));
      return false;
    }
    if (shape.size() == 1) {
      for (std::size_t index = 0; index < expr->elements.size(); ++index) {
        const ast::Expr* written = expr->elements[index];
        ir::Value* value = ready != nullptr ? (*ready)[at + index] : lower_into(builder, written, ty->element, written->span);
        if (value_type_of(value) == ir::error_type())
          return false;
        if (auto owner = owner_of(out_of_arenas); owner && owned::points(ty->element)) {
          // What an element points into lasts as long as the array, or is
          // copied into the allocator `in` named, or `⎕heap`.
          auto [kept, made, spare] = stored(builder, written, value, ty->element, *owner,
                                            static_cast<std::int64_t>(scopes.size()), true, written->span);
          value = kept;
          if (value_type_of(value) == ir::error_type())
            return false;
          element_made.insert(made.begin(), made.end());
          if (spare != nullptr)
            owned::give_back(builder, spare, ty->element,
                             freshly_heap(written) ? std::optional<std::string_view>("heap") : std::nullopt);
        }
        ir::Value* where = element_place(builder, place, ty->element,
                                         builder.int_const(ir::u64(), at + static_cast<std::int64_t>(index)), expr->span);
        builder.store(where, value, expr->span);
      }
      return true;
    }
    std::int64_t step = 1;
    for (auto along : shape.subspan(1))
      step *= static_cast<std::int64_t>(*along);
    for (std::size_t index = 0; index < expr->elements.size(); ++index) {
      auto written = ast::as<ast::ArrayLit>(expr->elements[index]);
      if (written == nullptr) [[unlikely]] {
        diags.emit(D::LANG_ARRAY_NOT_WRITTEN_DEEP_ENOUGH, expr->elements[index]->span);
        return false;
      }
      if (! fill(builder, written, ty, place, at + static_cast<std::int64_t>(index) * step, shape.subspan(1), ready))
        return false;
    }
    return true;
  }


  ir::Value* Checker::on_its_own(ir::Builder& builder, const ast::Expr* written)
  {
    auto outer_operand = std::exchange(operand_of, std::nullopt);
    auto outer_initializing = std::exchange(initializing, std::nullopt);
    auto outer_assigning = std::exchange(assigning, std::nullopt);
    auto outer_handing = std::exchange(handing_over, std::nullopt);
    Finally restore([&] {
      operand_of = std::move(outer_operand);
      initializing = std::move(outer_initializing);
      assigning = std::move(outer_assigning);
      handing_over = std::move(outer_handing);
    });
    auto literal = ast::as<ast::IntLit>(written);
    bool bare = literal != nullptr && ! literal->type_name;
    return lower_expr(builder, written, bare ? ir::u64() : nullptr);
  }


  ir::Value* Checker::field_place(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty, std::int64_t at,
                                  Span span)
  {
    const ir::Type* held = ty->fields[at].second;
    ir::Value* start = builder.cast(ir::CastKind::bitcast, where, ir::ptr_type(ir::u8(), true), span);
    ir::Value* moved = builder.binary(ir::BinOp::add, start,
                                      builder.int_const(ir::u64(), ir::offsets_of(ty, layout())[at]), span);
    return builder.cast(ir::CastKind::bitcast, moved, ir::ptr_type(held, true), span);
  }


  void Checker::build_record(ir::Builder& builder, ir::Value* where, const ast::Expr* expr, const ir::ProductType* ty,
                             Span span)
  {
    if (! is_record_literal(expr, ty)) {
      ir::Value* value = lower_into(builder, expr, ty, span);
      record_into(builder, where, value, ty, span);
      return;
    }
    auto given = fields_given(static_cast<const ast::Call*>(expr), ty);
    if (! given)
      return;
    for (std::size_t at = 0; at < ty->fields.size(); ++at) {
      const auto& [name, held] = ty->fields[at];
      ir::Value* place = field_place(builder, where, ty, static_cast<std::int64_t>(at), span);
      const ast::Named* written = *given->get(name);
      stored_into[id_of(written->value)] = "a record";
      if (auto record = ir::as<ir::ProductType>(held)) {
        build_record(builder, place, written->value, record, written->span);
        continue;
      }
      builder.store(place, lower_into(builder, written->value, held, written->span), span);
      owning_field(written->value, ty, name, held);
    }
  }


  bool Checker::is_record_literal(const ast::Expr* expr, const ir::ProductType* ty)
  {
    auto call = ast::as<ast::Call>(expr);
    if (call == nullptr || ! ast::is<ast::NameRef>(call->callee))
      return false;
    const Held* named = top->get(std::string(static_cast<const ast::NameRef*>(call->callee)->name));
    NamedType* defined = named != nullptr ? named->get<NamedType>() : nullptr;
    if (defined != nullptr && defined->takes_types())
      return ty->family == defined;
    return defined != nullptr && resolved(defined) == ty;
  }


  std::optional<Dict<std::string, const ast::Named*>> Checker::fields_given(const ast::Call* expr,
                                                                            const ir::ProductType* ty)
  {
    Dict<std::string, const ast::Named*> given;
    bool spoiled = false;
    for (auto one : expr->args) {
      auto named = ast::as<ast::Named>(one);
      if (named != nullptr)
        flow(slot_key("field", ty->name, named->name), named->value);
      if (named == nullptr) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_FIELD_NOT_NAMED, one->span, { { "type", ty->written() } });
        spoiled = true;
        continue;
      }
      if (! field_index(ty, named->name)) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_NO_SUCH_FIELD, one->span, { { "type", ty->written() }, { "name", named->name } });
        spoiled = true;
        continue;
      }
      if (given.contains(std::string(named->name))) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_FIELD_TWICE, one->span, { { "name", named->name } });
        spoiled = true;
        continue;
      }
      given[std::string(named->name)] = named;
    }
    for (const auto& [name, _] : ty->fields)
      if (! given.contains(name)) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_FIELD_MISSING, expr->span, { { "type", ty->written() }, { "name", name } });
        spoiled = true;
      }
    if (spoiled)
      return std::nullopt;
    return given;
  }


  std::int64_t Checker::record_into(ir::Builder& builder, ir::Value* where, ir::Value* value,
                                    const ir::ProductType* ty, Span span, std::int64_t first)
  {
    if (ir::held_in_memory(ty)) {
      // Bytes in a place: what the value is, is where they are, and they are
      // copied.
      ir::Value* place = builder.cast(ir::CastKind::bitcast, where, ir::ptr_type(ty, true), span);
      builder.store(place, value, span);
      return first + 1;
    }
    std::int64_t at_part = first;
    for (std::size_t at = 0; at < ty->fields.size(); ++at) {
      const ir::Type* held = ty->fields[at].second;
      ir::Value* place = field_place(builder, where, ty, static_cast<std::int64_t>(at), span);
      if (auto record = ir::as<ir::ProductType>(held)) {
        at_part = record_into(builder, place, value, record, span, at_part);
        continue;
      }
      // A field of several parts -- text, a lambda -- is that many leaves, put
      // back together into the one value the store writes.
      auto pieces = ir::parts_of(held);
      std::vector<ir::Value*> parts;
      for (std::size_t which = 0; which < pieces.size(); ++which)
        parts.push_back(builder.extract(value, at_part + static_cast<std::int64_t>(which), pieces[which], span));
      builder.store(place, builder.whole(parts, held, span), span);
      at_part += static_cast<std::int64_t>(pieces.size());
    }
    return at_part;
  }


  ir::Value* Checker::record_from(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty, Span span)
  {
    if (ir::held_in_memory(ty))
      return builder.load(builder.cast(ir::CastKind::bitcast, where, ir::ptr_type(ty, true), span), span);
    return builder.make_tuple(leaves_from(builder, where, ty, span), ty, span);
  }


  std::vector<ir::Value*> Checker::leaves_of(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span)
  {
    // A field of several parts -- text, a list, a nested record -- is as many
    // leaves as it has parts, which is what the record's own parts count.
    return builder.leaves(value, ty, span);
  }


  std::vector<ir::Value*> Checker::leaves_from(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty,
                                               Span span)
  {
    std::vector<ir::Value*> found;
    for (std::size_t at = 0; at < ty->fields.size(); ++at) {
      const ir::Type* held = ty->fields[at].second;
      ir::Value* place = field_place(builder, where, ty, static_cast<std::int64_t>(at), span);
      if (auto record = ir::as<ir::ProductType>(held)) {
        std::ranges::copy(leaves_from(builder, place, record, span), std::back_inserter(found));
        continue;
      }
      ir::Value* loaded = builder.load(place, span);
      std::ranges::copy(builder.leaves(loaded, held, span), std::back_inserter(found));
    }
    return found;
  }


  ir::Value* Checker::element_place(ir::Builder& builder, ir::Value* base, const ir::Type* element, ir::Value* index,
                                    Span span)
  {
    ir::Value* start = builder.cast(ir::CastKind::bitcast, base, ir::ptr_type(element, true), span);
    std::uint64_t stride = ir::stride_of(element, layout());
    ir::Value* moved = builder.binary(ir::BinOp::wrap_mul, index, builder.int_const(ir::u64(), stride), span);
    return builder.binary(ir::BinOp::add, start, moved, span);
  }


  std::tuple<ir::Value*, std::vector<ir::Value*>> Checker::shape_of(ir::Builder& builder, ir::Value* base,
                                                                    const ir::ArrayType* ty, Span span)
  {
    auto pointer = ir::ptr_type(ty->element, true);
    std::vector<ir::Value*> lengths;
    if (ty->fixed()) {
      ir::Value* start = builder.cast(ir::CastKind::bitcast, base, pointer, span);
      for (auto along : ty->shape)
        lengths.push_back(builder.int_const(ir::u64(), *along));
      return { start, std::move(lengths) };
    }
    ir::Value* start = builder.extract(base, 0, pointer, span);
    for (std::size_t at = 0; at < ty->rank(); ++at)
      lengths.push_back(builder.extract(base, static_cast<std::int64_t>(at) + 1, ir::u64(), span));
    return { start, std::move(lengths) };
  }


  std::vector<ir::Value*> Checker::strides_of(ir::Builder& builder, ir::Value* base, const ir::ArrayType* ty, Span span)
  {
    std::vector<ir::Value*> found;
    if (ty->fixed()) {
      __int128 along = 1;
      for (auto one : ty->shape | std::views::reverse) {
        found.push_back(builder.int_const(ir::u64(), along));
        along *= *one;
      }
      std::ranges::reverse(found);
      return found;
    }
    for (std::size_t at = 0; at < ty->rank(); ++at)
      found.push_back(builder.extract(base, static_cast<std::int64_t>(1 + ty->rank() + at), ir::u64(), span));
    return found;
  }


  std::vector<ir::Value*> Checker::dense_strides(ir::Builder& builder, std::span<ir::Value* const> lengths, Span span)
  {
    std::vector<ir::Value*> found;
    ir::Value* along = builder.int_const(ir::u64(), 1);
    for (auto one : lengths | std::views::reverse) {
      found.push_back(along);
      along = builder.binary(ir::BinOp::wrap_mul, along, one, span);
    }
    std::ranges::reverse(found);
    return found;
  }


  ir::Value* Checker::view(ir::Builder& builder, ir::Value* place, std::span<ir::Value* const> lengths,
                           std::span<ir::Value* const> strides, const ir::ArrayType* ty, Span span)
  {
    std::vector<ir::Value*> parts{ place };
    parts.insert(parts.end(), lengths.begin(), lengths.end());
    parts.insert(parts.end(), strides.begin(), strides.end());
    return builder.make_tuple(parts, ty, span);
  }


  ir::Value* Checker::dense_view(ir::Builder& builder, ir::Value* place, std::span<ir::Value* const> lengths,
                                 const ir::ArrayType* ty, Span span)
  {
    auto strides = dense_strides(builder, lengths, span);
    return view(builder, place, lengths, strides, ty, span);
  }


  ir::Value* Checker::flat_place(ir::Builder& builder, ir::Value* base, const ir::ArrayType* ty, ir::Value* flat,
                                 Span span, std::optional<std::int64_t> upto)
  {
    std::int64_t dims = upto ? *upto : static_cast<std::int64_t>(ty->rank());
    auto [start, lengths] = shape_of(builder, base, ty, span);
    auto strides = strides_of(builder, base, ty, span);
    ir::Value* offset = flat_offset(builder, lengths, strides, flat, dims, ty->fixed(), span);
    return element_place(builder, start, ty->element, offset, span);
  }


  ir::Value* Checker::flat_offset(ir::Builder& builder, std::span<ir::Value* const> lengths,
                                  std::span<ir::Value* const> strides, ir::Value* flat, std::int64_t upto,
                                  bool one_run, Span span)
  {
    if (one_run || upto == 1)
      return builder.binary(ir::BinOp::wrap_mul, flat, strides[upto - 1], span);
    ir::Value* offset = builder.int_const(ir::u64(), 0);
    ir::Value* left = flat;
    for (std::int64_t at = upto - 1; at >= 0; --at) {
      ir::Value* index = at == 0 ? left : builder.binary(ir::BinOp::urem, left, lengths[at], span);
      ir::Value* step = builder.binary(ir::BinOp::wrap_mul, index, strides[at], span);
      offset = builder.binary(ir::BinOp::wrap_add, offset, step, span);
      if (at != 0)
        left = builder.binary(ir::BinOp::udiv, left, lengths[at], span);
    }
    return offset;
  }


  std::vector<ir::Value*> Checker::odometer(ir::Builder& builder, std::span<ir::Value* const> indices,
                                            std::span<ir::Value* const> lengths, bool downward, Span span)
  {
    ir::Value* one = builder.int_const(ir::u64(), 1);
    std::vector<ir::Value*> moved(indices.begin(), indices.end());
    ir::Value* carry = one;
    for (auto at = static_cast<std::int64_t>(indices.size()) - 1; at >= 0; --at) {
      ir::Value* index = indices[at];
      if (at == 0) {
        moved[0] = builder.binary(downward ? ir::BinOp::wrap_sub : ir::BinOp::wrap_add, index, carry, span);
        break;
      }
      if (downward) {
        // Going under nought is being at nought with something to take.
        ir::Value* below = builder.compare(ir::CmpPred::ult, index, carry, span);
        ir::Value* under = builder.cast(ir::CastKind::zext, below, ir::u64(), span);
        ir::Value* less = builder.binary(ir::BinOp::wrap_sub, index, carry, span);
        ir::Value* back = builder.binary(ir::BinOp::wrap_mul, under, lengths[at], span);
        moved[at] = builder.binary(ir::BinOp::wrap_add, less, back, span);
        carry = under;
        continue;
      }
      ir::Value* next = builder.binary(ir::BinOp::wrap_add, index, carry, span);
      ir::Value* at_end = builder.compare(ir::CmpPred::eq, next, lengths[at], span);
      ir::Value* over = builder.cast(ir::CastKind::zext, at_end, ir::u64(), span);
      ir::Value* back = builder.binary(ir::BinOp::wrap_mul, over, lengths[at], span);
      moved[at] = builder.binary(ir::BinOp::wrap_sub, next, back, span);
      carry = over;
    }
    return moved;
  }


  ir::Value* Checker::offset_at(ir::Builder& builder, std::span<ir::Value* const> indices,
                                std::span<ir::Value* const> strides, Span span)
  {
    ir::Value* offset = builder.int_const(ir::u64(), 0);
    for (std::size_t at = 0; at < indices.size() && at < strides.size(); ++at) {
      ir::Value* step = builder.binary(ir::BinOp::wrap_mul, indices[at], strides[at], span);
      offset = builder.binary(ir::BinOp::wrap_add, offset, step, span);
    }
    return offset;
  }


  ir::Value* Checker::checked_index(ir::Builder& builder, const ast::Expr* written, std::optional<std::uint64_t> along,
                                    ir::Value* length, Span span, bool one_past)
  {
    if (! ast::is<ast::IntLit>(written) && ! ast::is<ast::Ready>(written))
      // Worked out while compiling where it can be, so that it is checked
      // against the length there, as a number written down is.
      if (auto down = index_written_down(written))
        written = down;
    ir::Value* index = on_its_own(builder, written);
    const ir::Type* found = value_type_of(index);
    if (found == ir::error_type())
      return nullptr;
    auto integer = ir::as<ir::IntType>(found);
    if (integer == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_INDEX_NOT_A_NUMBER, written->span, { { "found", found->written() } });
      return nullptr;
    }
    auto literal = ast::as<ast::IntLit>(written);
    if (literal == nullptr && ! stands_for(integer->unit, idx_unit())) [[unlikely]] {
      // Which element is wanted is not a length, a count of seconds or a
      // number of apples.  A literal is whatever it is asked to be.
      diags.emit(D::LANG_UNIT_INDEX, written->span, { { "found", found->written() } });
      return nullptr;
    }
    if (literal != nullptr && along) {
      __int128 limit = static_cast<__int128>(*along) + (one_past ? 1 : 0);
      if (literal->value.too_large || literal->value.value < 0 || literal->value.value >= limit) [[unlikely]] {
        diags.emit(D::LANG_ARRAY_INDEX_OUTSIDE, written->span,
                   { { "index", ir::decimal(literal->value.value) }, { "length", *along } });
        return nullptr;
      }
      return builder.int_const(ir::u64(), literal->value.value);
    }
    ir::Value* wide = as_count(builder, index, integer, span);
    ir::Value* inside = builder.compare(one_past ? ir::CmpPred::ule : ir::CmpPred::ult, wide, length, span);
    builder.check(inside, "an index outside its array", target::statuses::OUT_OF_RANGE, span);
    return wide;
  }


  ir::Value* Checker::as_count(ir::Builder& builder, ir::Value* value, const ir::IntType* found, Span span)
  {
    if (found == ir::u64())
      return value;
    return builder.cast(found->is_signed ? ir::CastKind::sext : ir::CastKind::zext, value, ir::u64(), span);
  }


  ir::Value* Checker::offset_of(ir::Builder& builder, std::span<const ast::Expr* const> written,
                                const ir::ArrayType* ty, std::span<ir::Value* const> lengths,
                                std::span<ir::Value* const> strides, Span span)
  {
    if (written.size() > ty->rank()) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_WRONG_RANK, span, { { "given", written.size() }, { "wanted", ty->rank() } });
      return nullptr;
    }
    ir::Value* offset = nullptr;
    for (std::size_t at = 0; at < written.size(); ++at) {
      ir::Value* index = checked_index(builder, written[at], ty->shape[at], lengths[at], span);
      if (index == nullptr)
        return nullptr;
      ir::Value* step = builder.binary(ir::BinOp::wrap_mul, index, strides[at], span);
      offset = offset == nullptr ? step : builder.binary(ir::BinOp::wrap_add, offset, step, span);
    }
    if (offset == nullptr) [[unlikely]]
      throw InternalError("an offset of no indices");
    return offset;
  }


  ir::Value* Checker::by_row(ir::Builder& builder, ir::Value* offset, std::span<ir::Value* const> left, Span span)
  {
    for (auto along : left)
      offset = builder.binary(ir::BinOp::wrap_mul, offset, along, span);
    return offset;
  }


  const ir::ArrayType* Checker::row_type(const ir::ArrayType* ty, std::int64_t taken)
  {
    return ir::array_type(ty->element, { ty->shape.begin() + taken, ty->shape.end() });
  }


  ir::Value* Checker::row_at(ir::Builder& builder, ir::Value* start, const ir::ArrayType* ty,
                             std::span<ir::Value* const> lengths, std::span<ir::Value* const> strides,
                             ir::Value* offset, std::int64_t taken, Span span)
  {
    ir::Value* place = element_place(builder, start, ty->element, offset, span);
    const ir::ArrayType* row = row_type(ty, taken);
    if (row->fixed())
      // Only ever the dimensions a view's type says, which are the ones it
      // left as they were and so still one run.
      return builder.cast(ir::CastKind::bitcast, place, row, span);
    return view(builder, place, lengths.subspan(taken), strides.subspan(taken), row, span);
  }


  ir::Value* Checker::lower_element(ir::Builder& builder, const ast::Element* expr, const ir::Type* expected)
  {
    // The expression as written, which the index settled below may replace.
    const ast::Element* as_written = expr;
    if (ir::Value* found = paired(builder, expr->base, array_pair, expr->indices, expr->span, expected))
      return found;
    ir::Value* base = array_in_a_field(builder, expr->base, false);
    if (base == nullptr)
      base = lower_expr(builder, expr->base, nullptr);
    const ir::Type* ty = value_type_of(base);
    if (ty == ir::error_type())
      return undef(module, ir::error_type());
    if (auto tuple = ir::as<ir::TupleType>(ty))
      return lower_tuple_member(builder, expr, base, tuple, expected);
    if (auto list = ir::as<ir::ListType>(ty))
      return list_element(builder, expr, base, list, expected);
    auto array = ir::as<ir::ArrayType>(ty);
    if (array == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_AN_ARRAY, expr->base->span, { { "found", ty->written() } });
      return undef(module, ir::error_type());
    }
    if (auto held = borrowed_name(expr)) {
      const auto& [name, path] = *held;
      if ((! taking_a_reference || *taking_a_reference != name) && lent_out(name, expr->span, false, path))
        // An element of an array a `&mut` into is out -- the same element, or
        // one nothing here can tell from it.
        return undef(module, ir::error_type());
    }
    if (std::ranges::any_of(expr->indices, [](const ast::Expr* one) { return ast::is<ast::Range>(one); }))
      return lower_slice(builder, expr, base, array, expected);
    // A copy of the element with its one index replaced: Python's `replace`.
    auto with_index = [this](const ast::Element* of, const ast::Expr* index) {
      auto copy = syntax.copy(*of);
      copy->indices = syntax.list(std::vector<const ast::Expr*>{ index });
      return static_cast<const ast::Element*>(copy);
    };
    auto ready = [this](Span span, ir::Value* value) {
      auto made = syntax.make<ast::Ready>(span);
      made->value = value;
      return static_cast<const ast::Expr*>(made);
    };
    auto settled_index = [](const ast::Expr* one) {
      return ast::is<ast::Range>(one) || ast::is<ast::IntLit>(one) || ast::is<ast::Ready>(one);
    };
    if (pick_in == expr && expr->indices.size() == 1 && walks_per_place(expr->indices[0])) {
      if (pick_per_place(builder, expr, base, array))
        return undef(module, ir::void_type());
      auto walked = std::get_if<std::tuple<ir::Value*, const ir::ArrayType*, Mask>>(&pick_walked);
      if (walked == nullptr)
        return undef(module, ir::error_type());
      // Nothing in it is walked, so it is what it would have been anywhere:
      // worked out once, and the index or the mask it came to.
      auto mask_value = std::get_if<ir::Value*>(&std::get<2>(*walked));
      if (mask_value == nullptr) [[unlikely]]
        throw InternalError("a pick walked with no mask");
      ir::Value* picked_by = *mask_value;
      pick_walked = std::monostate{};
      if (value_type_of(picked_by) == ir::error_type())
        return undef(module, ir::error_type());
      expr = with_index(expr, ready(expr->indices[0]->span, picked_by));
      pick_in = expr;
    } else if (expr->indices.size() == 1 && ! settled_index(expr->indices[0])) {
      if (auto written = index_written_down(expr->indices[0])) {
        // Known while compiling, so it is the number it comes to and is
        // checked against the length as a number written down is.
        bool walking = pick_in == expr;
        expr = with_index(expr, written);
        if (walking)
          pick_in = expr;
      } else {
        // Lowered once here, so that what it is -- a mask or an index -- is
        // asked of one value and a mistake in it is reported once.
        ir::Value* index = on_its_own(builder, expr->indices[0]);
        if (value_type_of(index) == ir::error_type())
          return undef(module, ir::error_type());
        bool walking = pick_in == expr;
        expr = with_index(expr, ready(expr->indices[0]->span, index));
        if (walking)
          pick_in = expr;
      }
    }
    if (ir::Value* mask = mask_written(builder, expr)) {
      if (pick_in == expr) {
        // What a loop takes its turns from: walked where it stands, so nothing
        // is copied and no room is taken.
        pick_walked = std::tuple<ir::Value*, const ir::ArrayType*, Mask>(base, array, Mask(mask));
        return undef(module, ir::void_type());
      }
      return lower_picked(builder, expr, base, array, mask, expected);
    }
    auto [start, lengths] = shape_of(builder, base, array, expr->span);
    auto strides = strides_of(builder, base, array, expr->span);
    ir::Value* offset = offset_of(builder, expr->indices, array, lengths, strides, expr->span);
    if (offset == nullptr)
      return undef(module, ir::error_type());
    if (expr->indices.size() < array->rank()) {
      // Fewer indices than dimensions names a row rather than an element.
      ir::Value* row = row_at(builder, start, array, lengths, strides, offset,
                              static_cast<std::int64_t>(expr->indices.size()), expr->span);
      const ir::Type* found_ty = value_type_of(row);
      if (! accepts(expected, found_ty))
        report_mismatch(expr->span, found_ty, expected);
      return row;
    }
    ir::Value* value = loaded_or_in_place(builder, element_place(builder, start, array->element, offset, expr->span),
                                          as_written, expr->span);
    if (! accepts(expected, array->element))
      report_mismatch(expr->span, array->element, expected);
    return value;
  }


  ir::Value* Checker::mask_written(ir::Builder& builder, const ast::Element* expr)
  {
    if (expr->indices.size() != 1)
      return nullptr;
    const ast::Expr* written = expr->indices[0];
    if (ast::is<ast::Range>(written))
      return nullptr;
    ir::Value* value = on_its_own(builder, written);
    auto ty = ir::as<ir::ArrayType>(value_type_of(value));
    if (ty != nullptr && ty->element == ir::bool_type())
      return value;
    // Not a mask, so it was an index; what it is, is asked again where an
    // index is asked, which is where the message about it belongs.
    return nullptr;
  }


  namespace {

    // Whether a mask of type `held` says one truth value for each place of the
    // leading dimensions of `ty`.
    bool mask_fits(const ir::ArrayType* held, const ir::ArrayType* ty)
    {
      if (! held->fixed() || held->rank() > ty->rank())
        return false;
      return std::equal(held->shape.begin(), held->shape.end(), ty->shape.begin());
    }

    // How many places the dimensions from `first` to `last` of a fixed shape
    // have together.
    std::int64_t places_of(const ir::ArrayType* ty, std::size_t first, std::size_t last)
    {
      std::int64_t count = 1;
      for (std::size_t at = first; at < last; ++at)
        count *= static_cast<std::int64_t>(*ty->shape[at]);
      return count;
    }

  } // namespace


  ir::Value* Checker::lower_picked(ir::Builder& builder, const ast::Element* expr, ir::Value* base,
                                   const ir::ArrayType* ty, ir::Value* mask, const ir::Type* expected)
  {
    auto held = ir::as<ir::ArrayType>(value_type_of(mask));
    if (! ty->fixed()) [[unlikely]] {
      diags.emit(D::LANG_MASK_NEEDS_A_STATED_SHAPE, expr->base->span, { { "found", ty->written() } });
      return undef(module, ir::error_type());
    }
    if (! mask_fits(held, ty)) [[unlikely]] {
      diags.emit(D::LANG_MASK_WRONG_SHAPE, expr->indices[0]->span,
                 { { "found", held->written() }, { "wanted", ty->written() } });
      return undef(module, ir::error_type());
    }
    std::vector<std::optional<std::uint64_t>> kept(ty->shape.begin() + held->rank(), ty->shape.end());
    std::vector<std::optional<std::uint64_t>> answer_shape{ std::nullopt };
    answer_shape.insert(answer_shape.end(), kept.begin(), kept.end());
    const ir::ArrayType* answer = ir::array_type(ty->element, answer_shape);
    std::int64_t row = places_of(ty, held->rank(), ty->rank());
    std::int64_t picks = places_of(ty, 0, held->rank());
    ir::Value* start = std::get<0>(shape_of(builder, base, ty, expr->span));
    ir::Value* marks = std::get<0>(shape_of(builder, mask, held, expr->span));
    ir::Value* place = builder.frame(ty, expr->span);
    ir::Value* count = builder.int_const(ir::u64(), 0);
    for (std::int64_t at = 0; at < picks; ++at) {
      ir::Value* where = builder.binary(ir::BinOp::wrap_mul, count, builder.int_const(ir::u64(), row), expr->span);
      for (std::int64_t inside = 0; inside < row; ++inside) {
        ir::Value* taken = builder.load(
          element_place(builder, start, ty->element, builder.int_const(ir::u64(), at * row + inside), expr->span),
          expr->span);
        ir::Value* to = builder.binary(ir::BinOp::wrap_add, where, builder.int_const(ir::u64(), inside), expr->span);
        builder.store(element_place(builder, place, ty->element, to, expr->span), taken, expr->span);
      }
      ir::Value* picked = builder.load(
        element_place(builder, marks, ir::bool_type(), builder.int_const(ir::u64(), at), expr->span), expr->span);
      ir::Value* one = builder.cast(ir::CastKind::zext, picked, ir::u64(), expr->span);
      count = builder.binary(ir::BinOp::wrap_add, count, one, expr->span);
    }
    ir::Value* first = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(ty->element, true), expr->span);
    std::vector<ir::Value*> lengths{ count };
    for (auto along : kept)
      lengths.push_back(builder.int_const(ir::u64(), *along));
    ir::Value* made = dense_view(builder, first, lengths, answer, expr->span);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return undef(module, ir::error_type());
    }
    return made;
  }


  void Checker::assign_picked(ir::Builder& builder, const ast::ElementAssign* stmt, ir::Value* base,
                              const ir::ArrayType* ty, ir::Value* mask)
  {
    auto held = ir::as<ir::ArrayType>(value_type_of(mask));
    if (! ty->fixed()) [[unlikely]] {
      diags.emit(D::LANG_MASK_NEEDS_A_STATED_SHAPE, stmt->base->span, { { "found", ty->written() } });
      return;
    }
    if (! mask_fits(held, ty)) [[unlikely]] {
      diags.emit(D::LANG_MASK_WRONG_SHAPE, stmt->indices[0]->span,
                 { { "found", held->written() }, { "wanted", ty->written() } });
      return;
    }
    if (! ir::is<ir::IntType>(ty->element) && ! ir::is<ir::BoolType>(ty->element) && ! ir::is<ir::FloatType>(ty->element)
        && ty->element != ir::char_type()) [[unlikely]] {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, stmt->span,
                 { { "feature", "writing through a mask into an array of '" + ty->element->written() + "'" } });
      return;
    }
    auto [start, lengths] = shape_of(builder, base, ty, stmt->span);
    if (read_only(start)) [[unlikely]] {
      diags.emit(D::LANG_STATIC_CHANGES, stmt->base->span, { { "name", written_as(stmt->base) } });
      return;
    }
    if (! made_here(start))
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, stmt->span);
    ir::Value* marks = std::get<0>(shape_of(builder, mask, held, stmt->span));
    ir::Value* value = lower_into(builder, stmt->value, ty->element, stmt->span);
    if (value_type_of(value) == ir::error_type())
      return;
    std::int64_t row = places_of(ty, held->rank(), ty->rank());
    std::int64_t picks = places_of(ty, 0, held->rank());
    for (std::int64_t at = 0; at < picks; ++at) {
      ir::Value* picked = builder.load(
        element_place(builder, marks, ir::bool_type(), builder.int_const(ir::u64(), at), stmt->span), stmt->span);
      for (std::int64_t inside = 0; inside < row; ++inside) {
        ir::Value* where = element_place(builder, start, ty->element, builder.int_const(ir::u64(), at * row + inside),
                                         stmt->span);
        ir::Value* was = builder.load(where, stmt->span);
        builder.store(where, builder.select(picked, value, was, stmt->span), stmt->span);
      }
    }
  }


  ir::Value* Checker::lower_tuple_member(ir::Builder& builder, const ast::Element* expr, ir::Value* base,
                                         const ir::TupleType* ty, const ir::Type* expected)
  {
    if (expr->indices.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_TUPLE_ONE_INDEX, expr->span, { { "given", expr->indices.size() } });
      return undef(module, ir::error_type());
    }
    KnownNumber at = constant_number(expr->indices[0]);
    if (at.refused)
      return undef(module, ir::error_type());
    if (! at.value) [[unlikely]] {
      diags.emit(D::LANG_TUPLE_INDEX_NOT_CONSTANT, expr->indices[0]->span);
      return undef(module, ir::error_type());
    }
    if (*at.value < 0 || *at.value >= static_cast<__int128>(ty->members.size())) [[unlikely]] {
      diags.emit(D::LANG_TUPLE_INDEX_OUTSIDE, expr->indices[0]->span,
                 { { "index", ir::decimal(*at.value) }, { "count", ty->members.size() } });
      return undef(module, ir::error_type());
    }
    const ir::Type* member_ty = ty->members[static_cast<std::size_t>(*at.value)];
    if (! accepts(expected, member_ty))
      report_mismatch(expr->span, member_ty, expected);
    // Where that member's own parts begin among the tuple's, and how many of
    // them there are: a member is one value to the program and may be several
    // to the machine.
    return member(builder, base, ty, static_cast<std::int64_t>(*at.value), expr->span);
  }


  std::optional<Folded> Checker::constant(const ast::Expr* expr)
  {
    switch (expr->tag) {
    case ast::NodeKind::IntLit: {
      auto literal = static_cast<const ast::IntLit*>(expr);
      if (! literal->type_name)
        return Folded{ .value = literal->value.value, .too_large = literal->value.too_large };
      auto ty = ir::as<ir::IntType>(ir::builtin_type(*literal->type_name));
      if (ty == nullptr || literal->value.too_large || ! ty->holds(literal->value.value))
        return std::nullopt;
      return Folded{ .value = literal->value.value, .ty = ty };
    }
    case ast::NodeKind::NameRef: {
      auto ref = static_cast<const ast::NameRef*>(expr);
      if (find_local(ref->name) != nullptr)
        return std::nullopt;
      const Held* found = top->get(std::string(ref->name));
      if (auto var = found != nullptr ? found->get<ir::GlobalVar>() : nullptr) {
        if (image_waits(ref->name))
          // A number read where it may be one the compiler knows.
          image_now(ref->name);
        if (var->is_mutable() || var->initializer == nullptr || var->initializer->vkind != ir::ValueKind::int_const)
          return std::nullopt;
        auto initial = static_cast<const ir::IntConst*>(var->initializer);
        return Folded{ .value = initial->value, .ty = ir::as<ir::IntType>(initial->ty) };
      }
      return constant_not_yet_defined(ref->name);
    }
    case ast::NodeKind::Unary: {
      auto unary = static_cast<const ast::Unary*>(expr);
      if (unary->op == ast::UnaryOp::length)
        return folded_length(unary);
      if (unary->op != ast::UnaryOp::bit_not)
        return std::nullopt;
      auto inner = constant(unary->operand);
      if (! inner || is_refused(inner) || inner->ty == nullptr || inner->failed)
        return is_refused(inner) ? inner : std::nullopt;
      return Folded{ .value = within(~inner->value, inner->ty), .ty = inner->ty };
    }
    case ast::NodeKind::Raised: {
      auto raised = static_cast<const ast::Raised*>(expr);
      auto inner = constant(raised->base);
      if (! inner || is_refused(inner) || inner->failed)
        return is_refused(inner) ? inner : std::nullopt;
      return folded_power(expr, inner->value, raised->exponent.value, inner->ty);
    }
    case ast::NodeKind::Try: {
      auto inner = constant(static_cast<const ast::Try*>(expr)->operand);
      if (inner && ! is_refused(inner) && inner->failed)
        return refuse(D::LANG_CONSTANT_HAS_NO_ANSWER, expr->span);
      return inner;
    }
    case ast::NodeKind::Binary:
      return folded_binary(static_cast<const ast::Binary*>(expr));
    case ast::NodeKind::Call:
      return folded_call(static_cast<const ast::Call*>(expr));
    default:
      return std::nullopt;
    }
  }

} // namespace pl4g::sema
