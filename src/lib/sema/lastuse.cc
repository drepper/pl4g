// Where each variable's scope ends: a port of pypl4g's sema/lastuse.py.
#include "sema/lastuse.hh"

#include <algorithm>
#include <ranges>
#include <utility>

#include "sema/walk.hh"
#include "util/unicode.hh"


namespace pl4g::sema {

  namespace {

    constexpr std::string_view wildcard_name = "_";

    // Whether `node` is one of the constructs whose text ends with a block.
    bool ends_with_a_block(const ast::Node* node, bool lambda)
    {
      if (node == nullptr)
        return false;
      switch (node->tag) {
      case ast::NodeKind::If:
      case ast::NodeKind::Match:
      case ast::NodeKind::Scoped:
      case ast::NodeKind::ForEach:
      case ast::NodeKind::While:
        return true;
      case ast::NodeKind::Lambda:
        return lambda;
      default:
        return false;
      }
    }

    // What a statement holding an expression holds, or nothing.
    const ast::Expr* value_of(const ast::Node* node)
    {
      switch (node->tag) {
      case ast::NodeKind::ExprStmt:
        return static_cast<const ast::ExprStmt*>(node)->value;
      case ast::NodeKind::ReturnStmt:
        return static_cast<const ast::ReturnStmt*>(node)->value;
      case ast::NodeKind::VarDef:
        return static_cast<const ast::VarDef*>(node)->value;
      case ast::NodeKind::AssignStmt:
        return static_cast<const ast::AssignStmt*>(node)->value;
      default:
        return nullptr;
      }
    }

  } // anonymous namespace


  Loc end_of(const ast::Node* node)
  {
    if (node == nullptr)
      return no_loc;
    switch (node->tag) {
    case ast::NodeKind::Block: {
      auto stmts = static_cast<const ast::Block*>(node)->stmts();
      return stmts.empty() ? node->span.end : end_of(stmts.back());
    }
    case ast::NodeKind::ExprStmt:
    case ast::NodeKind::ReturnStmt:
    case ast::NodeKind::VarDef:
    case ast::NodeKind::AssignStmt: {
      auto value = value_of(node);
      return ends_with_a_block(value, true) ? end_of(value) : node->span.end;
    }
    case ast::NodeKind::Defer:
      return end_of(static_cast<const ast::Defer*>(node)->stmt);
    case ast::NodeKind::If:
      return end_of(static_cast<const ast::If*>(node)->arms.back()->body);
    case ast::NodeKind::Match: {
      auto match = static_cast<const ast::Match*>(node);
      return match->arms.empty() ? node->span.end : end_of(match->arms.back()->body);
    }
    case ast::NodeKind::ForEach: {
      auto loop = static_cast<const ast::ForEach*>(node);
      return end_of(loop->alternative != nullptr ? loop->alternative : loop->body);
    }
    case ast::NodeKind::While: {
      auto loop = static_cast<const ast::While*>(node);
      return end_of(loop->alternative != nullptr ? loop->alternative : loop->body);
    }
    case ast::NodeKind::Scoped:
      return end_of(static_cast<const ast::Scoped*>(node)->body);
    case ast::NodeKind::Lambda:
      return end_of(static_cast<const ast::Lambda*>(node)->body);
    default:
      return node->span.end;
    }
  }


  bool leaves(const ast::Block* block)
  {
    auto stmts = block->stmts();
    if (stmts.empty())
      return false;
    auto last = stmts.back();
    switch (last->tag) {
    case ast::NodeKind::ReturnStmt:
    case ast::NodeKind::Break:
    case ast::NodeKind::Continue:
      return true;
    case ast::NodeKind::ExprStmt: {
      auto value = static_cast<const ast::ExprStmt*>(last)->value;
      if (auto chosen = ast::as<ast::If>(value))
        return chosen->arms.back()->condition == nullptr
               && std::ranges::all_of(chosen->arms, [](auto arm) { return leaves(arm->body); });
      if (auto chosen = ast::as<ast::Match>(value))
        return ! chosen->arms.empty()
               && std::ranges::all_of(chosen->arms, [](auto arm) { return leaves(arm->body); });
      return false;
    }
    default:
      return false;
    }
  }


