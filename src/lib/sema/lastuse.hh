// Where each variable's scope ends: after its last use, not at its block's end.
//
// A port of pypl4g's sema/lastuse.py.  Each variable gets a `scope-ended`
// entry on the line after the one its scope ends on.  The last use is the last
// on each way through the function; a loop, what a `foreach` walks, a deferred
// statement and a reference held by another name carry a scope past the text
// of its last use.  A last use that moves the value out has no entry.
//
// Positions are bytes here and characters in pypl4g; where a column is carried
// from one line to another (`line_after`) it is counted in characters, so the
// place is the same character pypl4g names.
#pragma once

#include <deque>
#include <functional>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "front/ast.hh"
#include "source/location.hh"
#include "source/manager.hh"


namespace pl4g::sema {

  // Where the text of `node` ends: the end of its last statement, where it
  // ends with a block -- a block's span runs on to the next statement.
  Loc end_of(const ast::Node* node);

  // Whether every way through `block` leaves it before its end.
  bool leaves(const ast::Block* block);

  // The first character that is not a blank of the line after the one the
  // character before `end` is on -- or where that line begins, where it has
  // none, or the end of the file, where there is no such line.  Given
  // `column_of`, the column that is in its own line instead, as far as the
  // line reaches.
  Span line_after(const SourceManager& sources, Loc end, Loc column_of = no_loc);


  // Walk one function and report where each of its variables' scopes ends:
  // pypl4g's `LastUses`.  `lends` maps where a name is bound to where the
  // names it holds a reference to are bound; `moves` holds where a name is
  // read as a value it is moved out of; `report` takes the variable's name,
  // the reason and the place the entry goes.
  class LastUses {
  public:
    using Lends = std::unordered_map<Loc, std::unordered_set<Loc>>;
    using Moves = std::unordered_set<Loc>;
    using Report = std::function<void(std::string_view name, std::string reason, Span where)>;

    LastUses(const SourceManager& sources, const Lends& lends, const Moves& moves, Report report)
      : sources(sources), lends(lends), moves(moves), report(std::move(report)) {}

    // Walk `node`: its parameters, its conditions and its body.
    void function(const ast::FuncDef* node);

  private:
    // Why a scope ends where it does, as pypl4g's tuples say it.
    struct Why {
      enum class Kind { never, used, walked, loop, deferred, lent, unused_here };
      Kind kind = Kind::never;
      // Where the use was: `used`, `walked`, `loop` (the latest use, which may
      // be missing), `deferred` (what was put off).
      std::optional<Span> span;
      // Where a loop begins.
      Loc begins = no_loc;
      // Who holds a reference, for `lent`.
      std::string_view holder;

      // One of these, every part given.
      static Why of(Kind kind, std::optional<Span> span = std::nullopt, Loc begins = no_loc,
                    std::string_view holder = {})
      {
        return Why{ kind, span, begins, holder };
      }

      bool operator==(const Why&) const = default;
    };

    // Where a variable's scope ends on one way through, and why there.
    struct End {
      Loc at = no_loc;
      Why why;
      // Whether the use there moves the value out.
      bool moved = false;
      // Where the entry goes, where that is not the line after `at`.
      Loc place = no_loc;

      bool operator==(const End&) const = default;
    };

    // A variable while its scope is walked.
    struct Binding {
      std::string_view name;
      Span at;
      std::vector<End> ends;
      std::vector<End> final;
      Loc held_to = no_loc;
      int uses = 0;
      std::optional<Span> latest;
    };

    struct Saved {
      std::vector<End> ends;
      Loc held_to = no_loc;
    };
    using State = std::unordered_map<Binding*, Saved>;

    struct Way {
      State entry;
      State outcome;
      Loc anchor = no_loc;
    };

    bool ours(Span span) const;
    void block(const ast::Block* block, bool scoped = true);
    void stmt(const ast::Stmt* stmt);
    void expr(const ast::Node* node);
    void header(const ast::Expr* expr);
    void fields(const ast::Node* node);
    State state() const;
    void restore(const State& state);
    void arm_done(const ast::Block* body, const State& entry, const State& start, std::vector<Way>& ways, Loc anchor);
    bool below(Loc anchor, const End& end) const;
    Loc anchor(const ast::Block* body) const;
    void join(const State& start, const std::vector<Way>& ways);
    void if_(const ast::If* node);
    void match(const ast::Match* node);
    std::unordered_map<const Binding*, int> uses() const;
    void looped(const ast::Node* node, const ast::Block* body, const std::unordered_map<const Binding*, int>& before);
    void while_(const ast::While* node);
    void for_each(const ast::ForEach* node);
    void bind(std::string_view name, Span at, Loc bound_end);
    Binding* find(std::string_view name) const;
    void use(std::string_view name, Span span);
    void pop();
    std::string reason(const End& end) const;
    std::string line(Loc loc) const;

    const SourceManager& sources;
    const Lends& lends;
    const Moves& moves;
    Report report;
    const SourceFile* file = nullptr;
    std::deque<Binding> store;
    std::vector<std::vector<Binding*>> scopes;
    std::unordered_map<Loc, Binding*> live;
    // Where the statement being walked ends: a use is a use until then.
    Loc stmt_end = no_loc;
    // Where a use in what a `foreach` walks counts as being: the loop's end.
    Loc held_until = no_loc;
    // The end of the block a deferred statement being walked runs at, and the
    // statement.
    std::optional<std::pair<Loc, Span>> deferred;
    std::vector<Loc> blocks;
  };

} // namespace pl4g::sema
