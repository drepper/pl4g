// Which names are still to be used, at each statement of a function: a port
// of pypl4g's sema/liveness.py.
#include "sema/liveness.hh"

#include <ranges>
#include <utility>

#include "sema/walk.hh"


namespace pl4g::sema {

  namespace {

    constexpr std::string_view wildcard_name = "_";

    const Bindings no_bindings;

    // The union of everything `sets` holds.
    template<typename T>
    FlatSet<T> union_of(const std::vector<FlatSet<T>>& sets)
    {
      FlatSet<T> out;
      for (const auto& one : sets)
        out |= one;
      return out;
    }

  } // anonymous namespace


  // -- entry points -------------------------------------------------------------

  template<typename P>
  void Liveness::start(const P* node)
  {
    scopes.assign(1, {});
    for (auto param : node->params)
      bind(param->name, param->span);
    resolve(node->body, false);
    scopes.clear();
    block(node->body, {});
    std::vector<LiveValue> given;
    for (auto param : node->params)
      if (param->span.is_valid())
        given.emplace_back(param->span.start, 0);
    forward(node->body, LiveValues(std::move(given)), {});
  }


  void Liveness::function(const ast::FuncDef* node)
  {
    if (node->body == nullptr || node->is_macro)
      return;
    start(node);
  }


  void Liveness::lambda_(const ast::Lambda* node)
  {
    start(node);
  }


  // -- what a name is -----------------------------------------------------------

  std::optional<Binding> Liveness::bind(std::string_view name, Span at)
  {
    if (name == wildcard_name || ! at.is_valid())
      return std::nullopt;
    scopes.back()[name] = at.start;
    return at.start;
  }


  std::optional<Binding> Liveness::find(std::string_view name) const
  {
    for (const auto& scope : scopes | std::views::reverse)
      if (auto found = scope.find(name); found != scope.end())
        return found->second;
    return std::nullopt;
  }


  void Liveness::use(const void* key, std::string_view name)
  {
    if (auto found = find(name))
      refs[key] = *found;
  }


  const Bindings& Liveness::defs(const ast::Node* node) const
  {
    auto found = defs_of.find(node);
    return found != defs_of.end() ? found->second : no_bindings;
  }


  // Walk forward, saying what each name resolves to and what each statement
  // binds.
  void Liveness::resolve(const ast::Node* node, bool scoped)
  {
    if (node == nullptr)
      return;
    auto bound_by = [&](std::span<const std::optional<Binding>> bound) {
      std::vector<Binding> kept;
      for (auto one : bound)
        if (one)
          kept.push_back(*one);
      return Bindings(std::move(kept));
    };
    auto on_name = [&](const ast::NameAt* name) {
      // An arena named where something is made in it.
      use(name, name->name);
    };
    switch (node->tag) {
    case ast::NodeKind::Block:
      if (scoped)
        scopes.emplace_back();
      for (auto one : static_cast<const ast::Block*>(node)->stmts())
        resolve(one);
      if (scoped)
        scopes.pop_back();
      break;
    case ast::NodeKind::VarDef: {
      auto def = static_cast<const ast::VarDef*>(node);
      resolve(def->value);
      resolve(def->type);
      if (def->made_in)
        use(def, def->made_in->name);
      std::vector<std::optional<Binding>> bound{ bind(def->name, def->name_span) };
      for (const auto& more : def->more)
        bound.push_back(bind(more.name, more.span));
      defs_of[def] = bound_by(bound);
      break;
    }
    case ast::NodeKind::AssignStmt: {
      auto assign = static_cast<const ast::AssignStmt*>(node);
      resolve(assign->value);
      std::vector<std::optional<Binding>> written{ find(assign->name) };
      for (const auto& more : assign->more)
        written.push_back(find(more.name));
      defs_of[assign] = bound_by(written);
      break;
    }
    case ast::NodeKind::ForEach: {
      auto loop = static_cast<const ast::ForEach*>(node);
      resolve(loop->iterable);
      resolve(loop->type);
      scopes.emplace_back();
      std::vector<std::optional<Binding>> bound{ bind(loop->name, loop->name_span) };
      for (const auto& more : loop->more)
        bound.push_back(bind(more.name, more.span));
      defs_of[loop] = bound_by(bound);
      resolve(loop->body, false);
      scopes.pop_back();
      resolve(loop->alternative);
      break;
    }
    case ast::NodeKind::Match: {
      auto match = static_cast<const ast::Match*>(node);
      resolve(match->subject);
      for (auto arm : match->arms) {
        scopes.emplace_back();
        resolve(arm->pattern->type);
        std::optional<Binding> bound;
        if (arm->pattern->name && ! arm->pattern->wildcard)
          bound = bind(*arm->pattern->name, arm->pattern->name_span);
        defs_of[arm] = bound ? Bindings{ *bound } : Bindings{};
        resolve(arm->body, false);
        scopes.pop_back();
      }
      break;
    }
    case ast::NodeKind::Lambda: {
      auto lambda = static_cast<const ast::Lambda*>(node);
      for (auto capture : lambda->captures)
        use(capture, capture->name);
      // Its body is a function of its own, worked out when it is lowered;
      // what it uses from here is what it brings in.
      resolve_free(lambda->body);
      break;
    }
    case ast::NodeKind::NameRef:
      use(node, static_cast<const ast::NameRef*>(node)->name);
      break;
    case ast::NodeKind::Capture:
      use(node, static_cast<const ast::Capture*>(node)->name);
      break;
    default:
      walk_fields(node, [&](const ast::Node* one) { resolve(one); }, on_name);
      break;
    }
  }