  Span line_after(const SourceManager& sources, Loc end, Loc column_of)
  {
    auto source = sources.file_of(std::max<Loc>(end - 1, 0));
    if (source == nullptr)
      return { end, end };
    const std::string& text = source->text;
    const auto& starts = source->line_starts;
    auto offset = std::uint32_t(std::max<Loc>(end - 1 - source->base, 0));
    auto index = std::size_t(std::ranges::upper_bound(starts, offset) - starts.begin());
    std::size_t at;
    if (index >= starts.size())
      at = text.size();
    else {
      at = starts[index];
      while (at < text.size() && (text[at] == ' ' || text[at] == '\t'))
        ++at;
      if (at < text.size() && text[at] == '\n')
        at = starts[index];
      if (column_of != no_loc && sources.file_of(column_of) == source) {
        // The column in characters, as pypl4g counts it, carried to the line
        // as far as it reaches.
        auto there = std::uint32_t(column_of - source->base);
        auto own = *(std::ranges::upper_bound(starts, there) - 1);
        std::size_t column = unicode::length(std::string_view(text).substr(own, there - own));
        std::size_t begins = starts[index];
        auto stop = text.find('\n', begins);
        if (stop == std::string::npos)
          stop = text.size();
        at = begins;
        for (; column > 0 && at < stop; --column)
          at += std::max(1u, unicode::decode(text, at).length);
        at = std::min(at, stop);
      }
    }
    return { source->base + Loc(at), source->base + Loc(at) };
  }


  // Whether `span` is in the function's own text, and not a macro's.
  bool LastUses::ours(Span span) const
  {
    return span.is_valid() && file != nullptr && file->base <= span.start && span.start <= file->end();
  }


  // -- the walk ---------------------------------------------------------------

  void LastUses::function(const ast::FuncDef* node)
  {
    if (node->body == nullptr || node->is_macro || ! node->span.is_valid())
      return;
    file = sources.file_of(node->span.start);
    scopes.emplace_back();
    for (auto param : node->params) {
      if (param->default_ != nullptr) {
        stmt_end = param->span.end;
        expr(param->default_);
      }
      bind(param->name, param->span, param->span.end);
    }
    for (auto clause : node->clauses) {
      stmt_end = clause->span.end;
      expr(clause->expr);
    }
    block(node->body, false);
    pop();
  }


  void LastUses::block(const ast::Block* block, bool scoped)
  {
    if (scoped)
      scopes.emplace_back();
    blocks.push_back(ours(block->span) ? end_of(block) : (! blocks.empty() ? blocks.back() : stmt_end));
    for (auto one : block->stmts())
      stmt(one);
    blocks.pop_back();
    if (scoped)
      pop();
  }


  void LastUses::stmt(const ast::Stmt* stmt)
  {
    if (ours(stmt->span)) {
      bool holds = ast::is<ast::ExprStmt>(stmt) || ast::is<ast::ReturnStmt>(stmt);
      stmt_end = ! holds || ! ends_with_a_block(value_of(stmt), false) ? end_of(stmt) : stmt->span.start;
    }
    switch (stmt->tag) {
    case ast::NodeKind::VarDef: {
      auto def = static_cast<const ast::VarDef*>(stmt);
      expr(def->value);
      if (def->type != nullptr)
        expr(def->type);
      if (def->made_in)
        use(def->made_in->name, def->made_in->span);
      if (ours(def->span))
        stmt_end = end_of(def);
      bind(def->name, def->name_span, stmt_end);
      for (const auto& more : def->more)
        bind(more.name, more.span, stmt_end);
      break;
    }
    case ast::NodeKind::AssignStmt: {
      auto assign = static_cast<const ast::AssignStmt*>(stmt);
      expr(assign->value);
      if (ours(assign->span))
        stmt_end = end_of(assign);
      use(assign->name, assign->name_span);
      for (const auto& more : assign->more)
        use(more.name, more.span);
      break;
    }
    case ast::NodeKind::Defer: {
      auto defer = static_cast<const ast::Defer*>(stmt);
      auto outer = deferred;
      if (ours(defer->span))
        deferred = std::pair{ blocks.back(), defer->span };
      this->stmt(defer->stmt);
      deferred = outer;
      break;
    }
    default:
      fields(stmt);
      break;
    }
  }