  // Resolve what a lambda's body names of what is around it, binding nothing of
  // the lambda's own over it.
  void Liveness::resolve_free(const ast::Node* node)
  {
    if (node == nullptr)
      return;
    if (auto ref = ast::as<ast::NameRef>(node))
      use(node, ref->name);
    else if (auto capture = ast::as<ast::Capture>(node))
      use(node, capture->name);
    else
      walk_fields(node, [&](const ast::Node* one) { resolve_free(one); }, [](const ast::NameAt*) {});
  }


  // -- what a piece of the tree uses ----------------------------------------------

  void Liveness::known(const void* key, std::vector<Binding>& into) const
  {
    if (auto found = refs.find(key); found != refs.end())
      into.push_back(found->second);
  }


  // Every binding named anywhere in `node`.
  Bindings Liveness::used(const ast::Node* node)
  {
    if (node == nullptr)
      return {};
    if (auto found = uses.find(node); found != uses.end())
      return found->second;
    std::vector<Binding> found;
    gather(node, found, true);
    return uses[node] = Bindings(std::move(found));
  }


  void Liveness::gather(const ast::Node* node, std::vector<Binding>& into, bool deep)
  {
    known(node, into);
    if (ast::is<ast::Block>(node) && ! deep)
      return;
    if (ast::is<ast::Lambda>(node))
      // Used where the lambda is, whatever its body does with it.
      gather_all(node, into);
    else
      walk_fields(node, [&](const ast::Node* one) { gather(one, into, deep); },
                  [&](const ast::NameAt* name) { known(name, into); });
  }


  void Liveness::gather_all(const ast::Node* node, std::vector<Binding>& into)
  {
    known(node, into);
    walk_fields(node, [&](const ast::Node* one) { gather_all(one, into); },
                [&](const ast::NameAt* name) { known(name, into); });
  }


  // -- backwards ------------------------------------------------------------------

  // What is live as `block` begins, given what is live once it is left.
  Bindings Liveness::block(const ast::Block* block, const Bindings& out)
  {
    auto stmts = block->stmts();
    Bindings put_off;
    for (auto one : stmts)
      if (auto defer = ast::as<ast::Defer>(one))
        put_off |= used(defer->stmt);
    deferred.push_back(put_off);
    Bindings live = out | put_off;
    for (auto stmt : stmts | std::views::reverse) {
      Bindings after = live;
      live = this->stmt(stmt, after);
      live_out[stmt] = after;
      live_in[stmt] = live;
    }
    deferred.pop_back();
    return live;
  }


  // What is live where a `return` goes: what every block it leaves put off.
  Bindings Liveness::leaving() const
  {
    return union_of(deferred);
  }