  void LastUses::expr(const ast::Node* node)
  {
    if (node == nullptr)
      return;
    switch (node->tag) {
    case ast::NodeKind::NameRef:
      use(static_cast<const ast::NameRef*>(node)->name, node->span);
      return;
    case ast::NodeKind::Capture:
      use(static_cast<const ast::Capture*>(node)->name, node->span);
      return;
    case ast::NodeKind::Member:
      expr(static_cast<const ast::Member*>(node)->base);
      return;
    case ast::NodeKind::Named:
      expr(static_cast<const ast::Named*>(node)->value);
      return;
    case ast::NodeKind::Block:
      block(static_cast<const ast::Block*>(node));
      return;
    case ast::NodeKind::If:
      if_(static_cast<const ast::If*>(node));
      return;
    case ast::NodeKind::Match:
      match(static_cast<const ast::Match*>(node));
      return;
    case ast::NodeKind::While:
      while_(static_cast<const ast::While*>(node));
      return;
    case ast::NodeKind::ForEach:
      for_each(static_cast<const ast::ForEach*>(node));
      return;
    case ast::NodeKind::Range:
      if (held_until != no_loc) {
        // The bounds of a range are worked out once, before the loop: the
        // loop keeps the range, and not what it was made of.
        auto outer = std::exchange(held_until, no_loc);
        fields(node);
        held_until = outer;
        return;
      }
      break;
    case ast::NodeKind::Lambda: {
      auto lambda = static_cast<const ast::Lambda*>(node);
      for (auto capture : lambda->captures)
        use(capture->name, capture->span);
      for (const auto& where : { lambda->made_in, lambda->kept_in })
        if (where)
          use(where->name, where->span);
      scopes.emplace_back();
      for (auto param : lambda->params)
        bind(param->name, param->span, param->span.end);
      block(lambda->body, false);
      pop();
      return;
    }
    case ast::NodeKind::Label:
    case ast::NodeKind::Attribute:
      return;
    default:
      break;
    }
    if (is_stmt(node))
      stmt(static_cast<const ast::Stmt*>(node));
    else
      fields(node);
  }


  // Walk what an `if`, a `match` or a `while` asks about: a statement of its
  // own, as far as where a use in it ends goes.
  void LastUses::header(const ast::Expr* expr)
  {
    if (ours(expr->span))
      stmt_end = end_of(expr);
    this->expr(expr);
  }


  // Walk everything `node` holds, it being nothing that binds a name.
  void LastUses::fields(const ast::Node* node)
  {
    walk_fields(node, [&](const ast::Node* one) { expr(one); },
                // An arena named where something is made in it.
                [&](const ast::NameAt* name) { use(name->name, name->span); });
  }


  // -- the ways through -------------------------------------------------------

  LastUses::State LastUses::state() const
  {
    State out;
    for (auto [_, one] : live)
      out.emplace(one, Saved{ one->ends, one->held_to });
    return out;
  }


  void LastUses::restore(const State& state)
  {
    for (const auto& [one, saved] : state) {
      one->ends = saved.ends;
      one->held_to = saved.held_to;
    }
  }


  // An arm has been walked from `entry`: what it comes to is one of the ways
  // on, or -- where it leaves -- what it used ends in it, and so does what the
  // choice's own conditions were the last to use.
  void LastUses::arm_done(const ast::Block* body, const State& entry, const State& start, std::vector<Way>& ways,
                          Loc anchor)
  {
    if (! leaves(body)) {
      ways.push_back(Way{ entry, state(), anchor });
      return;
    }
    static const std::vector<End> none;
    for (const auto& [one, saved] : entry) {
      auto found = start.find(one);
      const auto& before = found != start.end() ? found->second.ends : none;
      for (End end : one->ends) {
        if (std::ranges::contains(saved.ends, end)) {
          if (std::ranges::contains(before, end) || ! below(anchor, end))
            continue;
          end = End{ anchor, end.why, end.moved, anchor };
        }
        if (! std::ranges::contains(one->final, end))
          one->final.push_back(end);
      }
    }
  }


  // Whether an arm starting at `anchor` starts below the line `end` is on.
  bool LastUses::below(Loc anchor, const End& end) const
  {
    if (anchor == no_loc)
      return false;
    auto here = sources.position(anchor);
    auto there = sources.position(std::max<Loc>(end.at - 1, 0));
    return here && there && here->line > there->line;
  }


  // Where an end carried into an arm goes: its first statement.
  Loc LastUses::anchor(const ast::Block* body) const
  {
    auto stmts = body->stmts();
    return ! stmts.empty() && ours(stmts.front()->span) ? stmts.front()->span.start : no_loc;
  }