  Bindings Liveness::stmt(const ast::Stmt* stmt, const Bindings& out)
  {
    switch (stmt->tag) {
    case ast::NodeKind::VarDef: {
      auto def = static_cast<const ast::VarDef*>(stmt);
      Bindings live = expr(def->value, out - defs(def)) | used(def->type);
      if (auto found = refs.find(def); found != refs.end())
        live.insert(found->second);
      return live;
    }
    case ast::NodeKind::AssignStmt: {
      auto assign = static_cast<const ast::AssignStmt*>(stmt);
      return expr(assign->value, out - defs(assign));
    }
    case ast::NodeKind::ReturnStmt: {
      auto ret = static_cast<const ast::ReturnStmt*>(stmt);
      return ret->value != nullptr ? expr(ret->value, leaving()) : leaving();
    }
    case ast::NodeKind::Break: {
      auto brk = static_cast<const ast::Break*>(stmt);
      auto loop = loop_for(brk->label);
      return (loop != nullptr ? loop->after : out) | used(brk->value);
    }
    case ast::NodeKind::Continue: {
      auto loop = loop_for(static_cast<const ast::Continue*>(stmt)->label);
      return loop != nullptr ? loop->head : out;
    }
    case ast::NodeKind::Defer:
      // What it uses is live from here to where the block is left, which the
      // block already said.
      return out;
    case ast::NodeKind::ExprStmt:
      return expr(static_cast<const ast::ExprStmt*>(stmt)->value, out);
    default:
      return out | used(stmt);
    }
  }


  const Liveness::Loop* Liveness::loop_for(const ast::Label* label) const
  {
    if (label == nullptr)
      return loops.empty() ? nullptr : &loops.back();
    for (const auto& loop : loops | std::views::reverse)
      if (loop.label == label->name)
        return &loop;
    return nullptr;
  }


  // What is live before `expr` is worked out, given what is live after.
  Bindings Liveness::expr(const ast::Node* expr, const Bindings& out)
  {
    if (expr == nullptr)
      return out;
    switch (expr->tag) {
    case ast::NodeKind::If: {
      auto chosen = static_cast<const ast::If*>(expr);
      std::optional<Bindings> following;
      if (chosen->arms.back()->condition != nullptr)
        following = out;
      for (auto arm : chosen->arms | std::views::reverse) {
        Bindings body = block(arm->body, out);
        if (arm->condition == nullptr) {
          following = std::move(body);
          continue;
        }
        following = used(arm->condition) | body | *following;
      }
      return following ? *following : out;
    }
    case ast::NodeKind::Match: {
      auto match = static_cast<const ast::Match*>(expr);
      Bindings live;
      for (auto arm : match->arms)
        live = live | (block(arm->body, out) - defs(arm)) | used(arm->pattern->type);
      return used(match->subject) | live;
    }
    case ast::NodeKind::While: {
      auto loop = static_cast<const ast::While*>(expr);
      return this->loop(loop, loop->body, loop->alternative, loop->label, out, used(loop->condition), {});
    }
    case ast::NodeKind::ForEach: {
      auto loop = static_cast<const ast::ForEach*>(expr);
      bool again = loop->keyword == "while";
      Bindings kept = held(loop->iterable);
      if (again)
        return this->loop(loop, loop->body, loop->alternative, loop->label, out, used(loop->iterable), defs(loop));
      Bindings walked = used(loop->iterable);
      return walked | this->loop(loop, loop->body, loop->alternative, loop->label, out, kept, defs(loop));
    }
    case ast::NodeKind::Scoped:
      return block(static_cast<const ast::Scoped*>(expr)->body, out);
    default: {
      // Anything else: what it names, and whatever is inside it that holds
      // statements of its own, worked out so that they have answers too.
      Bindings live = out | used(expr);
      nested(expr, out);
      return live;
    }
    }
  }


  // Give the statements inside an expression their answers.
  void Liveness::nested(const ast::Node* node, const Bindings& out)
  {
    if (node == nullptr)
      return;
    switch (node->tag) {
    case ast::NodeKind::If:
    case ast::NodeKind::Match:
    case ast::NodeKind::While:
    case ast::NodeKind::ForEach:
    case ast::NodeKind::Scoped:
      expr(node, out);
      break;
    case ast::NodeKind::Lambda:
      break;
    default:
      walk_fields(node, [&](const ast::Node* one) { nested(one, out); }, [](const ast::NameAt*) {});
      break;
    }
  }