  // Every way on from the arms of one choice, put together.
  void LastUses::join(const State& start, const std::vector<Way>& ways)
  {
    for (const auto& [one, saved] : start) {
      if (ways.empty()) {
        one->ends.clear();
        continue;
      }
      const auto& first = ways.front().outcome.at(one).ends;
      bool differ = std::ranges::any_of(ways, [&](const Way& way) { return way.outcome.at(one).ends != first; });
      std::vector<End> ends;
      Loc held_to = no_loc;
      for (const auto& way : ways) {
        const auto& theirs = way.outcome.at(one);
        held_to = std::max(held_to, theirs.held_to);
        for (End end : theirs.ends) {
          if (below(way.anchor, end) && std::ranges::contains(way.entry.at(one).ends, end)
              && (differ || ! std::ranges::contains(saved.ends, end))) {
            Why why = end.why;
            if (end.why == Why{})
              why = Why::of(Why::Kind::unused_here);
            end = End{ way.anchor, why, end.moved, way.anchor };
          }
          if (! std::ranges::contains(ends, end))
            ends.push_back(end);
        }
      }
      one->ends = std::move(ends);
      one->held_to = held_to;
    }
  }


  void LastUses::if_(const ast::If* node)
  {
    State start = state();
    std::vector<Way> ways;
    for (auto arm : node->arms) {
      // What a condition reads, it reads on the way to its own arm and to
      // every arm after it.
      if (arm->condition != nullptr)
        header(arm->condition);
      State entry = state();
      block(arm->body);
      arm_done(arm->body, entry, start, ways, anchor(arm->body));
      restore(entry);
    }
    if (node->arms.back()->condition != nullptr) {
      // No arm taken: a way on of its own, which goes on after the `if`.
      Loc after = ours(node->span) ? line_after(sources, end_of(node), node->span.start).start : no_loc;
      ways.push_back(Way{ state(), state(), after });
    }
    join(start, ways);
  }


  void LastUses::match(const ast::Match* node)
  {
    State start = state();
    header(node->subject);
    State entry = state();
    std::vector<Way> ways;
    for (auto arm : node->arms) {
      restore(entry);
      scopes.emplace_back();
      auto pattern = arm->pattern;
      if (pattern->type != nullptr)
        expr(pattern->type);
      if (pattern->name && ! pattern->wildcard)
        bind(*pattern->name, pattern->name_span, stmt_end);
      block(arm->body, false);
      pop();
      arm_done(arm->body, entry, start, ways, anchor(arm->body));
    }
    restore(entry);
    join(start, ways);
  }


  std::unordered_map<const LastUses::Binding*, int> LastUses::uses() const
  {
    std::unordered_map<const Binding*, int> out;
    for (auto [_, one] : live)
      out.emplace(one, one->uses);
    return out;
  }


  // A loop is over: what was bound outside it and used in it may be used on any
  // turn, so it ends after the loop -- on every way on.
  void LastUses::looped(const ast::Node* node, const ast::Block* body,
                        const std::unordered_map<const Binding*, int>& before)
  {
    if (! ours(node->span))
      return;
    Loc begins = node->span.start;
    Loc ends = end_of(body);
    for (auto [_, one] : live) {
      auto found = before.find(one);
      int earlier = found != before.end() ? found->second : one->uses;
      if (earlier != one->uses && one->held_to <= ends)
        one->ends = { End{ ends, Why::of(Why::Kind::loop, one->latest, begins) } };
    }
  }


  void LastUses::while_(const ast::While* node)
  {
    auto before = uses();
    header(node->condition);
    block(node->body);
    looped(node, node->body, before);
    if (node->alternative != nullptr)
      block(node->alternative);
  }


  // What a `foreach` walks is worked out once, before the loop, and kept for
  // all of it; what a `while` binds is worked out on every turn.
  void LastUses::for_each(const ast::ForEach* node)
  {
    bool again = node->keyword == "while";
    auto before = uses();
    if (ours(node->iterable->span))
      stmt_end = end_of(node->iterable);
    auto outer = held_until;
    if (! again && ours(node->span))
      held_until = end_of(node->body);
    expr(node->iterable);
    held_until = outer;
    if (node->type != nullptr)
      expr(node->type);
    if (! again)
      before = uses();
    scopes.emplace_back();
    bind(node->name, node->name_span, stmt_end);
    for (const auto& more : node->more)
      bind(more.name, more.span, stmt_end);
    block(node->body, false);
    pop();
    looped(node, node->body, before);
    if (node->alternative != nullptr)
      block(node->alternative);
  }


  // -- the bindings -----------------------------------------------------------

  void LastUses::bind(std::string_view name, Span at, Loc bound_end)
  {
    if (name == wildcard_name || ! ours(at))
      return;
    Binding& binding = store.emplace_back(Binding{ name, at, { End{ bound_end, Why{}, false, no_loc } }, {}, no_loc, 0, std::nullopt });
    scopes.back().push_back(&binding);
    live[at.start] = &binding;
  }


  LastUses::Binding* LastUses::find(std::string_view name) const
  {
    for (const auto& scope : scopes | std::views::reverse)
      for (auto binding : scope | std::views::reverse)
        if (binding->name == name)
          return binding;
    return nullptr;
  }


  void LastUses::use(std::string_view name, Span span)
  {
    Binding* binding = find(name);
    if (binding == nullptr)
      return;
    Span line = ours(span) ? span : Span{ stmt_end - 1, stmt_end - 1 };
    binding->uses += 1;
    if (! binding->latest || line.start > binding->latest->start)
      binding->latest = line;
    bool moved = moves.contains(span.start);
    if (deferred) {
      Loc at = deferred->first;
      binding->ends = { End{ at, Why::of(Why::Kind::deferred, deferred->second), moved } };
      binding->held_to = std::max(binding->held_to, at);
      return;
    }
    if (held_until != no_loc) {
      // Kept to the loop's end, whatever else in what is walked uses it.
      Loc at = held_until;
      if (binding->held_to >= at)
        return;
      binding->ends = { End{ at, Why::of(Why::Kind::walked, line), moved } };
      binding->held_to = at;
      return;
    }
    Loc at = stmt_end;
    if (binding->held_to >= at)
      // A deferred use holds it to the end of a block this is in.
      return;
    binding->ends = { End{ at, Why::of(Why::Kind::used, line), moved } };
  }


  // Leave a scope: what its names hold references to lasts as long as they do,
  // and each name's scope is reported.
  void LastUses::pop()
  {
    auto scope = std::move(scopes.back());
    scopes.pop_back();
    for (auto binding : scope | std::views::reverse) {
      live.erase(binding->at.start);
      std::vector<End> ends = binding->ends;
      for (const auto& one : binding->final)
        if (! std::ranges::contains(binding->ends, one))
          ends.push_back(one);
      auto furthest_of = [](const std::vector<End>& of) {
        Loc furthest = no_loc;
        for (const auto& one : of)
          furthest = std::max(furthest, one.at);
        return furthest;
      };
      Loc furthest = furthest_of(ends);
      if (auto lent = lends.find(binding->at.start); lent != lends.end())
        for (Loc one : lent->second)
          if (auto lender = live.find(one); lender != live.end() && furthest > furthest_of(lender->second->ends)) {
            std::vector<End> carried;
            for (const auto& end : ends)
              carried.push_back(End{ end.at, Why::of(Why::Kind::lent, std::nullopt, no_loc, binding->name) });
            lender->second->ends = std::move(carried);
          }
      std::ranges::stable_sort(ends, {}, &End::at);
      std::unordered_set<Loc> told;
      for (const auto& end : ends) {
        // A last use that moves the value out: the move is logged, and after
        // it there is no variable left to end.
        if (end.moved)
          continue;
        Span where = end.place != no_loc ? Span{ end.place, end.place } : line_after(sources, end.at);
        if (! told.insert(where.start).second)
          continue;
        report(binding->name, reason(end), where);
      }
    }
  }


  std::string LastUses::reason(const End& end) const
  {
    const Why& why = end.why;
    switch (why.kind) {
    case Why::Kind::used:
      return "last used on line " + line(why.span->start);
    case Why::Kind::walked:
      return "used on line " + line(why.span->start) + " in what the loop walks, which it keeps to its end";
    case Why::Kind::loop:
      if (why.span)
        return "last used on line " + line(why.span->start) + ", in the loop from line " + line(why.begins)
               + ", which may come round to it again";
      break;
    case Why::Kind::deferred:
      return "last used by what is put off on line " + line(why.span->start) + ", when the block is left";
    case Why::Kind::lent:
      return "'" + std::string(why.holder) + "' holds a reference to it, and its scope ends here";
    case Why::Kind::unused_here:
      return "not used on the way through here";
    case Why::Kind::never:
      break;
    }
    return "never used";
  }


  std::string LastUses::line(Loc loc) const
  {
    auto found = sources.position(loc);
    return found ? std::to_string(found->line) : "?";
  }

} // namespace pl4g::sema