  // What a `foreach` keeps for the whole loop: what it walks, but not the
  // bounds of a range, which are worked out once.
  Bindings Liveness::held(const ast::Expr* iterable) const
  {
    if (ast::is<ast::Range>(iterable))
      return {};
    std::vector<Binding> found;
    auto walk = [&](this auto& self, const ast::Node* node) -> void {
      known(node, found);
      if (ast::is<ast::Range>(node))
        return;
      walk_fields(node, self, [&](const ast::NameAt* name) { known(name, found); });
    };
    if (iterable != nullptr)
      walk(iterable);
    return Bindings(std::move(found));
  }


  // What is live as a loop begins: worked to a fixed point, since what a later
  // turn uses is live on this one.
  Bindings Liveness::loop(const ast::Node*, const ast::Block* body, const ast::Block* alternative,
                          const ast::Label* label, const Bindings& out, const Bindings& asked, const Bindings& bound)
  {
    Bindings ran_out = alternative != nullptr ? block(alternative, out) : out;
    std::optional<std::string_view> name;
    if (label != nullptr)
      name = label->name;
    Bindings live = asked | ran_out;
    while (true) {
      loops.push_back(Loop{ name, out, live });
      Bindings turn = block(body, live) - bound;
      loops.pop_back();
      Bindings again = asked | turn | ran_out;
      if (again.subset_of(live))
        return live;
      live |= again;
    }
  }


  // -- forwards: the values a name holds, and where each stops counting ----------

  // Walk `block` forward from the values reaching it and those counting, and
  // answer both on the way out, less what the block bound.
  Liveness::Way Liveness::forward(const ast::Block* block, LiveValues reach, LiveValues active)
  {
    Bindings bound;
    for (auto stmt : block->stmts()) {
      std::tie(reach, active) = enter(stmt, std::move(reach), active);
      std::tie(reach, active) = through(stmt, std::move(reach), std::move(active));
      if (ast::is<ast::VarDef>(stmt))
        bound |= defs(stmt);
    }
    auto inside = [&](const LiveValue& one) { return bound.contains(one.first); };
    auto outside = [&](const LiveValue& one) { return ! bound.contains(one.first); };
    if (noting && ! bound.empty())
      if (LiveValues kept = active.filter(inside); ! kept.empty())
        exit_ends[block] |= kept;
    return { reach.filter(outside), active.filter(outside) };
  }


  // What counts as `stmt` begins: what reaches it whose name is live.
  Liveness::Way Liveness::enter(const ast::Stmt* stmt, LiveValues reach, const LiveValues& active)
  {
    auto found_live = live_in.find(stmt);
    const Bindings& live = found_live != live_in.end() ? found_live->second : no_bindings;
    LiveValues now = reach.filter([&](const LiveValue& one) { return live.contains(one.first); });
    if (noting) {
      reach_in[stmt] |= reach;
      if (LiveValues gone = active - now; ! gone.empty()) {
        const Bindings& given = ast::is<ast::VarDef>(stmt) || ast::is<ast::AssignStmt>(stmt) ? defs(stmt)
                                                                                             : no_bindings;
        auto& found = ends[stmt];
        for (const auto& one : gone)
          found.try_emplace(one, given.contains(one.first) ? "given" : "unused");
      }
    }
    return { std::move(reach), std::move(now) };
  }


  // The values once `stmt` has given its names new ones: the old ones no
  // longer reach, and one still counting stops counting once it is done.
  Liveness::Way Liveness::gives(const ast::Stmt* stmt, LiveValues reach, LiveValues active)
  {
    const Bindings& written = defs(stmt);
    if (written.empty())
      return { std::move(reach), std::move(active) };
    auto mine = [&](const LiveValue& one) { return written.contains(one.first); };
    auto not_mine = [&](const LiveValue& one) { return ! written.contains(one.first); };
    if (LiveValues old = active.filter(mine); ! old.empty() && noting && ast::is<ast::AssignStmt>(stmt))
      ends_after[stmt] |= old;
    std::vector<LiveValue> fresh;
    for (auto one : written)
      fresh.emplace_back(one, identity(stmt));
    LiveValues made(std::move(fresh));
    return { reach.filter(not_mine) | made, active.filter(not_mine) | made };
  }


  // The values on the way out of one statement, from those on the way in.
  Liveness::Way Liveness::through(const ast::Node* node, LiveValues reach, LiveValues active)
  {
    if (node == nullptr)
      return { std::move(reach), std::move(active) };
    switch (node->tag) {
    case ast::NodeKind::ReturnStmt: {
      auto ret = static_cast<const ast::ReturnStmt*>(node);
      if (ret->value != nullptr)
        through(ret->value, reach, active);
      if (noting && ! active.empty())
        leave_ends[node] = active;
      return {};
    }
    case ast::NodeKind::Break:
    case ast::NodeKind::Continue:
      if (noting && ! turns.empty())
        if (LiveValues left = active.filter([&](const LiveValue& one) { return turns.back().contains(one.first); });
            ! left.empty())
          leave_ends[node] = left;
      return {};
    case ast::NodeKind::VarDef: {
      auto def = static_cast<const ast::VarDef*>(node);
      std::tie(reach, active) = through(def->value, std::move(reach), std::move(active));
      return gives(def, std::move(reach), std::move(active));
    }
    case ast::NodeKind::AssignStmt: {
      auto assign = static_cast<const ast::AssignStmt*>(node);
      std::tie(reach, active) = through(assign->value, std::move(reach), std::move(active));
      return gives(assign, std::move(reach), std::move(active));
    }
    case ast::NodeKind::ExprStmt:
      return through(static_cast<const ast::ExprStmt*>(node)->value, std::move(reach), std::move(active));
    case ast::NodeKind::If: {
      auto chosen = static_cast<const ast::If*>(node);
      Way out;
      for (auto arm : chosen->arms) {
        auto [r, a] = forward(arm->body, reach, active);
        out.first |= r;
        out.second |= a;
      }
      if (chosen->arms.back()->condition != nullptr) {
        out.first |= reach;
        out.second |= active;
      }
      return out;
    }
    case ast::NodeKind::Match: {
      Way out;
      for (auto arm : static_cast<const ast::Match*>(node)->arms) {
        std::vector<LiveValue> turned;
        for (auto one : defs(arm))
          turned.emplace_back(one, identity(arm));
        LiveValues turn(std::move(turned));
        auto [r, a] = forward(arm->body, reach | turn, active | turn);
        out.first |= r - turn;
        out.second |= a - turn;
      }
      return out;
    }
    case ast::NodeKind::While:
    case ast::NodeKind::ForEach:
      return loop_forward(node, std::move(reach), std::move(active));
    case ast::NodeKind::Scoped:
      return forward(static_cast<const ast::Scoped*>(node)->body, std::move(reach), std::move(active));
    default:
      return { std::move(reach), std::move(active) };
    }
  }


  // A loop forward: to a fixed point first, since what a turn gives a name
  // reaches the next turn, and then once more writing down what it finds.
  Liveness::Way Liveness::loop_forward(const ast::Node* node, LiveValues reach, LiveValues active)
  {
    const ast::Block* body;
    const ast::Block* alternative;
    Bindings turn_names;
    if (auto loop = ast::as<ast::ForEach>(node)) {
      body = loop->body;
      alternative = loop->alternative;
      turn_names = defs(loop);
    } else {
      auto plain = static_cast<const ast::While*>(node);
      body = plain->body;
      alternative = plain->alternative;
    }
    std::vector<LiveValue> turned;
    for (auto one : turn_names)
      turned.emplace_back(one, identity(node));
    LiveValues turn(std::move(turned));
    LiveValues head_reach = std::move(reach);
    LiveValues head_active = std::move(active);
    bool was_noting = std::exchange(noting, false);
    while (true) {
      turns.push_back(turn_names);
      auto [out, counting] = forward(body, head_reach | turn, head_active | turn);
      turns.pop_back();
      LiveValues more_reach = head_reach | (out - turn);
      LiveValues more_active = head_active | (counting - turn);
      if (more_reach == head_reach && more_active == head_active)
        break;
      head_reach = std::move(more_reach);
      head_active = std::move(more_active);
    }
    noting = was_noting;
    turns.push_back(turn_names);
    forward(body, head_reach | turn, head_active | turn);
    turns.pop_back();
    if (alternative != nullptr)
      forward(alternative, head_reach, head_active);
    return { std::move(head_reach), std::move(head_active) };
  }

} // namespace pl4g::sema
